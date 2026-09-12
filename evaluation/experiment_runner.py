"""CS1-D sequential pilot orchestration and separate post-run consistency checks.

No automatic retries or live entry point. The A3 assembly can call real models;
use injected execution for deterministic tests. Live pilots need separate approval.
"""

from copy import deepcopy
from decimal import Decimal
from functools import partial
import json
from pathlib import Path
from time import perf_counter
from typing import Annotated, Callable, Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from conversation_runtime import ConversationSession, TurnFailure, TurnResult, process_customer_message
from order_agent import process_order_creation_message
from order_creation_order_store import DEFAULT_ORDER_STORE_PATH, OrderCreationRecord
from evaluation.customer_simulator import (
    CustomerSimulatorInput, CustomerSimulatorState, CustomerTurn, MAX_CUSTOMER_MESSAGES,
    SimulatorStep, StopDecision, step,
)
from evaluation.public_observation import PublicContract, PublicMessage, observe_public_response
from evaluation.scenario_loader import REPOSITORY_ROOT, load_scenarios, validate_repository
from evaluation.scenario_spec import CustomerScenario, EvaluationExpectations, Nonblank, ScenarioSpec
from evaluation.static_product_knowledge import (
    StaticProductKnowledge, load_static_product_knowledge, provide_support_knowledge,
)

Count = Annotated[int, Field(ge=0)]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Phase = Literal['SETUP', 'PROVIDER', 'RUNTIME', 'SIMULATOR', 'RUNNER_INTEGRITY', 'OUTPUT']


class TechnicalFailure(PublicContract):
    phase: Phase
    exception_type: Nonblank
    message: str
    runtime_phase: str | None = None
    cause: str | None = None
    pending_turn_json: str | None = None


class PublicStop(PublicContract):
    kind: Literal['PUBLIC_STOP'] = 'PUBLIC_STOP'
    decision: StopDecision


class TechnicalTermination(PublicContract):
    kind: Literal['TECHNICAL_FAILURE'] = 'TECHNICAL_FAILURE'
    failure: TechnicalFailure


Termination = Annotated[PublicStop | TechnicalTermination, Field(discriminator='kind')]


class PersistenceObservation(PublicContract):
    status: Literal['ABSENT', 'VALID', 'MALFORMED', 'UNREADABLE']
    record_count: Count | None = None
    records_json: tuple[str, ...] = ()
    raw_text: str | None = None
    error: str | None = None

    @model_validator(mode='after')
    def consistent_count(self) -> Self:
        if self.status == 'VALID' and self.record_count != len(self.records_json):
            raise ValueError('Persistence record count mismatch')
        if self.status == 'ABSENT' and (self.record_count != 0 or self.records_json):
            raise ValueError('Absent persistence must have zero records')
        if self.status in ('MALFORMED', 'UNREADABLE') and (self.record_count is not None or self.records_json):
            raise ValueError('Invalid persistence has unknown count')
        return self


class BusinessTerminalObservation(PublicContract):
    source: Literal['COMMITTED_SESSION', 'PENDING_EXECUTION', 'UNAVAILABLE']
    workflow_status: str | None = None
    result_status: str | None = None
    reason: str | None = None


class PublicRunRecord(PublicContract):
    run_id: Nonblank
    public_history: tuple[PublicMessage, ...]

    @model_validator(mode='after')
    def completed_pairs(self) -> Self:
        if len(self.public_history) % 2 or any(m.role != ('user' if i % 2 == 0 else 'assistant') for i, m in enumerate(self.public_history)):
            raise ValueError('Public record requires completed user/assistant pairs')
        return self


class TurnTrace(PublicContract):
    attempt_index: Count
    customer_message: Nonblank
    customer_turn: CustomerTurn
    proposed_customer_state: CustomerSimulatorState
    dispatched: bool = False
    completed: bool = False
    knowledge_json: str | None = None
    session_before_json: str
    session_after_json: str
    runtime_result_json: str | None = None
    support_response: str | None = None
    persistence_before: PersistenceObservation
    persistence_after: PersistenceObservation
    provider_elapsed: Duration = 0.0
    runtime_elapsed: Duration = 0.0
    technical_failure: TechnicalFailure | None = None
    unavailable_evidence: tuple[str, ...] = (
        'Internal controller transitions and confirmation interpretation are not exposed.',
        'Rejected raw model output and model token counts are not exposed.',
    )


class RunTiming(PublicContract):
    full_run_elapsed: Duration
    provider_elapsed: Duration
    runtime_elapsed: Duration


class OutputReferences(PublicContract):
    run_directory: Nonblank
    manifest: Nonblank
    public_history: Nonblank
    trace: Nonblank
    result: Nonblank
    order_store: Nonblank


class ExperimentRunResult(PublicContract):
    run_id: Nonblank
    architecture: Nonblank
    scenario_id: Nonblank
    termination: Termination
    attempted_customer_messages: tuple[str, ...]
    dispatched_customer_messages: tuple[str, ...]
    completed_turns: Count
    provider_calls: Count
    runtime_calls: Count
    public_record: PublicRunRecord
    final_customer_simulator_state: CustomerSimulatorState
    last_committed_session_json: str
    persistence_observation: PersistenceObservation
    business_terminal_observation: BusinessTerminalObservation
    traces: tuple[TurnTrace, ...]
    timing: RunTiming
    outputs: OutputReferences
    secondary_failures: tuple[TechnicalFailure, ...] = ()

    @model_validator(mode='after')
    def consistent_counts(self) -> Self:
        if self.public_record.run_id != self.run_id:
            raise ValueError('Public run identity mismatch')
        if len(self.public_record.public_history) != 2 * self.completed_turns:
            raise ValueError('Completed-turn/public-history mismatch')
        if self.runtime_calls != len(self.dispatched_customer_messages):
            raise ValueError('Every dispatch requires exactly one runtime call')
        if self.completed_turns > self.runtime_calls or self.runtime_calls > len(self.attempted_customer_messages):
            raise ValueError('Invalid attempted/dispatched/completed counts')
        return self


class PilotCheck(PublicContract):
    name: Nonblank
    status: Literal['PASS', 'FAIL', 'UNKNOWN']
    explanation: Nonblank
    evidence_references: tuple[str, ...] = ()


class PilotValidationResult(PublicContract):
    verdict: Literal['PASS', 'FAIL', 'INCONCLUSIVE']
    checks: tuple[PilotCheck, ...]

    @model_validator(mode='after')
    def consistent_verdict(self) -> Self:
        if not self.checks or len({c.name for c in self.checks}) != len(self.checks):
            raise ValueError('Pilot checks must be nonempty and uniquely named')
        expected = 'FAIL' if any(c.status == 'FAIL' for c in self.checks) else 'INCONCLUSIVE' if any(c.status == 'UNKNOWN' for c in self.checks) else 'PASS'
        if self.verdict != expected:
            raise ValueError('Pilot verdict differs from checks')
        return self


def observe_persistence(path: Path) -> PersistenceObservation:
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return PersistenceObservation(status='ABSENT', record_count=0)
    except (OSError, UnicodeError) as exc:
        return PersistenceObservation(status='UNREADABLE', error=str(exc))
    try:
        payload = json.loads(raw)
        if type(payload) is not list:
            raise ValueError('Order store must be an array')
        records = tuple(OrderCreationRecord.model_validate_json(json.dumps(record)) for record in payload)
        for attribute in ('order_id', 'source_workflow_id'):
            values = [getattr(record, attribute) for record in records]
            if len(values) != len(set(values)):
                raise ValueError(f'Duplicate {attribute}')
        return PersistenceObservation(status='VALID', record_count=len(records),
                                      records_json=tuple(r.model_dump_json() for r in records), raw_text=raw)
    except (ValueError, TypeError) as exc:
        return PersistenceObservation(status='MALFORMED', raw_text=raw, error=str(exc))


def _failure(phase: Phase, exc: Exception) -> TechnicalFailure:
    pending = exc.pending_turn if isinstance(exc, TurnFailure) else None
    return TechnicalFailure(phase=phase, exception_type=type(exc).__name__, message=str(exc),
        runtime_phase=exc.phase if isinstance(exc, TurnFailure) else None,
        cause=f'{type(exc.__cause__).__name__}: {exc.__cause__}' if exc.__cause__ else None,
        pending_turn_json=pending.model_dump_json() if pending is not None else None)


def _public(session: ConversationSession) -> tuple[PublicMessage, ...]:
    return tuple(PublicMessage(role=m.role, text=m.content) for m in session.history)


def a3_execution(store_path: Path) -> Callable:
    """Existing A3 execution assembly; does not invoke anything during assembly."""
    if store_path.resolve() == DEFAULT_ORDER_STORE_PATH.resolve():
        raise ValueError('Default order store is forbidden for pilots')
    return partial(process_customer_message,
                   order_creation_processor=partial(process_order_creation_message, order_store_path=store_path))


def prepare_pilot(directory=None) -> tuple[tuple[ScenarioSpec, ...], StaticProductKnowledge]:
    """Validate the frozen three-scenario setup; load shared knowledge once."""
    scenarios = load_scenarios() if directory is None else load_scenarios(directory)
    if tuple(s.scenario_id for s in scenarios) != ('S01', 'S02', 'S03'):
        raise ValueError('Initial pilot setup requires S01/S02/S03')
    reference = scenarios[0].fixtures.product_catalog
    if any(s.fixtures.product_catalog != reference for s in scenarios):
        raise ValueError('Pilot scenarios must share catalog provenance')
    knowledge = load_static_product_knowledge(REPOSITORY_ROOT / reference.path, expected_sha256=reference.sha256)
    return scenarios, knowledge


def _write_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def _append_trace(path: Path, trace: TurnTrace) -> None:
    with path.open('a', encoding='utf-8') as stream:
        stream.write(trace.model_dump_json() + '\n')
        stream.flush()


def _business(session: ConversationSession, traces: list[TurnTrace]) -> BusinessTerminalObservation:
    for trace in reversed(traces):
        failure = trace.technical_failure
        if failure and failure.pending_turn_json:
            execution = json.loads(failure.pending_turn_json)['execution']
            state, business = execution.get('state'), execution.get('business_result')
            return BusinessTerminalObservation(source='PENDING_EXECUTION', workflow_status=state.get('status') if state else None,
                result_status=business.get('result_status') if business else None, reason=business.get('reason') if business else None)
    state = session.workflow_state
    business = None
    for trace in reversed(traces):
        if trace.completed and trace.runtime_result_json:
            business = json.loads(trace.runtime_result_json)['execution'].get('business_result')
            if business:
                break
    return BusinessTerminalObservation(source='COMMITTED_SESSION' if state else 'UNAVAILABLE',
        workflow_status=state.status.value if state else None,
        result_status=business.get('result_status') if business else None, reason=business.get('reason') if business else None)


def run_scenario(
    scenario: ScenarioSpec, *, run_directory: str | Path,
    knowledge: StaticProductKnowledge | None = None,
    architecture: str = 'A3', execution_factory: Callable[[Path], Callable] | None = None,
    simulator: Callable = step, knowledge_provider: Callable = provide_support_knowledge,
) -> ExperimentRunResult:
    """Run one isolated trajectory, with no retries and no post-run scoring.

    The default execution can call live A3 models; tests must inject execution.
    Existing directories and default-store paths are rejected before any write.
    Setup preparation errors outside this call propagate without a run directory.
    """
    directory = Path(run_directory).resolve()
    store = directory / 'orders.json'
    if store.resolve() == DEFAULT_ORDER_STORE_PATH.resolve():
        raise ValueError('Default order store is forbidden')
    directory.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    run_id = directory.name + '-' + uuid4().hex[:12]
    outputs = OutputReferences(run_directory=str(directory), manifest=str(directory/'manifest.json'),
        public_history=str(directory/'public_history.json'), trace=str(directory/'trace.jsonl'),
        result=str(directory/'result.json'), order_store=str(store))
    session = ConversationSession(conversation_id=run_id)
    state = CustomerSimulatorState()
    history = ()
    attempted, dispatched, traces = [], [], []
    provider_calls = runtime_calls = 0
    termination = None
    secondary = []
    phase: Phase = 'SETUP'
    try:
        scenario = ScenarioSpec.model_validate(scenario)
        if scenario.scenario_id not in ('S01', 'S02', 'S03'):
            raise ValueError('Only S01/S02/S03 are in the initial pilot')
        validate_repository(scenario)
        if knowledge is None:
            reference = scenario.fixtures.product_catalog
            knowledge = load_static_product_knowledge(REPOSITORY_ROOT/reference.path, expected_sha256=reference.sha256)
        knowledge = StaticProductKnowledge.model_validate(knowledge)
        if knowledge.source_sha256 != scenario.fixtures.product_catalog.sha256:
            raise ValueError('Static knowledge/scenario provenance mismatch')
        if execution_factory is None and architecture != 'A3':
            raise ValueError('Only A3 has an execution assembly; other labels require an injected test boundary')
        execute = (execution_factory or a3_execution)(store)
        customer = scenario.customer_view()
        phase = 'OUTPUT'
        _write_json(Path(outputs.manifest), dict(run_id=run_id, scenario_id=scenario.scenario_id,
            architecture=architecture, fixtures=scenario.fixtures.model_dump(mode='json'),
            development_customer_message_safeguard=MAX_CUSTOMER_MESSAGES,
            measurement_scope='development observations; no token accounting',
            support_model='qwen3:8b', think=False, sampling_options='existing component defaults'))
        while termination is None:
            phase = 'SIMULATOR'
            proposal = simulator(CustomerSimulatorInput(customer=customer, state=state, public_history=history))
            # Validate the returned contract before adopting any state or message.
            proposal = SimulatorStep.model_validate(proposal)
            if isinstance(proposal.decision, StopDecision):
                state = proposal.state
                termination = PublicStop(decision=proposal.decision)
                break
            decision = proposal.decision
            attempted.append(decision.message)
            before = session.model_dump_json()
            before_store = observe_persistence(store)
            trace_values = dict(attempt_index=len(attempted)-1, customer_message=decision.message,
                customer_turn=decision, proposed_customer_state=proposal.state,
                session_before_json=before, session_after_json=before,
                persistence_before=before_store, persistence_after=before_store)
            provider_elapsed = runtime_elapsed = 0.0
            try:
                phase = 'PROVIDER'
                provider_calls += 1
                start = perf_counter()
                try:
                    supplied = knowledge_provider(decision.message, history, knowledge=knowledge)
                finally:
                    provider_elapsed = perf_counter()-start
                trace_values['knowledge_json'] = supplied.model_dump_json() if supplied is not None else None
                phase = 'RUNTIME'
                dispatched.append(decision.message)
                runtime_calls += 1
                state = proposal.state
                trace_values['dispatched'] = True
                start = perf_counter()
                try:
                    result = execute(deepcopy(session), decision.message, support_knowledge=supplied)
                finally:
                    runtime_elapsed = perf_counter()-start
                phase = 'RUNNER_INTEGRITY'
                result = TurnResult.model_validate(result)
                trace_values['runtime_result_json'] = result.model_dump_json()
                trace_values['support_response'] = result.customer_response.text
                expected = history + (PublicMessage(role='user', text=decision.message),
                                      PublicMessage(role='assistant', text=result.customer_response.text))
                if result.session.conversation_id != session.conversation_id or _public(result.session) != expected:
                    raise ValueError('Runtime public history differs from exactly one accepted pair')
                session = deepcopy(result.session)
                history = expected
                trace_values['completed'] = True
                trace_values['session_after_json'] = session.model_dump_json()
            except Exception as exc:
                failure = _failure(phase, exc)
                trace_values['technical_failure'] = failure
                termination = TechnicalTermination(failure=failure)
            finally:
                trace_values.update(provider_elapsed=provider_elapsed, runtime_elapsed=runtime_elapsed,
                                    persistence_after=observe_persistence(store))
                trace = TurnTrace(**trace_values)
                traces.append(trace)
            phase = 'OUTPUT'
            _append_trace(Path(outputs.trace), trace)
    except Exception as exc:
        failure = _failure(phase, exc)
        if termination is not None:
            secondary.append(failure)
        else:
            termination = TechnicalTermination(failure=failure)
    result = ExperimentRunResult(run_id=run_id, architecture=architecture, scenario_id=scenario.scenario_id,
        termination=termination, attempted_customer_messages=tuple(attempted), dispatched_customer_messages=tuple(dispatched),
        completed_turns=len(history)//2, provider_calls=provider_calls, runtime_calls=runtime_calls,
        public_record=PublicRunRecord(run_id=run_id, public_history=history), final_customer_simulator_state=state,
        last_committed_session_json=session.model_dump_json(), persistence_observation=observe_persistence(store),
        business_terminal_observation=_business(session, traces), traces=tuple(traces),
        timing=RunTiming(full_run_elapsed=perf_counter()-started,
            provider_elapsed=sum(t.provider_elapsed for t in traces), runtime_elapsed=sum(t.runtime_elapsed for t in traces)),
        outputs=outputs, secondary_failures=tuple(secondary))
    try:
        _write_json(Path(outputs.public_history), result.public_record.model_dump(mode='json'))
        _write_json(Path(outputs.result), result.model_dump(mode='json', exclude={'traces'}))
        # A terminal event also exists for zero-turn public stops/setup failures.
        with Path(outputs.trace).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'terminal': result.termination.model_dump(mode='json')}, ensure_ascii=False)+'\n')
    except Exception as exc:
        failure = _failure('OUTPUT', exc)
        result = ExperimentRunResult(**{**{k:getattr(result,k) for k in type(result).model_fields},
            'secondary_failures': (*result.secondary_failures, failure)})
    return result


def validate_pilot_result(
    result: ExperimentRunResult, *, customer: CustomerScenario, expectations: EvaluationExpectations,
) -> PilotValidationResult:
    """Post-run pilot consistency only. Never reads or scores paper invariants."""
    checks = []
    def check(name, condition, explanation, refs=()):
        checks.append(PilotCheck(name=name, status='UNKNOWN' if condition is None else 'PASS' if condition else 'FAIL',
                                 explanation=explanation, evidence_references=refs))
    check('no_technical_failure', isinstance(result.termination, PublicStop) and not result.secondary_failures,
          'Technical failures are separate from public/customer termination.')
    check('public_order_created', isinstance(result.termination, PublicStop) and result.termination.decision.reason == 'ORDER_CREATED_PUBLICLY_REPORTED',
          'Creation must be publicly reported and observed by CS1-B.')
    business = result.business_terminal_observation
    check('workflow_completed', business.workflow_status == expectations.outcome.workflow_status if business.workflow_status else None,
          'Observed workflow status compared with expected outcome.')
    check('business_outcome', (business.result_status == expectations.outcome.result_status and business.reason == expectations.outcome.reason)
          if business.result_status else None, 'Observed business outcome compared with expected outcome.')
    persistence = result.persistence_observation
    check('one_persisted_order', persistence.record_count == expectations.outcome.committed_order_count if persistence.record_count is not None else None,
          'Independent isolated-store count; malformed/unreadable stores have unknown counts.')
    records = [json.loads(r) for r in persistence.records_json]
    order = records[0]['order'] if len(records) == 1 else None
    intended = None
    pricing = None
    if order is not None:
        truth = customer.ground_truth
        intended = all(order.get(k) == v for k,v in truth.customer.model_dump().items()) and order.get('delivery_address') == truth.delivery_address.model_dump()
        intended = intended and order.get('room_size') == truth.room_size and all(order.get(k) == v for k,v in truth.configuration.model_dump().items())
        derived = expectations.system_derived
        pricing = order.get('product_sku') == derived.product_sku and all(
            Decimal(str(order[k])) == Decimal(getattr(derived.pricing,k)) for k in ('customisation_price','unit_price','shipping_cost','total_price'))
    check('persisted_customer_truth', intended, 'Compare identity/contact/address/configuration/quantity/room and optional facts.')
    check('persisted_system_derived', pricing, 'Compare stored SKU and four money fields; base-model price is not independently stored.')
    check('no_duplicate_records', len({r['order_id'] for r in records}) == len(records) and len({r['source_workflow_id'] for r in records}) == len(records)
          if persistence.status == 'VALID' else None, 'Checks recorded duplicates, not paper at-most-once side-effect scoring.')
    completed = [t for t in result.traces if t.completed]
    expected_public = tuple(m for t in completed for m in (PublicMessage(role='user',text=t.customer_message),PublicMessage(role='assistant',text=t.support_response)))
    committed = ConversationSession.model_validate_json(result.last_committed_session_json)
    check('public_pairs_exactly_once', expected_public == result.public_record.public_history == _public(committed),
          'Completed public pairs and committed runtime projection agree exactly.')
    artifact_matches = None
    if order is not None:
        first = next((t for t in result.traces if records[0]['order_id'] in [json.loads(r)['order_id'] for r in t.persistence_after.records_json]
                      and records[0]['order_id'] not in [json.loads(r)['order_id'] for r in t.persistence_before.records_json]), None)
        if first:
            approvals = [a for a in first.customer_turn.actions if a.kind == 'CONFIRM_FINAL_ORDER']
            if len(approvals) == 1:
                ref = approvals[0].artifact
                try:
                    ref.resolve(result.public_record.public_history)
                    artifact = observe_public_response(result.public_record.public_history[ref.message_index].text,ref.message_index).artifact
                    if artifact and artifact.evidence == ref and artifact.approval_request:
                        artifact_matches = True
                        for item in artifact.values:
                            actual = order['delivery_address'].get(item.field.split('.')[1]) if item.field.startswith('delivery_address.') else order.get(item.field)
                            if item.field in ('customisation_price','unit_price','shipping_cost','total_price'):
                                artifact_matches &= Decimal(str(actual)) == Decimal(item.value)
                            else:
                                artifact_matches &= str(actual) == item.value
                        for optional in ('company_name','customer_instructions'):
                            if optional not in {v.field for v in artifact.values}:
                                artifact_matches &= order.get(optional) is None
                except (ValueError, IndexError, KeyError):
                    artifact_matches = None
    check('persisted_matches_public_approval', artifact_matches,
          'Compare displayed fields in the final approval dispatched before the order first appeared; unavailable association is UNKNOWN.')
    receipts = result.final_customer_simulator_state.approval_receipts
    check('configuration_approved', any(r.kind == 'CONFIGURATION' for r in receipts), 'Customer-side configuration approval receipt exists.')
    check('final_order_approved', any(r.kind == 'FINAL_ORDER' for r in receipts), 'Customer-side placement approval receipt exists.')
    actions = [a for t in result.traces if t.dispatched for a in t.customer_turn.actions]
    discovery = customer.conversation_policy.configuration_selection
    if discovery is None:
        check('no_catalog_discovery', not any(a.kind in ('ASK_AVAILABLE_OPTIONS','ASK_ABOUT_TARGET_OPTION') for a in actions), 'S01 does not use discovery actions.')
    else:
        offered = {o.field for o in result.final_customer_simulator_state.observed_target_offers}
        selected = set(result.final_customer_simulator_state.disclosed_fields)
        check('discovery_supported', set(discovery.discovery_fields) <= offered & selected, 'Each required discovery field has public offer evidence and customer selection.')
    properties = expectations.interaction_properties
    if properties:
        if properties.direct_selection_preserved:
            check('direct_selection_preserved', intended, 'Final intended configuration preserves initial direct choices.')
        if properties.target_selected_only_after_observation:
            evidence_ordered = True
            for trace in result.traces:
                for action in trace.customer_turn.actions:
                    if action.kind == 'SELECT_OPTION' and action.field not in customer.initially_known_configuration_fields:
                        offers = [o for o in trace.proposed_customer_state.observed_target_offers if o.field == action.field]
                        prior = _public(ConversationSession.model_validate_json(trace.session_before_json))
                        try:
                            if not offers: evidence_ordered = False
                            for offer in offers: offer.evidence.resolve(prior)
                        except ValueError: evidence_ordered = False
            check('target_selected_after_public_observation', evidence_ordered, 'Selection actions refer to prior public target-offer evidence.')
        enquiries = []
        for index, trace in enumerate(result.traces):
            if trace.completed and trace.runtime_result_json:
                runtime = json.loads(trace.runtime_result_json)
                before = json.loads(trace.session_before_json).get('workflow_state')
                if runtime['classification']['category'] == 'GENERAL_ENQUIRY' and before and before['status'] in ('ACTIVE','AWAITING_USER_INPUT'):
                    enquiries.append((index, before, runtime))
        if properties.active_workflow_general_enquiry_supported:
            check('active_workflow_enquiry', bool(enquiries), 'An actual GENERAL_ENQUIRY occurred during an active workflow.')
        if properties.workflow_continuity_after_enquiry:
            continued = bool(enquiries)
            for index,before,runtime in enquiries:
                after = runtime['session']['workflow_state']
                same = after == before
                resumed = any(t.completed and t.runtime_result_json and
                    (json.loads(t.runtime_result_json)['execution'].get('business_result') or {}).get('workflow_id') == before['workflow_id']
                    for t in result.traces[index+1:])
                continued &= same and resumed
            check('workflow_continuity', continued, 'Enquiry preserved workflow state and later business execution resumed the same workflow.')
    verdict = 'FAIL' if any(c.status == 'FAIL' for c in checks) else 'INCONCLUSIVE' if any(c.status == 'UNKNOWN' for c in checks) else 'PASS'
    return PilotValidationResult(verdict=verdict, checks=tuple(checks))
