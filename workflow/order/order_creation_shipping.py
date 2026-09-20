"""Read-only shipping lookup using explicit MVP fixture records.

MVP ASSUMPTION: The local fixture intentionally has limited postcode coverage.
An absent postcode uses a zero per-table shipping rate for this demo only.
This is not ATS production free shipping or a real zero freight quote and
must be replaced by production shipping database/API behavior.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path


SHIPPING_PATH = Path(__file__).resolve().parents[2] / "data" / "shipping_rates.json"


def _load_shipping() -> tuple[dict, dict]:
    """Validate all records before lookup, including before postcode fallback."""
    payload = json.loads(SHIPPING_PATH.read_text())
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(key), list) for key in ("postcode_zones", "rates")
    ):
        raise ValueError("Malformed shipping fixture.")
    zones, rates = {}, {}
    for record in payload["postcode_zones"]:
        if not isinstance(record, dict) or set(record) != {"postcode", "shipping_zone"}:
            raise ValueError("Malformed postcode mapping.")
        postcode, zone = record["postcode"], record["shipping_zone"]
        if (not isinstance(postcode, str) or not re.fullmatch(r"[0-9]{4}", postcode)
                or not isinstance(zone, str) or not zone.strip()):
            raise ValueError("Invalid postcode mapping.")
        if postcode in zones:
            raise ValueError(f"Duplicate postcode mapping: {postcode}")
        zones[postcode] = zone
    for record in payload["rates"]:
        if not isinstance(record, dict) or set(record) != {
            "table_size_ft", "shipping_zone", "per_table_shipping_rate",
        }:
            raise ValueError("Malformed shipping rate record.")
        size, zone, raw = (record["table_size_ft"], record["shipping_zone"],
                           record["per_table_shipping_rate"])
        if (type(size) is not int or size <= 0 or not isinstance(zone, str)
                or not zone.strip() or not isinstance(raw, str) or not raw.strip()):
            raise ValueError("Invalid shipping rate record.")
        try:
            rate = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("Invalid shipping rate.") from exc
        if not rate.is_finite() or rate < 0:
            raise ValueError("Shipping rate must be finite and non-negative.")
        key = (size, zone)
        if key in rates:
            raise ValueError(f"Duplicate shipping rate: {key}")
        rates[key] = rate
    return zones, rates


def lookup_shipping_rate(table_size: str, postcode: str) -> dict:
    """Return match metadata and per-table Decimal rate without mutating Wt.

    Only an absent postcode receives the documented MVP zero fallback.
    A mapped postcode without a size/zone rate raises LookupError.
    """
    if not isinstance(postcode, str) or not postcode.strip():
        raise ValueError("Shipping lookup requires a supplied postcode.")
    if not isinstance(table_size, str) or not re.fullmatch(r"[1-9][0-9]*ft", table_size):
        raise ValueError("Shipping lookup requires a canonical table size.")
    zones, rates = _load_shipping()
    zone = zones.get(postcode)
    if zone is None:
        return {"postcode": postcode, "matched": False, "shipping_zone": None,
                "per_table_shipping_rate": Decimal("0")}
    key = (int(table_size[:-2]), zone)
    if key not in rates:
        raise LookupError(f"No shipping rate for table_size={table_size}, zone={zone}")
    return {"postcode": postcode, "matched": True, "shipping_zone": zone,
            "per_table_shipping_rate": rates[key]}
