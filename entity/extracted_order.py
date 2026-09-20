"""Extracted partial customer updates as value objects."""

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class ExtractedDeliveryAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    address: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None

    @field_validator("*")
    @classmethod
    def validate_supplied_component(cls, value, info: ValidationInfo):
        # Defaults are not validated: omitted fields are not clearing requests.
        if value is None:
            raise ValueError("Required address components cannot be cleared.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Supplied strings must contain non-whitespace text.")
        return value


class ExtractedOrderInformation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    customer_name: str | None = None
    email: str | None = None
    phone: str | None = None
    room_size: str | None = None
    company_name: str | None = None
    customer_instructions: str | None = None
    delivery_address: ExtractedDeliveryAddress | None = None
    product_model: str | None = None
    table_size: str | None = None
    timber: str | None = None
    timber_painting: str | None = None
    felt_color: str | None = None
    bracket: str | None = None
    top_profile: str | None = None
    quantity: int | None = Field(default=None, ge=1)

    @field_validator("*")
    @classmethod
    def validate_supplied_field(cls, value, info: ValidationInfo):
        if value is None and info.field_name not in {"company_name", "customer_instructions"}:
            raise ValueError("Only optional customer fields can be cleared.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Supplied strings must contain non-whitespace text.")
        return value
