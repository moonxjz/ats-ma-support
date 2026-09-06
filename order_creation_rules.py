"""Pure room validation using the workbook's 57-inch cue column.

Using only this cue length is an MVP assumption, NOT an established ATS
business default. Suitable sizes describe room fit, not product availability.
"""

import re
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
