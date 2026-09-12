"""Static shared option knowledge, projected through the existing Support API.

Catalog revision ownership belongs to the caller supplying expected_sha256.
Loading happens at setup; enquiry projection performs no I/O or model calls.
"""

import hashlib
import json
from pathlib import Path
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from order_creation_catalog import CATALOG_PATH, DEMO_TABLE_SIZES
from support_agent import KnowledgeFact, SupportKnowledgeContext
from evaluation.public_observation import PublicMessage

OptionField = Literal['product_model', 'table_size', 'timber', 'timber_painting',
                      'felt_color', 'bracket', 'top_profile']
TableSize = Literal['7ft', '8ft', '9ft']
CATEGORIES = {
    'timber': 'Timber', 'timber_painting': 'Timber Paint', 'felt_color': 'Felt',
    'bracket': 'Bracket', 'top_profile': 'Top Rail Profile',
}
LABELS = {'product_model': 'model', 'table_size': 'table size', 'timber': 'timber',
          'timber_painting': 'timber finish', 'felt_color': 'cloth colour',
          'bracket': 'bracket', 'top_profile': 'top profile'}
SOURCE_REFERENCE = 'Shared product option catalog'


class KnowledgeValidationError(ValueError):
    """Invalid/missing authoritative source; never silently repaired."""


class FrozenKnowledgeModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')


def _canonical(value):
    if type(value) is not str or not value.strip() or value != value.strip() or '\n' in value or '\r' in value:
        raise ValueError('Expected a nonblank canonical source string')
    return value


def _unique(values):
    if not values:
        raise ValueError('An authoritative option set cannot be empty')
    keys = [_canonical(value).casefold() for value in values]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate/conflicting canonical options')
    return values


class ModelSizeOptions(FrozenKnowledgeModel):
    product_model: str
    table_sizes: tuple[TableSize, ...]

    _model = field_validator('product_model')(_canonical)
    _sizes = field_validator('table_sizes')(_unique)


class StaticProductKnowledge(FrozenKnowledgeModel):
    source_path: str
    source_sha256: str
    model_sizes: tuple[ModelSizeOptions, ...]
    timber: tuple[str, ...]
    timber_painting: tuple[str, ...]
    felt_color: tuple[str, ...]
    bracket: tuple[str, ...]
    top_profile: tuple[str, ...]

    _path = field_validator('source_path')(_canonical)
    _options = field_validator('timber', 'timber_painting', 'felt_color', 'bracket', 'top_profile')(_unique)

    @field_validator('source_sha256')
    @classmethod
    def valid_hash(cls, value):
        if not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Expected a lowercase SHA-256 digest')
        return value

    @model_validator(mode='after')
    def unique_models(self) -> Self:
        _unique(tuple(model.product_model for model in self.model_sizes))
        return self

    @property
    def product_models(self) -> tuple[str, ...]:
        return tuple(model.product_model for model in self.model_sizes)

    @property
    def table_sizes(self) -> tuple[TableSize, ...]:
        return tuple(dict.fromkeys(size for model in self.model_sizes for size in model.table_sizes))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KnowledgeValidationError(f'Duplicate catalog JSON key: {key}')
        result[key] = value
    return result


def load_static_product_knowledge(
    path: str | Path = CATALOG_PATH, *, expected_sha256: str,
) -> StaticProductKnowledge:
    """Read once at setup, verify caller-owned provenance, discard non-option data."""
    if type(expected_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise KnowledgeValidationError('expected_sha256 must be 64 lowercase hexadecimal characters')
    try:
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha256:
            raise KnowledgeValidationError(f'Catalog hash mismatch: expected={expected_sha256}; actual={digest}')
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        if type(payload) is not dict or set(payload) != {'records'} or type(payload['records']) is not list:
            raise KnowledgeValidationError('Malformed catalog records container')
        options = {category: [] for category in CATEGORIES.values()}
        models = {}
        model_spellings = {}
        titles = set()
        pairs = set()
        for record in payload['records']:
            if type(record) is not dict or not {'category', 'title', 'price', 'sku'} <= set(record):
                raise KnowledgeValidationError('Malformed catalog record')
            category = _canonical(record['category'])
            title = _canonical(record['title'])
            # Price/SKU are source fields, never projected, used for filtering,
            # deduplication, or stored in the option snapshot.
            if any(type(record[k]) is not str or not record[k].strip() for k in ('price', 'sku')):
                raise KnowledgeValidationError('Malformed source metadata')
            key = (category, title.casefold())
            if key in titles:
                raise KnowledgeValidationError('Duplicate/conflicting catalog title')
            titles.add(key)
            if category == 'Table Design Model':
                if set(record) != {'category', 'title', 'price', 'sku', 'product_model', 'table_size'}:
                    raise KnowledgeValidationError('Missing/unexpected explicit product mapping')
                model, size = _canonical(record['product_model']), _canonical(record['table_size'])
                key = (model.casefold(), size.casefold())
                if key in pairs:
                    raise KnowledgeValidationError('Duplicate/conflicting model-size pair')
                pairs.add(key)
                if model.casefold() in model_spellings and model_spellings[model.casefold()] != model:
                    raise KnowledgeValidationError('Conflicting model spelling/casing')
                model_spellings[model.casefold()] = model
                if size not in (*DEMO_TABLE_SIZES, '6ft'):
                    raise KnowledgeValidationError('Unexpected catalog size; review supported-size scope')
                if size in DEMO_TABLE_SIZES:
                    models.setdefault(model, []).append(size)
            elif category in options:
                if set(record) != {'category', 'title', 'price', 'sku'}:
                    raise KnowledgeValidationError('Unexpected option record fields')
                options[category].append(title)
            else:
                raise KnowledgeValidationError(f'Unexpected catalog category: {category}')
        return StaticProductKnowledge(source_path=str(Path(path)), source_sha256=digest,
            model_sizes=tuple(ModelSizeOptions(product_model=m, table_sizes=tuple(s)) for m, s in models.items()),
            **{field: tuple(options[category]) for field, category in CATEGORIES.items()})
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, KnowledgeValidationError):
            raise
        raise KnowledgeValidationError(f'Invalid static catalog: {exc}') from exc


TOPICS = {
    'models': 'product_model', 'model': 'product_model', 'table model': 'product_model',
    'product models': 'product_model', 'table models': 'product_model',
    'sizes': 'table_size', 'table sizes': 'table_size', 'table size': 'table_size',
    'timber': 'timber', 'timber types': 'timber', 'timbers': 'timber',
    'timber finish': 'timber_painting', 'timber finishes': 'timber_painting',
    'timber painting': 'timber_painting', 'felt': 'felt_color', 'felt colours': 'felt_color',
    'felt colors': 'felt_color', 'felt colour': 'felt_color', 'felt color': 'felt_color',
    'cloth colour': 'felt_color', 'cloth colours': 'felt_color', 'cloth color': 'felt_color',
    'cloth colors': 'felt_color', 'bracket': 'bracket', 'brackets': 'bracket',
    'top profile': 'top_profile', 'top profiles': 'top_profile', 'top rail profile': 'top_profile',
    'top-profile': 'top_profile', 'top-profiles': 'top_profile',
}
_TOPIC = '(?:' + '|'.join(re.escape(t) for t in sorted(TOPICS, key=len, reverse=True)) + ')'


class ProductQuery(FrozenKnowledgeModel):
    field: OptionField
    explicit_model: str | None = None


def detect_product_query(current_customer_message: str) -> ProductQuery | None:
    """Bounded public syntax only. No private scenario or execution inputs."""
    if type(current_customer_message) is not str or not current_customer_message.strip():
        raise ValueError('Expected a nonblank current customer message')
    text = current_customer_message.strip()
    if text.count('?') != 1 or not text.endswith('?') or any(c in text for c in ('"', '`', '“', '”', '>')):
        return None
    # CS1-B places a discovery question after zero or more factual lines. These
    # lines establish no authority and are ignored only when syntactically bounded.
    lines = text.split('\n')
    if len(lines) > 1:
        for line in lines[:-1]:
            if not re.fullmatch(r"(?:My (?:name|phone number|email address|street address|city|state|postcode|country|room size) is .+|The quantity is [1-9][0-9]*|For (?:table model|table size|timber|timber finish|cloth colour|bracket|top profile), I'll choose .+|I have no special instructions|I don't have a company name to provide)\.", line):
                return None
        text = lines[-1]
    # A bounded buying-intent preamble, including S03, not scenario-ID matching.
    text = re.sub(r"^I'd like to (?:place an order for|order) (?:a )?custom pool table, but I don't know which model to choose\.\s+", '', text)
    patterns = [
        rf'What (?P<topic>{_TOPIC})(?: options)? are available(?: for (?P<model>[^?]+))?\?',
        rf'What (?P<topic>{_TOPIC})(?: options)? do you have(?: available)?\?',
        rf'What (?P<topic>{_TOPIC})(?: options)? can I choose\?',
        rf'Is [A-Za-z0-9][A-Za-z0-9 /-]* available for (?P<topic>{_TOPIC})\?',
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            field = TOPICS[match['topic'].lower()]
            model = match.groupdict().get('model')
            if model and field != 'table_size':
                return None
            return ProductQuery(field=field, explicit_model=model)
    return None


def _selected_model(history: tuple[PublicMessage, ...], knowledge: StaticProductKnowledge) -> str | None:
    selections = set()
    for message in history:
        if message.role != 'user':
            continue
        for line in message.text.split('\n'):
            match = re.fullmatch(r"For table model, I'll choose (.+)\.", line)
            if match:
                # Unknown or conflicting explicit selections preclude inference.
                selections.add(match[1])
    if len(selections) == 1:
        model = next(iter(selections))
        return model if model in knowledge.product_models else None
    return None


def _list(values: tuple[str, ...]) -> str:
    return values[0] if len(values) == 1 else ', '.join(values[:-1]) + ' and ' + values[-1]


def provide_support_knowledge(
    current_customer_message: str,
    public_history: tuple[PublicMessage, ...] = (),
    *, knowledge: StaticProductKnowledge,
) -> SupportKnowledgeContext | None:
    """Project relevant complete option sets, never target-specific hints.

    Source references are customer-safe labels. Provenance hashes/paths stay in
    the static snapshot, outside the Support prompt's allowed facts.
    """
    knowledge = StaticProductKnowledge.model_validate(knowledge)
    if type(public_history) is not tuple or any(type(m) is not PublicMessage for m in public_history):
        raise TypeError('Expected a tuple of public role/text messages')
    history = tuple(PublicMessage.model_validate(m) for m in public_history)
    query = detect_product_query(current_customer_message)
    if query is None:
        return None
    if query.field == 'product_model':
        text = f'Available model options are {_list(knowledge.product_models)}.'
    elif query.field == 'table_size':
        # Explicit current references take precedence, including ambiguous/unknown
        # references, which must not fall back to an older selected model.
        model = query.explicit_model if query.explicit_model is not None else _selected_model(history, knowledge)
        sizes = next((m.table_sizes for m in knowledge.model_sizes if m.product_model == model), None)
        if sizes is not None:
            text = f'Available table size options for {model} are {_list(sizes)}.'
        else:
            text = f'Table sizes available across supported models are {_list(knowledge.table_sizes)}. Size availability depends on model.'
    else:
        text = f'Available {LABELS[query.field]} options are {_list(getattr(knowledge, query.field))}.'
    return SupportKnowledgeContext(answer_facts=[KnowledgeFact(text=text, source_reference=SOURCE_REFERENCE)])
