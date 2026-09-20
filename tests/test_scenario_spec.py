"""Deterministic CS1-A tests. No workflow execution or live services."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from evaluation.scenario_spec import ScenarioSpec
from evaluation.scenario_loader import (
    SCENARIO_DIRECTORY, FixtureDiscrepancy, load_scenario, load_scenarios,
    validate_repository,
)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((SCENARIO_DIRECTORY / 'S01.json').read_text())

    def validate(self, raw):
        return ScenarioSpec.model_validate_json(json.dumps(raw))

    def reject(self, path, value):
        raw = deepcopy(self.raw)
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        with self.assertRaises(ValidationError):
            self.validate(raw)

    def test_frozen_fixture_bytes(self):
        # Independently pinned after direct workbook-cell comparison; catches any
        # text, policy, invariant, or expectation drift, including whitespace.
        hashes = FIXTURE_HASHES
        for name, digest in hashes.items():
            self.assertEqual(hashlib.sha256((SCENARIO_DIRECTORY / name).read_bytes()).hexdigest(), digest)

    def test_all_repository_expectations(self):
        scenarios = load_scenarios()
        self.assertEqual([s.scenario_id for s in scenarios], ['S01', 'S02', 'S03'])
        for s, sku, unit, shipping, total in zip(scenarios,
                ['B8ODYSSEY', 'B7KINGX', 'B8SAGA'], ['6800', '6900', '6550'],
                ['530', '1202', '1518'], ['7330', '8102', '8068']):
            self.assertEqual(s.evaluation.system_derived.product_sku, sku)
            p = s.evaluation.system_derived.pricing
            self.assertEqual((p.unit_price, p.shipping_cost, p.total_price), (unit, shipping, total))
            self.assertEqual(s.evaluation.invariants.model_dump(), {
                'I1':'SATISFIED','I2':'SATISFIED','I3':'SATISFIED','I4':'NOT_EXERCISED',
                'I5':'SATISFIED','I6':'SATISFIED','I7':'NOT_EXERCISED','I8':'SATISFIED'})

    def test_exact_messages_and_knowledge(self):
        scenarios = load_scenarios(check_repository=False)
        self.assertEqual([s.customer.initial_message for s in scenarios], [
            " I'd like to order one 8ft Odyssey pool table with a Waterfall top rail profile and brass bracket. For timber, I'd like   Tassie Oak in Natural finish, with Blue felt.",
            "I'd like to order one 7ft Kings Cross pool table with a Live-Edge top rail profile and Copper bracket.",
            " I'd like to place an order for a custom pool table, but I don't know which model to choose. What models do you have available?",
        ])
        self.assertEqual([s.customer.product_knowledge for s in scenarios], ['INFORMED','PARTIAL','NOVICE'])
        self.assertEqual(scenarios[1].customer.initial_disclosures,
                         ('product_model','table_size','bracket','top_profile','quantity'))
        self.assertEqual(scenarios[2].customer.initial_disclosures, ())
        self.assertIsNone(scenarios[0].evaluation.interaction_properties)
        self.assertIsNone(scenarios[0].customer.conversation_policy.configuration_selection)

    def test_strict_types_and_unknown_fields(self):
        for path, values in [
            (['customer','ground_truth','configuration','quantity'], [True, 0, -1, 1.0, '1']),
            (['customer','ground_truth','customer','phone'], [400000001, '', '  ']),
            (['customer','product_knowledge'], ['HIGH','partial']),
            (['schema_version'], [1,'2']),
            (['evaluation','system_derived','pricing','unit_price'], [6800, 'NaN', '-1','1e3',' 6800']),
            (['fixtures','shipping','sha256'], ['a'*63, 'A'*64, 'g'*64]),
            (['fixtures','shipping','path'], ['/data/shipping_rates.json', '../data/shipping_rates.json', 'data/../data/shipping_rates.json']),
            (['initial_state','workflow_state'], [{}, 'COLLECTING']),
        ]:
            for value in values:
                with self.subTest(path=path, value=value): self.reject(path, value)
        self.reject(['customer','ground_truth','delivery_address','address_line_1'], 'legacy')
        self.reject(['customer','ground_truth','delivery_address','address_line_2'], 'legacy')
        self.reject(['customer','pending_field'], 'timber')
        for field in ('address','city','state','postcode','country'):
            raw = deepcopy(self.raw)
            del raw['customer']['ground_truth']['delivery_address'][field]
            with self.assertRaises(ValidationError): self.validate(raw)
        for key in self.raw['evaluation']['invariants']:
            raw = deepcopy(self.raw); del raw['evaluation']['invariants'][key]
            with self.assertRaises(ValidationError): self.validate(raw)

    def test_quantity_reusable_and_optional_nulls(self):
        raw = deepcopy(self.raw)
        raw['customer']['ground_truth']['configuration']['quantity'] = 2
        raw['evaluation']['system_derived']['pricing'].update(shipping_cost='1060',total_price='14660')
        scenario = self.validate(raw)
        validate_repository(scenario)
        self.assertEqual(scenario.customer.ground_truth.configuration.quantity, 2)
        self.assertIsNone(scenario.customer.ground_truth.customer.company_name)
        self.assertIsNone(scenario.customer.ground_truth.customer.customer_instructions)

    def test_cross_field_rejections(self):
        self.reject(['customer','initial_disclosures'], ['timber','timber'])
        self.reject(['customer','initially_known_configuration_fields'], ['product_model'])
        self.reject(['evaluation','system_derived','pricing','total_price'], '1')
        self.reject(['evaluation','system_derived','pricing','customisation_price'], '1')
        self.reject(['evaluation','outcome','committed_order_count'], 2)
        for flag in ('requires_visible_artifact','requires_explicit_confirmation_request','requires_artifact_matches_ground_truth'):
            for invalid in (False, 1, 'true'):
                self.reject(['customer','conversation_policy','configuration_confirmation',flag], invalid)
        self.raw = json.loads((SCENARIO_DIRECTORY / 'S02.json').read_text())
        self.reject(['customer','initial_disclosures'], ['timber'])
        self.reject(['customer','conversation_policy','configuration_selection','discovery_fields'], ['timber'])
        self.reject(['customer','conversation_policy','configuration_selection','discovery_fields'], ['timber','timber'])
        self.reject(['customer','conversation_policy','configuration_selection','type'], 'DISCOVER_THEN_SELECT')

    def test_known_need_not_be_disclosed(self):
        self.raw['customer']['initial_disclosures'] = []
        self.validate(self.raw)

    def test_repository_discrepancies_are_not_repaired(self):
        for path, value, boundary in [
            (['fixtures','shipping','sha256'], '0'*64, 'sha256'),
            (['customer','ground_truth','configuration','timber'], 'Not a timber', 'lookup'),
            (['customer','ground_truth','configuration','product_model'], 'Unknown', 'lookup'),
            (['customer','ground_truth','configuration','table_size'], '12ft', 'lookup'),
            (['customer','ground_truth','room_size'], '1m x 1m', 'room'),
            (['customer','ground_truth','delivery_address','postcode'], '9999', 'postcode'),
            (['evaluation','system_derived','product_sku'], '\tB8ODYSSEY', 'product_sku'),
        ]:
            raw=deepcopy(self.raw); node=raw
            for key in path[:-1]: node=node[key]
            node[path[-1]]=value
            scenario=self.validate(raw)
            before=scenario.model_dump_json()
            with self.subTest(path=path):
                with self.assertRaisesRegex(FixtureDiscrepancy,boundary): validate_repository(scenario)
                self.assertEqual(before,scenario.model_dump_json())
        raw=deepcopy(self.raw)
        raw['evaluation']['system_derived']['pricing'].update(shipping_cost='531',total_price='7331')
        with self.assertRaisesRegex(FixtureDiscrepancy,'shipping_cost'): validate_repository(self.validate(raw))
        with patch('evaluation.scenario_loader.REPOSITORY_ROOT', Path('/nonexistent-cs1a')):
            with self.assertRaisesRegex(FixtureDiscrepancy,'unavailable'): validate_repository(self.validate(self.raw))

    def test_duplicate_json_keys_and_scenario_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'one.json'
            path.write_text(json.dumps(self.raw).replace('"schema_version": "1"', '"schema_version": "1", "schema_version": "1"'))
            with self.assertRaisesRegex(ValueError,'Duplicate JSON key'): load_scenario(path)
            path.write_text(json.dumps(self.raw))
            (Path(temp)/'two.json').write_text(json.dumps(self.raw))
            with self.assertRaisesRegex(ValueError,'Duplicate scenario_id'): load_scenarios(temp,check_repository=False)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'No scenario'): load_scenarios(temp)

    def test_customer_boundary_and_isolation(self):
        first, second = load_scenarios(check_repository=False), load_scenarios(check_repository=False)
        for a,b in zip(first,second):
            customer=a.customer_view()
            self.assertIsNot(customer,a.customer)
            self.assertIsNot(a.customer.ground_truth,b.customer.ground_truth)
            payload=customer.model_dump_json()
            for forbidden in ('evaluation','product_sku','unit_price','shipping_cost','total_price','fixtures','invariants','workflow_state'):
                self.assertNotIn('"'+forbidden+'"',payload)
            with self.assertRaises(ValidationError): customer.ground_truth.configuration.timber='Changed'
            exported=customer.model_dump()
            exported['ground_truth']['configuration']['timber']='Changed'
            self.assertNotEqual(a.customer.ground_truth.configuration.timber,'Changed')
        source=(Path(__file__).resolve().parents[1]/'evaluation'/'scenario_spec.py').read_text()
        for forbidden in ('order_creation_state','conversation_runtime','BusinessResult','Controller','ollama'):
            self.assertNotIn(forbidden,source)


FIXTURE_HASHES = {'S01.json': 'a349e5db116fd82e36f772e00890f53109c1d961c90dacc6391f4464e041b29e', 'S02.json': '29f80533e9d7d4452fa06ca86365888194b7c8a861e5a1c8fd93f5e5652640c8', 'S03.json': 'f963a27e6a16ba3a52231e484d1668d38d8d26f281aaa56d054f72347b2307c7'}

if __name__ == '__main__':
    unittest.main()
