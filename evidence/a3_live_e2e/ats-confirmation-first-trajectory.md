## Turn 1

Customer: I'd like to order an 8ft Odyssey pool table.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["customer_name", "email", "phone", "delivery_address.address_line_1", "delivery_address.city", "delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["customer_name"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your full name?

## Turn 2

Customer: My name is Demo Customer.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["email", "phone", "delivery_address.address_line_1", "delivery_address.city", "delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["email"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your email address?

## Turn 3

Customer: My email is customer@example.com.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["phone", "delivery_address.address_line_1", "delivery_address.city", "delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["phone"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your phone number?

## Turn 4

Customer: My phone number is 0400000000.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["delivery_address.address_line_1", "delivery_address.city", "delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["delivery_address.address_line_1"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your address line 1?

## Turn 5

Customer: My delivery street address is 1 Example Street.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["delivery_address.city", "delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["delivery_address.city"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your city?

## Turn 6

Customer: My delivery city is Melbourne.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["delivery_address.state", "delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["delivery_address.state"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your state?

## Turn 7

Customer: My delivery state is VIC.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["delivery_address.postcode", "delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["delivery_address.postcode"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your postcode?

## Turn 8

Customer: My delivery postcode is 3000.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["delivery_address.country", "room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["delivery_address.country"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your country?

## Turn 9

Customer: My delivery country is Australia.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["room_size", "product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["room_size"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your room size?

## Turn 10

Customer: My room is 5.2 metres by 4.0 metres.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["product_model", "timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["product_model"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your table model?

## Turn 11

Customer: I'd like the Odyssey model.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["timber", "timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["timber"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your timber?

## Turn 12

Customer: I'd like Tassie Oak timber.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["timber_painting", "felt_color", "bracket", "top_profile"]}, "required_input": ["timber_painting"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your timber finish?

## Turn 13

Customer: I'd like White timber painting.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["felt_color", "bracket", "top_profile"]}, "required_input": ["felt_color"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your cloth colour?

## Turn 14

Customer: I'd like Grey cloth.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["bracket", "top_profile"]}, "required_input": ["bracket"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your bracket?

## Turn 15

Customer: I'd like the Standard rubber bracket.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "COLLECT_REQUIREMENTS", "reason": "MISSING_REQUIRED_INFORMATION", "data": {"missing_fields": ["top_profile"]}, "required_input": ["top_profile"], "error": null}

Wt: COLLECT_REQUIREMENTS / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Could you please provide your top profile?

## Turn 16

Customer: I'd like the Waterfall top profile.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "CONFIGURATION_CONFIRMATION", "reason": "CONFIGURATION_CONFIRMATION_REQUIRED", "data": {"configuration_snapshot": {"product_model": "Odyssey", "table_size": "8ft", "timber": "Tassie Oak", "timber_painting": "White", "felt_color": "Grey", "bracket": "Standard rubber", "top_profile": "Waterfall", "quantity": 1}}, "required_input": ["configuration_confirmed"], "error": null}

Wt: CONFIGURATION_CONFIRMATION / AWAITING_USER_INPUT

Artifacts: {"configuration": true, "provisional_order": false}

Support:

We have prepared a configuration summary for your review. Please take a moment to examine the details provided and ensure they accurately reflect your requirements.

**Configuration Summary**

- **Product model:** Odyssey
- **Table size:** 8ft
- **Timber:** Tassie Oak
- **Timber finish:** White
- **Cloth colour:** Grey
- **Bracket:** Standard rubber
- **Top profile:** Waterfall
- **Quantity:** 1

If you notice any errors or need adjustments, please let us know so we can make the necessary corrections. We kindly ask for your approval of the configuration as presented.

## Turn 17

Customer: Yes, I confirm this configuration.

Classifier: WORKFLOW_RESPONSE

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "NEEDS_USER_INPUT", "current_stage": "FINAL_CONFIRMATION", "reason": "FINAL_CONFIRMATION_REQUIRED", "data": {"final_order_snapshot": {"customer_name": "Demo Customer", "company_name": null, "phone": "0400000000", "email": "customer@example.com", "delivery_address": {"address_line_1": "1 Example Street", "address_line_2": null, "city": "Melbourne", "state": "VIC", "postcode": "3000", "country": "Australia"}, "customer_instructions": null, "product_model": "Odyssey", "table_size": "8ft", "top_profile": "Waterfall", "bracket": "Standard rubber", "felt_color": "Grey", "timber": "Tassie Oak", "timber_painting": "White", "room_size": "5.2m x 4.0m", "room_size_validation_result": "SUITABLE", "quantity": 1, "product_sku": "B8ODYSSEY", "customisation_price": "800", "unit_price": "6050", "shipping_cost": "530", "total_price": "6580"}}, "required_input": ["final_order_confirmed"], "error": null}

Wt: FINAL_CONFIRMATION / AWAITING_USER_INPUT

Artifacts: {"configuration": false, "provisional_order": true}

Support:

We have prepared a provisional order for your review. Please take a moment to examine the details and let us know if any corrections are needed.

**Provisional Order**

**Customer details**

- **Customer name:** Demo Customer
- **Phone:** 0400000000
- **Email:** customer@example\.com

**Delivery address**

- **Address line 1:** 1 Example Street
- **City:** Melbourne
- **State:** VIC
- **Postcode:** 3000
- **Country:** Australia

**Product configuration**

- **Product code:** B8ODYSSEY
- **Product model:** Odyssey
- **Table size:** 8ft
- **Timber:** Tassie Oak
- **Timber finish:** White
- **Cloth colour:** Grey
- **Bracket:** Standard rubber
- **Top profile:** Waterfall
- **Quantity:** 1

**Room**

- **Room size:** 5\.2m x 4\.0m

**Pricing**

- **Customisation price:** 800
- **Unit price:** 6050
- **Delivery cost:** 530
- **Total:** 6580

Do you wish to place this provisional order?

## Turn 18

Customer: Yes, I confirm the final order and would like to place it.

Classifier: CREATE_ORDER

Root: ORDER_AGENT / CREATE_ORDER

BusinessResult: {"workflow_id": "WF-5578EAE8", "source_agent": "ORDER_AGENT", "action": "CREATE_ORDER", "result_status": "SUCCESS", "current_stage": "COMPLETED", "reason": "ORDER_CREATED", "data": {"order_id": "ORD-000001", "order_status": "CONFIRMED", "created": true}, "required_input": [], "error": null}

Wt: COMPLETED / COMPLETED

Artifacts: {"configuration": false, "provisional_order": false}

Support:

Your order has been successfully created. Order ID: ORD-000001, Order Status: CONFIRMED.

## Final state

```json
{
  "total_customer_turns": 18,
  "final_session": {
    "conversation_id": "SUP-LIVE-RUNTIME",
    "history": [
      {
        "role": "user",
        "content": "I'd like to order an 8ft Odyssey pool table."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your full name?"
      },
      {
        "role": "user",
        "content": "My name is Demo Customer."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your email address?"
      },
      {
        "role": "user",
        "content": "My email is customer@example.com."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your phone number?"
      },
      {
        "role": "user",
        "content": "My phone number is 0400000000."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your address line 1?"
      },
      {
        "role": "user",
        "content": "My delivery street address is 1 Example Street."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your city?"
      },
      {
        "role": "user",
        "content": "My delivery city is Melbourne."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your state?"
      },
      {
        "role": "user",
        "content": "My delivery state is VIC."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your postcode?"
      },
      {
        "role": "user",
        "content": "My delivery postcode is 3000."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your country?"
      },
      {
        "role": "user",
        "content": "My delivery country is Australia."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your room size?"
      },
      {
        "role": "user",
        "content": "My room is 5.2 metres by 4.0 metres."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your table model?"
      },
      {
        "role": "user",
        "content": "I'd like the Odyssey model."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your timber?"
      },
      {
        "role": "user",
        "content": "I'd like Tassie Oak timber."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your timber finish?"
      },
      {
        "role": "user",
        "content": "I'd like White timber painting."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your cloth colour?"
      },
      {
        "role": "user",
        "content": "I'd like Grey cloth."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your bracket?"
      },
      {
        "role": "user",
        "content": "I'd like the Standard rubber bracket."
      },
      {
        "role": "assistant",
        "content": "Could you please provide your top profile?"
      },
      {
        "role": "user",
        "content": "I'd like the Waterfall top profile."
      },
      {
        "role": "assistant",
        "content": "We have prepared a configuration summary for your review. Please take a moment to examine the details provided and ensure they accurately reflect your requirements.\n\n**Configuration Summary**\n\n- **Product model:** Odyssey\n- **Table size:** 8ft\n- **Timber:** Tassie Oak\n- **Timber finish:** White\n- **Cloth colour:** Grey\n- **Bracket:** Standard rubber\n- **Top profile:** Waterfall\n- **Quantity:** 1\n\nIf you notice any errors or need adjustments, please let us know so we can make the necessary corrections. We kindly ask for your approval of the configuration as presented."
      },
      {
        "role": "user",
        "content": "Yes, I confirm this configuration."
      },
      {
        "role": "assistant",
        "content": "We have prepared a provisional order for your review. Please take a moment to examine the details and let us know if any corrections are needed.\n\n**Provisional Order**\n\n**Customer details**\n\n- **Customer name:** Demo Customer\n- **Phone:** 0400000000\n- **Email:** customer@example\\.com\n\n**Delivery address**\n\n- **Address line 1:** 1 Example Street\n- **City:** Melbourne\n- **State:** VIC\n- **Postcode:** 3000\n- **Country:** Australia\n\n**Product configuration**\n\n- **Product code:** B8ODYSSEY\n- **Product model:** Odyssey\n- **Table size:** 8ft\n- **Timber:** Tassie Oak\n- **Timber finish:** White\n- **Cloth colour:** Grey\n- **Bracket:** Standard rubber\n- **Top profile:** Waterfall\n- **Quantity:** 1\n\n**Room**\n\n- **Room size:** 5\\.2m x 4\\.0m\n\n**Pricing**\n\n- **Customisation price:** 800\n- **Unit price:** 6050\n- **Delivery cost:** 530\n- **Total:** 6580\n\nDo you wish to place this provisional order?"
      },
      {
        "role": "user",
        "content": "Yes, I confirm the final order and would like to place it."
      },
      {
        "role": "assistant",
        "content": "Your order has been successfully created. Order ID: ORD-000001, Order Status: CONFIRMED."
      }
    ],
    "workflow_state": {
      "workflow_id": "WF-5578EAE8",
      "conversation_id": "SUP-LIVE-RUNTIME",
      "workflow_type": "ORDER_CREATE_WF",
      "owner_agent": "ORDER_AGENT",
      "status": "COMPLETED",
      "current_stage": "COMPLETED",
      "created_at": "2026-09-10T11:02:09.409614+00:00",
      "updated_at": "2026-09-10T11:05:17.068723+00:00",
      "last_question": null,
      "pending_field": null,
      "customer_name": "Demo Customer",
      "email": "customer@example.com",
      "phone": "0400000000",
      "company_name": null,
      "delivery_address": {
        "address_line_1": "1 Example Street",
        "address_line_2": null,
        "city": "Melbourne",
        "state": "VIC",
        "postcode": "3000",
        "country": "Australia"
      },
      "room_size": "5.2m x 4.0m",
      "customer_instructions": null,
      "product_model": "Odyssey",
      "table_size": "8ft",
      "timber": "Tassie Oak",
      "timber_painting": "White",
      "felt_color": "Grey",
      "bracket": "Standard rubber",
      "top_profile": "Waterfall",
      "quantity": 1,
      "missing_customer_fields": [],
      "pending_system_fields": [],
      "conflicting_fields": {},
      "pending_confirmations": [],
      "product_sku": "B8ODYSSEY",
      "room_size_validation_result": "SUITABLE",
      "unit_price": "6050",
      "customisation_price": "800",
      "shipping_method": null,
      "shipping_cost": "530",
      "total_price": "6580",
      "order_snapshot": {
        "product_model": "Odyssey",
        "table_size": "8ft",
        "timber": "Tassie Oak",
        "timber_painting": "White",
        "felt_color": "Grey",
        "bracket": "Standard rubber",
        "top_profile": "Waterfall",
        "quantity": 1
      },
      "final_order_snapshot": {
        "customer_name": "Demo Customer",
        "company_name": null,
        "phone": "0400000000",
        "email": "customer@example.com",
        "delivery_address": {
          "address_line_1": "1 Example Street",
          "address_line_2": null,
          "city": "Melbourne",
          "state": "VIC",
          "postcode": "3000",
          "country": "Australia"
        },
        "customer_instructions": null,
        "product_model": "Odyssey",
        "table_size": "8ft",
        "top_profile": "Waterfall",
        "bracket": "Standard rubber",
        "felt_color": "Grey",
        "timber": "Tassie Oak",
        "timber_painting": "White",
        "room_size": "5.2m x 4.0m",
        "room_size_validation_result": "SUITABLE",
        "quantity": 1,
        "product_sku": "B8ODYSSEY",
        "customisation_price": "800",
        "unit_price": "6050",
        "shipping_cost": "530",
        "total_price": "6580"
      },
      "approved": null,
      "configuration_confirmed": true,
      "final_order_confirmed": true,
      "order_id": "ORD-000001",
      "order_status": "CONFIRMED",
      "failure_reason": null
    }
  },
  "order_id": "ORD-000001",
  "persisted_order_count": 1,
  "observed_artifacts": {
    "configuration": true,
    "provisional_order": true
  },
  "payload_matches_confirmed_snapshot": true
}
```