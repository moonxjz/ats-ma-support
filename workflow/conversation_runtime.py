"""Shared sequential session orchestration; A3 is the default execution boundary.

Ht/Wt are committed by returning a new session after response success. Persistent
order effects are not transactional with session memory. Pending response retry
reuses completed execution and never repeats business execution. No restart or
concurrent-session persistence is provided by this MVP.
"""

from copy import deepcopy

from workflow.classifier import classify_message
from agents.order_agent import process_order_creation_message
from entity.conversation import (ConversationMessage, ConversationSession, PendingTurn,
                                 TurnResult, TurnStatus)
from entity.routing import RoutingStatus
from agents.root_agent import execute_route, route_message
from entity.support import CustomerResponse, SupportKnowledgeContext
from agents.support_agent import compose_customer_response, compose_route_outcome, handle_support_action


class TurnFailure(RuntimeError):
    """Technical failure, never a customer/business success; original error is __cause__."""
    def __init__(self, phase: str, pending_turn: PendingTurn | None = None):
        super().__init__(f"Customer turn failed during {phase}.")
        self.phase = phase
        self.pending_turn = pending_turn

def _validate_session(session):
    if not isinstance(session, ConversationSession):
        raise TypeError("session must be a ConversationSession.")
    return deepcopy(ConversationSession.model_validate(session))

def _finish(base, message, classification, execution, response):
    if not isinstance(response, CustomerResponse):
        raise TypeError("Response composer must return CustomerResponse.")
    response = CustomerResponse.model_validate(response)
    # A two-category turn appends the secondary category's answer after the reply
    # composed for the primary outcome, producing one customer-visible message.
    additional = execution.additional_response
    if additional is not None:
        response = CustomerResponse(text="\n\n".join((response.text, additional.text)))
    history = deepcopy(base.history)
    history.extend([ConversationMessage(role="user", content=message),
                    ConversationMessage(role="assistant", content=response.text)])
    session = ConversationSession(conversation_id=base.conversation_id, history=history,
                                  workflow_state=deepcopy(execution.state))
    status = (TurnStatus(execution.routing.status.value) if execution.routing.status != RoutingStatus.READY
              else TurnStatus.RESPONDED)
    return TurnResult(session=session, customer_response=response, classification=deepcopy(classification),
                      execution=deepcopy(execution), status=status)

def process_customer_message(
    session: ConversationSession,
    current_message: str,
    *,
    support_knowledge: SupportKnowledgeContext | None = None,
    order_creation_processor=process_order_creation_message,
    classifier=classify_message,
    support_processor=handle_support_action,
    response_composer=compose_customer_response,
) -> TurnResult:
    """Process a NEW turn. No automatic retry, stage decisions or persistence lookup.

    Each component receives prior Ht, not the staged customer message. Components
    receive independent copies so caller history/state are unchanged on failure.
    Frozen model shells do not make nested objects immutable: callers must retain
    the returned session and treat session/trace/pending contents as read-only.
    """
    base = _validate_session(session)
    if not isinstance(current_message, str) or not current_message.strip():
        raise ValueError("current_message must be a nonblank string.")
    knowledge = None if support_knowledge is None else SupportKnowledgeContext.model_validate(support_knowledge)
    phase = "classification"
    try:
        classification = classifier(current_message=current_message, conversation_history=deepcopy(base.history),
                                    state=deepcopy(base.workflow_state))
        phase = "routing"
        routings = route_message(classification, deepcopy(base.workflow_state))
        phase = "business execution"
        execution = execute_route(routings, current_message, deepcopy(base.history),
                                  conversation_id=base.conversation_id, state=deepcopy(base.workflow_state),
                                  order_creation_processor=order_creation_processor,
                                  support_processor=support_processor, support_knowledge=deepcopy(knowledge))
    except Exception as exc:
        raise TurnFailure(phase) from exc
    if execution.business_result is not None:
        pending = PendingTurn(base_session=deepcopy(base), current_message=current_message,
                              classification=deepcopy(classification), execution=deepcopy(execution))
        return retry_pending_response(base, pending, response_composer=response_composer)
    try:
        response = (execution.support_result.response if execution.support_result is not None
                    else compose_route_outcome(execution.routing.status.value,
                        execution.routing.business_action.value if execution.routing.business_action else None))
        return _finish(base, current_message, classification, execution, response)
    except Exception as exc:
        raise TurnFailure("customer response") from exc

def retry_pending_response(
    session: ConversationSession,
    pending_turn: PendingTurn,
    *,
    response_composer=compose_customer_response,
) -> TurnResult:
    """Retry ONLY composition from cached execution. No classifier/routing/agent calls.

    A successful order write remains committed even if composition fails. Retain
    this PendingTurn and retry it rather than resubmitting the customer message.
    If business execution itself raised (possibly after a write), there is no
    cached authoritative result: this API cannot invent recovery or replay it.
    """
    base = _validate_session(session)
    if not isinstance(pending_turn, PendingTurn):
        raise TypeError("pending_turn must be a PendingTurn.")
    if pending_turn._consumed:
        raise ValueError("Pending response has already been committed.")
    if base != pending_turn.base_session:
        raise ValueError("Pending response does not match the current committed session.")
    execution = deepcopy(pending_turn.execution)
    if not execution.executed or execution.business_result is None or execution.support_result is not None:
        raise ValueError("Pending response requires an authoritative completed business execution.")
    try:
        response = response_composer(execution.business_result,
                                     current_message=pending_turn.current_message,
                                     conversation_history=deepcopy(base.history))
        result = _finish(base, pending_turn.current_message, pending_turn.classification,
                         pending_turn.execution, response)
    except Exception as exc:
        raise TurnFailure("response composition", pending_turn) from exc
    pending_turn._consumed = True
    return result
