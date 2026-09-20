"""Bounded, deterministic interpretation of public text only.

Unrecognized prose is data (UNINTERPRETABLE), never a reason to guess. This
module has no catalog, workflow, model, or evaluator access.
"""

import html
import json
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workflow.confirmation_presentation import ADDRESS_FIELDS, CONFIG_FIELDS, CUSTOMER_FIELDS, PRICE_FIELDS
from evaluation.scenario_spec import ConfigurationField, Nonblank

NonnegativeInt = Annotated[int, Field(ge=0)]
InformationField = Literal[
    "customer_name", "phone", "email", "company_name", "customer_instructions",
    "delivery_address.address", "delivery_address.city", "delivery_address.state",
    "delivery_address.postcode", "delivery_address.country", "room_size", "quantity",
]
CustomerField = ConfigurationField | InformationField
PublicField = CustomerField | Literal[
    "product_sku", "customisation_price", "unit_price", "shipping_cost", "total_price",
]


class PublicContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class PublicMessage(PublicContract):
    role: Literal["user", "assistant"]
    text: Nonblank


class EvidenceRef(PublicContract):
    message_index: NonnegativeInt
    start: NonnegativeInt
    end: NonnegativeInt
    quote: Nonblank

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("Evidence span must exactly cover its quote")
        return self

    def resolve(self, history: tuple[PublicMessage, ...], role: str = "assistant") -> str:
        if self.message_index >= len(history):
            raise ValueError("Evidence message is absent from public history")
        message = history[self.message_index]
        if message.role != role or message.text[self.start:self.end] != self.quote:
            raise ValueError("Evidence does not resolve to the claimed public text")
        return self.quote


class RequestedFieldEvidence(PublicContract):
    field: CustomerField
    evidence: EvidenceRef


class AvailabilityEvidence(PublicContract):
    field: ConfigurationField
    values: tuple[Nonblank, ...]
    status: Literal["OFFERED", "DENIED"]
    evidence: EvidenceRef

    @model_validator(mode="after")
    def unique_values(self) -> Self:
        if not self.values or len(self.values) != len(set(self.values)):
            raise ValueError("Availability values must be nonempty and unique")
        return self


class ArtifactValue(PublicContract):
    field: PublicField
    value: Nonblank


class PublicArtifact(PublicContract):
    kind: Literal["CONFIGURATION", "FINAL_ORDER"]
    values: tuple[ArtifactValue, ...]
    evidence: EvidenceRef
    approval_request: EvidenceRef | None


class PublicObservation(PublicContract):
    kind: Literal["REQUESTS_OR_OPTIONS", "ARTIFACT", "ORDER_CREATED", "UNAVAILABLE", "UNINTERPRETABLE"]
    evidence: EvidenceRef
    requests: tuple[RequestedFieldEvidence, ...] = ()
    availability: tuple[AvailabilityEvidence, ...] = ()
    artifact: PublicArtifact | None = None


# Public wording only. Do not import the production agent to obtain this table.
FIELD_LABELS = {
    "customer_name": "full name", "phone": "phone number", "email": "email address",
    "delivery_address.address": "street address", "delivery_address.city": "city",
    "delivery_address.state": "state", "delivery_address.postcode": "postcode",
    "delivery_address.country": "country", "room_size": "room size",
    "product_model": "table model", "table_size": "table size", "timber": "timber",
    "timber_painting": "timber finish", "felt_color": "cloth colour", "bracket": "bracket",
    "top_profile": "top profile", "quantity": "quantity", "company_name": "company name",
    "customer_instructions": "special instructions",
}
LABEL_FIELDS = {label: field for field, label in FIELD_LABELS.items()}
LABEL_FIELDS.update({"model": "product_model", "felt colour": "felt_color", "felt color": "felt_color",
                     "cloth color": "felt_color", "top rail profile": "top_profile"})
TOPICS = {
    "model": "product_model", "models": "product_model", "table model": "product_model",
    "table models": "product_model", "table size": "table_size", "table sizes": "table_size",
    "timber": "timber", "timber finish": "timber_painting", "timber finishes": "timber_painting",
    "felt": "felt_color", "felt colour": "felt_color", "felt color": "felt_color",
    "cloth colour": "felt_color", "cloth color": "felt_color",
    "bracket": "bracket", "brackets": "bracket", "top profile": "top_profile",
    "top profiles": "top_profile", "top rail profile": "top_profile",
}
_TOPIC = "(?:" + "|".join(re.escape(t) for t in sorted(TOPICS, key=len, reverse=True)) + ")"
_UNSAFE_OPTION = re.compile(
    r'["“”`<>?;\n]|\b(?:if|might|may|could|would|should|perhaps|maybe|said|asked|quote|but|except|however|not only)\b', re.I)


def _ref(text: str, index: int, start: int = 0, end: int | None = None) -> EvidenceRef:
    end = len(text) if end is None else end
    return EvidenceRef(message_index=index, start=start, end=end, quote=text[start:end])


def initial_discovery_field(text: str) -> ConfigurationField | None:
    """Recognize the frozen S03 public question without scenario/architecture IDs."""
    match = re.search(rf"What (?P<topic>{_TOPIC}) do you have available\?$", text)
    return TOPICS[match['topic'].lower()] if match else None


def _values(raw: str) -> tuple[str, ...] | None:
    if _UNSAFE_OPTION.search(raw) or re.search(r"\b(?:not|no|unavailable|available|is|are|cannot|can't)\b", raw, re.I):
        return None
    values = tuple(part.strip() for part in re.split(r",\s*(?:and\s+)?|\s+and\s+", raw))
    if not values or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 /-]*", v) for v in values):
        return None
    return values if len(set(values)) == len(values) else None


def _availability(text: str, context: ConfigurationField | None, evidence: EvidenceRef) -> AvailabilityEvidence | None:
    if _UNSAFE_OPTION.search(text):
        return None
    patterns = [
        rf"Available (?P<topic>{_TOPIC})(?: options)? (?:include|are) (?P<values>.+)",
        rf"(?P<topic>{_TOPIC})(?: options)? (?:include|are) (?P<values>.+)",
        rf"(?P<values>.+) (?:is|are) available (?:for|as) (?P<topic>{_TOPIC})(?: options)?",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern + r"\.?", text, re.I)
        if match:
            values = _values(match['values'].removesuffix('.'))
            if values:
                return AvailabilityEvidence(field=TOPICS[match['topic'].lower()], values=values, status="OFFERED", evidence=evidence)
            return None
    match = re.fullmatch(rf"(?:Yes, )?(?P<values>.+?) (?:is|are) (?P<status>available|unavailable|not available)(?: (?:for|as) (?P<topic>{_TOPIC})(?: options)?)?\.?", text, re.I)
    if match:
        field = TOPICS[match['topic'].lower()] if match['topic'] else context
        values = _values(match['values'])
        if field and values:
            return AvailabilityEvidence(field=field, values=values,
                status="OFFERED" if match['status'].lower() == 'available' else "DENIED", evidence=evidence)
    return None


def _decode(value: str) -> str:
    # Decode exactly the shared renderer's encoding, then require round-trip.
    decoded = html.unescape(re.sub(r'\\([\\`*_{}\[\]()#+.!|>~-])', r'\1', value.replace('<br>', '\n')))
    encoded = html.escape(decoded, quote=False)
    encoded = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', encoded).replace('\n', '<br>')
    if encoded != value or not decoded.strip():
        raise ValueError("Noncanonical artifact escaping")
    return decoded


def _approval(text: str, kind: str) -> bool:
    # Full paragraph matches, not keyword searches in arbitrary/negated prose.
    suffix = r"(?:,? or tell me what to change)?[.?]"
    if kind == "CONFIGURATION":
        patterns = [r"Please (?:confirm|approve) (?:the|this) configuration" + suffix,
                    r"If everything is correct, kindly confirm the configuration\.",
                    r"Please confirm that the configuration above is correct\.",
                    r"(?:Can|Could) you (?:confirm|approve) (?:the|this) configuration\?",
                    r"Otherwise, approve the configuration as described\."]
    else:
        patterns = [r"Please (?:explicitly )?confirm (?:that )?you (?:wish|want) to place (?:this|the)(?: provisional)? order" + suffix,
                    r"Please confirm that you would like us to place the order\.",
                    r"If everything is correct, please confirm the final order\.",
                    r"(?:Do you wish|Would you like|Do you want) to place (?:this|the)(?: provisional)? order\?",
                    r"Please confirm (?:the final order and that you wish |you would like )to place (?:it|the order)\."]
    return any(re.fullmatch(pattern, text, re.I) for pattern in patterns)


def _review_sentence(text: str, kind: str) -> bool:
    """Bounded review prose, never field claims or general English inference."""
    title = 'configuration summary' if kind == 'CONFIGURATION' else 'provisional order'
    patterns = [
        rf"Please (?:review|check|examine) (?:the details below|(?:this|the) {title})\.",
        rf"(?:We have|We've) prepared (?:a|the) {title} for your review\.",
        rf"(?:Here is|Below is) (?:your|the) {title}(?: for your review)?\.",
        r"Please take a moment to examine the details provided and ensure they accurately reflect your requirements\.",
        r"Please (?:review|check) the details carefully\.",
        r"Thank you(?: for your patience)?\.",
        r"If you notice any errors or need adjustments, please let us know so we can make the necessary corrections\.",
        r"Please let us know if (?:any changes are needed|anything needs correcting)\.",
    ]
    return any(re.fullmatch(pattern, text, re.I) for pattern in patterns)


def _framing_request(text: str, index: int, kind: str, start: int, end: int) -> EvidenceRef | None:
    # Every sentence must be accounted for; do not cherry-pick an imperative
    # from quotations, reported speech, negation or contradictory instructions.
    request = None
    for left, right, introduction in ((0, start, True), (end, len(text), False)):
        cursor = left
        for sentence in re.finditer(r'[^.!?]+[.!?]', text[left:right]):
            a, b = left + sentence.start(), left + sentence.end()
            if text[cursor:a].strip():
                return None
            raw = text[a:b]
            a += len(raw) - len(raw.lstrip())
            value = text[a:b]
            if not introduction and _approval(value, kind):
                if request is not None:
                    return None  # multiple requests are conservatively ambiguous
                request = _ref(text, index, a, b)
            elif not _review_sentence(value, kind):
                return None
            cursor = b
        if text[cursor:right].strip():
            return None
    return request


def _artifact(text: str, index: int) -> PublicArtifact | None:
    headings = list(re.finditer(r"^\*\*(Configuration Summary|Provisional Order)\*\*$", text, re.M))
    if len(headings) != 1:
        return None
    heading = headings[0]
    kind = "CONFIGURATION" if heading[1] == "Configuration Summary" else "FINAL_ORDER"
    prefix = text[:heading.start()].strip()
    # Quoted/fenced documents are not presented as an artifact for approval.
    if re.search(r'["“”`]|^\s*>', prefix, re.M):
        return None
    fields = []

    def row(field, label, optional=False):
        fields.append(field)
        capture = 'v' + str(len(fields))
        pattern = re.escape('- **' + label + ':** ') + rf'(?P<{capture}>[^\n]+)'
        return '(?:' + pattern + r'\n)?' if optional else pattern

    def rows(items):
        return '\n'.join(row(field, label) for field, label in items)

    if kind == "CONFIGURATION":
        body = re.escape('**Configuration Summary**\n\n') + rows(CONFIG_FIELDS)
    else:
        customer_rows = row('customer_name', 'Customer name') + '\n' + row('company_name', 'Company', True) + rows(CUSTOMER_FIELDS[2:])
        body = (re.escape('**Provisional Order**\n\n**Customer details**\n\n') + customer_rows
                + re.escape('\n\n**Delivery address**\n\n') + rows(tuple(('delivery_address.' + f, l) for f, l in ADDRESS_FIELDS))
                + re.escape('\n\n**Product configuration**\n\n') + rows((("product_sku", "Product code"), *CONFIG_FIELDS))
                + re.escape('\n\n**Room**\n\n') + row('room_size', 'Room size'))
        body += '(?:\n\n' + row('customer_instructions', 'Customer instructions') + ')?'
        body += re.escape('\n\n**Pricing**\n\n') + rows(PRICE_FIELDS)
    match = re.match(body, text[heading.start():])
    if not match:
        return None
    end = heading.start() + match.end()
    tail = text[end:]
    # A body with trailing junk on its final row is already captured as value;
    # the strict scalar checks and request grammar reject it.
    # Do not reinterpret duplicate/extra rows or sections as harmless framing.
    if re.search(r'^\s*(?:[-*#>]|```)|\*\*', tail, re.M):
        return None
    values = []
    try:
        for number, field in enumerate(fields, 1):
            raw = match['v' + str(number)]
            if raw is None:
                continue
            value = _decode(raw)
            if field == 'quantity' and not re.fullmatch(r'[1-9][0-9]*', value):
                return None
            if field in {f for f, _ in PRICE_FIELDS} and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
                return None
            values.append(ArtifactValue(field=field, value=value))
    except ValueError:
        return None
    return PublicArtifact(kind=kind, values=tuple(values), evidence=_ref(text, index, heading.start(), end),
                          approval_request=_framing_request(text, index, kind, heading.start(), end))


def observe_public_response(text: str, message_index: int, *, context_field: ConfigurationField | None = None) -> PublicObservation:
    """Parse one public Support message. Context is an outstanding public question."""
    evidence = _ref(text, message_index)
    unknown = PublicObservation(kind="UNINTERPRETABLE", evidence=evidence)
    if 'Configuration Summary' in text or 'Provisional Order' in text:
        artifact = _artifact(text, message_index)
        return PublicObservation(kind="ARTIFACT", artifact=artifact, evidence=evidence) if artifact else unknown
    if re.fullmatch(r"Your order(?: [A-Za-z0-9-]+)? has been created\.(?: Its status is [A-Z_]+\.)?", text):
        return PublicObservation(kind="ORDER_CREATED", evidence=evidence)
    if re.fullmatch(r"(?:I (?:can't|cannot) (?:provide|retrieve|make|prepare) [A-Za-z ]+ here(?: yet)?|I don't have [A-Za-z ]+ (?:available )?here|(?:That information|Information) is unavailable here)\.", text):
        return PublicObservation(kind="UNAVAILABLE", evidence=evidence)

    requests = []
    availability = []
    # Deterministic request + optional quoted guidance, or independent public
    # paragraphs consisting entirely of recognized requests/availability claims.
    parts = list(re.finditer(r'[^\n]+', text))
    for part in parts:
        line = part[0]
        ref = _ref(text, message_index, part.start(), part.end())
        match = re.fullmatch(r"Could you please provide your (.+)\?", line)
        if not match:
            match = re.fullmatch(r"(?:Please choose|Which) (.+?)(?: would you like\?|\.)", line)
        if match:
            labels = re.split(r",\s*(?:and\s+)?|\s+and\s+", match[1])
            if any(label not in LABEL_FIELDS for label in labels):
                return unknown
            requests.extend(RequestedFieldEvidence(field=LABEL_FIELDS[label], evidence=ref) for label in labels)
            continue
        guidance = re.fullmatch(r'(Supplied value|Supported format|Supported values): (.+)', line)
        if guidance:
            if not requests:
                return unknown
            try:
                value = json.loads(guidance[2])
            except (ValueError, TypeError):
                return unknown
            if guidance[1] == 'Supported values':
                candidates = [r.field for r in requests if r.field in set(TOPICS.values())]
                if len(candidates) != 1 or not isinstance(value, list) or not value or any(type(v) is not str or not v.strip() for v in value) or len(value) != len(set(value)):
                    return unknown
                availability.append(AvailabilityEvidence(field=candidates[0], values=tuple(value), status="OFFERED", evidence=ref))
            elif type(value) is not str or not value.strip():
                return unknown
            continue
        candidates = [r.field for r in requests if r.field in set(TOPICS.values())]
        context = candidates[0] if len(candidates) == 1 else None if candidates else context_field
        item = _availability(line, context, ref)
        if item is None:
            return unknown
        availability.append(item)
    if len({r.field for r in requests}) != len(requests):
        return unknown
    # Do not salvage one positive clause from conflicting availability claims.
    for item in availability:
        if any(other.field == item.field and other.status != item.status and set(other.values) & set(item.values) for other in availability):
            return unknown
    if requests or availability:
        return PublicObservation(kind="REQUESTS_OR_OPTIONS", requests=tuple(requests), availability=tuple(availability), evidence=evidence)
    return unknown
