"""Order Agent orchestration tests; extraction is mocked, never live Ollama."""

from copy import deepcopy
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
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
        self.interpret.return_value = ConfirmationInterpretation(intent="CHANGE_REQUESTED")
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
        self.assertEqual(order, ["interpret", "execute"])
        self.assertIs(updated, working_states[0])
        self.assertIs(result, results[0])
        self.assertEqual(result.data["confirmation_intent"], "DECLINED")
        self.assertEqual(state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_first_arrival_skips_but_corrections_require_interpretation(self):
        state, _ = process_order_creation_message("Initial order", [], complete_customer_state())
        self.interpret.assert_not_called()
        self.interpret.return_value = ConfirmationInterpretation(intent="CHANGE_REQUESTED")
        for values in ({"felt_color":"Green"}, {"table_size":"7ft"},
                       {"room_size":"6m x 5m"}, {"phone":"0400123456"}):
            with self.subTest(values=values):
                self.extract.return_value = ExtractedOrderInformation(**values)
                process_order_creation_message("No, change it", [], state)
                self.interpret.assert_called()

    def test_same_value_confirmation_prices_and_preserves_inputs(self):
        state = complete_customer_state()
        execute_order_creation_workflow(state)
        before, history_before = state.model_dump(), deepcopy(self.history)
        self.extract.return_value = ExtractedOrderInformation(table_size="8ft")
        self.interpret.return_value = ConfirmationInterpretation(intent="CONFIRMED")
        with patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            updated, result = process_order_creation_message("Yes, 8ft is correct", self.history, state)
            self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
        self.interpret.assert_called_once()
        working = self.interpret.call_args.args[2]
        self.assertIs(working, execute.call_args.args[0])
        self.assertTrue(working.configuration_confirmed)
        self.assertEqual(working.current_stage, OrderCreationStage.FINAL_CONFIRMATION)
        self.assertEqual(working.unit_price, Decimal("6050"))
        self.assertEqual(working.shipping_cost, Decimal("530"))
        self.assertEqual(working.total_price, Decimal("6580"))
        self.assertEqual(working.order_snapshot, state.order_snapshot)
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
        self.interpret.reset_mock(side_effect=True)
        self.interpret.return_value = ConfirmationInterpretation(intent="CHANGE_REQUESTED")
        reentry_error = RuntimeError("Reentry failed")
        with patch("order_agent.apply_order_creation_reentry", side_effect=reentry_error), \
             patch("order_agent.execute_order_creation_workflow") as execute:
            with self.assertRaises(RuntimeError) as caught:
                process_order_creation_message("Yes", [], state)
            self.assertIs(caught.exception, reentry_error)
            self.interpret.assert_called_once()
            execute.assert_not_called()



class FinalOrderAgentTests(unittest.TestCase):
    def setUp(self):
        from test_order_creation_confirmation import final_waiting_state
        self.store_dir = TemporaryDirectory()
        self.addCleanup(self.store_dir.cleanup)
        import order_agent
        original = order_agent._execute_with_optional_store_path
        def isolated(state, **kwargs):
            if kwargs['order_store_path'] == order_agent.DEFAULT_ORDER_STORE_PATH:
                kwargs['order_store_path'] = Path(self.store_dir.name) / "orders.json"
            return original(state, **kwargs)
        store_patch = patch("order_agent._execute_with_optional_store_path", side_effect=isolated)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.state = final_waiting_state()
        self.history = [{"role": "assistant", "content": "Please authorize this exact final order."}]
        self.ep = patch("order_agent.extract_order_information", return_value=ExtractedOrderInformation())
        self.ip = patch("order_agent.interpret_confirmation_response", return_value=ConfirmationInterpretation(intent="CONFIRMED"))
        self.extract, self.interpret = self.ep.start(), self.ip.start()
        self.addCleanup(self.ep.stop)
        self.addCleanup(self.ip.stop)

    def test_confirmation_first_approval_decline_ambiguity_both_stages(self):
        from test_order_creation_confirmation import waiting_state, final_waiting_state
        for factory in (waiting_state, final_waiting_state):
            for intent in ("CONFIRMED", "DECLINED", "AMBIGUOUS"):
                state = factory()
                before = deepcopy(state)
                self.extract.reset_mock()
                self.extract.return_value = ExtractedOrderInformation(quantity=2)
                self.interpret.return_value = ConfirmationInterpretation(intent=intent)
                with patch("order_agent.apply_extracted_order_information") as merge, \
                     patch("order_agent.apply_order_creation_reentry") as reentry, \
                     patch("order_agent.execute_order_creation_workflow", return_value=object()) as execute:
                    updated, result = process_order_creation_message(
                        "Yes, I confirm the final order and would like to place it.", self.history, state)
                self.extract.assert_not_called()
                merge.assert_not_called()
                reentry.assert_not_called()
                self.assertEqual(updated, before)
                self.assertIsNot(updated, state)
                final = state.current_stage == OrderCreationStage.FINAL_CONFIRMATION
                evidence = execute.call_args.kwargs['final_confirmation_snapshot' if final else 'confirmation_snapshot']
                snapshot = state.final_order_snapshot if final else state.order_snapshot
                self.assertEqual(evidence, snapshot)
                self.assertIsNot(evidence, snapshot)
                self.assertEqual(state, before)

    def test_change_intent_without_effective_update_preserves_evidence(self):
        from test_order_creation_confirmation import waiting_state, final_waiting_state
        for factory in (waiting_state, final_waiting_state):
            for values in ({}, {"quantity": 1}):
                state = factory()
                before = deepcopy(state)
                self.extract.return_value = ExtractedOrderInformation(**values)
                self.interpret.return_value = ConfirmationInterpretation(intent="CHANGE_REQUESTED")
                updated, result = process_order_creation_message("Change it", self.history, state)
                self.assertEqual(updated.quantity, 1)
                self.assertEqual(updated.current_stage, state.current_stage)
                self.assertEqual(result.data['confirmation_intent'], 'CHANGE_REQUESTED')
                self.assertEqual(state, before)

    def test_malformed_pending_state_stops_before_language_calls(self):
        for field, value in (("final_order_snapshot", None), ("pending_confirmations", []), ("final_order_confirmed", True)):
            state = deepcopy(self.state)
            setattr(state, field, value)
            with self.assertRaisesRegex(ValueError, 'Malformed'):
                process_order_creation_message("Yes", self.history, state)
        self.extract.assert_not_called()
        self.interpret.assert_not_called()

    def test_correction_first_and_fresh_affirmative(self):
        for message, values, stage in (
            ("Yes, but make it 3 tables", {"quantity": 3}, OrderCreationStage.FINAL_CONFIRMATION),
            ("Looks good, change felt to Blue", {"felt_color": "Blue"}, OrderCreationStage.CONFIGURATION_CONFIRMATION),
            ("Yes, deliver to 3152", {"delivery_address": {"postcode": "3152"}}, OrderCreationStage.FINAL_CONFIRMATION),
            ("Yes, phone is 123", {"phone": "123"}, OrderCreationStage.FINAL_CONFIRMATION),
            ("Change city", {"delivery_address": {"city": "Richmond"}}, OrderCreationStage.FINAL_CONFIRMATION),
        ):
            with self.subTest(values=values):
                self.interpret.reset_mock()
                self.interpret.return_value = ConfirmationInterpretation(intent="CHANGE_REQUESTED")
                self.extract.return_value = ExtractedOrderInformation(**values)
                before, history = self.state.model_dump(), deepcopy(self.history)
                updated, result = process_order_creation_message(message, self.history, self.state)
                self.interpret.assert_called()
                self.assertEqual(updated.current_stage, stage)
                self.assertFalse(updated.final_order_confirmed)
                self.assertEqual(self.state.model_dump(), before)
                self.assertEqual(self.history, history)
                if stage == OrderCreationStage.FINAL_CONFIRMATION:
                    self.assertNotEqual(updated.final_order_snapshot, self.state.final_order_snapshot)
                    self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
                    self.extract.return_value = ExtractedOrderInformation()
                    self.interpret.return_value = ConfirmationInterpretation(intent="CONFIRMED")
                    with TemporaryDirectory() as tmp:
                        completed, success = process_order_creation_message(
                            "Yes, proceed", self.history, updated,
                            order_store_path=Path(tmp) / "orders.json",
                        )
                    self.assertEqual(success.result_status, BusinessResultStatus.SUCCESS)
                    self.assertEqual(success.reason, BusinessResultReason.ORDER_CREATED)
                    self.assertTrue(completed.final_order_confirmed)
                    self.assertEqual(completed.current_stage, OrderCreationStage.COMPLETED)
                    self.assertFalse(updated.final_order_confirmed)

    def test_final_authorization_preserves_caller_and_evidence(self):
        before, history = self.state.model_dump(), deepcopy(self.history)
        with TemporaryDirectory() as tmp, \
             patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            updated, result = process_order_creation_message(
                "Yes.", self.history, self.state,
                order_store_path=Path(tmp) / "orders.json",
            )
        working = execute.call_args.args[0]
        self.assertTrue(working.final_order_confirmed)
        self.assertIsNot(execute.call_args.kwargs["final_confirmation_snapshot"], working.final_order_snapshot)
        self.assertIs(updated, working)
        self.assertEqual(result.result_status, BusinessResultStatus.SUCCESS)
        self.assertEqual(result.reason, BusinessResultReason.ORDER_CREATED)
        self.assertEqual(updated.current_stage, OrderCreationStage.COMPLETED)
        self.assertEqual(self.state.model_dump(), before)
        self.assertEqual(self.history, history)

    def test_waiting_and_failure_preserve_inputs(self):
        before = self.state.model_dump()
        for intent in ("DECLINED", "AMBIGUOUS", "CHANGE_REQUESTED"):
            self.interpret.return_value = ConfirmationInterpretation(intent=intent)
            updated, result = process_order_creation_message("No or question", self.history, self.state)
            self.assertFalse(updated.final_order_confirmed)
            self.assertEqual(result.data["confirmation_intent"], intent)
            self.assertEqual(self.state.model_dump(), before)
        self.interpret.side_effect = ConnectionError("Ollama unavailable")
        with self.assertRaises(ConnectionError):
            process_order_creation_message("Yes", self.history, self.state)
        self.assertEqual(self.state.model_dump(), before)


if __name__ == "__main__":
    unittest.main()
