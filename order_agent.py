"""Order Agent internal orchestration for already-routed ORDER_CREATE_WF turns."""

from business_result import BusinessResult
from order_creation_controller import apply_order_creation_reentry, execute_order_creation_workflow
from order_creation_extraction import ConversationMessage, extract_order_information
from order_creation_state import OrderCreationState
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
    configurations return a confirmation request; acceptance remains unimplemented.
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
    result = execute_order_creation_workflow(updated_state)
    return updated_state, result
