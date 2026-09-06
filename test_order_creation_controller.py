"""In-memory tests for the Stage 2 collection handler."""

import unittest
from unittest.mock import patch

from business_result import BusinessResultReason, BusinessResultStatus
from order_creation_controller import (
    execute_order_creation_workflow,
    apply_order_creation_reentry,
    handle_configuration_confirmation,
    handle_collect_requirements,
    handle_validate_configuration,
)
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

                with patch(f"order_creation_controller.{handler.__name__}", side_effect=capture):
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
            if stage in (OrderCreationStage.COLLECT_REQUIREMENTS, OrderCreationStage.VALIDATE_CONFIGURATION, OrderCreationStage.CONFIGURATION_CONFIRMATION):
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
                with patch("order_creation_controller.handle_collect_requirements") as handler:
                    with self.assertRaises(ValueError):
                        execute_order_creation_workflow(state)
                handler.assert_not_called()
                self.assertEqual(state.model_dump_json(), before)

    def test_none_without_advancement_is_rejected(self):
        with patch("order_creation_controller.handle_collect_requirements", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "without stage advancement"):
                execute_order_creation_workflow(complete_customer_state())

    def test_internal_cycle_is_rejected(self):
        def back_to_collection(state):
            state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS

        with patch("order_creation_controller.handle_validate_configuration", side_effect=back_to_collection) as handler:
            with self.assertRaisesRegex(RuntimeError, "cycle"):
                execute_order_creation_workflow(complete_customer_state())
        self.assertEqual(handler.call_count, 1)

    def test_unexpected_handler_return_is_rejected(self):
        for value in (False, {}, "unexpected"):
            with self.subTest(value=value):
                with patch("order_creation_controller.handle_collect_requirements", return_value=value):
                    with self.assertRaisesRegex(RuntimeError, "Unexpected handler return"):
                        execute_order_creation_workflow(complete_customer_state())

    def test_non_active_continuation_is_rejected(self):
        for status in (OrderWorkflowStatus.AWAITING_USER_INPUT, OrderWorkflowStatus.COMPLETED,
                       OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED):
            with self.subTest(status=status):
                def invalid_continuation(state):
                    state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
                    state.status = status

                with patch("order_creation_controller.handle_collect_requirements", side_effect=invalid_continuation):
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
            with patch("order_creation_controller.current_utc_time", return_value="fixed"):
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
        from order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        updates = [{"felt_color": "Green"}, {"table_size": "9ft"}, {"room_size": "6m x 5m"},
                   {"phone": "0400123456"}, {"delivery_address": {"city": "Richmond"}},
                   {"company_name": None}, {"customer_instructions": None},
                   {"delivery_address": {"address_line_2": None}}, {"quantity": 2},
                   {"felt_color": "Green", "phone": "0400123456"}]
        for values in updates:
            with self.subTest(values=values):
                previous = self.waiting_state()
                previous.company_name = "Company"
                previous.customer_instructions = "Instructions"
                previous.delivery_address.address_line_2 = "Suite 2"
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
                with patch("order_creation_controller.current_utc_time", return_value="fixed"):
                    self.assertIsNone(apply_order_creation_reentry(previous, updated))
                self.assertEqual(updated.model_dump(), expected)
                self.assertEqual(previous.model_dump(), before)

    def test_no_change_and_outside_scope_are_noops(self):
        from order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
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
        from order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information
        cases = [({"felt_color": "Green"}, "CONFIGURATION_CONFIRMATION_REQUIRED", True),
                 ({"table_size": "9ft"}, "ROOM_SIZE_UNSUITABLE", False),
                 ({"room_size": "6m x 5m"}, "CONFIGURATION_CONFIRMATION_REQUIRED", True),
                 ({"room_size": "large"}, "MISSING_REQUIRED_INFORMATION", False),
                 ({"table_size": "11ft"}, "MISSING_REQUIRED_INFORMATION", False),
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
                with patch("order_creation_controller.handle_collect_requirements", side_effect=capture("collect", handle_collect_requirements)), \
                     patch("order_creation_controller.handle_validate_configuration", side_effect=capture("validate", handle_validate_configuration)), \
                     patch("order_creation_controller.handle_configuration_confirmation", side_effect=capture("confirm", handle_configuration_confirmation)):
                    result = execute_order_creation_workflow(updated)
                self.assertEqual(order, ["collect", "validate"] + (["confirm"] if confirms else []))
                self.assertEqual(result.reason.value, reason)
                self.assertEqual("configuration_confirmed" in updated.pending_confirmations, confirms)
                if confirms:
                    self.assertEqual(updated.order_snapshot["felt_color"], updated.felt_color)
                else:
                    self.assertEqual(updated.order_snapshot, {})


if __name__ == "__main__":
    unittest.main()
