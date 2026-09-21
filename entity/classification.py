"""Message classification value objects."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


# Multi-label pairs currently allowed. A single category is always valid; a
# two-element `categories` is rejected unless it is exactly one of these pairs.
SUPPORTED_CATEGORY_PAIRS: tuple[frozenset[MessageCategory], ...] = (
    frozenset({MessageCategory.WORKFLOW_RESPONSE, MessageCategory.GENERAL_ENQUIRY}),
    frozenset({MessageCategory.CREATE_ORDER, MessageCategory.GENERAL_ENQUIRY}),
)


class ClassifierResult(BaseModel):
    """Categories for one customer turn: one intent, or two supported intents.

    Most turns carry a single intent, so `categories` has one element. Two
    elements describe one message that simultaneously contains an order/workflow
    intent and an independent product enquiry. confidence and explanation
    describe the classification as a whole, not per category. Ordering inside
    the list carries no meaning: consumers normalise it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    categories: list[MessageCategory] = Field(min_length=1, max_length=2)
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)

    @field_validator("explanation")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation must not be blank.")
        return value

    @model_validator(mode="after")
    def unique_supported_categories(self):
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must not repeat a category.")
        if (len(self.categories) == 2
                and frozenset(self.categories) not in SUPPORTED_CATEGORY_PAIRS):
            raise ValueError(
                "unsupported two-category combination; only WORKFLOW_RESPONSE"
                "+GENERAL_ENQUIRY and CREATE_ORDER+GENERAL_ENQUIRY are supported.")
        return self

    @property
    def primary_category(self) -> MessageCategory:
        return self.categories[0]
