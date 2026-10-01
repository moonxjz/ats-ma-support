"""Shared scenario-outcome helpers for the metrics_test suite.

Every scenario declares its expected final state in its `outcome` block:

    "outcome": {
        "expected_persisted_order_count": 0 | 1,   # 0 => customer cancels / no order
        ...
    }

This resolves the ambiguity that previously produced the INCONCLUSIVE status:

  * A scenario with `expected_persisted_order_count == 0` is a *cancellation /
    no-order* scenario. The CORRECT outcome is that **no** order is created, so:
        - no order committed  -> PASS  ("no_order_as_expected")
        - any order committed -> FAIL  ("order_created_when_none_expected")
    This override applies to every metric uniformly (the user rule: "no order is
    PASS, generating an order is all FAIL").

  * A scenario with `expected_persisted_order_count == 1` is an *order* scenario.
    The metric's own substantive PASS/FAIL logic applies. Cases that previously
    returned INCONCLUSIVE (e.g. the expected order was never produced, or an
    evaluation/infrastructure error prevented scoring) now resolve to FAIL, so
    the INCONCLUSIVE status is eliminated entirely.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_SCENARIOS_DIR = Path("evaluation/scenarios")

NO_ORDER_PASS_REASON = "no_order_as_expected"
NO_ORDER_FAIL_REASON = "order_created_when_none_expected"


def load_scenario(
    scenario_id: str | None,
    scenarios_dir: str | Path = DEFAULT_SCENARIOS_DIR,
) -> dict | None:
    """Load a scenario JSON by id from the scenarios directory."""
    if not scenario_id:
        return None
    path = Path(scenarios_dir) / f"{scenario_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def scenario_expects_no_order(scenario: dict | None) -> bool:
    """True iff the scenario explicitly declares NO order should be created.

    A scenario is a cancellation / no-order scenario when its outcome block asks
    for `expected_persisted_order_count == 0`. When the scenario is missing or
    declares 1 (the default), it is treated as an order-expecting scenario.
    """
    if not scenario:
        return False
    outcome = scenario.get("outcome") or {}
    return outcome.get("expected_persisted_order_count", 1) == 0
