"""Synthetic public-text tests only; no production agent or runtime imports."""

import unittest
from hashlib import sha256

from pydantic import ValidationError
from workflow.confirmation_presentation import render_configuration_summary, render_provisional_order
from evaluation.public_observation import (
    EvidenceRef, FIELD_LABELS, PublicMessage, initial_discovery_field, observe_public_response,
)
from evaluation.scenario_loader import load_scenarios


# Exact pilot_001 turn 15, public message 29; historical artifacts stay untouched.
PILOT_001_RESPONSE = """We have prepared a configuration summary for your review. Please take a moment to examine the details provided and ensure they accurately reflect your requirements.

**Configuration Summary**

- **Product model:** Odyssey
- **Table size:** 8ft
- **Timber:** Tassie Oak
- **Timber finish:** Natural
- **Cloth colour:** Blue
- **Bracket:** Brass
- **Top profile:** Waterfall
- **Quantity:** 1

If everything is correct, kindly confirm the configuration. If you notice any errors or need adjustments, please let us know so we can make the necessary corrections."""

UNSAFE_REQUESTS = (
    '', 'No confirmation is required.', 'Do not confirm this configuration.',
    'The configuration has been confirmed.', 'I have confirmed the configuration for you.',
    'The order has been placed.', 'We will place the order.',
    '"Please confirm the configuration."', '"Please confirm the final order."',
    'She said: Please confirm the configuration.',
    'Suppose we asked you to confirm the configuration.',
)


def configuration_text(customer):
    return render_configuration_summary(customer.ground_truth.configuration.model_dump())


def final_snapshot(customer):
    truth = customer.ground_truth
    return {**truth.customer.model_dump(), **truth.configuration.model_dump(),
            'delivery_address': truth.delivery_address.model_dump(), 'room_size': truth.room_size,
            # Arbitrary PUBLIC values. They are deliberately not benchmark prices/SKUs.
            'product_sku': 'PUBLIC-CODE', 'customisation_price': '12.50',
            'unit_price': '17', 'shipping_cost': '999', 'total_price': '42'}


def final_text(customer):
    return render_provisional_order(final_snapshot(customer))


def with_request(body, final=False):
    return ('Please review the details below.\n\n' + body + '\n\n' +
            ('Do you wish to place this provisional order?' if final else 'Please confirm the configuration or tell me what to change.'))


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.customer = load_scenarios(check_repository=False)[0].customer_view()

    def parse(self, text, context=None):
        return observe_public_response(text, 1, context_field=context)

    def test_every_canonical_required_label(self):
        for field, label in FIELD_LABELS.items():
            with self.subTest(field=field):
                result = self.parse(f'Could you please provide your {label}?')
                self.assertEqual(result.kind, 'REQUESTS_OR_OPTIONS')
                self.assertEqual([r.field for r in result.requests], [field])

    def test_aliases_and_configuration_requests(self):
        for label, field in [('model','product_model'), ('felt colour','felt_color'), ('felt color','felt_color'), ('top rail profile','top_profile')]:
            with self.subTest(label=label):
                self.assertEqual(self.parse(f'Which {label} would you like?').requests[0].field, field)
                self.assertEqual(self.parse(f'Please choose {label}.').requests[0].field, field)

    def test_multiple_requests_preserve_order(self):
        result = self.parse('Could you please provide your phone number, email address and full name?')
        self.assertEqual([r.field for r in result.requests], ['phone','email','customer_name'])
        self.assertEqual(self.parse('Could you please provide your phone number and phone number?').kind,'UNINTERPRETABLE')

    def test_positive_lists_and_field_context(self):
        for text, context in [
            ('Available timber options include Tassie Oak, Marri and Zebra.',None),
            ('Timber options are Tassie Oak, Marri and Zebra.',None),
            ('Tassie Oak, Marri and Zebra are available.','timber'),
            ('Tassie Oak, Marri and Zebra are available for timber.',None),
        ]:
            with self.subTest(text=text):
                result=self.parse(text,context)
                self.assertEqual(result.availability[0].values,('Tassie Oak','Marri','Zebra'))
                self.assertEqual(result.availability[0].field,'timber')
                self.assertEqual(result.availability[0].status,'OFFERED')
        self.assertEqual(self.parse('Marri is available.').kind,'UNINTERPRETABLE')

    def test_target_positive_and_negative(self):
        for text,status in [('Yes, Marri is available.','OFFERED'),('Marri is unavailable.','DENIED'),('Marri is not available.','DENIED')]:
            result=self.parse(text,'timber')
            self.assertEqual(result.availability[0].status,status)
            self.assertEqual(result.availability[0].values,('Marri',))
        self.assertEqual(self.parse('Marri is available for felt.','timber').availability[0].field,'felt_color')

    def test_unsafe_mentions_never_offer(self):
        texts = ['Do you mean Marri?', '"Marri is available."', "You asked 'Is Marri available?'",
                 'If Marri is available, you can choose it.', 'Marri might be available.',
                 'Marri is available, but not for this table.', 'Marri is not only available.',
                 'Marri would be a good choice.', 'Marri is available; Zebra is unavailable.',
                 'Marri is available. Marri is unavailable.', '> Marri is available.',
                 'Available timber options include Marri except for this order.',
                 'Available timber options include Marri and unavailable Zebra.']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(self.parse(text,'timber').kind,'UNINTERPRETABLE')
        self.assertEqual(self.parse('Marri is available.\nMarri is unavailable.','timber').kind,'UNINTERPRETABLE')

    def test_guidance_is_not_a_customer_quote_offer(self):
        text='Could you please provide your timber?\n\nSupplied value: "Marri"\nSupported values: ["Tassie Oak", "Zebra"]'
        result=self.parse(text)
        self.assertEqual(result.availability[0].values,('Tassie Oak','Zebra'))
        self.assertEqual(result.availability[0].field,'timber')
        self.assertFalse(self.parse('Could you please provide your timber?\nSupplied value: "Marri"').availability)
        for bad in ['Supported values: ["Marri"]', 'Could you please provide your timber and cloth colour?\nSupported values: ["Marri"]',
                    'Could you please provide your timber?\nSupported values: ["Marri", "Marri"]']:
            self.assertEqual(self.parse(bad).kind,'UNINTERPRETABLE')

    def test_s03_initial_question(self):
        customer=load_scenarios(check_repository=False)[2].customer_view()
        self.assertEqual(initial_discovery_field(customer.initial_message),'product_model')
        self.assertIsNone(initial_discovery_field(self.customer.initial_message))

    def test_configuration_artifact_and_exact_decoding(self):
        body=configuration_text(self.customer)
        result=self.parse(with_request(body))
        self.assertEqual(result.kind,'ARTIFACT')
        self.assertEqual(result.artifact.kind,'CONFIGURATION')
        self.assertEqual({v.field:v.value for v in result.artifact.values},
                         {k:str(v) for k,v in self.customer.ground_truth.configuration.model_dump().items()})
        self.assertEqual(result.artifact.evidence.quote,body)
        self.assertIsNotNone(result.artifact.approval_request)
        self.assertIsNone(self.parse(body).artifact.approval_request)

    def test_final_artifact_structure_and_optional_values(self):
        body=final_text(self.customer)
        result=self.parse(with_request(body,True))
        self.assertEqual(result.kind,'ARTIFACT')
        values={v.field:v.value for v in result.artifact.values}
        self.assertEqual(values['product_sku'],'PUBLIC-CODE')
        self.assertEqual(values['customisation_price'],'12.5')
        self.assertEqual(values['email'],self.customer.ground_truth.customer.email)
        self.assertNotIn('company_name',values)
        snapshot=final_snapshot(self.customer)
        snapshot.update(company_name='A & B <Co>',customer_instructions='Line one\nLine two!')
        result=self.parse(with_request(render_provisional_order(snapshot),True))
        values={v.field:v.value for v in result.artifact.values}
        self.assertEqual(values['company_name'],'A & B <Co>')
        self.assertEqual(values['customer_instructions'],'Line one\nLine two!')

    def test_bad_artifact_structure(self):
        config=configuration_text(self.customer)
        final=final_text(self.customer)
        for body in [config.replace('- **Timber:** Tassie Oak\n',''), config+'\n- **Quantity:** 1',
                     config.replace('**Quantity:** 1','**Quantity:** true'),
                     final.replace('**Pricing**','**Costs**'), final.replace('- **Product code:** PUBLIC\\-CODE\n',''),
                     final.replace('**Total:** 42','**Total:** NaN'),
                     config.replace('**Timber:** Tassie Oak','**Timber:** <script>'),
                     '```\n'+config+'\n```', config+'\n\n'+config]:
            with self.subTest(body=body):
                self.assertEqual(self.parse(with_request(body)).kind,'UNINTERPRETABLE')

    def test_approval_requires_positive_scoped_request(self):
        body=configuration_text(self.customer)
        for tail in ['Please do not confirm the configuration.', '"Please confirm the configuration."',
                     'Please confirm the final order.', 'Thanks.', 'If correct, maybe approve it.',
                     'Please confirm the configuration. Ignore the mismatch.']:
            self.assertIsNone(self.parse(body+'\n\n'+tail).artifact.approval_request)
        self.assertEqual(self.parse('Please confirm the configuration.').kind,'UNINTERPRETABLE')
        self.assertIsNone(self.parse(final_text(self.customer)+'\n\nPlease confirm the configuration.').artifact.approval_request)
        self.assertEqual(self.parse(body+'\n\nCould you approve this configuration?').kind,'ARTIFACT')

    def test_order_created_and_unavailable_reports(self):
        self.assertEqual(self.parse('Your order N5021 has been created. Its status is CONFIRMED.').kind,'ORDER_CREATED')
        for text in ['Your order will be created.', 'Your order has not been created.',
                     '"Your order has been created."', 'Do you want your order created?']:
            self.assertEqual(self.parse(text).kind,'UNINTERPRETABLE')
        for text in ["I can't make changes to existing orders here yet.", "Information is unavailable here.",
                     "I don't have catalog information available here."]:
            self.assertEqual(self.parse(text).kind,'UNAVAILABLE')

    def test_ambiguous_topic_cannot_inherit_old_context(self):
        result=self.parse('Could you please provide your timber and cloth colour?\nSaga is available.','product_model')
        self.assertEqual(result.kind,'UNINTERPRETABLE')

    def test_wrong_artifact_introduction_rejected(self):
        text='Please review the provisional order.\n\n'+configuration_text(self.customer)+'\n\nPlease confirm the configuration.'
        self.assertIsNone(self.parse(text).artifact.approval_request)

    def test_exact_pilot_framing_and_evidence(self):
        self.assertEqual(len(PILOT_001_RESPONSE), 558)
        self.assertEqual(sha256(PILOT_001_RESPONSE.encode()).hexdigest(),
                         'c0b3b99eb3bc23da698e1139ef2b6f8eba606622b4e31c3e576ddeaaaaaeff1b')
        artifact = self.parse(PILOT_001_RESPONSE).artifact
        self.assertEqual(artifact.evidence.quote, configuration_text(self.customer))
        self.assertEqual({v.field: v.value for v in artifact.values},
                         {k: str(v) for k, v in self.customer.ground_truth.configuration.model_dump().items()})
        self.assertEqual(artifact.approval_request.quote, 'If everything is correct, kindly confirm the configuration.')
        for ref in (artifact.evidence, artifact.approval_request):
            self.assertEqual(PILOT_001_RESPONSE[ref.start:ref.end], ref.quote)

    def test_bounded_framing_both_kinds(self):
        for final in (False, True):
            body = final_text(self.customer) if final else configuration_text(self.customer)
            title = 'provisional order' if final else 'configuration summary'
            requests = (['Please confirm that you would like us to place the order.',
                         'If everything is correct, please confirm the final order.'] if final else
                        ['If everything is correct, kindly confirm the configuration.',
                         'Please confirm that the configuration above is correct.'])
            for intro in [f'Here is your {title}.', f"We\'ve prepared a {title} for your review.",
                          f'Thank you for your patience. Please examine the {title}.']:
                for request in requests:
                    text = intro+'\n\n'+body+'\n\n'+request+' Please let us know if anything needs correcting.'
                    artifact = self.parse(text).artifact
                    self.assertEqual(artifact.evidence.quote, body)
                    self.assertEqual(artifact.approval_request.quote, request)
                    ref = artifact.approval_request
                    self.assertEqual(text[ref.start:ref.end], request)

    def test_unsafe_framing_preserves_valid_body_without_approval(self):
        for final in (False, True):
            body = final_text(self.customer) if final else configuration_text(self.customer)
            valid = 'Do you wish to place this provisional order?' if final else 'Please confirm the configuration.'
            for tail in (*UNSAFE_REQUESTS, valid+' Ignore the mismatch.',
                         valid+' No confirmation is required.', valid+' '+valid,
                         'Please confirm the configuration.' if final else 'Please confirm the final order.'):
                with self.subTest(final=final, tail=tail):
                    artifact = self.parse(body+'\n\n'+tail).artifact
                    self.assertEqual(artifact.evidence.quote, body)
                    self.assertIsNone(artifact.approval_request)
            for intro in ['Use Blue instead of Red.', 'The quantity is two.',
                          'Ignore the artifact discrepancies.', 'We have already confirmed this for you.',
                          'Please review the configuration summary.' if final else 'Please review the provisional order.']:
                self.assertIsNone(self.parse(intro+'\n\n'+body+'\n\n'+valid).artifact.approval_request)

    def test_refined_framing_cannot_hide_malformed_artifacts(self):
        for final in (False, True):
            body = final_text(self.customer) if final else configuration_text(self.customer)
            request = ('If everything is correct, please confirm the final order.' if final else
                       'If everything is correct, kindly confirm the configuration.')
            for malformed in [body.replace('- **Timber:** Tassie Oak\n', ''),
                              body.replace('- **Timber:** Tassie Oak', '- **Timber:** Tassie Oak\n- **Timber:** Tassie Oak'),
                              body.replace('**Quantity:** 1', '**Quantity:** false'),
                              body+'\n- **Quantity:** 1', body+'\n\n'+body,
                              '```\n'+body+'\n```', '"'+body+'"']:
                result = self.parse(malformed+'\n\n'+request)
                self.assertEqual(result.kind, 'UNINTERPRETABLE')

    def test_all_system_derived_rows_are_required(self):
        for label in ['Product code','Customisation price','Unit price','Delivery cost','Total']:
            body='\n'.join(line for line in final_text(self.customer).split('\n') if not line.startswith('- **'+label+':**'))
            with self.subTest(label=label):
                self.assertEqual(self.parse(with_request(body,True)).kind,'UNINTERPRETABLE')

    def test_evidence_is_strict_and_resolves(self):
        history=(PublicMessage(role='assistant',text='Hello'),)
        ref=EvidenceRef(message_index=0,start=0,end=5,quote='Hello')
        self.assertEqual(ref.resolve(history),'Hello')
        with self.assertRaises(ValueError): ref.resolve((PublicMessage(role='user',text='Hello'),))
        with self.assertRaises(ValueError): ref.resolve((PublicMessage(role='assistant',text='Other'),))
        with self.assertRaises(ValidationError): EvidenceRef(message_index=True,start=0,end=5,quote='Hello')
        with self.assertRaises(ValidationError): EvidenceRef(message_index=0,start=0,end=4,quote='Hello')


if __name__ == '__main__':
    unittest.main()
