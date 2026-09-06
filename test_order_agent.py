"""Order Agent orchestration tests; extraction is mocked, never live Ollama."""

from copy import deepcopy
import unittest
from unittest.mock import Mock, call, patch

from pydantic import ValidationError

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_agent import process_order_creation_message
from order_creation_controller import execute_order_creation_workflow
from order_creation_extraction import ConversationMessage
from order_creation_state import OrderCreationStage, OrderCreationState, OrderWorkflowStatus
from order_creation_updates import ExtractedOrderInformation
from test_order_creation_state import complete_customer_state


class OrderAgentTests(unittest.TestCase):
    def setUp(self):
        self.state = OrderCreationState(conversation_id="stage-7")
        self.history = [{"role": "assistant", "content": "What name should we use?"}]
        self.extraction_patch = patch("order_agent.extract_order_information")
        self.extract = self.extraction_patch.start()
        self.addCleanup(self.extraction_patch.stop)
        self.extract.return_value = ExtractedOrderInformation()

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
             patch("order_agent.execute_order_creation_workflow", return_value=result) as execute:
            calls.attach_mock(merge, "merge")
            calls.attach_mock(execute, "execute")
            actual_state, actual_result = process_order_creation_message("Alex", self.history, self.state)
        self.assertEqual(calls.mock_calls, [
            call.extract("Alex", self.history, self.state),
            call.merge(self.state, extracted), call.execute(updated),
        ])
        self.assertIs(self.extract.call_args.args[1], self.history)
        self.assertIs(self.extract.call_args.args[2], self.state)
        self.assertIs(merge.call_args.args[1], extracted)
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
            ({"table_size": "11ft"}, BusinessResultReason.MISSING_REQUIRED_INFORMATION, "table_size"),
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

    def test_successful_validation_raises_without_returning_working_copy(self):
        state = complete_customer_state()
        before = state.model_dump()
        history_before = deepcopy(self.history)
        with patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            with self.assertRaisesRegex(NotImplementedError, "CONFIGURATION_CONFIRMATION"):
                process_order_creation_message("Thanks", self.history, state)
        # Only the test spy can inspect this copy: the invocation raised instead
        # of returning a tuple. The caller retains its original unchanged Wt.
        working = execute.call_args.args[0]
        self.assertIsNot(working, state)
        self.assertEqual(working.current_stage, OrderCreationStage.CONFIGURATION_CONFIRMATION)
        self.assertEqual(working.room_size_validation_result, "SUITABLE")
        self.assertEqual(working.status, OrderWorkflowStatus.ACTIVE)
        self.assertIsNone(working.failure_reason)
        self.assertEqual(state.model_dump(), before)
        self.assertEqual(self.history, history_before)

    def test_terminal_policy_remains_in_controller_after_extraction(self):
        self.state.status = OrderWorkflowStatus.COMPLETED
        before = self.state.model_dump()
        with patch("order_agent.execute_order_creation_workflow", wraps=execute_order_creation_workflow) as execute:
            with self.assertRaises(ValueError):
                process_order_creation_message("Hello", [], self.state)
        self.extract.assert_called_once()
        execute.assert_called_once()
        self.assertEqual(self.state.model_dump(), before)


if __name__ == "__main__":
    unittest.main()
