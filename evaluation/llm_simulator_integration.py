"""CS2-only recording adapter and independent public approval audit.

No live client is imported or assembled here. Provenance is operator-supplied;
input hashes are fingerprints, not evidence of information-boundary security.
"""
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from evaluation import llm_customer_simulator as cs2
from evaluation import llm_public_evidence as evidence
from evaluation.customer_simulator import (
    ApprovalReceipt, ConfirmConfiguration, ConfirmFinalOrder,
    SelectOption, StopDecision, customer_value,
)
from evaluation.public_observation import PublicArtifact, PublicContract, PublicMessage
from evaluation.scenario_spec import CONFIGURATION_FIELDS, CustomerScenario, Nonblank

Count = Annotated[int, Field(ge=0)]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
FailureCode = Literal['INVALID_INPUT', 'MODEL_FAILURE', 'INVALID_PROPOSAL', 'GUARD_REJECTED', 'UNEXPECTED_EXCEPTION']


class SimulatorMetadata(PublicContract):
    kind: Literal['CS2'] = 'CS2'
    provenance: Literal['MOCK', 'LIVE_UNVERIFIED', 'LIVE_VERIFIED']
    source_commit: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')] | None = None
    model: Literal['qwen3:8b'] = 'qwen3:8b'
    model_digest: Nonblank | None = None
    think: Literal[False] = False
    stream: Literal[False] = False
    temperature: Literal[0] = 0
    seed: Literal[0] = 0
    prompt_sha256: Digest
    proposal_schema_sha256: Digest
    guard_source_sha256: Digest
    evidence_source_sha256: Digest
    renderer_source_sha256: Digest
    trace_schema_version: Literal['CS2-1'] = 'CS2-1'
    ollama_client_version: Nonblank | None = None
    ollama_server_version: Nonblank | None = None

    @model_validator(mode='after')
    def provenance_consistent(self):
        if self.provenance == 'MOCK' and self.model_digest is not None:
            raise ValueError('Mock provenance must not claim a real model digest')
        if self.provenance == 'LIVE_VERIFIED' and any(v is None for v in (
            self.source_commit, self.model_digest, self.ollama_client_version, self.ollama_server_version,
        )):
            raise ValueError('Verified provenance requires actual revision/version identifiers')
        return self


def make_simulator_metadata(*, provenance, source_commit=None, model_digest=None,
                            ollama_client_version=None, ollama_server_version=None):
    """Hash local implementation bytes; never query Ollama or invent provenance."""
    directory = Path(__file__).parent
    def digest(raw):
        return sha256(raw).hexdigest()
    schema = json.dumps(cs2.ProposalEnvelope.model_json_schema(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return SimulatorMetadata(provenance=provenance, source_commit=source_commit, model_digest=model_digest,
        prompt_sha256=digest(cs2.SYSTEM_PROMPT.encode()), proposal_schema_sha256=digest(schema.encode()),
        guard_source_sha256=digest((directory/'llm_customer_simulator.py').read_bytes()),
        evidence_source_sha256=digest((directory/'llm_public_evidence.py').read_bytes()),
        renderer_source_sha256=digest((directory/'customer_simulator.py').read_bytes()),
        ollama_client_version=ollama_client_version, ollama_server_version=ollama_server_version)


class SimulatorFailure(PublicContract):
    code: FailureCode
    exception_type: Nonblank
    message: str


class SimulatorDiagnostics(PublicContract):
    input_sha256: Digest | None = None
    model_calls: Annotated[int, Field(ge=0, le=1)] = 0
    model_elapsed: Duration | None = None
    raw_model_content: str | None = None
    parsed_proposal_json: str | None = None
    guard_outcome: Literal['NOT_REACHED', 'ACCEPTED', 'REJECTED', 'BYPASSED']
    failure: SimulatorFailure | None = None

    @field_validator('parsed_proposal_json')
    @classmethod
    def serialized_object(cls, value):
        if value is not None and type(json.loads(value)) is not dict:
            raise ValueError('Parsed proposal snapshot must be a JSON object')
        return value


class SimulatorAttemptTrace(SimulatorDiagnostics):
    attempt_index: Count
    input_history_length: Count
    elapsed: Duration
    outcome: Literal['CUSTOMER_TURN', 'PUBLIC_STOP', 'FAILURE']
    rendered_customer_message: Nonblank | None = None
    stop_decision: StopDecision | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.input_history_length % 2:
            raise ValueError('Runner attempts consume completed public pairs')
        if (self.model_calls == 0) != (self.model_elapsed is None):
            raise ValueError('Model timing must match actual call presence')
        if self.model_calls == 0 and (self.raw_model_content is not None or self.parsed_proposal_json is not None):
            raise ValueError('A bypass cannot contain model output')
        if self.outcome == 'CUSTOMER_TURN':
            if self.rendered_customer_message is None or self.stop_decision or self.failure:
                raise ValueError('Customer attempt requires only rendered message')
        elif self.outcome == 'PUBLIC_STOP':
            if self.stop_decision is None or self.rendered_customer_message or self.failure:
                raise ValueError('Public stop requires only verified decision')
        elif self.failure is None or self.rendered_customer_message or self.stop_decision:
            raise ValueError('Failed attempt cannot have an accepted output')
        if self.guard_outcome == 'REJECTED' and (self.failure is None or self.failure.code != 'GUARD_REJECTED'):
            raise ValueError('Rejected verdict requires guard failure')
        if self.guard_outcome in ('ACCEPTED', 'REJECTED') and self.parsed_proposal_json is None:
            raise ValueError('Guard verdict requires actual parsed proposal')
        if self.guard_outcome == 'BYPASSED' and (self.model_calls or self.failure):
            raise ValueError('Only successful non-model decisions bypass guards')
        return self


class SimulatorSummary(PublicContract):
    kind: Literal['CS2'] = 'CS2'
    attempt_count: Count
    model_call_count: Count
    elapsed: Duration
    model_elapsed: Duration
    failed_attempt_index: Count | None = None
    failure_code: FailureCode | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.model_call_count > self.attempt_count:
            raise ValueError('More model calls than attempts')
        if (self.failed_attempt_index is None) != (self.failure_code is None):
            raise ValueError('Failure index/code must be paired')
        if self.failed_attempt_index is not None and self.failed_attempt_index >= self.attempt_count:
            raise ValueError('Failure refers to absent attempt')
        return self


def summarize_attempts(attempts):
    failed = next((a for a in reversed(attempts) if a.failure), None)
    return SimulatorSummary(attempt_count=len(attempts), model_call_count=sum(a.model_calls for a in attempts),
        elapsed=sum((a.elapsed for a in attempts), 0.0), model_elapsed=sum((a.model_elapsed or 0.0 for a in attempts), 0.0),
        failed_attempt_index=failed.attempt_index if failed else None, failure_code=failed.failure.code if failed else None)


class LLMSimulator:
    """One-run sequential callable with consumable, immutable diagnostics."""
    def __init__(self, chat_fn):
        self._chat_fn = chat_fn
        self._last = None

    def __call__(self, inputs):
        self._last = None
        capture = cs2.ProposalDiagnostics()
        failure = None
        try:
            return cs2.step(inputs, chat_fn=self._chat_fn, diagnostics=capture)
        except Exception as exc:
            code = exc.code if isinstance(exc, cs2.CS2Failure) else 'UNEXPECTED_EXCEPTION'
            failure = SimulatorFailure(code=code, exception_type=type(exc).__name__, message=str(exc))
            raise
        finally:
            guard = ('REJECTED' if failure and failure.code == 'GUARD_REJECTED' else 'NOT_REACHED' if failure
                     else 'ACCEPTED' if capture.parsed_proposal is not None else 'BYPASSED')
            self._last = SimulatorDiagnostics(input_sha256=capture.input_sha256, model_calls=capture.model_calls,
                model_elapsed=capture.model_elapsed, raw_model_content=capture.raw_model_content,
                parsed_proposal_json=capture.parsed_proposal.model_dump_json() if capture.parsed_proposal is not None else None,
                guard_outcome=guard, failure=failure)

    def take_diagnostics(self):
        if self._last is None:
            raise ValueError('No unconsumed simulator diagnostics')
        result, self._last = self._last, None
        return result


def make_llm_simulator(*, chat_fn):
    return LLMSimulator(chat_fn)


class VerifiedApproval(PublicContract):
    receipt: ApprovalReceipt
    artifact: PublicArtifact


class PublicApprovalAudit(PublicContract):
    approvals: tuple[VerifiedApproval, ...] = ()
    errors: tuple[Nonblank, ...] = ()
    incomplete: bool = False


def _customer_match(customer, artifact):
    fields = list(CONFIGURATION_FIELDS) + ['quantity']
    if artifact.kind == 'FINAL_ORDER':
        fields += ['customer_name', 'phone', 'email', 'company_name', 'customer_instructions', 'room_size',
                   'delivery_address.address', 'delivery_address.city', 'delivery_address.state',
                   'delivery_address.postcode', 'delivery_address.country']
    values = {v.field: v.value for v in artifact.values}
    return all(values.get(f) == (None if customer_value(customer, f) is None else str(customer_value(customer, f))) for f in fields)


def verify_cs2_public_approvals(public_history: tuple[PublicMessage, ...], *, customer: CustomerScenario) -> PublicApprovalAudit:
    """Reconstruct from public text + customer truth, never saved receipts/ATS.

    Current conversation-model assumption: approval is the immediately following
    user message in the completed alternating transcript, not a permanent policy.
    """
    if type(customer) is not CustomerScenario or type(public_history) is not tuple or any(type(m) is not PublicMessage for m in public_history):
        raise ValueError('Expected exact customer/public history contracts')
    customer = CustomerScenario.model_validate_json(customer.model_dump_json())
    history = tuple(PublicMessage.model_validate(m) for m in public_history)
    if len(history) % 2 or any(m.role != ('user' if i % 2 == 0 else 'assistant') for i, m in enumerate(history)):
        raise ValueError('Audit requires completed alternating public pairs')
    if history and history[0].text != customer.initial_message:
        return PublicApprovalAudit(errors=('INITIAL_MESSAGE_MISMATCH',))
    disclosed = set(customer.initial_disclosures) if history else set()
    approved, errors, incomplete = [], [], False
    approval_indices = set()
    for i, message in enumerate(history):
        if message.role == 'user':
            if i:
                for field in CONFIGURATION_FIELDS:
                    rendered = cs2.render_authorized((SelectOption(field=field),), customer)
                    if rendered in message.text.split('\n'):
                        disclosed.add(field)
                confirmation_texts = ('Yes, that configuration is correct.', 'Yes, I confirm the final order and would like to place it.')
                if message.text in confirmation_texts and i not in approval_indices:
                    errors.append(f'UNSUPPORTED_CUSTOMER_APPROVAL:{i}')
            continue
        artifact = evidence.extract_artifact(message.text, i)
        if artifact is None:
            if 'Configuration Summary' in message.text or 'Provisional Order' in message.text:
                errors.append(f'MALFORMED_ARTIFACT:{i}')
            continue
        if i + 1 >= len(history):
            incomplete = True
            continue
        try:
            artifact.evidence.resolve(history)
            requests = evidence.approval_requests(message.text, i, artifact)
            if len(requests) != 1:
                raise ValueError('MISSING_OR_AMBIGUOUS_REQUEST')
            request = requests[0]
            request.resolve(history)
            evidence.assert_framing_safe(message.text, i, artifact, request)
            if not _customer_match(customer, artifact):
                raise ValueError('CUSTOMER_FACT_MISMATCH')
            if not set(CONFIGURATION_FIELDS) <= disclosed:
                raise ValueError('MISSING_PRIOR_DISCLOSURES')
            final = artifact.kind == 'FINAL_ORDER'
            if final and not any(a.receipt.kind == 'CONFIGURATION' for a in approved):
                raise ValueError('MISSING_PRIOR_CONFIGURATION_APPROVAL')
            cls = ConfirmFinalOrder if final else ConfirmConfiguration
            expected = cs2.render_authorized((cls(artifact=artifact.evidence),), customer)
            if history[i + 1].text != expected:
                raise ValueError('WRONG_FOLLOWING_CUSTOMER_CONFIRMATION')
            receipt = ApprovalReceipt(kind=artifact.kind, artifact=artifact.evidence, approval_request=request,
                customer_message_index=i + 1, repeated=any(a.receipt.kind == artifact.kind and a.artifact.evidence.quote == artifact.evidence.quote for a in approved))
            approved.append(VerifiedApproval(receipt=receipt, artifact=PublicArtifact(
                kind=artifact.kind, values=artifact.values, evidence=artifact.evidence, approval_request=request)))
            approval_indices.add(i + 1)
        except (ValueError, IndexError) as exc:
            errors.append(f'INVALID_APPROVAL:{i}:{exc}')
    return PublicApprovalAudit(approvals=tuple(approved), errors=tuple(errors), incomplete=incomplete)
