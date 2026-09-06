"""In-memory tests for the Stage 2 collection handler."""

import unittest
from unittest.mock import patch

from business_result import BusinessResultReason, BusinessResultStatus
from order_creation_controller import handle_collect_requirements, handle_validate_configuration
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


class ValidateConfigurationTests(unittest.TestCase):
    def validation_state(self):
        state = complete_customer_state()
        state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
        return state

    def test_collection_then_suitable_validation_continues(self):
        state = complete_customer_state()
        self.assertIsNone(handle_collect_requirements(state))
        self.assertIsNone(handle_validate_configuration(state))
        self.assertEqual(state.current_stage, OrderCreationStage.CONFIGURATION_CONFIRMATION)
        self.assertEqual(state.status, OrderWorkflowStatus.ACTIVE)
        self.assertEqual(state.room_size_validation_result, "SUITABLE")
        self.assertIsNone(state.pending_field)
        self.assertIsNone(state.order_id)
        self.assertNotIn("result_status", state.model_dump())

    def test_unsuitable_waits_for_table_size(self):
        state = self.validation_state()
        state.room_size = "4.9m x 3.8m"
        result = handle_validate_configuration(state)
        self.assertEqual(state.current_stage, OrderCreationStage.VALIDATE_CONFIGURATION)
        self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(state.pending_field, "table_size")
        self.assertEqual(state.room_size_validation_result, "UNSUITABLE")
        self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
        self.assertEqual(result.reason, BusinessResultReason.ROOM_SIZE_UNSUITABLE)
        self.assertEqual(result.required_input, ["table_size"])
        self.assertEqual(result.current_stage, "VALIDATE_CONFIGURATION")
        self.assertEqual(result.data, {
            "room_size": "4.9m x 3.8m", "requested_table_size": "8ft",
            "suitable_table_sizes": ["7ft"],
        })

    def test_unusable_values_are_preserved_and_requested_one_at_a_time(self):
        state = self.validation_state()
        state.table_size, state.room_size = "6ft", "large"
        state.room_size_validation_result = "SUITABLE"
        for field in ("table_size", "room_size"):
            result = handle_validate_configuration(state)
            self.assertEqual(result.required_input, [field])
            self.assertEqual(state.pending_field, field)
            self.assertEqual(result.reason, BusinessResultReason.MISSING_REQUIRED_INFORMATION)
            self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
            self.assertEqual(result.current_stage, "VALIDATE_CONFIGURATION")
            self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
            self.assertIsNone(state.room_size_validation_result)
            self.assertEqual(state.missing_customer_fields, [])
            self.assertEqual(state.room_size, "large")
            self.assertEqual(state.table_size, "6ft" if field == "table_size" else "8ft")
            state.table_size = "8ft"
        self.assertEqual(state.pending_system_fields, ["room_size_validation_result"])
        state.room_size = "5.2m x 4m"
        self.assertIsNone(handle_validate_configuration(state))
        self.assertEqual(state.pending_system_fields, [])

    def test_validator_required_input_is_copied_into_result(self):
        state = self.validation_state()
        validation = {
            "result": None,
            "required_input": ["room_size"],
            "input_details": {"field": "room_size"},
        }
        with patch("order_creation_controller.validate_room_size", return_value=validation):
            result = handle_validate_configuration(state)
        self.assertEqual(state.pending_field, validation["required_input"][0])
        self.assertEqual(result.required_input, validation["required_input"])
        self.assertIsNot(result.required_input, validation["required_input"])
        validation["required_input"].clear()
        self.assertEqual(result.required_input, ["room_size"])

    def test_either_corrected_value_is_reevaluated(self):
        for field, value in (("table_size", "7ft"), ("room_size", "5.2m x 4m")):
            with self.subTest(field=field):
                state = self.validation_state()
                state.room_size = "4.9m x 3.8m"
                handle_validate_configuration(state)
                setattr(state, field, value)
                self.assertIsNone(handle_validate_configuration(state))
                self.assertEqual(state.room_size_validation_result, "SUITABLE")

    def test_missing_information_delegates_and_returns_identical_result(self):
        state = self.validation_state()
        state.phone = None
        state.room_size_validation_result = "SUITABLE"
        returned = []
        def collect(current):
            result = handle_collect_requirements(current)
            returned.append(result)
            return result
        with patch("order_creation_controller.handle_collect_requirements", side_effect=collect) as handler:
            result = handle_validate_configuration(state)
        handler.assert_called_once_with(state)
        self.assertIs(result, returned[0])
        self.assertEqual(result.current_stage, "COLLECT_REQUIREMENTS")
        self.assertEqual(state.current_stage, OrderCreationStage.COLLECT_REQUIREMENTS)
        self.assertEqual(result.required_input, ["phone"])
        self.assertIsNone(state.room_size_validation_result)
        self.assertIn("room_size_validation_result", state.pending_system_fields)

    def test_unrelated_state_preserved_on_every_branch(self):
        mutable = {"missing_customer_fields", "pending_system_fields", "pending_field",
                   "status", "current_stage", "updated_at", "room_size_validation_result"}
        for branch in ("suitable", "unsuitable", "unusable", "missing"):
            with self.subTest(branch=branch):
                state = self.validation_state()
                if branch == "unsuitable": state.room_size = "1m x 1m"
                if branch == "unusable": state.room_size = "large"
                if branch == "missing": state.phone = None
                state.last_question = "Existing wording"
                state.conflicting_fields = {"table_size": ["7ft", "8ft"]}
                state.pending_confirmations = ["final_order_confirmed"]
                state.pending_system_fields = ["product_sku", "room_size_validation_result", "total_price"]
                state.failure_reason = "Existing diagnostic"
                before = state.model_dump()
                now = "2026-01-02T00:00:00+00:00"
                with patch("order_creation_controller.current_utc_time", return_value=now):
                    handle_validate_configuration(state)
                self.assertEqual(state.updated_at, now)
                self.assertEqual(
                    {k: v for k, v in before.items() if k not in mutable},
                    {k: v for k, v in state.model_dump().items() if k not in mutable},
                )
                expected = ["product_sku", "total_price"] if branch in ("suitable", "unsuitable") else before["pending_system_fields"]
                self.assertEqual(state.pending_system_fields, expected)

    def test_invalid_invocation_does_not_mutate(self):
        cases = [("current_stage", s) for s in OrderCreationStage
                 if s != OrderCreationStage.VALIDATE_CONFIGURATION]
        cases += [("status", s) for s in (OrderWorkflowStatus.COMPLETED,
                  OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                state = self.validation_state()
                setattr(state, field, value)
                before = state.model_dump_json()
                with self.assertRaises(ValueError):
                    handle_validate_configuration(state)
                self.assertEqual(state.model_dump_json(), before)


if __name__ == "__main__":
    unittest.main()
