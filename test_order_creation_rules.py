"""Tests against the workbook's explicit 57-inch cue thresholds."""

import unittest
from decimal import Decimal

from order_creation_rules import DEMO_CUE_LENGTH_INCHES, build_configuration_snapshot, validate_room_size, calculate_total_price


class RoomSizeRulesTests(unittest.TestCase):
    def test_workbook_thresholds_and_each_dimension_boundary(self):
        self.assertEqual(DEMO_CUE_LENGTH_INCHES, 57)
        rows = [
            ("7ft", "4.90", "3.80"), ("8ft", "5.20", "4.00"),
            ("9ft", "5.50", "4.30"), ("10ft", "6.10", "4.60"),
            ("12ft", "6.70", "4.90"),
        ]
        for size, length, width in rows:
            with self.subTest(size=size):
                self.assertEqual(validate_room_size(f"{length}m x {width}m", size)["result"], "SUITABLE")
                for smaller in (
                    f"{Decimal(length) - Decimal('0.01')}m x {width}m",
                    f"{length}m x {Decimal(width) - Decimal('0.01')}m",
                ):
                    self.assertEqual(validate_room_size(smaller, size)["result"], "UNSUITABLE")

    def test_formatting_reversed_dimensions_and_suitable_sizes(self):
        expected = {"result": "SUITABLE", "suitable_table_sizes": ["7ft", "8ft"]}
        self.assertEqual(validate_room_size(" 4.0 m × 5.2 m ", " 8 ft "), expected)
        self.assertEqual(validate_room_size("5.2m x 4.0m", "8ft"), expected)
        self.assertEqual(validate_room_size("1m x 1m", "7ft"), {
            "result": "UNSUITABLE", "suitable_table_sizes": [],
        })

    def test_unusable_inputs_request_one_field_table_first(self):
        for table in (None, "", "6ft", "11ft", "eight feet"):
            with self.subTest(table=table):
                result = validate_room_size("bad room", table)
                self.assertIsNone(result["result"])
                self.assertEqual(result["required_input"], ["table_size"])
                self.assertEqual(result["input_details"]["supplied_value"], table)
        for room in (None, "", "large", "5 x 4", "0m x 4m", "-5m x 4m", "NaNm x 4m", "5m x 0m"):
            with self.subTest(room=room):
                result = validate_room_size(room, "8ft")
                self.assertIsNone(result["result"])
                self.assertEqual(result["required_input"], ["room_size"])
                self.assertEqual(result["input_details"]["supplied_value"], room)

    def test_repeatable_results_have_independent_lists(self):
        first = validate_room_size("5.2m x 4m", "8ft")
        second = validate_room_size("5.2m x 4m", "8ft")
        self.assertEqual(first, second)
        first["suitable_table_sizes"].clear()
        self.assertEqual(validate_room_size("5.2m x 4m", "8ft"), second)


class ConfigurationSnapshotTests(unittest.TestCase):
    def test_exact_fields_purity_and_independence(self):
        from test_order_creation_state import complete_customer_state
        state = complete_customer_state()
        state.quantity = 2
        before = state.model_dump()
        first = build_configuration_snapshot(state)
        expected = {
            "product_model": "Premier Standard", "table_size": "8ft",
            "timber": "Tasmanian Oak", "timber_painting": "White",
            "felt_color": "Charcoal Grey", "bracket": "Test bracket",
            "top_profile": "Test profile", "quantity": 2,
        }
        self.assertEqual(first, expected)
        second = build_configuration_snapshot(state)
        first.clear()
        self.assertEqual(second, expected)
        self.assertEqual(state.model_dump(), before)


class ProductAuthorizationTests(unittest.TestCase):
    def test_seven_fields_and_historical_quantity_without_mutation(self):
        from order_creation_rules import confirmed_product_configuration_matches
        from test_order_creation_state import complete_customer_state
        state = complete_customer_state()
        state.quantity = 2
        state.order_snapshot = build_configuration_snapshot(state)
        state.quantity = 3
        before = state.model_dump()
        self.assertTrue(confirmed_product_configuration_matches(state))
        self.assertEqual(state.model_dump(), before)
        for field in ("product_model", "table_size", "top_profile", "bracket", "felt_color", "timber", "timber_painting"):
            changed = state.model_copy(deep=True)
            setattr(changed, field, "different")
            with self.subTest(field=field):
                self.assertFalse(confirmed_product_configuration_matches(changed))

    def test_snapshot_structure_is_required(self):
        from order_creation_rules import confirmed_product_configuration_matches
        from test_order_creation_state import complete_customer_state
        state = complete_customer_state()
        valid = build_configuration_snapshot(state)
        invalid = [{}, {**valid, "extra": 1}, {**valid, "felt_color": None}]
        invalid += [{k: v for k, v in valid.items() if k != field} for field in valid]
        for snapshot in invalid:
            state.order_snapshot = snapshot
            before = state.model_dump()
            with self.subTest(snapshot=snapshot):
                self.assertFalse(confirmed_product_configuration_matches(state))
                self.assertEqual(state.model_dump(), before)


class TotalPriceTests(unittest.TestCase):
    def test_exact_decimal_totals_and_per_table_mvp_shipping(self):
        for quantity, shipping, total in ((1, "530", "5780"), (2, "1060", "11560")):
            self.assertEqual(calculate_total_price(Decimal("5250"), Decimal("530"), quantity),
                             {"shipping_cost": Decimal(shipping), "total_price": Decimal(total)})
        self.assertEqual(calculate_total_price(Decimal("0.10"), Decimal("0.20"), 3),
                         {"shipping_cost": Decimal("0.60"), "total_price": Decimal("0.90")})
        self.assertEqual(calculate_total_price(Decimal("6800"), Decimal("0"), 2)["total_price"], Decimal("13600"))

    def test_invalid_inputs(self):
        for quantity in (True, False, 0, -1, 1.0, "2", None):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                calculate_total_price(Decimal("1"), Decimal("0"), quantity)
        for value in (None, "1", 1, 1.0, Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("sNaN")):
            for first in (True, False):
                with self.subTest(value=value, first=first), self.assertRaises(ValueError):
                    calculate_total_price(value if first else Decimal("1"), Decimal("0") if first else value, 1)

    def test_repeatable_independent_results(self):
        first = calculate_total_price(Decimal("5250"), Decimal("530"), 2)
        second = calculate_total_price(Decimal("5250"), Decimal("530"), 2)
        self.assertEqual(first, second)
        first.clear()
        self.assertEqual(second["total_price"], Decimal("11560"))


if __name__ == "__main__":
    unittest.main()
