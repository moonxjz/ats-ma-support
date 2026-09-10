"""In-memory checks for the approved ATS Stage 1 contracts."""

import unittest
from decimal import Decimal

from pydantic import ValidationError

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_creation_state import (
    DeliveryAddress,
    OrderCreationStage,
    OrderCreationState,
    OrderWorkflowStatus,
    REQUIRED_CUSTOMER_FIELDS,
    determine_missing_fields,
)


def complete_customer_state() -> OrderCreationState:
    return OrderCreationState(
        conversation_id="ATS-TEST-001",
        customer_name="Demo Customer",
        email="customer@example.com",
        phone="0400000000",
        delivery_address=DeliveryAddress(
            address="1 Example Street",
            city="Melbourne",
            state="VIC",
            postcode="3000",
            country="Australia",
        ),
        room_size="5.2m x 4.0m",
        product_model="Premier Standard",
        table_size="8ft",
        timber="Tasmanian Oak",
        timber_painting="White",
        felt_color="Charcoal Grey",
        bracket="Test bracket",
        top_profile="Test profile",
    )


class OrderCreationStateTests(unittest.TestCase):
    def test_empty_state_and_defaults(self):
        state = OrderCreationState(conversation_id="ATS-TEST-001")
        self.assertEqual(determine_missing_fields(state), list(REQUIRED_CUSTOMER_FIELDS))
        self.assertEqual(len(REQUIRED_CUSTOMER_FIELDS), 16)
        self.assertEqual(state.workflow_type, "ORDER_CREATE_WF")
        self.assertEqual(state.owner_agent, "ORDER_AGENT")
        self.assertEqual(state.quantity, 1)
        self.assertFalse(state.configuration_confirmed)
        self.assertFalse(state.final_order_confirmed)
        self.assertEqual(state.status, OrderWorkflowStatus.ACTIVE)
        self.assertEqual(state.current_stage, OrderCreationStage.COLLECT_REQUIREMENTS)

    def test_zero_and_negative_quantities_are_rejected(self):
        state = OrderCreationState(conversation_id="ATS-TEST-001", quantity=2)
        self.assertEqual(state.quantity, 2)
        for quantity in (0, -1):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                OrderCreationState(conversation_id="ATS-TEST-001", quantity=quantity)

    def test_partial_fields_and_blank_nested_address(self):
        state = complete_customer_state()
        state.phone = None
        state.room_size = ""
        state.delivery_address.postcode = " \t\n"
        self.assertEqual(
            determine_missing_fields(state),
            ["phone", "delivery_address.postcode", "room_size"],
        )

    def test_complete_customer_fields_do_not_require_system_or_optional_fields(self):
        state = complete_customer_state()
        self.assertEqual(determine_missing_fields(state), [])
        self.assertIsNone(state.company_name)
        self.assertIsNone(state.customer_instructions)
        self.assertIsNone(state.product_sku)
        self.assertIsNone(state.total_price)
        self.assertIsNone(state.order_id)
        self.assertNotIn("result_status", state.model_dump())
        self.assertFalse(state.final_order_confirmed)
        self.assertEqual(state.status, OrderWorkflowStatus.ACTIVE)

    def test_completeness_check_is_side_effect_free_and_repeatable(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                state = complete_customer_state()
                if not complete:
                    state.phone = None
                state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
                state.pending_field = "phone"
                state.last_question = "What is your phone number?"
                state.missing_customer_fields = ["phone"]
                before = state.model_dump_json()
                first = determine_missing_fields(state)
                self.assertEqual(first, determine_missing_fields(state))
                first.append("unrelated")
                self.assertEqual(state.model_dump_json(), before)
                self.assertNotIn("unrelated", determine_missing_fields(state))

    def test_serialization_round_trip(self):
        state = complete_customer_state()
        state.unit_price = Decimal("4200.50")
        state.order_snapshot = {"product_model": state.product_model}
        restored = OrderCreationState.model_validate_json(state.model_dump_json())
        self.assertEqual(restored, state)
        self.assertIsInstance(restored.delivery_address, DeliveryAddress)

    def test_undefined_stage_and_workflow_status_are_rejected(self):
        for field in ("current_stage", "status"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                OrderCreationState(conversation_id="ATS-TEST-001", **{field: "UNKNOWN"})


class BusinessResultTests(unittest.TestCase):
    def result_values(self):
        return dict(
            workflow_id="WF-TEST-001",
            source_agent="ORDER_AGENT",
            action="CREATE_ORDER",
            result_status="NEEDS_USER_INPUT",
            current_stage="COLLECT_REQUIREMENTS",
            reason="MISSING_REQUIRED_INFORMATION",
        )

    def test_design_result_values_and_simple_payloads(self):
        pairs = [
            ("NEEDS_USER_INPUT", "MISSING_REQUIRED_INFORMATION"),
            ("NEEDS_USER_INPUT", "ROOM_SIZE_UNSUITABLE"),
            ("NEEDS_USER_INPUT", "CONFIGURATION_CONFIRMATION_REQUIRED"),
            ("NEEDS_USER_INPUT", "FINAL_CONFIRMATION_REQUIRED"),
            ("SUCCESS", "ORDER_CREATED"),
            ("FAILURE", "ORDER_CREATION_FAILED"),
            ("CANCELLED", "CUSTOMER_CANCELLED"),
        ]
        for status, reason in pairs:
            with self.subTest(reason=reason):
                values = self.result_values()
                values.update(result_status=status, reason=reason)
                result = BusinessResult(**values)
                self.assertEqual(result.result_status, BusinessResultStatus(status))
                self.assertEqual(result.reason, BusinessResultReason(reason))
                self.assertEqual(result.data, {})
                self.assertEqual(result.required_input, [])
                self.assertIsNone(result.error)
        result = BusinessResult(
            **self.result_values(),
            data={"missing_fields": ["phone"]},
            required_input=["phone"],
            error={"message": "Example error payload"},
        )
        self.assertIsInstance(result.data, dict)
        self.assertIsInstance(result.error, dict)
        self.assertEqual(
            BusinessResult.model_validate_json(result.model_dump_json()), result
        )

    def test_undefined_result_enums_are_rejected(self):
        for field, value in (("result_status", "COMPLETED"), ("reason", "PRICING_FAILED")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                values = self.result_values()
                values[field] = value
                BusinessResult(**values)

    def test_payload_types_and_independent_defaults(self):
        for field, value in (("data", []), ("required_input", [123]), ("error", "error")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                BusinessResult(**self.result_values(), **{field: value})
        first = BusinessResult(**self.result_values())
        second = BusinessResult(**self.result_values())
        first.data["phone"] = "example"
        first.required_input.append("phone")
        self.assertEqual(second.data, {})
        self.assertEqual(second.required_input, [])



class FinalSnapshotModelTests(unittest.TestCase):
    def snapshot(self):
        from order_creation_rules import build_final_order_snapshot
        from test_order_creation_controller import PricingTests
        from order_creation_controller import handle_pricing
        state = PricingTests().pricing_state()
        handle_pricing(state)
        return build_final_order_snapshot(state)

    def test_exact_schema_frozen_nested_and_roundtrip(self):
        from order_creation_state import FinalOrderSnapshot
        snapshot = self.snapshot()
        self.assertEqual(set(FinalOrderSnapshot.model_fields), {
            "customer_name", "company_name", "phone", "email", "delivery_address", "customer_instructions",
            "product_model", "table_size", "top_profile", "bracket", "felt_color", "timber", "timber_painting",
            "room_size", "room_size_validation_result", "quantity", "product_sku", "customisation_price",
            "unit_price", "shipping_cost", "total_price"})
        self.assertEqual(FinalOrderSnapshot.model_validate_json(snapshot.model_dump_json()), snapshot)
        self.assertIsInstance(snapshot.shipping_cost, Decimal)
        with self.assertRaises(ValidationError):
            snapshot.quantity = 3
        with self.assertRaises(ValidationError):
            snapshot.delivery_address.city = "Other"
        state = OrderCreationState(conversation_id="roundtrip", final_order_snapshot=snapshot)
        self.assertEqual(OrderCreationState.model_validate_json(state.model_dump_json()), state)

    def test_invalid_values_and_extra_fields(self):
        from order_creation_state import FinalOrderSnapshot
        values = self.snapshot().model_dump()
        for field, value in [("quantity", v) for v in (0, -1, True, 1.0, "2", None)] + [
            ("phone", " "), ("company_name", ""), ("room_size_validation_result", "UNSUITABLE"),
            ("shipping_method", None), ("shipping_quote_status", "anything")
        ] + [(f, v) for f in ("unit_price", "customisation_price", "shipping_cost", "total_price")
             for v in (None, Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 1.0)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                FinalOrderSnapshot.model_validate({**values, field: value})
        for field in values["delivery_address"]:
            with self.subTest(address=field), self.assertRaises(ValidationError):
                FinalOrderSnapshot.model_validate({**values, "delivery_address": {**values["delivery_address"], field: " "}})


if __name__ == "__main__":
    unittest.main()
