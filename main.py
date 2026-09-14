"""CLI adapter for the in-memory ATS conversation runtime."""

import argparse
from functools import partial
from pathlib import Path
from uuid import uuid4

from conversation_runtime import ConversationSession, TurnFailure, process_customer_message, retry_pending_response
from order_agent import process_order_creation_message
from order_creation_order_store import DEFAULT_ORDER_STORE_PATH
from evaluation.simulator import create_simple_simulator



def main():
    parser = argparse.ArgumentParser(description="ATS customer-support CLI")
    parser.add_argument('--conversation-id', default=None)
    parser.add_argument('--order-store-path', type=Path, default=DEFAULT_ORDER_STORE_PATH)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--scenario-path', default='evaluation/scenarios/S01.json')
    args = parser.parse_args()
    session = ConversationSession(conversation_id=args.conversation_id or 'SUP-' + uuid4().hex[:12].upper())
    processor = partial(process_order_creation_message, order_store_path=args.order_store_path)
    pending = None
    simulator = create_simple_simulator(args.scenario_path)
    print(f'Conversation / support ticket: {session.support_ticket_id}')
    turn = 0
    while True:
        turn += 1
        try:
            message = simulator(session.history)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.strip() == '/quit':
            break
        if not message.strip():
            continue
        if not pending and message.strip() == '/retry':
            print('Diagnostic: there is no pending response to retry.')
            continue
        if pending and message.strip() != '/retry':
            print('Diagnostic: resolve the pending response with /retry before entering a new turn.')
            continue
        print(f"\n{'='*60}")
        print(f"[Customer Turn {turn}]")
        print(f"{'='*60}")
        print(message)
        try:
            result = (retry_pending_response(session, pending) if pending else
                        process_customer_message(session, message, order_creation_processor=processor))
        except TurnFailure as exc:
            pending = exc.pending_turn
            print(f'Diagnostic: {exc}')
            if args.debug:
                print(f'Cause: {exc.__cause__!r}')
            if exc.phase == 'business execution':
                print('Diagnostic: execution did not return an authoritative outcome. No automatic replay will be attempted.')
                break
            continue
        session = result.session
        pending = None
        print(f"\n{'='*60}")
        print(f"[Agent Turn {turn}]")
        print(f"{'='*60}")
        print(result.customer_response.text)

        outcome = result.execution.business_result or result.execution.support_result
        if outcome is not None and hasattr(outcome, 'result_status'):
            if outcome.result_status.value in ('SUCCESS', 'FAILURE', 'CANCELLED'):
                print(f"\n{'='*60}")
                print(f"Conversation ended with status: {outcome.result_status.value}, because: {outcome.reason.value if hasattr(outcome, 'reason') else 'N/A'}")
                print(f"{'='*60}")
                break
        if args.debug:
            print('Classification:', result.classification.model_dump_json())
            print('Routing:', result.execution.routing.model_dump_json())
            outcome = result.execution.business_result or result.execution.support_result
            if outcome is not None:
                print('Outcome:', outcome.model_dump_json())
            if session.workflow_state is not None:
                print('Workflow:', session.workflow_state.current_stage.value, session.workflow_state.status.value)
        


if __name__ == '__main__':
    main()
