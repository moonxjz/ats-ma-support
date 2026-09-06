"""Order Agent internal orchestration for already-routed ORDER_CREATE_WF turns."""

from business_result import BusinessResult
from order_creation_controller import execute_order_creation_workflow
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

    Technical exceptions propagate unchanged. In particular, reaching the current
    unimplemented CONFIGURATION_CONFIRMATION boundary raises NotImplementedError
    before return: the caller does not receive the internally advanced working
    copy, and its original state remains unchanged. No alternate result or
    exception payload is provided for this temporary implementation boundary.
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
    result = execute_order_creation_workflow(updated_state)
    return updated_state, result
