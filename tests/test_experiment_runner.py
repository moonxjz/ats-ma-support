"""CS1-D tests use scripted runtime boundaries, never real model conversations."""

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from entity.business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from entity.classification import ClassifierResult, MessageCategory
from workflow.confirmation_presentation import render_configuration_summary, render_provisional_order
from entity.conversation import ConversationSession, PendingTurn, TurnResult, TurnStatus
from workflow.conversation_runtime import TurnFailure
from entity.conversation import ConversationMessage
from workflow.order.order_creation_order_store import DEFAULT_ORDER_STORE_PATH
from entity.order_record import OrderCreationRecord
from entity.order_creation_state import FinalOrderSnapshot, OrderCreationState, OrderCreationStage, OrderWorkflowStatus
from entity.routing import RootExecutionResult
from agents.root_agent import route_message
from entity.support import CustomerResponse, SupportAction, SupportActionResult, SupportOutcome
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
        classification=ClassifierResult(categories=[MessageCategory(event.category)],confidence=1.0,explanation='scripted public trajectory')
        route=route_message(classification,session.workflow_state)[0]
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


# CS2-A2 tests use injected model replies and the existing scripted ATS boundary.
# No live client, live scenario entry point, or model discovery is used.
from types import SimpleNamespace
from hashlib import sha256
from evaluation import llm_customer_simulator as cs2
from evaluation import llm_public_evidence as cs2_evidence
from evaluation.llm_simulator_integration import (
    make_llm_simulator, make_simulator_metadata, verify_cs2_public_approvals,
    SimulatorMetadata, SimulatorAttemptTrace,
)


def proposed_reply(**kwargs):
    """Mock proposer reads only the actual customer/public prompt payload."""
    payload = json.loads(kwargs['messages'][1]['content'])
    from evaluation.scenario_spec import CustomerScenario
    customer = CustomerScenario.model_validate_json(json.dumps(payload['customer']))
    text = payload['public_history'][-1]['text']
    index = len(payload['public_history']) - 1
    artifact = cs2_evidence.extract_artifact(text, index)
    if artifact:
        request = cs2_evidence.approval_requests(text, index, artifact)[0]
        proposal = {'kind': 'CUSTOMER_TURN', 'actions': [{
            'kind': 'CONFIRM_CONFIGURATION' if artifact.kind == 'CONFIGURATION' else 'CONFIRM_FINAL_ORDER',
            'artifact': artifact.evidence.model_dump(mode='json', include={'message_index', 'quote'}),
            'approval_request': request.model_dump(mode='json', include={'message_index', 'quote'})}]}
    elif 'has been created' in text:
        proposal = {'kind': 'STOP', 'category': 'ORDER_CREATED',
                    'evidence': [cs2_evidence.reference(text, index).model_dump(mode='json', include={'message_index', 'quote'})]}
    else:
        items = []
        for request in cs2_evidence.requested_fields(text, index):
            value = cs2.customer_value(customer, request.field)
            typed = {'kind': 'ABSENT'} if value is None else {'kind': 'QUANTITY' if type(value) is int else 'TEXT', 'value': value}
            items.append({'field': request.field, 'value': typed, 'request_evidence': [request.evidence.model_dump(mode='json', include={'message_index', 'quote'})]})
        proposal = {'kind': 'CUSTOMER_TURN', 'actions': [{'kind': 'PROVIDE_INFORMATION', 'items': items}]}
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps({'proposal': proposal}), thinking='DO_NOT_RECORD_THINKING'), done=True)


class CS2RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios, cls.knowledge = prepare_pilot()
        cls.metadata = make_simulator_metadata(provenance='MOCK')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_cs2(self, *, chat=None, events=None, **kwargs):
        chat = Mock(side_effect=proposed_reply) if chat is None else chat
        simulator = make_llm_simulator(chat_fn=chat)
        script = ScriptedExecution(self.scenarios[0], happy_events(self.scenarios[0]) if events is None else events)
        result = run_scenario(self.scenarios[0], run_directory=self.root/f'run-{len(list(self.root.iterdir()))}',
            knowledge=self.knowledge, simulator=simulator, simulator_metadata=self.metadata,
            simulator_diagnostics=simulator.take_diagnostics, execution_factory=script.factory, **kwargs)
        return result, script, chat

    def validate(self, result, **kwargs):
        return validate_pilot_result(result, customer=self.scenarios[0].customer_view(),
            expectations=self.scenarios[0].evaluation, artifact_verifier=verify_cs2_public_approvals, **kwargs)

    def events(self, result):
        return [json.loads(line) for line in Path(result.outputs.trace).read_text().splitlines()]

    def test_mocked_completion_and_linkage(self):
        result, script, chat = self.run_cs2()
        self.assertEqual(self.validate(result).verdict, 'PASS')
        events = self.events(result)
        attempts = [e['attempt'] for e in events if e['kind'] == 'SIMULATOR_ATTEMPT']
        runtime = [e for e in events if e['kind'] == 'RUNTIME_TURN']
        self.assertEqual(len(attempts), len(runtime) + 1)
        self.assertEqual(attempts[0]['model_calls'], 0)
        self.assertEqual(attempts[0]['guard_outcome'], 'BYPASSED')
        self.assertEqual(attempts[0]['rendered_customer_message'], self.scenarios[0].customer.initial_message)
        self.assertEqual(attempts[-1]['outcome'], 'PUBLIC_STOP')
        self.assertEqual(events[-1]['kind'], 'TERMINATION')
        for event in runtime:
            attempt = attempts[event['simulator_attempt_index']]
            self.assertEqual(event['turn']['customer_message'], attempt['rendered_customer_message'])
            self.assertNotIn('decision', attempt)
            self.assertNotIn('customer_turn', attempt)
            self.assertNotIn('proposed_customer_state', attempt)
        self.assertEqual(chat.call_count, len(attempts)-1)

    def test_attempt_written_before_provider(self):
        seen = []
        def provider(message, history, *, knowledge):
            path = next(self.root.glob('run-*/trace.jsonl'))
            last = json.loads(path.read_text().splitlines()[-1])
            self.assertEqual(last['kind'], 'SIMULATOR_ATTEMPT')
            self.assertEqual(last['attempt']['rendered_customer_message'], message)
            seen.append(message)
            return provide_support_knowledge(message, history, knowledge=knowledge)
        result, script, _ = self.run_cs2(knowledge_provider=provider)
        self.assertEqual(tuple(seen), result.dispatched_customer_messages)
        self.assertEqual(len(script.calls), len(seen))

    def assert_failed_second_attempt(self, chat, code):
        provider = Mock(wraps=provide_support_knowledge)
        result, script, chat = self.run_cs2(chat=chat, knowledge_provider=provider)
        self.assertEqual(result.termination.failure.phase, 'SIMULATOR')
        self.assertEqual(result.simulator_attempts[-1].failure.code, code)
        self.assertEqual(result.simulator_attempts[-1].outcome, 'FAILURE')
        self.assertEqual(len(script.calls), 1)  # Only turn zero preceded this failure.
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result.runtime_calls, 1)
        self.assertEqual(result.completed_turns, 1)
        self.assertEqual(result.final_customer_simulator_state, result.traces[0].proposed_customer_state)
        self.assertEqual(len(result.attempted_customer_messages), 1)
        self.assertEqual(len(result.simulator_attempts), 2)
        chat.assert_called_once()
        self.assertEqual([e['kind'] for e in self.events(result)],
                         ['SIMULATOR_ATTEMPT', 'RUNTIME_TURN', 'SIMULATOR_ATTEMPT', 'TERMINATION'])
        return result

    def test_invalid_json_retained_no_dispatch(self):
        raw = 'not JSON PRIVATE_RAW_MARKER'
        result = self.assert_failed_second_attempt(Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=raw))), 'INVALID_PROPOSAL')
        self.assertEqual(result.simulator_attempts[-1].raw_model_content, raw)
        self.assertIsNone(result.simulator_attempts[-1].parsed_proposal_json)

    def test_schema_and_duplicate_json_failures(self):
        for raw in ('{"proposal":{},"proposal":{}}', '{"proposal":{"kind":"INITIAL_MESSAGE"}}',
                    '{"proposal":{"kind":"CUSTOMER_TURN","actions":[],"customer_text":"PRIVATE_RAW_MARKER"}}', ''):
            with self.subTest(raw=raw):
                self.assert_failed_second_attempt(Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=raw))), 'INVALID_PROPOSAL')

    def test_guard_rejection_preserves_parsed_proposal(self):
        def wrong(**kwargs):
            reply = proposed_reply(**kwargs)
            value = json.loads(reply.message.content)
            value['proposal']['actions'][0]['items'][0]['value']['value'] = 'PRIVATE_RAW_MARKER'
            reply.message.content = json.dumps(value)
            return reply
        result = self.assert_failed_second_attempt(Mock(side_effect=wrong), 'GUARD_REJECTED')
        self.assertEqual(result.simulator_attempts[-1].guard_outcome, 'REJECTED')
        self.assertIn('PRIVATE_RAW_MARKER', result.simulator_attempts[-1].parsed_proposal_json)

    def test_model_exception_no_retry(self):
        result = self.assert_failed_second_attempt(Mock(side_effect=RuntimeError('PRIVATE_RAW_MARKER')), 'MODEL_FAILURE')
        self.assertIsNone(result.simulator_attempts[-1].raw_model_content)
        self.assertIsNotNone(result.simulator_attempts[-1].model_elapsed)

    def test_raw_content_only_in_hidden_trace(self):
        raw = '{"proposal":{"kind":"PRIVATE_RAW_MARKER"}}'
        result = self.assert_failed_second_attempt(Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=raw, thinking='SECRET_THINKING'))), 'INVALID_PROPOSAL')
        self.assertIn('PRIVATE_RAW_MARKER', Path(result.outputs.trace).read_text())
        for path in (result.outputs.result, result.outputs.manifest, result.outputs.public_history):
            self.assertNotIn('PRIVATE_RAW_MARKER', Path(path).read_text())
        self.assertNotIn('PRIVATE_RAW_MARKER', result.termination.model_dump_json())
        self.assertNotIn('SECRET_THINKING', Path(result.outputs.trace).read_text())
        self.assertNotIn('SECRET_THINKING', repr(result.simulator_attempts))

    def test_summary_and_fingerprint(self):
        result, _, chat = self.run_cs2()
        summary = result.simulator_summary
        self.assertEqual(summary.diagnostics_completeness, 'COMPLETE')
        self.assertEqual(summary.attempt_count, len(result.simulator_attempts))
        self.assertEqual(summary.model_call_count, chat.call_count)
        self.assertAlmostEqual(summary.elapsed, sum(a.elapsed for a in result.simulator_attempts))
        self.assertAlmostEqual(summary.model_elapsed, sum(a.model_elapsed or 0 for a in result.simulator_attempts))
        self.assertIsNone(summary.failure_code)
        for call, attempt in zip(chat.call_args_list, result.simulator_attempts[1:]):
            self.assertEqual(attempt.input_sha256, sha256(call.kwargs['messages'][1]['content'].encode()).hexdigest())
        saved = json.loads(Path(result.outputs.result).read_text())
        self.assertNotIn('simulator_attempts', saved)
        self.assertNotIn('parsed_proposal_json', json.dumps(saved))
        self.assertEqual(saved['simulator_summary'], summary.model_dump(mode='json'))

    def test_manifest_mock_provenance(self):
        result, _, _ = self.run_cs2()
        manifest = json.loads(Path(result.outputs.manifest).read_text())
        self.assertEqual(manifest['simulator'], self.metadata.model_dump(mode='json'))
        self.assertEqual(manifest['simulator']['provenance'], 'MOCK')
        self.assertIsNone(manifest['simulator']['model_digest'])
        self.assertIsNone(manifest['simulator']['source_commit'])
        self.assertEqual(manifest['simulator']['trace_schema_version'], 'CS2-2')
        with self.assertRaises(ValueError):
            make_simulator_metadata(provenance='MOCK', model_digest='invented')
        with self.assertRaises(ValueError):
            make_simulator_metadata(provenance='LIVE_VERIFIED')

    def test_provider_failure_keeps_prior_state(self):
        provider = Mock(side_effect=ValueError('provider failed'))
        result, script, chat = self.run_cs2(knowledge_provider=provider)
        self.assertEqual(result.termination.failure.phase, 'PROVIDER')
        self.assertEqual(result.final_customer_simulator_state, CustomerSimulatorState())
        self.assertEqual(len(result.simulator_attempts), 1)
        self.assertEqual(result.simulator_attempts[0].outcome, 'CUSTOMER_TURN')
        self.assertFalse(result.traces[0].dispatched)
        self.assertFalse(script.calls)
        chat.assert_not_called()

    def test_runtime_failure_pending_write_preserved(self):
        events = happy_events(self.scenarios[0])
        events[-1].fail_after_commit = True
        result, script, _ = self.run_cs2(events=events)
        self.assertEqual(result.termination.failure.phase, 'RUNTIME')
        self.assertIsNotNone(result.termination.failure.pending_turn_json)
        self.assertEqual(result.persistence_observation.record_count, 1)
        self.assertTrue(result.traces[-1].dispatched)
        self.assertFalse(result.traces[-1].completed)
        self.assertEqual(result.final_customer_simulator_state, result.traces[-1].proposed_customer_state)
        self.assertEqual(result.completed_turns, result.runtime_calls - 1)

    def test_recording_failure_prevents_dispatch(self):
        def fail_attempt(path, event):
            if event.kind == 'SIMULATOR_ATTEMPT':
                raise OSError('disk failed')
        with patch('evaluation.experiment_runner._append_event', side_effect=fail_attempt):
            result, script, chat = self.run_cs2()
        self.assertEqual(result.termination.failure.phase, 'OUTPUT')
        self.assertEqual(len(result.simulator_attempts), 1)
        self.assertEqual(result.provider_calls, 0)
        self.assertEqual(result.runtime_calls, 0)
        self.assertEqual(result.final_customer_simulator_state, CustomerSimulatorState())
        self.assertFalse(script.calls)
        chat.assert_not_called()

    def test_recording_failure_retains_primary_simulator_failure(self):
        from evaluation.experiment_runner import _append_event
        def fail_second(path, event):
            if event.kind == 'SIMULATOR_ATTEMPT' and event.attempt.attempt_index == 1:
                raise OSError('disk failed')
            return _append_event(path, event)
        chat = Mock(side_effect=RuntimeError('PRIVATE_RAW_MARKER'))
        with patch('evaluation.experiment_runner._append_event', side_effect=fail_second):
            result, script, _ = self.run_cs2(chat=chat)
        self.assertEqual(result.termination.failure.phase, 'SIMULATOR')
        self.assertEqual(result.secondary_failures[0].phase, 'OUTPUT')
        self.assertEqual(result.simulator_attempts[-1].failure.message, 'PRIVATE_RAW_MARKER')
        self.assertEqual(len(script.calls), 1)
        self.assertNotIn('PRIVATE_RAW_MARKER', Path(result.outputs.result).read_text())

    def recording_boundary(self, diagnostics, error=None, write_failure=False):
        simulator = Mock(side_effect=error if error is not None else step)
        provider, runtime = Mock(), Mock()
        from evaluation.experiment_runner import _append_event
        def write(path, event):
            if write_failure and event.kind == 'SIMULATOR_ATTEMPT_RECORDING_FAILURE':
                raise OSError('PRIVATE_WRITE')
            return _append_event(path, event)
        with patch('evaluation.experiment_runner._append_event', side_effect=write):
            result = run_scenario(self.scenarios[0], run_directory=self.root/'boundary',
                knowledge=self.knowledge, simulator=simulator, simulator_metadata=self.metadata,
                simulator_diagnostics=diagnostics, knowledge_provider=provider,
                execution_factory=lambda store: runtime)
        simulator.assert_called_once()
        diagnostics.assert_called_once()
        provider.assert_not_called()
        runtime.assert_not_called()
        self.assertEqual(result.provider_calls, 0)
        self.assertEqual(result.runtime_calls, 0)
        self.assertEqual(result.final_customer_simulator_state, CustomerSimulatorState())
        self.assertEqual(result.public_record.public_history, ())
        self.assertEqual(result.attempted_customer_messages, ())
        self.assertEqual(result.simulator_attempts, ())
        self.assertEqual(len(result.simulator_recording_failures), 1)
        event = result.simulator_recording_failures[0]
        self.assertEqual(event.attempt_index, 0)
        self.assertEqual(event.input_history_length, 0)
        self.assertGreaterEqual(event.elapsed, 0)
        summary = result.simulator_summary
        self.assertEqual(summary.attempt_count, 1)
        self.assertEqual(summary.diagnostics_completeness, 'INCOMPLETE')
        self.assertIsNone(summary.model_call_count)
        self.assertIsNone(summary.model_elapsed)
        self.assertEqual(summary.elapsed, event.elapsed)
        saved = Path(result.outputs.result).read_text()
        self.assertNotIn('PRIVATE', saved)
        self.assertNotIn('simulator_recording_failures', json.loads(saved))
        if not write_failure:
            from pydantic import TypeAdapter
            from evaluation.experiment_runner import TraceEvent
            self.assertEqual(TypeAdapter(TraceEvent).validate_python(self.events(result)[0]), event)
            self.assertEqual(self.events(result)[-1]['simulator_attempt_index'], 0)
        return result, event

    def test_diagnostics_retrieval_failure(self):
        result, event = self.recording_boundary(Mock(side_effect=RuntimeError('PRIVATE_DIAGNOSTICS')))
        self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
        self.assertEqual(event.invocation_outcome, 'RETURNED')
        self.assertEqual(event.returned_decision_kind, 'CUSTOMER_TURN')
        self.assertEqual(event.rendered_customer_message, self.scenarios[0].customer.initial_message)
        self.assertEqual(event.recording_stage, 'RETRIEVAL')
        self.assertIsNone(event.validated_diagnostics)
        self.assertIsNone(event.simulator_failure)
        self.assertFalse(result.secondary_failures)

    def test_malformed_diagnostics_no_invented_fields(self):
        result, event = self.recording_boundary(Mock(return_value={'model_calls': 'PRIVATE_BAD'}))
        self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
        self.assertEqual(event.recording_stage, 'VALIDATION')
        self.assertIsNone(event.validated_diagnostics)
        for name in ('model_calls', 'model_elapsed', 'raw_model_content', 'parsed_proposal_json', 'guard_outcome'):
            self.assertNotIn(name, event.model_dump())

    def test_cs2_failure_and_unavailable_diagnostics(self):
        result, event = self.recording_boundary(Mock(side_effect=ValueError('PRIVATE_DIAGNOSTICS')),
            error=cs2.CS2Failure('GUARD_REJECTED', 'PRIVATE_PROPOSAL'))
        self.assertEqual(result.termination.failure.phase, 'SIMULATOR')
        self.assertEqual(result.termination.failure.exception_type, 'CS2Failure')
        self.assertEqual(event.simulator_failure.code, 'GUARD_REJECTED')
        self.assertEqual(event.invocation_outcome, 'RAISED')
        self.assertIsNone(event.returned_decision_kind)
        self.assertEqual(result.secondary_failures, (event.recording_failure,))
        self.assertEqual(result.simulator_summary.failure_code, 'GUARD_REJECTED')

    def test_unexpected_failure_and_unavailable_diagnostics(self):
        result, event = self.recording_boundary(Mock(side_effect=ValueError('PRIVATE_DIAGNOSTICS')),
            error=RuntimeError('PRIVATE_SIMULATOR'))
        self.assertEqual(result.termination.failure.phase, 'SIMULATOR')
        self.assertEqual(result.termination.failure.exception_type, 'RuntimeError')
        self.assertEqual(event.simulator_failure.code, 'UNEXPECTED_EXCEPTION')
        self.assertEqual(result.secondary_failures, (event.recording_failure,))

    def test_recording_failure_event_write_failure_retained(self):
        result, event = self.recording_boundary(Mock(side_effect=ValueError('PRIVATE_DIAGNOSTICS')),
            error=cs2.CS2Failure('MODEL_FAILURE', 'PRIVATE_MODEL'), write_failure=True)
        self.assertEqual(result.termination.failure.phase, 'SIMULATOR')
        self.assertEqual([e.phase for e in result.secondary_failures], ['RUNNER_INTEGRITY', 'OUTPUT'])

    def test_validated_diagnostics_retained_if_attempt_invalid(self):
        result, event = self.recording_boundary(Mock(return_value={
            'guard_outcome': 'ACCEPTED', 'model_calls': 1, 'model_elapsed': None,
            'raw_model_content': 'PRIVATE_RAW', 'parsed_proposal_json': '{}'}))
        self.assertEqual(event.recording_stage, 'ATTEMPT_CONSTRUCTION')
        self.assertEqual(event.validated_diagnostics.raw_model_content, 'PRIVATE_RAW')
        self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')

    def test_later_diagnostics_failure_retains_prior_state_and_known_attempt(self):
        simulator = make_llm_simulator(chat_fn=Mock(side_effect=proposed_reply))
        diagnostics = Mock()
        def retrieve():
            if diagnostics.call_count == 1:
                return simulator.take_diagnostics()
            raise ValueError('unavailable')
        diagnostics.side_effect = retrieve
        script = ScriptedExecution(self.scenarios[0], happy_events(self.scenarios[0]))
        result = run_scenario(self.scenarios[0], run_directory=self.root/'later', knowledge=self.knowledge,
            simulator=simulator, simulator_metadata=self.metadata, simulator_diagnostics=diagnostics,
            execution_factory=script.factory)
        self.assertEqual(diagnostics.call_count, 2)
        self.assertEqual(result.runtime_calls, 1)
        self.assertEqual(result.provider_calls, 1)
        self.assertEqual(result.final_customer_simulator_state, result.traces[0].proposed_customer_state)
        self.assertEqual(len(result.public_record.public_history), 2)
        self.assertEqual(len(result.simulator_attempts), 1)
        self.assertEqual(result.simulator_recording_failures[0].attempt_index, 1)
        self.assertEqual(result.simulator_summary.attempt_count, 2)
        self.assertIsNone(result.simulator_summary.model_call_count)

    def test_public_stop_diagnostics_failure_does_not_adopt_stop(self):
        simulator = make_llm_simulator(chat_fn=Mock(side_effect=proposed_reply))
        last = []
        def invoke(inputs):
            proposal = simulator(inputs)
            last[:] = [proposal]
            return proposal
        def retrieve():
            if isinstance(last[0].decision, cs2.StopDecision):
                raise ValueError('stop diagnostics unavailable')
            return simulator.take_diagnostics()
        script = ScriptedExecution(self.scenarios[0], happy_events(self.scenarios[0]))
        result = run_scenario(self.scenarios[0], run_directory=self.root/'stop', knowledge=self.knowledge,
            simulator=invoke, simulator_metadata=self.metadata, simulator_diagnostics=retrieve,
            execution_factory=script.factory)
        event = result.simulator_recording_failures[0]
        self.assertEqual(event.returned_decision_kind, 'PUBLIC_STOP')
        self.assertIsNone(event.rendered_customer_message)
        self.assertIsNone(event.validated_diagnostics)
        self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
        self.assertEqual(result.final_customer_simulator_state, result.traces[-1].proposed_customer_state)
        self.assertIsNone(result.final_customer_simulator_state.stop_decision)
        self.assertEqual(result.runtime_calls, len(result.traces))
        self.assertEqual(result.provider_calls, len(result.traces))

    def test_success_recording_failure_event_write_failure(self):
        result, event = self.recording_boundary(Mock(side_effect=ValueError('PRIVATE_DIAGNOSTICS')),
            write_failure=True)
        self.assertEqual(result.termination.failure.phase, 'RUNNER_INTEGRITY')
        self.assertEqual([e.phase for e in result.secondary_failures], ['OUTPUT'])

    def test_cs2_requires_verifier(self):
        result, _, _ = self.run_cs2()
        validation = validate_pilot_result(result, customer=self.scenarios[0].customer_view(), expectations=self.scenarios[0].evaluation)
        self.assertEqual(validation.verdict, 'FAIL')
        self.assertEqual(validation.checks[0].name, 'cs2_verifier_required')

    def test_cs2_framing_bypasses_cs1_parser_in_validation(self):
        events = happy_events(self.scenarios[0])
        events[-3].text = 'Many thanks for your patience today!\n\n' + events[-3].text.replace('Please confirm the configuration.', 'If everything looks right, please approve this configuration.')
        events[-2].text = 'Many thanks for your patience today!\n\n' + events[-2].text
        from evaluation.public_observation import observe_public_response
        self.assertIsNone(observe_public_response(events[-2].text, 1).artifact.approval_request)
        result, _, _ = self.run_cs2(events=events)
        with patch('evaluation.experiment_runner.observe_public_response', side_effect=AssertionError('CS1 parser called')):
            self.assertEqual(self.validate(result).verdict, 'PASS')

    def test_audit_reconstructs_both_receipts(self):
        result, _, _ = self.run_cs2()
        audit = verify_cs2_public_approvals(result.public_record.public_history, customer=self.scenarios[0].customer_view())
        self.assertFalse(audit.errors)
        self.assertEqual(tuple(a.receipt for a in audit.approvals), result.final_customer_simulator_state.approval_receipts)
        self.assertEqual([a.receipt.kind for a in audit.approvals], ['CONFIGURATION', 'FINAL_ORDER'])

    def test_tampered_saved_receipt_fails(self):
        result, _, _ = self.run_cs2()
        state = result.final_customer_simulator_state
        receipts = list(state.approval_receipts)
        receipts[0] = receipts[0].model_copy(update={'repeated': True})
        changed = result.model_copy(update={'final_customer_simulator_state': state.model_copy(update={'approval_receipts': tuple(receipts)})})
        checks = {c.name: c.status for c in self.validate(changed).checks}
        self.assertEqual(checks['approval_receipts_match_public'], 'FAIL')

    def test_wrong_following_confirmation_fails(self):
        result, _, _ = self.run_cs2()
        history = list(result.public_record.public_history)
        index = result.final_customer_simulator_state.approval_receipts[0].customer_message_index
        history[index] = PublicMessage(role='user', text='No, do not confirm.')
        audit = verify_cs2_public_approvals(tuple(history), customer=self.scenarios[0].customer_view())
        self.assertTrue(audit.errors)
        self.assertFalse(audit.approvals)

    def test_missing_configuration_approval_fails(self):
        config, final = artifacts(self.scenarios[0])
        history = (PublicMessage(role='user', text=self.scenarios[0].customer.initial_message),
                   PublicMessage(role='assistant', text=final),
                   PublicMessage(role='user', text='Yes, I confirm the final order and would like to place it.'),
                   PublicMessage(role='assistant', text='Your order has been created.'))
        audit = verify_cs2_public_approvals(history, customer=self.scenarios[0].customer_view())
        self.assertTrue(any('MISSING_PRIOR_CONFIGURATION' in error for error in audit.errors))
        self.assertFalse(audit.approvals)

    def test_audit_fact_and_veto_rejection(self):
        result, _, _ = self.run_cs2()
        for old, new in (('Tassie Oak', 'Marri'), ('Please confirm the configuration.', 'Please confirm the configuration. Do not confirm this configuration.')):
            history = tuple(PublicMessage(role=m.role, text=m.text.replace(old, new) if m.role == 'assistant' else m.text) for m in result.public_record.public_history)
            audit = verify_cs2_public_approvals(history, customer=self.scenarios[0].customer_view())
            self.assertTrue(audit.errors)

    def test_public_audit_has_only_public_customer_inputs(self):
        result, _, _ = self.run_cs2()
        verifier = Mock(wraps=verify_cs2_public_approvals)
        validation = validate_pilot_result(result, customer=self.scenarios[0].customer_view(), expectations=self.scenarios[0].evaluation, artifact_verifier=verifier)
        self.assertEqual(validation.verdict, 'PASS')
        verifier.assert_called_once_with(result.public_record.public_history, customer=self.scenarios[0].customer_view())

    def test_derived_truth_stays_evaluator_owned(self):
        result, _, _ = self.run_cs2()
        for field, wrong in (('product_sku', 'WRONG'), ('total_price', '1')):
            record = json.loads(result.persistence_observation.records_json[0])
            record['order'][field] = wrong
            changed = result.model_copy(update={'persistence_observation': PersistenceObservation(status='VALID', record_count=1, records_json=(json.dumps(record),))})
            checks = {c.name: c.status for c in self.validate(changed).checks}
            self.assertEqual(checks['persisted_system_derived'], 'FAIL')
            self.assertEqual(checks['configuration_approved'], 'PASS')
            self.assertEqual(checks['final_order_approved'], 'PASS')

    def test_legacy_default_serialization_and_verifier(self):
        script = ScriptedExecution(self.scenarios[0], happy_events(self.scenarios[0]))
        result = run_scenario(self.scenarios[0], run_directory=self.root/'legacy', knowledge=self.knowledge, execution_factory=script.factory)
        self.assertIsNone(result.simulator_summary)
        self.assertNotIn('simulator_summary', result.model_dump())
        self.assertNotIn('simulator_attempts', result.model_dump())
        self.assertNotIn('simulator', json.loads(Path(result.outputs.manifest).read_text()))
        lines = self.events(result)
        self.assertEqual(len(lines), len(result.traces)+1)
        self.assertIn('terminal', lines[-1])
        self.assertNotIn('kind', lines[0])
        self.assertEqual(validate_pilot_result(result, customer=self.scenarios[0].customer_view(), expectations=self.scenarios[0].evaluation).verdict, 'PASS')
        self.assertEqual(ExperimentRunResult.model_validate_json(result.model_dump_json()), result)

    def test_diagnostics_equivalence(self):
        customer = self.scenarios[0].customer_view()
        initial = CustomerSimulatorInput(customer=customer, state=CustomerSimulatorState(), public_history=())
        first = cs2.step(initial, chat_fn=Mock())
        inputs = CustomerSimulatorInput(customer=customer, state=first.state,
            public_history=(PublicMessage(role='user', text=first.decision.message), PublicMessage(role='assistant', text=FACT_REQUEST)))
        for raw_kind in ('valid', 'invalid', 'guard', 'model'):
            calls = []
            def reply(**kwargs):
                calls.append(kwargs)
                if raw_kind == 'model':
                    raise RuntimeError('same transport failure')
                if raw_kind == 'invalid':
                    return SimpleNamespace(message=SimpleNamespace(content='{broken'))
                response = proposed_reply(**kwargs)
                if raw_kind == 'guard':
                    response.message.content = response.message.content.replace('Demo1 Customer1', 'Wrong')
                return response
            outcomes = []
            capture = cs2.ProposalDiagnostics()
            for diagnostic in (None, capture):
                try:
                    outcomes.append(cs2.step(inputs, chat_fn=reply, diagnostics=diagnostic))
                except cs2.CS2Failure as exc:
                    outcomes.append((exc.code, str(exc)))
            self.assertEqual(outcomes[0], outcomes[1])
            self.assertEqual(calls[0], calls[1])
            if capture.parsed_proposal is not None:
                self.assertEqual(capture.parsed_proposal, cs2.parse_proposal(capture.raw_model_content))
        capture = cs2.ProposalDiagnostics()
        self.assertEqual(cs2.step(initial, chat_fn=Mock(), diagnostics=capture), first)
        self.assertEqual(capture.model_calls, 0)

    def test_architecture_fairness(self):
        a, _, a_chat = self.run_cs2(architecture='A3')
        b, _, b_chat = self.run_cs2(architecture='TEST_OTHER')
        self.assertEqual(a_chat.call_args_list, b_chat.call_args_list)
        self.assertEqual(a.dispatched_customer_messages, b.dispatched_customer_messages)
        self.assertEqual(a.final_customer_simulator_state, b.final_customer_simulator_state)
        self.assertEqual(self.validate(a), self.validate(b))
        for call in a_chat.call_args_list:
            payload = json.loads(call.kwargs['messages'][1]['content'])
            self.assertEqual(set(payload), {'customer', 'state', 'public_history'})
            self.assertNotIn('TEST_OTHER', json.dumps(payload))

    def test_metadata_and_diagnostics_must_be_paired(self):
        with self.assertRaises(ValueError):
            run_scenario(self.scenarios[0], run_directory=self.root/'bad', simulator_metadata=self.metadata)
        self.assertFalse((self.root/'bad').exists())

    def test_trace_events_are_strict_and_round_trip(self):
        from pydantic import TypeAdapter
        from evaluation.experiment_runner import TraceEvent
        result, _, _ = self.run_cs2()
        adapter = TypeAdapter(TraceEvent)
        for line in Path(result.outputs.trace).read_text().splitlines():
            event = adapter.validate_json(line)
            self.assertEqual(json.loads(event.model_dump_json()), json.loads(line))
            value = json.loads(line)
            value['extra'] = True
            with self.assertRaises(ValueError):
                adapter.validate_json(json.dumps(value))
        with self.assertRaises(ValueError):
            SimulatorAttemptTrace(**{**result.simulator_attempts[0].model_dump(), 'raw_model_content': 'invented'})

    def test_diagnostics_public_stop_equivalence(self):
        customer = self.scenarios[0].customer_view()
        first = cs2.step(CustomerSimulatorInput(customer=customer, state=CustomerSimulatorState(), public_history=()), chat_fn=Mock())
        inputs = CustomerSimulatorInput(customer=customer, state=first.state,
            public_history=(PublicMessage(role='user', text=first.decision.message),
                            PublicMessage(role='assistant', text='Your order has been created.')))
        capture = cs2.ProposalDiagnostics()
        plain = cs2.step(inputs, chat_fn=Mock(side_effect=proposed_reply))
        observed = cs2.step(inputs, chat_fn=Mock(side_effect=proposed_reply), diagnostics=capture)
        self.assertEqual(plain, observed)
        self.assertEqual(capture.parsed_proposal.kind, 'STOP')

    def test_diagnostics_invalid_input_equivalence(self):
        inputs = CustomerSimulatorInput(customer=self.scenarios[0].customer_view(), state=CustomerSimulatorState(), public_history=())
        inputs = inputs.model_copy(update={'architecture': 'FORBIDDEN'})
        outcomes = []
        for capture in (None, cs2.ProposalDiagnostics()):
            chat = Mock()
            with self.assertRaises(cs2.CS2Failure) as caught:
                cs2.step(inputs, chat_fn=chat, diagnostics=capture)
            outcomes.append((caught.exception.code, str(caught.exception)))
            chat.assert_not_called()
        self.assertEqual(outcomes[0], outcomes[1])

    def test_incomplete_response_content_is_retained(self):
        chat = Mock(return_value=SimpleNamespace(done=False, message=SimpleNamespace(content='PARTIAL_PRIVATE_CONTENT')))
        result = self.assert_failed_second_attempt(chat, 'INVALID_PROPOSAL')
        self.assertEqual(result.simulator_attempts[-1].raw_model_content, 'PARTIAL_PRIVATE_CONTENT')
        self.assertIsNone(result.simulator_attempts[-1].parsed_proposal_json)

    def test_audit_incomplete_following_pair(self):
        config, _ = artifacts(self.scenarios[0])
        history = (PublicMessage(role='user', text=self.scenarios[0].customer.initial_message), PublicMessage(role='assistant', text=config))
        audit = verify_cs2_public_approvals(history, customer=self.scenarios[0].customer_view())
        self.assertTrue(audit.incomplete)
        self.assertFalse(audit.approvals)

    def test_metadata_hashes_identify_actual_sources(self):
        directory = Path(__file__).resolve().parents[1] / 'evaluation'
        for key, filename in (('guard_source_sha256', 'llm_customer_simulator.py'), ('evidence_source_sha256', 'llm_public_evidence.py'), ('renderer_source_sha256', 'customer_simulator.py')):
            self.assertEqual(getattr(self.metadata, key), sha256((directory/filename).read_bytes()).hexdigest())
        self.assertEqual(self.metadata.prompt_sha256, sha256(cs2.SYSTEM_PROMPT.encode()).hexdigest())

    def test_output_error_cannot_leak_raw_output_to_result(self):
        from evaluation.experiment_runner import _append_event
        def fail_second(path, event):
            if event.kind == 'SIMULATOR_ATTEMPT' and event.attempt.attempt_index == 1:
                raise OSError('PRIVATE_RAW_MARKER')
            return _append_event(path, event)
        with patch('evaluation.experiment_runner._append_event', side_effect=fail_second):
            result, script, _ = self.run_cs2()
        self.assertEqual(result.termination.failure.phase, 'OUTPUT')
        self.assertNotIn('PRIVATE_RAW_MARKER', Path(result.outputs.result).read_text())
        self.assertEqual(len(script.calls), 1)
        self.assertEqual(len(result.simulator_attempts), 2)

    def test_final_fact_mismatch_rejected_by_public_audit(self):
        result, _, _ = self.run_cs2()
        history = tuple(PublicMessage(role=m.role, text=m.text.replace('customer1@', 'other@') if 'Provisional Order' in m.text else m.text) for m in result.public_record.public_history)
        self.assertNotEqual(history, result.public_record.public_history)
        audit = verify_cs2_public_approvals(history, customer=self.scenarios[0].customer_view())
        self.assertTrue(audit.errors)
        self.assertEqual([a.receipt.kind for a in audit.approvals], ['CONFIGURATION'])
