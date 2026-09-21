"""Session integration: real A3 rules/store, mocked language boundaries; opt-in live E2E."""

from copy import deepcopy
from functools import partial
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from entity.classification import ClassifierResult, MessageCategory
from entity.conversation import ConversationSession, PendingTurn, TurnStatus
from workflow.conversation_runtime import TurnFailure, process_customer_message, retry_pending_response
from agents.order_agent import process_order_creation_message
from entity.confirmation import ConfirmationInterpretation
from entity.extracted_order import ExtractedOrderInformation
from agents.root_agent import route_message, execute_route
from entity.routing import RoutingStatus
from entity.support import CustomerResponse, SupportAction, SupportKnowledgeContext, KnowledgeFact, TicketInformation
from agents.support_agent import compose_customer_response, compose_route_outcome
from workflow.confirmation_presentation import render_configuration_summary, render_provisional_order
from tests.test_order_creation_controller import complete_customer_state


def classification(category):
    return ClassifierResult(categories=[category], confidence=0.9, explanation='Customer intent.')


def fake_support_chat(**kwargs):
    payload = json.loads(kwargs['messages'][1]['content'])
    if 'requested_fields' in kwargs['format']['properties']:
        output = {'requested_fields': [item['field'] for item in payload['requested_information']],
                  'text': 'Please provide your {requested_information}.'}
    elif 'introduction' in kwargs['format']['properties']:
        final = payload['response_intent'] == 'REQUEST_FINAL_CONFIRMATION'
        output = dict(introduction='Please review the provisional order below.' if final else 'Please review the configuration below.',
                      confirmation_request='Please confirm you wish to place this provisional order.' if final else 'Please confirm the configuration or request corrections.')
    else:
        context = payload['response_context']
        intent = context['response_intent']
        if intent == 'REPORT_ORDER_CREATED':
            facts = context['allowed_facts']
            text = f"Your order {facts['order_id']} has been created. Status: {facts['order_status']}."
        elif intent == 'REQUEST_REQUIRED_INFORMATION':
            text = 'Please provide your name.'
        elif intent == 'RESPOND_SOCIAL':
            text = 'Hello!'
        elif intent == 'REQUEST_CLARIFICATION':
            text = 'Could you clarify what you mean?'
        elif intent == 'ANSWER_FROM_KNOWLEDGE':
            text = 'The sample finish is White.'
        elif intent == 'REPORT_TICKET_INFORMATION':
            ticket = context['allowed_facts']['ticket']
            text = f"Ticket {ticket['reference']} is {ticket['status']}."
        else:
            text = 'That information is unavailable here.'
        output = {'text': text}
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))


def complete_updates():
    state = complete_customer_state()
    return ExtractedOrderInformation.model_validate(
        state.model_dump(include=set(ExtractedOrderInformation.model_fields)), strict=True)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.session = ConversationSession(conversation_id='SUP-RUNTIME')
        self.support_patch = patch('agents.support_agent.chat', side_effect=fake_support_chat)
        self.support = self.support_patch.start()
        self.addCleanup(self.support_patch.stop)

    def test_support_routes_and_exact_once_history_with_prior_context(self):
        cases = [
            (MessageCategory.CASUAL_CHAT, None, SupportAction.RESPOND_CHAT),
            (MessageCategory.GENERAL_ENQUIRY, SupportKnowledgeContext(answer_facts=[KnowledgeFact(text='The sample finish is White.', source_reference='catalog')]), SupportAction.ANSWER_ENQUIRY),
            (MessageCategory.UNKNOWN_OTHER_INQUIRY, None, SupportAction.REQUEST_CLARIFICATION),
            (MessageCategory.SUPPORT_TICKET_FOLLOWUP, SupportKnowledgeContext(ticket_information=TicketInformation(reference='SUP-123', status='open')), SupportAction.FOLLOW_UP_SUPPORT_TICKET),
        ]
        session = self.session
        for index, (category, knowledge, action) in enumerate(cases):
            before = deepcopy(session)
            classifier = Mock(return_value=classification(category))
            order = Mock(side_effect=AssertionError('Order Agent must not run'))
            text = f'Customer turn {index}'
            result = process_customer_message(session, text, classifier=classifier,
                                              support_knowledge=knowledge, order_creation_processor=order)
            classifier.assert_called_once()
            self.assertEqual(classifier.call_args.kwargs['conversation_history'], before.history)
            self.assertEqual(classifier.call_args.kwargs['current_message'], text)
            self.assertEqual(result.execution.support_result.action, action)
            self.assertEqual(result.execution.routing.status, RoutingStatus.READY)
            self.assertTrue(result.execution.executed)
            self.assertIsNone(result.execution.business_result)
            self.assertEqual(session, before)
            self.assertEqual(result.session.history[:-2], session.history)
            self.assertEqual(result.session.history[-2].content, text)
            self.assertEqual(result.session.history[-1].content, result.customer_response.text)
            self.assertEqual([m.role for m in result.session.history], ['user', 'assistant'] * (index + 1))
            self.assertEqual(result.session.support_ticket_id, result.session.conversation_id)
            order.assert_not_called()
            session = result.session

    def test_unavailable_and_unresolved_have_support_responses_without_business_calls(self):
        for category in (MessageCategory.UPDATE_ORDER, MessageCategory.ORDER_ENQUIRY,
                         MessageCategory.QUOTATION_ENQUIRY, MessageCategory.PRODUCTION_STATUS_ENQUIRY,
                         MessageCategory.WORKFLOW_RESPONSE):
            processor = Mock(side_effect=AssertionError('No execution allowed'))
            result = process_customer_message(self.session, 'Customer request',
                classifier=lambda **_: classification(category), order_creation_processor=processor)
            self.assertFalse(result.execution.executed)
            self.assertIsNone(result.execution.business_result)
            self.assertIsNone(result.execution.support_result)
            self.assertIsNone(result.session.workflow_state)
            self.assertEqual(len(result.session.history), 2)
            self.assertTrue(result.customer_response.text)
            self.assertEqual(result.status, TurnStatus.UNRESOLVED if category == MessageCategory.WORKFLOW_RESPONSE else TurnStatus.UNAVAILABLE)
            processor.assert_not_called()
        self.support.assert_not_called()  # Deterministic Support presentation, not direct runtime prose.

    def test_route_presenter_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            compose_route_outcome('READY', 'CREATE_ORDER')

    def test_classification_execution_and_support_action_failures_do_not_mutate_session(self):
        before = deepcopy(self.session)
        error = ConnectionError('Service unavailable')
        with self.assertRaises(TurnFailure) as failure:
            process_customer_message(self.session, 'Hi', classifier=Mock(side_effect=error))
        self.assertIs(failure.exception.__cause__, error)
        self.assertEqual(failure.exception.phase, 'classification')
        for category, kwargs in ((MessageCategory.CREATE_ORDER, {'order_creation_processor': Mock(side_effect=error)}),
                                 (MessageCategory.CASUAL_CHAT, {'support_processor': Mock(side_effect=error)})):
            with self.assertRaises(TurnFailure) as failure:
                process_customer_message(self.session, 'Hello', classifier=lambda **_: classification(category), **kwargs)
            self.assertIsNone(failure.exception.pending_turn)
            self.assertEqual(failure.exception.phase, 'business execution')
        self.assertEqual(self.session, before)

    def test_invalid_session_identity_and_message(self):
        from entity.order_creation_state import OrderCreationState
        with self.assertRaises(ValueError):
            ConversationSession(conversation_id='x', workflow_state=OrderCreationState(conversation_id='y'))
        with self.assertRaises(ValueError):
            process_customer_message(self.session, ' ')

    def run_order(self, fail_after_create=False):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Path(temp.name) / 'orders.json'
        processor = Mock(wraps=partial(process_order_creation_message, order_store_path=store))
        categories = [MessageCategory.CREATE_ORDER] + [MessageCategory.WORKFLOW_RESPONSE] * 3
        classifier = Mock(side_effect=[classification(category) for category in categories])
        messages = ["I'd like to order an 8ft custom pool table.", 'Here are my details and configuration.',
                    "Yes, that configuration is correct.", "Yes, please place this order."]
        initial = self.session
        session = initial
        turns = []
        extracted = [ExtractedOrderInformation(table_size='8ft'), complete_updates(), ExtractedOrderInformation(), ExtractedOrderInformation()]
        with patch('agents.order_agent.extract_order_information', side_effect=extracted) as extract, \
             patch('agents.order_agent.interpret_confirmation_response', return_value=ConfirmationInterpretation(intent='CONFIRMED')) as interpret:
            for index, message in enumerate(messages):
                before = deepcopy(session)
                composer = Mock(side_effect=ConnectionError('Support unavailable')) if fail_after_create and index == 3 else compose_customer_response
                try:
                    turn = process_customer_message(session, message, classifier=classifier,
                        order_creation_processor=processor, response_composer=composer)
                except TurnFailure as exc:
                    self.assertTrue(fail_after_create and index == 3)
                    self.assertEqual(session, before)
                    return session, exc.pending_turn, store, processor, turns
                self.assertEqual(session, before)
                self.assertEqual(classifier.call_args.kwargs['conversation_history'], before.history)
                if index < 2:
                    self.assertEqual(extract.call_args.args[1], before.history)
                    self.assertEqual(extract.call_args.args[0], message)
                else:
                    self.assertEqual(interpret.call_args.args[1], before.history)
                self.assertEqual(len(turn.session.history), 2 * (index + 1))
                if index:
                    self.assertEqual(turn.session.workflow_state.workflow_id, session.workflow_state.workflow_id)
                session = turn.session
                turns.append(turn)
            self.assertEqual(interpret.call_count, 2)
            self.assertEqual(interpret.call_args.args[1], turns[-2].session.history)
        self.assertEqual(initial, self.session)
        return session, None, store, processor, turns

    def test_real_a3_multi_turn_create_and_both_artifacts_in_history(self):
        session, _, store, processor, turns = self.run_order()
        self.assertEqual(turns[0].execution.business_result.reason.value, 'MISSING_REQUIRED_INFORMATION')
        for turn, key, renderer in (
            (turns[1], 'configuration_snapshot', render_configuration_summary),
            (turns[2], 'final_order_snapshot', render_provisional_order),
        ):
            artifact = renderer(turn.execution.business_result.data[key])
            self.assertIn(artifact, turn.session.history[-1].content)
            self.assertEqual(turn.customer_response.text, turn.session.history[-1].content)
        final = turns[-1].execution.business_result
        self.assertEqual(final.result_status.value, 'SUCCESS')
        self.assertEqual(final.reason.value, 'ORDER_CREATED')
        self.assertEqual(session.workflow_state.status.value, 'COMPLETED')
        records = json.loads(store.read_text())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['order_id'], final.data['order_id'])
        # Normal Support turns retain terminal Wt without restarting it.
        after = process_customer_message(session, 'Thanks', classifier=lambda **_: classification(MessageCategory.CASUAL_CHAT))
        self.assertEqual(after.session.workflow_state, session.workflow_state)
        self.assertIsNot(after.session.workflow_state, session.workflow_state)

    def test_post_create_support_failure_retry_never_reexecutes_and_commits_once(self):
        session, pending, store, processor, turns = self.run_order(fail_after_create=True)
        self.assertIsInstance(pending, PendingTurn)
        self.assertEqual(len(session.history), 6)
        self.assertEqual(pending.execution.state.status.value, 'COMPLETED')
        self.assertEqual(pending.execution.business_result.reason.value, 'ORDER_CREATED')
        contents = store.read_bytes()
        self.assertEqual(len(json.loads(contents)), 1)
        before = deepcopy(session)
        with patch('workflow.conversation_runtime.classify_message', side_effect=AssertionError('No classification')), \
             patch('workflow.conversation_runtime.route_message', side_effect=AssertionError('No routing')), \
             patch('workflow.conversation_runtime.execute_route', side_effect=AssertionError('No dispatch')), \
             patch('workflow.order.order_creation_controller.execute_order_creation_workflow', side_effect=AssertionError('No controller')), \
             patch('workflow.order.order_creation_order_store.create_order', side_effect=AssertionError('No store')):
            with self.assertRaises(TurnFailure) as repeated:
                retry_pending_response(session, pending, response_composer=Mock(side_effect=ConnectionError('Still unavailable')))
            self.assertIs(repeated.exception.pending_turn, pending)
            result = retry_pending_response(session, pending)
        self.assertEqual(processor.call_count, 4)
        self.assertEqual(store.read_bytes(), contents)
        self.assertEqual(session, before)
        self.assertEqual(len(result.session.history), 8)
        self.assertEqual(result.session.workflow_state.status.value, 'COMPLETED')
        self.assertEqual(result.execution.business_result, pending.execution.business_result)
        with self.assertRaises(ValueError):
            retry_pending_response(session, pending)

    def test_pending_retry_rejects_different_session(self):
        session, pending, _, _, _ = self.run_order(fail_after_create=True)
        with self.assertRaises(ValueError):
            retry_pending_response(ConversationSession(conversation_id='different'), pending)
        modified = session.model_copy(deep=True)
        modified.history.pop()
        with self.assertRaises(ValueError):
            retry_pending_response(modified, pending)

    def test_existing_store_idempotent_replay_retains_same_order(self):
        _, _, store, _, turns = self.run_order()
        before = store.read_bytes()
        prior_final = turns[-2].session
        with patch('agents.order_agent.extract_order_information', return_value=ExtractedOrderInformation()), \
             patch('agents.order_agent.interpret_confirmation_response', return_value=ConfirmationInterpretation(intent='CONFIRMED')):
            replay = process_customer_message(prior_final, 'Yes, please place this order.',
                classifier=lambda **_: classification(MessageCategory.WORKFLOW_RESPONSE),
                order_creation_processor=partial(process_order_creation_message, order_store_path=store))
        self.assertFalse(replay.execution.business_result.data['created'])
        self.assertEqual(replay.execution.business_result.data['order_id'], turns[-1].execution.business_result.data['order_id'])
        self.assertEqual(store.read_bytes(), before)

    def test_cli_prints_customer_response_and_keeps_diagnostics_separate(self):
        from main import main
        result = process_customer_message(self.session, 'Hi', classifier=lambda **_: classification(MessageCategory.CASUAL_CHAT))
        with patch('sys.argv', ['main.py', '--conversation-id', 'SUP-RUNTIME']), \
             patch('builtins.input', side_effect=['Hi', '/quit']), \
             patch('main.process_customer_message', return_value=result) as process, \
             patch('builtins.print') as output:
            main()
        process.assert_called_once()
        self.assertIn(unittest.mock.call('Support > Hello!'), output.call_args_list)


@unittest.skipUnless(os.environ.get('ATS_RUN_LIVE_RUNTIME_TESTS') == '1', 'Live runtime test requires ATS_RUN_LIVE_RUNTIME_TESTS=1')
class LiveRuntimeTests(unittest.TestCase):
    def test_natural_customer_conversation_reaches_order_created(self):
        self.run_live_conversation(dense=False)

    @unittest.skipUnless(os.environ.get('ATS_RUN_DENSE_RUNTIME_DIAGNOSTIC') == '1',
                         'Dense diagnostic requires ATS_RUN_DENSE_RUNTIME_DIAGNOSTIC=1')
    def test_dense_customer_diagnostic(self):
        self.run_live_conversation(dense=True)

    def run_live_conversation(self, *, dense):
        # Customer vocabulary only: the BusinessResult chooses the next field.
        answers = {
            'customer_name': 'My name is Demo Customer.',
            'phone': 'My phone number is 0400000000.',
            'email': 'My email is customer@example.com.',
            'company_name': 'I have no company name.',
            'customer_instructions': 'I have no special instructions.',
            'delivery_address.address': 'My delivery street address is 1 Example Street.',
            'delivery_address.city': 'My delivery city is Melbourne.',
            'delivery_address.state': 'My delivery state is VIC.',
            'delivery_address.postcode': 'My delivery postcode is 3000.',
            'delivery_address.country': 'My delivery country is Australia.',
            'room_size': 'My room is 5.2 metres by 4.0 metres.',
            'product_model': "I'd like the Odyssey model.",
            'table_size': "I'd like an 8ft table.",
            'timber': "I'd like Tassie Oak timber.",
            'timber_painting': "I'd like White timber painting.",
            'felt_color': "I'd like Grey cloth.",
            'bracket': "I'd like the Standard rubber bracket.",
            'top_profile': "I'd like the Waterfall top profile.",
            'quantity': "I'd like one table.",
        }
        messages = [
            "I'd like to order an 8ft custom pool table.",
            "I'd like one 8ft Odyssey table, Tassie Oak timber, White timber finish, Grey cloth, Standard rubber bracket and Waterfall top profile. My room is 5.2m x 4.0m. My name is Demo Customer, phone 0400000000, email customer@example.com. Deliver to 1 Example Street, Melbourne VIC 3000, Australia. I have no company name or special instructions.",
            "Yes, that configuration is correct.",
            "Yes, please place this order.",
        ]
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / 'orders.json'
            processor = partial(process_order_creation_message, order_store_path=store)
            session = ConversationSession(conversation_id='SUP-LIVE-RUNTIME')
            message = messages[0] if dense else "I'd like to order an 8ft Odyssey pool table."
            expected_history = []
            observed = {'configuration': False, 'provisional_order': False}
            confirmed_snapshot = None
            # Generous test runaway safeguard, never an order business outcome.
            for turn_index in range(1, 101):
                print('\nCustomer:', message, flush=True)
                # Test-only observation of real boundaries. Every wrapper calls the
                # original exactly once and returns its result without modification.
                from workflow.classifier import classify_message as real_classifier
                from agents.support_agent import chat as real_support_chat
                from agents.support_agent import _validate_response_grounding as real_validate
                from workflow.order.order_creation_extraction import chat as real_extraction_chat
                from agents.order_agent import extract_order_information as real_extract
                from agents.order_agent import apply_extracted_order_information as real_apply
                from agents.order_agent import apply_order_creation_reentry as real_reentry
                from agents.order_agent import execute_order_creation_workflow as real_execute
                trace = {
                    'turn_index': turn_index, 'current_message': message,
                    'prior_history': [item.model_dump(mode='json') for item in session.history],
                    'classification': None, 'routing': None, 'business_result': None,
                    'support_calls': [], 'grounding_checks': [],
                    'order_data_events': [],
                }
                before_session = deepcopy(session)

                def record_order_event(boundary, **data):
                    # Detached JSON values are diagnostic evidence only.
                    trace['order_data_events'].append({'boundary': boundary, **deepcopy(data)})

                def capture_processor(current_message, conversation_history, state):
                    record_order_event('before_order_agent', wt=state.model_dump(mode='json'))
                    return processor(current_message, conversation_history, state)

                def capture_extraction_chat(*args, **kwargs):
                    value = real_extraction_chat(*args, **kwargs)
                    record_order_event('raw_extraction_output', raw=value.message.content)
                    return value

                def capture_extract(*args, **kwargs):
                    try:
                        value = real_extract(*args, **kwargs)
                    except Exception as exc:
                        record_order_event('extraction_error', error_type=type(exc).__name__, error=str(exc))
                        raise
                    record_order_event('validated_extraction',
                        extracted=value.model_dump(mode='json'),
                        supplied_updates=value.model_dump(mode='json', exclude_unset=True),
                        fields_set=sorted(value.model_fields_set))
                    return value

                def capture_apply(state, extracted):
                    value = real_apply(state, extracted)
                    record_order_event('after_merge', wt=value.model_dump(mode='json'))
                    return value

                def capture_reentry(previous_state, updated_state):
                    before = updated_state.model_dump(mode='json')
                    value = real_reentry(previous_state, updated_state)
                    after = updated_state.model_dump(mode='json')
                    record_order_event('reentry', before=before, after=after,
                        changed_fields={key: {'before': before[key], 'after': after[key]}
                                        for key in before if before[key] != after[key]})
                    return value

                def capture_execute(state, **kwargs):
                    record_order_event('before_controller', wt=state.model_dump(mode='json'))
                    value = real_execute(state, **kwargs)
                    record_order_event('controller_result', business_result=value.model_dump(mode='json'))
                    return value

                def capture_classifier(**kwargs):
                    value = real_classifier(**kwargs)
                    trace['classification'] = value.model_dump(mode='json')
                    return value

                def capture_route(*args, **kwargs):
                    value = route_message(*args, **kwargs)
                    trace['routing'] = value.model_dump(mode='json')
                    return value

                def capture_composition(business_result, **kwargs):
                    trace['business_result'] = business_result.model_dump(mode='json')
                    return compose_customer_response(business_result, **kwargs)

                def capture_chat(*args, **kwargs):
                    value = real_support_chat(*args, **kwargs)
                    raw = value.message.content
                    trace['support_calls'].append({
                        'request_payload': json.loads(kwargs['messages'][1]['content']),
                        'raw_output_before_validation': raw,
                        'raw_output_numeric_tokens': sorted(set(re.findall(r'\d+(?:[.,]\d+)*', raw or ''))),
                    })
                    # Emit before validation so rejected output is retained as
                    # diagnostic evidence, never as a customer response/history.
                    print('RAW_SUPPORT_BEFORE_VALIDATION:', repr(raw), flush=True)
                    return value

                def capture_validation(response, context):
                    tokens = lambda text: sorted(set(re.findall(r'\d+(?:[.,]\d+)*', text)))
                    output_tokens = tokens(response.text)
                    allowed_tokens = tokens(json.dumps(context.allowed_facts, ensure_ascii=False))
                    check = {
                        'response_context': context.model_dump(mode='json'),
                        'response_text': response.text,
                        'response_text_numeric_tokens': output_tokens,
                        'allowed_numeric_tokens': allowed_tokens,
                        'numeric_difference': sorted(set(output_tokens) - set(allowed_tokens)),
                        'decision': None,
                    }
                    trace['grounding_checks'].append(check)
                    try:
                        value = real_validate(response, context)
                    except Exception as exc:
                        check['decision'] = 'REJECTED'
                        check['error'] = {'type': type(exc).__name__, 'message': str(exc)}
                        raise
                    check['decision'] = 'ACCEPTED'
                    return value

                try:
                    with patch('workflow.conversation_runtime.route_message', side_effect=capture_route), \
                         patch('workflow.order.order_creation_extraction.chat', side_effect=capture_extraction_chat), \
                         patch('agents.order_agent.extract_order_information', side_effect=capture_extract), \
                         patch('agents.order_agent.apply_extracted_order_information', side_effect=capture_apply), \
                         patch('agents.order_agent.apply_order_creation_reentry', side_effect=capture_reentry), \
                         patch('agents.order_agent.execute_order_creation_workflow', side_effect=capture_execute), \
                         patch('agents.support_agent.chat', side_effect=capture_chat), \
                         patch('agents.support_agent._validate_response_grounding', side_effect=capture_validation):
                        result = process_customer_message(session, message, order_creation_processor=capture_processor,
                            classifier=capture_classifier, response_composer=capture_composition)
                    business = result.execution.business_result
                    trace['support_response'] = result.customer_response.text
                    trace['wt'] = (result.session.workflow_state.model_dump(mode='json')
                                   if result.session.workflow_state is not None else None)
                    trace['confirmation_artifacts'] = {'configuration': False, 'provisional_order': False}
                    self.assertIsNotNone(business, 'Unexpected route without order BusinessResult')
                    if business.reason.value == 'CONFIGURATION_CONFIRMATION_REQUIRED':
                        artifact = render_configuration_summary(business.data['configuration_snapshot'])
                        trace['confirmation_artifacts']['configuration'] = artifact in result.customer_response.text
                        self.assertIn(artifact, result.customer_response.text)
                        observed['configuration'] = True
                    elif business.reason.value == 'FINAL_CONFIRMATION_REQUIRED':
                        artifact = render_provisional_order(business.data['final_order_snapshot'])
                        trace['confirmation_artifacts']['provisional_order'] = artifact in result.customer_response.text
                        self.assertIn(artifact, result.customer_response.text)
                        observed['provisional_order'] = True
                        confirmed_snapshot = deepcopy(business.data['final_order_snapshot'])
                    expected_history.extend([
                        {'role': 'user', 'content': message},
                        {'role': 'assistant', 'content': result.customer_response.text},
                    ])
                    self.assertEqual([item.model_dump(mode='json') for item in result.session.history], expected_history)
                    session = result.session
                except TurnFailure as exc:
                    trace['turn_failure'] = {'phase': exc.phase, 'message': str(exc),
                                             'cause': str(exc.__cause__)}
                    if exc.pending_turn is not None:
                        pending_before = deepcopy(exc.pending_turn.model_dump())
                        trace['pending_turn'] = exc.pending_turn.model_dump(mode='json')
                        trace['pending_classification'] = exc.pending_turn.classification.model_dump(mode='json')
                        trace['pending_routing'] = exc.pending_turn.execution.routing.model_dump(mode='json')
                        trace['pending_business_result'] = exc.pending_turn.execution.business_result.model_dump(mode='json')
                        self.assertEqual(exc.pending_turn.model_dump(), pending_before)
                    self.assertEqual(session, before_session)
                    trace['committed_session_unchanged'] = True
                    raise
                finally:
                    trace['session_after_turn'] = session.model_dump(mode='json')
                    trace['persisted_orders'] = json.loads(store.read_text()) if store.exists() else []
                    trace['observed_artifacts'] = observed.copy()
                    print('TURN_DIAGNOSTIC_JSON:', json.dumps(trace, ensure_ascii=False), flush=True)
                print('Classification:', result.classification.categories[0].value, flush=True)
                print('Support:', result.customer_response.text, flush=True)
                if business.result_status.value == 'SUCCESS':
                    break
                self.assertEqual(business.result_status.value, 'NEEDS_USER_INPUT',
                                 'Unexpected business outcome; stopping without retuning')
                if dense:
                    self.assertLess(turn_index, len(messages), 'Dense diagnostic messages exhausted')
                    message = messages[turn_index]
                elif business.reason.value == 'CONFIGURATION_CONFIRMATION_REQUIRED':
                    message = 'Yes, I confirm this configuration.'
                elif business.reason.value == 'FINAL_CONFIRMATION_REQUIRED':
                    message = 'Yes, I confirm the final order and would like to place it.'
                elif business.reason.value in {'MISSING_REQUIRED_INFORMATION',
                        'UNSUPPORTED_CONFIGURATION_VALUE', 'ROOM_SIZE_UNSUITABLE'}:
                    self.assertTrue(business.required_input, 'No authoritative requested fields')
                    self.assertTrue(all(field in answers for field in business.required_input),
                                    f'Unsupported customer fixture request: {business.required_input}')
                    message = ' '.join(answers[field] for field in business.required_input)
                else:
                    self.fail(f'Unexpected BusinessResult reason: {business.reason.value}')
            else:
                self.fail('TEST_RUNTIME_RUNAWAY: 100-turn safeguard reached; not an order business failure')
            self.assertEqual(result.execution.business_result.result_status.value, 'SUCCESS')
            self.assertEqual(result.execution.business_result.reason.value, 'ORDER_CREATED')
            self.assertEqual(session.workflow_state.status.value, 'COMPLETED')
            records = json.loads(store.read_text())
            self.assertEqual(len(records), 1)
            self.assertTrue(business.data['order_id'])
            self.assertEqual(records[0]['order_id'], business.data['order_id'])
            self.assertEqual(records[0]['order_status'], session.workflow_state.order_status)
            self.assertIsNotNone(confirmed_snapshot)
            self.assertEqual(records[0]['order'], confirmed_snapshot)
            self.assertTrue(all(observed.values()))
            self.assertEqual(len(session.history), 2 * turn_index)
            print('LIVE_FINAL_JSON:', json.dumps({
                'total_customer_turns': turn_index, 'final_session': session.model_dump(mode='json'),
                'order_id': business.data['order_id'], 'persisted_order_count': len(records),
                'observed_artifacts': observed, 'payload_matches_confirmed_snapshot': True,
            }), flush=True)


if __name__ == '__main__':
    unittest.main()
