"""Shared ATS deterministic routing and a separate Business Agent dispatch boundary."""

from collections.abc import Callable
from enum import Enum

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from workflow.classifier import ClassifierResult, MessageCategory
from agents.order_agent import process_order_creation_message
from workflow.order.order_creation_extraction import ConversationMessage
from entity.order_creation_state import OrderCreationState, OrderWorkflowStatus
from entity.business_result import BusinessResult
from agents.support_agent import SupportAction, SupportActionResult, SupportKnowledgeContext, handle_support_action


class TargetAgent(str, Enum):
    SUPPORT_AGENT = "SUPPORT_AGENT"
    ORDER_AGENT = "ORDER_AGENT"
    PRODUCTION_AGENT = "PRODUCTION_AGENT"


class BusinessAction(str, Enum):
    ANSWER_ENQUIRY = "ANSWER_ENQUIRY"
    RESPOND_CHAT = "RESPOND_CHAT"
    FOLLOW_UP_SUPPORT_TICKET = "FOLLOW_UP_SUPPORT_TICKET"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    CREATE_ORDER = "CREATE_ORDER"
    UPDATE_ORDER = "UPDATE_ORDER"
    GET_ORDER_INFO = "GET_ORDER_INFO"
    CREATE_QUOTATION = "CREATE_QUOTATION"
    GET_PRODUCTION_INFO = "GET_PRODUCTION_INFO"


class RoutingStatus(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    UNRESOLVED = "UNRESOLVED"


class RoutingReason(str, Enum):
    ROUTE_AVAILABLE = "ROUTE_AVAILABLE"
    DOWNSTREAM_NOT_IMPLEMENTED = "DOWNSTREAM_NOT_IMPLEMENTED"
    NO_ACTIVE_WORKFLOW = "NO_ACTIVE_WORKFLOW"
    WORKFLOW_NOT_ACTIVE = "WORKFLOW_NOT_ACTIVE"


class RoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always")

    source_category: MessageCategory
    target_agent: TargetAgent | None
    business_action: BusinessAction | None
    workflow_type: str | None
    status: RoutingStatus
    reason: RoutingReason


class RootExecutionResult(BaseModel):
    """executed means the agent returned normally, not business success.

    The caller retains returned Wt and sends BusinessResult to the later Support
    boundary. Neither this wrapper nor routing replaces the BusinessResult contract.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    routing: RoutingResult
    executed: bool
    state: OrderCreationState | None
    business_result: BusinessResult | None
    support_result: SupportActionResult | None = None


    @model_validator(mode="after")
    def consistent_execution(self):
        outcomes = int(self.business_result is not None) + int(self.support_result is not None)
        if outcomes != (1 if self.executed else 0):
            raise ValueError("Execution must contain exactly one outcome on normal return, otherwise none.")
        return self


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


def route_message(
    classification: ClassifierResult,
    state: OrderCreationState | None = None,
) -> RoutingResult:
    """Route an already classified customer turn, with no LLM call or execution.

    Wt contributes only workflow identity and active status. Stage, pending fields,
    confirmations, validation and pricing evidence play no part in routing.
    """
    if not isinstance(classification, ClassifierResult):
        raise TypeError("classification must be a ClassifierResult.")
    # Revalidate even a caller-mutated/model_construct-created classifier result.
    validated = ClassifierResult.model_validate(classification.model_dump(warnings=False), strict=True)
    return _route_category(validated.category, state)


def execute_route(
    routing: RoutingResult,
    current_message: str,
    conversation_history: list[ConversationMessage],
    *,
    conversation_id: str,
    state: OrderCreationState | None = None,
    order_creation_processor: OrderCreationProcessor = process_order_creation_message,
    support_processor: Callable = handle_support_action,
    support_knowledge: SupportKnowledgeContext | None = None,
) -> RootExecutionResult:
    """Dispatch an already-selected route; technical exceptions propagate.

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
    if not isinstance(routing, RoutingResult):
        raise TypeError("routing must be a RoutingResult.")
    routing = RoutingResult.model_validate(routing)
    expected = _route_category(routing.source_category, state)
    if routing != expected:
        raise ValueError("Routing decision does not match the current category/state mapping.")
    if not isinstance(current_message, str) or not current_message.strip():
        raise ValueError("current_message must be a non-blank string.")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id must be a non-blank string.")
    if state is not None and state.conversation_id != conversation_id:
        raise ValueError("Workflow conversation_id does not match the current conversation.")
    history = TypeAdapter(list[ConversationMessage]).validate_python(conversation_history, strict=True)
    if routing.status != RoutingStatus.READY:
        return RootExecutionResult(routing=routing, executed=False, state=state, business_result=None)
    if routing.target_agent == TargetAgent.SUPPORT_AGENT:
        if not callable(support_processor):
            raise TypeError("support_processor must be callable.")
        action = SupportAction(routing.business_action.value)
        support_result = support_processor(action, current_message, conversation_history=history,
                                           business_context=support_knowledge)
        if not isinstance(support_result, SupportActionResult) or support_result.action != action:
            raise TypeError("Support processor must return a matching SupportActionResult.")
        return RootExecutionResult(routing=routing, executed=True, state=state,
                                   business_result=None, support_result=support_result)
    if not callable(order_creation_processor):
        raise TypeError("order_creation_processor must be callable.")
    # Default initialization is lifecycle/bootstrap, not a workflow transition.
    dispatch_state = state if state is not None else OrderCreationState(conversation_id=conversation_id)
    updated_state, business_result = order_creation_processor(current_message, history, dispatch_state)
    if not isinstance(updated_state, OrderCreationState) or not isinstance(business_result, BusinessResult):
        raise TypeError("Order creation processor must return OrderCreationState and BusinessResult.")
    return RootExecutionResult(
        routing=routing, executed=True, state=updated_state, business_result=business_result,
    )
