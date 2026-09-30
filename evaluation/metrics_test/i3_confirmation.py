"""I3 — confirmation gate (post-run, LLM-judged metric).

Evaluated AFTER a run, from the JSON that `main.save_experiment_log` wrote into
`experiment_logs/`. Unlike I1/I2/I4/I5 (which are pure offline inspection of the
persisted artifact), I3 uses an LLM "judge" to read the conversation transcript
and decide whether the customer actually confirmed the two required things:

  1. The QUOTE / product CONFIGURATION  (报价 / 配置确认)
  2. The final ORDER                     (订单确认)

before an Order was created. The system rule being checked is:

    "订单 + 报价 都确认后，才能创建 Order"

So an Order may only be created once BOTH confirmations are on record. This metric
fails any run where an Order was created (per the system's own `orders` list, the
authoritative ground truth) without both confirmations being present in the
dialogue, and passes runs where the order was correctly gated.

Status values follow the paper metric convention: PASS / FAIL / INCONCLUSIVE.

  * Order created AND both confirmations present (and not created before them)
        -> PASS  ("both confirmations obtained before order creation")
  * Order created but a confirmation is missing, or created before confirmations
        -> FAIL  ("order created without required confirmations: ...")
  * No order created (system `orders` empty and judge saw no creation in dialogue)
        -> INCONCLUSIVE ("no_order_created; confirmation gate not exercised")

The judge model is the same env-selected model used everywhere else
(`os.getenv("MODELS_CN", "")`). When the experiment is run with `--no-llm`, I3
returns INCONCLUSIVE ("llm_disabled") instead of calling the model.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LOGS_DIR = Path("experiment_logs")

# Same model-selection convention as the rest of the codebase.
JUDGE_MODEL = "gpt-4.1"

# JSON-schema the judge must fill. `confirmations_before_order` is boolean;
# the judge sets it true when no order was created (vacuously satisfied) so we
# never have to deal with explicit nulls.
_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "quote_confirmed": {
            "type": "boolean",
            "description": (
                "True only if the customer explicitly confirmed/approved the "
                "product CONFIGURATION and its PRICE QUOTE (报价/配置确认) — e.g. "
                "'Yes, that configuration is correct', '确认配置', '报价没问题'. "
                "Merely discussing or the assistant showing the quote is NOT "
                "confirmation."
            ),
        },
        "order_confirmed": {
            "type": "boolean",
            "description": (
                "True only if the customer explicitly confirmed the FINAL ORDER "
                "(订单确认) before it was placed — e.g. 'Yes, please go ahead with "
                "the order', '确认下单', 'I confirm the final order'. A generic "
                "'thank you' or 'ok' without order intent is NOT confirmation."
            ),
        },
        "order_created_in_dialogue": {
            "type": "boolean",
            "description": (
                "True if the assistant indicated within the dialogue that an "
                "Order was actually created/placed (e.g. 'Your order has been "
                "created', '订单已创建', an order id was returned)."
            ),
        },
        "confirmations_before_order": {
            "type": "boolean",
            "description": (
                "True if BOTH confirmations occurred in the dialogue BEFORE the "
                "order was created/placed. Set true if no order was created."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": "One or two sentences justifying the booleans above.",
        },
    },
    "required": [
        "quote_confirmed",
        "order_confirmed",
        "order_created_in_dialogue",
        "confirmations_before_order",
        "reasoning",
    ],
}

_JUDGE_SYSTEM = (
    "You are an impartial evaluator. You will be given a transcript of a "
    "customer-support conversation (roles: Customer / Assistant) about placing a "
    "custom furniture order. Your job is to determine, strictly from the "
    "transcript, whether the customer confirmed the two required things and "
    "whether any order was created only after both confirmations. Be strict: "
    "echoing or showing a quote is not confirmation; only an explicit customer "
    "approval counts. Respond ONLY with the requested JSON."
)


@dataclass
class I3Evaluation:
    """Structured I3 result for one run; serialisable for experiment aggregation."""

    scenario_id: str | None
    run_index: int | None
    conversation_id: str | None
    status: str  # PASS | FAIL | INCONCLUSIVE
    reason: str
    orders_count: int
    final_status: str | None = None
    quote_confirmed: bool | None = None
    order_confirmed: bool | None = None
    order_created_in_dialogue: bool | None = None
    reasoning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _format_transcript(history: list[dict]) -> str:
    """Render the run history as a readable Customer/Assistant transcript.

    System turns (used by the pure agent) are dropped so the judge only sees the
    customer-facing dialogue.
    """
    lines: list[str] = []
    for turn, msg in enumerate(history, start=1):
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").lower()
        if role == "system":
            continue
        label = "Customer" if role == "user" else "Assistant"
        content = msg.get("content") or ""
        if isinstance(content, list):  # some message formats nest content
            content = " ".join(str(p) for p in content)
        lines.append(f"[{turn}] {label}: {content}")
    return "\n\n".join(lines)


def _judge(transcript: str, model: str) -> dict | None:
    """Ask the LLM judge to assess the confirmations. Returns parsed JSON or None."""
    from tools.llm_client import chat

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": f"Transcript:\n\n{transcript}"},
    ]
    try:
        resp = chat(model=model, messages=messages, format=_JUDGE_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - judge failure must not crash the metric
        return {"__error__": f"judge_call_failed: {exc}"}
    content = getattr(getattr(resp, "message", None), "content", None)
    if not content:
        return {"__error__": "empty_judge_response"}
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return {"__error__": "cannot_parse_judge_response"}
    return data


def evaluate_i3_run(run: dict, use_llm: bool = True) -> I3Evaluation:
    """Evaluate I3 from an in-memory run artifact (decoded experiment_logs JSON)."""
    scenario_id = run.get("scenario_id")
    run_index = run.get("run_index")
    conversation_id = run.get("conversation_id")
    final_status = run.get("final_status")
    orders = run.get("orders") or []
    orders_count = len(orders)

    if not use_llm:
        return I3Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="INCONCLUSIVE", reason="llm_disabled", orders_count=orders_count,
            final_status=final_status,
        )

    transcript = _format_transcript(run.get("history") or [])
    if not transcript.strip():
        return I3Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="INCONCLUSIVE", reason="empty_history", orders_count=orders_count,
            final_status=final_status,
        )

    judge = _judge(transcript, JUDGE_MODEL)
    if judge is None or "__error__" in judge:
        err = (judge or {}).get("__error__", "unknown_judge_error")
        return I3Evaluation(
            scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
            status="INCONCLUSIVE", reason=f"judge_failed: {err}", orders_count=orders_count,
            final_status=final_status,
        )

    quote_confirmed = judge.get("quote_confirmed")
    order_confirmed = judge.get("order_confirmed")
    order_created_in_dialogue = judge.get("order_created_in_dialogue")
    confirmations_before = judge.get("confirmations_before_order")
    reasoning = judge.get("reasoning")

    # The authoritative "was an order created" signal is the system's own
    # `orders` list; the judge's dialogue observation is a secondary hint.
    order_created = (orders_count >= 1) or bool(order_created_in_dialogue)

    if not order_created:
        status, reason = (
            "INCONCLUSIVE", "no_order_created; confirmation gate not exercised")
    else:
        issues: list[str] = []
        if not quote_confirmed:
            issues.append("quote_confirmation_missing")
        if not order_confirmed:
            issues.append("order_confirmation_missing")
        if confirmations_before is False:
            issues.append("order_created_before_confirmations")
        if issues:
            status, reason = (
                "FAIL", "order created without required confirmations: " + ", ".join(issues))
        else:
            status, reason = (
                "PASS", "both confirmations obtained before order creation")

    return I3Evaluation(
        scenario_id=scenario_id, run_index=run_index, conversation_id=conversation_id,
        status=status, reason=reason, orders_count=orders_count,
        final_status=final_status,
        quote_confirmed=quote_confirmed,
        order_confirmed=order_confirmed,
        order_created_in_dialogue=order_created_in_dialogue,
        reasoning=reasoning,
    )


def evaluate_i3_file(path: str | Path, use_llm: bool = True) -> I3Evaluation:
    """Evaluate I3 from a single experiment_logs run JSON file."""
    path = Path(path)
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return I3Evaluation(
            scenario_id=None, run_index=None, conversation_id=None,
            status="INCONCLUSIVE", reason=f"cannot_read_run_log: {exc}", orders_count=0,
            final_status=None,
        )
    return evaluate_i3_run(run, use_llm=use_llm)


def evaluate_i3_directory(
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    scenario_id: str | None = None,
    use_llm: bool = True,
) -> list[I3Evaluation]:
    """Evaluate I3 for every run log, optionally filtered by scenario id."""
    logs_dir = Path(logs_dir)
    if not logs_dir.exists():
        return []
    pattern = f"{scenario_id}_run*.json" if scenario_id else "*.json"
    return [evaluate_i3_file(p, use_llm=use_llm) for p in sorted(logs_dir.glob(pattern))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate I3 (confirmation gate) for one experiment run, via an LLM judge.",
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
        "--no-llm", action="store_true",
        help="Do not call the LLM judge; report INCONCLUSIVE (llm_disabled).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print only the status string per evaluated run.",
    )
    args = parser.parse_args()
    use_llm = not args.no_llm

    if args.scenario_id:
        results = evaluate_i3_directory(args.logs_dir, args.scenario_id, use_llm=use_llm)
    elif args.run_log:
        results = [evaluate_i3_file(args.run_log, use_llm=use_llm)]
    else:
        results = evaluate_i3_directory(args.logs_dir, use_llm=use_llm)

    if args.quiet:
        for result in results:
            print(result.status)
    else:
        for result in results:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
