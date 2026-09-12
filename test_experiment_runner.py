"""CS1-D tests use scripted runtime boundaries, never real model conversations."""

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from classifier import ClassifierResult, MessageCategory
from confirmation_presentation import render_configuration_summary, render_provisional_order
from conversation_runtime import ConversationSession, PendingTurn, TurnFailure, TurnResult, TurnStatus
from order_creation_extraction import ConversationMessage
from order_creation_order_store import DEFAULT_ORDER_STORE_PATH, OrderCreationRecord
from order_creation_state import FinalOrderSnapshot, OrderCreationState, OrderCreationStage, OrderWorkflowStatus
from root_agent import RootExecutionResult, route_message
from support_agent import CustomerResponse, SupportAction, SupportActionResult, SupportOutcome
from evaluation.customer_simulator import CustomerSimulatorInput, CustomerSimulatorState, step
from evaluation.public_observation import PublicMessage
from evaluation.static_product_knowledge import provide_support_knowledge
from evaluation.experiment_runner import (
    BusinessTerminalObservation, ExperimentRunResult, PersistenceObservation, PilotCheck,
    PilotValidationResult, PublicRunRecord, PublicStop, TechnicalTermination,
    a3_execution, observe_persistence, prepare_pilot, run_scenario, validate_pilot_result,
)


FACT_REQUEST = ('Could you please provide your full name, email address, phone number, street address, '
                'city, state, postcode, country and room size?')
LABELS = {'product_model':'table model','table_size':'table size','timber':'timber',
          'timber_painting':'timber finish','felt_color':'cloth colour','bracket':'bracket','top_profile':'top profile'}


def snapshot(scenario):
    truth=scenario.customer.ground_truth
    p=scenario.evaluation.system_derived.pricing
    return FinalOrderSnapshot.model_validate_json(json.dumps({**truth.customer.model_dump(),**truth.configuration.model_dump(),
        'delivery_address':truth.delivery_address.model_dump(),'room_size':truth.room_size,
        'room_size_validation_result':'SUITABLE','product_sku':scenario.evaluation.system_derived.product_sku,
        **{f:getattr(p,f) for f in ('customisation_price','unit_price','shipping_cost','total_price')}}))


def artifacts(scenario):
    config=render_configuration_summary(scenario.customer.ground_truth.configuration.model_dump())+'\n\nPlease confirm the configuration.'
    final=render_provisional_order(snapshot(scenario).model_dump(mode='json'))+'\n\nDo you wish to place this provisional order?'
    return config,final


@dataclass
class Event:
    text: str
    category: str = 'WORKFLOW_RESPONSE'
    commit: bool = False
    fail_after_commit: bool = False


class ScriptedExecution:
    """Fake ATS returns evidence; it never decides what the customer says."""
    def __init__(self, scenario, events):
        self.scenario=scenario; self.events=list(events); self.calls=[]; self.store=None; self.results=[]

    def factory(self, store):
        self.store=store
        return self.turn

    def turn(self, session, message, *, support_knowledge):
        self.calls.append((deepcopy(session),message,deepcopy(support_knowledge)))
        if not self.events: raise AssertionError('Unexpected runtime invocation')
        event=self.events.pop(0)
        classification=ClassifierResult(category=MessageCategory(event.category),confidence=1.0,explanation='scripted public trajectory')
        route=route_message(classification,session.workflow_state)
        state=deepcopy(session.workflow_state)
        support=None; business=None
        if event.category=='GENERAL_ENQUIRY':
            support=SupportActionResult(action=SupportAction.ANSWER_ENQUIRY,outcome=SupportOutcome.ANSWERED,
                                        response=CustomerResponse(text=event.text))
        else:
            if state is None: state=OrderCreationState(conversation_id=session.conversation_id)
            state.status=OrderWorkflowStatus.COMPLETED if event.commit else OrderWorkflowStatus.AWAITING_USER_INPUT
            state.current_stage=OrderCreationStage.CREATE_ORDER if event.commit else OrderCreationStage.COLLECT_REQUIREMENTS
            business=BusinessResult(workflow_id=state.workflow_id,source_agent='ORDER_AGENT',action='CREATE_ORDER',
                result_status=BusinessResultStatus.SUCCESS if event.commit else BusinessResultStatus.NEEDS_USER_INPUT,
                current_stage=state.current_stage.value,
                reason=BusinessResultReason.ORDER_CREATED if event.commit else BusinessResultReason.MISSING_REQUIRED_INFORMATION)
        execution=RootExecutionResult(routing=route,executed=True,state=state,business_result=business,support_result=support)
        if event.commit:
            record=OrderCreationRecord(order_id='ORD-000001',source_workflow_id=state.workflow_id,
                conversation_id=session.conversation_id,created_at='2026-09-12T00:00:00+00:00',order_status='CONFIRMED',order=snapshot(self.scenario))
            self.store.write_text('['+record.model_dump_json()+']')
        if event.fail_after_commit:
            pending=PendingTurn(base_session=session,current_message=message,classification=classification,execution=execution)
            raise TurnFailure('response composition',pending)
        updated=ConversationSession(conversation_id=session.conversation_id,workflow_state=state,
            history=[*session.history,ConversationMessage(role='user',content=message),ConversationMessage(role='assistant',content=event.text)])
        result=TurnResult(session=updated,customer_response=CustomerResponse(text=event.text),classification=classification,
                          execution=execution,status=TurnStatus.RESPONDED)
        self.results.append(result)
        return result


def happy_events(scenario):
    config,final=artifacts(scenario)
    policy=scenario.customer.conversation_policy.configuration_selection
    unknown=list(policy.discovery_fields) if policy else []
    events=[]
    if scenario.scenario_id=='S03':
        events.append(Event('Available models include Saga.','GENERAL_ENQUIRY'))
        unknown.remove('product_model')
    events.append(Event(FACT_REQUEST,'CREATE_ORDER'))
    for field in unknown:
        events.append(Event(f'Could you please provide your {LABELS[field]}?'))
        target=getattr(scenario.customer.ground_truth.configuration,field)
        events.append(Event(f'Available {LABELS[field]} options include {target}.','GENERAL_ENQUIRY'))
    events.extend([Event(config),Event(final),Event('Your order ORD-000001 has been created.',commit=True)])
    return events


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios,cls.knowledge=prepare_pilot()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def run_script(self,index=0,events=None,**kwargs):
        scenario=self.scenarios[index]
        script=ScriptedExecution(scenario,happy_events(scenario) if events is None else events)
        result=run_scenario(scenario,run_directory=self.root/f'run-{len(list(self.root.iterdir()))}',
                            knowledge=self.knowledge,execution_factory=script.factory,**kwargs)
        return result,script

    def validate(self,result,index=0):
        return validate_pilot_result(result,customer=self.scenarios[index].customer_view(),expectations=self.scenarios[index].evaluation)

    def test_s01_mocked_happy_path(self):
        result,script=self.run_script()
        validation=self.validate(result)
        self.assertEqual(validation.verdict,'PASS',validation)
        self.assertEqual(result.attempted_customer_messages[0],self.scenarios[0].customer.initial_message)
        self.assertEqual(result.dispatched_customer_messages,result.attempted_customer_messages)
        self.assertEqual(result.completed_turns,len(script.calls))
        self.assertEqual(len(result.public_record.public_history),2*len(script.calls))
        self.assertEqual(result.termination.decision.reason,'ORDER_CREATED_PUBLICLY_REPORTED')

    def test_s02_discovery_and_workflow_resumption(self):
        result,script=self.run_script(1)
        self.assertEqual(self.validate(result,1).verdict,'PASS')
        asks=[(m,k) for _,m,k in script.calls if m.startswith('What ')]
        self.assertEqual(len(asks),3)
        self.assertTrue(all(k is not None for _,k in asks))
        self.assertTrue(any("I'll choose Zebra" in m for _,m,_ in script.calls))

    def test_s03_enquiry_first_can_continue_under_scripted_classifier(self):
        result,script=self.run_script(2)
        self.assertEqual(self.validate(result,2).verdict,'PASS')
        self.assertIsNone(script.calls[1][0].workflow_state)
        self.assertEqual(script.calls[1][1],"For table model, I'll choose Saga.")
        self.assertIsNotNone(script.calls[0][2])
        self.assertEqual(len(result.final_customer_simulator_state.observed_target_offers),7)

    def test_s03_create_first_preserves_public_dead_end(self):
        result,script=self.run_script(2,[Event('Could you please provide your full name?','CREATE_ORDER')])
        self.assertEqual(result.termination.decision.reason,'SIMULATOR_UNINTERPRETABLE_RESPONSE')
        self.assertEqual(len(script.calls),1)
        self.assertIsNotNone(script.calls[0][2]) # supplied, but Order path did not consume it
        self.assertEqual(result.attempted_customer_messages,(self.scenarios[2].customer.initial_message,))
        self.assertEqual(self.validate(result,2).verdict,'FAIL')

    def test_information_boundary_provider_order_and_no_expectation_feedback(self):
        events=[];sim_inputs=[]
        def simulator(inputs):
            self.assertIsInstance(inputs,CustomerSimulatorInput)
            self.assertEqual(set(type(inputs).model_fields),{'customer','state','public_history'})
            sim_inputs.append(inputs); events.append('simulator')
            return step(inputs)
        def provider(message,history,*,knowledge):
            self.assertTrue(all(type(m) is PublicMessage for m in history))
            events.append('provider')
            return provide_support_knowledge(message,history,knowledge=knowledge)
        script=ScriptedExecution(self.scenarios[0],happy_events(self.scenarios[0]))
        def factory(store):
            execute=script.factory(store)
            def turn(*args,**kwargs):
                self.assertEqual(events[-1],'provider');events.append('runtime')
                return execute(*args,**kwargs)
            return turn
        result=run_scenario(self.scenarios[0],run_directory=self.root/'boundary',knowledge=self.knowledge,
                            simulator=simulator,knowledge_provider=provider,execution_factory=factory)
        before=tuple(events)
        self.validate(result)
        self.assertEqual(tuple(events),before)
        for inputs in sim_inputs:
            self.assertEqual(inputs.customer,self.scenarios[0].customer_view())
            self.assertNotIn('evaluation',inputs.model_dump())
            self.assertEqual(len(inputs.public_history)%2,0)

    def test_provider_failure_does_not_adopt_proposal_or_dispatch(self):
        provider=Mock(side_effect=ValueError('provider failed'))
        result,script=self.run_script(knowledge_provider=provider)
        self.assertEqual(result.termination.failure.phase,'PROVIDER')
        self.assertEqual(result.final_customer_simulator_state,CustomerSimulatorState())
        self.assertEqual(result.attempted_customer_messages,(self.scenarios[0].customer.initial_message,))
        self.assertFalse(result.dispatched_customer_messages);self.assertFalse(result.public_record.public_history)
        self.assertFalse(script.calls);self.assertEqual(result.provider_calls,1)
        self.assertFalse(result.traces[0].dispatched)

    def test_runtime_failure_no_unanswered_message_in_public_history(self):
        sim=Mock(wraps=step);runtime=Mock(side_effect=TurnFailure('classification'))
        result=run_scenario(self.scenarios[0],run_directory=self.root/'failed',knowledge=self.knowledge,
                            simulator=sim,execution_factory=lambda _:runtime)
        self.assertEqual(result.final_customer_simulator_state.turn_index,1)
        self.assertEqual(len(result.dispatched_customer_messages),1)
        self.assertFalse(result.public_record.public_history)
        self.assertFalse(json.loads(result.last_committed_session_json)['history'])
        self.assertTrue(result.traces[0].dispatched);self.assertFalse(result.traces[0].completed)
        self.assertEqual(result.traces[0].customer_message,result.dispatched_customer_messages[0])
        runtime.assert_called_once();sim.assert_called_once()

    def test_pending_failure_after_persistence_retains_all_evidence(self):
        events=happy_events(self.scenarios[0]);events[-1].fail_after_commit=True
        sim=Mock(wraps=step)
        result,script=self.run_script(events=events,simulator=sim)
        self.assertEqual(result.termination.failure.runtime_phase,'response composition')
        self.assertIsNotNone(result.termination.failure.pending_turn_json)
        self.assertEqual(result.persistence_observation.record_count,1)
        self.assertTrue(script.store.exists())
        self.assertEqual(result.business_terminal_observation.source,'PENDING_EXECUTION')
        self.assertEqual(result.business_terminal_observation.workflow_status,'COMPLETED')
        self.assertNotEqual(json.loads(result.last_committed_session_json)['workflow_state']['status'],'COMPLETED')
        self.assertEqual(len(result.dispatched_customer_messages),result.completed_turns+1)
        self.assertEqual(len(result.public_record.public_history),2*result.completed_turns)
        self.assertEqual(sim.call_count,result.runtime_calls)
        self.assertEqual(self.validate(result).verdict,'FAIL')

    def test_visible_creation_is_not_persistence_proof(self):
        result,_=self.run_script(events=[Event('Your order has been created.','CREATE_ORDER')])
        self.assertEqual(result.termination.decision.reason,'ORDER_CREATED_PUBLICLY_REPORTED')
        self.assertEqual(result.persistence_observation.status,'ABSENT')
        self.assertEqual(self.validate(result).verdict,'FAIL')

    def test_exclusive_stores_and_default_never_used(self):
        first,a=self.run_script();second,b=self.run_script()
        self.assertNotEqual(a.store,b.store)
        self.assertEqual(first.persistence_observation.record_count,1)
        self.assertEqual(second.persistence_observation.record_count,1)
        self.assertNotEqual(a.store.resolve(),DEFAULT_ORDER_STORE_PATH.resolve())
        original=a.store.read_bytes()
        with self.assertRaises(FileExistsError):
            run_scenario(self.scenarios[0],run_directory=a.store.parent,knowledge=self.knowledge,execution_factory=a.factory)
        self.assertEqual(a.store.read_bytes(),original)
        with self.assertRaises(ValueError): a3_execution(DEFAULT_ORDER_STORE_PATH)
        assembly=a3_execution(self.root/'isolated'/'orders.json')
        self.assertEqual(assembly.keywords['order_creation_processor'].keywords['order_store_path'],self.root/'isolated'/'orders.json')

    def test_public_history_equals_committed_projection_once(self):
        result,_=self.run_script()
        for trace in result.traces:
            after=json.loads(trace.session_after_json)['history']
            before=json.loads(trace.session_before_json)['history']
            self.assertEqual(after,before+[{'role':'user','content':trace.customer_message},{'role':'assistant','content':trace.support_response}])
        committed=json.loads(result.last_committed_session_json)['history']
        self.assertEqual([{'role':m.role,'content':m.text} for m in result.public_record.public_history],committed)

    def test_bad_runtime_history_stops_without_accepting_pair(self):
        script=ScriptedExecution(self.scenarios[0],happy_events(self.scenarios[0]))
        def factory(path):
            execute=script.factory(path)
            def turn(*args,**kwargs):
                result=execute(*args,**kwargs)
                result.session.history.append(result.session.history[-1])
                return result
            return turn
        result=run_scenario(self.scenarios[0],run_directory=self.root/'bad-history',knowledge=self.knowledge,execution_factory=factory)
        self.assertEqual(result.termination.failure.phase,'RUNNER_INTEGRITY')
        self.assertFalse(result.public_record.public_history)
        self.assertIsNotNone(result.traces[0].runtime_result_json)

    def test_repeated_identical_approvals_preserved(self):
        events=happy_events(self.scenarios[0]);events.insert(2,deepcopy(events[1]));events.insert(4,deepcopy(events[3]))
        result,_=self.run_script(events=events)
        self.assertEqual(self.validate(result).verdict,'PASS')
        receipts=result.final_customer_simulator_state.approval_receipts
        self.assertEqual([r.repeated for r in receipts],[False,True,False,True])
        self.assertEqual(len(receipts),4)

    def test_parser_limitations_remain_actual_public_text(self):
        for response in ['We offer several choices. Available models include Saga.', 'Available models include:\n- Saga']:
            result,_=self.run_script(2,[Event(response,'GENERAL_ENQUIRY')])
            self.assertEqual(result.public_record.public_history[-1].text,response)
            self.assertEqual(result.termination.decision.reason,'SIMULATOR_UNINTERPRETABLE_RESPONSE')

    def test_serialization_is_immutable_and_unavailable_evidence_explicit(self):
        result,script=self.run_script()
        before=result.model_dump_json()
        script.results[-1].session.workflow_state.customer_name='Changed afterwards'
        self.assertEqual(result.model_dump_json(),before)
        with self.assertRaises(ValidationError): result.completed_turns=999
        self.assertTrue(result.traces[0].unavailable_evidence)
        public=json.loads(Path(result.outputs.public_history).read_text())
        self.assertEqual(set(public),{'run_id','public_history'})
        self.assertNotIn('workflow_state',public)
        lines=[json.loads(line) for line in Path(result.outputs.trace).read_text().splitlines()]
        self.assertEqual(len(lines),len(result.traces)+1)
        self.assertEqual(lines[-1]['terminal']['kind'],'PUBLIC_STOP')

    def test_pilot_inconclusive_and_invariants_not_scored(self):
        result,_=self.run_script()
        changed=result.model_copy(update={'business_terminal_observation':BusinessTerminalObservation(source='UNAVAILABLE')})
        validation=self.validate(changed)
        self.assertEqual(validation.verdict,'INCONCLUSIVE')
        self.assertFalse(any(c.name.startswith('I') for c in validation.checks))
        expectations=self.scenarios[0].evaluation.model_copy(update={'invariants':None})
        self.assertEqual(validate_pilot_result(result,customer=self.scenarios[0].customer_view(),expectations=expectations).verdict,'PASS')
        with self.assertRaises(ValidationError): PilotValidationResult(verdict='PASS',checks=(PilotCheck(name='x',status='UNKNOWN',explanation='missing'),))

    def test_post_run_mismatches_are_not_repaired(self):
        result,_=self.run_script()
        for field,value in [('customer_name','Wrong'),('product_sku','WRONG'),('total_price','1')]:
            record=json.loads(result.persistence_observation.records_json[0]);record['order'][field]=value
            observation=PersistenceObservation(status='VALID',record_count=1,records_json=(json.dumps(record),))
            changed=result.model_copy(update={'persistence_observation':observation})
            self.assertEqual(self.validate(changed).verdict,'FAIL')
            self.assertEqual(json.loads(changed.persistence_observation.records_json[0])['order'][field],value)

    def test_persistence_statuses_and_duplicates(self):
        path=self.root/'orders.json'
        self.assertEqual(observe_persistence(path).status,'ABSENT')
        path.write_text('[]');self.assertEqual(observe_persistence(path).record_count,0)
        path.write_text('broken');self.assertEqual(observe_persistence(path).status,'MALFORMED')
        path.write_text('{}');self.assertEqual(observe_persistence(path).status,'MALFORMED')
        result,_=self.run_script();record=json.loads(result.persistence_observation.records_json[0])
        path.write_text(json.dumps([record,record]));self.assertEqual(observe_persistence(path).status,'MALFORMED')
        with patch('pathlib.Path.read_text',side_effect=PermissionError('denied')):
            self.assertEqual(observe_persistence(path).status,'UNREADABLE')

    def persistence_fault(self, status, observation_number):
        """Inject an invalid store at a specific read; retain its exact bytes."""
        calls = []
        def observe(path):
            calls.append(path)
            if len(calls) == observation_number:
                path.write_bytes(b'broken store bytes')
                if status == 'UNREADABLE':
                    with patch('pathlib.Path.read_text', side_effect=PermissionError('denied')):
                        return observe_persistence(path)
            return observe_persistence(path)
        return calls, observe

    def test_invalid_persistence_after_turn_stops_all_further_calls(self):
        for status in ('MALFORMED', 'UNREADABLE'):
            with self.subTest(status=status):
                calls, observe = self.persistence_fault(status, 2)
                sim = Mock(wraps=step)
                provider = Mock(wraps=provide_support_knowledge)
                with patch('evaluation.experiment_runner.observe_persistence', side_effect=observe):
                    result, script = self.run_script(simulator=sim, knowledge_provider=provider)
                self.assertEqual((sim.call_count, provider.call_count, len(script.calls)), (1, 1, 1))
                self.assertEqual(len(calls), 2)
                self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
                self.assertEqual(result.completed_turns, 1)
                self.assertEqual(result.dispatched_customer_messages, result.attempted_customer_messages)
                self.assertEqual(result.last_committed_session_json, script.results[0].session.model_dump_json())
                history = script.results[0].session.history
                self.assertEqual(result.public_record.public_history,
                                 tuple(PublicMessage(role=m.role, text=m.content) for m in history))
                self.assertEqual(result.traces[0].persistence_after, result.persistence_observation)
                self.assertEqual(result.persistence_observation.status, status)
                self.assertIsNone(result.persistence_observation.record_count)
                self.assertEqual(script.store.read_bytes(), b'broken store bytes')
                self.assertIsNotNone(result.traces[0].runtime_result_json)
                self.assertGreaterEqual(result.timing.runtime_elapsed, 0)
                validation = self.validate(result)
                self.assertEqual(validation.verdict, 'FAIL')
                self.assertEqual(next(c.status for c in validation.checks if c.name == 'one_persisted_order'), 'UNKNOWN')
                saved = json.loads(Path(result.outputs.result).read_text())
                self.assertEqual(saved['persistence_observation']['status'], status)

    def test_invalid_persistence_before_dispatch_preserves_proposal(self):
        for status in ('MALFORMED', 'UNREADABLE'):
            calls, observe = self.persistence_fault(status, 1)
            sim = Mock(wraps=step)
            provider = Mock(wraps=provide_support_knowledge)
            with patch('evaluation.experiment_runner.observe_persistence', side_effect=observe):
                result, script = self.run_script(simulator=sim, knowledge_provider=provider)
            self.assertEqual((sim.call_count, provider.call_count, len(script.calls)), (1, 0, 0))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(result.attempted_customer_messages), 1)
            self.assertEqual(result.dispatched_customer_messages, ())
            self.assertEqual(result.public_record.public_history, ())
            self.assertEqual(result.final_customer_simulator_state, CustomerSimulatorState())
            self.assertFalse(result.traces[0].dispatched)
            self.assertEqual(result.traces[0].persistence_before.status, status)
            self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
            self.assertEqual(script.store.read_bytes(), b'broken store bytes')

    def test_invalid_persistence_preserves_pending_runtime_failure(self):
        for status in ('MALFORMED', 'UNREADABLE'):
            events = happy_events(self.scenarios[0])
            events[-1].fail_after_commit = True
            calls, observe = self.persistence_fault(status, 2 * len(events))
            sim = Mock(wraps=step)
            with patch('evaluation.experiment_runner.observe_persistence', side_effect=observe):
                result, script = self.run_script(events=events, simulator=sim)
            self.assertEqual(result.termination.failure.phase, 'RUNTIME')
            self.assertIsNotNone(result.termination.failure.pending_turn_json)
            self.assertEqual(result.traces[-1].technical_failure, result.termination.failure)
            self.assertEqual(result.secondary_failures[0].phase, 'RUNNER_INTEGRITY')
            self.assertEqual(result.persistence_observation.status, status)
            self.assertEqual(result.completed_turns, len(events) - 1)
            self.assertEqual(sim.call_count, len(script.calls))
            self.assertEqual(result.last_committed_session_json, script.results[-1].session.model_dump_json())
            self.assertEqual(script.store.read_bytes(), b'broken store bytes')

    def test_invalid_final_observation_is_technical_not_public_termination(self):
        for status in ('MALFORMED', 'UNREADABLE'):
            calls, observe = self.persistence_fault(status, 2 * len(happy_events(self.scenarios[0])) + 1)
            with patch('evaluation.experiment_runner.observe_persistence', side_effect=observe):
                result, script = self.run_script()
            self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
            self.assertEqual(result.final_customer_simulator_state.stop_decision.reason, 'ORDER_CREATED_PUBLICLY_REPORTED')
            self.assertEqual(result.persistence_observation.status, status)
            self.assertEqual(result.completed_turns, len(script.calls))
            self.assertEqual(self.validate(result).verdict, 'FAIL')

    def test_setup_simulator_and_output_failures_do_not_continue(self):
        script=ScriptedExecution(self.scenarios[0],happy_events(self.scenarios[0]))
        with patch('evaluation.experiment_runner.validate_repository',side_effect=ValueError('bad provenance')):
            result=run_scenario(self.scenarios[0],run_directory=self.root/'setup',knowledge=self.knowledge,execution_factory=script.factory)
        self.assertEqual(result.termination.failure.phase,'SETUP');self.assertFalse(script.calls)
        result,_=self.run_script(simulator=Mock(side_effect=ValueError('simulator failed')))
        self.assertEqual(result.termination.failure.phase,'SIMULATOR')
        with patch('evaluation.experiment_runner._append_trace',side_effect=OSError('disk failed')):
            result,script=self.run_script()
        self.assertEqual(result.termination.failure.phase,'OUTPUT');self.assertEqual(len(script.calls),1)

    def test_architecture_label_does_not_change_customer_behavior(self):
        a,_=self.run_script(architecture='A3')
        b,_=self.run_script(architecture='TEST_OTHER')
        self.assertEqual(a.dispatched_customer_messages,b.dispatched_customer_messages)
        self.assertEqual(a.public_record.public_history,b.public_record.public_history)

    def test_final_output_failure_preserves_public_stop_and_fails_validation(self):
        from evaluation.experiment_runner import _write_json

        def write(path, value):
            if path.name == 'result.json':
                raise OSError('Final result could not be written')
            _write_json(path, value)

        with patch('evaluation.experiment_runner._write_json', side_effect=write):
            result, script = self.run_script()
        self.assertIsInstance(result.termination, PublicStop)
        self.assertEqual(result.secondary_failures[0].phase, 'OUTPUT')
        self.assertEqual(result.runtime_calls, len(script.calls))
        self.assertEqual(result.persistence_observation.record_count, 1)
        self.assertEqual(self.validate(result).verdict, 'FAIL')

    def test_timing_and_call_counts_are_development_only(self):
        result,script=self.run_script()
        self.assertEqual(result.runtime_calls,len(script.calls))
        self.assertEqual(result.provider_calls,len(result.attempted_customer_messages))
        self.assertGreaterEqual(result.timing.full_run_elapsed,0)
        self.assertAlmostEqual(result.timing.runtime_elapsed,sum(t.runtime_elapsed for t in result.traces))
        self.assertNotIn('tokens',result.timing.model_dump())

    def test_prepare_snapshot_loaded_once(self):
        from evaluation.static_product_knowledge import load_static_product_knowledge
        with patch('evaluation.experiment_runner.load_static_product_knowledge',wraps=load_static_product_knowledge) as load:
            scenarios,knowledge=prepare_pilot()
        load.assert_called_once()
        self.assertEqual(knowledge.source_sha256,scenarios[0].fixtures.product_catalog.sha256)


if __name__=='__main__':
    unittest.main()
