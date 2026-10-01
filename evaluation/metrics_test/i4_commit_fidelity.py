"""I4 — commit fidelity (post-run metric).

Evaluated AFTER a run, from the JSON that `main.save_experiment_log` wrote into
`experiment_logs/`, cross-checked against the originating scenario's ground truth.
It performs no runtime, workflow, controller or order-store writes and makes no
model calls: it only inspects the persisted run artifact and the scenario file.

Paper meaning
-------------
I4: "The committed business state must exactly match the confirmed snapshot."

Evidence
--------
Two artifacts are compared:
  * The FINAL confirmed Order — `orders[].order` (the `FinalOrderSnapshot`
    committed to `data/orders.json`, copied into the run log).
  * The scenario ground truth — `evaluation/scenarios/<id>.json` ::
    `customer.ground_truth` (the customer's intended, confirmed configuration),
    plus `evaluation.system_derived.pricing` (the expected priced breakdown).

I4 therefore re-checks that what the system persisted equals what the scenario
declared as the confirmed, intended state. This mirrors the architecture-neutral
style of I1/I2 (no imports of runtime/workflow modules).

Comparison rules
----------------
* Customer / delivery / configuration fields: exact match (trimmed text).
* `room_size`: numeric tolerance comparison (the dialogue may phrase the room
  size differently from the ground truth string, but the metres must match).
* `quantity`: integer comparison.
* Pricing (`product_sku`, `unit_price`, `customisation_price`, `shipping_cost`,
  `total_price`): compared as Decimal when the scenario declares expected
  pricing; otherwise that part of the check is skipped (still PASS if the
  business fields match).

Status values follow the paper metric convention: PASS / FAIL.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from evaluation.metrics_test.scenario_outcome import load_scenario, scenario_expects_no_order

DEFAULT_LOGS_DIR = Path("experiment_logs")
DEFAULT_SCENARIOS_DIR = Path("evaluation/scenarios")

# (order_field_path, ground_truth_field_path) pairs under the committed snapshot.
# ground_truth is the `customer.ground_truth` dict, which already contains the
# `customer`, `delivery_address`, `configuration` and `room_size` sub-trees.
BUSINESS_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("customer_name", "customer.customer_name"),
    ("company_name", "customer.company_name"),
    ("phone", "customer.phone"),
    ("email", "customer.email"),
    ("customer_instructions", "customer.customer_instructions"),
    ("delivery_address.address", "delivery_address.address"),
    ("delivery_address.city", "delivery_address.city"),
    ("delivery_address.state", "delivery_address.state"),
    ("delivery_address.postcode", "delivery_address.postcode"),
    ("delivery_address.country", "delivery_address.country"),
    ("room_size", "room_size"),
    ("product_model", "configuration.product_model"),
    ("table_size", "configuration.table_size"),
    ("timber", "configuration.timber"),
    ("timber_painting", "configuration.timber_painting"),
    ("felt_color", "configuration.felt_color"),
    ("bracket", "configuration.bracket"),
    ("top_profile", "configuration.top_profile"),
    ("quantity", "configuration.quantity"),
)

# Pricing fields expected from the scenario's `evaluation.system_derived.pricing`.
PRICE_FIELDS: tuple[str, ...] = (
    "product_sku",
    "unit_price",
    "customisation_price",
    "shipping_cost",
    "total_price",
)


@dataclass
class I4Evaluation:
    """Structured I4 result for one run; serialisable for experiment aggregation."""

    scenario_id: str | None
    run_index: int | None
    conversation_id: str | None
    status: str  # PASS | FAIL
    reason: str
    orders_count: int
    final_status: str | None = None
    mismatched_fields: list[str] = field(default_factory=list)
    price_mismatched_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path (e.g. 'delivery_address.city') against a dict."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _get_gt(ground_truth: dict, path: str) -> Any:
    """Resolve a dotted path against the ground_truth dict."""
    return _get_path(ground_truth, path)


def _coerce_int(value: Any) -> Any:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _text_match(actual: Any, expected: Any) -> bool:
    if actual is None and expected is None:
        return True
    norm = lambda v: "" if v is None else str(v).strip()
    return norm(actual) == norm(expected)


def _parse_dims(value: Any) -> tuple[Decimal, Decimal] | None:
    if value is None:
        return None
    s = str(value).lower().replace("\u00d7", "x")
    parts = [p.strip() for p in s.split("x")]
    if len(parts) != 2:
        return None
    out: list[Decimal] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit() or ch == ".")
        if not digits:
            return None
        try:
            out.append(Decimal(digits))
        except Exception:
            return None
    if len(out) != 2 or any(d <= 0 for d in out):
        return None
    return (out[0], out[1])


def _room_size_match(actual: Any, expected: Any) -> bool:
    """Match room sizes by numeric tolerance; fall back to normalised text."""
    if actual is None and expected is None:
        return True
    dims_a, dims_b = _parse_dims(actual), _parse_dims(expected)
    if dims_a and dims_b:
        return (abs(dims_a[0] - dims_b[0]) < Decimal("0.01")
                and abs(dims_a[1] - dims_b[1]) < Decimal("0.01"))
    norm = lambda v: "" if v is None else " ".join(str(v).lower().split())
    return norm(actual) == norm(expected)


def _price_match(actual: Any, expected: Any) -> bool:
    if actual is None and expected is None:
        return True
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except Exception:
        return False


def _field_matches(order_val: Any, gt_val: Any, field: str) -> bool:
    if field == "quantity":
        return _coerce_int(order_val) == _coerce_int(gt_val)
    if field == "room_size":
        return _room_size_match(order_val, gt_val)
    return _text_match(order_val, gt_val)


def _expected_pricing(scenario: dict) -> dict | None:
    """Return the FINAL-phase expected pricing block from the scenario, if any."""
    derived = (scenario.get("evaluation") or {}).get("system_derived") or {}
    pricing_list = derived.get("pricing")
    if not pricing_list:
        return None
    final = next((e for e in pricing_list if e.get("phase") == "FINAL"), None)
    return final or pricing_list[-1]


def compare_pricing(order: dict, scenario: dict) -> list[str]:
    """Return pricing field names whose committed value differs from expected."""
    expected = _expected_pricing(scenario)
    if not expected:
        return []
    mismatched: list[str] = []
    if expected.get("product_sku") is not None and not _text_match(
        order.get("product_sku"), expected.get("product_sku")
    ):
        mismatched.append("product_sku")
    prices = expected.get("pricing") or {}
    for field_name in ("unit_price", "customisation_price", "shipping_cost", "total_price"):
        if field_name in prices and not _price_match(order.get(field_name), prices[field_name]):
            mismatched.append(field_name)
    return mismatched


def evaluate_i4_run(run: dict, scenario: dict | None) -> I4Evaluation:
    """Evaluate I4 from an in-memory run artifact and its scenario ground truth."""
    scenario_id = run.get("scenario_id")
    run_index = run.get("run_index")
    conversation_id = run.get("conversation_id")
    final_status = run.get("final_status")
    orders = run.get("orders") or []
    orders_count = len(orders)

    # Resolve scenario for the cancellation/no-order policy (the caller may pass
    # it in, or we fall back to loading it ourselves).
    scenario = scenario or load_scenario(scenario_id)
    if scenario_expects_no_order(scenario):
        if orders_count == 0:
            return I4Evaluation(
                scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
                status="PASS", reason="no_order_as_expected", orders_count=orders_count,
                final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
            )
        return I4Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="order_created_when_none_expected", orders_count=orders_count,
            final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
        )

    # Order-expecting scenario. The INCONCLUSIVE status has been removed; the
    # cases below that previously were INCONCLUSIVE now resolve to FAIL.
    if orders_count == 0:
        return I4Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="no_order_created_when_expected", orders_count=orders_count,
            final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
        )
    if scenario is None:
        return I4Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="scenario_not_found", orders_count=orders_count,
            final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
        )
    ground_truth = (scenario.get("customer") or {}).get("ground_truth")
    if not ground_truth:
        return I4Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="ground_truth_missing", orders_count=orders_count,
            final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
        )

    first_mismatch: tuple[list[str], list[str]] | None = None
    for order_record in orders:
        snapshot = order_record.get("order") or {}
        mismatched = [
            gt_path
            for order_path, gt_path in BUSINESS_FIELD_MAP
            if not _field_matches(_get_path(snapshot, order_path), _get_gt(ground_truth, gt_path), gt_path)
        ]
        price_mismatched = compare_pricing(snapshot, scenario)
        if not mismatched and not price_mismatched:
            return I4Evaluation(
                scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
                status="PASS", reason="committed_state_matches_ground_truth", orders_count=orders_count,
                final_status=final_status, mismatched_fields=[], price_mismatched_fields=[],
            )
        if first_mismatch is None:
            first_mismatch = (mismatched, price_mismatched)

    mismatched, price_mismatched = first_mismatch or ([], [])
    return I4Evaluation(
        scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
        status="FAIL", reason="committed_state_mismatch", orders_count=orders_count,
        final_status=final_status,
        mismatched_fields=mismatched, price_mismatched_fields=price_mismatched,
    )


def evaluate_i4_file(path: str | Path, scenarios_dir: str | Path = DEFAULT_SCENARIOS_DIR) -> I4Evaluation:
    """Evaluate I4 from a single experiment_logs run JSON file."""
    path = Path(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return I4Evaluation(
            scenario_id=None, run_index=None, conversation_id=None,
            status="FAIL", reason=f"cannot_read_run_log: {exc}", orders_count=0,
            final_status=None, mismatched_fields=[], price_mismatched_fields=[],
        )
    scenario = load_scenario(run.get("scenario_id"), scenarios_dir)
    return evaluate_i4_run(run, scenario)


def evaluate_i4_directory(
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    scenario_id: str | None = None,
    scenarios_dir: str | Path = DEFAULT_SCENARIOS_DIR,
) -> list[I4Evaluation]:
    """Evaluate I4 for every run log, optionally filtered by scenario id."""
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    pattern = f"{scenario_id}_run*.json" if scenario_id else "*.json"
    return [evaluate_i4_file(p, scenarios_dir=scenarios_dir) for p in sorted(logs_dir.glob(pattern))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate I4 (commit fidelity) for one experiment run.",
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
        "--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS_DIR,
        help=f"Directory of scenario JSON files (default: {DEFAULT_SCENARIOS_DIR}).",
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

    if args.scenario_id:
        results = evaluate_i4_directory(
            args.logs_dir, args.scenario_id, scenarios_dir=args.scenarios_dir)
    elif args.run_log:
        results = [evaluate_i4_file(args.run_log, scenarios_dir=args.scenarios_dir)]
    else:
        results = evaluate_i4_directory(
            args.logs_dir, scenarios_dir=args.scenarios_dir)

    if args.quiet:
        for result in results:
            print(result.status)
    else:
        for result in results:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
