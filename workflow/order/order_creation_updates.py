"""Partial customer updates; no extraction, workflow execution, or invalidation."""

from copy import deepcopy

from entity.extracted_order import ExtractedOrderInformation
from entity.order_creation_state import OrderCreationState


def apply_extracted_order_information(
    state: OrderCreationState,
    extracted: ExtractedOrderInformation,
) -> OrderCreationState:
    """Return an independent validated state, preserving both input objects.

    exclude_unset preserves the distinction between omission and explicit null.
    Revalidate supplied data because assignments can bypass model validation.
    Derived values and confirmations are preserved, not certified still valid:
    downstream invalidation belongs to future explicit workflow policy.
    """
    supplied = extracted.model_dump(exclude_unset=True)
    updates = ExtractedOrderInformation.model_validate(supplied).model_dump(
        exclude_unset=True
    )
    candidate = deepcopy(state.model_dump())
    for field, value in updates.items():
        if field == "delivery_address":
            candidate[field].update(value)
        else:
            candidate[field] = value
    return OrderCreationState.model_validate(candidate)
