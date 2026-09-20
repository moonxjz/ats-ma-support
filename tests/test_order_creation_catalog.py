"""Deterministic tests against the owner-supplied catalog fixture."""

from collections import Counter
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from workflow.order.order_creation_catalog import (
    CATALOG_PATH, list_product_options, lookup_base_product, lookup_product_option, lookup_product_pricing,
)


class CatalogTests(unittest.TestCase):
    def test_source_skus_are_nonblank_without_surrounding_whitespace(self):
        records = json.loads(CATALOG_PATH.read_text())["records"]
        for index, record in enumerate(records):
            with self.subTest(index=index, title=record["title"]):
                sku = record["sku"]
                self.assertIsInstance(sku, str)
                self.assertTrue(sku.strip(), "SKU must be nonblank")
                self.assertEqual(sku, sku.strip(), "SKU contains surrounding whitespace")

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
        self.assertEqual(lookup_base_product("Saga", "7ft")["record"]["sku"], "B7SAGA")
        self.assertEqual(lookup_base_product("Cyber", "8ft")["record"]["sku"], "B8CYBERIN")
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


class ProductPricingTests(unittest.TestCase):
    def setUp(self):
        self.values = dict(product_model="Odyssey", table_size="8ft",
                           top_profile="Bull-nose Edge - with black steel side skirt",
                           bracket="Standard rubber", felt_color="Blue", timber="Tassie Oak",
                           timber_painting="Natural")

    def test_base_and_zero_adjustments(self):
        result = lookup_product_pricing(**self.values)
        self.assertEqual(result["product_sku"], "B8ODYSSEY")
        self.assertEqual(result["base_price"], Decimal("5250"))
        self.assertEqual(result["customisation_price"], Decimal("0"))
        self.assertEqual(result["unit_price"], Decimal("5250"))
        self.assertEqual(len(result["option_adjustments"]), 5)
        self.assertTrue(all(v == Decimal("0") for v in result["option_adjustments"].values()))

    def test_multiple_adjustments_and_exact_source_sku(self):
        self.values.update(top_profile="Waterfall", bracket="Stainless Steel")
        before = self.values.copy()
        result = lookup_product_pricing(**self.values)
        self.assertEqual(result["option_adjustments"]["top_profile"], Decimal("800"))
        self.assertEqual(result["option_adjustments"]["bracket"], Decimal("750"))
        self.assertEqual(result["customisation_price"], Decimal("1550"))
        self.assertEqual(result["unit_price"], Decimal("6800"))
        self.assertEqual(self.values, before)
        self.values.update(product_model="Saga", table_size="7ft")
        self.assertEqual(lookup_product_pricing(**self.values)["product_sku"], "B7SAGA")

    def test_noncanonical_and_unresolvable_inputs_are_not_corrected(self):
        for field in self.values:
            for value, error in ((" " + self.values[field] + " ", ValueError),
                                 ("Unknown", LookupError), (None, ValueError)):
                values = {**self.values, field: value}
                before = values.copy()
                with self.subTest(field=field, value=value), self.assertRaises(error):
                    lookup_product_pricing(**values)
                self.assertEqual(values, before)

    def test_fractional_prices_and_technical_errors(self):
        records = json.loads(CATALOG_PATH.read_text())["records"]
        for record in records:
            if record["title"] == "Odyssey 8ft":
                record["price"] = "5250.10"
            if record["category"] == "Felt" and record["title"] == "Blue":
                record["price"] = "0.20"
        with patch("pathlib.Path.read_text", return_value=json.dumps({"records": records})):
            self.assertEqual(lookup_product_pricing(**self.values)["unit_price"], Decimal("5250.30"))
        with patch("workflow.order.order_creation_catalog.lookup_base_product", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                lookup_product_pricing(**self.values)


if __name__ == "__main__":
    unittest.main()
