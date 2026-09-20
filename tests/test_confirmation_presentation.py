"""Deterministic artifact and framing boundary tests; no model/backend required."""

from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workflow.confirmation_presentation import render_configuration_summary, render_provisional_order
from agents.support_agent import compose_customer_response
from entity.support import ConfirmationFraming
from tests.test_support_agent import CONFIG, FINAL, Reason, result_for


class RendererTests(unittest.TestCase):
    def test_configuration_exact_output(self):
        self.assertEqual(render_configuration_summary(CONFIG), '''**Configuration Summary**

- **Product model:** Odyssey
- **Table size:** 8ft
- **Timber:** Tassie Oak
- **Timber finish:** White
- **Cloth colour:** Grey
- **Bracket:** Standard rubber
- **Top profile:** Waterfall
- **Quantity:** 1''')

    def test_provisional_order_all_visible_fields_and_no_metadata(self):
        snapshot = deepcopy(FINAL)
        snapshot.update(company_name="Example Company", customer_instructions="Use side door",
                        workflow_id="secret-workflow", confirmation_intent="SECRET-INTENT")
        snapshot['delivery_address']['address'] = 'Unit A, 1 Example Street'
        text = render_provisional_order(snapshot)
        for value in ("Demo Customer", "Example Company", "0400000000", "customer@example", "Use side door",
                      "1 Example Street", "Unit A", "Melbourne", "VIC", "3000", "Australia",
                      "Odyssey", "8ft", "Tassie Oak", "White", "Grey", "Standard rubber", "Waterfall"):
            self.assertIn(value, text)
        for label in ('Product code', 'Room size', 'Quantity', 'Customisation price', 'Unit price', 'Delivery cost', 'Total'):
            self.assertIn(label, text)
        self.assertIn('INTERNAL\\-SKU', text)
        self.assertIn('**Delivery cost:** 0', text)
        self.assertIn('**Total:** 7330', text)
        self.assertIn('**Email:** customer@example\\.com', text)
        for secret in ('SUITABLE', 'secret-workflow', 'SECRET-INTENT', 'validation', '$', 'AUD', 'tax', 'tomorrow'):
            self.assertNotIn(secret, text)

    def test_numeric_value_has_one_lossless_display(self):
        outputs = []
        for value in (7330, '7330.000', Decimal('7.330E+3'), 7330.0):
            snapshot = deepcopy(FINAL)
            snapshot['total_price'] = value
            outputs.append(render_provisional_order(snapshot))
        self.assertEqual(len(set(outputs)), 1)
        for value in (0, '0.00', Decimal('-0')):
            snapshot = deepcopy(FINAL)
            for key in ('customisation_price', 'unit_price', 'shipping_cost', 'total_price'):
                snapshot[key] = value
            text = render_provisional_order(snapshot)
            for label in ('Customisation price', 'Unit price', 'Delivery cost', 'Total'):
                self.assertIn(f'**{label}:** 0', text)
        snapshot['total_price'] = '123.456789'
        self.assertIn('123\\.456789', render_provisional_order(snapshot))

    def test_pure_repeatable_order_independent_and_snapshot_only(self):
        for renderer, snapshot in ((render_configuration_summary, CONFIG), (render_provisional_order, FINAL)):
            before = deepcopy(snapshot)
            expected = renderer(snapshot)
            self.assertEqual(renderer(snapshot), expected)
            self.assertEqual(renderer(dict(reversed(list(snapshot.items())))), expected)
            self.assertEqual(snapshot, before)
            changed = deepcopy(snapshot)
            changed['felt_color'] = 'Purple'
            self.assertNotEqual(renderer(changed), expected)
            self.assertNotIn('Grey', renderer(changed))
        self.assertNotIn('SECRET', render_configuration_summary({**CONFIG, 'workflow_id': 'SECRET'}))

    def test_null_optional_omitted_but_missing_fields_rejected(self):
        text = render_provisional_order(FINAL)
        for label in ('**Company:**', '**Customer instructions:**', '**Address line 2:**'):
            self.assertNotIn(label, text)
        for key in FINAL:
            if key == 'room_size_validation_result':
                continue  # Internal-only evidence is not needed for rendering.
            snapshot = deepcopy(FINAL)
            snapshot.pop(key)
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                render_provisional_order(snapshot)
        for key in CONFIG:
            broken = deepcopy(CONFIG)
            broken.pop(key)
            with self.assertRaises(ValueError):
                render_configuration_summary(broken)

    def test_malformed_values_fail_without_repair(self):
        for key, value in (('total_price', True), ('total_price', 'NaN'), ('total_price', '-1'),
                           ('quantity', 0), ('phone', None), ('email', ' ')):
            snapshot = deepcopy(FINAL)
            snapshot[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                render_provisional_order(snapshot)

    def test_escape_customer_markup_and_multiline_without_structure_injection(self):
        snapshot = deepcopy(FINAL)
        snapshot['customer_instructions'] = 'Line one\n# Forged heading <script> & [link](url)'
        text = render_provisional_order(snapshot)
        self.assertIn('Line one<br>\\# Forged heading &lt;script&gt; &amp; \\[link\\]\\(url\\)', text)
        self.assertNotIn('\n# Forged', text)

    def test_actual_controller_snapshot_serialization_is_rendered_directly(self):
        from tests.test_order_creation_controller import complete_customer_state, PricingTests
        from workflow.order.order_creation_controller import execute_order_creation_workflow
        for state, key, renderer in (
            (complete_customer_state(), 'configuration_snapshot', render_configuration_summary),
            (PricingTests().pricing_state(), 'final_order_snapshot', render_provisional_order),
        ):
            result = execute_order_creation_workflow(state)
            before = deepcopy(state)
            authoritative = state.order_snapshot if key == 'configuration_snapshot' else state.final_order_snapshot.model_dump(mode='json')
            self.assertEqual(renderer(result.data[key]), renderer(authoritative))
            self.assertEqual(state, before)


class ConfirmationCompositionTests(unittest.TestCase):
    def test_artifact_inserted_unchanged_and_llm_sees_no_values_or_history(self):
        for reason, key, renderer in (
            (Reason.CONFIGURATION_CONFIRMATION_REQUIRED, 'configuration_snapshot', render_configuration_summary),
            (Reason.FINAL_CONFIRMATION_REQUIRED, 'final_order_snapshot', render_provisional_order),
        ):
            result = result_for(reason)
            before = deepcopy(result)
            request = ('Please confirm the configuration or tell me what to change.' if reason == Reason.CONFIGURATION_CONFIRMATION_REQUIRED
                       else 'Please explicitly confirm you wish to place this order, or tell me what to change.')
            framing = {'introduction': 'Please review the details below.', 'confirmation_request': request}
            with patch('agents.support_agent.chat', return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps(framing)))) as chat:
                response = compose_customer_response(result, current_message='Secret customer text',
                    conversation_history=[{'role': 'assistant', 'content': 'Historical private data'}])
            artifact = renderer(result.data[key])
            self.assertEqual(response.text, framing['introduction'] + '\n\n' + artifact + '\n\n' + request)
            self.assertEqual(response.text.count(artifact), 1)
            payload = json.loads(chat.call_args.kwargs['messages'][1]['content'])
            self.assertEqual(payload['allowed_facts'], {})
            for secret in ('Odyssey', '7330', 'Secret customer', 'Historical private', 'snapshot', '0400000000'):
                self.assertNotIn(secret, json.dumps(payload))
            self.assertEqual(chat.call_args.kwargs['format'], ConfirmationFraming.model_json_schema())
            self.assertEqual(result, before)

    def test_invalid_framing_and_failures_propagate(self):
        invalid = [None, '', '{}', '{"text":"wrong contract"}',
                   json.dumps({'introduction': 'Price: 999.', 'confirmation_request': 'Please confirm to place the order.'}),
                   json.dumps({'introduction': 'Please review.', 'confirmation_request': 'Thanks.'}),
                   json.dumps({'introduction': 'Please review.', 'confirmation_request': 'Please confirm.', 'artifact': 'replacement'})]
        for content in invalid:
            with patch('agents.support_agent.chat', return_value=SimpleNamespace(message=SimpleNamespace(content=content))):
                with self.subTest(content=content), self.assertRaises(ValueError):
                    compose_customer_response(result_for(Reason.FINAL_CONFIRMATION_REQUIRED))
        with patch('agents.support_agent.chat', side_effect=ConnectionError('Unavailable')):
            with self.assertRaises(ConnectionError):
                compose_customer_response(result_for(Reason.FINAL_CONFIRMATION_REQUIRED))

    def test_missing_artifact_evidence_fails_before_llm(self):
        result = result_for(Reason.FINAL_CONFIRMATION_REQUIRED)
        result.data['final_order_snapshot'].pop('phone')
        with patch('agents.support_agent.chat') as chat, self.assertRaises(ValueError):
            compose_customer_response(result)
        chat.assert_not_called()

    def test_natural_explicit_placement_question_does_not_require_keyword(self):
        framing = {'introduction': 'Please review the provisional order below.',
                   'confirmation_request': 'Do you wish to place this provisional order?'}
        with patch('agents.support_agent.chat', return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps(framing)))):
            response = compose_customer_response(result_for(Reason.FINAL_CONFIRMATION_REQUIRED))
        self.assertTrue(response.text.endswith(framing['confirmation_request']))
        framing['confirmation_request'] = 'Otherwise, approve the configuration as described.'
        with patch('agents.support_agent.chat', return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps(framing)))):
            response = compose_customer_response(result_for(Reason.CONFIGURATION_CONFIRMATION_REQUIRED))
        self.assertTrue(response.text.endswith(framing['confirmation_request']))


if __name__ == '__main__':
    unittest.main()
