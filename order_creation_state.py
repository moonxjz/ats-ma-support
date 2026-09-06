"""ATS order state and completeness checking; temporary MVP file organization."""

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from workflow_state import current_utc_time, generate_workflow_id


class OrderCreationStage(str, Enum):
    COLLECT_REQUIREMENTS = "COLLECT_REQUIREMENTS"
    VALIDATE_CONFIGURATION = "VALIDATE_CONFIGURATION"
    CONFIGURATION_CONFIRMATION = "CONFIGURATION_CONFIRMATION"
    PRICING = "PRICING"
    FINAL_CONFIRMATION = "FINAL_CONFIRMATION"
    CREATE_ORDER = "CREATE_ORDER"
    COMPLETED = "COMPLETED"


class OrderWorkflowStatus(str, Enum):
    ACTIVE = "ACTIVE"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DeliveryAddress(BaseModel):
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None


class OrderCreationState(BaseModel):
    workflow_id: str = Field(default_factory=generate_workflow_id)
    conversation_id: str
    workflow_type: Literal["ORDER_CREATE_WF"] = "ORDER_CREATE_WF"
    owner_agent: Literal["ORDER_AGENT"] = "ORDER_AGENT"
    status: OrderWorkflowStatus = OrderWorkflowStatus.ACTIVE
    current_stage: OrderCreationStage = OrderCreationStage.COLLECT_REQUIREMENTS
    created_at: str = Field(default_factory=current_utc_time)
    updated_at: str = Field(default_factory=current_utc_time)
    last_question: str | None = None
    pending_field: str | None = None

    customer_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_name: str | None = None
    delivery_address: DeliveryAddress = Field(default_factory=DeliveryAddress)
    room_size: str | None = None
    customer_instructions: str | None = None

    product_model: str | None = None
    table_size: str | None = None
    timber: str | None = None
    timber_painting: str | None = None
    felt_color: str | None = None
    bracket: str | None = None
    top_profile: str | None = None
    quantity: int = Field(default=1, ge=1)

    missing_customer_fields: list[str] = Field(default_factory=list)
    pending_system_fields: list[str] = Field(default_factory=list)
    conflicting_fields: dict = Field(default_factory=dict)
    pending_confirmations: list[str] = Field(
        default_factory=lambda: ["configuration_confirmed", "final_order_confirmed"]
    )

    product_sku: str | None = None
    room_size_validation_result: Literal["SUITABLE", "UNSUITABLE"] | None = None
    unit_price: Decimal | None = None
    customisation_price: Decimal | None = None
    shipping_method: str | None = None
    shipping_cost: Decimal | None = None
    total_price: Decimal | None = None
    order_snapshot: dict = Field(default_factory=dict)
    approved: bool | None = None

    configuration_confirmed: bool = False
    final_order_confirmed: bool = False
    order_id: str | None = None
    order_status: str | None = None
    failure_reason: str | None = None


# WF- ORDER_CREATE_WF, rows 4–25: fixed custom-pool-table MVP requirements.
REQUIRED_CUSTOMER_FIELDS: tuple[str, ...] = (
    "customer_name",
    "email",
    "phone",
    "delivery_address.address_line_1",
    "delivery_address.city",
    "delivery_address.state",
    "delivery_address.postcode",
    "delivery_address.country",
    "room_size",
    "product_model",
    "table_size",
    "timber",
    "timber_painting",
    "felt_color",
    "bracket",
    "top_profile",
)


def determine_missing_fields(state: OrderCreationState) -> list[str]:
    """Return absent/blank customer fields without changing state or validating rules.

    Tracking fields are not refreshed here. The future controller owns updates,
    stage transitions, and decisions about customer questions.
    """
    missing = []
    for field_path in REQUIRED_CUSTOMER_FIELDS:
        value = state
        for component in field_path.split("."):
            value = getattr(value, component)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_path)
    return missing
