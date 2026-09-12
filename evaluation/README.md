# ScenarioSpec v1 (CS1-A)

This directory contains architecture-neutral, frozen ORDER_CREATE benchmark
contracts and fixtures. It stores customer policies; it does not execute them.

```python
from evaluation.scenario_loader import load_scenarios

scenarios = load_scenarios()  # structural, cross-field and repository checks
customer = scenarios[0].customer_view()  # isolated customer-only projection
```

`load_scenario(path)` loads one fixture. `check_repository=False` permits
structural/cross-field checks alone; repository comparison is on by default.
Loading rejects duplicate JSON keys and batch scenario IDs. Required strings are
nonblank and are never stripped. Models forbid extra fields and coercion. Nested
models are frozen and collections are tuples. Every load/projection is independent.
Quantity is a strict positive integer; it is not restricted to one.

## Source and provenance

The authoritative human source is `scenario 1-3(1).xlsx`, Sheet1, columns A/B/C
for S01/S02/S03. It was read directly from the supplied Desktop file. Workbook
SHA-256: `71f011411dcc46ff6ed5dab05ed756862f02650ee098f2e598bda675f05a4305`.
The workbook is not required at runtime or for the deterministic test suite.
Fixture byte hashes and exact initial messages are pinned in tests.

Shared data bytes were verified against HEAD `5653d7c` on
`evaluation/customer-simulator`:

| File | SHA-256 |
| --- | --- |
| `data/product_prices.json` | `ef8ac04e8ac00a6690f60ecd53b4418fbc4bdd3ccc2d527e65cdb2afdb9cc007` |
| `data/shipping_rates.json` | `933d9ef7ac2910e74642f874e30a78be97935a48a19defe7f3d6f9ec72650cb3` |

The shipping hash above was explicitly approved after the earlier supplied
provenance hash was found stale. No shared data or business logic was changed.

## Workbook encoding

| Source cells/sections | Contract location |
| --- | --- |
| Rows 1–6 | scenario ID/name; customer knowledge and exact profile description |
| Rows 9–10 | initial state null workflow and existing order |
| Rows 14–35 | typed customer ground truth, five-field address, room, configuration |
| A39/B39/C39 | exact initial message, including leading/internal spaces |
| A41–59 | initial disclosures, subsequent disclosure and confirmation policy |
| B41–97 / C41–94 | discovery constraints, known/unknown fields, information and confirmation policy |
| A61–70 / B99–108 / C96–105 | evaluator SKU and decimal-string prices |
| A72–88 / B110–124 / C107–122 | evaluator workflow and outcome |
| A90–98 / B125–133 / C124–132 | complete I1–I8 status mapping |
| B136–141 / C135–139 | scenario-specific interaction properties |

S01 knows all seven configuration selections; its explicit disclosure list also
includes quantity. S02 knows and initially discloses model, size, bracket and top
profile; “one” in B39 also discloses quantity. S03 knows/discloses no configuration
selection. These are human-readable fixture declarations, not NLP inference at
runtime. Known fields and disclosures remain separate reusable concepts.

B/C per-field `target_selection` repeats the corresponding ground-truth value;
these values were compared directly and encoded once under
`customer.ground_truth.configuration`. Initially known fields in the mixed policy
select directly; the discovery-fields tuple identifies every unknown field that
asks available options before selection. Both discovery scenarios retain the
positive-offer constraint and ASK_ABOUT_TARGET_OPTION fallback. S01 has no
invented discovery structure or interaction expectations.

Absent company/instruction values remain optional `None`; no values are invented.
Null initial workflow state means no active workflow, with no controller state
serialization. No additional initial-store or session facts are asserted.

CONFIRM_WITHOUT_CHANGE retains the request's explicit semantics: the matching
public artifact must be displayed, confirmation explicitly requested, and the
artifact must match customer truth. The flags store this contract only; they do
not recognize artifacts or generate actions. The workbook does not define extra
unsafe-response stopping rules or evaluation assertions, so none are invented.

## Validation and information boundary

Repository validation verifies actual file hashes before invoking these existing
read-only deterministic functions:

- `order_creation_catalog.lookup_product_pricing`: canonical model/size/options,
  unchanged SKU, base/customisation/unit prices.
- `order_creation_shipping.lookup_shipping_rate`: postcode match and shipping rate.
- `order_creation_rules.validate_room_size`: room suitability for the selected size.
- `order_creation_rules.calculate_total_price`: quantity-dependent freight and total.

The workbook has no separate room-suitability expectation; its VALID configuration
and successful outcome are checked for suitable room dimensions. Money is stored
as nonnegative decimal strings and compared using Decimal, with no currency/tax
assumptions added. The shared per-table shipping rule is used unchanged.

The rules module imports existing state types, but the validator only invokes the
named pure functions. The ScenarioSpec contract imports no architecture module.
No runtime, workflow, controller, order-store writes, evaluator execution, or
Ollama calls occur. Evaluation expectations and fixture references stay outside
`CustomerScenario`; its projection exposes no evaluator prices, SKU or invariants.

I1–I8 retain their paper meanings:

- I1: required-information completeness
- I2: configuration validity
- I3: explicit confirmation before side effect
- I4: material changes invalidate prior confirmation
- I5: committed snapshot equals confirmed snapshot
- I6: at-most-once commit
- I7: failed validation requires appropriate re-entry
- I8: side effects require workflow-state authorization

S01–S03 expect I4/I7 NOT_EXERCISED and the other six SATISFIED. These are benchmark
expectations, not claims that CS1-A has executed or measured the invariants.

Any repository mismatch raises `FixtureDiscrepancy` with scenario, boundary, frozen
expectation and repository evidence (or lookup error). It does not repair fixtures
or change business rules. Changing scenario semantics requires human approval.

Run deterministic checks with the repository's pinned dependencies installed:

```sh
python -m unittest test_scenario_spec test_order_creation_catalog test_order_creation_shipping test_order_creation_rules -v
```

CS1-B, simulator behavior, conversation execution and architecture integration are
outside this implementation.
