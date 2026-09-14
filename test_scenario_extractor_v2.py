"""Frozen-source fidelity tests; no active fixture inputs, business calls or models."""
from copy import copy, deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from evaluation import scenario_extractor_v2 as e
from evaluation.scenario_spec_v2 import ConfigurationPhase, ScenarioSpecV2

INITIAL_MESSAGES = (
    "I'd like to order one 8ft Odyssey pool table with a Waterfall top rail profile and brass bracket. For timber, I'd like Tassie Oak in Natural finish, with Blue felt.",
    "I'd like to place an order for a custom pool table, but I don't know which model to choose. What models do you have available?",
    "I'd like to order one 8ft Saga pool table with a Live-Edge top rail profile and Standard rubber. For timber, I'd like Marri in Natural finish, with Red felt.",
    "I'd like to order one 7ft Sleek pool table with a Waterfall top profile and Standard rubber. I'd like Jarrah timber with a Natural finish and Red felt.",
    "I'd like to order one 8ft Regent pool table with a Waterfall top profile and Standard rubber. I'd like Tassie Oak timber with a Black finish and Grey felt.",
)
NAMES = ('Complete Configuration Upfront', 'Guided Catalog Discovery', 'Configuration Revision Before Confirmation',
         'Valid Configuration but Customer Abandons at Final Confirmation', 'Invalid Room Size Resolved by Changing Table Size')
CONFIGURATIONS = (
    ('Odyssey','8ft','Tassie Oak','Natural','Blue','Brass','Waterfall',1),
    ('Saga','8ft','Marri','Natural','Olive','Standard rubber','Live-Edge',1),
    ('Saga','8ft','Marri','Natural','Red','Copper','Live-Edge',1),
    ('Sleek','7ft','Jarrah','Natural','Red','Standard rubber','Waterfall',1),
    ('Regent','7ft','Tassie Oak','Black','Grey','Standard rubber','Waterfall',1),
)
RESPONSES = {
    'D56': "Actually, I don't want Standard rubber. Please change it to a Copper bracket instead.",
    'E52': 'Yes, that configuration is correct.',
    'E57': "Actually, I've changed my mind and don't want to go ahead with the order anymore. Please don't place the order.",
    'F57': 'In that case, please change the table size from 8ft to 7ft.',
    'F62': 'Yes, that configuration is correct.',
    'F67': 'Yes, everything looks good. Please go ahead with the order.',
}


class ExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (e.ROOT / e.SOURCE_FILE).read_bytes()
        cls.cells = e._read_archive(cls.raw)
        cls.results = e.extract_workbook()
        cls.scenarios = tuple(r.scenario for r in cls.results)

    def test_frozen_source_identity(self):
        self.assertEqual(sha256(self.raw).hexdigest(), '476c83d6b597decd258292d43e83314156a5b71cfd1f45560a37a6beb6578128')
        self.assertEqual(e.EXTRACTOR_VERSION, 'v2-b-1')
        self.assertEqual(e.WORKSHEET, 'Conversation Profiles')
        self.assertEqual(e.SOURCE_VERSION, 'Authoritative Conversation Scenario Source v1')
        self.assertEqual([(s.scenario_id,s.source.scenario_column) for s in self.scenarios],
                         [('S01','B'),('S02','C'),('S03','D'),('S04','E'),('S05','F')])
        for s in self.scenarios:
            self.assertEqual(s.source.source_file, 'evaluation/scenario_sources/Conversation Scenario-CSimulator.xlsx')
            self.assertEqual(s.source.source_sha256,sha256(self.raw).hexdigest())
            self.assertEqual(s.source.worksheet,'Conversation Profiles')
            self.assertEqual(s.source.source_version,e.SOURCE_VERSION)
            self.assertEqual(s.schema_version,'2')

    def test_names_descriptions_and_exact_initial_messages(self):
        for index,s in enumerate(self.scenarios):
            column=s.source.scenario_column
            self.assertEqual(s.name,NAMES[index])
            self.assertEqual(s.customer.description,self.cells[column+'5'])
            self.assertEqual(s.customer.initial_message,INITIAL_MESSAGES[index])
            self.assertEqual(s.customer.initial_message,self.cells[column+'33'])
        self.assertFalse(any('\u4e00' <= c <= '\u9fff' for c in self.scenarios[1].customer.initial_message))

    def test_customer_address_room_and_omissions(self):
        for index,s in enumerate(self.scenarios,1):
            truth=s.customer.ground_truth
            self.assertEqual(truth.customer.model_dump(),dict(customer_name=f'Demo{index} Customer{index}',
                phone=f'040000000{index}',email=f'customer{index}@example.com',company_name=None,customer_instructions=None))
            city,state,postcode=(('Melbourne','VIC','3000'),('Sydney','NSW','2000'),('Sydney','NSW','2000'),
                                 ('Perth Metro','WA','6001'),('Perth Metro','WA','6001'))[index-1]
            self.assertEqual(truth.delivery_address.model_dump(),dict(address=f'{index} Example Street',city=city,state=state,postcode=postcode,country='Australia'))
            self.assertEqual(truth.room_size,'5.3m x 4.1m' if index<=3 else '5.0m x 4.0m')

    def test_final_targets_and_sparse_initial_overrides(self):
        for index,s in enumerate(self.scenarios):
            truth=s.customer.ground_truth
            self.assertEqual(tuple(truth.configuration.model_dump().values()),CONFIGURATIONS[index])
            expected=({'bracket':'Standard rubber'} if index==2 else {'table_size':'8ft'} if index==4 else {})
            self.assertEqual(truth.initial_configuration_overrides.model_dump(),expected)
            final=truth.resolve_effective_configuration(ConfigurationPhase.FINAL)
            self.assertEqual(final,truth.configuration)
            initial=truth.resolve_effective_configuration(ConfigurationPhase.INITIAL)
            expected_initial={**truth.configuration.model_dump(),**expected}
            self.assertEqual(initial.model_dump(),expected_initial)

    def test_disclosure_and_knowledge(self):
        for index,s in enumerate(self.scenarios):
            self.assertEqual(s.customer.initial_disclosures,() if index==1 else (*e.CONFIGURATION_FIELDS,'quantity'))
            self.assertEqual(s.customer.initially_known_configuration_fields,() if index==1 else e.CONFIGURATION_FIELDS)
            self.assertNotIn('product_knowledge',s.customer.model_dump())
            self.assertEqual(s.customer.conversation_policy.disclosure.type,'ANSWER_REQUESTED_INFORMATION')

    def test_discovery(self):
        policy=self.scenarios[1].customer.conversation_policy
        discovery=policy.configuration_selection
        self.assertEqual(discovery.type,'DISCOVER_THEN_SELECT')
        self.assertEqual(discovery.discovery_fields,e.CONFIGURATION_FIELDS)
        self.assertEqual(discovery.if_options_unknown,'ASK_AVAILABLE_OPTIONS')
        self.assertEqual(discovery.selection,'ONLY_AFTER_POSITIVE_PUBLIC_OBSERVATION')
        self.assertEqual(discovery.if_target_not_offered,'ASK_ABOUT_TARGET_OPTION')
        self.assertEqual(policy.configuration_confirmation.type,'CONFIRM_WITHOUT_CHANGE')
        self.assertEqual(policy.final_confirmation.type,'CONFIRM_WITHOUT_CHANGE')
        for index in (0,2,3,4): self.assertIsNone(self.scenarios[index].customer.conversation_policy.configuration_selection)

    def test_revision_and_exact_responses(self):
        policy=self.scenarios[2].customer.conversation_policy
        self.assertEqual(policy.configuration_confirmation.type,'REJECT_AND_MODIFY_CONFIGURATION')
        modification=policy.configuration_confirmation.modifications[0]
        self.assertEqual(modification.model_dump(),{'field':'bracket','from':'INITIAL_OVERRIDE','to':'FINAL_TARGET'})
        self.assertEqual(modification.resolve(self.scenarios[2].customer.ground_truth).model_dump(),
                         dict(field='bracket',from_value='Standard rubber',to_value='Copper'))
        self.assertEqual(policy.configuration_confirmation.revised_confirmation.type,'CONFIRM_WITHOUT_CHANGE')
        actual=(policy.configuration_confirmation.response,
                self.scenarios[3].customer.conversation_policy.configuration_confirmation.response,
                self.scenarios[3].customer.conversation_policy.final_confirmation.response,
                self.scenarios[4].customer.conversation_policy.validation_failure_response.response,
                self.scenarios[4].customer.conversation_policy.configuration_confirmation.response,
                self.scenarios[4].customer.conversation_policy.final_confirmation.response)
        for response,(cell,expected) in zip(actual,RESPONSES.items()):
            self.assertEqual(response.text,expected)
            self.assertEqual(response.text,self.cells[cell])
            self.assertEqual(response.type,'EXACT_TEXT')
        self.assertIsNone(self.scenarios[0].customer.conversation_policy.configuration_confirmation.response)

    def test_all_prices_and_phase_semantics(self):
        expected=(('B8ODYSSEY',('5250','1550','6800','530','7330')),
                  ('B8SAGA',('5750','800','6550','1518','8068')),
                  ('B8SAGA',('5750','1550','7300','1518','8818')),
                  ('B7SLEEK',('7000','800','7800','2800','10600')),
                  ('B7REGENT',('5800','800','6600','2800','9400')))
        for index,s in enumerate(self.scenarios):
            prices=s.evaluation.system_derived.pricing
            self.assertEqual(len(prices),2 if index==2 else 1)
            self.assertEqual((prices[-1].phase,prices[-1].expectation_type),('FINAL','EXECUTED'))
            self.assertEqual((prices[-1].product_sku,tuple(prices[-1].pricing.model_dump().values())),expected[index])
            self.assertNotIn('INITIAL_PRICING',[m.id for m in s.evaluation.workflow.milestones])
        initial=self.scenarios[2].evaluation.system_derived.pricing[0]
        self.assertEqual((initial.phase,initial.expectation_type),('INITIAL','REFERENCE_BASELINE'))
        self.assertEqual((initial.product_sku,tuple(initial.pricing.model_dump().values())),expected[1])

    def test_outcomes_and_abandonment(self):
        for index,s in enumerate(self.scenarios):
            cancelled=index==3
            self.assertEqual(s.evaluation.outcome.model_dump(),dict(
                result_status='TERMINATED' if cancelled else 'SUCCESS',reason='CUSTOMER_CANCELLED' if cancelled else 'ORDER_CREATED',
                workflow_status='CANCELLED' if cancelled else 'COMPLETED',expected_persisted_order_count=0 if cancelled else 1))
            milestones={m.id:m for m in s.evaluation.workflow.milestones}
            self.assertEqual(milestones['ORDER_CREATION'].status,'NOT_EXECUTED' if cancelled else 'EXECUTED')
            self.assertEqual(milestones['TERMINATION'].side_effect_allowed,not cancelled)
        policy=self.scenarios[3].customer.conversation_policy.final_confirmation
        self.assertEqual((policy.type,policy.customer_intent),('REJECT_AND_ABANDON','ABANDON_PURCHASE'))
        milestones={m.id:m for m in self.scenarios[3].evaluation.workflow.milestones}
        self.assertEqual(milestones['REQUIREMENTS'].status,'COMPLETE')
        self.assertEqual(milestones['FINAL_CONFIRMATION'].result.model_dump(),dict(status='REJECTED',customer_intent='ABANDON_PURCHASE'))

    def test_revision_workflow_chronology(self):
        workflow=self.scenarios[2].evaluation.workflow
        milestones={m.id:m for m in workflow.milestones}
        self.assertEqual(milestones['INITIAL_VALIDATION'].result.status,'VALID')
        self.assertEqual(milestones['INITIAL_REVIEW'].result.status,'REJECTED_WITH_MODIFICATION')
        self.assertEqual(milestones['INITIAL_REVIEW'].result.changed_fields,('bracket',))
        self.assertEqual(milestones['CONFIGURATION_CHANGE'].changed_fields,('bracket',))
        self.assertTrue(milestones['CONFIGURATION_CHANGE'].price_recalculation_required)
        self.assertEqual(milestones['FINAL_VALIDATION'].result.status,'VALID')
        self.assertEqual(milestones['FINAL_REVIEW'].result.status,'CONFIRMED')
        self.assertIn(('FINAL_REVIEW','FINAL_PRICING'),[(edge.before,edge.after) for edge in workflow.precedence])

    def test_recovery_workflow(self):
        s=self.scenarios[4]; policy=s.customer.conversation_policy.validation_failure_response
        self.assertEqual((policy.type,policy.trigger),('MODIFY_CONFIGURATION','ROOM_SIZE_UNSUITABLE'))
        self.assertEqual(policy.modifications[0].model_dump(),dict(field='table_size',**{'from':'INITIAL_OVERRIDE','to':'FINAL_TARGET'}))
        milestones={m.id:m for m in s.evaluation.workflow.milestones}
        self.assertEqual(milestones['INITIAL_VALIDATION'].result.model_dump(),dict(status='INVALID',reason='ROOM_SIZE_UNSUITABLE'))
        self.assertEqual(milestones['CONFIGURATION_CHANGE'].status,'APPLIED')
        self.assertEqual(milestones['CONFIGURATION_CHANGE'].changed_fields,('table_size',))
        self.assertEqual(milestones['FINAL_VALIDATION'].result.status,'VALID')
        self.assertNotIn('INVALID_ROOM_SIZE',s.model_dump_json())

    def test_all_invariants_and_interactions(self):
        for index,s in enumerate(self.scenarios):
            expected={f'I{i}':'NOT_EXERCISED' if i==4 or (i==7 and index!=4) else 'SATISFIED' for i in range(1,9)}
            self.assertEqual(s.evaluation.invariants.model_dump(),expected)
        discovery=('catalog_discovery_supported','target_selected_only_after_observation',
                   'active_workflow_general_enquiry_supported','workflow_continuity_after_enquiry')
        revision=('complete_initial_configuration_provided','initial_configuration_valid','configuration_revision_supported',
                  'material_change_detected','prior_configuration_snapshot_invalidated','configuration_revalidated_after_change',
                  'price_recalculated_after_material_change','revised_configuration_confirmed','final_confirmation_without_change',
                  'committed_order_matches_revised_configuration')
        for index,s in enumerate(self.scenarios):
            self.assertEqual(s.evaluation.interaction_properties,discovery if index==1 else revision if index==2 else ())

    def test_consumed_cell_accounting(self):
        for result,count in zip(self.results,(74,105,109,92,100)):
            self.assertEqual(len(result.consumed),count)
            consumed={use.cell for use in result.consumed}
            column=result.scenario.source.scenario_column
            populated={cell for cell,value in self.cells.items() if cell.startswith(column) and value}
            self.assertEqual(consumed,populated)
            self.assertEqual(len(consumed),len(result.consumed))
            self.assertTrue(all(use.destination for use in result.consumed))
            self.assertFalse(consumed & set(result.blank_cells))
            self.assertEqual(len(consumed)+len(result.blank_cells),133)
            self.assertIn(column+'1',consumed)  # Overview explicitly pinned/accounted, never silently ignored.

    def test_structure_failures_beyond_hash_gate(self):
        mutations=[('B4','unexpected_heading:'),('B8',''),('B34','extra_field: lost'),('C2','scenario_id: S01'),
                   ('F2','scenario_id: S06'),('G2','scenario_id: S06'),('D30','  initial_configuration_overrides:\n    bracket: Standard rubber\n    unknown: dropped'),
                   ('D54','      to: Brass'),('C46','        target_selection: Kings Cross'),('D120','expected_outcome:'),
                   ('B134','unmapped'),('B1',self.cells['B1']+' changed')]
        for cell,value in mutations:
            with self.subTest(cell=cell):
                cells={**self.cells,cell:value}
                with self.assertRaises(e.SourceFidelityError): e.extract_cells(cells)

    def _mutate_copy(self, directory, cell=None, value=None, rename=False):
        destination=Path(directory)/'mutated.xlsx'
        with ZipFile(e.ROOT/e.SOURCE_FILE) as source, ZipFile(destination,'w') as target:
            for info in source.infolist():
                data=source.read(info.filename)
                if rename and info.filename=='xl/workbook.xml':
                    data=data.replace(b'Conversation Profiles',b'Renamed Profiles')
                if cell and info.filename=='xl/worksheets/sheet1.xml':
                    root=ET.fromstring(data)
                    found=root.find(f'.//s:c[@r="{cell}"]',e.NS)
                    if found is None:
                        row=root.find('s:sheetData/s:row',e.NS)
                        found=ET.SubElement(row,'{'+e.NS['s']+'}c',{'r':cell})
                    for child in list(found): found.remove(child)
                    found.set('t','inlineStr')
                    inline=ET.SubElement(found,'{'+e.NS['s']+'}is')
                    ET.SubElement(inline,'{'+e.NS['s']+'}t').text=value
                    data=ET.tostring(root)
                target.writestr(copy(info),data)
        return destination

    def test_temporary_workbook_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            for cell,value in [('B2','scenario_id: S02'),('C8',''),('D4','wrong:'),('F34','unknown: value'),
                               ('D56','Altered response.'),('E2','scenario_id: S05')]:
                with self.subTest(cell=cell):
                    path=self._mutate_copy(directory,cell,value)
                    with self.assertRaisesRegex(e.SourceFidelityError,'SHA-256'): e.extract_workbook(path)
                    if cell!='D56':
                        with self.assertRaises(e.SourceFidelityError): e.extract_cells(e._read_archive(path.read_bytes()))
            path=self._mutate_copy(directory,rename=True)
            with self.assertRaises(e.SourceFidelityError): e.extract_workbook(path)
            with self.assertRaisesRegex(e.SourceFidelityError,'worksheet'): e._read_archive(path.read_bytes())
        self.assertEqual((e.ROOT/e.SOURCE_FILE).read_bytes(),self.raw)

    def test_candidate_fidelity_rejects_altered_response_or_provenance(self):
        source=self.results[2]
        for location in ('response','provenance'):
            raw=source.scenario.model_dump(mode='json')
            if location=='response': raw['customer']['conversation_policy']['configuration_confirmation']['response']['text']='Changed.'
            else: raw['source']['scenario_column']='E'
            candidate=ScenarioSpecV2.model_validate_json(json.dumps(raw))
            with self.assertRaises(e.SourceFidelityError): e.verify_candidate_fidelity(candidate,source)

    def test_harness_defaults_and_projection_boundary(self):
        excluded={'source','initial_state','fixtures','evaluation','system_derived','pricing','workflow','milestones',
                  'outcome','expected_persisted_order_count','invariants','interaction_properties','architecture'}
        def keys(value):
            if isinstance(value,dict): return set(value).union(*(keys(v) for v in value.values()))
            if isinstance(value,list): return set().union(*(keys(v) for v in value))
            return set()
        for s in self.scenarios:
            self.assertEqual(s.initial_state.model_dump(),dict(conversation='EMPTY',order_store='EMPTY_ISOLATED'))
            customer=s.customer_view()
            self.assertIsNot(customer,s.customer)
            self.assertFalse(keys(customer.model_dump(mode='json')) & excluded)

    def test_legacy_semantics_absent(self):
        s02,s03=self.scenarios[1:3]
        self.assertNotIn('Kings Cross',s02.model_dump_json()); self.assertNotIn('Canberra',s02.model_dump_json())
        self.assertEqual(s02.customer.initially_known_configuration_fields,())
        self.assertEqual(s03.customer.ground_truth.configuration.felt_color,'Red')
        self.assertEqual(s03.customer.ground_truth.configuration.bracket,'Copper')
        self.assertIsNone(s03.customer.conversation_policy.configuration_selection)

    def test_deterministic_generation_round_trip_and_checked_in_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'candidates'
            first=e.generate_candidates(output)
            before={p.name:p.read_bytes() for p in output.iterdir()}
            second=e.generate_candidates(output)
            after={p.name:p.read_bytes() for p in output.iterdir()}
            self.assertEqual(before,after)
            self.assertEqual(set(before),{'S01.json','S02.json','S03.json','S04.json','S05.json'})
            for one,two in zip(first,second):
                filename=one.scenario.scenario_id+'.json'
                self.assertEqual(one,two)
                self.assertEqual(before[filename],e.serialize_candidate(one.scenario))
                self.assertTrue(before[filename].endswith(b'\n'))
                loaded=ScenarioSpecV2.model_validate_json(before[filename])
                e.verify_candidate_fidelity(loaded,one)
                self.assertEqual((e.CANDIDATE_DIRECTORY/filename).read_bytes(),before[filename])

    def test_active_directory_is_forbidden(self):
        with self.assertRaises(e.SourceFidelityError): e.generate_candidates(e.ROOT/'evaluation/scenarios')


if __name__=='__main__':
    unittest.main()
