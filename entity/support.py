"""Support value objects and shared knowledge contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string.")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class CustomerResponse(StrictModel):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return _text(value, "text")


class ResponseIntent(str, Enum):
    REQUEST_REQUIRED_INFORMATION = "REQUEST_REQUIRED_INFORMATION"
    EXPLAIN_CONFIGURATION_ISSUE = "EXPLAIN_CONFIGURATION_ISSUE"
    EXPLAIN_ROOM_INCOMPATIBILITY = "EXPLAIN_ROOM_INCOMPATIBILITY"
    REQUEST_CONFIGURATION_CONFIRMATION = "REQUEST_CONFIGURATION_CONFIRMATION"
    REQUEST_FINAL_CONFIRMATION = "REQUEST_FINAL_CONFIRMATION"
    REPORT_ORDER_CREATED = "REPORT_ORDER_CREATED"
    REPORT_ORDER_CREATION_FAILURE = "REPORT_ORDER_CREATION_FAILURE"
    ACKNOWLEDGE_REQUEST_CANCELLATION = "ACKNOWLEDGE_REQUEST_CANCELLATION"
    ANSWER_FROM_KNOWLEDGE = "ANSWER_FROM_KNOWLEDGE"
    RESPOND_SOCIAL = "RESPOND_SOCIAL"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    REPORT_TICKET_INFORMATION = "REPORT_TICKET_INFORMATION"
    REPORT_INFORMATION_UNAVAILABLE = "REPORT_INFORMATION_UNAVAILABLE"


class ResponseContext(StrictModel):
    response_intent: ResponseIntent
    allowed_facts: dict[str, JsonValue]
    required_input: list[str]
    response_constraints: list[str]


class SupportAction(str, Enum):
    ANSWER_ENQUIRY = "ANSWER_ENQUIRY"
    RESPOND_CHAT = "RESPOND_CHAT"
    FOLLOW_UP_SUPPORT_TICKET = "FOLLOW_UP_SUPPORT_TICKET"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"


class SupportOutcome(str, Enum):
    ANSWERED = "ANSWERED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    INFORMATION_UNAVAILABLE = "INFORMATION_UNAVAILABLE"


class KnowledgeFact(StrictModel):
    """Caller-selected authoritative, customer-safe answer evidence, not retrieval."""
    text: str
    source_reference: str

    @field_validator("text", "source_reference")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return _text(value, "knowledge fact")


class TicketInformation(StrictModel):
    reference: str
    status: str

    @field_validator("reference", "status")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return _text(value, "ticket information")


class SupportKnowledgeContext(StrictModel):
    """The caller establishes relevance and authority; Support never searches Bt.

    ticket_reference may be customer-supplied; ticket_information is authoritative.
    Source references must also be customer-safe. Do not pass private backend dumps.
    """
    answer_facts: list[KnowledgeFact] = Field(default_factory=list)
    ticket_reference: str | None = None
    ticket_information: TicketInformation | None = None

    @field_validator("ticket_reference")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        return None if value is None else _text(value, "ticket_reference")


class SupportActionResult(StrictModel):
    action: SupportAction
    outcome: SupportOutcome
    response: CustomerResponse


class ConfirmationFraming(StrictModel):
    introduction: str
    confirmation_request: str

    @field_validator("introduction", "confirmation_request")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return _text(value, "confirmation framing")
