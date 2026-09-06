"""Internal collection-stage handling for ORDER_CREATE_WF."""

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_creation_rules import validate_room_size
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


def handle_validate_configuration(state: OrderCreationState) -> BusinessResult | None:
    """Evaluate current room/table values; leave response wording to its owner."""
    if state.current_stage != OrderCreationStage.VALIDATE_CONFIGURATION:
        raise ValueError("Handler requires current_stage=VALIDATE_CONFIGURATION.")
    if state.status not in {
        OrderWorkflowStatus.ACTIVE,
        OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Handler requires status=ACTIVE or AWAITING_USER_INPUT.")

    state.missing_customer_fields = determine_missing_fields(state)
    validation_field = "room_size_validation_result"
    if state.missing_customer_fields:
        state.room_size_validation_result = None
        if validation_field not in state.pending_system_fields:
            state.pending_system_fields.append(validation_field)
        state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS
        return handle_collect_requirements(state)

    validation = validate_room_size(state.room_size, state.table_size)
    state.room_size_validation_result = validation["result"]
    if validation["result"] is None:
        if validation_field not in state.pending_system_fields:
            state.pending_system_fields.append(validation_field)
        required_input = validation["required_input"].copy()
        state.pending_field = required_input[0]
        reason = BusinessResultReason.MISSING_REQUIRED_INFORMATION
        data = {
            "missing_fields": state.missing_customer_fields.copy(),
            "input_details": validation["input_details"],
        }
    else:
        state.pending_system_fields = [
            field for field in state.pending_system_fields if field != validation_field
        ]
        if validation["result"] == "SUITABLE":
            state.pending_field = None
            state.status = OrderWorkflowStatus.ACTIVE
            state.current_stage = OrderCreationStage.CONFIGURATION_CONFIRMATION
            state.updated_at = current_utc_time()
            return None
        state.pending_field = "table_size"
        required_input = ["table_size"]
        reason = BusinessResultReason.ROOM_SIZE_UNSUITABLE
        data = {
            "room_size": state.room_size,
            "requested_table_size": state.table_size,
            "suitable_table_sizes": validation["suitable_table_sizes"],
        }

    state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
    state.updated_at = current_utc_time()
    return BusinessResult(
        workflow_id=state.workflow_id,
        source_agent="ORDER_AGENT",
        action="CREATE_ORDER",
        current_stage=state.current_stage.value,
        result_status=BusinessResultStatus.NEEDS_USER_INPUT,
        reason=reason,
        required_input=required_input,
        data=data,
        error=None,
    )
