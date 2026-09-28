"""CLI adapter for the in-memory ATS conversation runtime."""

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from uuid import uuid4

from entity.conversation import ConversationSession
from workflow.conversation_runtime import TurnFailure, process_customer_message, retry_pending_response
from agents.order_agent import process_order_creation_message
from workflow.order.order_creation_order_store import DEFAULT_ORDER_STORE_PATH, load_orders
from evaluation.simulator import create_simple_simulator
from tools.knowledge_tool import load_product_prices_as_knowledge

logger = logging.getLogger("ats_support_cli")


EXPERIMENT_LOG_DIR = Path("experiment_logs")


def save_experiment_log(*, session, scenario_id, order_store_path, final_status, final_reason):
    """Persist the final order(s) and full dialogue history for a single run.

    Each run is saved independently under experiment_logs/<scenario>_run<NNN>.json,
    where <NNN> is an incrementing per-scenario run index.
    """
    EXPERIMENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_pattern = re.compile(rf"^{re.escape(scenario_id)}_run(\d+)\.json$")
    max_index = 0
    for existing in EXPERIMENT_LOG_DIR.glob(f"{scenario_id}_run*.json"):
        match = run_pattern.match(existing.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    run_index = max_index + 1
    log_path = EXPERIMENT_LOG_DIR / f"{scenario_id}_run{run_index:03d}.json"

    history = [message.model_dump() for message in session.history]

    orders = []
    try:
        for record in load_orders(order_store_path):
            if record.conversation_id == session.conversation_id:
                orders.append(json.loads(record.model_dump_json()))
    except Exception as exc:  # noqa: BLE001 - read failure must not block shutdown
        logger.warning("Could not read order store for experiment log: %s", exc)

    payload = {
        "scenario_id": scenario_id,
        "run_index": run_index,
        "conversation_id": session.conversation_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
        "final_reason": final_reason,
        "orders": orders,
        "history": history,
    }
    log_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Experiment log saved to %s", log_path)
    print(f"\nExperiment log saved: {log_path} (scenario={scenario_id}, run={run_index})")
    return log_path



def run_scenario_conversation(*, scenario_id, order_store_path=DEFAULT_ORDER_STORE_PATH,
                              conversation_id=None, max_turns=100, debug=False,
                              manual=False, verbose=True):
    """Run one full scenario conversation and persist the run log.

    When ``manual`` is False the customer is driven by the simulator; otherwise it
    reads from stdin. Returns the path to the written ``experiment_logs`` run JSON.
    """
    session = ConversationSession(conversation_id=conversation_id or 'SUP-' + uuid4().hex[:12].upper())
    if verbose:
        print(f'Conversation / support ticket: {session.support_ticket_id}')
    logger.info('Session initialised: %s', session.support_ticket_id)
    processor = partial(process_order_creation_message, order_store_path=order_store_path)
    support_knowledge = load_product_prices_as_knowledge()
    pending = None
    final_status = "UNRESOLVED"
    final_reason = None
    simulator = create_simple_simulator(f"evaluation/scenarios/{scenario_id}.json")
    logger.info('Scenario Id is: %s', scenario_id)
    turn = 0
    while turn < max_turns:
        turn += 1
        logger.info('--- Turn %d start ---', turn)
        try:
            if not manual:
                message = simulator(session.history)
            else:
                message = input(f"\n Your message {turn}: \n ")
        except (EOFError, KeyboardInterrupt):
            logger.info('Input stream ended (EOF / KeyboardInterrupt)')
            final_status = "INTERRUPTED"
            final_reason = "EOF / KeyboardInterrupt"
            if verbose:
                print()
            break
        if message.strip() == '/quit':
            logger.info('User issued /quit command')
            final_status = "INTERRUPTED"
            final_reason = "user issued /quit"
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
        if verbose:
            print(f"\n{'='*60}")
            print(f"[Customer Turn {turn}]")
            print(f"{'='*60}")
            print(message)
        logger.info('Customer message (turn %d): %s', turn, message)
        if debug:
            result = (retry_pending_response(session, pending) if pending else
                      process_customer_message(session, message, order_creation_processor=processor,
                                              support_knowledge=support_knowledge))
        else:
            try:
                result = (retry_pending_response(session, pending) if pending else
                          process_customer_message(session, message, order_creation_processor=processor,
                                                  support_knowledge=support_knowledge))
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

        # Detailed per-turn trace: classifier, order agent, support agent, workflow.
        execution = getattr(result, "execution", None)
        if execution is not None:
            logger.info('Classification: %s', result.classification.model_dump_json())
            logger.info('Routing: %s', execution.routing.model_dump_json())
            if execution.business_result is not None:
                logger.info('Order agent outcome: %s', execution.business_result.model_dump_json())
            if execution.support_result is not None:
                logger.info('Support agent outcome: %s', execution.support_result.model_dump_json())
        if session.workflow_state is not None:
            logger.info('Workflow: stage=%s status=%s',
                        session.workflow_state.current_stage.value,
                        session.workflow_state.status.value)
        if debug:
            print(f"\n[Trace turn {turn}] classification: {result.classification.model_dump_json()}")
            print(f"[Trace turn {turn}] routing: {execution.routing.model_dump_json()}")
            if execution is not None and execution.business_result is not None:
                print(f"[Trace turn {turn}] order_agent: {execution.business_result.model_dump_json()}")
            if execution is not None and execution.support_result is not None:
                print(f"[Trace turn {turn}] support_agent: {execution.support_result.model_dump_json()}")
            if session.workflow_state is not None:
                print(f"[Trace turn {turn}] workflow: stage={session.workflow_state.current_stage.value} "
                      f"status={session.workflow_state.status.value}")

        if verbose:
            logger.info('Agent response (turn %d): %s', turn, result.customer_response.text)
            print(f"\n{'='*60}")
            print(f"[Agent Turn {turn}]")
            print(f"{'='*60}")
            print(result.customer_response.text)
        outcome = result.execution.business_result or result.execution.support_result
        if outcome is not None and hasattr(outcome, 'result_status'):
            if outcome.result_status.value in ('SUCCESS', 'FAILURE', 'CANCELLED'):
                final_status = outcome.result_status.value
                final_reason = outcome.reason.value if hasattr(outcome, 'reason') else None
                logger.info('Conversation ended – status=%s reason=%s', final_status, final_reason or 'N/A')
                if verbose:
                    print(f"\n{'='*60}")
                    print(f"Conversation ended with status: {final_status}, because: {final_reason or 'N/A'}")
                    print(f"{'='*60}")
                break
        logger.info('Turn %d completed – no terminal outcome', turn)
    logger.info('Progress completed')
    return save_experiment_log(
        session=session,
        scenario_id=scenario_id,
        order_store_path=order_store_path,
        final_status=final_status,
        final_reason=final_reason,
    )


def main():
    parser = argparse.ArgumentParser(description="ATS customer-support CLI")
    parser.add_argument('--conversation-id', default=None)
    parser.add_argument('--order-store-path', type=Path, default=DEFAULT_ORDER_STORE_PATH)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--manual', action='store_true')
    parser.add_argument('--scenario-id', default='S01')
    parser.add_argument('--log-file', type=Path, default=None,
                        help='Path to log file (default: logs/<scenario-id>.log)')
    parser.add_argument('--max-turns', type=int, default=100)
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

    run_scenario_conversation(
        scenario_id=args.scenario_id,
        order_store_path=args.order_store_path,
        conversation_id=args.conversation_id,
        max_turns=args.max_turns,
        debug=args.debug,
        manual=args.manual,
        verbose=True,
    )


if __name__ == '__main__':
    main()