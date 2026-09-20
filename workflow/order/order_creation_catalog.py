"""Read-only MVP catalog tools. Source Titles and SKUs are never rewritten.

Only 7/8/9ft have complete demo catalog + room-rule coverage. This is an
MVP restriction, not an ATS production rule; the fixture retains 6ft records.
Source prices remain decimal strings; pricing results use Decimal arithmetic.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "product_prices.json"
DEMO_TABLE_SIZES = ("7ft", "8ft", "9ft")
CUSTOMIZATION_CATEGORIES = {
    "top_profile": "Top Rail Profile",
    "bracket": "Bracket",
    "felt_color": "Felt",
    "timber": "Timber",
    "timber_painting": "Timber Paint",
}


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _load_records() -> list[dict]:
    """Read fresh independent records; malformed data is a technical error."""
    payload = json.loads(CATALOG_PATH.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Malformed product catalog records.")
    records = payload["records"]
    keys = set()
    base_keys = set()
    for record in records:
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), str) or not record[key].strip()
            for key in ("category", "title", "price", "sku")
        ):
            raise ValueError("Malformed product catalog record.")
        try:
            price = Decimal(record["price"])
        except InvalidOperation as exc:
            raise ValueError("Malformed catalog price.") from exc
        if not price.is_finite():
            raise ValueError("Non-finite catalog price.")
        key = (record["category"], _normalize(record["title"]))
        if key in keys:
            raise ValueError("Ambiguous normalized catalog Title.")
        keys.add(key)
        if record["category"] == "Table Design Model":
            if any(not isinstance(record.get(k), str) or not record[k].strip()
                   for k in ("product_model", "table_size")):
                raise ValueError("Missing explicit base product mapping.")
            key = (_normalize(record["product_model"]), _normalize(record["table_size"]))
            if key in base_keys:
                raise ValueError("Ambiguous base product mapping.")
            base_keys.add(key)
    return records


def list_product_options(category: str) -> list[dict]:
    """Return independent source records in source order, including zero prices."""
    records = [r for r in _load_records() if r["category"] == category]
    if not records:
        raise ValueError(f"Missing catalog category: {category}")
    return records


def lookup_product_option(category: str, supplied_value: str) -> dict:
    """Normalized exact match only; no-match is data, not an exception."""
    records = list_product_options(category)
    match = next((r for r in records
                  if _normalize(r["title"]) == _normalize(supplied_value)), None)
    return {"record": match, "allowed_values": [r["title"] for r in records]}


def lookup_base_product(product_model: str, table_size: str) -> dict:
    """Use explicit mappings, never concatenate/parse a runtime lookup Title.

    Unknown models request model correction. A known model with an unavailable
    size requests size correction. Lists include every eligible choice.
    """
    records = [r for r in list_product_options("Table Design Model")
               if r["table_size"] in DEMO_TABLE_SIZES]
    if not records:
        raise ValueError("Catalog has no demo base products.")
    models = list(dict.fromkeys(r["product_model"] for r in records))
    if len({_normalize(model) for model in models}) != len(models):
        raise ValueError("Ambiguous normalized product model.")
    selected = [r for r in records
                if _normalize(r["product_model"]) == _normalize(product_model)]
    if not selected:
        return {"record": None, "field": "product_model", "allowed_values": models}
    sizes = [r["table_size"] for r in selected]
    match = next((r for r in selected
                  if _normalize(r["table_size"]) == _normalize(table_size)), None)
    return {"record": match, "field": "table_size", "allowed_values": sizes}


def lookup_product_pricing(
    *, product_model: str, table_size: str, top_profile: str, bracket: str,
    felt_color: str, timber: str, timber_painting: str,
) -> dict:
    """Price canonical confirmed selections using the existing catalog tools.

    Option prices are per-table adjustments. This never canonicalizes inputs,
    mutates workflow state, or manufactures a configured SKU.
    """
    selections = dict(top_profile=top_profile, bracket=bracket, felt_color=felt_color,
                      timber=timber, timber_painting=timber_painting)
    if any(not isinstance(value, str) or not value.strip()
           for value in (product_model, table_size, *selections.values())):
        raise ValueError("Pricing requires supplied canonical catalog selections.")
    base = lookup_base_product(product_model, table_size)["record"]
    if base is None:
        raise LookupError("Confirmed base product is not resolvable.")
    if base["product_model"] != product_model or base["table_size"] != table_size:
        raise ValueError("Confirmed base product is not canonical.")
    adjustments = {}
    for field, category in CUSTOMIZATION_CATEGORIES.items():
        record = lookup_product_option(category, selections[field])["record"]
        if record is None:
            raise LookupError(f"Confirmed catalog selection is not resolvable: {field}")
        if record["title"] != selections[field]:
            raise ValueError(f"Confirmed catalog selection is not canonical: {field}")
        adjustments[field] = Decimal(record["price"])
    base_price = Decimal(base["price"])
    if any(value < 0 for value in (base_price, *adjustments.values())):
        raise ValueError("Product prices must be non-negative.")
    customisation_price = sum(adjustments.values(), Decimal("0"))
    return {"product_sku": base["sku"], "base_price": base_price,
            "option_adjustments": adjustments, "customisation_price": customisation_price,
            "unit_price": base_price + customisation_price}
