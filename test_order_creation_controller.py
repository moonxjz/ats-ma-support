"""In-memory tests for the Stage 2 collection handler."""

import unittest
from unittest.mock import patch

from business_result import BusinessResultReason, BusinessResultStatus
from order_creation_controller import handle_collect_requirements
from order_creation_state import (
    OrderCreationStage,
    OrderCreationState,
    OrderWorkflowStatus,
    REQUIRED_CUSTOMER_FIELDS,
)
from test_order_creation_state import complete_customer_state


class CollectRequirementsTests(unittest.TestCase):
    def test_empty_state_returns_missing_information_result(self):
        state = OrderCreationState(conversation_id="ATS-TEST-001")
        result = handle_collect_requirements(state)
        self.assertEqual(state.missing_customer_fields, list(REQUIRED_CUSTOMER_FIELDS))
        self.assertEqual(state.pending_field, "customer_name")
        self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(state.current_stage, OrderCreationStage.COLLECT_REQUIREMENTS)
        self.assertIsNone(state.last_question)
        self.assertEqual(result.workflow_id, state.workflow_id)
        self.assertEqual(result.source_agent, "ORDER_AGENT")
        self.assertEqual(result.action, "CREATE_ORDER")
        self.assertEqual(result.current_stage, "COLLECT_REQUIREMENTS")
        self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
        self.assertEqual(result.reason, BusinessResultReason.MISSING_REQUIRED_INFORMATION)
        self.assertEqual(result.required_input, ["customer_name"])
        self.assertEqual(result.data, {"missing_fields": list(REQUIRED_CUSTOMER_FIELDS)})
        self.assertIsNone(result.error)

    def test_partial_state_resumes_and_advances_pending_field(self):
        state = complete_customer_state()
        state.phone = None
        state.delivery_address.postcode = " \t"
        result = handle_collect_requirements(state)
        self.assertEqual(result.data["missing_fields"], ["phone", "delivery_address.postcode"])
        self.assertEqual(result.required_input, ["phone"])
        state.phone = "0400000000"
        result = handle_collect_requirements(state)
        self.assertEqual(state.pending_field, "delivery_address.postcode")
        self.assertEqual(result.required_input, ["delivery_address.postcode"])
        self.assertEqual(state.missing_customer_fields, ["delivery_address.postcode"])
        state.delivery_address.postcode = "3000"
        self.assertIsNone(handle_collect_requirements(state))
        self.assertEqual(state.missing_customer_fields, [])
        self.assertIsNone(state.pending_field)
        self.assertEqual(state.status, OrderWorkflowStatus.ACTIVE)
        self.assertEqual(state.current_stage, OrderCreationStage.VALIDATE_CONFIGURATION)

    def test_complete_customer_information_does_not_require_system_or_confirmation(self):
        state = complete_customer_state()
        state.pending_system_fields = ["product_sku", "total_price"]
        self.assertIsNone(handle_collect_requirements(state))
        self.assertEqual(state.current_stage, OrderCreationStage.VALIDATE_CONFIGURATION)
        self.assertIsNone(state.product_sku)
        self.assertIsNone(state.total_price)
        self.assertIsNone(state.order_id)
        self.assertFalse(state.configuration_confirmed)
        self.assertFalse(state.final_order_confirmed)
        self.assertNotIn("result_status", state.model_dump())

    def test_only_collection_fields_change_on_both_branches(self):
        for complete in (False, True):
            for status in (OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT):
                with self.subTest(complete=complete, status=status):
                    state = complete_customer_state()
                    if not complete:
                        state.phone = None
                    state.status = status
                    state.pending_field = "old_field"
                    state.missing_customer_fields = ["old_field"]
                    state.last_question = "Existing response-layer wording"
                    state.conflicting_fields = {"table_size": ["7ft", "8ft"]}
                    state.pending_system_fields = ["product_sku", "total_price"]
                    state.pending_confirmations = ["final_order_confirmed"]
                    state.failure_reason = "Existing diagnostic"
                    state.updated_at = "2026-01-01T00:00:00+00:00"
                    before = state.model_dump()
                    now = "2026-01-02T00:00:00+00:00"
                    with patch("order_creation_controller.current_utc_time", return_value=now):
                        result = handle_collect_requirements(state)
                    expected = before.copy()
                    expected.update(
                        missing_customer_fields=[] if complete else ["phone"],
                        pending_field=None if complete else "phone",
                        status=OrderWorkflowStatus.ACTIVE if complete else OrderWorkflowStatus.AWAITING_USER_INPUT,
                        current_stage=OrderCreationStage.VALIDATE_CONFIGURATION if complete else OrderCreationStage.COLLECT_REQUIREMENTS,
                        updated_at=now,
                    )
                    self.assertEqual(state.model_dump(), expected)
                    if complete:
                        self.assertIsNone(result)
                    else:
                        self.assertEqual(result.reason, BusinessResultReason.MISSING_REQUIRED_INFORMATION)

    def test_invalid_invocations_raise_without_mutation(self):
        cases = [
            ("current_stage", stage)
            for stage in OrderCreationStage
            if stage != OrderCreationStage.COLLECT_REQUIREMENTS
        ] + [
            ("status", status)
            for status in (OrderWorkflowStatus.COMPLETED, OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED)
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                state = complete_customer_state()
                setattr(state, field, value)
                before = state.model_dump_json()
                with self.assertRaisesRegex(ValueError, "Handler requires"):
                    handle_collect_requirements(state)
                self.assertEqual(state.model_dump_json(), before)

    def test_result_lists_do_not_alias_workflow_tracking(self):
        state = OrderCreationState(conversation_id="ATS-TEST-001")
        result = handle_collect_requirements(state)
        result.data["missing_fields"].clear()
        result.required_input.append("unrelated")
        self.assertEqual(state.missing_customer_fields, list(REQUIRED_CUSTOMER_FIELDS))
        self.assertEqual(state.pending_field, "customer_name")
        second = handle_collect_requirements(state)
        state.missing_customer_fields.clear()
        self.assertEqual(second.data["missing_fields"], list(REQUIRED_CUSTOMER_FIELDS))
        self.assertEqual(second.required_input, ["customer_name"])


if __name__ == "__main__":
    unittest.main()
