"""Business result contract from the ATS order-creation design."""

from enum import Enum

from pydantic import BaseModel, Field


class BusinessResultStatus(str, Enum):
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    CANCELLED = "CANCELLED"


class BusinessResultReason(str, Enum):
    UNSUPPORTED_CONFIGURATION_VALUE = "UNSUPPORTED_CONFIGURATION_VALUE"
    MISSING_REQUIRED_INFORMATION = "MISSING_REQUIRED_INFORMATION"
    ROOM_SIZE_UNSUITABLE = "ROOM_SIZE_UNSUITABLE"
    CONFIGURATION_CONFIRMATION_REQUIRED = "CONFIGURATION_CONFIRMATION_REQUIRED"
    FINAL_CONFIRMATION_REQUIRED = "FINAL_CONFIRMATION_REQUIRED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_CREATION_FAILED = "ORDER_CREATION_FAILED"
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED"


class BusinessResult(BaseModel):
    workflow_id: str
    source_agent: str
    action: str
    result_status: BusinessResultStatus
    current_stage: str
    reason: BusinessResultReason
    data: dict = Field(default_factory=dict)
    required_input: list[str] = Field(default_factory=list)
    error: dict | None = None
