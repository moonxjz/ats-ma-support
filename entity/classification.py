"""Message classification value objects."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageCategory(str, Enum):
    GENERAL_ENQUIRY = "GENERAL_ENQUIRY"
    CASUAL_CHAT = "CASUAL_CHAT"
    SUPPORT_TICKET_FOLLOWUP = "SUPPORT_TICKET_FOLLOWUP"
    UNKNOWN_OTHER_INQUIRY = "UNKNOWN_OTHER_INQUIRY"
    CREATE_ORDER = "CREATE_ORDER"
    UPDATE_ORDER = "UPDATE_ORDER"
    ORDER_ENQUIRY = "ORDER_ENQUIRY"
    QUOTATION_ENQUIRY = "QUOTATION_ENQUIRY"
    PRODUCTION_STATUS_ENQUIRY = "PRODUCTION_STATUS_ENQUIRY"
    WORKFLOW_RESPONSE = "WORKFLOW_RESPONSE"


class ClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: MessageCategory
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)

    @field_validator("explanation")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation must not be blank.")
        return value
