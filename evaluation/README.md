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

## CS1-B deterministic customer simulator

CS1-B implements a pure customer decision step over the CS1-A customer projection
and public text. It does not load scenarios, access catalog/shipping data, import
ATS agents/state/runtime, send messages, or run conversations. Fixture loading and
`scenario.customer_view()` happen outside the simulator. CS1-C catalog integration
and the experiment runner remain unimplemented.

The two implementation modules are:

- `evaluation/public_observation.py`: strict public message/evidence contracts,
  bounded public-text grammar, availability statements, and artifact extraction.
- `evaluation/customer_simulator.py`: customer state, discriminated action and stop
  contracts, policy decisions, comparison of customer-intended facts, and wording.

The public entry point is `step(CustomerSimulatorInput(...)) -> SimulatorStep`.
Input fields are `customer: CustomerScenario`, `state: CustomerSimulatorState`,
and `public_history: tuple[PublicMessage, ...]`. `latest_support_response` is a
property derived from the last assistant message. `PublicMessage` has only `role`
(`user` or `assistant`) and nonblank `text`; no raw runtime/session/result objects
are accepted. The first call requires empty history and a fresh state.

`SimulatorStep` contains `decision` and a proposed `state`. A decision is either
`CustomerTurn` (ordered `actions`, deterministic `message`) or `StopDecision`.
The caller adopts the proposed state only after accepting the generated message
into public history. The caller supplies that public user message on subsequent
calls. Sending, delivery acknowledgement, transcript continuity, replay/session
validation, and persistence belong to the future harness. The simulator does not
hash public history or own session integrity.

### Customer state and contracts

`CustomerSimulatorState` contains exactly:

| Field | Meaning |
| --- | --- |
| `turn_index` | Number of customer messages emitted |
| `disclosed_fields` | Unique customer/configuration identifiers actually communicated |
| `observed_target_offers` | Latest retained positive evidence per target field |
| `pending_requests` | Ordered unresolved requests explicitly made in public text |
| `pending_discovery` | Outstanding customer options/target question, field, and public user-message index |
| `approval_receipts` | Public artifact/request references, customer approval index, and `repeated` flag |
| `stop_decision` | Terminal public decision, if any |

`EvidenceRef` contains `message_index`, `start`, `end`, and exact `quote`. References
must resolve against public history with the correct role. Stored target offers
are also reparsed to verify positive, field-specific evidence. Stored requests and
approval receipts must resolve to their corresponding public structures; pending
questions must match the customer's public question. These checks verify cited
evidence, not whole-session integrity. State and nested contracts are frozen,
strict, forbid extras, and use tuples. Validation errors leave input state intact.

`CustomerAction` is a Pydantic discriminated union on `kind`:

| Variant class | Kind | Payload |
| --- | --- | --- |
| `InitialMessage` | `INITIAL_MESSAGE` | None |
| `ProvideInformation` | `PROVIDE_INFORMATION` | Ordered, nonempty, unique information `fields` |
| `AskAvailableOptions` | `ASK_AVAILABLE_OPTIONS` | Configuration `field` |
| `AskAboutTargetOption` | `ASK_ABOUT_TARGET_OPTION` | Configuration `field` |
| `SelectOption` | `SELECT_OPTION` | Configuration `field` |
| `ConfirmConfiguration` | `CONFIRM_CONFIGURATION` | Public `artifact` evidence reference |
| `ConfirmFinalOrder` | `CONFIRM_FINAL_ORDER` | Public `artifact` evidence reference |

Actions never carry independently supplied customer values. `customer_value`
resolves fields against `CustomerScenario.ground_truth`; the internal renderer uses
fixed templates. Configuration selection cannot be smuggled into
`ProvideInformation`. Initial and confirmation actions occur alone. A customer
turn may answer several requested facts and ask at most one discovery question.
Facts retain request order; the discovery question is placed last so a subsequent
fieldless availability reply has an unambiguous public referent. Unresolved fields
from a compound request remain pending rather than being silently discarded.

`StopDecision` has `kind="STOP"`, a strict `reason`, public `evidence`, and an
optional configuration `field`. Public stop reasons require evidence; target stop
reasons require a field. Reasons are:

- `ORDER_CREATED_PUBLICLY_REPORTED`
- `PUBLIC_CONTENT_MISMATCH`
- `SIMULATOR_UNINTERPRETABLE_RESPONSE`
- `TARGET_OPTION_DENIED`
- `TARGET_OPTION_NOT_RESOLVED`
- `PUBLIC_UNAVAILABLE_RESPONSE`
- `RUNAWAY_LIMIT_REACHED`

A stop emits no customer message. Visible creation reporting is not proof that an
order was persisted. There is no customer clarification/correction action.

### Public parser grammar and limitations

The parser reads Support text only; the simulator supplies topic context derived
from a preceding public customer question. It does not receive business results,
internal requested-field lists, workflow stages, or hidden catalog options.

Recognized requests include the shared deterministic form
`Could you please provide your {labels}?`, plus `Please choose {label}.` and
`Which {label} would you like?`. Labels support comma/and lists. Canonical labels
are full name, phone number, email address, street address, city, state, postcode,
country, room size, table model, table size, timber, timber finish, cloth colour,
bracket, top profile, quantity, company name, and special instructions. Explicit
aliases include model, felt colour/color, cloth color, and top rail profile.
Labels in these bounded request forms are case-sensitive as emitted by the shared
presentation; arbitrary paraphrases are not inferred.

Deterministic `Supplied value`, `Supported format`, and `Supported values` JSON
lines are recognized after a request. Only a valid `Supported values` list with
one unambiguous configuration topic supplies offer evidence. A quoted supplied
value is never an offer.

Availability grammar accepts whole-line constructions such as:

- `Available timber options include Tassie Oak, Marri and Zebra.`
- `Available models include Saga.`
- `Timber options are Tassie Oak and Zebra.`
- `Marri is available for timber.`
- `Tassie Oak and Zebra are available.` when the public question establishes timber.
- `Yes, Marri is available.` after a field-specific public question.
- `Marri is unavailable.` / `Marri is not available.` in the same context.

Grammatical keywords/topics are case-insensitive, but target text must match the
complete canonical customer target exactly. Candidate lists are split on commas
and `and`; no catalog vocabulary is imported. Supported candidate spelling is
bounded to ASCII letters/digits, spaces, slash and hyphen, covering S01-S03.

Negative, hypothetical, quoted, interrogative, conflicting, ambiguous, or wrong-
field mentions cannot authorize selection. A fieldless response with multiple
current topics is uninterpretable. A target absent from an otherwise recognized
options list triggers the one permitted target-specific question. An explicit
denial stops. A recognized answer that still omits the target after that question
stops unresolved; unrecognized prose stops uninterpretable. The simulator never
selects an alternative. Explicit denial overrides a previously observed offer.

Recognized request/option lines can be separated by newlines. Arbitrary prose,
Markdown option bullets, multiple prose sentences on one line, speculative
availability, unlisted wording, and unsupported framing are conservatively
uninterpretable. No keyword-only or LLM fallback is used. Consequently, valid but
uncovered generated Support wording can cause a simulator stop. Future grammar
extensions must preserve the same positive-evidence rules for every architecture.

Creation reporting currently recognizes `Your order [optional identifier] has
been created.` with optional `Its status is STATUS.`. Negated, future, conditional,
quoted, and interrogative variants do not count. Unavailable-response grammar
covers bounded `I can't/cannot ... here [yet].`, `I don't have ... here.`, and
`Information is unavailable here.` forms. Other routing/failure prose stops as
uninterpretable instead of being repaired.

### Scenario behavior and confirmation

S01 sends the exact frozen initial message, answers requested facts, and directly
repeats any requested known configuration selection without discovery. S02 does
the same for model, size, bracket and top profile; timber, finish and felt require
public offer evidence. S03 discovers all seven fields. Its initial public question
already asks for model options, so the initial step establishes pending model
discovery without a scenario-ID or architecture branch.

All three preserve initial message whitespace. Asking whether a target exists
does not mark that selection disclosed. Selection marks it disclosed only after
the policy permits it. A matching system-filled summary cannot bypass undisclosed
configuration selections. If Support ignores an outstanding discovery question
and asks something unrelated, the simulator stops rather than silently repairing
the interaction.

The parser reuses only public field-label constants from the pure shared
`confirmation_presentation` module. It does not import Support/ATS state. Artifact
bodies require the exact shared headings, sections, row labels/order, and scalar
structure. Values are decoded using the shared Markdown/HTML/newline encoding
with a canonical round-trip check. Missing/duplicate rows or malformed structures
are uninterpretable. Optional company/instruction rows are supported.

Confirmation framing is generated prose in ATS, not deterministic artifact data.
The bounded grammar accepts no introduction or `Please review the details below.`
(or a matching configuration-summary/provisional-order review introduction), plus
an explicit scoped request. Configuration examples include `Please confirm the
configuration or tell me what to change.` and `Could you approve this configuration?`.
Final examples include `Please explicitly confirm you wish to place this order,
or tell me what to change.` and `Do you wish to place this provisional order?`.
Unsupported or contradictory framing cannot authorize approval. An artifact alone
or approval request alone is insufficient.

Configuration checks all seven selections and quantity against customer truth.
Final checks customer identity/contact, all five address fields, configuration,
quantity, room size, and applicable optional customer-provided facts. Comparison
is exact after presentation decoding. Final approval also requires a prior
customer-side configuration approval receipt.

The customer accepts system-derived pricing/product-code values as presented,
provided the customer-intended facts match and the required final artifact and
placement request are present. Product code and all four public pricing rows must
exist with valid presentation structure. Their correctness, price arithmetic,
shipping rules, and equality to evaluator expectations are NOT checked by the
simulator. There are no evaluator or business-lookup inputs.

Repeated identical configuration/final artifacts with a new explicit approval
request receive the same deterministic approval message. Each receipt records the
new public evidence and customer-message index, with `repeated=True` for an exact
previously approved artifact body. Changed customer-intended content stops with
`PUBLIC_CONTENT_MISMATCH`. Changed system-derived values alone remain acceptable
as presented; a different complete body is recorded as a new artifact receipt,
not an identical repeat. Prior approval never automatically authorizes a new body.

### Atomic updates and development safeguard

Parsing and policy decisions operate on immutable inputs. Rendering completes
before any proposed state is returned. A failure cannot partly disclose a field,
record an approval, or advance the turn. Expected interpretation failures return
a stop; invalid contracts/evidence and unexpected rendering errors raise without
fallback messages. Sending and accepting the proposed state remain caller duties.

`MAX_CUSTOMER_MESSAGES = 64` is a **DEVELOPMENT RUNAWAY SAFEGUARD, NOT THE FROZEN
EXPERIMENT INTERACTION BUDGET**. It includes the initial message and repeated
approvals. After 64 emitted messages the next step stops without emitting another.
The formal common A1/A2/A3 interaction budget will be determined after pilot runs
and before the paper benchmark is frozen. This limit is not stored in S01-S03.

The same customer projection, simulator state and public transcript produce the
same actions/messages regardless of architecture. No architecture label exists
in the simulator API. Parser limitations and development safeguards apply equally.

Run CS1-B and CS1-A deterministic tests:

```sh
python -m unittest test_public_observation test_customer_simulator test_scenario_spec -v
```

Tests use synthetic public messages and pure artifact renderers, not runtime
conversations or live Ollama. They cover all seven action variants, strict
contracts, reference verification, negative/ambiguous option evidence, compound
requests, S01-S03 discovery behavior, every intended artifact-field mismatch,
repeated approvals, public system-derived values independent of evaluator truth,
immutability/failure behavior, architecture-independent results, and the safety
limit. No CS1-C adapter or experiment runner is included.
