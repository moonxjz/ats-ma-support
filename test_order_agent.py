"""Order Agent orchestration tests; extraction is mocked, never live Ollama."""

from copy import deepcopy
import unittest
from unittest.mock import Mock, call, patch

from pydantic import ValidationError

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_creation_confirmation import ConfirmationInterpretation
from order_agent import process_order_creation_message
from order_creation_controller import execute_order_creation_workflow
from order_creation_extraction import ConversationMessage
from order_creation_state import OrderCreationStage, OrderCreationState, OrderWorkflowStatus
from order_creation_updates import ExtractedOrderInformation
from test_order_creation_state import complete_customer_state as schema_complete_state


def complete_customer_state():
    """Catalog-valid workflow fixture; Stage 1 completeness fixture stays unchanged."""
    state = schema_complete_state()
    state.product_model = "Odyssey"
    state.timber = "Tassie Oak"
    state.felt_color = "Grey"
    state.bracket = "Standard rubber"
    state.top_profile = "Waterfall"
    return state


class OrderAgentTests(unittest.TestCase):
    def setUp(self):
        self.state = OrderCreationState(conversation_id="stage-7")
        self.history = [{"role": "assistant", "content": "What name should we use?"}]
        self.extraction_patch = patch("order_agent.extract_order_information")
        self.extract = self.extraction_patch.start()
        self.addCleanup(self.extraction_patch.stop)
        self.extract.return_value = ExtractedOrderInformation()
        self.confirmation_patch = patch("order_agent.interpret_confirmation_response")
        self.interpret = self.confirmation_patch.start()
        self.addCleanup(self.confirmation_patch.stop)
        self.interpret.return_value = ConfirmationInterpretation(intent="AMBIGUOUS")

    def test_exact_chain_arguments_and_return_identity(self):
        extracted = ExtractedOrderInformation(customer_name="Alex")
        self.extract.return_value = extracted
        updated = self.state.model_copy(deep=True)
        result = BusinessResult(
            workflow_id=self.state.workflow_id, source_agent="ORDER_AGENT",
            action="CREATE_ORDER", current_stage="COLLECT_REQUIREMENTS",
            result_status=BusinessResultStatus.NEEDS_USER_INPUT,
            reason=BusinessResultReason.MISSING_REQUIRED_INFORMATION,
            required_input=["email"], data={"missing_fields": ["email"]},
        )
        calls = Mock()
        calls.attach_mock(self.extract, "extract")
        with patch("order_agent.apply_extracted_order_information", return_value=updated) as merge, \
             patch("order_agent.execute_order_creation_workflow", return_value=result) as execute, \
             patch("order_agent.apply_order_creation_reentry") as reentry:
            calls.attach_mock(merge, "merge")
            calls.attach_mock(reentry, "reentry")
            calls.attach_mock(execute, "execute")
            actual_state, actual_result = process_order_creation_message("Alex", self.history, self.state)
        self.assertEqual(calls.mock_calls, [
            call.extract("Alex", self.history, self.state),
            call.merge(self.state, extracted), call.reentry(self.state, updated), call.execute(updated),
        ])
        self.assertIs(self.extract.call_args.args[1], self.history)
        self.assertIs(self.extract.call_args.args[2], self.state)
        self.assertIs(merge.call_args.args[1], extracted)
        self.assertIs(reentry.call_args.args[0], self.state)
        self.assertIs(reentry.call_args.args[1], updated)
        self.assertIs(execute.call_args.args[0], updated)
        self.assertIsNot(updated, self.state)
        self.assertIs(actual_state, updated)
        self.assertIs(actual_result, result)

    def test_empty_extraction_runs_real_merge_and_controller(self):
        before = self.state.model_dump()
        history_before = deepcopy(self.history)
        updated, result = process_order_creation_message("Thanks", self.history, self.state)
        self.assertIsNot(updated, self.state)
        self.assertEqual(updated.pending_field, "customer_name")
        self.assertEqual(updated.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(result.required_input, ["customer_name"])
        self.assertEqual(result.reason, BusinessResultReason.MISSING_REQUIRED_INFORMATION)
        self.assertEqual(self.state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_extraction_errors_propagate_without_downstream_calls(self):
        errors = [ConnectionError("Ollama unavailable")]
        for factory in (
            lambda: ConversationMessage(role="system", content="Invalid role"),
            lambda: ExtractedOrderInformation(quantity=0),
        ):
            try:
                factory()
            except ValidationError as error:
                errors.append(error)
        for error in errors:
            with self.subTest(error=error), \
                 patch("order_agent.apply_extracted_order_information") as merge, \
                 patch("order_agent.execute_order_creation_workflow") as execute:
                self.extract.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    process_order_creation_message("Hello", self.history, self.state)
                self.assertIs(caught.exception, error)
                merge.assert_not_called()
                execute.assert_not_called()

    def test_merge_validation_error_propagates_without_controller(self):
        try:
            ExtractedOrderInformation(phone=None)
        except ValidationError as error:
            merge_error = error
        with patch("order_agent.apply_extracted_order_information", side_effect=merge_error), \
             patch("order_agent.execute_order_creation_workflow") as execute:
            with self.assertRaises(ValidationError) as caught:
                process_order_creation_message("Hello", self.history, self.state)
            self.assertIs(caught.exception, merge_error)
            execute.assert_not_called()

    def test_controller_exception_propagates_unchanged(self):
        before = self.state.model_dump()
        error = RuntimeError("Controller failure")
        with patch("order_agent.execute_order_creation_workflow", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                process_order_creation_message("Hello", self.history, self.state)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.state.model_dump(), before)

    def test_multi_turn_retains_returned_state_and_preserves_inputs(self):
        self.extract.side_effect = [
            ExtractedOrderInformation(product_model="Odyssey", table_size="8ft", room_size="5.2m x 4m"),
            ExtractedOrderInformation(customer_name="Alex Morgan", email="alex@example.com"),
        ]
        first, first_result = process_order_creation_message("I'd like an 8ft Odyssey", [], self.state)
        self.assertEqual(first_result.required_input, ["customer_name"])
        before = first.model_dump()
        history = [{"role": "user", "content": "I'd like an 8ft Odyssey"},
                   {"role": "assistant", "content": "What name should we use?"}]
        history_before = deepcopy(history)
        second, result = process_order_creation_message("Alex Morgan, alex@example.com", history, first)
        self.assertIs(self.extract.call_args.args[2], first)
        self.assertEqual(second.product_model, "Odyssey")
        self.assertEqual(second.table_size, "8ft")
        self.assertEqual(second.room_size, "5.2m x 4m")
        self.assertEqual(second.customer_name, "Alex Morgan")
        self.assertEqual(second.email, "alex@example.com")
        self.assertEqual(result.required_input, ["phone"])
        self.assertEqual(first.model_dump(), before)
        self.assertEqual(history, history_before)

    def test_correction_different_from_pending_field(self):
        state = complete_customer_state()
        state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.pending_field = "table_size"
        self.extract.return_value = ExtractedOrderInformation(room_size="4m x 3m")
        before = state.model_dump()
        updated, result = process_order_creation_message("Actually the room is 4 by 3 metres", [], state)
        self.assertEqual(updated.room_size, "4m x 3m")
        self.assertEqual(updated.table_size, "8ft")
        self.assertEqual(result.reason, BusinessResultReason.ROOM_SIZE_UNSUITABLE)
        self.assertEqual(state.model_dump(), before)

    def test_real_validation_business_outcomes(self):
        cases = [
            ({"room_size": "4m x 3m"}, BusinessResultReason.ROOM_SIZE_UNSUITABLE, "table_size"),
            ({"room_size": "large"}, BusinessResultReason.MISSING_REQUIRED_INFORMATION, "room_size"),
            ({"table_size": "11ft"}, BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE, "table_size"),
        ]
        for values, reason, required in cases:
            with self.subTest(values=values):
                state = complete_customer_state()
                before = state.model_dump()
                self.extract.return_value = ExtractedOrderInformation(**values)
                updated, result = process_order_creation_message("Correction", [], state)
                self.assertEqual(updated.current_stage, OrderCreationStage.VALIDATE_CONFIGURATION)
                self.assertEqual(updated.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
                self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.required_input, [required])
                for field, value in values.items():
                    self.assertEqual(getattr(updated, field), value)
                self.assertEqual(state.model_dump(), before)

    def test_successful_validation_returns_advanced_copy_and_exact_result(self):
        state = complete_customer_state()
        before = state.model_dump()
        history_before = deepcopy(self.history)
        results = []
        def capture(working):
            result = execute_order_creation_workflow(working)
            results.append(result)
            return result
        with patch("order_agent.execute_order_creation_workflow", side_effect=capture) as execute:
            updated, result = process_order_creation_message("Thanks", self.history, state)
        self.assertIs(result, results[0])
        self.assertIs(updated, execute.call_args.args[0])
        self.assertIsNot(updated, state)
        self.assertEqual(updated.current_stage, OrderCreationStage.CONFIGURATION_CONFIRMATION)
        self.assertEqual(updated.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
        self.assertEqual(state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_next_turn_empty_or_same_value_stays_waiting_without_prior_handlers(self):
        state, _ = process_order_creation_message("Initial", [], complete_customer_state())
        for message, values in (("Yes", {}), ("No", {}), ("8ft", {"table_size": "8ft"})):
            self.extract.return_value = ExtractedOrderInformation(**values)
            before = state.model_dump()
            with patch("order_creation_controller.handle_collect_requirements") as collect, \
                 patch("order_creation_controller.handle_validate_configuration") as validate:
                updated, result = process_order_creation_message(message, [], state)
            collect.assert_not_called()
            validate.assert_not_called()
            self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
            self.assertFalse(updated.configuration_confirmed)
            self.assertEqual(state.model_dump(), before)

    def test_next_turn_correction_cannot_bypass_collection_and_validation(self):
        from order_creation_controller import handle_collect_requirements, handle_validate_configuration
        state, _ = process_order_creation_message("Initial", [], complete_customer_state())
        before = state.model_dump()
        self.extract.return_value = ExtractedOrderInformation(felt_color="Green")
        with patch("order_creation_controller.handle_collect_requirements", wraps=handle_collect_requirements) as collect, \
             patch("order_creation_controller.handle_validate_configuration", wraps=handle_validate_configuration) as validate:
            updated, result = process_order_creation_message("Green instead", self.history, state)
        collect.assert_called_once()
        validate.assert_called_once()
        self.assertEqual(updated.order_snapshot, {})
        self.assertEqual(updated.felt_color, "Green")
        self.assertEqual(result.reason, BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE)
        self.assertEqual(state.model_dump(), before)

    def test_terminal_policy_remains_in_controller_after_extraction(self):
        self.state.status = OrderWorkflowStatus.COMPLETED
        before = self.state.model_dump()
        with patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            with self.assertRaises(ValueError):
                process_order_creation_message("Hello", [], self.state)
        self.extract.assert_called_once()
        execute.assert_called_once()
        self.assertEqual(self.state.model_dump(), before)


    def test_pending_turn_interprets_updated_state_in_correct_order(self):
        state = complete_customer_state()
        execute_order_creation_workflow(state)
        before, history_before = state.model_dump(), deepcopy(self.history)
        order = []
        from order_creation_updates import apply_extracted_order_information
        from order_creation_controller import apply_order_creation_reentry
        def extract(*args):
            order.append("extract")
            return ExtractedOrderInformation()
        def merge(*args):
            order.append("merge")
            return apply_extracted_order_information(*args)
        def reentry(*args):
            order.append("reentry")
            apply_order_creation_reentry(*args)
        working_states = []
        def interpret(message, history, current):
            order.append("interpret")
            self.assertEqual(message, "No")
            self.assertIs(history, self.history)
            self.assertIsNot(current, state)
            working_states.append(current)
            return ConfirmationInterpretation(intent="DECLINED")
        results = []
        def execute(current, **kwargs):
            order.append("execute")
            self.assertIs(current, working_states[0])
            self.assertEqual(kwargs["confirmation_snapshot"], current.order_snapshot)
            self.assertIsNot(kwargs["confirmation_snapshot"], current.order_snapshot)
            result = execute_order_creation_workflow(current, **kwargs)
            results.append(result)
            return result
        self.extract.side_effect = extract
        self.interpret.side_effect = interpret
        with patch("order_agent.apply_extracted_order_information", side_effect=merge), \
             patch("order_agent.apply_order_creation_reentry", side_effect=reentry), \
             patch("order_agent.execute_order_creation_workflow", side_effect=execute):
            updated, result = process_order_creation_message("No", self.history, state)
        self.assertEqual(order, ["extract", "merge", "reentry", "interpret", "execute"])
        self.assertIs(updated, working_states[0])
        self.assertIs(result, results[0])
        self.assertEqual(result.data["confirmation_intent"], "DECLINED")
        self.assertEqual(state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_first_arrival_and_effective_corrections_skip_interpretation(self):
        state, _ = process_order_creation_message("Initial order", [], complete_customer_state())
        self.interpret.assert_not_called()
        for values in ({"felt_color":"Green"}, {"table_size":"7ft"},
                       {"room_size":"6m x 5m"}, {"phone":"0400123456"}):
            with self.subTest(values=values):
                self.extract.return_value = ExtractedOrderInformation(**values)
                process_order_creation_message("No, change it", [], state)
                self.interpret.assert_not_called()

    def test_same_value_confirmation_reaches_pricing_and_preserves_inputs(self):
        state = complete_customer_state()
        execute_order_creation_workflow(state)
        before, history_before = state.model_dump(), deepcopy(self.history)
        self.extract.return_value = ExtractedOrderInformation(table_size="8ft")
        self.interpret.return_value = ConfirmationInterpretation(intent="CONFIRMED")
        with patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            with self.assertRaisesRegex(NotImplementedError, "PRICING"):
                process_order_creation_message("Yes, 8ft is correct", self.history, state)
        self.interpret.assert_called_once()
        working = self.interpret.call_args.args[2]
        self.assertIs(working, execute.call_args.args[0])
        self.assertTrue(working.configuration_confirmed)
        self.assertEqual(working.current_stage, OrderCreationStage.PRICING)
        self.assertIsNone(working.failure_reason)
        self.assertEqual(state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_interpreter_and_reentry_errors_propagate_before_execution(self):
        state = complete_customer_state()
        execute_order_creation_workflow(state)
        error = ConnectionError("Interpreter unavailable")
        self.interpret.side_effect = error
        with patch("order_agent.execute_order_creation_workflow") as execute:
            with self.assertRaises(ConnectionError) as caught:
                process_order_creation_message("Yes", [], state)
            self.assertIs(caught.exception, error)
            execute.assert_not_called()
        self.interpret.reset_mock()
        reentry_error = RuntimeError("Reentry failed")
        with patch("order_agent.apply_order_creation_reentry", side_effect=reentry_error), \
             patch("order_agent.execute_order_creation_workflow") as execute:
            with self.assertRaises(RuntimeError) as caught:
                process_order_creation_message("Yes", [], state)
            self.assertIs(caught.exception, reentry_error)
            self.interpret.assert_not_called()
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
