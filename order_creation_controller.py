"""Deterministic stage execution and bounded re-entry for ORDER_CREATE_WF."""

from pathlib import Path

from order_creation_confirmation import ConfirmationIntent, ConfirmationInterpretation
from order_creation_catalog import (
    CUSTOMIZATION_CATEGORIES, lookup_base_product, lookup_product_option, lookup_product_pricing,
)

from business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from order_creation_order_store import DEFAULT_ORDER_STORE_PATH, ORDER_ID_PATTERN, ORDER_STATUS_CONFIRMED
from order_creation_order_store import create_order
from order_creation_rules import build_configuration_snapshot, validate_room_size, calculate_total_price
from order_creation_rules import PRODUCT_AUTHORIZATION_FIELDS, confirmed_product_configuration_matches
from order_creation_rules import build_final_order_snapshot, final_order_snapshot_matches
from order_creation_shipping import lookup_shipping_rate
from order_creation_updates import ExtractedOrderInformation
from order_creation_state import (
    OrderCreationStage,
    OrderCreationState,
    FinalOrderSnapshot,
    OrderWorkflowStatus,
    determine_missing_fields,
)
from workflow_state import current_utc_time


def execute_order_creation_workflow(
    state: OrderCreationState,
    *,
    confirmation: ConfirmationInterpretation | None = None,
    confirmation_snapshot: dict | None = None,
    final_confirmation: ConfirmationInterpretation | None = None,
    final_confirmation_snapshot: FinalOrderSnapshot | None = None,
    order_store_path: Path = DEFAULT_ORDER_STORE_PATH,
) -> BusinessResult:
    """Run implemented stages until a handler produces a business result.

    Unimplemented stages raise NotImplementedError without undoing progress or
    marking business failure. Cycle detection is local to this execution call.
    """
    if state.status not in {
        OrderWorkflowStatus.ACTIVE,
        OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Execution requires status=ACTIVE or AWAITING_USER_INPUT.")

    if confirmation is not None or confirmation_snapshot is not None:
        if state.current_stage != OrderCreationStage.CONFIGURATION_CONFIRMATION:
            raise ValueError("Confirmation arguments require CONFIGURATION_CONFIRMATION entry.")
        if confirmation is None:
            raise ValueError("confirmation_snapshot requires a confirmation interpretation.")

    if final_confirmation is not None or final_confirmation_snapshot is not None:
        if confirmation is not None or confirmation_snapshot is not None:
            raise ValueError("Configuration and final confirmation arguments cannot be mixed.")
        if state.current_stage != OrderCreationStage.FINAL_CONFIRMATION:
            raise ValueError("Final confirmation arguments require FINAL_CONFIRMATION entry.")
        if final_confirmation is None or final_confirmation_snapshot is None:
            raise ValueError("Final confirmation requires interpretation and snapshot evidence.")

    visited_stages = set()
    while True:
        try:
            stage = OrderCreationStage(state.current_stage)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Unrecognized workflow stage: {state.current_stage!r}") from exc

        if stage not in {
            OrderCreationStage.COLLECT_REQUIREMENTS,
            OrderCreationStage.VALIDATE_CONFIGURATION,
            OrderCreationStage.CONFIGURATION_CONFIRMATION,
            OrderCreationStage.PRICING,
            OrderCreationStage.FINAL_CONFIRMATION,
            OrderCreationStage.CREATE_ORDER,
        }:
            raise NotImplementedError(f"Workflow stage is not implemented: {stage.value}")
        if stage in visited_stages:
            raise RuntimeError(f"Internal workflow cycle at stage: {stage.value}")
        visited_stages.add(stage)

        if stage == OrderCreationStage.COLLECT_REQUIREMENTS:
            result = handle_collect_requirements(state)
        elif stage == OrderCreationStage.VALIDATE_CONFIGURATION:
            result = handle_validate_configuration(state)
        elif stage == OrderCreationStage.PRICING:
            result = handle_pricing(state)
        elif stage == OrderCreationStage.FINAL_CONFIRMATION:
            result = handle_final_confirmation(
                state, confirmation=final_confirmation,
                confirmation_snapshot=final_confirmation_snapshot,
            )
            final_confirmation = None
            final_confirmation_snapshot = None
        elif stage == OrderCreationStage.CREATE_ORDER:
            result = handle_create_order(state, store_path=order_store_path)
        else:
            if confirmation is None and confirmation_snapshot is None:
                result = handle_configuration_confirmation(state)
            else:
                result = handle_configuration_confirmation(
                    state, confirmation=confirmation,
                    confirmation_snapshot=confirmation_snapshot,
                )
                confirmation = None
                confirmation_snapshot = None

        if isinstance(result, BusinessResult):
            return result
        if result is not None:
            raise RuntimeError(f"Unexpected handler return value at stage: {stage.value}")
        if state.current_stage == stage:
            raise RuntimeError(f"Handler returned None without stage advancement: {stage.value}")
        if state.status != OrderWorkflowStatus.ACTIVE:
            raise RuntimeError("Internal continuation requires status=ACTIVE.")


def handle_create_order(
    state: OrderCreationState,
    *,
    store_path: Path = DEFAULT_ORDER_STORE_PATH,
) -> BusinessResult:
    """Commit the exact confirmed final snapshot through the create_order adapter."""
    if state.current_stage != OrderCreationStage.CREATE_ORDER:
        raise ValueError("Create order requires current_stage=CREATE_ORDER.")
    if state.status != OrderWorkflowStatus.ACTIVE:
        raise ValueError("Create order requires status=ACTIVE.")
    if state.final_order_confirmed is not True:
        raise ValueError("Create order requires explicit final authorization.")
    if "final_order_confirmed" in state.pending_confirmations:
        raise ValueError("Create order requires final confirmation to be consumed.")
    if state.final_order_snapshot is None:
        raise ValueError("Create order requires a confirmed final snapshot.")
    snapshot = FinalOrderSnapshot.model_validate(state.final_order_snapshot)
    if not final_order_snapshot_matches(state, snapshot):
        raise ValueError("Create order requires current state to match the confirmed final snapshot.")

    result = create_order(
        workflow_id=state.workflow_id,
        conversation_id=state.conversation_id,
        confirmed_snapshot=snapshot,
        store_path=store_path,
    )
    record = result.record
    if (record.source_workflow_id != state.workflow_id
            or record.conversation_id != state.conversation_id
            or record.order != snapshot
            or ORDER_ID_PATTERN.fullmatch(record.order_id) is None
            or record.order_status != ORDER_STATUS_CONFIRMED):
        raise RuntimeError("create_order returned committed evidence that does not match workflow state.")

    now = current_utc_time()
    state.order_id = record.order_id
    state.order_status = record.order_status
    state.status = OrderWorkflowStatus.COMPLETED
    state.current_stage = OrderCreationStage.COMPLETED
    state.pending_field = None
    state.updated_at = now
    return BusinessResult(
        workflow_id=state.workflow_id,
        source_agent="ORDER_AGENT",
        action="CREATE_ORDER",
        current_stage=OrderCreationStage.COMPLETED.value,
        result_status=BusinessResultStatus.SUCCESS,
        reason=BusinessResultReason.ORDER_CREATED,
        data={
            "order_id": record.order_id,
            "order_status": record.order_status,
            "created": result.created,
        },
        required_input=[],
        error=None,
    )


def handle_final_confirmation(
    state: OrderCreationState,
    *,
    confirmation: ConfirmationInterpretation | None = None,
    confirmation_snapshot: FinalOrderSnapshot | None = None,
) -> BusinessResult | None:
    """Wait or authorize exact priced evidence; never look up or recalculate freight.

    Stored zero shipping is valid MVP free shipping. Corrections are prepared by
    Stage 12D before entry. A stale response never rebuilds and authorizes at once.
    """
    if state.current_stage != OrderCreationStage.FINAL_CONFIRMATION or state.status not in {
        OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Final confirmation requires eligible FINAL_CONFIRMATION entry.")
    if state.final_order_confirmed is not False:
        raise ValueError("Final order is already authorized.")
    if (confirmation is None) != (confirmation_snapshot is None):
        raise ValueError("Final confirmation requires interpretation and snapshot evidence together.")
    candidate = build_final_order_snapshot(state)
    if state.final_order_snapshot is not None:
        FinalOrderSnapshot.model_validate(state.final_order_snapshot)
    if confirmation is not None:
        confirmation = ConfirmationInterpretation.model_validate(confirmation)
        evidence = FinalOrderSnapshot.model_validate(confirmation_snapshot)
        if (state.status != OrderWorkflowStatus.AWAITING_USER_INPUT
                or "final_order_confirmed" not in state.pending_confirmations
                or state.final_order_snapshot is None
                or evidence != state.final_order_snapshot
                or not final_order_snapshot_matches(state, state.final_order_snapshot)):
            raise ValueError("Final confirmation evidence is not pending or is stale.")
        if confirmation.intent == ConfirmationIntent.CONFIRMED:
            now = current_utc_time()
            state.final_order_confirmed = True
            state.pending_confirmations = [f for f in state.pending_confirmations if f != "final_order_confirmed"]
            state.pending_field = None
            state.status = OrderWorkflowStatus.ACTIVE
            state.current_stage = OrderCreationStage.CREATE_ORDER
            state.updated_at = now
            return None

    pending = [f for f in state.pending_confirmations if f != "final_order_confirmed"] + ["final_order_confirmed"]
    changed = (state.final_order_snapshot != candidate
               or state.status != OrderWorkflowStatus.AWAITING_USER_INPUT
               or state.pending_field is not None or state.pending_confirmations != pending)
    if changed:
        now = current_utc_time()
        if state.final_order_snapshot != candidate:
            state.final_order_snapshot = candidate
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.pending_field = None
        state.pending_confirmations = pending
        state.updated_at = now
    data = {"final_order_snapshot": state.final_order_snapshot.model_dump(mode="json")}
    if confirmation is not None:
        data["confirmation_intent"] = confirmation.intent.value
    return BusinessResult(
        workflow_id=state.workflow_id, source_agent="ORDER_AGENT", action="CREATE_ORDER",
        current_stage=state.current_stage.value,
        result_status=BusinessResultStatus.NEEDS_USER_INPUT,
        reason=BusinessResultReason.FINAL_CONFIRMATION_REQUIRED,
        required_input=["final_order_confirmed"], data=data,
    )


def handle_collect_requirements(state: OrderCreationState) -> BusinessResult | None:
    """Update collection tracking and return a user-input result when needed.

    None signals internal continuation to VALIDATE_CONFIGURATION; it is not
    a successful order result. This handler does not execute the next stage.
    Customer wording, conflicts, and other requirement tracking are untouched.
    """
    if state.current_stage != OrderCreationStage.COLLECT_REQUIREMENTS:
        raise ValueError("Handler requires current_stage=COLLECT_REQUIREMENTS.")
    if state.status not in {
        OrderWorkflowStatus.ACTIVE,
        OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Handler requires status=ACTIVE or AWAITING_USER_INPUT.")

    missing = determine_missing_fields(state)
    state.missing_customer_fields = missing

    if missing:
        state.pending_field = missing[0]
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.updated_at = current_utc_time()
        return BusinessResult(
            workflow_id=state.workflow_id,
            source_agent="ORDER_AGENT",
            action="CREATE_ORDER",
            current_stage=OrderCreationStage.COLLECT_REQUIREMENTS.value,
            result_status=BusinessResultStatus.NEEDS_USER_INPUT,
            reason=BusinessResultReason.MISSING_REQUIRED_INFORMATION,
            required_input=[state.pending_field],
            data={"missing_fields": missing.copy()},
            error=None,
        )

    state.pending_field = None
    state.status = OrderWorkflowStatus.ACTIVE
    state.current_stage = OrderCreationStage.VALIDATE_CONFIGURATION
    state.updated_at = current_utc_time()
    return None


def handle_validate_configuration(state: OrderCreationState) -> BusinessResult | None:
    """Validate catalog selections, canonicalize, then evaluate room suitability."""
    if state.current_stage != OrderCreationStage.VALIDATE_CONFIGURATION:
        raise ValueError("Handler requires current_stage=VALIDATE_CONFIGURATION.")
    if state.status not in {
        OrderWorkflowStatus.ACTIVE,
        OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Handler requires status=ACTIVE or AWAITING_USER_INPUT.")

    state.missing_customer_fields = determine_missing_fields(state)
    validation_field = "room_size_validation_result"
    if state.missing_customer_fields:
        state.room_size_validation_result = None
        if validation_field not in state.pending_system_fields:
            state.pending_system_fields.append(validation_field)
        state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS
        return handle_collect_requirements(state)

    base = lookup_base_product(state.product_model, state.table_size)
    correction = None
    canonical = {}
    if base["record"] is None:
        correction = (base["field"], "Table Design Model", base["allowed_values"])
    else:
        canonical = {key: base["record"][key] for key in ("product_model", "table_size")}
        for field, category in CUSTOMIZATION_CATEGORIES.items():
            option = lookup_product_option(category, getattr(state, field))
            if option["record"] is None:
                correction = (field, category, option["allowed_values"])
                break
            canonical[field] = option["record"]["title"]
    if correction is not None:
        field, category, allowed = correction
        state.pending_field = field
        state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
        state.updated_at = current_utc_time()
        return BusinessResult(
            workflow_id=state.workflow_id, source_agent="ORDER_AGENT",
            action="CREATE_ORDER", current_stage=state.current_stage.value,
            result_status=BusinessResultStatus.NEEDS_USER_INPUT,
            reason=BusinessResultReason.UNSUPPORTED_CONFIGURATION_VALUE,
            required_input=[field],
            data={"field": field, "category": category,
                  "supplied_value": getattr(state, field), "allowed_values": allowed.copy()},
        )
    for field, value in canonical.items():
        setattr(state, field, value)

    validation = validate_room_size(state.room_size, state.table_size)
    state.room_size_validation_result = validation["result"]
    if validation["result"] is None:
        if validation_field not in state.pending_system_fields:
            state.pending_system_fields.append(validation_field)
        required_input = validation["required_input"].copy()
        state.pending_field = required_input[0]
        reason = BusinessResultReason.MISSING_REQUIRED_INFORMATION
        data = {
            "missing_fields": state.missing_customer_fields.copy(),
            "input_details": validation["input_details"],
        }
    else:
        state.pending_system_fields = [
            field for field in state.pending_system_fields if field != validation_field
        ]
        if validation["result"] == "SUITABLE":
            state.pending_field = None
            state.status = OrderWorkflowStatus.ACTIVE
            state.current_stage = OrderCreationStage.CONFIGURATION_CONFIRMATION
            state.updated_at = current_utc_time()
            return None
        state.pending_field = "table_size"
        required_input = ["table_size"]
        reason = BusinessResultReason.ROOM_SIZE_UNSUITABLE
        data = {
            "room_size": state.room_size,
            "requested_table_size": state.table_size,
            "suitable_table_sizes": validation["suitable_table_sizes"],
        }

    state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
    state.updated_at = current_utc_time()
    return BusinessResult(
        workflow_id=state.workflow_id,
        source_agent="ORDER_AGENT",
        action="CREATE_ORDER",
        current_stage=state.current_stage.value,
        result_status=BusinessResultStatus.NEEDS_USER_INPUT,
        reason=reason,
        required_input=required_input,
        data=data,
        error=None,
    )


def handle_pricing(state: OrderCreationState) -> BusinessResult | None:
    """Price the confirmed configuration; publish derived values only on success."""
    if state.current_stage != OrderCreationStage.PRICING or state.status != OrderWorkflowStatus.ACTIVE:
        raise ValueError("Pricing requires current_stage=PRICING and status=ACTIVE.")
    if state.configuration_confirmed is not True:
        raise ValueError("Pricing requires configuration confirmation.")
    if not confirmed_product_configuration_matches(state):
        raise ValueError("Pricing requires the unchanged confirmed product configuration.")
    if type(state.quantity) is not int or state.quantity < 1:
        raise ValueError("Pricing requires a positive integer quantity, not bool.")
    postcode = state.delivery_address.postcode
    if not isinstance(postcode, str) or not postcode.strip():
        raise ValueError("Pricing requires a supplied delivery postcode.")

    product = lookup_product_pricing(
        product_model=state.product_model, table_size=state.table_size,
        **{field: getattr(state, field) for field in CUSTOMIZATION_CATEGORIES},
    )
    shipping = lookup_shipping_rate(state.table_size, postcode)
    totals = calculate_total_price(product["unit_price"], shipping["per_table_shipping_rate"], state.quantity)
    completed_fields = {"product_sku", "customisation_price", "unit_price", "shipping_cost", "total_price"}
    pending = [field for field in state.pending_system_fields if field not in completed_fields]
    now = current_utc_time()

    state.product_sku = product["product_sku"]
    state.customisation_price = product["customisation_price"]
    state.unit_price = product["unit_price"]
    state.shipping_cost = totals["shipping_cost"]
    state.total_price = totals["total_price"]
    state.pending_system_fields = pending
    state.current_stage = OrderCreationStage.FINAL_CONFIRMATION
    state.status = OrderWorkflowStatus.ACTIVE
    state.updated_at = now
    return None


def apply_order_creation_reentry(
    previous_state: OrderCreationState,
    updated_state: OrderCreationState,
) -> None:
    """Invalidate only the independent merged Wt on a confirmation-stage change.

    Configuration-stage behavior stays uniform. Final-stage changes use the
    bounded MVP classification below. No handlers or business outcomes here.
    """
    if previous_state.current_stage == OrderCreationStage.FINAL_CONFIRMATION:
        _apply_final_confirmation_reentry(previous_state, updated_state)
        return
    if previous_state.current_stage != OrderCreationStage.CONFIGURATION_CONFIRMATION:
        return
    if previous_state.status not in {
        OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        return
    fields = set(ExtractedOrderInformation.model_fields)
    if previous_state.model_dump(include=fields) == updated_state.model_dump(include=fields):
        return
    updated_state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS
    updated_state.status = OrderWorkflowStatus.ACTIVE
    updated_state.pending_field = None
    updated_state.configuration_confirmed = False
    updated_state.room_size_validation_result = None
    updated_state.order_snapshot = {}
    updated_state.pending_system_fields = [
        field for field in updated_state.pending_system_fields
        if field != "room_size_validation_result"
    ] + ["room_size_validation_result"]
    updated_state.pending_confirmations = [
        field for field in updated_state.pending_confirmations
        if field != "configuration_confirmed"
    ]
    updated_state.updated_at = current_utc_time()


def _apply_final_confirmation_reentry(
    previous_state: OrderCreationState, updated_state: OrderCreationState,
) -> None:
    """Prepare an independent merged Wt; never reopen final-authorized orders."""
    if (previous_state.status not in {OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT}
            or previous_state.final_order_confirmed is not False
            or previous_state.configuration_confirmed is not True):
        return
    fields = set(ExtractedOrderInformation.model_fields)
    if previous_state.model_dump(include=fields) == updated_state.model_dump(include=fields):
        return
    configuration_changed = any(
        getattr(previous_state, field) != getattr(updated_state, field)
        for field in (*PRODUCT_AUTHORIZATION_FIELDS, "room_size")
    )
    pricing_changed = (previous_state.quantity != updated_state.quantity
                       or previous_state.delivery_address.postcode != updated_state.delivery_address.postcode)
    pricing_fields = ("product_sku", "customisation_price", "unit_price", "shipping_cost", "total_price")
    updated_state.status = OrderWorkflowStatus.ACTIVE
    updated_state.pending_field = None
    updated_state.final_order_confirmed = False
    updated_state.pending_confirmations = [
        field for field in updated_state.pending_confirmations if field != "final_order_confirmed"
    ]
    if configuration_changed or pricing_changed:
        for field in pricing_fields:
            setattr(updated_state, field, None)
        required = (("room_size_validation_result",) if configuration_changed else ()) + pricing_fields
        updated_state.pending_system_fields = [
            field for field in updated_state.pending_system_fields if field not in required
        ] + list(required)
    if configuration_changed:
        updated_state.current_stage = OrderCreationStage.COLLECT_REQUIREMENTS
        updated_state.configuration_confirmed = False
        updated_state.order_snapshot = {}
        updated_state.room_size_validation_result = None
        updated_state.pending_confirmations = [
            field for field in updated_state.pending_confirmations if field != "configuration_confirmed"
        ]
    elif pricing_changed:
        updated_state.current_stage = OrderCreationStage.PRICING
    else:
        updated_state.current_stage = OrderCreationStage.FINAL_CONFIRMATION
    updated_state.updated_at = current_utc_time()


def handle_configuration_confirmation(
    state: OrderCreationState,
    *,
    confirmation: ConfirmationInterpretation | None = None,
    confirmation_snapshot: dict | None = None,
) -> BusinessResult | None:
    """Wait or accept the exact pending snapshot; no language interpretation.

    Callers apply re-entry after data changes. Stored suitability is established
    by validation. Acceptance returns None to continue to the pricing boundary.
    """
    if state.current_stage != OrderCreationStage.CONFIGURATION_CONFIRMATION:
        raise ValueError("Handler requires current_stage=CONFIGURATION_CONFIRMATION.")
    if state.status not in {
        OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT,
    }:
        raise ValueError("Handler requires status=ACTIVE or AWAITING_USER_INPUT.")
    if confirmation is None and confirmation_snapshot is not None:
        raise ValueError("confirmation_snapshot requires a confirmation interpretation.")
    if confirmation is not None:
        confirmation = ConfirmationInterpretation.model_validate(confirmation)
        if state.configuration_confirmed:
            raise ValueError("Configuration is already confirmed.")
        if state.status != OrderWorkflowStatus.AWAITING_USER_INPUT:
            raise ValueError("Confirmation response requires AWAITING_USER_INPUT.")
        if "configuration_confirmed" not in state.pending_confirmations:
            raise ValueError("Configuration confirmation is not pending.")
        if (not state.order_snapshot
                or confirmation_snapshot != state.order_snapshot
                or state.order_snapshot != build_configuration_snapshot(state)):
            raise ValueError("Confirmation snapshot is missing or does not match current configuration.")
    if state.configuration_confirmed:
        raise NotImplementedError("Confirmed configuration processing is not implemented.")
    if determine_missing_fields(state) or state.room_size_validation_result != "SUITABLE":
        raise ValueError("Confirmation requires complete customer information and suitable room validation.")
    if confirmation is not None and confirmation.intent == ConfirmationIntent.CONFIRMED:
        state.configuration_confirmed = True
        state.pending_confirmations = [
            field for field in state.pending_confirmations if field != "configuration_confirmed"
        ]
        state.status = OrderWorkflowStatus.ACTIVE
        state.current_stage = OrderCreationStage.PRICING
        state.pending_field = None
        state.updated_at = current_utc_time()
        return None
    if confirmation is None:
        state.order_snapshot = build_configuration_snapshot(state)
    state.status = OrderWorkflowStatus.AWAITING_USER_INPUT
    state.configuration_confirmed = False
    state.pending_field = None
    state.pending_confirmations = [
        field for field in state.pending_confirmations if field != "configuration_confirmed"
    ] + ["configuration_confirmed"]
    state.updated_at = current_utc_time()
    data = {"configuration_snapshot": state.order_snapshot.copy()}
    if confirmation is not None:
        data["confirmation_intent"] = confirmation.intent.value
    return BusinessResult(
        workflow_id=state.workflow_id,
        source_agent="ORDER_AGENT",
        action="CREATE_ORDER",
        current_stage=OrderCreationStage.CONFIGURATION_CONFIRMATION.value,
        result_status=BusinessResultStatus.NEEDS_USER_INPUT,
        reason=BusinessResultReason.CONFIGURATION_CONFIRMATION_REQUIRED,
        data=data,
        required_input=["configuration_confirmed"],
        error=None,
    )
