"""Order Agent internal orchestration for already-routed ORDER_CREATE_WF turns."""

from copy import deepcopy

from order_creation_confirmation import interpret_confirmation_response
from business_result import BusinessResult
from order_creation_controller import apply_order_creation_reentry, execute_order_creation_workflow
from order_creation_extraction import ConversationMessage, extract_order_information
from order_creation_state import OrderCreationStage, OrderCreationState, OrderWorkflowStatus
from order_creation_updates import apply_extracted_order_information


def process_order_creation_message(
    current_message: str,
    conversation_history: list[ConversationMessage],
    state: OrderCreationState,
) -> tuple[OrderCreationState, BusinessResult]:
    """Process CREATE_ORDER after classification/routing has already occurred.

    The caller owns Wt and prior Ht; neither input is changed. On normal return,
    retain the returned independent state for the next turn. History dictionaries
    are accepted through the extractor's existing runtime validation boundary.
    The controller owns transitions and the exact BusinessResult returned here.

    Technical exceptions propagate unchanged. The controller-owned re-entry
    policy prepares changed requirements before stage execution. Normal unconfirmed
    configurations return a confirmation request. Accepted confirmation advances
    through PRICING to final waiting. Final authorization reaches unimplemented
    CREATE_ORDER and raises before returning its working
    copy; caller-owned Wt remains unchanged at that temporary boundary.
    """
    extracted = extract_order_information(
        current_message,
        conversation_history,
        state,
    )
    updated_state = apply_extracted_order_information(
        state,
        extracted,
    )
    apply_order_creation_reentry(state, updated_state)
    # Only a previously pending turn may interpret a response. Re-entry has
    # already moved effective changes away from confirmation. Use current Wt.
    if (
        state.current_stage == OrderCreationStage.CONFIGURATION_CONFIRMATION
        and state.status == OrderWorkflowStatus.AWAITING_USER_INPUT
        and not state.configuration_confirmed
        and "configuration_confirmed" in state.pending_confirmations
        and state.order_snapshot
        and updated_state.current_stage == OrderCreationStage.CONFIGURATION_CONFIRMATION
        and updated_state.status == OrderWorkflowStatus.AWAITING_USER_INPUT
        and not updated_state.configuration_confirmed
        and "configuration_confirmed" in updated_state.pending_confirmations
        and updated_state.order_snapshot
    ):
        snapshot = deepcopy(updated_state.order_snapshot)
        confirmation = interpret_confirmation_response(
            current_message, conversation_history, updated_state,
        )
        result = execute_order_creation_workflow(
            updated_state, confirmation=confirmation, confirmation_snapshot=snapshot,
        )
    elif (
        state.current_stage == OrderCreationStage.FINAL_CONFIRMATION
        and state.status == OrderWorkflowStatus.AWAITING_USER_INPUT
        and not state.final_order_confirmed
        and "final_order_confirmed" in state.pending_confirmations
        and state.final_order_snapshot is not None
        and updated_state.current_stage == OrderCreationStage.FINAL_CONFIRMATION
        and updated_state.status == OrderWorkflowStatus.AWAITING_USER_INPUT
        and not updated_state.final_order_confirmed
        and "final_order_confirmed" in updated_state.pending_confirmations
        and updated_state.final_order_snapshot is not None
    ):
        snapshot = deepcopy(updated_state.final_order_snapshot)
        confirmation = interpret_confirmation_response(current_message, conversation_history, updated_state)
        result = execute_order_creation_workflow(
            updated_state, final_confirmation=confirmation,
            final_confirmation_snapshot=snapshot,
        )
    else:
        result = execute_order_creation_workflow(updated_state)
    return updated_state, result
