"""Pure CS1-B unit tests with synthetic public messages; no ATS conversations."""

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError
from confirmation_presentation import render_configuration_summary, render_provisional_order
from evaluation.scenario_loader import load_scenarios
from evaluation.scenario_spec import CONFIGURATION_FIELDS, ScenarioSpec
from evaluation.public_observation import EvidenceRef, FIELD_LABELS, PublicMessage, RequestedFieldEvidence
from evaluation.customer_simulator import (
    MAX_CUSTOMER_MESSAGES, AskAboutTargetOption, AskAvailableOptions,
    CustomerAction, CustomerSimulatorInput, CustomerSimulatorState, CustomerTurn,
    InitialMessage, PendingDiscovery, ProvideInformation, SelectOption,
    StopDecision, TargetOfferEvidence, customer_value, step,
)
from test_public_observation import configuration_text, final_snapshot, final_text, with_request


class PublicDriver:
    """In-memory unit-test fixture, never a runtime/experiment runner."""
    def __init__(self, customer):
        self.customer=customer
        self.history=()
        self.state=CustomerSimulatorState()
        self.last=self.advance()

    def advance(self, text=None):
        if text is not None:
            self.history += (PublicMessage(role='assistant',text=text),)
        result=step(CustomerSimulatorInput(customer=self.customer,state=self.state,public_history=self.history))
        self.state=result.state
        if isinstance(result.decision,CustomerTurn):
            self.history += (PublicMessage(role='user',text=result.decision.message),)
        self.last=result
        return result

    def request(self, field):
        return self.advance(f'Could you please provide your {FIELD_LABELS[field]}?')

    def select_all(self):
        if self.state.pending_discovery:
            field=self.state.pending_discovery.field
            self.advance(f'Available {FIELD_LABELS[field]} options include {customer_value(self.customer,field)}.')
        for field in CONFIGURATION_FIELDS:
            if field in self.state.disclosed_fields:
                continue
            result=self.request(field)
            if isinstance(result.decision.actions[-1],AskAvailableOptions):
                self.advance(f'Available {FIELD_LABELS[field]} options include {customer_value(self.customer,field)}.')
        return self


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios=load_scenarios(check_repository=False)
        cls.customers=tuple(s.customer_view() for s in cls.scenarios)

    def assert_stop(self,result,reason):
        self.assertIsInstance(result.decision,StopDecision)
        self.assertEqual(result.decision.reason,reason)
        self.assertEqual(result.state.stop_decision,result.decision)

    def test_exact_initial_messages_and_disclosures(self):
        for customer in self.customers:
            with self.subTest(knowledge=customer.product_knowledge):
                driver=PublicDriver(customer)
                self.assertEqual(driver.last.decision.message,customer.initial_message)
                self.assertEqual(driver.last.decision.actions,(InitialMessage(),))
                self.assertEqual(driver.state.disclosed_fields,customer.initial_disclosures)
                self.assertEqual(driver.state.turn_index,1)
        self.assertEqual(PublicDriver(self.customers[2]).state.pending_discovery,
                         PendingDiscovery(field='product_model',question_kind='AVAILABLE_OPTIONS',customer_message_index=0))
        self.assertIsNone(PublicDriver(self.customers[0]).state.pending_discovery)

    def test_all_requested_customer_facts(self):
        fields=[f for f in FIELD_LABELS if f not in CONFIGURATION_FIELDS]
        for customer in self.customers:
            driver=PublicDriver(customer).select_all()
            for field in fields:
                with self.subTest(customer=customer.product_knowledge,field=field):
                    result=driver.request(field)
                    self.assertEqual(result.decision.actions,(ProvideInformation(fields=(field,)),))
                    value=customer_value(customer,field)
                    if value is not None:
                        self.assertIn(str(value),result.decision.message)
                    else:
                        self.assertNotIn('None',result.decision.message)
                    self.assertIn(field,result.state.disclosed_fields)

    def test_requested_facts_in_textual_order_only(self):
        driver=PublicDriver(self.customers[0])
        result=driver.advance('Could you please provide your phone number, email address and full name?')
        self.assertEqual(result.decision.message,'My phone number is 0400000001.\nMy email address is customer1@example.com.\nMy name is Demo1 Customer1.')
        self.assertNotIn('Example Street',result.decision.message)

    def test_s01_redundant_configuration_never_discovers(self):
        driver=PublicDriver(self.customers[0])
        for field in CONFIGURATION_FIELDS:
            result=driver.request(field)
            self.assertEqual(result.decision.actions,(SelectOption(field=field),))
            self.assertIn(str(customer_value(driver.customer,field)),result.decision.message)
            self.assertIsNone(result.state.pending_discovery)

    def test_s02_direct_fields_not_rediscovered(self):
        driver=PublicDriver(self.customers[1])
        for field in driver.customer.initially_known_configuration_fields:
            self.assertEqual(driver.request(field).decision.actions,(SelectOption(field=field),))

    def test_each_s02_unknown_field_requires_public_offer(self):
        for field in self.customers[1].conversation_policy.configuration_selection.discovery_fields:
            with self.subTest(field=field):
                driver=PublicDriver(self.customers[1])
                result=driver.request(field)
                self.assertEqual(result.decision.actions,(AskAvailableOptions(field=field),))
                self.assertNotIn(field,result.state.disclosed_fields)
                target=customer_value(driver.customer,field)
                result=driver.advance(f'Available {FIELD_LABELS[field]} options include {target}.')
                self.assertEqual(result.decision.actions,(SelectOption(field=field),))
                offer=result.state.observed_target_offers[-1]
                self.assertEqual(offer.field,field)
                self.assertIn(str(target),offer.evidence.resolve(driver.history))

    def test_absent_target_then_positive_followup(self):
        driver=PublicDriver(self.customers[2])
        driver.advance('Available models include Saga.')
        driver.request('timber')
        result=driver.advance('Tassie Oak and Zebra are available.')
        self.assertEqual(result.decision.actions,(AskAboutTargetOption(field='timber'),))
        self.assertEqual(result.decision.message,'Is Marri available for timber?')
        self.assertNotIn('timber',result.state.disclosed_fields)
        result=driver.advance('Yes, Marri is available.')
        self.assertEqual(result.decision.actions,(SelectOption(field='timber'),))
        self.assertEqual(result.decision.message,"For timber, I'll choose Marri.")

    def test_target_denial_and_unresolved_followup(self):
        for reply,reason in [('Zebra is unavailable.','TARGET_OPTION_DENIED'),
                             ('Tassie Oak is available.','TARGET_OPTION_NOT_RESOLVED')]:
            driver=PublicDriver(self.customers[1]); driver.request('timber')
            driver.advance('Available timber options include Tassie Oak.')
            self.assert_stop(driver.advance(reply),reason)

    def test_uninterpretable_followup_stops_without_clarification(self):
        driver=PublicDriver(self.customers[1]); driver.request('timber')
        driver.advance('Available timber options include Tassie Oak.')
        result=driver.advance('Do you mean Zebra?')
        self.assert_stop(result,'SIMULATOR_UNINTERPRETABLE_RESPONSE')
        self.assertFalse(hasattr(result.decision,'message'))

    def test_positive_list_in_original_request_is_sufficient(self):
        driver=PublicDriver(self.customers[1])
        result=driver.advance('Could you please provide your timber?\nAvailable timber options include Zebra.')
        self.assertEqual(result.decision.actions,(SelectOption(field='timber'),))
        other=PublicDriver(self.customers[1])
        result=other.advance('Could you please provide your timber?\nSupported values: ["Tassie Oak"]')
        self.assertEqual(result.decision.actions,(AskAboutTargetOption(field='timber'),))

    def test_s03_repeated_discovery_all_fields(self):
        driver=PublicDriver(self.customers[2]).select_all()
        self.assertEqual(set(driver.state.disclosed_fields),set(CONFIGURATION_FIELDS))
        self.assertEqual({o.field for o in driver.state.observed_target_offers},set(CONFIGURATION_FIELDS))
        for offer in driver.state.observed_target_offers:
            self.assertIn(str(customer_value(driver.customer,offer.field)),offer.evidence.resolve(driver.history))
        self.assertEqual(driver.request('email').decision.message,'My email address is customer3@example.com.')

    def test_wrong_field_offer_does_not_authorize_selection(self):
        driver=PublicDriver(self.customers[1]); driver.request('timber')
        result=driver.advance('Zebra is available for felt.')
        self.assert_stop(result,'SIMULATOR_UNINTERPRETABLE_RESPONSE')
        self.assertNotIn('timber',result.state.disclosed_fields)

    def test_target_substring_and_wrong_case_not_canonical(self):
        for text in ['Available timber options include Zebrawood.','Available timber options include zebra.']:
            driver=PublicDriver(self.customers[1]); driver.request('timber')
            self.assertEqual(driver.advance(text).decision.actions,(AskAboutTargetOption(field='timber'),))

    def test_negative_hypothetical_quoted_mentions_do_not_select(self):
        for reply in ['"Zebra is available."','Zebra might be available.','Do you mean Zebra?',
                      'If Zebra is available, choose it.','Zebra is available, but not for this order.']:
            driver=PublicDriver(self.customers[1]); driver.request('timber')
            self.assert_stop(driver.advance(reply),'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def test_public_denial_overrides_prior_offer(self):
        driver=PublicDriver(self.customers[1]); driver.request('timber')
        driver.advance('Zebra is available.')
        self.assert_stop(driver.advance('Zebra is unavailable for timber.'),'TARGET_OPTION_DENIED')

    def test_multiple_unknown_requests_are_preserved(self):
        driver=PublicDriver(self.customers[1])
        result=driver.advance('Could you please provide your timber, timber finish and email address?')
        self.assertEqual(result.decision.actions,(ProvideInformation(fields=('email',)),AskAvailableOptions(field='timber')))
        self.assertEqual([r.field for r in result.state.pending_requests],['timber','timber_painting'])
        result=driver.advance('Available timber options include Zebra.')
        self.assertEqual(result.decision.actions,(SelectOption(field='timber'),AskAvailableOptions(field='timber_painting')))
        result=driver.advance('Available timber finish options include Natural.')
        self.assertEqual(result.decision.actions,(SelectOption(field='timber_painting'),))
        self.assertFalse(result.state.pending_requests)
        self.assertIsNone(result.state.pending_discovery)

    def test_no_repair_when_support_ignores_discovery(self):
        driver=PublicDriver(self.customers[1]); driver.request('timber')
        self.assert_stop(driver.request('email'),'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def test_matching_configuration_and_final_gates_all_scenarios(self):
        for customer in self.customers:
            driver=PublicDriver(customer).select_all()
            config=driver.advance(with_request(configuration_text(customer)))
            self.assertEqual(config.decision.actions[0].kind,'CONFIRM_CONFIGURATION')
            final=driver.advance(with_request(final_text(customer),True))
            self.assertEqual(final.decision.actions[0].kind,'CONFIRM_FINAL_ORDER')
            self.assertEqual(final.decision.message,'Yes, I confirm the final order and would like to place it.')

    def test_no_confirmation_without_request_or_without_artifact(self):
        for reply in [configuration_text(self.customers[0]),'Please confirm the configuration.']:
            self.assert_stop(PublicDriver(self.customers[0]).advance(reply),'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def test_unknown_selections_cannot_be_filled_by_summary(self):
        driver=PublicDriver(self.customers[1])
        self.assert_stop(driver.advance(with_request(configuration_text(driver.customer))),'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def test_each_configuration_fact_mismatch_blocks_approval(self):
        for field in (*CONFIGURATION_FIELDS,'quantity'):
            for customer in self.customers:
                with self.subTest(field=field,customer=customer.product_knowledge):
                    driver=PublicDriver(customer).select_all()
                    config=customer.ground_truth.configuration.model_dump()
                    config[field]=2 if field=='quantity' else 'Different'
                    self.assert_stop(driver.advance(with_request(render_configuration_summary(config))),'PUBLIC_CONTENT_MISMATCH')

    def test_each_final_customer_fact_mismatch_blocks_approval(self):
        fields=['customer_name','phone','email','company_name','customer_instructions','room_size',*CONFIGURATION_FIELDS,'quantity',
                'delivery_address.address','delivery_address.city','delivery_address.state','delivery_address.postcode','delivery_address.country']
        for field in fields:
            with self.subTest(field=field):
                driver=PublicDriver(self.customers[0])
                driver.advance(with_request(configuration_text(driver.customer)))
                snapshot=final_snapshot(driver.customer)
                if field.startswith('delivery_address.'):
                    snapshot['delivery_address'][field.split('.')[1]]='Different'
                else:
                    snapshot[field]=2 if field=='quantity' else 'Different'
                self.assert_stop(driver.advance(with_request(render_provisional_order(snapshot),True)),'PUBLIC_CONTENT_MISMATCH')

    def test_repeated_identical_configuration_approval(self):
        driver=PublicDriver(self.customers[0])
        text=with_request(configuration_text(driver.customer))
        first=driver.advance(text); second=driver.advance(text)
        self.assertEqual(first.decision.message,second.decision.message)
        self.assertEqual(second.decision.actions[0].kind,'CONFIRM_CONFIGURATION')
        self.assertEqual([r.repeated for r in second.state.approval_receipts],[False,True])
        self.assertNotEqual(first.state.approval_receipts[-1].customer_message_index,second.state.approval_receipts[-1].customer_message_index)

    def test_repeated_identical_final_approval(self):
        driver=PublicDriver(self.customers[0])
        driver.advance(with_request(configuration_text(driver.customer)))
        text=with_request(final_text(driver.customer),True)
        first=driver.advance(text); second=driver.advance(text)
        self.assertEqual(first.decision.message,second.decision.message)
        self.assertEqual(second.decision.actions[0].kind,'CONFIRM_FINAL_ORDER')
        self.assertTrue(second.state.approval_receipts[-1].repeated)

    def test_changed_previously_approved_artifacts_stop(self):
        for final in (False,True):
            driver=PublicDriver(self.customers[0])
            driver.advance(with_request(configuration_text(driver.customer)))
            if final:
                driver.advance(with_request(final_text(driver.customer),True))
            snapshot=final_snapshot(driver.customer) if final else driver.customer.ground_truth.configuration.model_dump()
            snapshot['felt_color']='Different'
            body=render_provisional_order(snapshot) if final else render_configuration_summary(snapshot)
            self.assert_stop(driver.advance(with_request(body,final)),'PUBLIC_CONTENT_MISMATCH')

    def test_public_system_derived_values_accepted_as_presented(self):
        driver=PublicDriver(self.customers[0])
        driver.advance(with_request(configuration_text(driver.customer)))
        driver.advance(with_request(final_text(driver.customer),True))
        snapshot=final_snapshot(driver.customer)
        snapshot.update(product_sku='OTHER-SKU',customisation_price='999999',unit_price='1',shipping_cost='2',total_price='0')
        result=driver.advance(with_request(render_provisional_order(snapshot),True))
        self.assertEqual(result.decision.actions[0].kind,'CONFIRM_FINAL_ORDER')
        self.assertFalse(result.state.approval_receipts[-1].repeated)

    def test_evaluator_expectations_cannot_change_decisions(self):
        raw=self.scenarios[0].model_dump(mode='json')
        raw['evaluation']['system_derived']['product_sku']='OTHER-EXPECTED'
        raw['evaluation']['system_derived']['pricing'].update(base_model_price='1',customisation_price='2',unit_price='3',shipping_cost='4',total_price='7')
        import json
        altered=ScenarioSpec.model_validate_json(json.dumps(raw))
        self.assertEqual(altered.customer_view(),self.customers[0])
        drivers=[PublicDriver(s.customer_view()) for s in (self.scenarios[0],altered)]
        for text in [with_request(configuration_text(self.customers[0])),with_request(final_text(self.customers[0]),True)]:
            self.assertEqual(drivers[0].advance(text),drivers[1].advance(text))

    def test_public_creation_and_unavailable_stop(self):
        driver=PublicDriver(self.customers[0])
        result=driver.advance('Your order N1 has been created. Its status is CONFIRMED.')
        self.assert_stop(result,'ORDER_CREATED_PUBLICLY_REPORTED')
        # No order store exists in this test: visible reporting is not persistence proof.
        again=step(CustomerSimulatorInput(customer=driver.customer,state=result.state,public_history=driver.history))
        self.assertEqual(again,result)
        self.assert_stop(PublicDriver(self.customers[0]).advance('Information is unavailable here.'),'PUBLIC_UNAVAILABLE_RESPONSE')

    def test_development_safeguard_with_repeated_approvals(self):
        driver=PublicDriver(self.customers[0])
        text=with_request(configuration_text(driver.customer))
        for _ in range(MAX_CUSTOMER_MESSAGES-1):
            self.assertIsInstance(driver.advance(text).decision,CustomerTurn)
        self.assertEqual(driver.state.turn_index,64)
        self.assert_stop(driver.advance(text),'RUNAWAY_LIMIT_REACHED')
        source=(Path(__file__).parent/'evaluation/customer_simulator.py').read_text()
        self.assertIn('DEVELOPMENT RUNAWAY SAFEGUARD',source)
        self.assertIn('NOT the frozen',source)

    def test_strict_action_and_input_contracts(self):
        adapter=TypeAdapter(CustomerAction)
        for bad in [{'kind':'REQUEST_CLARIFICATION'}, {'kind':'SELECT_OPTION','field':'timber','value':'Invented'},
                    {'kind':'PROVIDE_INFORMATION','fields':['timber']}, {'kind':'SELECT_OPTION','field':'product_sku'}]:
            with self.assertRaises(ValidationError): adapter.validate_python(bad)
        with self.assertRaises(ValidationError): ProvideInformation(fields=())
        with self.assertRaises(ValidationError): ProvideInformation(fields=('email','email'))
        with self.assertRaises(ValidationError): CustomerSimulatorState(turn_index=True)
        with self.assertRaises(ValidationError): CustomerSimulatorState(public_history_sha256='0'*64)
        with self.assertRaises(ValidationError): CustomerSimulatorInput(customer=self.scenarios[0],state=CustomerSimulatorState(),public_history=())
        with self.assertRaises(ValidationError): PublicMessage(role='system',text='hidden')
        with self.assertRaises(ValidationError): PublicMessage(role='assistant',text='hello',required_input=['email'])
        with self.assertRaises(ValidationError): CustomerTurn(actions=(InitialMessage(),SelectOption(field='timber')),message='invalid')
        with self.assertRaises(ValidationError): CustomerTurn(actions=(AskAvailableOptions(field='timber'),AskAvailableOptions(field='felt_color')),message='invalid')

    def test_no_hidden_state_fields(self):
        expected={'turn_index','disclosed_fields','observed_target_offers','pending_requests','pending_discovery','approval_receipts','stop_decision'}
        self.assertEqual(set(CustomerSimulatorState.model_fields),expected)
        self.assertEqual(set(CustomerSimulatorInput.model_fields),{'customer','state','public_history'})

    def test_boundary_imports_and_no_external_calls(self):
        allowed={'re','html','json','typing','pydantic','confirmation_presentation','evaluation.scenario_spec','evaluation.public_observation'}
        for name in ['customer_simulator','public_observation']:
            source=(Path(__file__).parent/'evaluation'/f'{name}.py').read_text()
            tree=ast.parse(source)
            imports=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.Import): imports.update(alias.name for alias in node.names)
                if isinstance(node,ast.ImportFrom): imports.add(node.module)
            self.assertLessEqual(imports,allowed)
        with patch('builtins.open',side_effect=AssertionError('No file access')):
            self.assertIsInstance(PublicDriver(self.customers[0]).request('email').decision,CustomerTurn)

    def test_same_public_transcript_same_behavior_across_architectures(self):
        results=[]
        for architecture_label in ('A1','A2','A3'):
            # Label is deliberately retained outside simulator inputs.
            driver=PublicDriver(self.customers[1]); driver.request('timber')
            results.append(driver.advance('Available timber options include Zebra.'))
        self.assertEqual(results[0],results[1]); self.assertEqual(results[1],results[2])

    def test_immutability_and_no_partial_update_on_render_failure(self):
        driver=PublicDriver(self.customers[0])
        history=driver.history+(PublicMessage(role='assistant',text='Could you please provide your email address?'),)
        inputs=CustomerSimulatorInput(customer=driver.customer,state=driver.state,public_history=history)
        before=inputs.model_dump_json()
        self.assertEqual(step(inputs),step(inputs))
        self.assertEqual(inputs.model_dump_json(),before)
        with self.assertRaises(ValidationError): inputs.state.turn_index=99
        with self.assertRaises(ValidationError): inputs.customer.ground_truth.customer.email='changed'
        exported=inputs.model_dump(); exported['state']['turn_index']=99
        self.assertEqual(inputs.model_dump_json(),before)
        with patch('evaluation.customer_simulator._render_actions',side_effect=ValueError('render failure')):
            with self.assertRaisesRegex(ValueError,'render failure'): step(inputs)
        self.assertEqual(inputs.model_dump_json(),before)

    def test_forged_offer_and_request_evidence_rejected(self):
        driver=PublicDriver(self.customers[1])
        text='Do you mean Zebra?'
        history=driver.history+(PublicMessage(role='assistant',text=text),)
        ref=EvidenceRef(message_index=1,start=0,end=len(text),quote=text)
        for change in [dict(observed_target_offers=(TargetOfferEvidence(field='timber',evidence=ref),)),
                       dict(pending_requests=(RequestedFieldEvidence(field='timber',evidence=ref),))]:
            state=CustomerSimulatorState(turn_index=1,**change)
            with self.assertRaises(ValueError): step(CustomerSimulatorInput(customer=driver.customer,state=state,public_history=history))
        driver.request('timber'); driver.advance('Zebra is available.')
        history=list(driver.history); history[3]=PublicMessage(role='assistant',text='Zebra is unavailable.')
        history.append(PublicMessage(role='assistant',text='Could you please provide your timber?'))
        with self.assertRaises(ValueError): step(CustomerSimulatorInput(customer=driver.customer,state=driver.state,public_history=tuple(history)))

    def test_pending_question_evidence_must_resolve(self):
        driver=PublicDriver(self.customers[2])
        history=(PublicMessage(role='user',text='Hello'),PublicMessage(role='assistant',text='Available models include Saga.'))
        with self.assertRaises(ValueError): step(CustomerSimulatorInput(customer=driver.customer,state=driver.state,public_history=history))


if __name__ == '__main__':
    unittest.main()
