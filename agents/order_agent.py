"""Order Agent internal orchestration for already-routed ORDER_CREATE_WF turns."""

from copy import deepcopy
from pathlib import Path

from workflow.order.order_creation_confirmation import ConfirmationIntent, interpret_confirmation_response
from entity.business_result import BusinessResult
from workflow.order.order_creation_order_store import DEFAULT_ORDER_STORE_PATH
from workflow.order.order_creation_controller import apply_order_creation_reentry, execute_order_creation_workflow
from workflow.order.order_creation_extraction import ConversationMessage, extract_order_information
from entity.order_creation_state import OrderCreationStage, OrderCreationState, OrderWorkflowStatus
from workflow.order.order_creation_updates import ExtractedOrderInformation, apply_extracted_order_information


def _execute_with_optional_store_path(updated_state: OrderCreationState, **kwargs) -> BusinessResult:
    order_store_path = kwargs.pop("order_store_path")
    if order_store_path == DEFAULT_ORDER_STORE_PATH:
        return execute_order_creation_workflow(updated_state, **kwargs)
    return execute_order_creation_workflow(updated_state, **kwargs, order_store_path=order_store_path)


def process_order_creation_message(
    current_message: str,
    conversation_history: list[ConversationMessage],
    state: OrderCreationState,
    *,
    order_store_path: Path = DEFAULT_ORDER_STORE_PATH,
) -> tuple[OrderCreationState, BusinessResult]:
    """Process CREATE_ORDER after classification/routing has already occurred.

    Pending confirmation is interpreted first; only CHANGE_REQUESTED enters
    extraction/merge. The Controller alone accepts evidence and progresses.
    The caller owns Wt and prior Ht; neither input is changed. On normal return,
    retain the returned independent state for the next turn. History dictionaries
    are accepted through the extractor's existing runtime validation boundary.
    The controller owns transitions and the exact BusinessResult returned here.

    Technical exceptions propagate unchanged. The controller-owned re-entry
    policy prepares changed requirements before stage execution. Normal unconfirmed
    configurations return a confirmation request. Accepted confirmation advances
    through PRICING to final waiting. After exact final authorization, the
    controller executes CREATE_ORDER through the deterministic JSON-backed local
    order-store tool. Successful creation or idempotent replay returns an
    order_id, reaches COMPLETED, and returns SUCCESS / ORDER_CREATED. Caller-owned
    Wt remains unchanged because this function works on the validated merged copy.
    """
    confirmation_stage = state.current_stage in {
        OrderCreationStage.CONFIGURATION_CONFIRMATION, OrderCreationStage.FINAL_CONFIRMATION,
    }
    if confirmation_stage and state.status == OrderWorkflowStatus.AWAITING_USER_INPUT:
        final = state.current_stage == OrderCreationStage.FINAL_CONFIRMATION
        pending = "final_order_confirmed" if final else "configuration_confirmed"
        stored_snapshot = state.final_order_snapshot if final else state.order_snapshot
        already_confirmed = state.final_order_confirmed if final else state.configuration_confirmed
        if already_confirmed or pending not in state.pending_confirmations or not stored_snapshot:
            raise ValueError("Malformed pending-confirmation state.")
        snapshot = deepcopy(stored_snapshot)
        working = deepcopy(state)
        confirmation = interpret_confirmation_response(current_message, conversation_history, working)
        if confirmation.intent == ConfirmationIntent.CHANGE_REQUESTED:
            extracted = extract_order_information(current_message, conversation_history, working)
            updated = apply_extracted_order_information(working, extracted)
            apply_order_creation_reentry(state, updated)
            # Intent alone never establishes a change. Compare writable values;
            # re-entry and all workflow consequences remain Controller-owned.
            fields = set(ExtractedOrderInformation.model_fields)
            if state.model_dump(include=fields) != updated.model_dump(include=fields):
                result = _execute_with_optional_store_path(updated, order_store_path=order_store_path)
                return updated, result
            working = updated
        elif confirmation.intent == ConfirmationIntent.CANCEL_REQUESTED:
            # Cancellation goes directly to Controller; no extraction or merge.
            pass
        evidence = ({"final_confirmation": confirmation, "final_confirmation_snapshot": snapshot}
                    if final else {"confirmation": confirmation, "confirmation_snapshot": snapshot})
        result = _execute_with_optional_store_path(working, order_store_path=order_store_path, **evidence)
        return working, result

    extracted = extract_order_information(current_message, conversation_history, state)
    updated_state = apply_extracted_order_information(state, extracted)
    apply_order_creation_reentry(state, updated_state)
    result = _execute_with_optional_store_path(updated_state, order_store_path=order_store_path)
    return updated_state, result