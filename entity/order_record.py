"""Persisted order creation record and its identifiers."""

from datetime import datetime, timezone
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from entity.order_creation_state import FinalOrderSnapshot


ORDER_ID_PATTERN = re.compile(r"^ORD-(\d{6})$")


def _validate_utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("created_at must be a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("created_at must be timezone-aware UTC.")
    return value


class OrderCreationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")

    order_id: str
    source_workflow_id: str
    conversation_id: str
    created_at: str
    order_status: Literal["CONFIRMED"]
    order: FinalOrderSnapshot

    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: str) -> str:
        if ORDER_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("order_id must match ORD-000001 format.")
        return value

    @field_validator("source_workflow_id", "conversation_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Store identifiers must not be blank.")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)
