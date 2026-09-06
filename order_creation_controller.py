"""Internal collection-stage handling for ORDER_CREATE_WF."""

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_creation_state import (
    OrderCreationStage,
    OrderCreationState,
    OrderWorkflowStatus,
    determine_missing_fields,
)
from workflow_state import current_utc_time


def handle_collect_requirements(state: OrderCreationState) -> BusinessResult | None:
    """Update collection tracking and return a user-input result when needed.

    None signals internal continuation to VALIDATE_CONFIGURATION; it is not
    a successful order result. This handler does not execute the next stage.
    Customer wording, conflicts, and other requirement tracking are untouched.
    """
    if state.current_stage != OrderCreationStage.COLLECT_REQUIREMENTS:
        raise ValueError("Handler requires current_stage=COLLECT_REQUIREMENTS.")
    if state.status not in {
        OrderWorkflowStatus.ACTIVE,
        OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Handler requires status=ACTIVE or AWAITING_USER_INPUT.")

    missing = determine_missing_fields(state)
    state.missing_customer_fields = missing

    if missing:
        state.pending_field = missing[0]
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.updated_at = current_utc_time()
        return BusinessResult(
            workflow_id=state.workflow_id,
            source_agent="ORDER_AGENT",
            action="CREATE_ORDER",
            current_stage=OrderCreationStage.COLLECT_REQUIREMENTS.value,
            result_status=BusinessResultStatus.NEEDS_USER_INPUT,
            reason=BusinessResultReason.MISSING_REQUIRED_INFORMATION,
            required_input=[state.pending_field],
            data={"missing_fields": missing.copy()},
            error=None,
        )

    state.pending_field = None
    state.status = OrderWorkflowStatus.ACTIVE
    state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
    state.updated_at = current_utc_time()
    return None
