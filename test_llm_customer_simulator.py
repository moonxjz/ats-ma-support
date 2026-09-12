"""Mock-only CS2 decisions. No scenario runner or live inference."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from evaluation import llm_customer_simulator as s
from evaluation import llm_public_evidence as e
from evaluation.customer_simulator import CustomerSimulatorInput, CustomerSimulatorState, PendingDiscovery
from evaluation.public_observation import PublicMessage, RequestedFieldEvidence
from evaluation.scenario_loader import load_scenarios
from evaluation.scenario_spec import CONFIGURATION_FIELDS
from test_public_observation import configuration_text, final_text


def ref(text, index=1, quote=None):
    start = 0 if quote is None else text.index(quote)
    return e.reference(text, index, start, None if quote is None else start + len(quote)).model_dump(mode='json', include={'message_index', 'quote'})


def model(proposal):
    return Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({'proposal': proposal})), done=True, done_reason='stop'))


def turn(*actions):
    return {'kind': 'CUSTOMER_TURN', 'actions': list(actions)}


def info(text, field='email', value='customer1@example.com', index=1, kind='TEXT'):
    v = {'kind': kind}
    if kind != 'ABSENT':
        v['value'] = value
    return {'kind': 'PROVIDE_INFORMATION', 'items': [{'field': field, 'value': v, 'request_evidence': [ref(text, index)]}]}


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.customers = tuple(x.customer_view() for x in load_scenarios(check_repository=False))

    def inputs(self, text, scenario=0):
        customer = self.customers[scenario]
        initial = s.step(CustomerSimulatorInput(customer=customer, state=CustomerSimulatorState(), public_history=()), chat_fn=Mock())
        return CustomerSimulatorInput(customer=customer, state=initial.state,
            public_history=(PublicMessage(role='user', text=initial.decision.message), PublicMessage(role='assistant', text=text)))

    def advance(self, inputs, result, support):
        return CustomerSimulatorInput(customer=inputs.customer, state=result.state, public_history=inputs.public_history +
            (PublicMessage(role='user', text=result.decision.message), PublicMessage(role='assistant', text=support)))

    def reject(self, inputs, proposal, code='GUARD_REJECTED'):
        before = inputs.model_dump_json()
        chat = model(proposal)
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(inputs, chat_fn=chat)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(inputs.model_dump_json(), before)
        chat.assert_called_once()

    def historical_raw(self):
        return '{"proposal":{"kind":"CUSTOMER_TURN","actions":[{"kind":"CONFIRM_CONFIGURATION","artifact":{"message_index":0,"start":0,"end":245,"quote":" I\'d like to order one 8ft Odyssey pool table with a Waterfall top rail profile and brass bracket. For timber, I\'d like   Tassie Oak in Natural finish, with Blue felt."},"approval_request":{"message_index":1,"start":0,"end":34,"quote":"Could you please provide your full name?"}}]}}'

    def test_historical_pilot_input_and_old_coordinate_failure(self):
        from hashlib import sha256
        from pydantic import ValidationError
        inputs = self.inputs('Could you please provide your full name?')
        original = json.dumps(inputs.model_dump(mode='json'), ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False)
        self.assertEqual(sha256(original.encode()).hexdigest(),
                         'b1cc85dafb636118edfeda7468a4dfc9daa53dcdbeaa3f1b04dbf91948bb6f73')
        # The canonical-reference envelope retains the old span validators.
        with self.assertRaises(ValidationError) as caught:
            s.GroundedProposalEnvelope.model_validate_json(self.historical_raw())
        self.assertEqual(sum('Evidence span must exactly cover its quote' in error['msg']
                             for error in caught.exception.errors()), 2)
        with self.assertRaises(s.CS2Failure) as caught:
            s.parse_proposal(self.historical_raw())
        self.assertEqual(caught.exception.code, 'INVALID_PROPOSAL')

    def test_historical_semantics_still_rejected_with_selectors(self):
        proposal = json.loads(self.historical_raw())['proposal']
        action = proposal['actions'][0]
        for name in ('artifact', 'approval_request'):
            value = action[name]
            action[name] = {k: value[k] for k in ('message_index', 'quote')}
        inputs = self.inputs('Could you please provide your full name?')
        # Both quotes occur exactly once, but the artifact belongs to the customer.
        for value in action.values():
            if isinstance(value, dict):
                self.assertEqual(inputs.public_history[value['message_index']].text.count(value['quote']), 1)
        self.reject(inputs, proposal)
        action['artifact'] = action['approval_request']
        # Now both references ground; an ordinary request is still no artifact.
        capture = s.ProposalDiagnostics()
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(inputs, chat_fn=model(proposal), diagnostics=capture)
        self.assertEqual(caught.exception.code, 'GUARD_REJECTED')
        self.assertIn('Not the complete presented artifact', str(caught.exception))
        self.assertEqual(len(capture.grounded_evidence), 2)

    def test_historical_correct_name_selector_and_adapter_diagnostics(self):
        from evaluation.llm_simulator_integration import make_llm_simulator
        text = 'Could you please provide your full name?'
        chat = model(turn(info(text, 'customer_name', 'Demo1 Customer1')))
        simulator = make_llm_simulator(chat_fn=chat)
        result = simulator(self.inputs(text))
        self.assertEqual(result.decision.message, 'My name is Demo1 Customer1.')
        diagnostics = simulator.take_diagnostics()
        self.assertEqual(diagnostics.guard_outcome, 'ACCEPTED')
        self.assertEqual([(r.message_index, r.start, r.end) for r in diagnostics.grounded_evidence], [(1, 0, 40)])
        self.assertNotIn('start', json.loads(diagnostics.parsed_proposal_json)['actions'][0]['items'][0]['request_evidence'][0])
        self.assertEqual(diagnostics.grounded_evidence[0].quote, text)
        chat.assert_called_once()

    def test_partial_grounding_retained_without_retry(self):
        from evaluation.llm_simulator_integration import make_llm_simulator
        text = 'Please provide your full name and email.'
        action = info(text, 'customer_name', 'Demo1 Customer1')
        action['items'].append(info(text, 'email')['items'][0])
        action['items'][1]['request_evidence'][0]['quote'] = 'Not present.'
        inputs = self.inputs(text)
        outcomes = []
        for capture in (None, s.ProposalDiagnostics()):
            chat = model(turn(action))
            with self.assertRaises(s.CS2Failure) as caught:
                s.step(inputs, chat_fn=chat, diagnostics=capture)
            outcomes.append((caught.exception.code, str(caught.exception)))
            chat.assert_called_once()
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(len(capture.grounded_evidence), 1)
        self.assertIsNotNone(capture.raw_model_content)
        self.assertIsNotNone(capture.parsed_proposal)
        simulator = make_llm_simulator(chat_fn=model(turn(action)))
        with self.assertRaises(s.CS2Failure):
            simulator(inputs)
        diagnostics = simulator.take_diagnostics()
        self.assertEqual(len(diagnostics.grounded_evidence), 1)
        self.assertEqual(diagnostics.failure.code, 'GUARD_REJECTED')

    def test_selector_syntax_vs_grounding_failure(self):
        text = 'Please provide your email.'
        for changes, code in (({'start': 0, 'end': len(text)}, 'INVALID_PROPOSAL'),
                              ({'quote': ''}, 'INVALID_PROPOSAL'),
                              ({'quote': 'Please provide your Email.'}, 'GUARD_REJECTED'),
                              ({'message_index': 3}, 'GUARD_REJECTED')):
            action = info(text)
            action['items'][0]['request_evidence'][0].update(changes)
            self.reject(self.inputs(text), turn(action), code)

    def test_payload_frozen_presentation(self):
        from hashlib import sha256
        inputs = self.inputs('Could you please provide your full name?')
        chat = model(turn(info(inputs.public_history[-1].text, 'customer_name', 'Demo1 Customer1')))
        capture = s.ProposalDiagnostics()
        s.step(inputs, chat_fn=chat, diagnostics=capture)
        raw = chat.call_args.kwargs['messages'][1]['content']
        self.assertEqual(list(json.loads(raw)), ['customer', 'state', 'public_history'])
        self.assertEqual([m['message_index'] for m in json.loads(raw)['public_history']], [0, 1])
        self.assertEqual(sha256(raw.encode()).hexdigest(),
                         '61a6ac9ebf0f20690f5a4327a0a9dd8e24d96b6f7a4cab13e356e6dd32159fcb')
        self.assertEqual(capture.input_sha256, sha256(raw.encode()).hexdigest())
        reversed_payload = dict(reversed(list(s.build_payload(inputs).items())))
        self.assertEqual(s.serialize_payload(reversed_payload), raw)

    def test_schema_selectors_and_prerequisites_all_actions(self):
        schema = s.ProposalEnvelope.model_json_schema()
        self.assertNotIn('EvidenceRef', schema['$defs'])
        selector = schema['$defs']['EvidenceSelector']
        self.assertEqual(set(selector['properties']), {'message_index', 'quote'})
        self.assertFalse(selector['additionalProperties'])
        for name in ('InformationProposal', 'OptionsProposal', 'TargetQuestionProposal',
                     'SelectionProposal', 'ConfigurationProposal', 'FinalOrderProposal', 'StopProposal'):
            self.assertTrue(schema['$defs'][name + '_EvidenceSelector_']['description'])

    def test_all_information_fields_with_selectors(self):
        labels = {'customer_name': 'full name', 'phone': 'phone number', 'email': 'email address',
                  'company_name': 'company name', 'customer_instructions': 'special instructions',
                  'delivery_address.address': 'street address', 'delivery_address.city': 'city',
                  'delivery_address.state': 'state', 'delivery_address.postcode': 'postcode',
                  'delivery_address.country': 'country', 'room_size': 'room size', 'quantity': 'quantity'}
        for field, label in labels.items():
            with self.subTest(field=field):
                text = f'Please provide your {label}.'
                inputs = self.inputs(text)
                value = s.customer_value(inputs.customer, field)
                kind = 'ABSENT' if value is None else 'QUANTITY' if type(value) is int else 'TEXT'
                result = s.step(inputs, chat_fn=model(turn(info(text, field, value, kind=kind))))
                self.assertEqual(result.decision.actions[0].fields, (field,))

    def test_initial_exact_all_three_no_call(self):
        for customer in self.customers:
            chat = Mock()
            result = s.step(CustomerSimulatorInput(customer=customer, state=CustomerSimulatorState(), public_history=()), chat_fn=chat)
            self.assertEqual(result.decision.message, customer.initial_message)
            self.assertEqual(result.state.disclosed_fields, customer.initial_disclosures)
            chat.assert_not_called()
        self.assertEqual(result.state.pending_discovery.field, 'product_model')

    def test_exact_payload_and_call(self):
        text = 'Please provide your email.'
        inputs = self.inputs(text)
        chat = model(turn(info(text)))
        s.step(inputs, chat_fn=chat)
        args = chat.call_args.kwargs
        self.assertEqual(set(args), {'model', 'think', 'stream', 'messages', 'format', 'options'})
        self.assertEqual(args['model'], 'qwen3:8b')
        self.assertIs(args['think'], False)
        self.assertIs(args['stream'], False)
        self.assertEqual(args['options'], {'temperature': 0, 'seed': 0})
        self.assertEqual(args['format'], s.ProposalEnvelope.model_json_schema())
        payload = json.loads(args['messages'][1]['content'])
        expected = inputs.model_dump(mode='json')
        expected['public_history'] = [dict(message_index=i, **m) for i, m in enumerate(expected['public_history'])]
        self.assertEqual(payload, expected)
        self.assertEqual(set(payload), {'customer', 'state', 'public_history'})
        for forbidden in ('architecture', 'evaluation', 'required_input', 'workflow_state', 'business_result', 'support_knowledge'):
            self.assertNotIn(forbidden, payload)

    def test_schema_rejects_customer_text_and_rationale(self):
        text = 'Please provide your email.'
        for extra in ('customer_text', 'rationale', 'state', 'architecture'):
            proposal = turn(info(text)); proposal[extra] = 'unauthorized'
            self.reject(self.inputs(text), proposal, 'INVALID_PROPOSAL')

    def test_json_failures_zero_retry(self):
        for raw in ('', '{}', 'null', '```json\n{}\n```', '{} trailing', '{"proposal":{},"proposal":{}}', '{"proposal":{"kind":"INITIAL_MESSAGE"}}'):
            chat = Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=raw)))
            with self.assertRaises(s.CS2Failure) as caught:
                s.step(self.inputs('Hello.'), chat_fn=chat)
            self.assertEqual(caught.exception.code, 'INVALID_PROPOSAL')
            chat.assert_called_once()

    def test_quantity_strict_and_invention(self):
        text = 'Please provide your quantity.'
        for value in ('1', True, 0):
            self.reject(self.inputs(text), turn(info(text, 'quantity', value, kind='QUANTITY')), 'INVALID_PROPOSAL')
        self.reject(self.inputs(text), turn(info(text, 'quantity', 2, kind='QUANTITY')))
        result = s.step(self.inputs(text), chat_fn=model(turn(info(text, 'quantity', 1, kind='QUANTITY'))))
        self.assertEqual(result.decision.message, 'The quantity is 1.')

    def test_wrong_customer_facts(self):
        for field, label in (('customer_name', 'full name'), ('phone', 'phone number'), ('email', 'email'), ('delivery_address.city', 'city'), ('room_size', 'room size')):
            text = f'Please provide your {label}.'
            self.reject(self.inputs(text), turn(info(text, field, 'Wrong value')))

    def test_optional_absence(self):
        for field, label, wording in (('company_name', 'company name', "I don't have a company name to provide."), ('customer_instructions', 'special instructions', 'I have no special instructions.')):
            text = f'Please provide your {label}.'
            result = s.step(self.inputs(text), chat_fn=model(turn(info(text, field, kind='ABSENT'))))
            self.assertEqual(result.decision.message, wording)
        text = 'Please provide your email.'
        self.reject(self.inputs(text), turn(info(text, kind='ABSENT')), 'INVALID_PROPOSAL')

    def test_requested_email_deterministic(self):
        text = 'Can you share your email?'
        result = s.step(self.inputs(text), chat_fn=model(turn(info(text))))
        self.assertEqual(result.decision.message, 'My email address is customer1@example.com.')
        self.assertIn('email', result.state.disclosed_fields)

    def test_wrong_request_reference(self):
        text = 'Please provide your phone number.'
        self.reject(self.inputs(text), turn(info(text)))

    def test_extra_true_fact_not_authorized(self):
        text = 'Please provide your email.'
        action = info(text)
        action['items'].append(info(text, 'phone', '0400000001')['items'][0])
        self.reject(self.inputs(text), turn(action))

    def test_duplicate_bundle_fields(self):
        text = 'Please provide your email.'
        self.reject(self.inputs(text), turn(info(text), info(text)), 'INVALID_PROPOSAL')

    def test_s01_redundant_known_selection(self):
        text = 'Which timber would you like?'
        action = {'kind': 'SELECT_OPTION', 'field': 'timber', 'value': 'Tassie Oak', 'trigger_evidence': [ref(text)], 'offer_evidence': []}
        result = s.step(self.inputs(text), chat_fn=model(turn(action)))
        self.assertEqual(result.decision.message, "For timber, I'll choose Tassie Oak.")
        action['value'] = 'Marri'
        self.reject(self.inputs(text), turn(action))

    def test_s01_cannot_discover(self):
        text = 'Which timber would you like?'
        self.reject(self.inputs(text), turn({'kind': 'ASK_AVAILABLE_OPTIONS', 'field': 'timber', 'request_evidence': [ref(text)]}))

    def test_s02_ask_options_and_select(self):
        text = 'Which timber would you like?'
        inputs = self.inputs(text, 1)
        result = s.step(inputs, chat_fn=model(turn({'kind': 'ASK_AVAILABLE_OPTIONS', 'field': 'timber', 'request_evidence': [ref(text)]})))
        self.assertEqual(result.decision.message, 'What timber options are available?')
        support = 'Happy to help!\nAvailable timber options:\n- Zebra\n- Marri'
        inputs = self.advance(inputs, result, support)
        offer = e.availability(support, 3, 'timber')[0].evidence.model_dump(mode='json', include={'message_index', 'quote'})
        result = s.step(inputs, chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': 'timber', 'value': 'Zebra', 'trigger_evidence': [offer], 'offer_evidence': [offer]})))
        self.assertEqual(result.decision.message, "For timber, I'll choose Zebra.")
        self.assertIsNone(result.state.pending_discovery)

    def test_unknown_target_needs_offer(self):
        text = 'Which timber would you like?'
        self.reject(self.inputs(text, 1), turn({'kind': 'SELECT_OPTION', 'field': 'timber', 'value': 'Zebra', 'trigger_evidence': [ref(text)], 'offer_evidence': []}))

    def test_s02_direct_selection(self):
        text = 'Which bracket would you like?'
        result = s.step(self.inputs(text, 1), chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': 'bracket', 'value': 'Copper', 'trigger_evidence': [ref(text)], 'offer_evidence': []})))
        self.assertEqual(result.decision.message, "For bracket, I'll choose Copper.")

    def test_s03_model_selection_and_provider_compatibility(self):
        from evaluation.static_product_knowledge import detect_product_query, _selected_model
        text = 'Available models include Saga and Odyssey.'
        result = s.step(self.inputs(text, 2), chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [ref(text)]})))
        self.assertEqual(result.decision.message, "For table model, I'll choose Saga.")
        self.assertEqual(_selected_model((PublicMessage(role='user', text=result.decision.message),), SimpleNamespace(product_models=('Saga',))), 'Saga')
        for field in CONFIGURATION_FIELDS:
            for action in (s.AskAvailableOptions(field=field), s.AskAboutTargetOption(field=field)):
                self.assertEqual(detect_product_query(s.render_authorized((action,), self.customers[2])).field, field)

    def test_omission_then_target_question(self):
        text = 'Available models include Odyssey.'
        result = s.step(self.inputs(text, 2), chat_fn=model(turn({'kind': 'ASK_ABOUT_TARGET_OPTION', 'field': 'product_model', 'value': 'Saga', 'options_evidence': [ref(text)]})))
        self.assertEqual(result.decision.message, 'Is Saga available for table model?')
        self.assertNotIn('product_model', result.state.disclosed_fields)

    def test_target_question_not_when_offered(self):
        text = 'Available models include Saga.'
        self.reject(self.inputs(text, 2), turn({'kind': 'ASK_ABOUT_TARGET_OPTION', 'field': 'product_model', 'value': 'Saga', 'options_evidence': [ref(text)]}))

    def test_denial_stop_and_selection_rejection(self):
        text = 'Saga is unavailable.'
        inputs = self.inputs(text, 2)
        result = s.step(inputs, chat_fn=model({'kind': 'STOP', 'category': 'TARGET_DENIED', 'evidence': [ref(text)]}))
        self.assertEqual(result.decision.reason, 'TARGET_OPTION_DENIED')
        self.reject(inputs, turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [ref(text)]}))

    def test_bad_offer_semantics(self):
        for text in ('Saga is available for timber.', '"Saga is available."', 'Saga might be available.', 'If Saga is available, choose it.'):
            self.reject(self.inputs(text, 2), turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [ref(text)]}))

    def test_user_text_is_not_offer(self):
        text = 'Which table model would you like?'
        inputs = self.inputs(text, 2)
        user_ref = ref(inputs.public_history[0].text, 0)
        self.reject(inputs, turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [user_ref]}))

    def test_s03_ignored_question_no_repair(self):
        text = 'Please provide your email.'
        inputs = self.inputs(text, 2)
        self.reject(inputs, turn(info(text, value='customer3@example.com')))
        result = s.step(inputs, chat_fn=model({'kind': 'STOP', 'category': 'CANNOT_INTERPRET', 'evidence': [ref(text)]}))
        self.assertEqual(result.decision.reason, 'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def confirmation(self, inputs, final=False):
        text = inputs.public_history[-1].text
        index = len(inputs.public_history)-1
        artifact = e.extract_artifact(text, index)
        request = e.approval_requests(text, index, artifact)[0]
        return {'kind': 'CONFIRM_FINAL_ORDER' if final else 'CONFIRM_CONFIGURATION', 'artifact': artifact.evidence.model_dump(mode='json', include={'message_index', 'quote'}), 'approval_request': request.model_dump(mode='json', include={'message_index', 'quote'})}

    def test_configuration_flexible_framing(self):
        text = 'Many thanks for your patience today!\n\n' + configuration_text(self.customers[0]) + '\n\nIf everything looks right, please approve this configuration.'
        inputs = self.inputs(text)
        result = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        self.assertEqual(result.decision.message, 'Yes, that configuration is correct.')
        self.assertEqual(len(result.state.approval_receipts), 1)

    def test_all_configuration_customer_mismatches(self):
        body = configuration_text(self.customers[0])
        for line in body.splitlines():
            if not line.startswith('- '):
                continue
            changed = line.rsplit(' ', 1)[0] + (' 2' if 'Quantity' in line else ' Wrong')
            text = body.replace(line, changed) + '\n\nPlease confirm the configuration.'
            inputs = self.inputs(text)
            self.reject(inputs, turn(self.confirmation(inputs)))

    def test_missing_approval_not_authorized(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease review the details.'
        artifact = e.extract_artifact(text, 1)
        action = {'kind': 'CONFIRM_CONFIGURATION', 'artifact': artifact.evidence.model_dump(mode='json', include={'message_index', 'quote'}), 'approval_request': ref(text, quote='Please review the details.')}
        self.reject(self.inputs(text), turn(action))

    def test_final_requires_configuration_receipt(self):
        text = final_text(self.customers[0]) + '\n\nDo you wish to place this provisional order?'
        inputs = self.inputs(text)
        self.reject(inputs, turn(self.confirmation(inputs, True)))

    def test_final_with_prior_approval_and_public_prices(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration.'
        inputs = self.inputs(text)
        configured = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        inputs = self.advance(inputs, configured, final_text(self.customers[0]) + '\n\nDo you wish to place this provisional order?')
        result = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs, True))))
        self.assertEqual(result.decision.message, 'Yes, I confirm the final order and would like to place it.')
        self.assertEqual(len(result.state.approval_receipts), 2)

    def test_public_created_stop(self):
        text = 'Your order ABC has been created.'
        result = s.step(self.inputs(text), chat_fn=model({'kind': 'STOP', 'category': 'ORDER_CREATED', 'evidence': [ref(text)]}))
        self.assertEqual(result.decision.reason, 'ORDER_CREATED_PUBLICLY_REPORTED')
        self.assertEqual(result.state.turn_index, 1)

    def test_stop_not_blindly_trusted(self):
        text = 'Please provide your email.'
        self.reject(self.inputs(text), {'kind': 'STOP', 'category': 'ORDER_CREATED', 'evidence': [ref(text)]})

    def test_model_failure_and_incomplete(self):
        chat = Mock(side_effect=RuntimeError('offline'))
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(self.inputs('Hello.'), chat_fn=chat)
        self.assertEqual(caught.exception.code, 'MODEL_FAILURE')
        chat.assert_called_once()
        chat = Mock(return_value=SimpleNamespace(done=False))
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(self.inputs('Hello.'), chat_fn=chat)
        self.assertEqual(caught.exception.code, 'INVALID_PROPOSAL')

    def test_identical_input_proposal_result_and_prompt(self):
        text = 'Please provide your email.'
        inputs = self.inputs(text)
        a, b = model(turn(info(text))), model(turn(info(text)))
        self.assertEqual(s.step(inputs, chat_fn=a), s.step(inputs, chat_fn=b))
        self.assertEqual(a.call_args, b.call_args)
        self.assertNotIn('architecture', s.build_payload(inputs))

    def test_hidden_input_extra_rejected_before_call(self):
        inputs = self.inputs('Please provide your email.')
        poisoned = inputs.model_copy(update={'architecture': 'A3'})
        chat = Mock()
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(poisoned, chat_fn=chat)
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        chat.assert_not_called()

    def test_invalid_saved_pending_request(self):
        text = 'Please provide your email.'
        inputs = self.inputs(text)
        forged = RequestedFieldEvidence(field='phone', evidence=e.reference(text, 1))
        inputs = CustomerSimulatorInput(customer=inputs.customer, public_history=inputs.public_history,
            state=CustomerSimulatorState(turn_index=1, disclosed_fields=inputs.state.disclosed_fields, pending_requests=(forged,)))
        chat = Mock()
        with self.assertRaises(s.CS2Failure):
            s.step(inputs, chat_fn=chat)
        chat.assert_not_called()

    def test_forged_disclosures_rejected_before_model(self):
        inputs = self.inputs('Available models include Saga.', 2)
        forged = inputs.state.model_copy(update={'disclosed_fields': CONFIGURATION_FIELDS})
        inputs = CustomerSimulatorInput(customer=inputs.customer, state=forged, public_history=inputs.public_history)
        chat = Mock()
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(inputs, chat_fn=chat)
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        chat.assert_not_called()

    def test_repeated_configuration_receipts_validate(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration.'
        inputs = self.inputs(text)
        result = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        inputs = self.advance(inputs, result, text)
        result = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        self.assertTrue(result.state.approval_receipts[-1].repeated)

    def test_final_all_customer_fields_mismatch(self):
        from test_public_observation import final_snapshot
        from confirmation_presentation import render_provisional_order
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration.'
        initial = self.inputs(text)
        approved = s.step(initial, chat_fn=model(turn(self.confirmation(initial))))
        for field in ('customer_name', 'phone', 'email', 'room_size', 'company_name', 'customer_instructions', 'address', 'city', 'state', 'postcode', 'country'):
            snap = final_snapshot(self.customers[0])
            if field in snap['delivery_address']:
                snap['delivery_address'][field] = 'Wrong'
            else:
                snap[field] = 'Wrong'
            public_text = render_provisional_order(snap) + '\n\nDo you wish to place this provisional order?'
            inputs = self.advance(initial, approved, public_text)
            self.reject(inputs, turn(self.confirmation(inputs, True)))

    def test_discovery_question_last_and_unique(self):
        text = 'Which timber would you like?'
        question = {'kind': 'ASK_AVAILABLE_OPTIONS', 'field': 'timber', 'request_evidence': [ref(text)]}
        self.reject(self.inputs(text, 1), turn(question, info(text)), 'INVALID_PROPOSAL')
        other = {**question, 'field': 'felt_color'}
        self.reject(self.inputs(text, 1), turn(question, other), 'INVALID_PROPOSAL')

    def test_confirmation_bundle_rejected(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration.'
        inputs = self.inputs(text)
        self.reject(inputs, turn(self.confirmation(inputs), info(text)), 'INVALID_PROPOSAL')

    def test_wrong_kind_and_null_values(self):
        text = 'Please provide your email.'
        for value in (None, '', ' '):
            self.reject(self.inputs(text), turn(info(text, value=value)), 'INVALID_PROPOSAL')
        proposal = turn(info(text)); proposal['actions'][0]['kind'] = 'REPAIR'
        self.reject(self.inputs(text), proposal, 'INVALID_PROPOSAL')

    def test_mismatch_public_stop(self):
        text = configuration_text(self.customers[0]).replace('Tassie Oak', 'Marri') + '\n\nPlease confirm the configuration.'
        artifact = e.extract_artifact(text, 1)
        result = s.step(self.inputs(text), chat_fn=model({'kind': 'STOP', 'category': 'CONTENT_MISMATCH', 'evidence': [artifact.evidence.model_dump(mode='json', include={'message_index', 'quote'})]}))
        self.assertEqual(result.decision.reason, 'PUBLIC_CONTENT_MISMATCH')

    def test_unresolved_target_stops(self):
        text = 'Available models include Odyssey.'
        inputs = self.inputs(text, 2)
        asked = s.step(inputs, chat_fn=model(turn({'kind': 'ASK_ABOUT_TARGET_OPTION', 'field': 'product_model', 'value': 'Saga', 'options_evidence': [ref(text)]})))
        inputs = self.advance(inputs, asked, text)
        stopped = s.step(inputs, chat_fn=model({'kind': 'STOP', 'category': 'TARGET_UNRESOLVED', 'evidence': [ref(text, 3)]}))
        self.assertEqual(stopped.decision.reason, 'TARGET_OPTION_NOT_RESOLVED')
        self.reject(inputs, turn({'kind': 'ASK_ABOUT_TARGET_OPTION', 'field': 'product_model', 'value': 'Saga', 'options_evidence': [ref(text, 3)]}))

    def test_saved_offer_denied_on_next_turn(self):
        text = 'Available models include Saga.'
        inputs = self.inputs(text, 2)
        selected = s.step(inputs, chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [ref(text)]})))
        support = 'Saga is not available for models. Which table model would you like?'
        inputs = self.advance(inputs, selected, support)
        self.reject(inputs, turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(support, 3, 'Which table model would you like?')], 'offer_evidence': [ref(text)]}))

    def test_saved_receipt_requires_actual_customer_approval(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration.'
        inputs = self.inputs(text)
        approved = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        inputs = self.advance(inputs, approved, 'Please provide your email.')
        history = list(inputs.public_history)
        history[2] = PublicMessage(role='user', text='No.')
        bad = CustomerSimulatorInput(customer=inputs.customer, state=inputs.state, public_history=tuple(history))
        chat = Mock()
        with self.assertRaises(s.CS2Failure):
            s.step(bad, chat_fn=chat)
        chat.assert_not_called()

    def test_frozen_copy_safe_results(self):
        text = 'Please provide your email.'
        inputs = self.inputs(text)
        before = inputs.model_dump_json()
        result = s.step(inputs, chat_fn=model(turn(info(text))))
        self.assertEqual(inputs.model_dump_json(), before)
        with self.assertRaises(ValueError):
            result.state.turn_index = 99
        self.assertIsNot(result.state, inputs.state)

    def test_renderer_rejects_proposal_types(self):
        with self.assertRaises(ValueError):
            s.render_authorized((s.OptionsProposal(kind='ASK_AVAILABLE_OPTIONS', field='timber', request_evidence=(e.reference('Which timber would you like?', 1),)),), self.customers[1])

    def test_public_veto_overrides_confirmation_ref(self):
        text = configuration_text(self.customers[0]) + '\n\nPlease confirm the configuration. Do not confirm this configuration.'
        inputs = self.inputs(text)
        self.reject(inputs, turn(self.confirmation(inputs)))

    def test_preserved_pilot_001_customer_approval(self):
        from test_public_observation import PILOT_001_RESPONSE
        inputs = self.inputs(PILOT_001_RESPONSE)
        result = s.step(inputs, chat_fn=model(turn(self.confirmation(inputs))))
        self.assertEqual(result.decision.message, 'Yes, that configuration is correct.')

    def test_each_s03_unknown_field_requires_discovery(self):
        text = 'Available models include Saga.'
        inputs = self.inputs(text, 2)
        result = s.step(inputs, chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': 'product_model', 'value': 'Saga', 'trigger_evidence': [ref(text)], 'offer_evidence': [ref(text)]})))
        from evaluation.public_observation import FIELD_LABELS
        for field in CONFIGURATION_FIELDS[1:]:
            support = f'Which {FIELD_LABELS[field]} would you like?'
            inputs = self.advance(inputs, result, support)
            index = len(inputs.public_history) - 1
            result = s.step(inputs, chat_fn=model(turn({'kind': 'ASK_AVAILABLE_OPTIONS', 'field': field, 'request_evidence': [ref(support, index)]})))
            value = s.customer_value(inputs.customer, field)
            support = f'{value} is available for {FIELD_LABELS[field]}.'
            if field == 'table_size':
                support = 'Available table size options for Saga are 7ft and 8ft.'
            inputs = self.advance(inputs, result, support)
            index = len(inputs.public_history) - 1
            result = s.step(inputs, chat_fn=model(turn({'kind': 'SELECT_OPTION', 'field': field, 'value': value, 'trigger_evidence': [ref(support, index)], 'offer_evidence': [ref(support, index)]})))
        self.assertEqual(set(result.state.disclosed_fields), set(CONFIGURATION_FIELDS))
        self.assertEqual({o.field for o in result.state.observed_target_offers}, set(CONFIGURATION_FIELDS))

    def test_compound_requests_preserved_across_discovery(self):
        text = 'Could you please provide your timber and felt colour?'
        inputs = self.inputs(text, 1)
        result = s.step(inputs, chat_fn=model(turn({'kind': 'ASK_AVAILABLE_OPTIONS', 'field': 'timber', 'request_evidence': [ref(text)]})))
        self.assertEqual([r.field for r in result.state.pending_requests], ['timber', 'felt_color'])
        support = 'Zebra is available.'
        inputs = self.advance(inputs, result, support)
        result = s.step(inputs, chat_fn=model(turn(
            {'kind': 'SELECT_OPTION', 'field': 'timber', 'value': 'Zebra', 'trigger_evidence': [ref(support, 3)], 'offer_evidence': [ref(support, 3)]},
            {'kind': 'ASK_AVAILABLE_OPTIONS', 'field': 'felt_color', 'request_evidence': [ref(text)]})))
        self.assertEqual(result.state.pending_discovery.field, 'felt_color')
        self.assertEqual([r.field for r in result.state.pending_requests], ['felt_color'])

    def test_invalid_saved_offer(self):
        from evaluation.customer_simulator import TargetOfferEvidence
        text = 'Available models include Odyssey.'
        inputs = self.inputs(text, 2)
        state = inputs.state.model_copy(update={'observed_target_offers': (TargetOfferEvidence(field='product_model', evidence=e.reference(text, 1)),)})
        inputs = CustomerSimulatorInput(customer=inputs.customer, state=state, public_history=inputs.public_history)
        chat = Mock()
        with self.assertRaises(s.CS2Failure) as caught:
            s.step(inputs, chat_fn=chat)
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        chat.assert_not_called()

    def test_terminal_state_bypasses_model(self):
        text = 'Your order has been created.'
        inputs = self.inputs(text)
        result = s.step(inputs, chat_fn=model({'kind': 'STOP', 'category': 'ORDER_CREATED', 'evidence': [ref(text)]}))
        inputs = CustomerSimulatorInput(customer=inputs.customer, state=result.state, public_history=inputs.public_history)
        chat = Mock()
        self.assertEqual(s.step(inputs, chat_fn=chat), result)
        chat.assert_not_called()
