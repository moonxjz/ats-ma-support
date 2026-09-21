"""Shared ATS deterministic routing and a separate Business Agent dispatch boundary."""

from collections.abc import Callable

from pydantic import TypeAdapter

from entity.classification import ClassifierResult, MessageCategory
from agents.order_agent import process_order_creation_message
from entity.conversation import ConversationMessage
from entity.order_creation_state import OrderCreationState, OrderWorkflowStatus
from entity.business_result import BusinessResult
from entity.support import CustomerResponse, SupportAction, SupportActionResult, SupportKnowledgeContext
from agents.support_agent import handle_support_action

from entity.routing import TargetAgent, BusinessAction, RoutingStatus, RoutingReason, RoutingResult, RootExecutionResult

OrderCreationProcessor = Callable[
    [str, list[ConversationMessage], OrderCreationState],
    tuple[OrderCreationState, BusinessResult],
]

# Ownership/action semantics are shared across architectures. Order creation and the four Support actions
# currently have downstream implementations. Availability is not order authorization.
_ROUTES = {
    MessageCategory.GENERAL_ENQUIRY: (TargetAgent.SUPPORT_AGENT, BusinessAction.ANSWER_ENQUIRY, None),
    MessageCategory.CASUAL_CHAT: (TargetAgent.SUPPORT_AGENT, BusinessAction.RESPOND_CHAT, None),
    MessageCategory.SUPPORT_TICKET_FOLLOWUP: (TargetAgent.SUPPORT_AGENT, BusinessAction.FOLLOW_UP_SUPPORT_TICKET, None),
    MessageCategory.UNKNOWN_OTHER_INQUIRY: (TargetAgent.SUPPORT_AGENT, BusinessAction.REQUEST_CLARIFICATION, None),
    MessageCategory.CREATE_ORDER: (TargetAgent.ORDER_AGENT, BusinessAction.CREATE_ORDER, "ORDER_CREATE_WF"),
    MessageCategory.UPDATE_ORDER: (TargetAgent.ORDER_AGENT, BusinessAction.UPDATE_ORDER, "ORDER_UPDATE_WF"),
    MessageCategory.ORDER_ENQUIRY: (TargetAgent.ORDER_AGENT, BusinessAction.GET_ORDER_INFO, None),
    MessageCategory.QUOTATION_ENQUIRY: (TargetAgent.ORDER_AGENT, BusinessAction.CREATE_QUOTATION, "QUOTATION_CREATE_WF"),
    MessageCategory.PRODUCTION_STATUS_ENQUIRY: (TargetAgent.PRODUCTION_AGENT, BusinessAction.GET_PRODUCTION_INFO, None),
}

def _validate_routing_state(state: OrderCreationState | None) -> None:
    """Validate only routing metadata; never inspect workflow-control evidence."""
    if state is None:
        return
    if not isinstance(state, OrderCreationState):
        raise TypeError("state must be an OrderCreationState or None.")
    if state.workflow_type != "ORDER_CREATE_WF" or state.owner_agent != "ORDER_AGENT":
        raise ValueError("Unsupported or corrupted workflow routing identity.")
    if not isinstance(state.status, OrderWorkflowStatus):
        raise ValueError("Invalid workflow routing status.")

def _route_category(category: MessageCategory, state: OrderCreationState | None) -> RoutingResult:
    _validate_routing_state(state)
    if category == MessageCategory.WORKFLOW_RESPONSE:
        if state is None or state.status not in (
            OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT,
        ):
            return RoutingResult(
                source_category=category, target_agent=None, business_action=None,
                workflow_type=None, status=RoutingStatus.UNRESOLVED,
                reason=(RoutingReason.NO_ACTIVE_WORKFLOW if state is None
                        else RoutingReason.WORKFLOW_NOT_ACTIVE),
            )
        target, action, workflow_type = _ROUTES[MessageCategory.CREATE_ORDER]
    else:
        target, action, workflow_type = _ROUTES[category]
    available = action == BusinessAction.CREATE_ORDER or target == TargetAgent.SUPPORT_AGENT
    return RoutingResult(
        source_category=category, target_agent=target, business_action=action,
        workflow_type=workflow_type,
        status=RoutingStatus.READY if available else RoutingStatus.UNAVAILABLE,
        reason=(RoutingReason.ROUTE_AVAILABLE if available
                else RoutingReason.DOWNSTREAM_NOT_IMPLEMENTED),
    )

# A coexisting order-side intent is dispatched before GENERAL_ENQUIRY, so a
# combined turn advances the workflow first and then answers the question.
_ORDER_SIDE_CATEGORIES = (MessageCategory.CREATE_ORDER, MessageCategory.WORKFLOW_RESPONSE)


def _dispatch_order(category: MessageCategory) -> int:
    return 0 if category in _ORDER_SIDE_CATEGORIES else 1


def route_message(
    classification: ClassifierResult,
    state: OrderCreationState | None = None,
) -> list[RoutingResult]:
    """Route every classified category of a turn, with no LLM call or execution.

    Returns one RoutingResult per category, ordered for dispatch (order-side
    first, then any coexisting GENERAL_ENQUIRY). A single-category classification
    therefore yields the one-element list equivalent to the previous behaviour.
    Wt contributes only workflow identity and active status. Stage, pending
    fields, confirmations, validation and pricing evidence play no part in routing.
    """
    if not isinstance(classification, ClassifierResult):
        raise TypeError("classification must be a ClassifierResult.")
    # Revalidate even a caller-mutated/model_construct-created classifier result.
    validated = ClassifierResult.model_validate(classification.model_dump(warnings=False), strict=True)
    ordered = sorted(validated.categories, key=_dispatch_order)
    return [_route_category(category, state) for category in ordered]


def _dispatch_support(routing: RoutingResult, current_message: str,
                      history: list[ConversationMessage], support_processor: Callable,
                      support_knowledge: SupportKnowledgeContext | None) -> SupportActionResult:
    """Run one Support route and verify it returns the matching action result."""
    if not callable(support_processor):
        raise TypeError("support_processor must be callable.")
    action = SupportAction(routing.business_action.value)
    support_result = support_processor(action, current_message, conversation_history=history,
                                       business_context=support_knowledge)
    if not isinstance(support_result, SupportActionResult) or support_result.action != action:
        raise TypeError("Support processor must return a matching SupportActionResult.")
    return support_result

def execute_route(
    routing: list[RoutingResult],
    current_message: str,
    conversation_history: list[ConversationMessage],
    *,
    conversation_id: str,
    state: OrderCreationState | None = None,
    order_creation_processor: OrderCreationProcessor = process_order_creation_message,
    support_processor: Callable = handle_support_action,
    support_knowledge: SupportKnowledgeContext | None = None,
) -> RootExecutionResult:
    """Dispatch every routed category of one turn; technical exceptions propagate.

    The PRIMARY route (index 0) owns the single outcome: exactly one of
    business_result and support_result is returned, preserving that contract.
    Additional READY routes contribute no outcome — their already-composed
    wording is returned in additional_response so the caller can emit one
    concatenated customer reply (workflow/order answer first, enquiry second).
    A non-READY primary route stops the turn exactly as before, with no
    secondary category executed.

    Creating default Wt when CREATE_ORDER has no supplied state is lifecycle
    bootstrap only. Root neither selects subsequent stages nor performs recovery.
    Existing Wt (including terminal Wt for explicit CREATE_ORDER) is passed through
    unchanged to the processor. WORKFLOW_RESPONSE never bootstraps a workflow.

    Callers supply prior Ht, oldest first, and retain returned Wt themselves. The
    processor is the replaceable architecture-specific Business Agent boundary;
    the default is the existing A3 Order Agent. Root never calls its controller or
    order store directly. executed=True means normal agent return, including a
    NEEDS_USER_INPUT, FAILURE or CANCELLED business outcome.
    """
    if not isinstance(routing, list) or not routing:
        raise TypeError("routing must be a non-empty list of RoutingResult.")
    if not all(isinstance(item, RoutingResult) for item in routing):
        raise TypeError("routing must be a non-empty list of RoutingResult.")
    if not isinstance(current_message, str) or not current_message.strip():
        raise ValueError("current_message must be a non-blank string.")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id must be a non-blank string.")
    if state is not None and state.conversation_id != conversation_id:
        raise ValueError("Workflow conversation_id does not match the current conversation.")
    history = TypeAdapter(list[ConversationMessage]).validate_python(conversation_history, strict=True)

    primary = RoutingResult.model_validate(routing[0])
    if primary != _route_category(primary.source_category, state):
        raise ValueError("Routing decision does not match the current category/state mapping.")
    if primary.status != RoutingStatus.READY:
        return RootExecutionResult(routing=primary, executed=False, state=state, business_result=None)

    outcome_state = state
    business_result = None
    support_result = None
    if primary.target_agent == TargetAgent.SUPPORT_AGENT:
        support_result = _dispatch_support(primary, current_message, history,
                                           support_processor, support_knowledge)
    else:
        if not callable(order_creation_processor):
            raise TypeError("order_creation_processor must be callable.")
        # Default initialization is lifecycle/bootstrap, not a workflow transition.
        dispatch_state = state if state is not None else OrderCreationState(conversation_id=conversation_id)
        updated_state, business_result = order_creation_processor(current_message, history, dispatch_state)
        if not isinstance(updated_state, OrderCreationState) or not isinstance(business_result, BusinessResult):
            raise TypeError("Order creation processor must return OrderCreationState and BusinessResult.")
        outcome_state = updated_state

    if len(routing) == 1:
        return RootExecutionResult(routing=primary, executed=True, state=outcome_state,
                                   business_result=business_result, support_result=support_result)

    extra_texts = []
    for extra in routing[1:]:
        extra = RoutingResult.model_validate(extra)
        if extra != _route_category(extra.source_category, outcome_state):
            raise ValueError("Routing decision does not match the current category/state mapping.")
        if extra.status != RoutingStatus.READY:
            continue
        if extra.target_agent != TargetAgent.SUPPORT_AGENT:
            raise ValueError("Only a SUPPORT_AGENT secondary route can be combined into one reply.")
        dispatched = _dispatch_support(extra, current_message, history,
                                       support_processor, support_knowledge)
        extra_texts.append(dispatched.response.text)
    additional = CustomerResponse(text="\n\n".join(extra_texts)) if extra_texts else None
    return RootExecutionResult(routing=primary, executed=True, state=outcome_state,
                               business_result=business_result, support_result=support_result,
                               additional_response=additional)
