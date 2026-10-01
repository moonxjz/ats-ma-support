"""Full experiment runner over all scenarios.

For each scenario this runs the conversation `runs_per_scenario` times (one order
per conversation). Every run's persisted artifact (written by `main.save_experiment_log`
into `experiment_logs/`) is then evaluated with *every* implemented metric in
`evaluation/metrics_test/` (I1, I2, I3, I4, I5). Results are aggregated and written
to `evaluation/results/`.

Three levels of result are recorded, as required:
  1. per execution, per metric result   -> run["metrics"][<Ix>]   (each metric's full dict)
  2. per execution, final result        -> run["run_final_status"] (PASS/FAIL)
  3. all executions, final result       -> output["summary"] (per-scenario + overall counts)

Usage:
    python -m evaluation.run_experiment --runs-per-scenario 3
    python -m evaluation.run_experiment --runs-per-scenario 2 --scenario-id S01 S02
    python -m evaluation.run_experiment --runs-per-scenario 5 --no-llm
    python -m evaluation.run_experiment --runs-per-scenario 3 --agent pure
    python -m evaluation.run_experiment --runs-per-scenario 3 --agent legacy pure
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.metrics_test.i1_required_information_completeness import evaluate_i1_file
from evaluation.metrics_test.i2_configuration_validity import evaluate_i2_file
from evaluation.metrics_test.i3_confirmation import evaluate_i3_file
from evaluation.metrics_test.i4_commit_fidelity import evaluate_i4_file
from evaluation.metrics_test.i5_at_most_once_commit import evaluate_i5_file
from tools.llm_client import reset_usage, get_usage

DEFAULT_SCENARIOS_DIR = Path("evaluation/scenarios")
DEFAULT_RESULTS_DIR = Path("evaluation/results")
DEFAULT_ORDER_STORE_PATH = Path("data/orders.json")

METRIC_NAMES = ("I1", "I2", "I3", "I4", "I5")

# INCONCLUSIVE status removed: metrics now only emit PASS or FAIL.

# Supported agents. "legacy" is the classifier/router/order-agent/support-agent
# pipeline; "pure" is the single-LLM PureAgent.
AGENT_MODES = ("legacy", "pure")


def _get_driver(agent_mode: str):
    """Return the conversation runner for the requested agent mode."""
    if agent_mode == "pure":
        from main import run_scenario_conversation_pure
        return run_scenario_conversation_pure
    from main import run_scenario_conversation
    return run_scenario_conversation


def _aggregate_run_status(metric_results: dict[str, dict]) -> dict[str, Any]:
    """Worst-of metric statuses: any FAIL -> FAIL; else PASS (INCONCLUSIVE removed)."""
    failing: list[str] = []
    for name, res in metric_results.items():
        if res.get("status") == "FAIL":
            failing.append(name)
    if failing:
        return {"status": "FAIL", "detail": f"failing metrics: {', '.join(failing)}"}
    return {"status": "PASS", "detail": "all metrics PASS"}


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
    agent_modes: list[str] | None = None,
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

    # I2 needs the LLM room-size extractor. If unavailable, I2 degrades to FAIL.
    room_extractor = None
    if use_llm:
        try:
            from agents.room_size_extractor import extract_final_room_information
            room_extractor = extract_final_room_information
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            print(f"Warning: LLM room extractor unavailable ({exc}); I2 will be FAIL.")

    # Default to testing BOTH agents so the results are directly comparable.
    effective_agent_modes = list(agent_modes) if agent_modes else list(AGENT_MODES)

    # Driver selection. By default each requested agent mode runs through its own
    # runner. An injected ``run_driver`` (used by tests) overrides everything and
    # is run once under the "legacy" label.
    if run_driver is not None:
        drivers = {"legacy": run_driver}
        effective_agent_modes = ["legacy"]
    else:
        drivers = {mode: _get_driver(mode) for mode in effective_agent_modes}

    def _execute_one(driver, sid, rep, agent_mode):
        print(f"[{datetime.now():%H:%M:%S}] Running scenario {sid} "
              f"(agent={agent_mode}) rep {rep}/{runs_per_scenario}")
        reset_usage()
        try:
            log_path = driver(
                scenario_id=sid,
                order_store_path=Path(order_store_path),
                max_turns=max_turns,
                debug=False,
                manual=False,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001 - one bad run must not abort the batch
            print(f"  ! scenario {sid} ({agent_mode}) rep {rep} crashed: {exc}")
            run_records.append({
                "scenario_id": sid,
                "agent_mode": agent_mode,
                "run_index": None,
                "conversation_id": None,
                "final_status": "ERROR",
                "metrics": {name: {"status": "FAIL",
                                   "reason": f"run_crashed: {exc}"} for name in METRIC_NAMES},
                "run_final_status": "FAIL",
                "run_final_detail": "run crashed before completion",
                "token_usage": get_usage(),
            })
            return

        # Evaluate each metric independently. A failure (or an INCONCLUSIVE
        # result) in one metric must NOT stop the evaluation of the subsequent
        # ones, so every call is wrapped in its own try/except instead of a
        # single shared block.
        metric_evaluators = {
            "I1": lambda: evaluate_i1_file(log_path).to_dict(),
            "I2": lambda: evaluate_i2_file(log_path, room_extractor=room_extractor).to_dict(),
            "I3": lambda: evaluate_i3_file(log_path, use_llm=use_llm).to_dict(),
            "I4": lambda: evaluate_i4_file(log_path, scenarios_dir=scenarios_dir).to_dict(),
            "I5": lambda: evaluate_i5_file(log_path).to_dict(),
        }
        metric_results: dict[str, dict] = {}
        for name, evaluate in metric_evaluators.items():
            try:
                metric_results[name] = evaluate()
            except Exception as exc:  # noqa: BLE001 - one bad metric must not skip the rest
                print(f"  ! metric {name} failed for {log_path.name}: {exc}")
                metric_results[name] = {
                    "status": "FAIL", "reason": f"metric_error: {exc}"}
        res_i1 = metric_results["I1"]
        res_i2 = metric_results["I2"]
        res_i3 = metric_results["I3"]
        res_i4 = metric_results["I4"]
        res_i5 = metric_results["I5"]
        run_final = _aggregate_run_status(metric_results)
        run_records.append({
            "scenario_id": sid,
            "agent_mode": agent_mode,
            "run_index": res_i1.get("run_index"),
            "conversation_id": res_i1.get("conversation_id"),
            "final_status": res_i1.get("final_status"),
            "metrics": metric_results,
            "run_final_status": run_final["status"],
            "run_final_detail": run_final["detail"],
            "total_turns": _count_turns(log_path),
            "token_usage": get_usage(),
        })
        print(f"  -> {log_path.name}: run_final={run_final['status']} "
              f"(I1={res_i1['status']} I2={res_i2['status']} "
              f"I3={res_i3['status']} I4={res_i4['status']} I5={res_i5['status']})")

    run_records: list[dict[str, Any]] = []
    for sf in scenario_files:
        sid = sf.stem
        for agent_mode in effective_agent_modes:
            driver = drivers[agent_mode]
            for rep in range(1, runs_per_scenario + 1):
                _execute_one(driver, sid, rep, agent_mode)

    summary = _build_summary(run_records)
    output: dict[str, Any] = {
        "params": {
            "runs_per_scenario": runs_per_scenario,
            "scenarios": [sf.stem for sf in scenario_files],
            "agent_modes": effective_agent_modes,
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
    return {name: {"PASS": 0, "FAIL": 0} for name in METRIC_NAMES}


def _empty_token_counts() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


def _add_tokens(acc: dict[str, int], usage: dict[str, int] | None) -> None:
    if not usage:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
        acc[key] += usage.get(key, 0) or 0


def _count_turns(log_path: str | Path) -> int:
    """Count customer (user) turns in a run log's history (offline; no model calls).

    One turn = one customer message. The committed ``history`` alternates
    user/assistant, so the user-message count equals the number of exchanges.
    Blank/pending turns that never entered the history are excluded.
    """
    try:
        run = json.loads(Path(log_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(1 for m in (run.get("history") or [])
               if isinstance(m, dict) and m.get("role") == "user")


def _summarize_records(run_records: list[dict[str, Any]]) -> dict[str, Any]:
    per_scenario: dict[str, Any] = {}
    overall_metrics = _empty_metric_counts()
    overall_runs = {"PASS": 0, "FAIL": 0}
    overall_tokens = _empty_token_counts()
    overall_turns_sum = 0
    overall_turns_count = 0

    for rec in run_records:
        sid = rec["scenario_id"]
        ps = per_scenario.setdefault(sid, {
            "runs": 0,
            "run_status": {"PASS": 0, "FAIL": 0},
            "metrics": _empty_metric_counts(),
            "tokens": _empty_token_counts(),
            "turns_sum": 0,
            "turns_count": 0,
        })
        ps["runs"] += 1
        ps["run_status"][rec["run_final_status"]] = ps["run_status"].get(rec["run_final_status"], 0) + 1
        for name in METRIC_NAMES:
            st = rec["metrics"].get(name, {}).get("status")
            if st in ps["metrics"][name]:
                ps["metrics"][name][st] += 1
        _add_tokens(ps["tokens"], rec.get("token_usage"))
        tt = rec.get("total_turns") or 0
        ps["turns_sum"] += tt
        ps["turns_count"] += 1

        overall_runs[rec["run_final_status"]] = overall_runs.get(rec["run_final_status"], 0) + 1
        for name in METRIC_NAMES:
            st = rec["metrics"].get(name, {}).get("status")
            if st in overall_metrics[name]:
                overall_metrics[name][st] += 1
        _add_tokens(overall_tokens, rec.get("token_usage"))
        overall_turns_sum += tt
        overall_turns_count += 1

    return {
        "per_scenario": per_scenario,
        "overall": {
            "runs": len(run_records),
            "run_status": overall_runs,
            "metrics": overall_metrics,
            "tokens": overall_tokens,
            "avg_turns": round(overall_turns_sum / overall_turns_count, 2) if overall_turns_count else 0,
        },
    }


def _build_summary(run_records: list[dict[str, Any]]) -> dict[str, Any]:
    base = _summarize_records(run_records)
    # Break the aggregate down per agent mode so legacy vs pure are comparable.
    by_agent: dict[str, Any] = {}
    for mode in sorted({r["agent_mode"] for r in run_records}):
        by_agent[mode] = _summarize_records(
            [r for r in run_records if r["agent_mode"] == mode])
    base["by_agent"] = by_agent
    return base


def _print_summary(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    print("\n=== Overall summary ===")
    print(f"Total runs: {overall['runs']}  run status: {overall['run_status']}")
    for name in METRIC_NAMES:
        print(f"  {name}: {overall['metrics'][name]}")
    _print_tokens("  ", overall["tokens"])
    print(f"  avg_turns: {overall.get('avg_turns', 0)}")
    print("\nPer scenario:")
    for sid, ps in summary["per_scenario"].items():
        avg = round(ps["turns_sum"] / ps["turns_count"], 2) if ps["turns_count"] else 0
        print(f"  {sid}: runs={ps['runs']} run_status={ps['run_status']} avg_turns={avg}")
        for name in METRIC_NAMES:
            print(f"      {name}: {ps['metrics'][name]}")
        _print_tokens("      ", ps["tokens"])

    print("\nBy agent mode:")
    for mode, sm in summary.get("by_agent", {}).items():
        print(f"  [{mode}] runs={sm['overall']['runs']} run_status={sm['overall']['run_status']} "
              f"avg_turns={sm['overall'].get('avg_turns', 0)}")
        for name in METRIC_NAMES:
            print(f"      {name}: {sm['overall']['metrics'][name]}")
        _print_tokens("      ", sm["overall"]["tokens"])


def _print_tokens(indent: str, tokens: dict[str, int]) -> None:
    print(f"{indent}tokens: prompt={tokens['prompt_tokens']} "
          f"completion={tokens['completion_tokens']} "
          f"total={tokens['total_tokens']} (calls={tokens['calls']})")


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
    parser.add_argument("--agent", nargs="*", choices=list(AGENT_MODES), default=list(AGENT_MODES),
                        help="Which agent(s) to test: legacy and/or pure (default: both).")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable the LLM room-size extractor for I2 (I2 -> FAIL).")
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
        agent_modes=args.agent,
    )


if __name__ == "__main__":
    main()
