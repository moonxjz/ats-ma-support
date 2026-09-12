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
`scenario.customer_view()` happen outside the simulator. CS1-C supplies a separate static Support knowledge provider, described below.
The experiment runner remains unimplemented.

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

## CS1-C shared static product knowledge

To isolate workflow-control allocation, authoritative product-option knowledge is
provided through a shared static knowledge provider and the existing Support
knowledge interface. Future A1/A2/A3 integrations must use the same snapshot,
projection rules, Support prompt, model/settings and grounding checks. In
production, facts would normally come from catalog/database tools. Tool selection,
retrieval planning, retrieval latency and recovery are excluded from this design.

`evaluation/static_product_knowledge.py` provides:

```python
load_static_product_knowledge(
    path: str | Path = CATALOG_PATH,
    *, expected_sha256: str,
) -> StaticProductKnowledge

provide_support_knowledge(
    current_customer_message: str,
    public_history: tuple[PublicMessage, ...] = (),
    *, knowledge: StaticProductKnowledge,
) -> SupportKnowledgeContext | None

detect_product_query(current_customer_message: str) -> ProductQuery | None
```

Load the catalog once at setup, then reuse the validated immutable snapshot. The
expected hash is REQUIRED and caller-owned; it can come from the frozen fixture's
`fixtures.product_catalog.sha256`. The provider contains no hard-coded benchmark
hash. It verifies actual bytes, rejects malformed sources, and never reads files
or invokes tools/models during enquiry projection. No second manually maintained
option fixture is created.

`StaticProductKnowledge` contains `source_path`, `source_sha256`,
`model_sizes: tuple[ModelSizeOptions, ...]`, and tuple fields `timber`,
`timber_painting`, `felt_color`, `bracket`, `top_profile`. `ModelSizeOptions` has
`product_model` and `table_sizes`. Properties `product_models` and `table_sizes`
derive model order and the global size union from those mappings. The models are
strict, frozen, forbid extras and revalidate supplied instances. They contain no
SKU, price, shipping, room-validation or workflow values.

### Authoritative universes

Values below preserve current source spelling and order; provider code derives
them from the catalog rather than embedding this table.

| Field | Values |
| --- | --- |
| `product_model` | Odyssey; Odyssey Rise; Saga; Kings Cross; Sleek; Cyber; Double Moon; Wave; Victory; Regent; Regent Rise; Homestead; Southern Cross; Executive; Melody; Prism; Rustic |
| `table_size` | 7ft; 8ft; 9ft, subject to model |
| `timber` | Tassie Oak; American Oak; Messmate; Zebra; Blackwood; Myrtle; Marri; Camphor Laurel; Jarrah |
| `timber_painting` | Natural; Black; Nutmeg; Riverbed; Stone; Teak; Walnut; Wenge; White; Jarrah; Umber |
| `felt_color` | Olive; Blue; Burgundy; Black; Red; Purple; Grey |
| `bracket` | Standard rubber; Stainless Steel; Brass; Black Powder; Black Chrome; Copper |
| `top_profile` | Bull-nose Edge - with black steel side skirt; Bull-nose Edge - with stainless steel side skirt; Bull-nose Edge - with matching timber side skirt; Ball Return; Ball Return with Timber Cladding; Waterfall; Live-Edge |

Odyssey Rise, Sleek, Double Moon, Wave, Regent Rise, Melody and Prism support 7ft
and 8ft. The other ten models also support 9ft. Existing `DEMO_TABLE_SIZES`
restricts this experiment to 7ft/8ft/9ft despite retained 6ft source records.
These are supported configuration options, not claims about live inventory.

Model names come from explicit `product_model` mappings, not display-title
parsing. Repeated models across sizes are expected; duplicate model/size pairs,
duplicate category/title records and case-conflicting spellings fail validation.
Canonical strings are not silently stripped or recased. SKU is never a
deduplication key: Tassie Oak and American Oak remain distinct options even though
their source SKU repeats. Source price/SKU strings are checked for presence only,
then discarded; no pricing computation or SKU projection occurs.

### Public query interpretation and projection

The bounded grammar recognizes one public question using forms such as:

- `What timber options are available?`
- `What models do you have available?` / `What models do you have?`
- `What felt colours can I choose?`
- `What table sizes are available for Saga?`
- `Is Marri available for timber?`

Aliases cover the seven discovery fields, including timber types/finishes,
felt/cloth colour/color, brackets, and top profile/top-profile. Longer phrases
such as timber finish remain separate from timber. Grammar keywords are
case-insensitive; explicit model names retain canonical matching.

The parser recognizes the S03 buying-intent preamble plus its model question,
and bounded factual lines preceding CS1-B's final discovery question. These
prefixes do not supply authoritative product facts. Multiple questions, ambiguous
multi-topic questions, quoted questions, unknown preambles, and unsupported
price/shipping/room queries return `None`. There is no LLM interpretation or hidden
state fallback. Broader paraphrases remain outside this grammar.

Table-size context follows these rules:

1. A canonical model explicitly identified in the current size question wins.
2. Otherwise, one unique prior customer selection of the form
   `For table model, I'll choose Saga.` may establish the model.
3. Only user-role selection statements count. Support lists, mentions, customer
   availability questions and vague references do not establish a selection.
4. Conflicting/unknown selections or ambiguous current model references yield the
   global size union, explicitly qualified by `Size availability depends on model.`
5. An ambiguous/unknown explicit current reference cannot fall back to an older
   model selection. Repeated identical customer selections remain unambiguous.

For a timber question the provider returns one `KnowledgeFact` containing:

> Available timber options are Tassie Oak, American Oak, Messmate, Zebra, Blackwood, Myrtle, Marri, Camphor Laurel and Jarrah.

For an explicit Sleek size question it returns:

> Available table size options for Sleek are 7ft and 8ft.

With no unique public model it returns:

> Table sizes available across supported models are 7ft, 8ft and 9ft. Size availability depends on model.

Each fact has `source_reference="Shared product option catalog"`. Source paths and
hashes stay outside Support's prompt payload. `answer_facts` is a fresh list each
call; both ticket fields remain null. No unrelated option categories are included.
The existing context has a frozen shell but a mutable list, so callers receive
independent copies instead of a cached context object.

Target questions receive the SAME complete relevant option set as general option
questions, including when the named candidate is unknown. The provider does not
copy the queried candidate into authoritative facts. Same question/public model
context produces identical knowledge regardless of scenario or architecture;
neither label, scenario ground truth, customer action/state, nor evaluator data
is accepted by the API.

### Unchanged Support path and known limitations

The future harness can pass provider output directly to the existing
`process_customer_message(..., support_knowledge=context)` hook. Root forwards it
as `handle_support_action(..., business_context=context)` only on a Support route.
The provider does not influence classification or override routing. S03's mixed
order-intent/model question may still be routed to order creation, where this
knowledge is not consumed. That remains a pilot/integration risk, not something
CS1-C silently repairs.

Support projects facts into `ResponseContext.allowed_facts['answer_facts']` and
uses its unchanged system prompt, qwen3:8b, `think=False`, structured response
schema and last six public history messages. Sampling options remain unspecified
in the existing call; future experiments should record model revision/effective
settings. The provider output remains suitable for a future database/tool adapter
without changing Support's interface.

The static source is authoritative, but existing Support grounding does NOT prove
semantic membership/completeness of generated nonnumeric options. Mocked tests
show both `Available timber options include InventedWood.` and an incomplete
`Available timber options include Marri.` pass the current checks. CS1-C does not
repair or replace these responses; the exact public text is preserved for later
evaluation/diagnosis. Numeric and other existing guards still apply.

Compatibility tests pass representative public answers through unchanged mocked
Support generation and the actual CS1-B parser:

| Public answer format | Existing CS1-B result |
| --- | --- |
| `Available timber options include Marri.` | Accepted |
| `Available timber options include Tassie Oak, Marri, Zebra.` | Accepted |
| `Available timber options include Tassie Oak, Marri and Zebra.` | Accepted |
| `We offer several timber choices. Available timber options include Tassie Oak, Marri and Zebra.` | Rejected as uninterpretable |
| `Available timber options include:` followed by Markdown `-` item lines | Rejected as uninterpretable |

A single-sentence paragraph matching the list grammar is accepted; the tested
paragraph with introductory prose is rejected. These are format observations,
not claims that every paragraph/bullet response has been exhaustively classified.
Other generated phrasing, including model-qualified size answers, can likewise
fall outside the narrow CS1-B grammar. No CS1-B change or Support wording
constraint is included. A later approval must choose grammar extensions,
constrained wording, or deterministic presentation if desired.

### Failure behavior and validation

Missing source files, malformed records, missing required option sets, conflicting
duplicates and hash mismatch raise `KnowledgeValidationError` during setup.
Unsupported/ambiguous public queries return `None`. If routed to ANSWER_ENQUIRY,
absent knowledge follows existing `INFORMATION_UNAVAILABLE` behavior. No fact is
invented to recover from either case.

Support generation/JSON/grounding errors propagate unchanged. On the enquiry path
runtime wraps them as `TurnFailure(phase="business execution", pending_turn=None)`;
there is no PendingTurn for Support enquiry generation. PendingTurn remains the
separate existing business-result response-composition mechanism. No automatic
retry, state mutation, message repair or persistence is added.

`test_static_product_knowledge.py` covers exact options/order, provenance ownership,
source errors, topic grammar, public model context, immutable snapshot/fresh
contexts, target/architecture independence, hidden-value exclusion, mocked Support
payloads, a single mocked runtime Support turn, failure behavior, parser format
compatibility and the grounding limitation. It does not run full conversations.

CS1-C adds only the provider and its tests plus this documentation. No Support,
Root, Classifier, runtime, Order Agent, Controller, CS1-A/CS1-B or catalog changes
are required. CS1-D and real experiment execution remain outside this stage.

## CS1-D experiment runner

`experiment_runner.py` adds sequential orchestration and separate, minimal pilot
consistency validation. CS1-D implementation has deterministic mocked tests only.
Live qwen/Ollama execution requires separate approval. There is no automatic CLI
or batch execution entry point. After future approval, run S01, S02, then S03
individually, stopping at the first failure and preserving its evidence.

`prepare_pilot()` validates S01/S02/S03 and loads one shared static knowledge
snapshot. `run_scenario(scenario, run_directory=..., knowledge=...,
execution_factory=...)` returns raw `ExperimentRunResult`. Omitting the execution
factory selects the existing A3 runtime and **can invoke live models**. Tests
always inject scripted execution. Post-run validation is a separate call:
`validate_pilot_result(result, customer=scenario.customer_view(),
expectations=scenario.evaluation)`. It does not execute the system or repair data.

### Contracts and public turn loop

All runner contracts forbid extra fields and coercion and use frozen models and
tuple collections. Mutable ATS objects are captured immediately as JSON strings.

| Contract | Contents |
| --- | --- |
| `PublicRunRecord` | Run identity and completed alternating user/assistant pairs only |
| `ExperimentRunResult` | Identity, termination, attempted/dispatched ledgers, completed/call counts, public record, final simulator state, committed session snapshot, persistence and business observations, traces, timing and output references |
| `TurnTrace` | Proposed action/state, exact customer text, dispatch/completion flags, supplied knowledge, session snapshots, returned runtime snapshot, actual response, persistence before/after, durations and technical failure |
| `PublicStop` / `TechnicalTermination` | CS1-B public stop decision or runner/runtime failure |
| `PersistenceObservation` | ABSENT, VALID, MALFORMED or UNREADABLE; validated record snapshots/count where known, raw readable data and error |
| `BusinessTerminalObservation` | Observed workflow/result status and reason, sourced from committed session, pending execution or explicitly unavailable |
| `PilotValidationResult` / `PilotCheck` | Separate PASS/FAIL/INCONCLUSIVE verdict; named PASS/FAIL/UNKNOWN checks and evidence-reference slots |

For each step the simulator receives exactly the customer projection, its state
and prior completed public history. The exact proposed customer message enters
the attempted ledger. CS1-C then receives only that message, completed public
history and static knowledge, before runtime classification. Knowledge is passed
through `support_knowledge`; it neither selects nor overrides the runtime route.
An Order route may leave it unused.

Only at dispatch does the runner adopt the proposed simulator state and append
the message to the dispatched ledger. It calls runtime exactly once, using a
copy of the last committed session. On success it requires the returned session
to have the same conversation identity and exactly one additional pair containing
the dispatched text and actual `CustomerResponse.text`. The independently held
public transcript must equal the committed session's public projection exactly.
Only then is the returned session adopted and the pair recorded as completed.

A provider failure leaves the proposed simulator state unadopted. A runtime
failure retains the dispatch and proposed state but contributes no public pair.
No retry, resubmission, synthetic response or further simulator step follows.
Repeated identical approvals remain valid CS1-B actions. S03 classification and
pending discovery remain untouched: the enquiry-first trajectory can proceed;
an order-first trajectory that loses the enquiry may end at a public dead-end.
Introductory prose, unsupported bullets and generated nonnumeric option errors
remain diagnostic public evidence under the existing CS1-B/CS1-C limitations.

### Evidence, isolation and failures

Each run exclusively creates a new directory. Its `orders.json` is bound through
the existing `process_order_creation_message(..., order_store_path=...)` injection
inside the A3 runtime assembly. The default `data/orders.json` is rejected. No
directory is cleared or reused; failed-run stores remain available. Directory
creation/reuse errors propagate before execution, as do `prepare_pilot()` errors.
Injected factories receive this same isolated store path and must honor it.

The directory contains `manifest.json`, public-only `public_history.json`, private
`trace.jsonl`, private `result.json`, and `orders.json` if an order was written.
The manifest records scenario/fixture provenance, architecture label, the CS1-B
64-message development safeguard and existing model-setting declarations. It is
not a measurement of the installed model revision or effective sampling settings.
`result.json` references the JSONL trace instead of duplicating its turn entries;
the returned in-memory result includes all traces. JSONL also has a terminal event.

Technical phases are SETUP, PROVIDER, RUNTIME, SIMULATOR, RUNNER_INTEGRITY and
OUTPUT. Public stop reasons remain CS1-B's own reasons, including publicly
reported creation, content mismatch, denied target, uninterpretable response and
runaway safeguard. Workflow completion and persisted records are separate
observations; public creation wording is never persistence proof.

`TurnFailure` captures its phase, exception/cause and serialized `PendingTurn`
when present. The last committed session remains intact. Store observations
before/after the failed dispatch expose writes that preceded failed response
composition; pending business state is explicitly distinguished from committed
state. No `retry_pending_response` call occurs. Secondary output failures retain
the earlier termination and independently fail pilot validation. A disk failure
can leave incomplete/stale files; the returned result retains available evidence
and output errors without retrying the conversation.

Runtime snapshots contain exposed classification, routing, execution status,
BusinessResult, Support result and workflow state. Internal controller transitions,
confirmation interpretation, rejected raw model output and token counts are
explicitly unavailable, never reconstructed as observed events. Nothing in this
private trace is supplied to the simulator or knowledge provider.

### Minimal post-run validation and execution boundary

Pilot checks compare technical/public termination, completed workflow/business
outcome, exactly one independently persisted order, customer truth, stored SKU and
four stored price fields, duplicate records, completed public pairs, configuration
and final-order approval receipts, and the persisted payload against the public
artifact approved on the dispatch before the record first appeared. Base-model
price is not independently stored. Missing artifact association or unavailable
business/store evidence produces UNKNOWN; any failed check yields FAIL, otherwise
any UNKNOWN yields INCONCLUSIVE, otherwise PASS.

Scenario checks cover S01 absence of discovery, S02/S03 required observed target
offers and selections, and declared interaction expectations: preserved direct
choices, target observation before selection, actual active-workflow enquiries
and subsequent workflow continuity. These are pilot consistency checks. They do
not read or score `ScenarioSpec.evaluation.invariants`, I1–I8, aggregate paper
metrics or unexposed internal safety events. Evaluation expectations are used only
in this post-run function, outside customer/provider/ATS decision inputs.

The execution factory accepts the store path and returns a callable with the
current runtime turn signature. This keeps execution assembly outside the customer
loop; architecture labels do not change customer behavior. A1/A2 are not
implemented. The current `ConversationSession.workflow_state` still requires
`OrderCreationState`, and returned results require `TurnResult`: a future adapter
must satisfy these contracts or receive approval for a separate contract change.
This is an injection boundary, not a claim of complete architecture neutrality.

Timing uses wall-clock elapsed seconds for the run through final observation
(excluding final result/public-file serialization), each provider/runtime call,
and their totals. Counts distinguish attempts, dispatches, completed turns,
provider calls and runtime calls. These are development observations only; there
is no token accounting or paper-grade efficiency measurement.

`test_experiment_runner.py` exercises scripted S01/S02/S03 trajectories, both S03
initial routes, information boundaries, state/history semantics, isolation,
failure/pending-write retention, output failures, serialization, unchanged parser
limitations and all three pilot verdicts. Scripted runtime fixtures supply public
responses and persistence evidence; these tests do not establish real A3 pilot
success. Existing CS1-A/B/C and mocked Support/Root/runtime regressions are run
alongside them. All production code and shared business fixtures remain unchanged.

Implementation validation: 144 deterministic tests passed (23 CS1-D, 10 CS1-A,
53 CS1-B/parser, 20 CS1-C and 38 mocked Support/Root/runtime regressions). No live
model calls or real S01/S02/S03 pilots were run.
