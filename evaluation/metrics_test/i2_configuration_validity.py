"""I2 — configuration validity (post-run metric, LLM-extracted room size).

Evaluated AFTER a run, from the JSON that `main.save_experiment_log` wrote into
`experiment_logs/`. No runtime, workflow, controller or order-store writes occur;
only the persisted run artifact is inspected.

Paper meaning
-------------
I2: "Invalid product combinations or unsuitable room conditions cannot be committed."

Judgement basis
---------------
The authoritative evidence is the final confirmed Order (`orders[].order`).
This metric checks two aspects on the committed order:
  * product combination — every product-authorization field in the order snapshot
                      is present and non-blank (the minimum bar for an authorized
                      combination). This is a plain presence check on the order,
                      not string matching.
  * room condition     — the FINAL room size mentioned in the dialogue. Because the
                      customer may state several sizes across turns and may phrase
                      the room size in free-form natural language, we do NOT parse
                      the order's stored string with regex. Instead a dedicated LLM
                      agent (`agents.room_size_extractor.extract_final_room_information`)
                      extracts the final, normalised room/table size from the
                      dialogue history; this metric then compares it numerically
                      against the minimum-room-size table. No local string/regex
                      matching of the room size is performed.

I2 therefore REQUIRES an injected `room_extractor` (the LLM agent). It is a pure
function of (run artifact, room_extractor); the LLM call lives entirely inside the
extractor. The extractor is used only here (I2), not at runtime or in other metrics.

Status values follow the paper metric convention: PASS / FAIL / INCONCLUSIVE.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

# Minimum room sizes per table size (metres). Mirrors
# workflow/order/order_creation_rules.py::MINIMUM_ROOM_SIZES. Declared locally so
# the suitability comparison stays a pure, deterministic numeric check (no regex).
MINIMUM_ROOM_SIZES: dict[str, tuple[Decimal, Decimal]] = {
    "7ft": (Decimal("4.90"), Decimal("3.80")),
    "8ft": (Decimal("5.20"), Decimal("4.00")),
    "9ft": (Decimal("5.50"), Decimal("4.30")),
    "10ft": (Decimal("6.10"), Decimal("4.60")),
    "12ft": (Decimal("6.70"), Decimal("4.90")),
}

# Product-authorization fields whose combination defines a valid configuration.
PRODUCT_AUTHORIZATION_FIELDS: tuple[str, ...] = (
    "product_model", "table_size", "top_profile", "bracket", "felt_color",
    "timber", "timber_painting",
)

DEFAULT_LOGS_DIR = Path("experiment_logs")


@dataclass
class I2Evaluation:
    """Structured I2 result for one run; serialisable for experiment aggregation."""

    scenario_id: str | None
    run_index: int | None
    conversation_id: str | None
    status: str  # PASS | FAIL | INCONCLUSIVE
    reason: str
    orders_count: int
    final_status: str | None = None
    room_size_validation: str | None = None
    invalid_aspects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _parse_normalized_room_size(room_size: Any) -> tuple[Decimal, Decimal] | None:
    """Parse a normalised 'WxHm x Hm' string into two positive Decimal metres.

    Lightweight string splitting only — no regex/fullmatch string matching. The
    LLM extractor is responsible for normalisation, so this just reads digits.
    Returns None when the value is absent or unparseable.
    """
    if _is_blank(room_size):
        return None
    s = str(room_size).lower().replace("\u00d7", "x")
    parts = [p.strip() for p in s.split("x")]
    if len(parts) != 2:
        return None
    dims: list[Decimal] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit() or ch == ".")
        if not digits:
            return None
        try:
            dims.append(Decimal(digits))
        except Exception:
            return None
    if len(dims) != 2 or any(d <= 0 for d in dims):
        return None
    return (dims[0], dims[1])


def _table_size_key(table_size: Any) -> str | None:
    """Map a (possibly messy) table-size mention to a MINIMUM_ROOM_SIZES key."""
    t = (table_size or "").strip().lower()
    if not t:
        return None
    for key in MINIMUM_ROOM_SIZES:
        if t == key.lower() or t.startswith(key.lower()):
            return key
    return None


def _is_room_suitable(room_size: Any, table_size: Any) -> bool | None:
    """Numerically compare the extracted room size against the minimum table size.

    Returns True/False, or None when either value cannot be resolved. No regex:
    the LLM extractor already normalised the strings; we only do numeric checks.
    """
    key = _table_size_key(table_size)
    if key is None:
        return None
    dims = _parse_normalized_room_size(room_size)
    if dims is None:
        return None
    width, height = dims
    min_width, min_height = MINIMUM_ROOM_SIZES[key]
    return width >= min_width and height >= min_height


def _check_product_combination(snapshot: dict) -> bool:
    """Return True when every product-authorization field is present and non-blank."""
    return not any(_is_blank(snapshot.get(field)) for field in PRODUCT_AUTHORIZATION_FIELDS)


def evaluate_i2_run(run: dict, room_extractor: Callable) -> I2Evaluation:
    """Evaluate I2 from an in-memory run artifact and an injected room extractor.

    `room_extractor` must accept the run's `history` (list of message dicts) and
    return an object exposing `.room_size` / `.table_size` (e.g.
    agents.room_size_extractor.extract_final_room_information). It is REQUIRED: the
    room condition is judged only from the dialogue via this LLM agent, never from
    a regex match on the order's stored string.
    """
    scenario_id = run.get("scenario_id")
    run_index = run.get("run_index")
    conversation_id = run.get("conversation_id")
    final_status = run.get("final_status")
    orders = run.get("orders") or []
    orders_count = len(orders)

    if orders_count == 0:
        return I2Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="INCONCLUSIVE", reason="no_order_committed", orders_count=orders_count,
            final_status=final_status, room_size_validation=None, invalid_aspects=[],
        )

    history = run.get("history") or []
    try:
        recovered = room_extractor(history)
    except Exception as exc:
        return I2Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="INCONCLUSIVE", reason=f"room_extraction_failed: {exc}", orders_count=orders_count,
            final_status=final_status, room_size_validation=None, invalid_aspects=[],
        )
    r_size = getattr(recovered, "room_size", None)
    t_size = getattr(recovered, "table_size", None)
    suit = _is_room_suitable(r_size, t_size)
    if suit is True:
        room_ok, room_detail = True, "SUITABLE(llm)"
    elif suit is False:
        room_ok, room_detail = False, "UNSUITABLE(llm)"
    else:
        room_ok, room_detail = False, "room_size_unresolved"

    for order_record in orders:
        snapshot = order_record.get("order") or {}
        combo_ok = _check_product_combination(snapshot)
        invalid: list[str] = []
        if not room_ok:
            invalid.append("room_size")
        if not combo_ok:
            invalid.append("product_combination")
        if not invalid:
            return I2Evaluation(
                scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
                status="PASS", reason="valid_configuration_committed", orders_count=orders_count,
                final_status=final_status, room_size_validation=room_detail, invalid_aspects=[],
            )
        return I2Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="invalid_configuration_committed", orders_count=orders_count,
            final_status=final_status, room_size_validation=room_detail, invalid_aspects=invalid,
        )

    # Unreachable when orders_count > 0; defensive fallback.
    return I2Evaluation(
        scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
        status="INCONCLUSIVE", reason="no_order_committed", orders_count=orders_count,
        final_status=final_status, room_size_validation=None, invalid_aspects=[],
    )


def evaluate_i2_file(path: str | Path, room_extractor: Callable) -> I2Evaluation:
    """Evaluate I2 from a single experiment_logs run JSON file."""
    path = Path(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return I2Evaluation(
            scenario_id=None, run_index=None, conversation_id=None,
            status="INCONCLUSIVE", reason=f"cannot_read_run_log: {exc}", orders_count=0,
            final_status=None, room_size_validation=None, invalid_aspects=[],
        )
    return evaluate_i2_run(run, room_extractor=room_extractor)


def evaluate_i2_directory(
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    scenario_id: str | None = None,
    room_extractor: Callable | None = None,
) -> list[I2Evaluation]:
    """Evaluate I2 for every run log, optionally filtered by scenario id.

    Returns results sorted by file name, ready for aggregation. Requires an
    injected `room_extractor` (the LLM agent) — I2 no longer performs any local
    string matching of room size.
    """
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    if room_extractor is None:
        raise ValueError("evaluate_i2_directory requires a room_extractor (LLM agent).")
    pattern = f"{scenario_id}_run*.json" if scenario_id else "*.json"
    return [evaluate_i2_file(p, room_extractor=room_extractor) for p in sorted(logs_dir.glob(pattern))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate I2 (configuration validity) for one experiment run.",
    )
    parser.add_argument(
        "run_log", type=Path, nargs="?",
        help="Path to an experiment_logs/<scenario>_run<NNN>.json file.",
    )
    parser.add_argument(
        "--logs-dir", type=Path, default=DEFAULT_LOGS_DIR,
        help=f"Directory of run logs (default: {DEFAULT_LOGS_DIR}).",
    )
    parser.add_argument(
        "--scenario-id", default=None,
        help="If set, evaluate every run log for this scenario id.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print only the status string per evaluated run.",
    )
    args = parser.parse_args()

    from agents.room_size_extractor import extract_final_room_information
    room_extractor = extract_final_room_information

    if args.scenario_id:
        results = evaluate_i2_directory(args.logs_dir, args.scenario_id, room_extractor=room_extractor)
    elif args.run_log:
        results = [evaluate_i2_file(args.run_log, room_extractor=room_extractor)]
    else:
        results = evaluate_i2_directory(args.logs_dir, room_extractor=room_extractor)

    if args.quiet:
        for result in results:
            print(result.status)
    else:
        for result in results:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
