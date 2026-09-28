"""I5 — at-most-once commit (post-run metric).

Evaluated AFTER a run, from the JSON that `main.save_experiment_log` wrote into
`experiment_logs/`. It performs no runtime, workflow, controller or order-store
writes and no model calls: it only inspects the persisted run artifact. This keeps
the metric architecture-neutral, like I1/I2/I4.

Paper meaning
-------------
I5: "At-most-once commit — A workflow instance must not commit the same
transaction more than once."

Evidence
--------
The committed orders for the conversation: `orders` in the run log. The run log is
already scoped to a single conversation (`save_experiment_log` filters the order
store by `conversation_id`), so every entry in `orders` is a commit produced by
that one conversation. The at-most-once property is therefore a pure count check:

  * 1 committed order  -> PASS  (exactly one commit; not duplicated)
  * >1 committed orders -> FAIL  (the same transaction was committed more than once)
  * 0 committed orders  -> INCONCLUSIVE (no commit occurred; the at-most-once
                           property is neither exercised nor violated)

Status values follow the paper metric convention: PASS / FAIL / INCONCLUSIVE.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LOGS_DIR = Path("experiment_logs")


@dataclass
class I5Evaluation:
    """Structured I5 result for one run; serialisable for experiment aggregation."""

    scenario_id: str | None
    run_index: int | None
    conversation_id: str | None
    status: str  # PASS | FAIL | INCONCLUSIVE
    reason: str
    orders_count: int
    final_status: str | None = None
    order_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_i5_run(run: dict) -> I5Evaluation:
    """Evaluate I5 from an in-memory run artifact (decoded experiment_logs JSON)."""
    scenario_id = run.get("scenario_id")
    run_index = run.get("run_index")
    conversation_id = run.get("conversation_id")
    final_status = run.get("final_status")
    orders = run.get("orders") or []
    orders_count = len(orders)
    order_ids = [o.get("order_id") for o in orders if isinstance(o, dict)]

    if orders_count == 1:
        return I5Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="PASS", reason="single_order_committed", orders_count=orders_count,
            final_status=final_status, order_ids=order_ids,
        )
    if orders_count > 1:
        return I5Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="FAIL", reason="multiple_orders_committed", orders_count=orders_count,
            final_status=final_status, order_ids=order_ids,
        )
    return I5Evaluation(
        scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
        status="INCONCLUSIVE", reason="no_order_committed", orders_count=orders_count,
        final_status=final_status, order_ids=order_ids,
    )


def evaluate_i5_file(path: str | Path) -> I5Evaluation:
    """Evaluate I5 from a single experiment_logs run JSON file."""
    path = Path(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return I5Evaluation(
            scenario_id=None, run_index=None, conversation_id=None,
            status="INCONCLUSIVE", reason=f"cannot_read_run_log: {exc}", orders_count=0,
            final_status=None, order_ids=[],
        )
    return evaluate_i5_run(run)


def evaluate_i5_directory(
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    scenario_id: str | None = None,
) -> list[I5Evaluation]:
    """Evaluate I5 for every run log, optionally filtered by scenario id."""
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    pattern = f"{scenario_id}_run*.json" if scenario_id else "*.json"
    return [evaluate_i5_file(p) for p in sorted(logs_dir.glob(pattern))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate I5 (at-most-once commit) for one experiment run.",
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

    if args.scenario_id:
        results = evaluate_i5_directory(args.logs_dir, args.scenario_id)
    elif args.run_log:
        results = [evaluate_i5_file(args.run_log)]
    else:
        results = evaluate_i5_directory(args.logs_dir)

    if args.quiet:
        for result in results:
            print(result.status)
    else:
        for result in results:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
