"""Shipping fixture fidelity and explicit lookup semantics; no live services."""

from copy import deepcopy
from collections import Counter
from decimal import Decimal
import hashlib
import json
import unittest
from unittest.mock import patch

from workflow.order.order_creation_shipping import SHIPPING_PATH, lookup_shipping_rate


class ShippingTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(SHIPPING_PATH.read_text())

    def test_fixture_matches_source_normalized_records(self):
        # Pin the source records plus the approved postcode 2000 Sydney Metro addition.
        encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         "591956368912ffd7f737d379a123b1acc26a2773918e10712723a5e5841df3b4")
        self.assertEqual(len(self.payload["postcode_zones"]), 181)
        self.assertEqual(len(self.payload["rates"]), 36)
        self.assertEqual(Counter(r["shipping_zone"] for r in self.payload["postcode_zones"]), {
            "Sydney Metro": 21,
            "Melbourne Metro": 20,
            "Brisbane Metro": 20,
            "Gold Coast": 20,
            "Sunshine Coast": 20,
            "Newcastle": 20,
            "Perth Metro": 20,
            "Adelaide Metro": 20,
            "Canberra": 20,
        })
        self.assertEqual({r["table_size_ft"] for r in self.payload["rates"]}, {6, 7, 8, 9})

    def test_postcode_2000_8ft_uses_sydney_metro_rate(self):
        self.assertEqual(lookup_shipping_rate("8ft", "2000"), {
            "postcode": "2000", "matched": True, "shipping_zone": "Sydney Metro",
            "per_table_shipping_rate": Decimal("1518"),
        })

    def test_all_explicit_mappings_and_rates(self):
        for mapping in self.payload["postcode_zones"]:
            for rate in self.payload["rates"]:
                if rate["shipping_zone"] == mapping["shipping_zone"]:
                    result = lookup_shipping_rate(f'{rate["table_size_ft"]}ft', mapping["postcode"])
                    self.assertEqual(result, {
                        **mapping, "matched": True,
                        "per_table_shipping_rate": Decimal(rate["per_table_shipping_rate"]),
                    })
        self.assertEqual(lookup_shipping_rate("8ft", "3000"), {
            "postcode": "3000", "matched": True, "shipping_zone": "Melbourne Metro",
            "per_table_shipping_rate": Decimal("530"),
        })

    def test_unknown_postcode_uses_zero_shipping_rate_under_mvp_assumption(self):
        for postcode in ("3152", "3020", "1020"):
            self.assertEqual(lookup_shipping_rate("8ft", postcode), {
                "postcode": postcode, "matched": False, "shipping_zone": None,
                "per_table_shipping_rate": Decimal("0"),
            })

    def test_matched_zero_is_distinct_from_mvp_fallback(self):
        for rate in self.payload["rates"]:
            if rate["table_size_ft"] == 8 and rate["shipping_zone"] == "Melbourne Metro":
                rate["per_table_shipping_rate"] = "0"
        with patch("pathlib.Path.read_text", return_value=json.dumps(self.payload)):
            actual = lookup_shipping_rate("8ft", "3000")
        self.assertTrue(actual["matched"])
        self.assertEqual(actual["shipping_zone"], "Melbourne Metro")
        self.assertEqual(actual["per_table_shipping_rate"], Decimal("0"))

    def test_mapped_postcode_missing_rate_is_not_fallback(self):
        self.payload["rates"] = [r for r in self.payload["rates"]
                                 if not (r["table_size_ft"] == 8 and r["shipping_zone"] == "Melbourne Metro")]
        with patch("pathlib.Path.read_text", return_value=json.dumps(self.payload)):
            with self.assertRaises(LookupError):
                lookup_shipping_rate("8ft", "3000")
        with self.assertRaises(LookupError):
            lookup_shipping_rate("10ft", "3000")

    def test_bad_fixture_never_becomes_fallback(self):
        bad = [{}, {"postcode_zones": {}, "rates": []}]
        for key in ("postcode_zones", "rates"):
            for conflict in (False, True):
                payload = deepcopy(self.payload)
                duplicate = payload[key][0].copy()
                if conflict:
                    duplicate["shipping_zone" if key == "postcode_zones" else "per_table_shipping_rate"] = "other" if key == "postcode_zones" else "99"
                payload[key].append(duplicate)
                bad.append(payload)
        for key, field, values in (
            ("rates", "per_table_shipping_rate", ("-1", "NaN", "Infinity", "bad", "", 530, None)),
            ("rates", "table_size_ft", (True, 0, -1, "8")),
            ("postcode_zones", "postcode", (None, 3000, "")),
            ("postcode_zones", "shipping_zone", (None, "")),
        ):
            for value in values:
                payload = deepcopy(self.payload)
                payload[key][0][field] = value
                bad.append(payload)
        for payload in bad:
            with self.subTest(payload=payload), patch("pathlib.Path.read_text", return_value=json.dumps(payload)):
                with self.assertRaises(ValueError):
                    lookup_shipping_rate("8ft", "3152")

    def test_invalid_arguments_and_file_errors(self):
        for size, postcode in (("8ft", None), ("8ft", "  "), ("8ft", 3000),
                               (None, "3000"), ("eight", "3000"), ("8 FT", "3000")):
            with self.subTest(size=size, postcode=postcode), self.assertRaises(ValueError):
                lookup_shipping_rate(size, postcode)
        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                lookup_shipping_rate("8ft", "3152")
        with patch("pathlib.Path.read_text", return_value="not JSON"):
            with self.assertRaises(json.JSONDecodeError):
                lookup_shipping_rate("8ft", "3152")

    def test_results_are_independent(self):
        first = lookup_shipping_rate("8ft", "3000")
        first.clear()
        self.assertEqual(lookup_shipping_rate("8ft", "3000")["per_table_shipping_rate"], Decimal("530"))


if __name__ == "__main__":
    unittest.main()
