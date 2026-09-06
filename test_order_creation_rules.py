"""Tests against the workbook's explicit 57-inch cue thresholds."""

import unittest
from decimal import Decimal

from order_creation_rules import DEMO_CUE_LENGTH_INCHES, validate_room_size


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


if __name__ == "__main__":
    unittest.main()
