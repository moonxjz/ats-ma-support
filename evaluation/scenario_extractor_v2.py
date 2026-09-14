"""Frozen workbook -> inactive v2 candidates; no business lookups or workflow calls."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from evaluation.scenario_spec import CONFIGURATION_FIELDS
from evaluation.scenario_spec_v2 import ScenarioSpecV2

EXTRACTOR_VERSION = 'v2-b-1'
SOURCE_FILE = 'evaluation/scenario_sources/Conversation Scenario-CSimulator.xlsx'
SOURCE_SHA256 = '476c83d6b597decd258292d43e83314156a5b71cfd1f45560a37a6beb6578128'
SOURCE_VERSION = 'Authoritative Conversation Scenario Source v1'
WORKSHEET = 'Conversation Profiles'
ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIRECTORY = ROOT / 'evaluation/scenarios_v2_candidates'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
# Approved shared-fixture references, not extracted customer facts or business checks.
FIXTURES = {
    'product_catalog': {'path': 'data/product_prices.json', 'sha256': 'ef8ac04e8ac00a6690f60ecd53b4418fbc4bdd3ccc2d527e65cdb2afdb9cc007'},
    'shipping': {'path': 'data/shipping_rates.json', 'sha256': '933d9ef7ac2910e74642f874e30a78be97935a48a19defe7f3d6f9ec72650cb3'},
}
PRICE_FIELDS = ('base_model_price', 'customisation_price', 'unit_price', 'shipping_cost', 'total_price')
ADDRESS_FIELDS = ('address', 'city', 'state', 'postcode', 'country')
INTERACTIONS_DISCOVERY = ('catalog_discovery_supported', 'target_selected_only_after_observation',
    'active_workflow_general_enquiry_supported', 'workflow_continuity_after_enquiry')
INTERACTIONS_REVISION = ('complete_initial_configuration_provided', 'initial_configuration_valid',
    'configuration_revision_supported', 'material_change_detected', 'prior_configuration_snapshot_invalidated',
    'configuration_revalidated_after_change', 'price_recalculated_after_material_change',
    'revised_configuration_confirmed', 'final_confirmation_without_change', 'committed_order_matches_revised_configuration')


class SourceFidelityError(ValueError):
    """Frozen source, explicit mapping or candidate projection disagrees."""


@dataclass(frozen=True)
class ColumnLayout:
    scenario_id: str
    column: str
    selection: str
    configuration_policy: int
    final_policy: int
    outcome: int
    invariants: int
    system_heading: int
    sku: int
    final_price: int
    workflow_heading: int
    validation: int
    review: int
    final_review: int
    # Optional blocks are explicit, never discovered by scenario-ID branching.
    override_field: str | None = None
    initial_price: int | None = None
    revised_policy: int | None = None
    recovery_policy: int | None = None
    initial_review: int | None = None
    change: int | None = None
    revised_validation: int | None = None
    requirements: int | None = None
    execution: int | None = None
    termination: int | None = None
    workflow_price: int | None = None
    interaction_heading: int | None = None
    interaction_start: int | None = None
    interaction_fields: tuple[str, ...] = ()
    configuration_response: bool = False
    final_response: bool = False
    unchanged_review_flag: bool = False


# All indices are source row numbers. Shared rows: overview 1, identity 2-3,
# description 4-5, truth 6-29, overrides 30, policy/initial speech 31-33.
LAYOUTS = (
    ColumnLayout('S01', 'B', 'DIRECT', 49, 52, 77, 84, 55, 57, 59, 66, 68, 71, 74),
    ColumnLayout('S02', 'C', 'DISCOVERY', 84, 87, 112, 118, 90, 92, 94, 101, 103, 106, 109,
                 interaction_heading=129, interaction_start=130, interaction_fields=INTERACTIONS_DISCOVERY),
    ColumnLayout('S03', 'D', 'DIRECT', 49, 61, 103, 110, 64, 65, 73, 80, 82, 97, 100,
                 override_field='bracket', initial_price=66, revised_policy=58, initial_review=85,
                 change=88, revised_validation=94, interaction_heading=120, interaction_start=122,
                 interaction_fields=INTERACTIONS_REVISION),
    ColumnLayout('S04', 'E', 'DIRECT', 49, 54, 97, 105, 59, 60, 61, 68, 72, 75, 85,
                 requirements=69, execution=89, termination=92, workflow_price=79,
                 configuration_response=True, final_response=True, unchanged_review_flag=True),
    ColumnLayout('S05', 'F', 'DIRECT', 59, 64, 106, 114, 68, 69, 70, 76, 77, 89, 96,
                 override_field='table_size', recovery_policy=49, change=81, revised_validation=86,
                 execution=99, termination=102, configuration_response=True, final_response=True,
                 unchanged_review_flag=True),
)


@dataclass(frozen=True)
class CellUse:
    cell: str
    destination: str


@dataclass(frozen=True)
class Extraction:
    scenario: ScenarioSpecV2
    consumed: tuple[CellUse, ...]
    blank_cells: tuple[str, ...]


def _read_archive(raw: bytes) -> dict[str, str]:
    """Read cell text only. Hash pinning is enforced by the public entry point."""
    try:
        with ZipFile(BytesIO(raw)) as archive:
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
            sheets = workbook.findall('s:sheets/s:sheet', NS)
            if len(sheets) != 1 or sheets[0].get('name') != WORKSHEET:
                raise SourceFidelityError('Expected the single Conversation Profiles worksheet')
            relation_id = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
            matches = [r for r in relationships if r.get('Id') == relation_id]
            if len(matches) != 1 or matches[0].get('TargetMode') == 'External':
                raise SourceFidelityError('Invalid worksheet relationship')
            target = matches[0].get('Target', '')
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in PurePosixPath(member).parts:
                raise SourceFidelityError('Worksheet path traversal')
            strings = [''.join(t.text or '' for t in item.findall('.//s:t', NS))
                       for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            cells = {}
            for cell in ET.fromstring(archive.read(member)).findall('.//s:sheetData/s:row/s:c', NS):
                address = cell.get('r')
                if address in cells or cell.find('s:f', NS) is not None:
                    raise SourceFidelityError(f'Duplicate cell or unexpected formula: {address}')
                value = cell.find('s:v', NS)
                if cell.get('t') == 's':
                    text = strings[int(value.text)]
                elif cell.get('t') == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.findall('s:is//s:t', NS))
                elif value is None:
                    text = ''
                else:
                    raise SourceFidelityError(f'Unexpected non-text source cell: {address}')
                cells[address] = text
            return cells
    except SourceFidelityError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, ET.ParseError, BadZipFile, OSError, IndexError) as exc:
        raise SourceFidelityError(f'Cannot read frozen workbook: {exc}') from exc


class ColumnReader:
    def __init__(self, cells, layout):
        self.cells, self.layout, self.uses = cells, layout, {}

    def take(self, row, destination):
        address = f'{self.layout.column}{row}'
        value = self.cells.get(address, '')
        if not value or not value.strip():
            raise SourceFidelityError(f'{address}: required cell is blank')
        if address in self.uses:
            raise SourceFidelityError(f'{address}: cell consumed twice')
        self.uses[address] = destination
        return value

    def heading(self, row, label):
        text = self.take(row, f'structure:{label}')
        if text.strip() != label:
            raise SourceFidelityError(f'{self.layout.column}{row}: expected heading {label!r}')

    def scalar(self, row, label, destination):
        text = self.take(row, destination)
        match = re.fullmatch(r' *' + re.escape(label) + r': (\S.*)', text)
        if not match:
            raise SourceFidelityError(f'{self.layout.column}{row}: expected scalar label {label}')
        value = match[1]
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except ValueError as exc:
                raise SourceFidelityError(f'Invalid quoted scalar at {self.layout.column}{row}') from exc
            if not isinstance(decoded, str):
                raise SourceFidelityError('Expected quoted text')
            return decoded
        return value

    def expect(self, row, label, value, destination):
        actual = self.scalar(row, label, destination)
        if actual != value:
            raise SourceFidelityError(f'{self.layout.column}{row}: expected {value!r}, got {actual!r}')
        return actual

    def item(self, row, expected, destination):
        text = self.take(row, destination)
        if text.strip() != '- ' + expected:
            raise SourceFidelityError(f'{self.layout.column}{row}: expected list field {expected}')
        return expected

    def response(self, row, destination):
        self.heading(row, 'response: >')
        return {'type': 'EXACT_TEXT', 'text': self.take(row + 1, destination)}


def _truth(reader):
    for row, label in ((4, 'description:'), (6, 'customer_ground_truth:'), (7, 'customer:'),
                       (12, 'delivery_address:'), (21, 'configuration:'), (31, 'conversation_policy:'),
                       (32, 'initial_message: >')):
        reader.heading(row, label)
    details = {key: reader.scalar(row, key, 'customer.ground_truth.customer.' + key)
               for row, key in enumerate(('customer_name', 'phone', 'email'), 8)}
    address = {key: reader.scalar(row, key, 'customer.ground_truth.delivery_address.' + key)
               for row, key in enumerate(ADDRESS_FIELDS, 13)}
    configuration = {key: reader.scalar(row, key, 'customer.ground_truth.configuration.' + key)
                     for row, key in enumerate((*CONFIGURATION_FIELDS, 'quantity'), 22)}
    if not re.fullmatch(r'[1-9][0-9]*', configuration['quantity']):
        raise SourceFidelityError('Invalid quantity spelling')
    configuration['quantity'] = int(configuration['quantity'])
    overrides = {}
    if reader.layout.override_field:
        field = reader.layout.override_field
        text = reader.take(30, 'customer.ground_truth.initial_configuration_overrides.' + field)
        match = re.fullmatch(r'  initial_configuration_overrides:\n    ' + field + r': (\S[^\n]*)', text)
        if not match:
            raise SourceFidelityError('Malformed structured override block')
        overrides[field] = match[1]
    return dict(customer=details, delivery_address=address,
                room_size=reader.scalar(19, 'room_size', 'customer.ground_truth.room_size'),
                configuration=configuration, initial_configuration_overrides=overrides)


def _selection(reader, truth):
    if reader.layout.selection == 'DIRECT':
        reader.heading(35, 'disclosure_policy:'); reader.heading(36, 'initial:')
        disclosures = [reader.item(row, key, 'customer.initial_disclosures')
                       for row, key in enumerate((*CONFIGURATION_FIELDS, 'quantity'), 37)]
        reader.heading(46, 'subsequent:'); reader.heading(47, 'ANSWER_REQUESTED_INFORMATION')
        # The complete-knowledge overview and disclosure list jointly establish known fields.
        return disclosures, disclosures[:-1], None
    reader.heading(35, 'configuration_selection_behavior:')
    kind = reader.expect(36, 'type', 'DISCOVER_THEN_SELECT', 'customer.policy.discovery.type')
    reader.heading(38, 'selection_constraints:')
    reader.expect(39, 'select_target_only_if_offered', 'true', 'customer.policy.discovery.selection')
    fallback = reader.expect(40, 'if_target_not_offered', 'ASK_ABOUT_TARGET_OPTION', 'customer.policy.discovery.fallback')
    reader.heading(42, 'fields:')
    for row, field in zip((43, 48, 53, 58, 63, 68, 73), CONFIGURATION_FIELDS):
        reader.heading(row, field + ':')
        reader.expect(row + 1, 'initial_knowledge', 'UNKNOWN', 'customer.initially_known_configuration_fields')
        reader.expect(row + 2, 'if_options_unknown', 'ASK_AVAILABLE_OPTIONS', 'customer.policy.discovery.request')
        reader.expect(row + 3, 'target_selection', truth['configuration'][field], 'cross-check:final_target.' + field)
    reader.heading(78, 'customer_information_behavior:')
    reader.expect(79, 'when_requested', 'PROVIDE_REQUESTED_INFORMATION', 'customer.policy.disclosure')
    reader.heading(81, 'room_information_behavior:')
    reader.expect(82, 'when_requested', 'PROVIDE_GROUND_TRUTH_ROOM_SIZE', 'customer.policy.disclosure')
    return [], [], dict(type=kind, discovery_fields=list(CONFIGURATION_FIELDS),
        if_options_unknown='ASK_AVAILABLE_OPTIONS', selection='ONLY_AFTER_POSITIVE_PUBLIC_OBSERVATION',
        if_target_not_offered=fallback)


def _modification(reader, row, truth):
    reader.heading(row, 'modification:')
    field = reader.scalar(row + 1, 'field', 'customer.policy.modifications.field')
    if field not in truth['initial_configuration_overrides']:
        raise SourceFidelityError('Modification has no corresponding override')
    reader.expect(row + 2, 'from', truth['initial_configuration_overrides'][field], 'cross-check:INITIAL_OVERRIDE')
    reader.expect(row + 3, 'to', truth['configuration'][field], 'cross-check:FINAL_TARGET')
    return [{'field': field, 'from': 'INITIAL_OVERRIDE', 'to': 'FINAL_TARGET'}]


def _confirm(reader, row, label, authored):
    reader.heading(row, label + ':')
    if authored:
        action = reader.scalar(row + 1, 'action', 'customer.policy.' + label)
        response = reader.response(row + 2, 'customer.policy.' + label + '.response')
        return dict(type=action, response=response)
    reader.heading(row + 1, 'CONFIRM_WITHOUT_CHANGE')
    return {'type': 'CONFIRM_WITHOUT_CHANGE'}


def _policies(reader, truth):
    layout = reader.layout
    disclosures, known, discovery = _selection(reader, truth)
    if layout.revised_policy:
        row = layout.configuration_policy
        reader.heading(row, 'configuration_confirmation:')
        kind = reader.expect(row + 1, 'type', 'REJECT_AND_MODIFY_CONFIGURATION', 'customer.policy.configuration_confirmation.type')
        confirmation = dict(type=kind, modifications=_modification(reader, row + 2, truth),
            response=reader.response(row + 6, 'customer.policy.configuration_confirmation.response'),
            revised_confirmation=_confirm(reader, layout.revised_policy, 'revised_configuration_confirmation', False))
    else:
        confirmation = _confirm(reader, layout.configuration_policy, 'configuration_confirmation', layout.configuration_response)
    recovery = None
    if layout.recovery_policy:
        row = layout.recovery_policy
        reader.heading(row, 'validation_failure_response:')
        trigger = reader.expect(row + 1, 'trigger', 'ROOM_SIZE_UNSUITABLE', 'customer.policy.recovery.trigger')
        action = reader.expect(row + 2, 'action', 'MODIFY_CONFIGURATION', 'customer.policy.recovery.type')
        recovery = dict(type=action, trigger=trigger, modifications=_modification(reader, row + 3, truth),
                        response=reader.response(row + 7, 'customer.policy.recovery.response'))
    final = _confirm(reader, layout.final_policy, 'final_confirmation', layout.final_response)
    if final['type'] == 'REJECT_AND_ABANDON':
        # Intent is cross-checked against the authored final-review expectation below.
        final['customer_intent'] = 'ABANDON_PURCHASE'
    return disclosures, known, dict(disclosure={'type': 'ANSWER_REQUESTED_INFORMATION'},
        configuration_selection=discovery, configuration_confirmation=confirmation,
        validation_failure_response=recovery, final_confirmation=final)


def _prices(reader):
    layout = reader.layout
    reader.heading(layout.system_heading, 'expected_system_derived:')
    sku = reader.scalar(layout.sku, 'product_sku', 'evaluation.system_derived.pricing.product_sku')
    phases = []
    blocks = [(layout.final_price, 'FINAL', 'EXECUTED', 'pricing:')]
    if layout.initial_price:
        blocks = [(layout.initial_price, 'INITIAL', 'REFERENCE_BASELINE', 'pricing_before_configuration_change:'),
                  (layout.final_price, 'FINAL', 'EXECUTED', 'pricing_after_configuration_change:')]
    for row, phase, kind, heading in blocks:
        reader.heading(row, heading)
        values = {field: reader.scalar(row + offset, field, f'evaluation.pricing.{phase}.{field}')
                  for offset, field in enumerate(PRICE_FIELDS, 1)}
        phases.append(dict(phase=phase, expectation_type=kind, product_sku=sku, pricing=values))
    return phases


def _outcome(reader):
    row = reader.layout.outcome
    reader.heading(row, 'expected_outcome:')
    # The discovery column has no separator between heading and values.
    start = row + (1 if reader.layout.selection == 'DISCOVERY' else 2)
    outcome = {key: reader.scalar(start + offset, key, 'evaluation.outcome.' + key)
               for offset, key in enumerate(('result_status', 'reason', 'workflow_status', 'committed_order_count'))}
    count = outcome.pop('committed_order_count')
    if count not in ('0', '1'):
        raise SourceFidelityError('Expected zero or one authored order')
    outcome['expected_persisted_order_count'] = int(count)
    return outcome


def _workflow(reader, truth, policy, prices, outcome):
    layout = reader.layout
    reader.heading(layout.workflow_heading, 'expected_workflow:')
    milestones = []
    def add(kind, identifier, **payload):
        milestones.append(dict(kind=kind, id=identifier, **payload))
    def validation(row, label, phase):
        reader.heading(row, label + ':')
        status = reader.scalar(row + 1, 'expected', f'evaluation.workflow.{phase}_VALIDATION')
        result = {'status': status}
        if status == 'INVALID':
            result['reason'] = reader.expect(row + 2, 'reason', 'ROOM_SIZE_UNSUITABLE', 'evaluation.workflow.validation.reason')
        add('VALIDATION', phase + '_VALIDATION', phase=phase, result=result)
    if layout.requirements:
        reader.heading(layout.requirements, 'collect_requirements:')
        status = reader.expect(layout.requirements + 1, 'expected', 'COMPLETE', 'evaluation.workflow.REQUIREMENTS')
        add('REQUIREMENTS', 'REQUIREMENTS', status=status)
    changed = bool(truth['initial_configuration_overrides'])
    validation(layout.validation, 'initial_configuration_validation' if changed else 'configuration_validation',
               'INITIAL' if changed else 'FINAL')
    if layout.initial_review:
        reader.heading(layout.initial_review, 'initial_configuration_confirmation:')
        status = reader.expect(layout.initial_review + 1, 'expected', 'REJECTED_WITH_MODIFICATION', 'evaluation.workflow.INITIAL_REVIEW')
        add('CONFIGURATION_REVIEW', 'INITIAL_REVIEW', phase='INITIAL',
            result=dict(status=status, changed_fields=list(truth['initial_configuration_overrides'])))
    if layout.change:
        row = layout.change
        recovery = policy['validation_failure_response'] is not None
        reader.heading(row, 'validation_recovery:' if recovery else 'configuration_change:')
        reader.expect(row + 1, 'expected', 'CONFIGURATION_MODIFIED' if recovery else 'APPLIED', 'evaluation.workflow.CONFIGURATION_CHANGE')
        reader.heading(row + 2, 'changed_fields:')
        field = next(iter(truth['initial_configuration_overrides']))
        fields = [reader.item(row + 3, field, 'evaluation.workflow.CONFIGURATION_CHANGE.changed_fields')]
        if not recovery:
            reader.expect(row + 4, 'price_recalculation_required', 'true', 'evaluation.workflow.CONFIGURATION_CHANGE.price_recalculation_required')
        # Changed product fields are material; final executed pricing applies to the revised target.
        add('CONFIGURATION_CHANGE', 'CONFIGURATION_CHANGE', status='APPLIED', changed_fields=fields,
            material_change_detected=True, price_recalculation_required=True)
        validation(layout.revised_validation, 'revised_configuration_validation', 'FINAL')
    reader.heading(layout.review, 'revised_configuration_confirmation:' if layout.revised_policy else 'configuration_confirmation:')
    status = reader.expect(layout.review + 1, 'expected', 'CONFIRMED', 'evaluation.workflow.FINAL_REVIEW')
    add('CONFIGURATION_REVIEW', 'FINAL_REVIEW', phase='FINAL', result={'status': status})
    if layout.unchanged_review_flag:
        reader.expect(layout.review + 2, 'configuration_changed', 'false', 'cross-check:unchanged_final_review')
    if layout.workflow_price:
        row = layout.workflow_price
        reader.heading(row, 'price_calculation:')
        reader.expect(row + 1, 'expected', 'COMPLETED', 'evaluation.workflow.FINAL_PRICING')
        final_price = prices[-1]['pricing']
        for offset, field in enumerate(('unit_price', 'shipping_cost', 'total_price'), 2):
            reader.expect(row + offset, 'expected_' + field, final_price[field], 'cross-check:FINAL.pricing.' + field)
    # Approved V2-B mapping: final authored pricing is executed; initial reference is not.
    add('PRICING', 'FINAL_PRICING', phase='FINAL', status='COMPLETED')
    reader.heading(layout.final_review, 'final_confirmation:')
    status = reader.scalar(layout.final_review + 1, 'expected', 'evaluation.workflow.FINAL_CONFIRMATION')
    result = {'status': status}
    cancelled = outcome['result_status'] == 'TERMINATED'
    if cancelled:
        result['customer_intent'] = reader.expect(layout.final_review + 2, 'customer_intent',
            policy['final_confirmation']['customer_intent'], 'evaluation.workflow.FINAL_CONFIRMATION.customer_intent')
    add('FINAL_CONFIRMATION', 'FINAL_CONFIRMATION', result=result)
    execution = 'NOT_EXECUTED' if cancelled else 'EXECUTED'
    if layout.execution:
        reader.heading(layout.execution, 'order_creation:')
        reader.expect(layout.execution + 1, 'expected' if cancelled else 'status', execution, 'evaluation.workflow.ORDER_CREATION')
    add('ORDER_CREATION', 'ORDER_CREATION', status=execution)
    if layout.termination:
        reader.heading(layout.termination, 'workflow_termination:')
        reader.expect(layout.termination + 1, 'expected', outcome['workflow_status'], 'evaluation.workflow.TERMINATION')
        if cancelled:
            reader.expect(layout.termination + 2, 'side_effect_allowed', 'false', 'evaluation.workflow.TERMINATION.side_effect_allowed')
    add('TERMINATION', 'TERMINATION', status=outcome['workflow_status'], side_effect_allowed=not cancelled)
    # Source milestones plus approved chronology; no invented initial pricing event.
    ids = [m['id'] for m in milestones]
    precedence = [{'before': before, 'after': after} for before, after in zip(ids, ids[1:])]
    return dict(milestones=milestones, precedence=precedence)


def extract_cells(cells: dict[str, str]) -> tuple[Extraction, ...]:
    """Structural fidelity layer for decoded cells; public extraction also pins bytes."""
    extra = [cell for cell, value in cells.items() if value and not re.fullmatch(r'[B-F](?:[1-9][0-9]?|1[0-2][0-9]|13[0-3])', cell)]
    if extra:
        raise SourceFidelityError(f'Unexpected populated cells outside source region: {extra}')
    results = []
    for layout in LAYOUTS:
        reader = ColumnReader(cells, layout)
        overview = reader.take(1, 'cross-check:overview/customer-policy/workflow')
        if sha256(overview.encode()).hexdigest() != OVERVIEW_HASHES[layout.column]:
            raise SourceFidelityError(f'{layout.column}1: frozen overview changed')
        identifier = reader.expect(2, 'scenario_id', layout.scenario_id, 'scenario_id')
        name = reader.scalar(3, 'name', 'name')
        description = reader.take(5, 'customer.description')
        truth = _truth(reader)
        initial_message = reader.take(33, 'customer.initial_message')
        disclosures, known, policy = _policies(reader, truth)
        prices = _prices(reader)
        outcome = _outcome(reader)
        workflow = _workflow(reader, truth, policy, prices, outcome)
        reader.heading(layout.invariants, 'expected_invariants:')
        invariants = {f'I{i}': reader.scalar(layout.invariants + i, f'I{i}', f'evaluation.invariants.I{i}') for i in range(1, 9)}
        properties = []
        if layout.interaction_heading:
            reader.heading(layout.interaction_heading, 'expected_interaction_properties:')
            for row, field in enumerate(layout.interaction_fields, layout.interaction_start):
                reader.expect(row, field, 'true', 'evaluation.interaction_properties.' + field)
                properties.append(field)
        populated = {cell for cell, value in cells.items() if cell.startswith(layout.column) and value}
        unconsumed = populated - reader.uses.keys()
        if unconsumed:
            raise SourceFidelityError(f'{identifier}: populated unconsumed cells: {sorted(unconsumed)}')
        raw = dict(schema_version='2', scenario_id=identifier, name=name,
            source=dict(source_file=SOURCE_FILE, worksheet=WORKSHEET, source_sha256=SOURCE_SHA256,
                        source_version=SOURCE_VERSION, scenario_column=layout.column),
            initial_state=dict(conversation='EMPTY', order_store='EMPTY_ISOLATED'), fixtures=FIXTURES,
            customer=dict(description=description, ground_truth=truth, initial_message=initial_message,
                          initial_disclosures=disclosures, initially_known_configuration_fields=known,
                          conversation_policy=policy),
            evaluation=dict(system_derived={'pricing': prices}, workflow=workflow, outcome=outcome,
                            invariants=invariants, interaction_properties=properties))
        try:
            scenario = ScenarioSpecV2.model_validate_json(json.dumps(raw, ensure_ascii=False))
        except ValueError as exc:
            raise SourceFidelityError(f'{identifier}: extracted candidate violates frozen V2-A contract: {exc}') from exc
        blanks = tuple(f'{layout.column}{row}' for row in range(1, 134) if not cells.get(f'{layout.column}{row}', ''))
        results.append(Extraction(scenario, tuple(CellUse(cell, destination) for cell, destination in reader.uses.items()), blanks))
    return tuple(results)


def extract_workbook(path: Path = ROOT / SOURCE_FILE) -> tuple[Extraction, ...]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SourceFidelityError(f'Cannot read source workbook: {exc}') from exc
    if sha256(raw).hexdigest() != SOURCE_SHA256:
        raise SourceFidelityError('Frozen workbook SHA-256 mismatch')
    return extract_cells(_read_archive(raw))


def serialize_candidate(scenario: ScenarioSpecV2) -> bytes:
    return (json.dumps(scenario.model_dump(mode='json'), ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def verify_candidate_fidelity(candidate: ScenarioSpecV2, source: Extraction) -> None:
    if candidate != source.scenario:
        raise SourceFidelityError(f'{candidate.scenario_id}: candidate differs from workbook projection')


def generate_candidates(directory: Path = CANDIDATE_DIRECTORY) -> tuple[Extraction, ...]:
    results = extract_workbook()
    destination = Path(directory)
    if destination.resolve() == (ROOT / 'evaluation/scenarios').resolve():
        raise SourceFidelityError('Active v1 fixture directory is forbidden')
    if destination.is_symlink():
        raise SourceFidelityError('Candidate directory symlinks are forbidden')
    destination.mkdir(parents=True, exist_ok=True)
    expected = {result.scenario.scenario_id + '.json' for result in results}
    if {p.name for p in destination.iterdir()} - expected:
        raise SourceFidelityError('Unexpected candidate-directory contents')
    paths = [destination / (result.scenario.scenario_id + '.json') for result in results]
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths):
        raise SourceFidelityError('Candidate targets must be regular files, not symlinks')
    for result, path in zip(results, paths):
        path.write_bytes(serialize_candidate(result.scenario))
    return results


# Frozen overview cells summarize, rather than add to, the mapped policy/workflow
# fields. Pin exact text to prevent silently discarding a changed overview claim.
OVERVIEW_HASHES = {'B': '9f88ed9e3c4856c0b8fdb24ff5ca6973719a2890adbf39cf6cd1101e4b77c006', 'C': '1e1e7ed078c9c6f949a1ce7b16a6c785908407beac608b191cd4850777e4b865', 'D': 'c876acb1dc3b7693102850773795c1a1ec5165036c691129031b61da753488f3', 'E': '8d97c11ff1869bd9f83eadb30b657fddd69cb2ea1b3f49c05849accea0f68d0f', 'F': '197db18ad9c4926d58be6f4959906a3b98d430213415b9ade2be8c6d6c40a255'}


if __name__ == '__main__':
    for result in generate_candidates():
        print(result.scenario.scenario_id, sha256(serialize_candidate(result.scenario)).hexdigest(),
              'consumed', len(result.consumed), 'blank', len(result.blank_cells))
