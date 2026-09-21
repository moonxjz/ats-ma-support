"""Routing and root-execution value objects."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from entity.business_result import BusinessResult
from entity.classification import MessageCategory
from entity.order_creation_state import OrderCreationState
from entity.support import CustomerResponse, SupportActionResult


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
    # Text-only companion reply from a second executed category. The single
    # outcome contract above is unchanged: exactly one of business_result and
    # support_result describes the primary route. This field only carries the
    # already-composed customer wording of a secondary route (currently a
    # GENERAL_ENQUIRY answered alongside an order/workflow turn) so callers can
    # concatenate the two replies into one customer-facing message.
    additional_response: CustomerResponse | None = None


    @model_validator(mode="after")
    def consistent_execution(self):
        outcomes = int(self.business_result is not None) + int(self.support_result is not None)
        if outcomes != (1 if self.executed else 0):
            raise ValueError("Execution must contain exactly one outcome on normal return, otherwise none.")
        return self
