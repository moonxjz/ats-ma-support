"""In-memory tests for the Stage 2 collection handler."""

import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from entity.business_result import BusinessResultReason, BusinessResultStatus
from workflow.order.order_creation_controller import (
    execute_order_creation_workflow,
    apply_order_creation_reentry,
    handle_configuration_confirmation,
    handle_collect_requirements,
    handle_validate_configuration,
    handle_pricing,
    handle_create_order,
)
from entity.order_creation_state import (
    OrderCreationStage,
    OrderCreationState,
    OrderWorkflowStatus,
    REQUIRED_CUSTOMER_FIELDS,
)
from tests.test_order_creation_state import complete_customer_state as schema_complete_state


def complete_customer_state():
    """Catalog-valid workflow fixture; Stage 1 completeness fixture stays unchanged."""
    state = schema_complete_state()
    state.product_model = "Odyssey"
    state.timber = "Tassie Oak"
    state.felt_color = "Grey"
    state.bracket = "Standard rubber"
    state.top_profile = "Waterfall"
    return state


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
                    with patch("workflow.order.order_creation_controller.current_utc_time", return_value=now):
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
            self.assertEqual(result.reason, BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE
                             if field == "table_size" else BusinessResultReason.MISSING_REQUIRED_INFORMATION)
            self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
            self.assertEqual(result.current_stage, "VALIDATE_CONFIGURATION")
            self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
            if field == "room_size":
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
        with patch("workflow.order.order_creation_controller.validate_room_size", return_value=validation):
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
        with patch("workflow.order.order_creation_controller.handle_collect_requirements", side_effect=collect) as handler:
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
                with patch("workflow.order.order_creation_controller.current_utc_time", return_value=now):
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


class CatalogValidationTests(unittest.TestCase):
    def test_unsupported_values_stop_before_room_and_confirmation(self):
        cases = [("felt_color", "Green", "Felt"),
                 ("product_model", "Unknown", "Table Design Model"),
                 ("table_size", "6ft", "Table Design Model"),
                 ("top_profile", "Unknown", "Top Rail Profile"),
                 ("bracket", "Unknown", "Bracket"),
                 ("timber", "Tasmanian Oak", "Timber"),
                 ("timber_painting", "Unknown", "Timber Paint")]
        for field, value, category in cases:
            with self.subTest(field=field):
                state = complete_customer_state()
                setattr(state, field, value)
                state.last_question = "Preserve wording"
                state.failure_reason = "Preserve diagnostic"
                state.conflicting_fields = {"other": ["value"]}
                state.pending_system_fields = ["product_sku"]
                confirmations = state.pending_confirmations.copy()
                with patch("workflow.order.order_creation_controller.validate_room_size") as room, \
                     patch("workflow.order.order_creation_controller.handle_configuration_confirmation") as confirm:
                    result = execute_order_creation_workflow(state)
                room.assert_not_called()
                confirm.assert_not_called()
                self.assertEqual(getattr(state, field), value)
                self.assertEqual(state.missing_customer_fields, [])
                self.assertEqual(state.current_stage, OrderCreationStage.VALIDATE_CONFIGURATION)
                self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
                self.assertEqual(state.pending_field, field)
                self.assertEqual(result.required_input, [field])
                self.assertEqual(result.reason, BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE)
                self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
                self.assertEqual(result.data["field"], field)
                self.assertEqual(result.data["category"], category)
                self.assertEqual(result.data["supplied_value"], value)
                self.assertTrue(result.data["allowed_values"])
                if field == "felt_color":
                    self.assertEqual(result.data["allowed_values"], ["Olive", "Blue", "Burgundy", "Black", "Red", "Purple", "Grey"])
                self.assertEqual(state.order_snapshot, {})
                self.assertEqual(state.last_question, "Preserve wording")
                self.assertEqual(state.failure_reason, "Preserve diagnostic")
                self.assertEqual(state.conflicting_fields, {"other": ["value"]})
                self.assertEqual(state.pending_system_fields, ["product_sku"])
                self.assertEqual(state.pending_confirmations, confirmations)

    def test_canonicalization_precedes_room_and_snapshot(self):
        state = complete_customer_state()
        for field in ("product_model", "table_size", "top_profile", "bracket",
                      "felt_color", "timber", "timber_painting"):
            setattr(state, field, " " + getattr(state, field).upper() + " ")
        result = execute_order_creation_workflow(state)
        expected = complete_customer_state()
        for field in ("product_model", "table_size", "top_profile", "bracket",
                      "felt_color", "timber", "timber_painting"):
            self.assertEqual(getattr(state, field), getattr(expected, field))
            self.assertEqual(result.data["configuration_snapshot"][field], getattr(expected, field))
        self.assertEqual(state.room_size_validation_result, "SUITABLE")
        self.assertIsNone(state.product_sku)
        self.assertIsNone(state.unit_price)

    def test_corrections_are_one_at_a_time_and_canonicalization_waits(self):
        state = complete_customer_state()
        state.product_model = " odyssey "
        state.top_profile = "Unknown"
        state.felt_color = "Green"
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.required_input, ["top_profile"])
        self.assertEqual(state.product_model, " odyssey ")
        state.top_profile = "Waterfall"
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.required_input, ["felt_color"])
        state.felt_color = " blue "
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
        self.assertEqual(state.order_snapshot["felt_color"], "Blue")

    def test_completeness_precedes_catalog_and_technical_errors_propagate(self):
        state = complete_customer_state()
        state.phone = None
        with patch("workflow.order.order_creation_controller.lookup_base_product") as lookup:
            result = execute_order_creation_workflow(state)
        lookup.assert_not_called()
        self.assertEqual(result.current_stage, "COLLECT_REQUIREMENTS")
        state.phone = "0400000000"
        with patch("workflow.order.order_creation_controller.lookup_base_product", side_effect=ValueError("Bad catalog")):
            with self.assertRaisesRegex(ValueError, "Bad catalog"):
                execute_order_creation_workflow(state)
        self.assertIsNone(state.failure_reason)
        self.assertNotEqual(state.status, OrderWorkflowStatus.FAILED)


class WorkflowExecutionTests(unittest.TestCase):
    def test_returns_exact_handler_result(self):
        for stage, handler in (
            (OrderCreationStage.COLLECT_REQUIREMENTS, handle_collect_requirements),
            (OrderCreationStage.VALIDATE_CONFIGURATION, handle_validate_configuration),
        ):
            with self.subTest(stage=stage):
                state = complete_customer_state()
                state.current_stage = stage
                state.phone = None
                returned = []

                def capture(current):
                    result = handler(current)
                    returned.append(result)
                    return result

                with patch(f"workflow.order.order_creation_controller.{handler.__name__}", side_effect=capture):
                    result = execute_order_creation_workflow(state)
                self.assertIs(result, returned[0])
                self.assertEqual(result.current_stage, "COLLECT_REQUIREMENTS")
                self.assertEqual(result.required_input, ["phone"])

    def test_automatically_dispatches_collection_to_validation(self):
        for room, reason in (
            ("1m x 1m", BusinessResultReason.ROOM_SIZE_UNSUITABLE),
            ("large", BusinessResultReason.MISSING_REQUIRED_INFORMATION),
        ):
            with self.subTest(room=room):
                state = complete_customer_state()
                state.room_size = room
                result = execute_order_creation_workflow(state)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.current_stage, "VALIDATE_CONFIGURATION")
                self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)

    def test_successful_validation_returns_confirmation_request(self):
        state = complete_customer_state()
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
        self.assertEqual(state.current_stage, OrderCreationStage.CONFIGURATION_CONFIRMATION)
        self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(state.room_size_validation_result, "SUITABLE")
        self.assertIsNone(state.failure_reason)
        self.assertIsNone(state.order_id)

    def test_direct_unimplemented_stages_do_not_mutate(self):
        for stage in OrderCreationStage:
            if stage in (
                OrderCreationStage.COLLECT_REQUIREMENTS,
                OrderCreationStage.VALIDATE_CONFIGURATION,
                OrderCreationStage.CONFIGURATION_CONFIRMATION,
                OrderCreationStage.PRICING,
                OrderCreationStage.FINAL_CONFIRMATION,
                OrderCreationStage.CREATE_ORDER,
            ):
                continue
            with self.subTest(stage=stage):
                state = complete_customer_state()
                state.current_stage = stage
                before = state.model_dump_json()
                with self.assertRaisesRegex(NotImplementedError, stage.value):
                    execute_order_creation_workflow(state)
                self.assertEqual(state.model_dump_json(), before)

    def test_invalid_entry_rejected_without_dispatch_or_mutation(self):
        cases = [("status", status) for status in (
            OrderWorkflowStatus.COMPLETED, OrderWorkflowStatus.FAILED,
            OrderWorkflowStatus.CANCELLED,
        )]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                state = complete_customer_state()
                setattr(state, field, value)
                before = state.model_dump_json()
                with patch("workflow.order.order_creation_controller.handle_collect_requirements") as handler:
                    with self.assertRaises(ValueError):
                        execute_order_creation_workflow(state)
                handler.assert_not_called()
                self.assertEqual(state.model_dump_json(), before)

    def test_none_without_advancement_is_rejected(self):
        with patch("workflow.order.order_creation_controller.handle_collect_requirements", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "without stage advancement"):
                execute_order_creation_workflow(complete_customer_state())

    def test_internal_cycle_is_rejected(self):
        def back_to_collection(state):
            state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS

        with patch("workflow.order.order_creation_controller.handle_validate_configuration", side_effect=back_to_collection) as handler:
            with self.assertRaisesRegex(RuntimeError, "cycle"):
                execute_order_creation_workflow(complete_customer_state())
        self.assertEqual(handler.call_count, 1)

    def test_unexpected_handler_return_is_rejected(self):
        for value in (False, {}, "unexpected"):
            with self.subTest(value=value):
                with patch("workflow.order.order_creation_controller.handle_collect_requirements", return_value=value):
                    with self.assertRaisesRegex(RuntimeError, "Unexpected handler return"):
                        execute_order_creation_workflow(complete_customer_state())

    def test_non_active_continuation_is_rejected(self):
        for status in (OrderWorkflowStatus.AWAITING_USER_INPUT, OrderWorkflowStatus.COMPLETED,
                       OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED):
            with self.subTest(status=status):
                def invalid_continuation(state):
                    state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
                    state.status = status

                with patch("workflow.order.order_creation_controller.handle_collect_requirements", side_effect=invalid_continuation):
                    with self.assertRaisesRegex(RuntimeError, "continuation requires status=ACTIVE"):
                        execute_order_creation_workflow(complete_customer_state())

    def test_cycle_tracking_resets_between_customer_turns(self):
        state = complete_customer_state()
        state.room_size = "1m x 1m"
        first = execute_order_creation_workflow(state)
        second = execute_order_creation_workflow(state)
        self.assertEqual(first.reason, second.reason)
        state.room_size = "5.2m x 4m"
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)


class ConfirmationAndReentryTests(unittest.TestCase):
    def waiting_state(self):
        state = complete_customer_state()
        execute_order_creation_workflow(state)
        return state

    def test_waiting_result_tracking_aliases_and_repetition(self):
        for status in (OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT):
            state = self.waiting_state()
            state.status = status
            state.last_question = "Previous wording"
            state.conflicting_fields = {"felt_color": ["Blue", "Green"]}
            state.pending_confirmations = ["configuration_confirmed", "final_order_confirmed", "configuration_confirmed"]
            before = state.model_dump()
            with patch("workflow.order.order_creation_controller.current_utc_time", return_value="fixed"):
                result = handle_configuration_confirmation(state)
                again = handle_configuration_confirmation(state)
            self.assertEqual(result, again)
            self.assertEqual(result.result_status, BusinessResultStatus.NEEDS_USER_INPUT)
            self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
            self.assertEqual(result.required_input, ["configuration_confirmed"])
            self.assertEqual(state.pending_confirmations, ["final_order_confirmed", "configuration_confirmed"])
            self.assertEqual(result.data["configuration_snapshot"], state.order_snapshot)
            self.assertIsNot(result.data["configuration_snapshot"], state.order_snapshot)
            result.data["configuration_snapshot"].clear()
            self.assertTrue(state.order_snapshot)
            changed = {"order_snapshot", "status", "pending_field", "configuration_confirmed", "pending_confirmations", "updated_at"}
            self.assertEqual({k:v for k,v in before.items() if k not in changed},
                             {k:v for k,v in state.model_dump().items() if k not in changed})
            self.assertEqual(state.updated_at, "fixed")

    def test_invalid_confirmation_entry_does_not_mutate(self):
        cases = [("configuration_confirmed", True, NotImplementedError),
                 ("phone", None, ValueError), ("room_size_validation_result", None, ValueError),
                 ("room_size_validation_result", "UNSUITABLE", ValueError)]
        cases += [("status", status, ValueError) for status in
                  (OrderWorkflowStatus.COMPLETED, OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED)]
        cases += [("current_stage", stage, ValueError) for stage in OrderCreationStage
                  if stage != OrderCreationStage.CONFIGURATION_CONFIRMATION]
        for field, value, error in cases:
            with self.subTest(field=field, value=value):
                state = self.waiting_state()
                setattr(state, field, value)
                before = state.model_dump()
                with self.assertRaises(error):
                    handle_configuration_confirmation(state)
                self.assertEqual(state.model_dump(), before)

    def test_effective_changes_invalidate_only_approved_fields(self):
        from workflow.order.order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        updates = [{"felt_color": "Green"}, {"table_size": "9ft"}, {"room_size": "6m x 5m"},
                   {"phone": "0400123456"}, {"delivery_address": {"city": "Richmond"}},
                   {"company_name": None}, {"customer_instructions": None},
                   {"delivery_address": {"address": "Suite 3, 1 Example Street"}}, {"quantity": 2},
                   {"felt_color": "Green", "phone": "0400123456"}]
        for values in updates:
            with self.subTest(values=values):
                previous = self.waiting_state()
                previous.company_name = "Company"
                previous.customer_instructions = "Instructions"
                previous.delivery_address.address = "Suite 2, 1 Example Street"
                previous.configuration_confirmed = True
                previous.pending_field = "old"
                previous.pending_system_fields = ["sku", "room_size_validation_result", "price", "room_size_validation_result"]
                previous.pending_confirmations = ["configuration_confirmed", "final_order_confirmed", "configuration_confirmed"]
                previous.last_question = "Old question"
                previous.failure_reason = "Old diagnostic"
                previous.conflicting_fields = {"felt_color": ["Blue"]}
                before = previous.model_dump()
                updated = apply_extracted_order_information(previous, ExtractedOrderInformation(**values))
                expected = updated.model_dump()
                expected.update(current_stage=OrderCreationStage.COLLECT_REQUIREMENTS,
                                status=OrderWorkflowStatus.ACTIVE, pending_field=None,
                                configuration_confirmed=False, room_size_validation_result=None,
                                order_snapshot={}, updated_at="fixed",
                                pending_system_fields=["sku", "price", "room_size_validation_result"],
                                pending_confirmations=["final_order_confirmed"])
                with patch("workflow.order.order_creation_controller.current_utc_time", return_value="fixed"):
                    self.assertIsNone(apply_order_creation_reentry(previous, updated))
                self.assertEqual(updated.model_dump(), expected)
                self.assertEqual(previous.model_dump(), before)

    def test_no_change_and_outside_scope_are_noops(self):
        from workflow.order.order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        previous = self.waiting_state()
        for values in ({}, {"table_size": "8ft"}, {"delivery_address": {}}, {"company_name": None}):
            updated = apply_extracted_order_information(previous, ExtractedOrderInformation(**values))
            updated.updated_at = "Different timestamp"
            updated.failure_reason = "Different diagnostic"
            before = updated.model_dump()
            apply_order_creation_reentry(previous, updated)
            self.assertEqual(updated.model_dump(), before)
        for stage in OrderCreationStage:
            for status in OrderWorkflowStatus:
                if stage == OrderCreationStage.CONFIGURATION_CONFIRMATION and status in (
                    OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT):
                    continue
                previous = self.waiting_state()
                previous.current_stage, previous.status = stage, status
                updated = apply_extracted_order_information(previous, ExtractedOrderInformation(felt_color="Green"))
                before = updated.model_dump()
                apply_order_creation_reentry(previous, updated)
                self.assertEqual(updated.model_dump(), before)

    def test_reentry_routes_through_actual_handlers(self):
        from workflow.order.order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        cases = [({"felt_color": "Green"}, "UNSUPPORTED_CONFIGURATION_VALUE", False),
                 ({"table_size": "9ft"}, "ROOM_SIZE_UNSUITABLE", False),
                 ({"room_size": "6m x 5m"}, "CONFIGURATION_CONFIRMATION_REQUIRED", True),
                 ({"room_size": "large"}, "MISSING_REQUIRED_INFORMATION", False),
                 ({"table_size": "11ft"}, "UNSUPPORTED_CONFIGURATION_VALUE", False),
                 ({"phone": "0400123456"}, "CONFIGURATION_CONFIRMATION_REQUIRED", True)]
        for values, reason, confirms in cases:
            with self.subTest(values=values):
                previous = self.waiting_state()
                updated = apply_extracted_order_information(previous, ExtractedOrderInformation(**values))
                apply_order_creation_reentry(previous, updated)
                order = []
                def capture(name, handler):
                    def run(state):
                        order.append(name)
                        return handler(state)
                    return run
                with patch("workflow.order.order_creation_controller.handle_collect_requirements", side_effect=capture("collect", handle_collect_requirements)), \
                     patch("workflow.order.order_creation_controller.handle_validate_configuration", side_effect=capture("validate", handle_validate_configuration)), \
                     patch("workflow.order.order_creation_controller.handle_configuration_confirmation", side_effect=capture("confirm", handle_configuration_confirmation)):
                    result = execute_order_creation_workflow(updated)
                self.assertEqual(order, ["collect", "validate"] + (["confirm"] if confirms else []))
                self.assertEqual(result.reason.value, reason)
                self.assertEqual("configuration_confirmed" in updated.pending_confirmations, confirms)
                if confirms:
                    self.assertEqual(updated.order_snapshot["felt_color"], updated.felt_color)
                else:
                    self.assertEqual(updated.order_snapshot, {})


class PricingTests(unittest.TestCase):
    def pricing_state(self, quantity=2, postcode="3000"):
        from workflow.order.order_creation_rules import build_configuration_snapshot
        state = complete_customer_state()
        state.bracket = "Stainless Steel"
        state.quantity = quantity
        state.delivery_address.postcode = postcode
        state.current_stage = OrderCreationStage.PRICING
        state.configuration_confirmed = True
        state.room_size_validation_result = "SUITABLE"
        state.order_snapshot = build_configuration_snapshot(state)
        return state

    def test_exact_values_and_only_approved_mutations(self):
        state = self.pricing_state()
        state.pending_system_fields = ["other", "product_sku", "unit_price", "customisation_price",
                                       "shipping_cost", "total_price", "shipping_method", "total_price", "other"]
        state.last_question = "Keep wording"
        state.failure_reason = "Keep diagnostic"
        state.conflicting_fields = {"felt_color": ["Blue"]}
        state.pending_field = "keep tracking"
        before = state.model_dump()
        snapshot = state.order_snapshot
        with patch("workflow.order.order_creation_controller.current_utc_time", return_value="fixed"):
            self.assertIsNone(handle_pricing(state))
        expected = {**before, "product_sku": "B8ODYSSEY", "customisation_price": Decimal("1550"),
                    "unit_price": Decimal("6800"), "shipping_cost": Decimal("1060"),
                    "total_price": Decimal("14660"), "pending_system_fields": ["other", "shipping_method", "other"],
                    "current_stage": OrderCreationStage.FINAL_CONFIRMATION, "updated_at": "fixed"}
        self.assertEqual(state.model_dump(), expected)
        self.assertIs(state.order_snapshot, snapshot)
        self.assertTrue(state.configuration_confirmed)

    def test_unknown_postcode_completes_under_mvp_assumption(self):
        state = self.pricing_state(postcode="3152")
        state.pending_system_fields = ["shipping_cost", "total_price"]
        self.assertIsNone(handle_pricing(state))
        self.assertEqual(state.shipping_cost, Decimal("0"))
        self.assertEqual(state.total_price, Decimal("13600"))
        self.assertEqual(state.pending_system_fields, [])
        self.assertIsNone(state.failure_reason)
        self.assertIsNone(state.shipping_method)

    def test_invalid_prerequisites_do_not_mutate_or_lookup(self):
        cases = [("current_stage", stage) for stage in OrderCreationStage if stage != OrderCreationStage.PRICING]
        cases += [("status", status) for status in OrderWorkflowStatus if status != OrderWorkflowStatus.ACTIVE]
        cases += [("configuration_confirmed", False), ("order_snapshot", {}),
                  ("order_snapshot", {"table_size": "8ft"}), ("felt_color", "Blue"),
                  ("quantity", 0), ("quantity", -1), ("quantity", True), ("quantity", 2.0)]
        for field, value in cases:
            state = self.pricing_state()
            setattr(state, field, value)
            # Compare attributes without serializing deliberately corrupted types.
            before = state.model_copy(deep=True)
            with self.subTest(field=field, value=value), \
                 patch("workflow.order.order_creation_controller.lookup_product_pricing") as product:
                with self.assertRaises(ValueError):
                    handle_pricing(state)
                product.assert_not_called()
            self.assertEqual(state, before)
        for postcode in (None, "", "  "):
            state = self.pricing_state(postcode=postcode)
            before = state.model_copy(deep=True)
            with self.assertRaises(ValueError):
                handle_pricing(state)
            self.assertEqual(state, before)

    def test_lookup_calculation_and_clock_failures_leave_state_unchanged(self):
        cases = [("lookup_product_pricing", LookupError("missing product")),
                 ("lookup_shipping_rate", LookupError("missing rate")),
                 ("lookup_shipping_rate", ValueError("corrupt data")),
                 ("calculate_total_price", ValueError("invalid money")),
                 ("current_utc_time", RuntimeError("clock failure"))]
        for target, error in cases:
            state = self.pricing_state()
            state.product_sku = "old SKU"
            state.unit_price = Decimal("1")
            state.customisation_price = Decimal("2")
            state.shipping_cost = Decimal("3")
            state.total_price = Decimal("4")
            state.pending_system_fields = ["unit_price", "shipping_cost", "other"]
            before = state.model_dump()
            with self.subTest(target=target), patch("workflow.order.order_creation_controller." + target, side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    handle_pricing(state)
                self.assertIs(caught.exception, error)
            self.assertEqual(state.model_dump(), before)

    def test_confirmed_noncanonical_or_missing_catalog_value_is_exception(self):
        from workflow.order.order_creation_rules import build_configuration_snapshot
        for value, error in ((" blue ", ValueError), ("Green", LookupError)):
            state = self.pricing_state()
            state.felt_color = value
            state.order_snapshot = build_configuration_snapshot(state)
            before = state.model_dump()
            with self.assertRaises(error):
                handle_pricing(state)
            self.assertEqual(state.model_dump(), before)

    def test_controller_boundary_preserves_priced_progress(self):
        state = self.pricing_state(quantity=1)
        result = execute_order_creation_workflow(state)
        self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
        self.assertEqual(state.current_stage, OrderCreationStage.FINAL_CONFIRMATION)
        self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
        self.assertEqual(state.total_price, Decimal("7330"))
        self.assertIsNone(state.failure_reason)
        self.assertIsNone(state.order_id)


class FinalReentryTests(unittest.TestCase):
    PRICES = ("product_sku", "customisation_price", "unit_price", "shipping_cost", "total_price")

    def final_state(self):
        state = PricingTests().pricing_state()
        handle_pricing(state)
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.pending_field = "old"
        state.pending_confirmations = ["other", "final_order_confirmed", "final_order_confirmed"]
        state.last_question = "Keep wording"
        state.failure_reason = "Keep diagnostic"
        state.conflicting_fields = {"other": ["value"]}
        return state

    def merge_and_route(self, previous, values):
        from workflow.order.order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        before = previous.model_dump()
        updated = apply_extracted_order_information(previous, ExtractedOrderInformation(**values))
        with patch("workflow.order.order_creation_controller.current_utc_time", return_value="fixed"):
            apply_order_creation_reentry(previous, updated)
        self.assertEqual(previous.model_dump(), before)
        self.assertEqual(updated.last_question, previous.last_question)
        self.assertEqual(updated.failure_reason, previous.failure_reason)
        self.assertEqual(updated.conflicting_fields, previous.conflicting_fields)
        return updated

    def test_configuration_and_room_reset_exact_state_and_priority(self):
        changes = [{field: "different"} for field in (
            "product_model", "table_size", "top_profile", "bracket", "felt_color", "timber", "timber_painting", "room_size")]
        changes += [{"felt_color": "Green", "quantity": 3, "delivery_address": {"postcode": "3152"}, "phone": "123"}]
        for values in changes:
            with self.subTest(values=values):
                previous = self.final_state()
                previous.pending_confirmations += ["configuration_confirmed"] * 2
                previous.pending_system_fields = ["first", *self.PRICES, "room_size_validation_result", "last", *self.PRICES]
                updated = self.merge_and_route(previous, values)
                self.assertEqual(updated.current_stage, OrderCreationStage.COLLECT_REQUIREMENTS)
                self.assertEqual(updated.status, OrderWorkflowStatus.ACTIVE)
                self.assertFalse(updated.configuration_confirmed)
                self.assertFalse(updated.final_order_confirmed)
                self.assertEqual(updated.order_snapshot, {})
                self.assertIsNone(updated.room_size_validation_result)
                self.assertIsNone(updated.pending_field)
                self.assertEqual(updated.updated_at, "fixed")
                self.assertTrue(all(getattr(updated, f) is None for f in self.PRICES))
                self.assertEqual(updated.pending_confirmations, ["other"])
                self.assertEqual(updated.pending_system_fields, ["first", "last", "room_size_validation_result", *self.PRICES])

    def test_quantity_and_postcode_reprice_with_historical_snapshot(self):
        for values, shipping, total in (
            ({"quantity": 3}, "1590", "21990"),
            ({"delivery_address": {"postcode": "3152"}}, "0", "13600"),
            ({"quantity": 3, "phone": "123"}, "1590", "21990"),
        ):
            with self.subTest(values=values):
                previous = self.final_state()
                previous.pending_system_fields = ["shipping_method", *self.PRICES, "other", *self.PRICES]
                updated = self.merge_and_route(previous, values)
                self.assertEqual(updated.current_stage, OrderCreationStage.PRICING)
                self.assertTrue(updated.configuration_confirmed)
                self.assertEqual(updated.order_snapshot, previous.order_snapshot)
                self.assertEqual(updated.order_snapshot["quantity"], 2)
                snapshot = updated.order_snapshot
                self.assertEqual(updated.room_size_validation_result, previous.room_size_validation_result)
                self.assertEqual(updated.pending_confirmations, ["other"])
                self.assertTrue(all(getattr(updated, f) is None for f in self.PRICES))
                self.assertEqual(updated.pending_system_fields, ["shipping_method", "other", *self.PRICES])
                result = execute_order_creation_workflow(updated)
                self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
                self.assertEqual(updated.shipping_cost, Decimal(shipping))
                self.assertEqual(updated.total_price, Decimal(total))
                self.assertIs(updated.order_snapshot, snapshot)
                self.assertEqual(updated.pending_system_fields, ["shipping_method", "other"])

    def test_other_customer_changes_preserve_prices(self):
        for values in ({"phone": "123"}, {"email": "new@example.com"}, {"customer_name": "New"},
                       {"delivery_address": {"city": "Richmond"}}, {"company_name": "Company"},
                       {"customer_instructions": "New instructions"}):
            previous = self.final_state()
            updated = self.merge_and_route(previous, values)
            self.assertEqual(updated.current_stage, OrderCreationStage.FINAL_CONFIRMATION)
            self.assertEqual(updated.status, OrderWorkflowStatus.ACTIVE)
            self.assertIsNone(updated.pending_field)
            self.assertEqual(updated.pending_confirmations, ["other"])
            self.assertEqual(updated.updated_at, "fixed")
            for field in (*self.PRICES, "order_snapshot", "configuration_confirmed", "room_size_validation_result", "pending_system_fields"):
                self.assertEqual(getattr(updated, field), getattr(previous, field))

    def test_no_changes_and_ineligible_states_are_noops(self):
        for values in ({}, {"quantity": 2}, {"felt_color": "Grey"}, {"delivery_address": {"postcode": "3000"}}):
            previous = self.final_state()
            updated = self.merge_and_route(previous, values)
            self.assertEqual(updated.model_dump(), previous.model_dump())
        for field, value in (("final_order_confirmed", True), ("configuration_confirmed", False),
                             ("status", OrderWorkflowStatus.COMPLETED), ("status", OrderWorkflowStatus.FAILED),
                             ("status", OrderWorkflowStatus.CANCELLED)):
            previous = self.final_state()
            setattr(previous, field, value)
            updated = self.merge_and_route(previous, {"quantity": 3})
            expected = previous.model_dump()
            expected["quantity"] = 3
            self.assertEqual(updated.model_dump(), expected)

    def test_configuration_corrections_reach_real_validation(self):
        for values, reason in (({"felt_color": "Green"}, BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE),
                               ({"table_size": "9ft"}, BusinessResultReason.ROOM_SIZE_UNSUITABLE),
                               ({"room_size": "1m x 1m"}, BusinessResultReason.ROOM_SIZE_UNSUITABLE),
                               ({"felt_color": "Blue"}, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)):
            previous = self.final_state()
            updated = self.merge_and_route(previous, values)
            with patch("workflow.order.order_creation_controller.handle_collect_requirements", wraps=handle_collect_requirements) as collect, \
                 patch("workflow.order.order_creation_controller.handle_validate_configuration", wraps=handle_validate_configuration) as validate:
                result = execute_order_creation_workflow(updated)
            collect.assert_called_once()
            validate.assert_called_once()
            self.assertEqual(result.reason, reason)
            self.assertFalse(updated.configuration_confirmed)


class ConfirmationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        self.confirmed = ConfirmationInterpretation(intent="CONFIRMED")
        self.state = complete_customer_state()
        execute_order_creation_workflow(self.state)
        self.snapshot = self.state.order_snapshot.copy()

    def test_accept_exact_snapshot_then_final_confirmation_boundary(self):
        snapshot_object = self.state.order_snapshot
        with patch("workflow.order.order_creation_controller.current_utc_time", return_value="fixed"):
            self.assertIsNone(handle_configuration_confirmation(
                self.state, confirmation=self.confirmed, confirmation_snapshot=self.snapshot))
        self.assertTrue(self.state.configuration_confirmed)
        self.assertNotIn("configuration_confirmed", self.state.pending_confirmations)
        self.assertIn("final_order_confirmed", self.state.pending_confirmations)
        self.assertEqual(self.state.current_stage, OrderCreationStage.PRICING)
        self.assertEqual(self.state.status, OrderWorkflowStatus.ACTIVE)
        self.assertEqual(self.state.updated_at, "fixed")
        self.assertIs(self.state.order_snapshot, snapshot_object)
        self.assertEqual(self.state.order_snapshot, self.snapshot)
        result = execute_order_creation_workflow(self.state)
        self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
        self.assertIsNone(self.state.failure_reason)

    def test_executor_continues_through_pricing_without_business_result(self):
        result = execute_order_creation_workflow(self.state, confirmation=self.confirmed,
                                                 confirmation_snapshot=self.snapshot)
        self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
        self.assertTrue(self.state.configuration_confirmed)
        self.assertEqual(self.state.current_stage, OrderCreationStage.FINAL_CONFIRMATION)
        self.assertEqual(self.state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)

    def test_invalid_acceptance_rejected_before_mutation(self):
        cases = [("order_snapshot", {}), ("order_snapshot", {"table_size":"8ft"}),
                 ("phone", None), ("table_size", "9ft"),
                 ("room_size_validation_result", None), ("room_size_validation_result", "UNSUITABLE"),
                 ("pending_confirmations", []), ("configuration_confirmed", True),
                 ("status", OrderWorkflowStatus.ACTIVE), ("status", OrderWorkflowStatus.COMPLETED),
                 ("current_stage", OrderCreationStage.COLLECT_REQUIREMENTS)]
        for field, value in cases:
            with self.subTest(field=field):
                state = self.state.model_copy(deep=True)
                setattr(state, field, value)
                before = state.model_dump()
                with self.assertRaises(ValueError):
                    execute_order_creation_workflow(state, confirmation=self.confirmed,
                                                    confirmation_snapshot=self.snapshot)
                self.assertEqual(state.model_dump(), before)
        for snapshot in (None, {}, {**self.snapshot, "felt_color":"Green"}):
            before = self.state.model_dump()
            with self.assertRaises(ValueError):
                handle_configuration_confirmation(self.state, confirmation=self.confirmed,
                                                   confirmation_snapshot=snapshot)
            self.assertEqual(self.state.model_dump(), before)

    def test_waiting_interpretations_preserve_snapshot_and_add_context(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        for intent in ("DECLINED", "AMBIGUOUS", "CHANGE_REQUESTED"):
            with self.subTest(intent=intent):
                state = self.state.model_copy(deep=True)
                snapshot_object = state.order_snapshot
                result = execute_order_creation_workflow(state,
                    confirmation=ConfirmationInterpretation(intent=intent),
                    confirmation_snapshot=self.snapshot)
                self.assertEqual(result.reason, BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED)
                self.assertEqual(result.data["confirmation_intent"], intent)
                self.assertEqual(result.required_input, ["configuration_confirmed"])
                self.assertFalse(state.configuration_confirmed)
                self.assertEqual(state.status, OrderWorkflowStatus.AWAITING_USER_INPUT)
                self.assertIs(state.order_snapshot, snapshot_object)
                self.assertIsNot(result.data["configuration_snapshot"], snapshot_object)
                self.assertEqual(state.pending_confirmations.count("configuration_confirmed"), 1)

    def test_snapshot_without_interpretation_rejected_and_no_args_preserved(self):
        before = self.state.model_dump()
        with self.assertRaises(ValueError):
            execute_order_creation_workflow(self.state, confirmation_snapshot=self.snapshot)
        self.assertEqual(self.state.model_dump(), before)
        result = execute_order_creation_workflow(self.state)
        self.assertNotIn("confirmation_intent", result.data)



class FinalConfirmationTests(unittest.TestCase):
    def waiting(self, postcode="3000"):
        state = PricingTests().pricing_state(postcode=postcode)
        execute_order_creation_workflow(state)
        return state

    def test_waiting_repeat_no_aliasing_or_shipping_operations(self):
        from workflow.order.order_creation_controller import handle_final_confirmation
        state = PricingTests().pricing_state()
        handle_pricing(state)
        historical = state.order_snapshot
        state.pending_confirmations = ["other", "final_order_confirmed", "final_order_confirmed"]
        with patch("workflow.order.order_creation_controller.lookup_shipping_rate") as shipping, \
             patch("workflow.order.order_creation_controller.calculate_total_price") as totals:
            first = handle_final_confirmation(state)
            snapshot = state.final_order_snapshot
            before = state.model_dump()
            with patch("workflow.order.order_creation_controller.current_utc_time") as clock:
                again = handle_final_confirmation(state)
                clock.assert_not_called()
            self.assertEqual(state.model_dump(), before)
            self.assertIs(state.final_order_snapshot, snapshot)
            self.assertIs(state.order_snapshot, historical)
            self.assertEqual(first, again)
            self.assertEqual(state.pending_confirmations, ["other", "final_order_confirmed"])
            first.data["final_order_snapshot"]["delivery_address"]["city"] = "Changed"
            self.assertNotEqual(snapshot.delivery_address.city, "Changed")
            shipping.assert_not_called()
            totals.assert_not_called()

    def test_matched_and_free_shipping_authorize_exact_order(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        for postcode, cost in (("3000", Decimal("1060")), ("3152", Decimal("0"))):
            with TemporaryDirectory() as tmp:
                store_path = Path(tmp) / "orders.json"
                state = self.waiting(postcode)
                snapshot = state.final_order_snapshot
                self.assertEqual(snapshot.shipping_cost, cost)
                with patch("workflow.order.order_creation_controller.lookup_shipping_rate") as shipping, \
                     patch("workflow.order.order_creation_controller.calculate_total_price") as totals:
                    result = execute_order_creation_workflow(
                        state,
                        final_confirmation=ConfirmationInterpretation(intent="CONFIRMED"),
                        final_confirmation_snapshot=snapshot,
                        order_store_path=store_path,
                    )
                    shipping.assert_not_called()
                    totals.assert_not_called()
                self.assertEqual(result.result_status, BusinessResultStatus.SUCCESS)
                self.assertEqual(result.reason, BusinessResultReason.ORDER_CREATED)
                self.assertEqual(result.data, {
                    "order_id": "ORD-000001",
                    "order_status": "CONFIRMED",
                    "created": True,
                })
                self.assertTrue(state.final_order_confirmed)
                self.assertEqual(state.current_stage, OrderCreationStage.COMPLETED)
                self.assertEqual(state.status, OrderWorkflowStatus.COMPLETED)
                self.assertNotIn("final_order_confirmed", state.pending_confirmations)
                self.assertIs(state.final_order_snapshot, snapshot)
                self.assertEqual(state.order_id, "ORD-000001")
                self.assertEqual(state.order_status, "CONFIRMED")
                self.assertIsNone(state.failure_reason)

    def test_create_order_guards_prevent_store_call_and_mutation(self):
        for field, value in (
            ("current_stage", OrderCreationStage.FINAL_CONFIRMATION),
            ("status", OrderWorkflowStatus.AWAITING_USER_INPUT),
            ("final_order_confirmed", False),
            ("pending_confirmations", ["final_order_confirmed"]),
            ("final_order_snapshot", None),
            ("phone", "changed"),
        ):
            state = self.waiting()
            state.final_order_confirmed = True
            state.current_stage = OrderCreationStage.CREATE_ORDER
            state.status = OrderWorkflowStatus.ACTIVE
            state.pending_confirmations = [
                field for field in state.pending_confirmations if field != "final_order_confirmed"
            ]
            setattr(state, field, value)
            before = state.model_copy(deep=True)
            with self.subTest(field=field), patch("workflow.order.order_creation_controller.create_order") as create:
                with self.assertRaises(ValueError):
                    handle_create_order(state, store_path=Path("unused.json"))
                create.assert_not_called()
            self.assertEqual(state, before)

    def test_create_order_verifies_returned_record_before_success_mutation(self):
        state = self.waiting()
        state.final_order_confirmed = True
        state.current_stage = OrderCreationStage.CREATE_ORDER
        cases = (
            {"source_workflow_id": "WF-OTHER"},
            {"conversation_id": "C-OTHER"},
            {"order": state.final_order_snapshot.model_copy(update={"phone": "changed"})},
            {"order_id": "BAD-1"},
            {"order_status": "PENDING"},
        )
        for override in cases:
            state = self.waiting()
            state.final_order_confirmed = True
            state.current_stage = OrderCreationStage.CREATE_ORDER
            state.status = OrderWorkflowStatus.ACTIVE
            state.pending_confirmations = [
                field for field in state.pending_confirmations if field != "final_order_confirmed"
            ]
            base_record = {
                "order_id": "ORD-000001",
                "source_workflow_id": state.workflow_id,
                "conversation_id": state.conversation_id,
                "created_at": state.created_at,
                "order_status": "CONFIRMED",
                "order": state.final_order_snapshot,
            }
            before = state.model_copy(deep=True)
            record = SimpleNamespace(**(base_record | override))
            with self.subTest(override=override), patch(
                "workflow.order.order_creation_controller.create_order",
                return_value=SimpleNamespace(record=record, created=True),
            ):
                with self.assertRaises(RuntimeError):
                    handle_create_order(state, store_path=Path("unused.json"))
            self.assertEqual(state, before)

    def test_create_order_replay_with_matching_store_completes_without_duplicate(self):
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "orders.json"
            state = self.waiting()
            snapshot = state.final_order_snapshot
            state.final_order_confirmed = True
            state.current_stage = OrderCreationStage.CREATE_ORDER
            state.status = OrderWorkflowStatus.ACTIVE
            state.pending_confirmations = [
                field for field in state.pending_confirmations if field != "final_order_confirmed"
            ]
            first = handle_create_order(state, store_path=store_path)
            replay = self.waiting()
            replay.workflow_id = state.workflow_id
            replay.final_order_confirmed = True
            replay.current_stage = OrderCreationStage.CREATE_ORDER
            replay.status = OrderWorkflowStatus.ACTIVE
            replay.pending_confirmations = [
                field for field in replay.pending_confirmations if field != "final_order_confirmed"
            ]
            replay_result = handle_create_order(replay, store_path=store_path)
            self.assertEqual(first.data["order_id"], replay_result.data["order_id"])
            self.assertFalse(replay_result.data["created"])
            self.assertEqual(replay.order_id, "ORD-000001")
            self.assertEqual(replay.current_stage, OrderCreationStage.COMPLETED)
            self.assertEqual(replay.final_order_snapshot, snapshot)

    def test_declined_ambiguous_and_change_request_stay_waiting(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        for intent in ("DECLINED", "AMBIGUOUS", "CHANGE_REQUESTED"):
            state = self.waiting()
            before = state.model_dump()
            result = execute_order_creation_workflow(state,
                final_confirmation=ConfirmationInterpretation(intent=intent),
                final_confirmation_snapshot=state.final_order_snapshot)
            self.assertEqual(result.reason, BusinessResultReason.FINAL_CONFIRMATION_REQUIRED)
            self.assertEqual(result.required_input, ["final_order_confirmed"])
            self.assertEqual(result.data["confirmation_intent"], intent)
            self.assertEqual(state.model_dump(), before)

    def test_stale_and_invalid_evidence_raise_before_mutation(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        for field, value in (("phone", "123"), ("quantity", 3), ("shipping_cost", Decimal("0")),
                             ("final_order_snapshot", None), ("final_order_snapshot", {}),
                             ("configuration_confirmed", False), ("pending_confirmations", []),
                             ("status", OrderWorkflowStatus.ACTIVE), ("final_order_confirmed", True)):
            state = self.waiting()
            evidence = state.final_order_snapshot
            setattr(state, field, value)
            before = state.model_copy(deep=True)
            with self.subTest(field=field), self.assertRaises(ValueError):
                execute_order_creation_workflow(state,
                    final_confirmation=ConfirmationInterpretation(intent="CONFIRMED"),
                    final_confirmation_snapshot=evidence)
            self.assertEqual(state, before)

    def test_bad_arguments_and_clock_failure(self):
        from workflow.order.order_creation_confirmation import ConfirmationInterpretation
        from workflow.order.order_creation_controller import handle_final_confirmation
        state = self.waiting()
        intent = ConfirmationInterpretation(intent="CONFIRMED")
        for kwargs in ({"final_confirmation": intent}, {"final_confirmation_snapshot": state.final_order_snapshot},
                       {"confirmation": intent, "final_confirmation": intent,
                        "final_confirmation_snapshot": state.final_order_snapshot}):
            before = state.model_dump()
            with self.assertRaises(ValueError):
                execute_order_creation_workflow(state, **kwargs)
            self.assertEqual(state.model_dump(), before)
        before = state.model_dump()
        with patch("workflow.order.order_creation_controller.current_utc_time", side_effect=RuntimeError("clock")):
            with self.assertRaises(RuntimeError):
                handle_final_confirmation(state, confirmation=intent, confirmation_snapshot=state.final_order_snapshot)
        self.assertEqual(state.model_dump(), before)


if __name__ == "__main__":
    unittest.main()
