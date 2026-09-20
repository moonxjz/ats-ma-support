"""CLI adapter for the in-memory ATS conversation runtime."""

import argparse
import logging
from functools import partial
from pathlib import Path
from uuid import uuid4

from entity.conversation import ConversationSession
from workflow.conversation_runtime import TurnFailure, process_customer_message, retry_pending_response
from agents.order_agent import process_order_creation_message
from workflow.order.order_creation_order_store import DEFAULT_ORDER_STORE_PATH
from evaluation.simulator import create_simple_simulator
from tools.knowledge_tool import load_product_prices_as_knowledge

logger = logging.getLogger("ats_support_cli")



def main():
    parser = argparse.ArgumentParser(description="ATS customer-support CLI")
    parser.add_argument('--conversation-id', default=None)
    parser.add_argument('--order-store-path', type=Path, default=DEFAULT_ORDER_STORE_PATH)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--manual', action='store_true')
    parser.add_argument('--scenario-id', default='S01')
    parser.add_argument('--log-file', type=Path, default=None,
                        help='Path to log file (default: logs/<scenario-id>.log)')
    args = parser.parse_args()

    log_level = getattr(logging, 'INFO')
    log_file = Path('logs') / (args.log_file or f'{args.scenario_id}.log')
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            # logging.StreamHandler(),
        ],
    )

    session = ConversationSession(conversation_id=args.conversation_id or 'SUP-' + uuid4().hex[:12].upper())
    logger.info('Session initialised: %s', session.support_ticket_id)
    processor = partial(process_order_creation_message, order_store_path=args.order_store_path)
    pending = None
    simulator = create_simple_simulator(f"evaluation/scenarios/{args.scenario_id}.json")
    logger.info('Scenario Id is: %s', args.scenario_id)
    print(f'Conversation / support ticket: {session.support_ticket_id}')
    logger.info(f'Conversation / support ticket: {session.support_ticket_id}')
    turn = 0
    while True:
        turn += 1
        logger.info('--- Turn %d start ---', turn)
        try:
            if not args.manual:
                message = simulator(session.history)
            else:
                message = input(f"\n Your message {turn}: \n ")
        except (EOFError, KeyboardInterrupt):
            logger.info('Input stream ended (EOF / KeyboardInterrupt)')
            print()
            break
        if message.strip() == '/quit':
            logger.info('User issued /quit command')
            break
        if not message.strip():
            logger.debug('Empty message, skipping')
            continue
        if not pending and message.strip() == '/retry':
            logger.warning('/retry issued but no pending response exists')
            print('Diagnostic: there is no pending response to retry.')
            continue
        if pending and message.strip() != '/retry':
            logger.warning('Pending response unresolved; user must /retry before new input')
            print('Diagnostic: resolve the pending response with /retry before entering a new turn.')
            continue
        print(f"\n{'='*60}")
        print(f"[Customer Turn {turn}]")
        print(f"{'='*60}")
        print(message)
        logger.info('Customer message (turn %d): %s', turn, message)
        if args.debug:
            result = (retry_pending_response(session, pending) if pending else
                                process_customer_message(session, message, order_creation_processor=processor, support_knowledge=load_product_prices_as_knowledge()))
        else:
            try:
                result = (retry_pending_response(session, pending) if pending else
                            process_customer_message(session, message, order_creation_processor=processor))
            except TurnFailure as exc:
                pending = exc.pending_turn
                logger.error('TurnFailure at turn %d (phase=%s): %s', turn, exc.phase, exc)
                print(f'Diagnostic: {exc}')
                if exc.phase == 'business execution':
                    logger.critical('Business execution failure – terminating conversation')
                    print('Diagnostic: execution did not return an authoritative outcome. No automatic replay will be attempted.')
                    break
                continue
        session = result.session
        pending = None
        logger.info('Agent response (turn %d): %s', turn, result.customer_response.text)
        print(f"\n{'='*60}")
        print(f"[Agent Turn {turn}]")
        print(f"{'='*60}")
        print(result.customer_response.text)

        outcome = result.execution.business_result or result.execution.support_result
        if outcome is not None and hasattr(outcome, 'result_status'):
            if outcome.result_status.value in ('SUCCESS', 'FAILURE', 'CANCELLED'):
                logger.info('Conversation ended – status=%s reason=%s',
                            outcome.result_status.value,
                            outcome.reason.value if hasattr(outcome, 'reason') else 'N/A')
                print(f"\n{'='*60}")
                print(f"Conversation ended with status: {outcome.result_status.value}, because: {outcome.reason.value if hasattr(outcome, 'reason') else 'N/A'}")
                print(f"{'='*60}")
                break
        logger.info('Turn %d completed – no terminal outcome', turn)
        if args.debug:
            logger.info('Classification: %s', result.classification.model_dump_json())
            logger.info('Routing: %s', result.execution.routing.model_dump_json())
            outcome = result.execution.business_result or result.execution.support_result
            if outcome is not None:
                logger.info('Outcome: %s', outcome.model_dump_json())
            if session.workflow_state is not None:
                logger.info('Workflow: stage=%s status=%s',
                             session.workflow_state.current_stage.value,
                             session.workflow_state.status.value)
            # print('Classification:', result.classification.model_dump_json())
            # print('Routing:', result.execution.routing.model_dump_json())
            # outcome = result.execution.business_result or result.execution.support_result
            # if outcome is not None:
            #     print('Outcome:', outcome.model_dump_json())
            # if session.workflow_state is not None:
            #     print('Workflow:', session.workflow_state.current_stage.value, session.workflow_state.status.value)

    logger.info('Progress completed')
        


if __name__ == '__main__':
    main()