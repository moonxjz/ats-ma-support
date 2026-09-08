"""Pure room validation using the workbook's 57-inch cue column.

Using only this cue length is an MVP assumption, NOT an established ATS
business default. Suitable sizes describe room fit, not product availability.
"""

import re

from order_creation_state import OrderCreationState, FinalOrderSnapshot, determine_missing_fields
from decimal import Decimal


DEMO_CUE_LENGTH_INCHES = 57
# WF- ORDER_CREATE_WF: embedded minimum-room-size table near row 222 (metres).
MINIMUM_ROOM_SIZES = {
    "7ft": (Decimal("4.90"), Decimal("3.80")),
    "8ft": (Decimal("5.20"), Decimal("4.00")),
    "9ft": (Decimal("5.50"), Decimal("4.30")),
    "10ft": (Decimal("6.10"), Decimal("4.60")),
    "12ft": (Decimal("6.70"), Decimal("4.90")),
}


def validate_room_size(room_size: str | None, table_size: str | None) -> dict:
    """Return suitability or one input correction, without state or I/O effects.

    Check table size first. Room input requires two positive metre measurements
    separated by x or ×. Dimension order is immaterial; equality is sufficient.
    """
    table_match = re.fullmatch(r"\s*(7|8|9|10|12)\s*ft\s*", table_size or "")
    if table_match is None:
        return {
            "result": None,
            "required_input": ["table_size"],
            "input_details": {
                "field": "table_size",
                "supplied_value": table_size,
                "supported_values": list(MINIMUM_ROOM_SIZES),
            },
        }

    room_match = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*m\s*[x×]\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*m\s*",
        room_size or "",
    )
    dimensions = (
        sorted((Decimal(value) for value in room_match.groups()), reverse=True)
        if room_match else []
    )
    if not dimensions or any(value <= 0 for value in dimensions):
        return {
            "result": None,
            "required_input": ["room_size"],
            "input_details": {
                "field": "room_size",
                "supplied_value": room_size,
                "supported_format": "positive metres x positive metres",
            },
        }

    suitable_sizes = [
        size for size, minimum in MINIMUM_ROOM_SIZES.items()
        if dimensions[0] >= minimum[0] and dimensions[1] >= minimum[1]
    ]
    selected_size = table_match.group(1) + "ft"
    return {
        "result": "SUITABLE" if selected_size in suitable_sizes else "UNSUITABLE",
        "suitable_table_sizes": suitable_sizes,
    }


def build_configuration_snapshot(state: OrderCreationState) -> dict:
    """Return the workbook's unpriced product configuration without changing Wt."""
    return {field: getattr(state, field) for field in (
        "product_model", "table_size", "timber", "timber_painting",
        "felt_color", "bracket", "top_profile", "quantity",
    )}


PRODUCT_AUTHORIZATION_FIELDS = (
    "product_model", "table_size", "top_profile", "bracket", "felt_color",
    "timber", "timber_painting",
)


def confirmed_product_configuration_matches(state: OrderCreationState) -> bool:
    """Compare product selections without changing historical snapshot quantity.

    Require the existing eight-field snapshot structure. Quantity is historical
    evidence, not part of current product authorization; pricing validates the
    current quantity separately. No lookup, calculation, or mutation occurs here.
    """
    snapshot = state.order_snapshot
    if not isinstance(snapshot, dict) or set(snapshot) != set(PRODUCT_AUTHORIZATION_FIELDS) | {"quantity"}:
        return False
    return all(
        isinstance(snapshot[field], str) and bool(snapshot[field].strip())
        and snapshot[field] == getattr(state, field)
        for field in PRODUCT_AUTHORIZATION_FIELDS
    )


def build_final_order_snapshot(state: OrderCreationState) -> FinalOrderSnapshot:
    """Copy already-validated/priced Wt; no lookup, calculation or mutation.

    PRICING's stored shipping_cost is authoritative, including MVP free shipping.
    Historical configuration quantity does not constrain current order quantity.
    """
    if determine_missing_fields(state):
        raise ValueError("Final snapshot requires complete customer information.")
    if state.configuration_confirmed is not True or not confirmed_product_configuration_matches(state):
        raise ValueError("Final snapshot requires authorized product configuration.")
    values = {field: getattr(state, field) for field in FinalOrderSnapshot.model_fields}
    values["delivery_address"] = state.delivery_address.model_dump()
    return FinalOrderSnapshot.model_validate(values)


def final_order_snapshot_matches(state: OrderCreationState, snapshot: FinalOrderSnapshot) -> bool:
    """Compare all final-order evidence, excluding workflow control/tracking."""
    validated = FinalOrderSnapshot.model_validate(snapshot)
    return validated == build_final_order_snapshot(state)


def calculate_total_price(
    unit_price: Decimal, per_table_shipping_rate: Decimal, quantity: int,
) -> dict:
    """Calculate order totals without side effects or binary float conversion.

    MVP ASSUMPTION: freight is charged per table, not per order. This is not
    established ATS production freight policy.
    """
    if type(quantity) is not int or quantity < 1:
        raise ValueError("Quantity must be a positive integer, not bool.")
    for value in (unit_price, per_table_shipping_rate):
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise ValueError("Money must be a finite, non-negative Decimal.")
    shipping_cost = per_table_shipping_rate * quantity
    return {"shipping_cost": shipping_cost,
            "total_price": unit_price * quantity + shipping_cost}
