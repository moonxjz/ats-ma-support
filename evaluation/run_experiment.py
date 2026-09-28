"""Full experiment runner over all scenarios.

For each scenario this runs the conversation `runs_per_scenario` times (one order
per conversation). Every run's persisted artifact (written by `main.save_experiment_log`
into `experiment_logs/`) is then evaluated with *every* implemented metric in
`evaluation/metrics_test/` (I1, I2, I4, I5). Results are aggregated and written
to `evaluation/results/`.

Three levels of result are recorded, as required:
  1. per execution, per metric result   -> run["metrics"][<Ix>]   (each metric's full dict)
  2. per execution, final result        -> run["run_final_status"] (PASS/FAIL/INCONCLUSIVE)
  3. all executions, final result       -> output["summary"] (per-scenario + overall counts)

Usage:
    python -m evaluation.run_experiment --runs-per-scenario 3
    python -m evaluation.run_experiment --runs-per-scenario 2 --scenario-id S01 S02
    python -m evaluation.run_experiment --runs-per-scenario 5 --no-llm
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.metrics_test.i1_required_information_completeness import evaluate_i1_file
from evaluation.metrics_test.i2_configuration_validity import evaluate_i2_file
from evaluation.metrics_test.i4_commit_fidelity import evaluate_i4_file
from evaluation.metrics_test.i5_at_most_once_commit import evaluate_i5_file

DEFAULT_SCENARIOS_DIR = Path("evaluation/scenarios")
DEFAULT_RESULTS_DIR = Path("evaluation/results")
DEFAULT_ORDER_STORE_PATH = Path("data/orders.json")

METRIC_NAMES = ("I1", "I2", "I4", "I5")

STATUS_RANK = {"PASS": 0, "INCONCLUSIVE": 1, "FAIL": 2}


def _aggregate_run_status(metric_results: dict[str, dict]) -> dict[str, Any]:
    """Worst-of metric statuses: any FAIL -> FAIL; else any INCONCLUSIVE -> INCONCLUSIVE; else PASS."""
    worst = "PASS"
    failing: list[str] = []
    for name, res in metric_results.items():
        status = res.get("status")
        if status is None:
            continue
        if STATUS_RANK.get(status, 3) > STATUS_RANK.get(worst, 0):
            worst = status
        if status == "FAIL":
            failing.append(name)
    if worst == "FAIL":
        detail = f"failing metrics: {', '.join(failing)}"
    elif worst == "INCONCLUSIVE":
        detail = "one or more metrics inconclusive (not all PASS)"
    else:
        detail = "all metrics PASS"
    return {"status": worst, "detail": detail}


def run_experiment(
    *,
    runs_per_scenario: int,
    scenarios_dir: Path = DEFAULT_SCENARIOS_DIR,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    order_store_path: Path = DEFAULT_ORDER_STORE_PATH,
    use_llm: bool = True,
    max_turns: int = 100,
    scenario_ids: list[str] | None = None,
    run_driver: Any = None,
) -> dict[str, Any]:
    """Run the experiment and write the aggregated results file. Returns the result dict."""
    scenarios_dir = Path(scenarios_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    if scenario_ids:
        scenario_files = [scenarios_dir / f"{sid}.json" for sid in scenario_ids]
    else:
        scenario_files = sorted(scenarios_dir.glob("*.json"))
    scenario_files = [p for p in scenario_files if p.exists()]

    if not scenario_files:
        raise SystemExit(f"No scenario files found in {scenarios_dir}")

    # I2 needs the LLM room-size extractor. If unavailable, I2 degrades to INCONCLUSIVE.
    room_extractor = None
    if use_llm:
        try:
            from agents.room_size_extractor import extract_final_room_information
            room_extractor = extract_final_room_information
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            print(f"Warning: LLM room extractor unavailable ({exc}); I2 will be INCONCLUSIVE.")

    # Imported lazily: main pulls in runtime deps not needed for arg parsing.
    # A driver can be injected (e.g. for testing) instead of the real runtime.
    if run_driver is None:
        from main import run_scenario_conversation as run_driver

    run_records: list[dict[str, Any]] = []
    for sf in scenario_files:
        sid = sf.stem
        for rep in range(1, runs_per_scenario + 1):
            print(f"[{datetime.now():%H:%M:%S}] Running scenario {sid} "
                  f"rep {rep}/{runs_per_scenario}")
            try:
                log_path = run_driver(
                    scenario_id=sid,
                    order_store_path=Path(order_store_path),
                    max_turns=max_turns,
                    debug=False,
                    manual=False,
                    verbose=False,
                )
            except Exception as exc:  # noqa: BLE001 - one bad run must not abort the batch
                print(f"  ! scenario {sid} rep {rep} crashed: {exc}")
                run_records.append({
                    "scenario_id": sid,
                    "run_index": None,
                    "conversation_id": None,
                    "final_status": "ERROR",
                    "metrics": {name: {"status": "INCONCLUSIVE",
                                       "reason": f"run_crashed: {exc}"} for name in METRIC_NAMES},
                    "run_final_status": "INCONCLUSIVE",
                    "run_final_detail": "run crashed before completion",
                })
                continue

            try:
                res_i1 = evaluate_i1_file(log_path).to_dict()
                res_i2 = evaluate_i2_file(log_path, room_extractor=room_extractor).to_dict()
                res_i4 = evaluate_i4_file(log_path, scenarios_dir=scenarios_dir).to_dict()
                res_i5 = evaluate_i5_file(log_path).to_dict()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! metrics failed for {log_path.name}: {exc}")
                res_i1 = res_i2 = res_i4 = res_i5 = {
                    "status": "INCONCLUSIVE", "reason": f"metric_error: {exc}"}

            metric_results = {"I1": res_i1, "I2": res_i2, "I4": res_i4, "I5": res_i5}
            run_final = _aggregate_run_status(metric_results)
            run_records.append({
                "scenario_id": sid,
                "run_index": res_i1.get("run_index"),
                "conversation_id": res_i1.get("conversation_id"),
                "final_status": res_i1.get("final_status"),
                "metrics": metric_results,
                "run_final_status": run_final["status"],
                "run_final_detail": run_final["detail"],
            })
            print(f"  -> {log_path.name}: run_final={run_final['status']} "
                  f"(I1={res_i1['status']} I2={res_i2['status']} "
                  f"I4={res_i4['status']} I5={res_i5['status']})")

    summary = _build_summary(run_records)
    output: dict[str, Any] = {
        "params": {
            "runs_per_scenario": runs_per_scenario,
            "scenarios": [sf.stem for sf in scenario_files],
            "order_store_path": str(order_store_path),
            "use_llm": use_llm,
            "max_turns": max_turns,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "runs": run_records,
        "summary": summary,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_path = results_dir / f"experiment_{stamp}.json"
    results_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {results_path}")
    _print_summary(summary)
    return output


def _empty_metric_counts() -> dict[str, dict[str, int]]:
    return {name: {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0} for name in METRIC_NAMES}


def _build_summary(run_records: list[dict[str, Any]]) -> dict[str, Any]:
    per_scenario: dict[str, Any] = {}
    overall_metrics = _empty_metric_counts()
    overall_runs = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}

    for rec in run_records:
        sid = rec["scenario_id"]
        ps = per_scenario.setdefault(sid, {
            "runs": 0,
            "run_status": {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0},
            "metrics": _empty_metric_counts(),
        })
        ps["runs"] += 1
        ps["run_status"][rec["run_final_status"]] = ps["run_status"].get(rec["run_final_status"], 0) + 1
        for name in METRIC_NAMES:
            st = rec["metrics"].get(name, {}).get("status")
            if st in ps["metrics"][name]:
                ps["metrics"][name][st] += 1

        overall_runs[rec["run_final_status"]] = overall_runs.get(rec["run_final_status"], 0) + 1
        for name in METRIC_NAMES:
            st = rec["metrics"].get(name, {}).get("status")
            if st in overall_metrics[name]:
                overall_metrics[name][st] += 1

    return {
        "per_scenario": per_scenario,
        "overall": {
            "runs": len(run_records),
            "run_status": overall_runs,
            "metrics": overall_metrics,
        },
    }


def _print_summary(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    print("\n=== Overall summary ===")
    print(f"Total runs: {overall['runs']}  run status: {overall['run_status']}")
    for name in METRIC_NAMES:
        print(f"  {name}: {overall['metrics'][name]}")
    print("\nPer scenario:")
    for sid, ps in summary["per_scenario"].items():
        print(f"  {sid}: runs={ps['runs']} run_status={ps['run_status']}")
        for name in METRIC_NAMES:
            print(f"      {name}: {ps['metrics'][name]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full experiment over all scenarios.")
    parser.add_argument("--runs-per-scenario", type=int, required=True,
                        help="How many times each scenario is executed.")
    parser.add_argument("--scenario-id", nargs="*", default=None,
                        help="Optional subset of scenario ids (default: all).")
    parser.add_argument("--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--order-store-path", type=Path, default=DEFAULT_ORDER_STORE_PATH)
    parser.add_argument("--max-turns", type=int, default=100,
                        help="Max conversation turns per run before force-stop.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable the LLM room-size extractor for I2 (I2 -> INCONCLUSIVE).")
    parser.add_argument("--use-llm", dest="use_llm", action="store_true",
                        help="Enable the LLM room-size extractor for I2 (default).")
    parser.set_defaults(use_llm=True)
    args = parser.parse_args()

    run_experiment(
        runs_per_scenario=args.runs_per_scenario,
        scenarios_dir=args.scenarios_dir,
        results_dir=args.results_dir,
        order_store_path=args.order_store_path,
        use_llm=args.use_llm and not args.no_llm,
        max_turns=args.max_turns,
        scenario_ids=args.scenario_id,
    )


if __name__ == "__main__":
    main()
