"""CS2 public text unit tests; no model or runtime execution."""
import unittest
from evaluation import llm_public_evidence as e
from evaluation.public_observation import PublicMessage
from evaluation.scenario_loader import load_scenarios
from test_public_observation import PILOT_001_RESPONSE, configuration_text, final_text, final_snapshot
from confirmation_presentation import render_provisional_order


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.customer = load_scenarios(check_repository=False)[0].customer_view()

    def test_reference_unicode_original_offsets(self):
        text = 'Thanks — welcome. Marri is available.'
        ref = tuple(e.units(text, 1))[1]
        self.assertEqual(ref.resolve((PublicMessage(role='user', text='x'), PublicMessage(role='assistant', text=text))), 'Marri is available.')
        self.assertEqual(ref.start, text.index('Marri'))

    def test_reference_wrong_role_and_quote(self):
        ref = e.reference('Marri is available.', 0)
        for message in (PublicMessage(role='user', text=ref.quote), PublicMessage(role='assistant', text='Something else.')):
            with self.assertRaises(ValueError):
                ref.resolve((message,))

    def test_requested_facts_paraphrases(self):
        for text in ('Can you share your email?', 'Please provide your email address.', "What's your email?", 'Could you please provide your email address?'):
            with self.subTest(text=text):
                self.assertEqual(e.requested_fields(text, 1)[0].field, 'email')

    def test_compound_information_requests(self):
        result = e.requested_fields('Could you please provide your phone number, email address and full name?', 1)
        self.assertEqual([r.field for r in result], ['phone', 'email', 'customer_name'])

    def test_unknown_and_quoted_requests(self):
        for text in ('She said: Please provide your email.', '"Please provide your email."', 'Please provide your password.'):
            self.assertEqual(e.requested_fields(text, 1), ())

    def test_positive_offer_families(self):
        for text in ('Marri is available.', 'We offer Tassie Oak and Marri.', 'Available timber options include Tassie Oak and Marri.', 'Marri is available for timber.'):
            with self.subTest(text=text):
                offers = e.availability(text, 1, 'timber')
                self.assertIn('Marri', offers[0].values)
                self.assertEqual(offers[0].field, 'timber')

    def test_bullet_offer_original_span(self):
        text = 'Happy to help!\nAvailable timber options:\n- Tassie Oak\n- Marri\nThanks.'
        offers = e.availability(text, 1)
        self.assertEqual(offers[0].values, ('Tassie Oak', 'Marri'))
        self.assertEqual(offers[0].evidence.quote, 'Available timber options:\n- Tassie Oak\n- Marri')
        e.assert_claims_safe(text, 1, [o.evidence for o in offers])

    def test_denial(self):
        for text in ('Marri is unavailable.', 'Marri is not available.'):
            self.assertEqual(e.availability(text, 1, 'timber')[0].status, 'DENIED')

    def test_wrong_field_is_preserved(self):
        self.assertEqual(e.availability('Marri is available for timber finish.', 1, 'timber')[0].field, 'timber_painting')

    def test_hypothetical_quoted_and_partial_mentions_not_offers(self):
        for text in ('"Marri is available."', 'If Marri is available, choose it.', 'Marri might be available.', 'You said Marri is available.', 'Marri.', 'Marri is available, but not for timber.'):
            self.assertEqual(e.availability(text, 1, 'timber'), (), text)

    def test_material_tail_not_ignored(self):
        text = 'Marri is available. However, do not choose it.'
        offers = e.availability(text, 1, 'timber')
        with self.assertRaises(e.EvidenceError):
            e.assert_claims_safe(text, 1, [o.evidence for o in offers])

    def test_fieldless_requires_public_context(self):
        self.assertEqual(e.availability('Marri is available.', 1), ())
        self.assertEqual(e.question_context('What timber options are available?'), 'timber')

    def test_supported_values(self):
        text = 'Which timber would you like?\nSupported values: ["Marri", "Zebra"]'
        self.assertEqual(e.availability(text, 1)[0].values, ('Marri', 'Zebra'))

    def test_configuration_original_body(self):
        body = configuration_text(self.customer)
        text = 'A little patience goes a long way.\n\n' + body + '\n\nIf everything looks right, please approve this configuration.'
        artifact = e.extract_artifact(text, 1)
        self.assertEqual(artifact.evidence.quote, body)
        request = e.approval_requests(text, 1, artifact)[0]
        e.assert_framing_safe(text, 1, artifact, request)

    def test_preserved_pilot_001(self):
        artifact = e.extract_artifact(PILOT_001_RESPONSE, 29)
        request = e.approval_requests(PILOT_001_RESPONSE, 29, artifact)[0]
        e.assert_framing_safe(PILOT_001_RESPONSE, 29, artifact, request)

    def test_review_is_not_approval(self):
        text = configuration_text(self.customer) + '\n\nPlease review the details carefully.'
        self.assertEqual(e.approval_requests(text, 1, e.extract_artifact(text, 1)), ())

    def test_veto_outside_cited_request(self):
        body = configuration_text(self.customer)
        for extra in ('Do not confirm this configuration.', 'The quantity is 2.', 'We will place the order.', 'She said: Please confirm the configuration.', 'Actually, choose Zebra.'):
            text = body + '\n\nPlease confirm the configuration. ' + extra
            artifact = e.extract_artifact(text, 1)
            with self.assertRaises(e.EvidenceError, msg=extra):
                e.assert_framing_safe(text, 1, artifact, e.approval_requests(text, 1, artifact)[0])

    def test_duplicate_missing_reordered_rows(self):
        body = configuration_text(self.customer)
        for malformed in (body.replace('- **Timber:** Tassie Oak\n', ''), body + '\n- **Quantity:** 1', body.replace('- **Quantity:** 1', '- **Quantity:** 01'), body.replace('- **Quantity:** 1', '- **Quantity:** 1 junk')):
            self.assertIsNone(e.extract_artifact(malformed, 1))

    def test_competing_quoted_fenced_artifacts(self):
        body = configuration_text(self.customer)
        for text in (body + '\n\n' + body, '```\n' + body + '\n```', '> ' + body.replace('\n', '\n> '), '"Example"\n' + body):
            self.assertIsNone(e.extract_artifact(text, 1))

    def test_final_body_and_arbitrary_public_prices(self):
        artifact = e.extract_artifact(final_text(self.customer), 1)
        values = {v.field: v.value for v in artifact.values}
        self.assertEqual(values['product_sku'], 'PUBLIC-CODE')
        self.assertEqual(values['total_price'], '42')
        self.assertEqual(values['delivery_address.city'], 'Melbourne')

    def test_final_optional_rows_and_escaping(self):
        snap = final_snapshot(self.customer)
        snap.update(company_name='A & B', customer_instructions='Door *left*\nRing bell')
        artifact = e.extract_artifact(render_provisional_order(snap), 1)
        self.assertEqual({v.field: v.value for v in artifact.values}['customer_instructions'], snap['customer_instructions'])

    def test_final_placement_scope(self):
        body = final_text(self.customer)
        for request in ('Do you wish to place this provisional order?', 'If everything looks right, please confirm the final order.'):
            text = body + '\n\n' + request
            artifact = e.extract_artifact(text, 1)
            e.assert_framing_safe(text, 1, artifact, e.approval_requests(text, 1, artifact)[0])
        text = body + '\n\nPlease confirm the configuration.'
        self.assertEqual(e.approval_requests(text, 1, e.extract_artifact(text, 1)), ())

    def test_order_created(self):
        self.assertEqual(e.terminal_evidence('Thanks! Your order ABC-1 has been created.', 1, 'ORDER_CREATED')[0].quote, 'Your order ABC-1 has been created.')

    def test_false_creation(self):
        for text in ('Your order will be created.', '"Your order has been created."', 'Your order has not been created.', 'Your order has been created. Actually, it is unavailable.'):
            with self.assertRaises(e.EvidenceError, msg=text):
                e.terminal_evidence(text, 1, 'ORDER_CREATED')

    def test_unavailable(self):
        self.assertTrue(e.terminal_evidence('Information is unavailable here.', 1, 'UNAVAILABLE'))

    def test_model_qualified_size_evidence(self):
        text = 'Available table size options for Saga are 7ft and 8ft.'
        self.assertEqual(e.availability(text, 1, 'table_size', 'Saga')[0].values, ('7ft', '8ft'))
        self.assertEqual(e.availability(text, 1, 'table_size', 'Odyssey'), ())
        self.assertEqual(e.availability(text, 1, 'table_size'), ())

    def test_ambiguous_model_size_union_not_authority(self):
        text = 'Table sizes available across supported models are 7ft, 8ft and 9ft. Size availability depends on model.'
        self.assertEqual(e.availability(text, 1, 'table_size'), ())

    def test_negated_correctness_and_example_veto(self):
        for extra in ('The configuration is not correct.', 'This is only an example.', 'The table is red.'):
            text = configuration_text(self.customer) + '\n\nPlease confirm the configuration. ' + extra
            artifact = e.extract_artifact(text, 1)
            with self.assertRaises(e.EvidenceError):
                e.assert_framing_safe(text, 1, artifact, e.approval_requests(text, 1, artifact)[0])

    def test_multiple_approval_requests_ambiguous(self):
        text = configuration_text(self.customer) + '\n\nPlease confirm the configuration. Could you approve this configuration?'
        artifact = e.extract_artifact(text, 1)
        with self.assertRaises(e.EvidenceError):
            e.assert_framing_safe(text, 1, artifact, e.approval_requests(text, 1, artifact)[0])

    def test_price_scalar_structure_only(self):
        text = final_text(self.customer)
        for value in ('-1', 'NaN', '1e3', '42 AUD'):
            self.assertIsNone(e.extract_artifact(text.replace('- **Total:** 42', '- **Total:** ' + value), 1))

    def test_noncanonical_escaping(self):
        text = configuration_text(self.customer).replace('Tassie Oak', 'Tassie &amp; Oak')
        self.assertIsNotNone(e.extract_artifact(text, 1))
        self.assertIsNone(e.extract_artifact(text.replace('&amp;', '&'), 1))

    def test_request_conflict_outside_span(self):
        text = 'Please provide your email. No email is needed.'
        requests = e.requested_fields(text, 1)
        with self.assertRaises(e.EvidenceError):
            e.assert_claims_safe(text, 1, [r.evidence for r in requests])

    def test_competing_noncanonical_heading(self):
        text = configuration_text(self.customer) + '\n\n**Provisional Order** example'
        self.assertIsNone(e.extract_artifact(text, 1))
