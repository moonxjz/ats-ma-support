"""I1 — required-information completeness (post-run metric).

This metric is evaluated AFTER a run, from the JSON that `main.save_experiment_log`
wrote into `experiment_logs/`. It deliberately performs no runtime, workflow,
controller, order-store write or model calls: it only inspects the persisted
run artifact. That keeps the metric architecture-neutral and safe to run in bulk
when aggregating experiments.

Judgement basis
---------------
I1 asks whether the order-creation required information was completely collected.
The authoritative evidence is the final confirmed Order (the `FinalOrderSnapshot`
stored under `orders[].order` in the run log). The runtime only commits that
snapshot after `determine_missing_fields` is empty, so any committed order already
satisfies completeness; this metric re-checks it from the persisted artifact so
the conclusion is independently auditable.

Required fields are declared here as an explicit evaluation contract (mirroring
`entity/order_creation_state.REQUIRED_CUSTOMER_FIELDS`) rather than imported from
the runtime, so this metric stays free of architecture modules.

Status values follow the paper metric convention: PASS / FAIL / INCONCLUSIVE.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Required customer fields for order creation. Mirrors
# entity/order_creation_state.py::REQUIRED_CUSTOMER_FIELDS. Declared locally so
# the metric is self-contained and architecture-neutral.
REQUIRED_CUSTOMER_FIELDS: tuple[str, ...] = (
    "customer_name",
    "email",
    "phone",
    "delivery_address.address",
    "delivery_address.city",
    "delivery_address.state",
    "delivery_address.postcode",
    "delivery_address.country",
    "room_size",
    "product_model",
    "table_size",
    "timber",
    "timber_painting",
    "felt_color",
    "bracket",
    "top_profile",
)

DEFAULT_LOGS_DIR = Path("experiment_logs")


@dataclass
class I1Evaluation:
    """Structured I1 result for one run; serialisable for experiment aggregation."""

    scenario_id: str | None
    run_index: int | None
    conversation_id: str | None
    status: str  # PASS | FAIL | INCONCLUSIVE
    reason: str
    orders_count: int
    final_status: str | None = None
    missing_fields: list[str] = field(default_factory=list)

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


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def missing_required_fields(order: dict) -> list[str]:
    """Return required fields whose value is absent/blank in the order snapshot."""
    return [field_path for field_path in REQUIRED_CUSTOMER_FIELDS
            if _is_blank(_get_path(order, field_path))]


def evaluate_i1_run(run: dict) -> I1Evaluation:
    """Evaluate I1 from an in-memory run artifact (decoded experiment_logs JSON)."""
    scenario_id = run.get("scenario_id")
    run_index = run.get("run_index")
    conversation_id = run.get("conversation_id")
    final_status = run.get("final_status")
    orders = run.get("orders") or []
    orders_count = len(orders)

    if orders_count == 0:
        # No committed order means required information was not completed into an
        # order; I1 cannot be satisfied.
        return I1Evaluation(
            scenario_id=scenario_id,
            run_index=run_index,
            conversation_id=conversation_id,
            status="FAIL",
            reason="no_order_created",
            orders_count=orders_count,
            final_status=final_status,
            missing_fields=[],
        )

    last_missing: list[str] = []
    for order_record in orders:
        snapshot = order_record.get("order") or {}
        missing = missing_required_fields(snapshot)
        if not missing:
            return I1Evaluation(
                scenario_id=scenario_id,
                run_index=run_index,
                conversation_id=conversation_id,
                status="PASS",
                reason="all_required_fields_present_in_order",
                orders_count=orders_count,
                final_status=final_status,
                missing_fields=[],
            )
        last_missing = missing

    return I1Evaluation(
        scenario_id=scenario_id,
        run_index=run_index,
        conversation_id=conversation_id,
        status="FAIL",
        reason="required_fields_missing_in_order",
        orders_count=orders_count,
        final_status=final_status,
        missing_fields=last_missing,
    )


def evaluate_i1_file(path: str | Path) -> I1Evaluation:
    """Evaluate I1 from a single experiment_logs run JSON file."""
    path = Path(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return I1Evaluation(
            scenario_id=None,
            run_index=None,
            conversation_id=None,
            status="INCONCLUSIVE",
            reason=f"cannot_read_run_log: {exc}",
            orders_count=0,
            final_status=None,
            missing_fields=[],
        )
    return evaluate_i1_run(run)


def evaluate_i1_directory(
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    scenario_id: str | None = None,
) -> list[I1Evaluation]:
    """Evaluate I1 for every run log, optionally filtered by scenario id.

    Returns results sorted by file name, ready for aggregation.
    """
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    pattern = f"{scenario_id}_run*.json" if scenario_id else "*.json"
    return [evaluate_i1_file(p) for p in sorted(logs_dir.glob(pattern))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate I1 (required-information completeness) for one experiment run.",
    )
    parser.add_argument(
        "run_log",
        type=Path,
        nargs="?",
        help="Path to an experiment_logs/<scenario>_run<NNN>.json file.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help=f"Directory of run logs (default: {DEFAULT_LOGS_DIR}).",
    )
    parser.add_argument(
        "--scenario-id",
        default=None,
        help="If set, evaluate every run log for this scenario id.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the status string per evaluated run.",
    )
    args = parser.parse_args()

    if args.scenario_id:
        results = evaluate_i1_directory(args.logs_dir, args.scenario_id)
    elif args.run_log:
        results = [evaluate_i1_file(args.run_log)]
    else:
        results = evaluate_i1_directory(args.logs_dir)

    if args.quiet:
        for result in results:
            print(result.status)
    else:
        for result in results:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
