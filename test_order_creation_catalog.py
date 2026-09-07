"""Deterministic tests against the owner-supplied catalog fixture."""

from collections import Counter
import json
import unittest
from unittest.mock import patch

from order_creation_catalog import (
    CATALOG_PATH, list_product_options, lookup_base_product, lookup_product_option,
)


class CatalogTests(unittest.TestCase):
    def test_source_records_and_explicit_mappings(self):
        records = json.loads(CATALOG_PATH.read_text())["records"]
        self.assertEqual(len(records), 87)
        self.assertEqual(Counter(r["category"] for r in records), {
            "Table Design Model": 47, "Top Rail Profile": 7, "Bracket": 6,
            "Felt": 7, "Timber": 9, "Timber Paint": 11,
        })
        self.assertTrue(all(isinstance(r["price"], str) for r in records))
        for record in records:
            if record["category"] == "Table Design Model" and record["table_size"] != "6ft":
                self.assertEqual(lookup_base_product(record["product_model"], record["table_size"])["record"], record)
        homestead = lookup_base_product("Homestead", "7ft")["record"]
        self.assertEqual(homestead["title"], "Homestead7ft")
        self.assertEqual(homestead["sku"], "B7HOMESTEAD")
        self.assertEqual(lookup_base_product("Saga", "7ft")["record"]["sku"], "\tB7SAGA")
        self.assertEqual(lookup_base_product("Cyber", "8ft")["record"]["sku"], "\tB8CYBERIN")
        timber = list_product_options("Timber")
        self.assertEqual([r["title"] for r in timber if r["sku"] == "TTAOAK"], ["Tassie Oak", "American Oak"])

    def test_titles_prices_normalization_and_no_semantic_matching(self):
        titles = ["Olive", "Blue", "Burgundy", "Black", "Red", "Purple", "Grey"]
        for supplied in ("Blue", " blue ", "BLUE"):
            result = lookup_product_option("Felt", supplied)
            self.assertEqual(result["record"]["title"], "Blue")
            self.assertEqual(result["record"]["price"], "0")
            self.assertEqual(result["allowed_values"], titles)
        for supplied in ("Green", "blue felt"):
            result = lookup_product_option("Felt", supplied)
            self.assertIsNone(result["record"])
            self.assertEqual(result["allowed_values"], titles)
        self.assertEqual(lookup_product_option("Bracket", "Stainless Steel")["record"]["price"], "750")
        self.assertEqual(lookup_base_product(" odyssey ", " 8FT ")["record"]["title"], "Odyssey 8ft")

    def test_base_combination_and_demo_coverage(self):
        for size in ("7ft", "8ft", "9ft"):
            self.assertIsNotNone(lookup_base_product("Odyssey", size)["record"])
        for size in ("6ft", "10ft", "12ft", "large"):
            result = lookup_base_product("Odyssey", size)
            self.assertIsNone(result["record"])
            self.assertEqual(result["field"], "table_size")
            self.assertEqual(result["allowed_values"], ["7ft", "8ft", "9ft"])
        result = lookup_base_product("Sleek", "9ft")
        self.assertIsNone(result["record"])
        self.assertEqual(result["allowed_values"], ["7ft", "8ft"])
        self.assertEqual(lookup_base_product("Unknown", "8ft")["field"], "product_model")

    def test_independent_results(self):
        result = lookup_product_option("Felt", "Blue")
        result["record"]["title"] = "Changed"
        result["allowed_values"].clear()
        self.assertEqual(lookup_product_option("Felt", "Blue")["record"]["title"], "Blue")

    def test_catalog_errors_are_not_customer_no_match(self):
        with self.assertRaises(ValueError):
            list_product_options("Unknown")
        records = list_product_options("Felt")
        ambiguous = records + [{**records[1], "title": " blue "}]
        for payload in ({}, {"records": [{}]}, {"records": ambiguous},
                        {"records": [{**records[0], "price": "NaN"}]}):
            with self.subTest(payload=payload), patch("pathlib.Path.read_text", return_value=json.dumps(payload)):
                with self.assertRaises(ValueError):
                    lookup_product_option("Felt", "Blue")
        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                lookup_product_option("Felt", "Blue")


if __name__ == "__main__":
    unittest.main()
