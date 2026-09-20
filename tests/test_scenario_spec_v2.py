"""Synthetic contract examples only; never authoritative workbook projections."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from evaluation.scenario_spec import CONFIGURATION_FIELDS
from evaluation.scenario_spec_v2 import (
    ConfigurationPhase, RequirementsMilestone, ScenarioSpecV2, ScenarioSource,
)


def synthetic(case='simple'):
    final = dict(product_model='Example', table_size='7ft', timber='Example timber',
                 timber_painting='Natural', felt_color='Blue', bracket='Copper',
                 top_profile='Example profile', quantity=1)
    confirm = {'type': 'CONFIRM_WITHOUT_CHANGE'}
    policy = dict(disclosure={'type': 'ANSWER_REQUESTED_INFORMATION'},
                  configuration_confirmation=deepcopy(confirm), final_confirmation=deepcopy(confirm))
    pricing = dict(phase='FINAL', expectation_type='EXECUTED', product_sku='EXAMPLE',
                   pricing=dict(base_model_price='10', customisation_price='2', unit_price='12',
                                shipping_cost='3', total_price='15'))
    milestones = [
        dict(kind='VALIDATION', id='FINAL_VALIDATION', phase='FINAL', result={'status': 'VALID'}),
        dict(kind='CONFIGURATION_REVIEW', id='FINAL_REVIEW', phase='FINAL', result={'status': 'CONFIRMED'}),
        dict(kind='PRICING', id='FINAL_PRICING', phase='FINAL', status='COMPLETED'),
        dict(kind='FINAL_CONFIRMATION', id='FINAL_CONFIRMATION', result={'status': 'CONFIRMED'}),
        dict(kind='ORDER_CREATION', id='ORDER_CREATION', status='EXECUTED'),
        dict(kind='TERMINATION', id='TERMINATION', status='COMPLETED', side_effect_allowed=True),
    ]
    raw = dict(schema_version='2', scenario_id='S01', name='Synthetic '+case,
        source=dict(source_file='sources/example.xlsx', worksheet='Synthetic', source_sha256='a'*64,
                    source_version='Synthetic test source', scenario_column='XFD'),
        initial_state=dict(conversation='EMPTY', order_store='EMPTY_ISOLATED'),
        fixtures=dict(product_catalog=dict(path='data/product_prices.json', sha256='b'*64),
                      shipping=dict(path='data/shipping_rates.json', sha256='c'*64)),
        customer=dict(description='Synthetic customer only', initial_message='  Exact initial text.  ',
            ground_truth=dict(customer=dict(customer_name='Example Person', phone='0400', email='example@example.com'),
                delivery_address=dict(address='Example street', city='Example city', state='VIC', postcode='3000', country='Australia'),
                room_size='Example room', configuration=final, initial_configuration_overrides={}),
            initial_disclosures=list(CONFIGURATION_FIELDS)+['quantity'],
            initially_known_configuration_fields=list(CONFIGURATION_FIELDS), conversation_policy=policy),
        evaluation=dict(system_derived={'pricing': [pricing]}, workflow={'milestones': milestones, 'precedence': []},
            outcome=dict(result_status='SUCCESS', reason='ORDER_CREATED', workflow_status='COMPLETED', expected_persisted_order_count=1),
            invariants={f'I{i}': 'NOT_EXERCISED' if i in (4,7) else 'SATISFIED' for i in range(1,9)},
            interaction_properties=[]))
    if case=='discovery':
        raw['customer'].update(initial_disclosures=[], initially_known_configuration_fields=[])
        policy['configuration_selection']=dict(type='DISCOVER_THEN_SELECT', discovery_fields=list(CONFIGURATION_FIELDS),
            if_options_unknown='ASK_AVAILABLE_OPTIONS', selection='ONLY_AFTER_POSITIVE_PUBLIC_OBSERVATION',
            if_target_not_offered='ASK_ABOUT_TARGET_OPTION')
    if case in ('revision','recovery'):
        field,old=('bracket','Standard rubber') if case=='revision' else ('table_size','8ft')
        raw['customer']['ground_truth']['initial_configuration_overrides']={field:old}
        modifications=[{'field':field,'from':'INITIAL_OVERRIDE','to':'FINAL_TARGET'}]
        change=dict(kind='CONFIGURATION_CHANGE',id='CONFIGURATION_CHANGE',status='APPLIED',changed_fields=[field],
                    material_change_detected=True,price_recalculation_required=True)
        initial=dict(kind='VALIDATION',id='INITIAL_VALIDATION',phase='INITIAL',result={'status':'VALID'})
        milestones[:0]=[initial,change]
        if case=='revision':
            policy['configuration_confirmation']=dict(type='REJECT_AND_MODIFY_CONFIGURATION', modifications=modifications,
                revised_confirmation=deepcopy(confirm),response={'type':'EXACT_TEXT','text':'  Change the bracket.\n'})
            milestones.insert(1,dict(kind='CONFIGURATION_REVIEW',id='INITIAL_REVIEW',phase='INITIAL',
                                    result={'status':'REJECTED_WITH_MODIFICATION','changed_fields':[field]}))
            reference=deepcopy(pricing); reference.update(phase='INITIAL',expectation_type='REFERENCE_BASELINE')
            raw['evaluation']['system_derived']['pricing'].insert(0,reference)
            raw['evaluation']['interaction_properties']=['prior_configuration_snapshot_invalidated']
        else:
            policy['validation_failure_response']=dict(type='MODIFY_CONFIGURATION',trigger='ROOM_SIZE_UNSUITABLE',
                modifications=modifications,response={'type':'EXACT_TEXT','text':'Change size.'})
            initial['result']={'status':'INVALID','reason':'ROOM_SIZE_UNSUITABLE'}
        raw['evaluation']['workflow']['precedence']=[
            {'before':'CONFIGURATION_CHANGE','after':'FINAL_VALIDATION'},
            {'before':'FINAL_VALIDATION','after':'FINAL_REVIEW'},
            {'before':'FINAL_REVIEW','after':'FINAL_PRICING'}]
    if case=='abandonment':
        policy['final_confirmation']=dict(type='REJECT_AND_ABANDON',customer_intent='ABANDON_PURCHASE',
                                         response={'type':'EXACT_TEXT','text':'Please do not place the order.'})
        milestones[-3]['result']={'status':'REJECTED','customer_intent':'ABANDON_PURCHASE'}
        milestones[-2]['status']='NOT_EXECUTED'
        milestones[-1].update(status='CANCELLED',side_effect_allowed=False)
        raw['evaluation']['outcome']=dict(result_status='TERMINATED',reason='CUSTOMER_CANCELLED',
            workflow_status='CANCELLED',expected_persisted_order_count=0)
    return raw


def parse(raw):
    return ScenarioSpecV2.model_validate_json(json.dumps(raw))


class V2Tests(unittest.TestCase):
    def reject(self, path, value, case='simple'):
        raw=synthetic(case); node=raw
        for key in path[:-1]: node=node[key]
        node[path[-1]]=value
        with self.assertRaises(ValidationError): parse(raw)

    def test_five_synthetic_shapes_round_trip(self):
        for case in ('simple','discovery','revision','abandonment','recovery'):
            with self.subTest(case=case):
                model=parse(synthetic(case))
                self.assertEqual(ScenarioSpecV2.model_validate_json(model.model_dump_json()),model)
                self.assertEqual(model.customer.initial_message,'  Exact initial text.  ')
                self.assertIsNone(model.customer.ground_truth.customer.company_name)
                self.assertIsNone(model.customer.ground_truth.customer.customer_instructions)

    def test_identity_and_scalars(self):
        checks=[(['schema_version'],x) for x in ('1',2,None)]
        checks += [(['scenario_id'],x) for x in ('S00','S06','s01',1)]
        checks += [(['name'],x) for x in (' ',1)]
        checks += [(['customer','ground_truth','configuration','quantity'],x) for x in (True,False,0,-1,1.0,'1')]
        checks += [(['initial_state','conversation'],x) for x in ('ACTIVE',None)]
        checks += [(['initial_state','order_store'],x) for x in ('DEFAULT',None)]
        for path,value in checks:
            with self.subTest(path=path,value=value): self.reject(path,value)

    def test_extras_and_missing_targets(self):
        for key in ('architecture','production_mapping','product_knowledge'):
            self.reject([key],'unexpected')
            self.reject(['customer',key],'unexpected')
        for field in (*CONFIGURATION_FIELDS,'quantity'):
            raw=synthetic(); del raw['customer']['ground_truth']['configuration'][field]
            with self.assertRaises(ValidationError): parse(raw)
        self.reject(['customer','ground_truth','configuration','bracket'],' ')

    def test_source(self):
        source=synthetic()['source']
        for key,values in {
            'source_file':['/tmp/a.xlsx',r'C:\a.xlsx','C:/a.xlsx','C:a.xlsx','../a.xlsx','a/../b.xlsx','./a.xlsx','a//b.xlsx',' ','.'],
            'worksheet':['', ' '], 'source_version':[' ',1],
            'source_sha256':['A'*64,'g'*64,'a'*63,'a'*65],
            'scenario_column':['','b','A0','XFE','AAAA',1],
        }.items():
            for value in values:
                with self.subTest(key=key,value=value):
                    with self.assertRaises(ValidationError): ScenarioSource.model_validate_json(json.dumps({**source,key:value}))
        for column in ('A','B','F','AA','XFD'):
            self.assertEqual(ScenarioSource(**{**source,'scenario_column':column}).scenario_column,column)

    def test_immutability_and_projection(self):
        model=parse(synthetic('revision')); view=model.customer_view()
        self.assertIsNot(view,model.customer)
        self.assertIsNot(view.ground_truth,model.customer.ground_truth)
        self.assertIsNot(view.ground_truth.configuration,model.customer.ground_truth.configuration)
        for obj,field,value in ((model,'name','changed'),(view,'description','changed'),
                                (view.ground_truth.configuration,'bracket','changed')):
            with self.assertRaises(ValidationError): setattr(obj,field,value)
        self.assertIsInstance(view.initial_disclosures,tuple)
        self.assertIsInstance(view.conversation_policy.configuration_confirmation.modifications,tuple)
        self.assertEqual(view.ground_truth.initial_configuration_overrides.bracket,'Standard rubber')
        self.assertEqual(view.conversation_policy.configuration_confirmation.response.text,'  Change the bracket.\n')
        self.assertIsNotNone(parse(synthetic('recovery')).customer_view().conversation_policy.validation_failure_response)
        excluded={'source','fixtures','initial_state','system_derived','pricing','workflow','milestones','outcome',
                  'expected_persisted_order_count','invariants','interaction_properties','architecture'}
        def keys(node):
            if isinstance(node,dict):
                return set(node).union(*(keys(v) for v in node.values()))
            if isinstance(node,list): return set().union(*(keys(v) for v in node))
            return set()
        self.assertFalse(keys(view.model_dump(mode='json')) & excluded)
        other=synthetic('revision'); other['source']['source_sha256']='d'*64
        other['evaluation']['invariants']['I4']='SATISFIED'
        other['evaluation']['interaction_properties']=[]
        for p in other['evaluation']['system_derived']['pricing']:
            p['product_sku']='EVALUATOR_ONLY'; p['pricing'].update(base_model_price='20',unit_price='22',total_price='25')
        self.assertEqual(parse(other).customer_view(),view)
        self.assertNotIn('EVALUATOR_ONLY',parse(other).customer_view().model_dump_json())

    def test_effective_configurations_and_modifications(self):
        for case,field,old,new in [('revision','bracket','Standard rubber','Copper'),('recovery','table_size','8ft','7ft')]:
            model=parse(synthetic(case)); truth=model.customer.ground_truth
            before=truth.model_dump_json()
            initial=truth.resolve_effective_configuration(ConfigurationPhase.INITIAL)
            final=truth.resolve_effective_configuration(ConfigurationPhase.FINAL)
            self.assertEqual(getattr(initial,field),old); self.assertEqual(getattr(final,field),new)
            self.assertIsNot(final,truth.configuration)
            policy=model.customer.conversation_policy
            modification=(policy.configuration_confirmation if case=='revision' else policy.validation_failure_response).modifications[0]
            resolved=modification.resolve(truth)
            self.assertEqual((resolved.from_value,resolved.to_value),(old,new))
            self.assertEqual(truth.model_dump_json(),before)
            with self.assertRaises(ValueError): truth.resolve_effective_configuration('BAD')
        self.assertEqual(parse(synthetic()).customer.ground_truth.initial_configuration_overrides.model_dump(),{})

    def test_invalid_overrides(self):
        for value in ({'bracket':None},{'bracket':'Copper'},{'unknown':'x'},{'quantity':True},{'quantity':0},{'bracket':'Other'}):
            self.reject(['customer','ground_truth','initial_configuration_overrides'],value)
        for value in ({},{'bracket':'Copper'},{'bracket':None}):
            self.reject(['customer','ground_truth','initial_configuration_overrides'],value,'revision')

    def test_modification_policies(self):
        path=['customer','conversation_policy','configuration_confirmation']
        self.reject(path+['modifications'],[],'revision')
        modification={'field':'bracket','from':'INITIAL_OVERRIDE','to':'FINAL_TARGET'}
        self.reject(path+['modifications'],[modification,modification],'revision')
        for key,value in [('field','timber'),('from','Copper'),('to','INITIAL_OVERRIDE')]:
            self.reject(path+['modifications'],[{**modification,key:value}],'revision')
        self.reject(path,{'type':'REJECT_AND_MODIFY_CONFIGURATION','modifications':[modification]},'revision')
        self.reject(path+['modifications'],[modification])
        self.reject(['customer','conversation_policy','validation_failure_response','trigger'],'INVALID_ROOM_SIZE','recovery')
        self.reject(['customer','conversation_policy','final_confirmation','customer_intent'],'CONTINUE','abandonment')

    def test_discovery_and_disclosures(self):
        for key in ('initial_disclosures','initially_known_configuration_fields'):
            for value in (['bracket','bracket'],['unknown']): self.reject(['customer',key],value)
        self.reject(['customer','initially_known_configuration_fields'],[])
        self.reject(['customer','initial_disclosures'],['bracket'],'discovery')
        path=['customer','conversation_policy','configuration_selection']
        for value in ([],['bracket','bracket'],['quantity'],['bracket']): self.reject(path+['discovery_fields'],value,'discovery')
        self.reject(path+['type'],'MIXED_DIRECT_AND_DISCOVER','discovery')
        self.reject(path+['selection'],'UNCONDITIONAL','discovery')
        self.reject(path+['target'],'Copper','discovery')

    def test_responses(self):
        path=['customer','conversation_policy','configuration_confirmation','response']
        for value in ({'type':'EXACT_TEXT','text':' '},{'type':'TEMPLATE','text':'x'},{'type':'EXACT_TEXT','text':1}):
            self.reject(path,value)
        self.reject(['customer','conversation_policy','response'],{'type':'EXACT_TEXT','text':'x'})
        self.reject(['customer','conversation_policy','disclosure','response'],{'type':'EXACT_TEXT','text':'x'})
        model=synthetic(); model['customer']['conversation_policy']['final_confirmation']['response']={'type':'EXACT_TEXT','text':' Exact.\n'}
        self.assertEqual(parse(model).customer.conversation_policy.final_confirmation.response.text,' Exact.\n')

    def test_pricing_reference_and_execution(self):
        raw=synthetic('revision'); model=parse(raw)
        self.assertEqual(len(model.evaluation.system_derived.pricing),2)
        self.assertNotIn('INITIAL_PRICING',[m.id for m in model.evaluation.workflow.milestones])
        self.assertEqual(len(parse(synthetic('recovery')).evaluation.system_derived.pricing),1)
        self.reject(['evaluation','system_derived','pricing'],raw['evaluation']['system_derived']['pricing']*2,'revision')
        self.reject(['evaluation','system_derived','pricing',0,'expectation_type'],'EXECUTED','revision')
        self.reject(['evaluation','system_derived','pricing',0,'expectation_type'],'REFERENCE_BASELINE')
        self.reject(['evaluation','system_derived','pricing',0,'pricing','unit_price'],'13')
        self.reject(['evaluation','system_derived','pricing',0,'pricing','total_price'],'99')
        self.reject(['evaluation','system_derived','pricing',0,'pricing','unit_price'],12)
        raw['evaluation']['workflow']['milestones']=[m for m in raw['evaluation']['workflow']['milestones'] if m['kind']!='PRICING']
        raw['evaluation']['workflow']['precedence']=[]
        with self.assertRaises(ValidationError): parse(raw)

    def test_phase_quantity_arithmetic(self):
        raw=synthetic('revision'); truth=raw['customer']['ground_truth']
        truth['initial_configuration_overrides']={'quantity':2}
        raw['customer']['conversation_policy']['configuration_confirmation']['modifications'][0]['field']='quantity'
        for m in raw['evaluation']['workflow']['milestones']:
            if m['kind']=='CONFIGURATION_CHANGE': m['changed_fields']=['quantity']
            if m['id']=='INITIAL_REVIEW': m['result']['changed_fields']=['quantity']
        raw['evaluation']['system_derived']['pricing'][0]['pricing']['total_price']='27'
        self.assertEqual(parse(raw).customer.ground_truth.resolve_effective_configuration(ConfigurationPhase.INITIAL).quantity,2)
        raw['evaluation']['system_derived']['pricing'][0]['pricing']['total_price']='15'
        with self.assertRaises(ValidationError): parse(raw)

    def test_graph_and_milestone_payloads(self):
        path=['evaluation','workflow','precedence']
        edge={'before':'FINAL_REVIEW','after':'FINAL_PRICING'}
        for edges in ([edge,edge],[{'before':'FINAL_REVIEW','after':'FINAL_REVIEW'}],
                      [{'before':'INITIAL_REVIEW','after':'FINAL_REVIEW'}],
                      [edge,{'before':'FINAL_PRICING','after':'FINAL_REVIEW'}],
                      [{'before':'UNKNOWN','after':'FINAL_REVIEW'}]): self.reject(path,edges)
        raw=synthetic(); raw['evaluation']['workflow']['milestones']*=2
        with self.assertRaises(ValidationError): parse(raw)
        self.reject(['evaluation','workflow','milestones',0,'phase'],'INITIAL')
        self.reject(['evaluation','workflow','milestones',0,'result'],{'status':'VALID','reason':'ROOM_SIZE_UNSUITABLE'})
        self.reject(['evaluation','workflow','milestones',1,'result'],{'status':'REJECTED_WITH_MODIFICATION','changed_fields':['bracket']})
        self.reject(['evaluation','workflow','milestones',2,'status'],'NOT_EXECUTED')

    def test_required_revision_and_recovery_milestones(self):
        for case in ('revision','recovery'):
            for identifier in ('CONFIGURATION_CHANGE','FINAL_VALIDATION','FINAL_REVIEW', 'INITIAL_REVIEW' if case=='revision' else 'INITIAL_VALIDATION'):
                raw=synthetic(case); workflow=raw['evaluation']['workflow']
                workflow['precedence']=[]; workflow['milestones']=[m for m in workflow['milestones'] if m['id']!=identifier]
                with self.subTest(case=case,id=identifier):
                    with self.assertRaises(ValidationError): parse(raw)
            raw=synthetic(case)
            next(m for m in raw['evaluation']['workflow']['milestones'] if m['id']=='CONFIGURATION_CHANGE')['changed_fields']=['timber']
            with self.assertRaises(ValidationError): parse(raw)

    def test_outcome_contradictions(self):
        for case in ('simple','abandonment'):
            for value in (True,False,'1',1.0,2,-1):
                self.reject(['evaluation','outcome','expected_persisted_order_count'],value,case)
            self.reject(['evaluation','outcome','expected_persisted_order_count'],1 if case=='abandonment' else 0,case)
            for identifier,key,value in [('ORDER_CREATION','status','EXECUTED' if case=='abandonment' else 'NOT_EXECUTED'),
                    ('TERMINATION','status','COMPLETED' if case=='abandonment' else 'CANCELLED'),
                    ('TERMINATION','side_effect_allowed',case=='abandonment'),
                    ('FINAL_CONFIRMATION','result',{'status':'CONFIRMED'} if case=='abandonment' else {'status':'REJECTED','customer_intent':'ABANDON_PURCHASE'})]:
                raw=synthetic(case); next(m for m in raw['evaluation']['workflow']['milestones'] if m['id']==identifier)[key]=value
                with self.assertRaises(ValidationError): parse(raw)
            raw=synthetic(case); raw['customer']['conversation_policy']['final_confirmation']=synthetic('simple' if case=='abandonment' else 'abandonment')['customer']['conversation_policy']['final_confirmation']
            with self.assertRaises(ValidationError): parse(raw)

    def test_invariants_and_properties(self):
        for i in range(1,9):
            raw=synthetic(); del raw['evaluation']['invariants'][f'I{i}']
            with self.assertRaises(ValidationError): parse(raw)
        self.reject(['evaluation','invariants','I4'],'UNKNOWN')
        self.reject(['evaluation','invariants','I9'],'SATISFIED')
        self.reject(['evaluation','interaction_properties'],['unknown'])
        self.reject(['evaluation','interaction_properties'],['prior_configuration_snapshot_invalidated']*2)
        raw=synthetic('revision'); self.assertEqual(parse(raw).evaluation.invariants.I4,'NOT_EXERCISED')
        raw['evaluation']['invariants']['I4']='SATISFIED'
        self.assertEqual(parse(raw).evaluation.invariants.I4,'SATISFIED')  # No inferred rewrite.

    def test_missing_outcome_milestones(self):
        for case in ('simple','abandonment'):
            for identifier in ('FINAL_CONFIRMATION','ORDER_CREATION','TERMINATION'):
                raw=synthetic(case)
                raw['evaluation']['workflow']['milestones']=[m for m in raw['evaluation']['workflow']['milestones'] if m['id']!=identifier]
                with self.subTest(case=case,id=identifier):
                    with self.assertRaises(ValidationError): parse(raw)

    def test_python_inputs_are_strict(self):
        model=parse(synthetic())
        payload=model.model_dump()
        self.assertEqual(ScenarioSpecV2.model_validate(payload),model)
        payload['customer']['initial_disclosures']=list(payload['customer']['initial_disclosures'])
        with self.assertRaises(ValidationError): ScenarioSpecV2.model_validate(payload)
        self.reject(['evaluation','workflow','milestones',5,'side_effect_allowed'],1)
        self.reject(['evaluation','workflow','milestones',5,'side_effect_allowed'],'true')

    def test_partial_discovery(self):
        raw=synthetic('discovery')
        raw['customer']['initially_known_configuration_fields']=['bracket']
        raw['customer']['initial_disclosures']=['bracket']
        discovery=raw['customer']['conversation_policy']['configuration_selection']
        discovery['type']='MIXED_DIRECT_AND_DISCOVER'
        discovery['discovery_fields'].remove('bracket')
        self.assertEqual(parse(raw).customer.initially_known_configuration_fields,('bracket',))

    def test_policy_and_failure_contradictions(self):
        raw=synthetic('recovery')
        raw['evaluation']['workflow']['milestones'][0]['result']={'status':'VALID'}
        with self.assertRaises(ValidationError): parse(raw)
        raw=synthetic('revision')
        review=next(m for m in raw['evaluation']['workflow']['milestones'] if m['id']=='INITIAL_REVIEW')
        review['result']={'status':'CONFIRMED'}
        with self.assertRaises(ValidationError): parse(raw)
        raw=synthetic('recovery')
        change=next(m for m in raw['evaluation']['workflow']['milestones'] if m['id']=='CONFIGURATION_CHANGE')
        change['changed_fields']=['table_size','table_size']
        with self.assertRaises(ValidationError): parse(raw)
        raw=synthetic('revision')
        policy=raw['customer']['conversation_policy']
        policy['validation_failure_response']=synthetic('recovery')['customer']['conversation_policy']['validation_failure_response']
        raw['customer']['ground_truth']['initial_configuration_overrides']['table_size']='8ft'
        with self.assertRaises(ValidationError): parse(raw)

    def test_requirements_milestone_complete_round_trip(self):
        expected = {'kind': 'REQUIREMENTS', 'id': 'REQUIREMENTS', 'status': 'COMPLETE'}
        milestone = RequirementsMilestone(**expected)
        self.assertEqual(milestone.model_dump(mode='json'), expected)
        raw = synthetic()
        raw['evaluation']['workflow']['milestones'].insert(0, expected)
        raw['evaluation']['workflow']['precedence'].append(
            {'before': 'REQUIREMENTS', 'after': 'FINAL_VALIDATION'})
        scenario = parse(raw)
        self.assertEqual(scenario.evaluation.workflow.milestones[0], milestone)
        serialized = scenario.model_dump_json()
        self.assertEqual(json.loads(serialized)['evaluation']['workflow']['milestones'][0], expected)
        self.assertEqual(ScenarioSpecV2.model_validate_json(serialized), scenario)

    def test_requirements_milestone_invalid_status_rejected(self):
        invalid = {'kind': 'REQUIREMENTS', 'id': 'REQUIREMENTS', 'status': 'INCOMPLETE'}
        with self.assertRaises(ValidationError):
            RequirementsMilestone(**invalid)
        raw = synthetic()
        raw['evaluation']['workflow']['milestones'].insert(0, invalid)
        with self.assertRaises(ValidationError):
            parse(raw)

    def test_contract_has_no_file_or_business_io(self):
        raw=synthetic('revision')
        with patch('builtins.open',side_effect=AssertionError('Contract attempted I/O')):
            model=parse(raw); model.customer_view(); model.customer.ground_truth.resolve_effective_configuration(ConfigurationPhase.INITIAL)
        schema=ScenarioSpecV2.model_json_schema()
        self.assertEqual(schema['properties']['schema_version']['const'],'2')
        self.assertNotIn('architecture',schema['properties'])


if __name__=='__main__':
    unittest.main()
