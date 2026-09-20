"""Conversation message, session and turn value objects."""

from enum import Enum
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, PrivateAttr, field_validator,
                      model_validator)

from entity.classification import ClassifierResult
from entity.order_creation_state import OrderCreationState
from entity.routing import RootExecutionResult
from entity.support import CustomerResponse


class ConversationMessage(BaseModel):
    """One prior customer/Support message; callers may also supply dictionaries."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("History message content must not be blank.")
        return value


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class ConversationSession(RuntimeModel):
    conversation_id: str
    history: list[ConversationMessage] = Field(default_factory=list)
    workflow_state: OrderCreationState | None = None

    @property
    def support_ticket_id(self) -> str:
        return self.conversation_id

    @field_validator("conversation_id")
    @classmethod
    def valid_id(cls, value):
        if not value.strip():
            raise ValueError("conversation_id must be nonblank.")
        return value

    @model_validator(mode="after")
    def matching_workflow(self):
        if self.workflow_state is not None and self.workflow_state.conversation_id != self.conversation_id:
            raise ValueError("Session and workflow conversation IDs must match.")
        return self


class TurnStatus(str, Enum):
    RESPONDED = "RESPONDED"
    UNAVAILABLE = "UNAVAILABLE"
    UNRESOLVED = "UNRESOLVED"


class TurnResult(RuntimeModel):
    session: ConversationSession
    customer_response: CustomerResponse
    classification: ClassifierResult
    execution: RootExecutionResult
    status: TurnStatus


class PendingTurn(RuntimeModel):
    """In-memory response retry record. Caller must retain it after composition failure.

    A consumed record cannot be retried twice. Caller must not submit new turns
    against the base session while holding an unresolved pending record.
    """
    base_session: ConversationSession
    current_message: str
    classification: ClassifierResult
    execution: RootExecutionResult
    _consumed: bool = PrivateAttr(default=False)
