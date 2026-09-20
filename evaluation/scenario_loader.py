"""Strict, uncached fixture loading and read-only shared-business comparisons."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from evaluation.scenario_spec import ScenarioSpec

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIRECTORY = Path(__file__).parent / "scenarios"


class FixtureDiscrepancy(ValueError):
    """A frozen expectation disagrees with its referenced repository evidence."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_repository(scenario: ScenarioSpec) -> None:
    """Compare with shared pure lookups, never redefine expected values.

    Uses lookup_product_pricing, lookup_shipping_rate, validate_room_size and
    calculate_total_price only. The rules module imports state types, but no
    workflow state is instantiated, observed, or exposed through the contract.
    """
    def compare(boundary, expected, actual):
        if expected != actual:
            raise FixtureDiscrepancy(
                f"{scenario.scenario_id}: {boundary}: frozen={expected!r}; repository={actual!r}"
            )

    for reference in (scenario.fixtures.product_catalog, scenario.fixtures.shipping):
        path = REPOSITORY_ROOT / reference.path
        if not path.resolve().is_relative_to(REPOSITORY_ROOT):
            raise FixtureDiscrepancy(f"{scenario.scenario_id}: reference escapes repository: {reference.path}")
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise FixtureDiscrepancy(f"{scenario.scenario_id}: unavailable fixture {reference.path}: {exc}") from exc
        compare(f"{reference.path} sha256", reference.sha256, actual_hash)

    from workflow.order.order_creation_catalog import lookup_product_pricing
    from workflow.order.order_creation_shipping import lookup_shipping_rate
    from workflow.order.order_creation_rules import validate_room_size, calculate_total_price

    truth = scenario.customer.ground_truth
    configuration = truth.configuration
    try:
        pricing = lookup_product_pricing(**configuration.model_dump(exclude={"quantity"}))
        shipping = lookup_shipping_rate(configuration.table_size, truth.delivery_address.postcode)
        room = validate_room_size(truth.room_size, configuration.table_size)
        totals = calculate_total_price(pricing["unit_price"], shipping["per_table_shipping_rate"], configuration.quantity)
    except (ValueError, LookupError, OSError) as exc:
        raise FixtureDiscrepancy(f"{scenario.scenario_id}: shared business lookup: {exc}") from exc
    expected = scenario.evaluation.system_derived
    compare("product_sku", expected.product_sku, pricing["product_sku"])
    # VALID configuration and successful order imply suitable room; the workbook
    # does not supply a separate system-derived room-result field.
    compare("VALID configuration room suitability", "SUITABLE", room["result"])
    compare("shipping postcode match", True, shipping["matched"])
    for field, actual in {
        "base_model_price": pricing["base_price"],
        "customisation_price": pricing["customisation_price"],
        "unit_price": pricing["unit_price"], **totals,
    }.items():
        compare(f"pricing.{field}", Decimal(getattr(expected.pricing, field)), actual)


def load_scenario(path: str | Path, *, check_repository: bool = True) -> ScenarioSpec:
    """Every call parses a fresh object; strict JSON admits arrays for tuples only."""
    raw = Path(path).read_text(encoding="utf-8")
    json.loads(raw, object_pairs_hook=_unique_object)
    scenario = ScenarioSpec.model_validate_json(raw)
    if check_repository:
        validate_repository(scenario)
    return scenario


def load_scenarios(directory: str | Path = SCENARIO_DIRECTORY, *, check_repository: bool = True) -> tuple[ScenarioSpec, ...]:
    scenarios = tuple(load_scenario(path, check_repository=check_repository)
                      for path in sorted(Path(directory).glob("*.json")))
    if not scenarios:
        raise ValueError("No scenario fixtures found")
    identifiers = [scenario.scenario_id for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate scenario_id")
    return scenarios
