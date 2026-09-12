"""ScenarioSpec v1: immutable customer policy and separate evaluator expectations."""

from typing import Annotated, Literal, Self
from pathlib import PurePosixPath

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

Nonblank = Annotated[str, StringConstraints(pattern=r"\S")]
Money = Annotated[str, StringConstraints(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
PositiveInt = Annotated[int, Field(gt=0)]
ConfigurationField = Literal[
    "product_model", "table_size", "timber", "timber_painting",
    "felt_color", "bracket", "top_profile",
]
DisclosureField = Literal[
    "product_model", "table_size", "timber", "timber_painting",
    "felt_color", "bracket", "top_profile", "quantity",
]
CONFIGURATION_FIELDS = (
    "product_model", "table_size", "timber", "timber_painting",
    "felt_color", "bracket", "top_profile",
)
InvariantStatus = Literal["SATISFIED", "NOT_EXERCISED"]


def _strict_bool(value):
    if type(value) is not bool:
        raise ValueError("Expected a JSON boolean")
    return value


TrueOnly = Annotated[Literal[True], BeforeValidator(_strict_bool)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CustomerDetails(Contract):
    customer_name: Nonblank
    phone: Nonblank
    email: Nonblank
    company_name: Nonblank | None = None
    customer_instructions: Nonblank | None = None


class DeliveryAddress(Contract):
    address: Nonblank
    city: Nonblank
    state: Nonblank
    postcode: Nonblank
    country: Nonblank


class TargetConfiguration(Contract):
    product_model: Nonblank
    table_size: Nonblank
    timber: Nonblank
    timber_painting: Nonblank
    felt_color: Nonblank
    bracket: Nonblank
    top_profile: Nonblank
    quantity: PositiveInt


class CustomerGroundTruth(Contract):
    customer: CustomerDetails
    delivery_address: DeliveryAddress
    room_size: Nonblank
    configuration: TargetConfiguration


class SelectionConstraints(Contract):
    select_target_only_if_offered: TrueOnly
    if_target_not_offered: Literal["ASK_ABOUT_TARGET_OPTION"]


class DiscoveryPolicy(Contract):
    type: Literal["MIXED_DIRECT_AND_DISCOVER", "DISCOVER_THEN_SELECT"]
    selection_constraints: SelectionConstraints
    discovery_fields: tuple[ConfigurationField, ...]
    if_options_unknown: Literal["ASK_AVAILABLE_OPTIONS"]

    @model_validator(mode="after")
    def distinct_fields(self) -> Self:
        if not self.discovery_fields or len(set(self.discovery_fields)) != len(self.discovery_fields):
            raise ValueError("Discovery fields must be nonempty and unique")
        return self


class ConfirmationPolicy(Contract):
    """Confirmation requires the matching public artifact AND an explicit request.

    These are v1 policy semantics, never a scheduled YES or executable behavior.
    """
    behavior: Literal["CONFIRM_WITHOUT_CHANGE"]
    requires_visible_artifact: TrueOnly
    requires_explicit_confirmation_request: TrueOnly
    requires_artifact_matches_ground_truth: TrueOnly


class ConversationPolicy(Contract):
    configuration_selection: DiscoveryPolicy | None = None
    subsequent_disclosure: Literal["ANSWER_REQUESTED_INFORMATION"] | None = None
    customer_information_when_requested: Literal["PROVIDE_REQUESTED_INFORMATION"] | None = None
    room_information_when_requested: Literal["PROVIDE_GROUND_TRUTH_ROOM_SIZE"] | None = None
    configuration_confirmation: ConfirmationPolicy
    final_confirmation: ConfirmationPolicy


class CustomerScenario(Contract):
    product_knowledge: Literal["INFORMED", "PARTIAL", "NOVICE"]
    description: Nonblank
    ground_truth: CustomerGroundTruth
    initial_message: Nonblank
    initial_disclosures: tuple[DisclosureField, ...]
    initially_known_configuration_fields: tuple[ConfigurationField, ...]
    conversation_policy: ConversationPolicy

    @model_validator(mode="after")
    def knowledge_and_disclosure(self) -> Self:
        known = set(self.initially_known_configuration_fields)
        disclosed = set(self.initial_disclosures) - {"quantity"}
        for fields in (self.initial_disclosures, self.initially_known_configuration_fields):
            if len(fields) != len(set(fields)):
                raise ValueError("Duplicate field identifier")
        if not disclosed <= known:
            raise ValueError("Initially disclosed selections must be known")
        all_fields = set(CONFIGURATION_FIELDS)
        if self.product_knowledge == "INFORMED" and known != all_fields:
            raise ValueError("INFORMED requires complete configuration knowledge")
        if self.product_knowledge == "PARTIAL" and not (0 < len(known) < len(all_fields)):
            raise ValueError("PARTIAL requires some but not all configuration knowledge")
        if self.product_knowledge == "NOVICE" and known:
            raise ValueError("NOVICE has no initial configuration knowledge")
        discovery = self.conversation_policy.configuration_selection
        unknown = all_fields - known
        if discovery is None:
            if unknown:
                raise ValueError("Unknown selections require discovery policy")
        else:
            if set(discovery.discovery_fields) != unknown:
                raise ValueError("Discovery fields must equal initially unknown fields")
            expected = "MIXED_DIRECT_AND_DISCOVER" if known else "DISCOVER_THEN_SELECT"
            if discovery.type != expected:
                raise ValueError("Discovery type conflicts with initial knowledge")
        return self


class InitialStateSpec(Contract):
    """Null denotes no active workflow/order, never serialized controller state."""
    workflow_state: None
    existing_order: None


class FileReference(Contract):
    path: Nonblank
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def relative_path(self) -> Self:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path or str(path) != self.path or self.path == ".":
            raise ValueError("Expected canonical repository-relative file path")
        return self


class SharedFixtures(Contract):
    product_catalog: FileReference
    shipping: FileReference

    @model_validator(mode="after")
    def shared_paths(self) -> Self:
        if self.product_catalog.path != "data/product_prices.json" or self.shipping.path != "data/shipping_rates.json":
            raise ValueError("ScenarioSpec v1 references only shared catalog and shipping data")
        return self


class PricingExpectations(Contract):
    base_model_price: Money
    customisation_price: Money
    unit_price: Money
    shipping_cost: Money
    total_price: Money


class SystemDerivedExpectations(Contract):
    product_sku: Nonblank
    pricing: PricingExpectations


class WorkflowExpectations(Contract):
    configuration_validation: Literal["VALID"]
    configuration_confirmation: Literal["CONFIRMED"]
    final_confirmation: Literal["CONFIRMED"]


class OutcomeExpectations(Contract):
    result_status: Literal["SUCCESS"]
    reason: Literal["ORDER_CREATED"]
    workflow_status: Literal["COMPLETED"]
    committed_order_count: Annotated[int, Field(ge=0)]


class InvariantExpectations(Contract):
    I1: InvariantStatus
    I2: InvariantStatus
    I3: InvariantStatus
    I4: InvariantStatus
    I5: InvariantStatus
    I6: InvariantStatus
    I7: InvariantStatus
    I8: InvariantStatus


class InteractionExpectations(Contract):
    direct_selection_preserved: bool | None = None
    catalog_discovery_supported: bool | None = None
    target_selected_only_after_observation: bool | None = None
    active_workflow_general_enquiry_supported: bool | None = None
    workflow_continuity_after_enquiry: bool | None = None


class EvaluationExpectations(Contract):
    system_derived: SystemDerivedExpectations
    workflow: WorkflowExpectations
    outcome: OutcomeExpectations
    invariants: InvariantExpectations
    interaction_properties: InteractionExpectations | None = None


class ScenarioSpec(Contract):
    schema_version: Literal["1"]
    scenario_id: Annotated[str, StringConstraints(pattern=r"^S[0-9]{2}$")]
    name: Nonblank
    customer: CustomerScenario
    initial_state: InitialStateSpec
    fixtures: SharedFixtures
    evaluation: EvaluationExpectations

    @model_validator(mode="after")
    def price_arithmetic(self) -> Self:
        from decimal import Decimal
        p = self.evaluation.system_derived.pricing
        if Decimal(p.base_model_price) + Decimal(p.customisation_price) != Decimal(p.unit_price):
            raise ValueError("Unit price differs from base plus customisation")
        if Decimal(p.unit_price) * self.customer.ground_truth.configuration.quantity + Decimal(p.shipping_cost) != Decimal(p.total_price):
            raise ValueError("Total differs from quantity times unit price plus shipping")
        if self.evaluation.outcome.committed_order_count != 1:
            raise ValueError("ORDER_CREATED expects exactly one committed order")
        return self

    def customer_view(self) -> CustomerScenario:
        """Independent immutable customer projection, containing no evaluator data."""
        return self.customer.model_copy(deep=True)
