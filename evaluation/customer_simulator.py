"""CS1 deterministic customer policy. Pure public-input -> proposed-output steps.

MAX_CUSTOMER_MESSAGES is a DEVELOPMENT RUNAWAY SAFEGUARD, NOT the frozen
experiment interaction budget. A formal common budget follows later pilot runs.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from evaluation.scenario_spec import CONFIGURATION_FIELDS, ConfigurationField, CustomerScenario, Nonblank
from evaluation.public_observation import (
    CustomerField, EvidenceRef, FIELD_LABELS, InformationField, NonnegativeInt,
    PublicArtifact, PublicContract, PublicMessage, RequestedFieldEvidence,
    initial_discovery_field, observe_public_response,
)

MAX_CUSTOMER_MESSAGES = 64
StopReason = Literal[
    "ORDER_CREATED_PUBLICLY_REPORTED", "PUBLIC_CONTENT_MISMATCH",
    "SIMULATOR_UNINTERPRETABLE_RESPONSE", "TARGET_OPTION_DENIED",
    "TARGET_OPTION_NOT_RESOLVED", "PUBLIC_UNAVAILABLE_RESPONSE", "RUNAWAY_LIMIT_REACHED",
]


class StopDecision(PublicContract):
    kind: Literal["STOP"] = "STOP"
    reason: StopReason
    evidence: tuple[EvidenceRef, ...] = ()
    field: ConfigurationField | None = None

    @model_validator(mode="after")
    def required_public_evidence(self) -> Self:
        if self.reason != 'RUNAWAY_LIMIT_REACHED' and not self.evidence:
            raise ValueError("Public stop reasons require public evidence")
        if self.reason in ('TARGET_OPTION_DENIED', 'TARGET_OPTION_NOT_RESOLVED') and self.field is None:
            raise ValueError("Target stop reasons require a configuration field")
        return self


class TargetOfferEvidence(PublicContract):
    field: ConfigurationField
    evidence: EvidenceRef


class PendingDiscovery(PublicContract):
    field: ConfigurationField
    question_kind: Literal["AVAILABLE_OPTIONS", "TARGET_OPTION"]
    customer_message_index: NonnegativeInt


class ApprovalReceipt(PublicContract):
    kind: Literal["CONFIGURATION", "FINAL_ORDER"]
    artifact: EvidenceRef
    approval_request: EvidenceRef
    customer_message_index: NonnegativeInt
    repeated: bool


class CustomerSimulatorState(PublicContract):
    turn_index: NonnegativeInt = 0
    disclosed_fields: tuple[CustomerField, ...] = ()
    observed_target_offers: tuple[TargetOfferEvidence, ...] = ()
    pending_requests: tuple[RequestedFieldEvidence, ...] = ()
    pending_discovery: PendingDiscovery | None = None
    approval_receipts: tuple[ApprovalReceipt, ...] = ()
    stop_decision: StopDecision | None = None

    @model_validator(mode="after")
    def distinct_customer_records(self) -> Self:
        for values in (self.disclosed_fields, tuple(r.field for r in self.pending_requests),
                       tuple(o.field for o in self.observed_target_offers)):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate customer-state field")
        if self.turn_index == 0 and any((self.disclosed_fields, self.observed_target_offers,
                                        self.pending_requests, self.pending_discovery, self.approval_receipts)):
            raise ValueError("Initial customer state cannot contain prior actions/evidence")
        return self


class InitialMessage(PublicContract):
    kind: Literal["INITIAL_MESSAGE"] = "INITIAL_MESSAGE"


class ProvideInformation(PublicContract):
    kind: Literal["PROVIDE_INFORMATION"] = "PROVIDE_INFORMATION"
    fields: tuple[InformationField, ...]

    @model_validator(mode="after")
    def distinct_fields(self) -> Self:
        if not self.fields or len(self.fields) != len(set(self.fields)):
            raise ValueError("Information fields must be nonempty and unique")
        return self


class AskAvailableOptions(PublicContract):
    kind: Literal["ASK_AVAILABLE_OPTIONS"] = "ASK_AVAILABLE_OPTIONS"
    field: ConfigurationField


class AskAboutTargetOption(PublicContract):
    kind: Literal["ASK_ABOUT_TARGET_OPTION"] = "ASK_ABOUT_TARGET_OPTION"
    field: ConfigurationField


class SelectOption(PublicContract):
    kind: Literal["SELECT_OPTION"] = "SELECT_OPTION"
    field: ConfigurationField


class ConfirmConfiguration(PublicContract):
    kind: Literal["CONFIRM_CONFIGURATION"] = "CONFIRM_CONFIGURATION"
    artifact: EvidenceRef


class ConfirmFinalOrder(PublicContract):
    kind: Literal["CONFIRM_FINAL_ORDER"] = "CONFIRM_FINAL_ORDER"
    artifact: EvidenceRef


CustomerAction = Annotated[
    InitialMessage | ProvideInformation | AskAvailableOptions | AskAboutTargetOption |
    SelectOption | ConfirmConfiguration | ConfirmFinalOrder,
    Field(discriminator="kind"),
]


class CustomerTurn(PublicContract):
    kind: Literal["CUSTOMER_TURN"] = "CUSTOMER_TURN"
    actions: tuple[CustomerAction, ...]
    message: Nonblank

    @model_validator(mode="after")
    def legal_bundle(self) -> Self:
        if not self.actions:
            raise ValueError("Empty customer action bundle")
        if len(self.actions) > 1 and any(isinstance(a, (InitialMessage, ConfirmConfiguration, ConfirmFinalOrder)) for a in self.actions):
            raise ValueError("Initial/confirmation actions must occur alone")
        if sum(isinstance(a, (AskAvailableOptions, AskAboutTargetOption)) for a in self.actions) > 1:
            raise ValueError("At most one discovery question per customer message")
        return self


class CustomerSimulatorInput(PublicContract):
    customer: CustomerScenario
    state: CustomerSimulatorState
    public_history: tuple[PublicMessage, ...]

    @property
    def latest_support_response(self) -> PublicMessage | None:
        return self.public_history[-1] if self.public_history and self.public_history[-1].role == "assistant" else None


class SimulatorStep(PublicContract):
    decision: Annotated[CustomerTurn | StopDecision, Field(discriminator="kind")]
    state: CustomerSimulatorState


def customer_value(customer: CustomerScenario, field: CustomerField) -> str | int | None:
    """One typed source of truth; actions never carry independent fact values."""
    truth = customer.ground_truth
    if field.startswith('delivery_address.'):
        return getattr(truth.delivery_address, field.split('.')[1])
    if field in (*CONFIGURATION_FIELDS, 'quantity'):
        return getattr(truth.configuration, field)
    if field == 'room_size':
        return truth.room_size
    return getattr(truth.customer, field)


def _question(field: ConfigurationField, customer: CustomerScenario, target: bool) -> str:
    label = FIELD_LABELS[field]
    return (f"Is {customer_value(customer, field)} available for {label}?" if target
            else f"What {label} options are available?")


def _public_question_context(text: str) -> ConfigurationField | None:
    initial = initial_discovery_field(text)
    if initial:
        return initial
    for field in CONFIGURATION_FIELDS:
        label = re.escape(FIELD_LABELS[field])
        if re.search(rf'(?:^|\n)(?:What {label} options are available\?|Is [^\n?]+ available for {label}\?)$', text):
            return field
    return None


def _observe_at(history: tuple[PublicMessage, ...], index: int):
    context = _public_question_context(history[index - 1].text) if index and history[index - 1].role == 'user' else None
    return observe_public_response(history[index].text, index, context_field=context)


def _validate_evidence(customer: CustomerScenario, state: CustomerSimulatorState, history: tuple[PublicMessage, ...]) -> None:
    for offer in state.observed_target_offers:
        offer.evidence.resolve(history)
        observation = _observe_at(history, offer.evidence.message_index)
        if not any(a.field == offer.field and a.status == 'OFFERED' and
                   customer_value(customer, offer.field) in a.values and a.evidence == offer.evidence
                   for a in observation.availability):
            raise ValueError("Stored target offer lacks positive field-specific public evidence")
    for request in state.pending_requests:
        request.evidence.resolve(history)
        observation = _observe_at(history, request.evidence.message_index)
        if request not in observation.requests:
            raise ValueError("Pending request was not publicly requested")
    pending = state.pending_discovery
    if pending:
        if pending.customer_message_index >= len(history):
            raise ValueError("Pending discovery question is absent from history")
        message = history[pending.customer_message_index]
        expected = _question(pending.field, customer, pending.question_kind == 'TARGET_OPTION')
        initial = (pending.question_kind == 'AVAILABLE_OPTIONS' and message.text == customer.initial_message
                   and initial_discovery_field(message.text) == pending.field)
        if message.role != 'user' or not (message.text.split('\n')[-1] == expected or initial):
            raise ValueError("Pending discovery does not match the customer's public question")
    for receipt in state.approval_receipts:
        receipt.artifact.resolve(history)
        receipt.approval_request.resolve(history)
        artifact = _observe_at(history, receipt.artifact.message_index).artifact
        if artifact is None or artifact.kind != receipt.kind or artifact.evidence != receipt.artifact or artifact.approval_request != receipt.approval_request:
            raise ValueError("Approval receipt lacks a public artifact and approval request")
        if receipt.customer_message_index >= len(history) or history[receipt.customer_message_index] != PublicMessage(
            role='user', text=_confirmation_message(receipt.kind)):
            raise ValueError("Approval receipt lacks the customer's public approval")
    if state.stop_decision:
        for evidence in state.stop_decision.evidence:
            evidence.resolve(history)


def _confirmation_message(kind: str) -> str:
    return ("Yes, that configuration is correct." if kind == 'CONFIGURATION'
            else "Yes, I confirm the final order and would like to place it.")


def _intended_match(customer: CustomerScenario, artifact: PublicArtifact) -> bool:
    values = {v.field: v.value for v in artifact.values}
    fields = list(CONFIGURATION_FIELDS) + ['quantity']
    if artifact.kind == 'FINAL_ORDER':
        fields += ['customer_name', 'phone', 'email', 'company_name', 'customer_instructions',
                   'delivery_address.address', 'delivery_address.city', 'delivery_address.state',
                   'delivery_address.postcode', 'delivery_address.country', 'room_size']
    for field in fields:
        expected = customer_value(customer, field)
        if values.get(field) != (None if expected is None else str(expected)):
            return False
    # Public product code/prices are accepted as presented. No hidden truth is
    # available here and no business lookup or numeric correctness check occurs.
    return True


def _render_actions(actions: tuple[CustomerAction, ...], customer: CustomerScenario) -> str:
    parts = []
    for action in actions:
        if isinstance(action, InitialMessage):
            parts.append(customer.initial_message)
        elif isinstance(action, ProvideInformation):
            for field in action.fields:
                value = customer_value(customer, field)
                if value is None:
                    parts.append("I don't have a company name to provide." if field == 'company_name' else "I have no special instructions.")
                elif field == 'customer_name':
                    parts.append(f"My name is {value}.")
                elif field == 'quantity':
                    parts.append(f"The quantity is {value}.")
                else:
                    parts.append(f"My {FIELD_LABELS[field]} is {value}.")
        elif isinstance(action, (AskAvailableOptions, AskAboutTargetOption)):
            parts.append(_question(action.field, customer, isinstance(action, AskAboutTargetOption)))
        elif isinstance(action, SelectOption):
            parts.append(f"For {FIELD_LABELS[action.field]}, I'll choose {customer_value(customer, action.field)}.")
        else:
            parts.append(_confirmation_message('CONFIGURATION' if isinstance(action, ConfirmConfiguration) else 'FINAL_ORDER'))
    return '\n'.join(parts)


def _replace_state(state: CustomerSimulatorState, **changes) -> CustomerSimulatorState:
    # model_copy(update=...) does not validate updates; construct a validated shell.
    return CustomerSimulatorState(**{**{name: getattr(state, name) for name in type(state).model_fields}, **changes})


def _stop(state, reason, evidence=(), field=None) -> SimulatorStep:
    decision = StopDecision(reason=reason, evidence=evidence, field=field)
    return SimulatorStep(decision=decision, state=_replace_state(state, stop_decision=decision))


def step(inputs: CustomerSimulatorInput) -> SimulatorStep:
    """Return an atomic proposal. Caller adopts it only after accepting the message.

    No session continuity/hashing, sending, persistence, retries, or I/O occurs.
    Invalid contracts/evidence raise; uninterpretable public text yields STOP.
    """
    inputs = CustomerSimulatorInput.model_validate(inputs)
    customer, state, history = inputs.customer, inputs.state, inputs.public_history
    _validate_evidence(customer, state, history)
    if state.stop_decision:
        return SimulatorStep(decision=state.stop_decision, state=state)
    if state.turn_index >= MAX_CUSTOMER_MESSAGES:
        return _stop(state, 'RUNAWAY_LIMIT_REACHED')
    if state.turn_index == 0:
        if history:
            raise ValueError("Initial customer turn requires empty public history")
        field = initial_discovery_field(customer.initial_message)
        policy = customer.conversation_policy.configuration_selection
        if field and (policy is None or field not in policy.discovery_fields):
            raise ValueError("Initial public discovery question conflicts with customer policy")
        actions = (InitialMessage(),)
        decision = CustomerTurn(actions=actions, message=_render_actions(actions, customer))
        new_state = _replace_state(state, turn_index=1, disclosed_fields=customer.initial_disclosures,
            pending_discovery=PendingDiscovery(field=field, question_kind='AVAILABLE_OPTIONS', customer_message_index=0) if field else None)
        return SimulatorStep(decision=decision, state=new_state)
    if inputs.latest_support_response is None:
        raise ValueError("A subsequent customer decision requires latest public Support text")
    observation = _observe_at(history, len(history) - 1)
    evidence = (observation.evidence,)
    if observation.kind == 'UNINTERPRETABLE':
        return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence)
    if observation.kind == 'UNAVAILABLE':
        return _stop(state, 'PUBLIC_UNAVAILABLE_RESPONSE', evidence)
    if observation.kind == 'ORDER_CREATED':
        return _stop(state, 'ORDER_CREATED_PUBLICLY_REPORTED', evidence)
    if observation.artifact:
        artifact = observation.artifact
        if not _intended_match(customer, artifact):
            return _stop(state, 'PUBLIC_CONTENT_MISMATCH', evidence)
        if artifact.approval_request is None or not set(CONFIGURATION_FIELDS) <= set(state.disclosed_fields):
            return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence)
        if artifact.kind == 'FINAL_ORDER' and not any(r.kind == 'CONFIGURATION' for r in state.approval_receipts):
            return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence)
        policy = (customer.conversation_policy.configuration_confirmation if artifact.kind == 'CONFIGURATION'
                  else customer.conversation_policy.final_confirmation)
        if policy.behavior != 'CONFIRM_WITHOUT_CHANGE':
            raise ValueError("Unsupported confirmation policy")
        action = (ConfirmConfiguration(artifact=artifact.evidence) if artifact.kind == 'CONFIGURATION'
                  else ConfirmFinalOrder(artifact=artifact.evidence))
        decision = CustomerTurn(actions=(action,), message=_render_actions((action,), customer))
        receipt = ApprovalReceipt(kind=artifact.kind, artifact=artifact.evidence,
            approval_request=artifact.approval_request, customer_message_index=len(history),
            repeated=any(r.kind == artifact.kind and r.artifact.quote == artifact.evidence.quote for r in state.approval_receipts))
        return SimulatorStep(decision=decision, state=_replace_state(state, turn_index=state.turn_index + 1,
            approval_receipts=(*state.approval_receipts, receipt)))

    offers = {offer.field: offer for offer in state.observed_target_offers}
    # Public denials invalidate earlier positive evidence; private targets never
    # override them, even for a previously selected option.
    for item in observation.availability:
        if customer_value(customer, item.field) in item.values:
            if item.status == 'DENIED':
                return _stop(state, 'TARGET_OPTION_DENIED', (item.evidence,), item.field)
            offers[item.field] = TargetOfferEvidence(field=item.field, evidence=item.evidence)
    requests = list(state.pending_requests)
    for request in observation.requests:
        if request.field not in {r.field for r in requests}:
            requests.append(request)
    actions = []
    disclosed = list(state.disclosed_fields)
    pending = state.pending_discovery
    new_pending = None
    fulfilled = set()
    if pending:
        answers = [a for a in observation.availability if a.field == pending.field]
        if not answers:
            # Do not skip a failed enquiry to answer a fresh unrelated prompt.
            return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence, pending.field)
        target_offered = any(a.status == 'OFFERED' and customer_value(customer, pending.field) in a.values for a in answers)
        if target_offered:
            actions.append(SelectOption(field=pending.field))
            fulfilled.add(pending.field)
        elif pending.question_kind == 'AVAILABLE_OPTIONS':
            actions.append(AskAboutTargetOption(field=pending.field))
            new_pending = PendingDiscovery(field=pending.field, question_kind='TARGET_OPTION', customer_message_index=len(history))
        else:
            return _stop(state, 'TARGET_OPTION_NOT_RESOLVED', evidence, pending.field)

    for request in requests:
        field = request.field
        if field in fulfilled:
            continue
        if field not in CONFIGURATION_FIELDS:
            # Scenario policies explicitly permit these disclosures when asked.
            policy = customer.conversation_policy
            if field == 'room_size':
                permitted = policy.subsequent_disclosure or policy.room_information_when_requested
            else:
                permitted = policy.subsequent_disclosure or policy.customer_information_when_requested
            if not permitted:
                return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence)
            actions.append(ProvideInformation(fields=(field,)))
            fulfilled.add(field)
        elif field in customer.initially_known_configuration_fields or field in offers:
            actions.append(SelectOption(field=field))
            fulfilled.add(field)
        elif new_pending is None:
            policy = customer.conversation_policy.configuration_selection
            if policy is None or field not in policy.discovery_fields:
                return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence, field)
            has_options = any(a.field == field and a.status == 'OFFERED' for a in observation.availability)
            action = AskAboutTargetOption(field=field) if has_options else AskAvailableOptions(field=field)
            actions.append(action)
            new_pending = PendingDiscovery(field=field, question_kind='TARGET_OPTION' if has_options else 'AVAILABLE_OPTIONS', customer_message_index=len(history))
    if not actions:
        return _stop(state, 'SIMULATOR_UNINTERPRETABLE_RESPONSE', evidence)
    # Put the single discovery question last so its public referent is clear.
    actions = tuple(a for a in actions if not isinstance(a, (AskAvailableOptions, AskAboutTargetOption))) + tuple(
        a for a in actions if isinstance(a, (AskAvailableOptions, AskAboutTargetOption)))
    for action in actions:
        fields = action.fields if isinstance(action, ProvideInformation) else (action.field,) if isinstance(action, SelectOption) else ()
        for field in fields:
            if field not in disclosed:
                disclosed.append(field)
    decision = CustomerTurn(actions=actions, message=_render_actions(actions, customer))
    new_state = _replace_state(state, turn_index=state.turn_index + 1, disclosed_fields=tuple(disclosed),
        observed_target_offers=tuple(offers.values()), pending_requests=tuple(r for r in requests if r.field not in fulfilled),
        pending_discovery=new_pending)
    return SimulatorStep(decision=decision, state=new_state)
