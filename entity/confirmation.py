"""Confirmation intent and interpretation value objects."""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ConfirmationIntent(str, Enum):
    CONFIRMED = "CONFIRMED"
    CHANGE_REQUESTED = "CHANGE_REQUESTED"
    DECLINED = "DECLINED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


class ConfirmationInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    intent: ConfirmationIntent
