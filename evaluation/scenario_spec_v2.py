"""Inactive ScenarioSpec v2 contracts. No extraction, business I/O or execution."""
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, model_serializer, model_validator

from evaluation.scenario_spec import (
    CONFIGURATION_FIELDS, ConfigurationField, Contract, CustomerDetails,
    DeliveryAddress, DisclosureField, InvariantExpectations, Nonblank,
    PositiveInt, PricingExpectations, SharedFixtures,
    TargetConfiguration,
)

ScenarioId = Literal['S01', 'S02', 'S03', 'S04', 'S05']
Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class V2Contract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True,
                              revalidate_instances='always', serialize_by_alias=True)


class ConfigurationPhase(str, Enum):
    INITIAL = 'INITIAL'
    FINAL = 'FINAL'


class PricingExpectationType(str, Enum):
    REFERENCE_BASELINE = 'REFERENCE_BASELINE'
    EXECUTED = 'EXECUTED'


class ScenarioSource(V2Contract):
    source_file: Nonblank
    worksheet: Nonblank
    source_sha256: Digest
    source_version: Nonblank
    scenario_column: Annotated[str, StringConstraints(pattern=r'^[A-Z]{1,3}$')]

    @model_validator(mode='after')
    def portable_reference(self) -> Self:
        path = PurePosixPath(self.source_file)
        if (path.is_absolute() or PureWindowsPath(self.source_file).drive
                or '\\' in self.source_file or '..' in path.parts
                or str(path) != self.source_file or self.source_file == '.'):
            raise ValueError('Source must be a canonical repository-relative path')
        column = 0
        for letter in self.scenario_column:
            column = column * 26 + ord(letter) - ord('A') + 1
        if column > 16384:
            raise ValueError('Excel column must not exceed XFD')
        return self


class InitialExperimentState(V2Contract):
    conversation: Literal['EMPTY']
    order_store: Literal['EMPTY_ISOLATED']


class InitialConfigurationOverrides(V2Contract):
    # None is an internal omission sentinel; explicitly supplied null is rejected.
    product_model: Nonblank | None = None
    table_size: Nonblank | None = None
    timber: Nonblank | None = None
    timber_painting: Nonblank | None = None
    felt_color: Nonblank | None = None
    bracket: Nonblank | None = None
    top_profile: Nonblank | None = None
    quantity: PositiveInt | None = None

    @model_serializer(mode='wrap')
    def sparse_serialization(self, handler):
        return {key: value for key, value in handler(self).items()
                if key in self.model_fields_set}

    @model_validator(mode='after')
    def no_explicit_null(self) -> Self:
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError('Explicit null overrides are invalid; omit inherited fields')
        return self


class CustomerGroundTruthV2(V2Contract):
    customer: CustomerDetails
    delivery_address: DeliveryAddress
    room_size: Nonblank
    configuration: TargetConfiguration
    initial_configuration_overrides: InitialConfigurationOverrides = Field(
        default_factory=InitialConfigurationOverrides)

    @model_validator(mode='after')
    def differing_overrides(self) -> Self:
        for field in self.initial_configuration_overrides.model_fields_set:
            if getattr(self.initial_configuration_overrides, field) == getattr(self.configuration, field):
                raise ValueError(f'Override must differ from final target: {field}')
        return self

    def resolve_effective_configuration(self, phase: ConfigurationPhase) -> TargetConfiguration:
        if not isinstance(phase, ConfigurationPhase):
            raise ValueError('Expected ConfigurationPhase')
        values = self.configuration.model_dump()
        if phase == ConfigurationPhase.INITIAL:
            values.update(self.initial_configuration_overrides.model_dump(exclude_unset=True))
        return TargetConfiguration.model_validate(values)


class ResolvedModification(V2Contract):
    field: DisclosureField
    from_value: Nonblank | PositiveInt
    to_value: Nonblank | PositiveInt


class ConfigurationModification(V2Contract):
    field: DisclosureField
    from_reference: Literal['INITIAL_OVERRIDE'] = Field(alias='from')
    to_reference: Literal['FINAL_TARGET'] = Field(alias='to')

    def resolve(self, truth: CustomerGroundTruthV2) -> ResolvedModification:
        if self.field not in truth.initial_configuration_overrides.model_fields_set:
            raise ValueError(f'Modification lacks override: {self.field}')
        old = getattr(truth.initial_configuration_overrides, self.field)
        new = getattr(truth.configuration, self.field)
        if old == new:
            raise ValueError('Modification must change the value')
        return ResolvedModification(field=self.field, from_value=old, to_value=new)


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f'Duplicate {label}')


class AuthoredResponse(V2Contract):
    type: Literal['EXACT_TEXT']
    text: Nonblank


class RequestedInformationPolicy(V2Contract):
    type: Literal['ANSWER_REQUESTED_INFORMATION']


class CatalogDiscoveryPolicy(V2Contract):
    type: Literal['DISCOVER_THEN_SELECT', 'MIXED_DIRECT_AND_DISCOVER']
    discovery_fields: Annotated[tuple[ConfigurationField, ...], Field(min_length=1)]
    if_options_unknown: Literal['ASK_AVAILABLE_OPTIONS']
    selection: Literal['ONLY_AFTER_POSITIVE_PUBLIC_OBSERVATION']
    if_target_not_offered: Literal['ASK_ABOUT_TARGET_OPTION']

    @model_validator(mode='after')
    def distinct_fields(self) -> Self:
        _unique(self.discovery_fields, 'discovery fields')
        return self


class ConfirmConfigurationPolicy(V2Contract):
    """Requires a matching effective public snapshot and explicit review request."""
    type: Literal['CONFIRM_WITHOUT_CHANGE']
    response: AuthoredResponse | None = None


class ModificationPolicy(V2Contract):
    modifications: Annotated[tuple[ConfigurationModification, ...], Field(min_length=1)]
    response: AuthoredResponse | None = None

    @model_validator(mode='after')
    def distinct_modifications(self) -> Self:
        _unique(tuple(m.field for m in self.modifications), 'modification fields')
        return self


class ReviseConfigurationPolicy(ModificationPolicy):
    """Reject an initial public review; no prior customer approval is implied."""
    type: Literal['REJECT_AND_MODIFY_CONFIGURATION']
    revised_confirmation: ConfirmConfigurationPolicy


class RoomRecoveryPolicy(ModificationPolicy):
    """Requires public evidence of room unsuitability for the initial selection."""
    type: Literal['MODIFY_CONFIGURATION']
    trigger: Literal['ROOM_SIZE_UNSUITABLE']


class ConfirmFinalPolicy(V2Contract):
    """Requires matching public order, explicit request and current configuration approval."""
    type: Literal['CONFIRM_WITHOUT_CHANGE']
    response: AuthoredResponse | None = None


class AbandonPurchasePolicy(V2Contract):
    """Customer intent in response to public final review, not production capability."""
    type: Literal['REJECT_AND_ABANDON']
    customer_intent: Literal['ABANDON_PURCHASE']
    response: AuthoredResponse | None = None


class ConversationPolicyV2(V2Contract):
    disclosure: RequestedInformationPolicy
    configuration_selection: CatalogDiscoveryPolicy | None = None
    configuration_confirmation: Annotated[
        ConfirmConfigurationPolicy | ReviseConfigurationPolicy, Field(discriminator='type')]
    validation_failure_response: RoomRecoveryPolicy | None = None
    final_confirmation: Annotated[ConfirmFinalPolicy | AbandonPurchasePolicy,
                                 Field(discriminator='type')]


class CustomerScenarioV2(V2Contract):
    description: Nonblank
    ground_truth: CustomerGroundTruthV2
    initial_message: Nonblank
    initial_disclosures: tuple[DisclosureField, ...]
    initially_known_configuration_fields: tuple[ConfigurationField, ...]
    conversation_policy: ConversationPolicyV2

    @model_validator(mode='after')
    def knowledge_consistency(self) -> Self:
        _unique(self.initial_disclosures, 'disclosures')
        _unique(self.initially_known_configuration_fields, 'known fields')
        known = set(self.initially_known_configuration_fields)
        if not set(self.initial_disclosures) - {'quantity'} <= known:
            raise ValueError('Disclosed configuration must be initially known')
        unknown = set(CONFIGURATION_FIELDS) - known
        discovery = self.conversation_policy.configuration_selection
        if discovery is None:
            if unknown:
                raise ValueError('Unknown fields require discovery')
        else:
            if set(discovery.discovery_fields) != unknown:
                raise ValueError('Discovery fields must equal unknown fields')
            expected = 'MIXED_DIRECT_AND_DISCOVER' if known else 'DISCOVER_THEN_SELECT'
            if discovery.type != expected:
                raise ValueError('Discovery type conflicts with initial knowledge')
            if unknown & self.ground_truth.initial_configuration_overrides.model_fields_set:
                raise ValueError('Discovery selects final targets, not differing initial overrides')
        return self

    @model_validator(mode='after')
    def modification_paths(self) -> Self:
        policy = self.conversation_policy
        revision = policy.configuration_confirmation
        paths = ([revision] if isinstance(revision, ReviseConfigurationPolicy) else [])
        if policy.validation_failure_response:
            paths.append(policy.validation_failure_response)
        fields = []
        for path in paths:
            for modification in path.modifications:
                modification.resolve(self.ground_truth)
                fields.append(modification.field)
        _unique(fields, 'competing modification fields')
        if set(fields) != self.ground_truth.initial_configuration_overrides.model_fields_set:
            raise ValueError('Every initial override requires a modification policy path')
        return self


class PhasePricingExpectation(V2Contract):
    phase: ConfigurationPhase
    expectation_type: PricingExpectationType
    product_sku: Nonblank
    pricing: PricingExpectations


class SystemDerivedExpectationsV2(V2Contract):
    pricing: tuple[PhasePricingExpectation, ...]

    @model_validator(mode='after')
    def unique_phases(self) -> Self:
        _unique(tuple(p.phase for p in self.pricing), 'pricing phases')
        return self


MilestoneId = Literal[
    'REQUIREMENTS', 'INITIAL_VALIDATION', 'FINAL_VALIDATION', 'INITIAL_REVIEW',
    'FINAL_REVIEW', 'CONFIGURATION_CHANGE', 'FINAL_PRICING', 'FINAL_CONFIRMATION',
    'ORDER_CREATION', 'TERMINATION',
]


class RequirementsMilestone(V2Contract):
    kind: Literal['REQUIREMENTS']
    id: Literal['REQUIREMENTS']
    status: Literal['COMPLETE']


class ValidValidation(V2Contract):
    status: Literal['VALID']


class InvalidRoomValidation(V2Contract):
    status: Literal['INVALID']
    reason: Literal['ROOM_SIZE_UNSUITABLE']


class ValidationMilestone(V2Contract):
    kind: Literal['VALIDATION']
    id: Literal['INITIAL_VALIDATION', 'FINAL_VALIDATION']
    phase: ConfigurationPhase
    result: Annotated[ValidValidation | InvalidRoomValidation, Field(discriminator='status')]

    @model_validator(mode='after')
    def phase_id(self) -> Self:
        if self.id != f'{self.phase.value}_VALIDATION':
            raise ValueError('Validation ID must match phase')
        return self


class ConfirmedReview(V2Contract):
    status: Literal['CONFIRMED']


class RejectedReview(V2Contract):
    status: Literal['REJECTED_WITH_MODIFICATION']
    changed_fields: Annotated[tuple[DisclosureField, ...], Field(min_length=1)]

    @model_validator(mode='after')
    def distinct_fields(self) -> Self:
        _unique(self.changed_fields, 'review changed fields')
        return self


class ConfigurationReviewMilestone(V2Contract):
    kind: Literal['CONFIGURATION_REVIEW']
    id: Literal['INITIAL_REVIEW', 'FINAL_REVIEW']
    phase: ConfigurationPhase
    result: Annotated[ConfirmedReview | RejectedReview, Field(discriminator='status')]

    @model_validator(mode='after')
    def phase_id(self) -> Self:
        if self.id != f'{self.phase.value}_REVIEW':
            raise ValueError('Review ID must match phase')
        if isinstance(self.result, RejectedReview) and self.phase != ConfigurationPhase.INITIAL:
            raise ValueError('Modification rejects the initial review')
        return self


class ConfigurationChangeMilestone(V2Contract):
    kind: Literal['CONFIGURATION_CHANGE']
    id: Literal['CONFIGURATION_CHANGE']
    status: Literal['APPLIED']
    changed_fields: Annotated[tuple[DisclosureField, ...], Field(min_length=1)]
    material_change_detected: bool
    price_recalculation_required: bool

    @model_validator(mode='after')
    def distinct_fields(self) -> Self:
        _unique(self.changed_fields, 'changed fields')
        return self


class PricingMilestone(V2Contract):
    kind: Literal['PRICING']
    id: Literal['FINAL_PRICING']
    phase: Literal['FINAL']
    status: Literal['COMPLETED']


class ConfirmedFinal(V2Contract):
    status: Literal['CONFIRMED']


class RejectedFinal(V2Contract):
    status: Literal['REJECTED']
    customer_intent: Literal['ABANDON_PURCHASE']


class FinalConfirmationMilestone(V2Contract):
    kind: Literal['FINAL_CONFIRMATION']
    id: Literal['FINAL_CONFIRMATION']
    result: Annotated[ConfirmedFinal | RejectedFinal, Field(discriminator='status')]


class OrderCreationMilestone(V2Contract):
    kind: Literal['ORDER_CREATION']
    id: Literal['ORDER_CREATION']
    status: Literal['EXECUTED', 'NOT_EXECUTED']


class WorkflowTerminationMilestone(V2Contract):
    kind: Literal['TERMINATION']
    id: Literal['TERMINATION']
    status: Literal['COMPLETED', 'CANCELLED']
    side_effect_allowed: bool


WorkflowMilestone = Annotated[
    RequirementsMilestone | ValidationMilestone | ConfigurationReviewMilestone
    | ConfigurationChangeMilestone | PricingMilestone | FinalConfirmationMilestone
    | OrderCreationMilestone | WorkflowTerminationMilestone, Field(discriminator='kind')]


class MilestonePrecedence(V2Contract):
    before: MilestoneId
    after: MilestoneId


class WorkflowExpectationsV2(V2Contract):
    milestones: tuple[WorkflowMilestone, ...]
    precedence: tuple[MilestonePrecedence, ...] = ()

    @model_validator(mode='after')
    def valid_graph(self) -> Self:
        ids = tuple(m.id for m in self.milestones)
        _unique(ids, 'milestone IDs')
        edges = tuple((e.before, e.after) for e in self.precedence)
        _unique(edges, 'precedence edges')
        pending = {key: set() for key in ids}
        for before, after in edges:
            if before not in pending or after not in pending or before == after:
                raise ValueError('Precedence requires distinct existing milestones')
            pending[after].add(before)
        while pending:
            ready = {key for key, parents in pending.items() if not parents}
            if not ready:
                raise ValueError('Precedence graph is cyclic')
            pending = {key: parents - ready for key, parents in pending.items() if key not in ready}
        return self


class OrderCreatedOutcome(V2Contract):
    result_status: Literal['SUCCESS']
    reason: Literal['ORDER_CREATED']
    workflow_status: Literal['COMPLETED']
    expected_persisted_order_count: Annotated[int, Field(ge=1, le=1)]


class CustomerCancelledOutcome(V2Contract):
    result_status: Literal['TERMINATED']
    reason: Literal['CUSTOMER_CANCELLED']
    workflow_status: Literal['CANCELLED']
    expected_persisted_order_count: Annotated[int, Field(ge=0, le=0)]


OutcomeExpectation = Annotated[OrderCreatedOutcome | CustomerCancelledOutcome,
                               Field(discriminator='result_status')]


class InteractionProperty(str, Enum):
    CATALOG_DISCOVERY_SUPPORTED = 'catalog_discovery_supported'
    TARGET_SELECTED_ONLY_AFTER_OBSERVATION = 'target_selected_only_after_observation'
    ACTIVE_WORKFLOW_GENERAL_ENQUIRY_SUPPORTED = 'active_workflow_general_enquiry_supported'
    WORKFLOW_CONTINUITY_AFTER_ENQUIRY = 'workflow_continuity_after_enquiry'
    COMPLETE_INITIAL_CONFIGURATION_PROVIDED = 'complete_initial_configuration_provided'
    INITIAL_CONFIGURATION_VALID = 'initial_configuration_valid'
    CONFIGURATION_REVISION_SUPPORTED = 'configuration_revision_supported'
    MATERIAL_CHANGE_DETECTED = 'material_change_detected'
    PRIOR_CONFIGURATION_SNAPSHOT_INVALIDATED = 'prior_configuration_snapshot_invalidated'
    CONFIGURATION_REVALIDATED_AFTER_CHANGE = 'configuration_revalidated_after_change'
    PRICE_RECALCULATED_AFTER_MATERIAL_CHANGE = 'price_recalculated_after_material_change'
    REVISED_CONFIGURATION_CONFIRMED = 'revised_configuration_confirmed'
    FINAL_CONFIRMATION_WITHOUT_CHANGE = 'final_confirmation_without_change'
    COMMITTED_ORDER_MATCHES_REVISED_CONFIGURATION = 'committed_order_matches_revised_configuration'


class EvaluationExpectationsV2(V2Contract):
    system_derived: SystemDerivedExpectationsV2
    workflow: WorkflowExpectationsV2
    outcome: OutcomeExpectation
    invariants: InvariantExpectations
    interaction_properties: tuple[InteractionProperty, ...] = ()

    @model_validator(mode='after')
    def pricing_execution(self) -> Self:
        _unique(self.interaction_properties, 'interaction properties')
        executed = {p.phase for p in self.system_derived.pricing
                    if p.expectation_type == PricingExpectationType.EXECUTED}
        milestones = {m.phase for m in self.workflow.milestones if isinstance(m, PricingMilestone)}
        if executed != milestones:
            raise ValueError('Executed pricing and pricing milestones must correspond')
        if ConfigurationPhase.FINAL not in executed:
            raise ValueError('Final executed pricing is required for supported outcomes')
        return self

    @model_validator(mode='after')
    def outcome_consistency(self) -> Self:
        milestones = {m.id: m for m in self.workflow.milestones}
        required = {'FINAL_CONFIRMATION', 'ORDER_CREATION', 'TERMINATION'}
        if not required <= milestones.keys():
            raise ValueError('Outcome requires final confirmation, creation and termination milestones')
        cancelled = isinstance(self.outcome, CustomerCancelledOutcome)
        final = milestones['FINAL_CONFIRMATION'].result
        if isinstance(final, RejectedFinal) != cancelled:
            raise ValueError('Final confirmation contradicts outcome')
        if milestones['ORDER_CREATION'].status != ('NOT_EXECUTED' if cancelled else 'EXECUTED'):
            raise ValueError('Order creation contradicts outcome')
        terminal = milestones['TERMINATION']
        if terminal.status != self.outcome.workflow_status or terminal.side_effect_allowed == cancelled:
            raise ValueError('Termination/side effects contradict outcome')
        return self


class ScenarioSpecV2(V2Contract):
    schema_version: Literal['2']
    scenario_id: ScenarioId
    name: Nonblank
    source: ScenarioSource
    initial_state: InitialExperimentState
    fixtures: SharedFixtures
    customer: CustomerScenarioV2
    evaluation: EvaluationExpectationsV2

    @model_validator(mode='after')
    def policy_outcome(self) -> Self:
        abandon = isinstance(self.customer.conversation_policy.final_confirmation, AbandonPurchasePolicy)
        if abandon != isinstance(self.evaluation.outcome, CustomerCancelledOutcome):
            raise ValueError('Final customer policy contradicts expected outcome')
        return self

    @model_validator(mode='after')
    def policy_milestones(self) -> Self:
        policy = self.customer.conversation_policy
        milestones = {m.id: m for m in self.evaluation.workflow.milestones}
        revision = isinstance(policy.configuration_confirmation, ReviseConfigurationPolicy)
        recovery = policy.validation_failure_response is not None
        if revision and recovery:
            raise ValueError('One initial-to-final transition cannot have two different triggers')
        fields = self.customer.ground_truth.initial_configuration_overrides.model_fields_set
        change = milestones.get('CONFIGURATION_CHANGE')
        if fields:
            if change is None or set(change.changed_fields) != fields:
                raise ValueError('Change milestone must match modification fields')
            validation = milestones.get('FINAL_VALIDATION')
            review = milestones.get('FINAL_REVIEW')
            if (validation is None or not isinstance(validation.result, ValidValidation)
                    or review is None or not isinstance(review.result, ConfirmedReview)):
                raise ValueError('Change requires final valid configuration and confirmed review')
        elif change is not None:
            raise ValueError('Change milestone lacks a customer modification policy')
        initial_review = milestones.get('INITIAL_REVIEW')
        if revision:
            if (initial_review is None or not isinstance(initial_review.result, RejectedReview)
                    or set(initial_review.result.changed_fields) != fields):
                raise ValueError('Revision requires matching initial review rejection')
        elif initial_review and isinstance(initial_review.result, RejectedReview):
            raise ValueError('Rejected review lacks a revision policy')
        initial_validation = milestones.get('INITIAL_VALIDATION')
        if recovery:
            if initial_validation is None or not isinstance(initial_validation.result, InvalidRoomValidation):
                raise ValueError('Recovery requires initial ROOM_SIZE_UNSUITABLE failure')
        elif initial_validation and isinstance(initial_validation.result, InvalidRoomValidation):
            raise ValueError('Initial room failure lacks a recovery policy')
        return self

    @model_validator(mode='after')
    def pricing_arithmetic(self) -> Self:
        for expectation in self.evaluation.system_derived.pricing:
            configuration = self.customer.ground_truth.resolve_effective_configuration(expectation.phase)
            p = expectation.pricing
            if Decimal(p.base_model_price) + Decimal(p.customisation_price) != Decimal(p.unit_price):
                raise ValueError('Unit price must equal base plus customisation')
            if Decimal(p.unit_price) * configuration.quantity + Decimal(p.shipping_cost) != Decimal(p.total_price):
                raise ValueError('Total must use effective phase quantity')
        return self

    def customer_view(self) -> CustomerScenarioV2:
        return self.customer.model_copy(deep=True)
