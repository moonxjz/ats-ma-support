"""Tests for the customer-only structured update boundary."""

import unittest
from decimal import Decimal
from unittest.mock import patch

from pydantic import ValidationError

from entity.order_creation_state import OrderCreationState
from entity.extracted_order import ExtractedDeliveryAddress, ExtractedOrderInformation
from workflow.order.order_creation_updates import apply_extracted_order_information
from tests.test_order_creation_state import complete_customer_state


class OrderInformationUpdateTests(unittest.TestCase):
    def test_partial_corrections_and_multiple_fields(self):
        state = complete_customer_state()
        state.pending_field = "table_size"
        for update in (
            {"table_size": "7ft"}, {"room_size": "6m x 5m"},
            {"phone": "0400 000 000"},
            {"product_model": "Odyssey", "table_size": "7ft", "quantity": 2},
        ):
            with self.subTest(update=update):
                result = apply_extracted_order_information(state, ExtractedOrderInformation(**update))
                expected = state.model_dump()
                expected.update(update)
                self.assertEqual(result.model_dump(), expected)

    def test_optional_fields_preserve_replace_and_clear(self):
        for field in ("company_name", "customer_instructions"):
            with self.subTest(field=field):
                state = complete_customer_state()
                setattr(state, field, "Existing value")
                for update, expected in (({}, "Existing value"), ({field: "New value"}, "New value"), ({field: None}, None)):
                    result = apply_extracted_order_information(state, ExtractedOrderInformation(**update))
                    self.assertEqual(getattr(result, field), expected)
                self.assertEqual(getattr(state, field), "Existing value")

    def test_nested_address_preserve_replace_clear_and_empty(self):
        state = complete_customer_state()
        state.delivery_address.address = "Suite 1, 1 Example Street"
        for update in ({}, {"address": "Suite 2, 1 Example Street"},
                       {"postcode": "3001"}):
            with self.subTest(update=update):
                result = apply_extracted_order_information(
                    state, ExtractedOrderInformation(delivery_address=update)
                )
                expected = state.model_dump()
                expected["delivery_address"].update(update)
                self.assertEqual(result.model_dump(), expected)

    def test_postcode_only_preserves_all_other_address_components(self):
        state = complete_customer_state()
        before = state.model_dump()
        result = apply_extracted_order_information(state,
            ExtractedOrderInformation(delivery_address={"postcode": "3152"}))
        self.assertEqual(result.delivery_address.model_dump(),
                         {**before["delivery_address"], "postcode": "3152"})
        self.assertEqual(state.model_dump(), before)

    def test_empty_update_preserves_quantity_and_all_values(self):
        state = complete_customer_state()
        state.quantity = 3
        result = apply_extracted_order_information(state, ExtractedOrderInformation())
        self.assertEqual(result, state)
        self.assertIsNot(result, state)

    def test_required_nulls_and_all_blank_strings_rejected(self):
        for model, optional in (
            (ExtractedOrderInformation, {"company_name", "customer_instructions"}),
            (ExtractedDeliveryAddress, set()),
        ):
            for field in model.model_fields:
                if field not in optional:
                    with self.subTest(model=model.__name__, field=field, value=None):
                        with self.assertRaises(ValidationError):
                            model(**{field: None})
                for value in ("", " \t\n", 123, [], {}):
                    if field == "quantity" and value == 123:
                        continue
                    if field == "delivery_address" and value == {}:
                        continue
                    with self.subTest(model=model.__name__, field=field, value=value):
                        with self.assertRaises(ValidationError):
                            model(**{field: value})

    def test_quantity_requires_positive_integer(self):
        self.assertEqual(ExtractedOrderInformation(quantity=2).quantity, 2)
        for value in (0, -1, None, True, False, 1.5, 2.0, "2"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ExtractedOrderInformation(quantity=value)

    def test_protected_and_unknown_fields_rejected(self):
        protected = set(OrderCreationState.model_fields) - set(ExtractedOrderInformation.model_fields)
        protected.update({"result_status", "reason", "data", "required_input", "error", "unknown"})
        for field in protected:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ExtractedOrderInformation(**{field: "forbidden"})
        with self.assertRaises(ValidationError):
            ExtractedOrderInformation(delivery_address={"unknown": "forbidden"})

    def test_preserves_protected_state_and_does_not_execute_workflow(self):
        state = complete_customer_state()
        state.room_size_validation_result = "SUITABLE"
        state.unit_price = Decimal("4200.00")
        state.total_price = Decimal("4550.00")
        state.product_sku = "EXAMPLE-SKU"
        state.order_snapshot = {"configuration": {"table_size": "8ft"}}
        state.configuration_confirmed = True
        state.final_order_confirmed = True
        state.order_id = "EXAMPLE-ORDER"
        state.order_status = "PENDING"
        state.failure_reason = "Existing diagnostic"
        state.missing_customer_fields = ["phone"]
        state.pending_system_fields = ["shipping_cost"]
        state.conflicting_fields = {"table_size": ["7ft", "8ft"]}
        state.last_question = "Existing wording"
        before = state.model_dump()
        with patch("workflow.order.order_creation_controller.execute_order_creation_workflow") as execute:
            result = apply_extracted_order_information(state, ExtractedOrderInformation(table_size="7ft"))
        execute.assert_not_called()
        before["table_size"] = "7ft"
        self.assertEqual(result.model_dump(), before)

    def test_success_leaves_inputs_unchanged_and_nested_values_independent(self):
        state = complete_customer_state()
        state.order_snapshot = {"items": [{"name": "old"}]}
        state.conflicting_fields = {"table_size": ["7ft"]}
        extracted = ExtractedOrderInformation(delivery_address={"city": "Richmond"})
        state_before = state.model_dump_json()
        update_before = extracted.model_dump_json(exclude_unset=True)
        result = apply_extracted_order_information(state, extracted)
        result.delivery_address.city = "Changed"
        result.order_snapshot["items"][0]["name"] = "new"
        result.conflicting_fields["table_size"].append("8ft")
        result.pending_confirmations.clear()
        self.assertEqual(state.model_dump_json(), state_before)
        self.assertEqual(extracted.model_dump_json(exclude_unset=True), update_before)

    def test_revalidates_modified_extraction_without_mutating_inputs(self):
        extracted = ExtractedOrderInformation(company_name="Valid")
        extracted.company_name = " "
        state = complete_customer_state()
        before = state.model_dump_json()
        with self.assertRaises(ValidationError):
            apply_extracted_order_information(state, extracted)
        self.assertEqual(state.model_dump_json(), before)
        self.assertEqual(extracted.company_name, " ")

    def test_failed_state_validation_does_not_mutate_inputs(self):
        state = complete_customer_state()
        state.quantity = 0
        extracted = ExtractedOrderInformation(phone="0400 000 000")
        before = state.model_dump_json()
        update_before = extracted.model_dump_json(exclude_unset=True)
        with self.assertRaises(ValidationError):
            apply_extracted_order_information(state, extracted)
        self.assertEqual(state.model_dump_json(), before)
        self.assertEqual(extracted.model_dump_json(exclude_unset=True), update_before)

    def test_json_round_trip_preserves_omission_and_null(self):
        for values in ({}, {"company_name": None},
                       {"delivery_address": {"postcode": "3152"}},
                       {"company_name": None, "phone": "0400", "customer_instructions": None}):
            with self.subTest(values=values):
                extracted = ExtractedOrderInformation(**values)
                restored = ExtractedOrderInformation.model_validate_json(
                    extracted.model_dump_json(exclude_unset=True)
                )
                self.assertEqual(restored.model_dump(exclude_unset=True), values)
                self.assertEqual(restored.model_fields_set, extracted.model_fields_set)
                result = apply_extracted_order_information(complete_customer_state(), restored)
                self.assertEqual(OrderCreationState.model_validate_json(result.model_dump_json()), result)


if __name__ == "__main__":
    unittest.main()
