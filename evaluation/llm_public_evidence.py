"""CS2-A1 bounded public evidence; no whole-response CS1 framing gate.

Spans prove provenance only. Consequential claims must also match independently
verified constructions. Unknown material framing fails closed. No ATS/catalog I/O.
"""
import html
import json
import re

from confirmation_presentation import ADDRESS_FIELDS, CONFIG_FIELDS, CUSTOMER_FIELDS, PRICE_FIELDS
from evaluation.public_observation import (
    ArtifactValue, AvailabilityEvidence, EvidenceRef, FIELD_LABELS, LABEL_FIELDS,
    PublicArtifact, RequestedFieldEvidence, TOPICS,
)


class EvidenceError(ValueError):
    """Public evidence is absent, ambiguous, conflicting, or unsupported."""


def reference(text, index, start=0, end=None):
    end = len(text) if end is None else end
    return EvidenceRef(message_index=index, start=start, end=end, quote=text[start:end])


def units(text, index):
    """Whole sentences/lines with original Unicode character offsets."""
    for match in re.finditer(r'[^\n]+', text):
        for part in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)', match[0]):
            raw = part[0]
            start = match.start() + part.start() + len(raw) - len(raw.lstrip())
            end = match.start() + part.end() - (len(raw) - len(raw.rstrip()))
            if end > start:
                yield reference(text, index, start, end)


_TOPIC = '(?:' + '|'.join(re.escape(t) for t in sorted(TOPICS, key=len, reverse=True)) + ')'
_LABELS = {**LABEL_FIELDS, 'name': 'customer_name', 'email': 'email', 'phone': 'phone',
           'company': 'company_name', 'customer instructions': 'customer_instructions'}
_UNSAFE = re.compile(r'["“”`<>;]|\b(?:might|may|perhaps|maybe|hypothetically|suppose|said|quoted|except|however|but)\b', re.I)
_VETO = re.compile(
    r"\b(?:do not|don't|cannot|can't|must not|should not|not yet|no longer|ignore|disregard|"
    r"instead|incorrect|wrong|unavailable|hold off|wait|cancel|not available|not required|"
    r"not correct|not ready|not final|not valid|not suitable|not supported|not offered|out of stock|only an example|illustration|"
    r"already (?:placed|created|confirmed)|has been (?:placed|confirmed))\b", re.I)


def requested_fields(text, index):
    result = []
    for ref in units(text, index):
        raw = ref.quote
        if _UNSAFE.search(raw):
            continue
        patterns = [
            r'(?:Could|Can|Would) you (?:please )?(?:provide|share|give me|tell me) (?:your |the )?(.+?)\?',
            r'Please (?:provide|share|enter) (?:your |the )?(.+?)[.?]',
            r'(?:What is|What\'s) your (.+?)\?',
            r'Please (?:choose|select) (?:your |the )?(.+?)\.',
            r'Which (.+?) would you like\?',
        ]
        for pattern in patterns:
            match = re.fullmatch(pattern, raw, re.I)
            if match:
                labels = re.split(r',\s*(?:and\s+)?|\s+and\s+', match[1].lower())
                if labels and all(label in _LABELS for label in labels):
                    fields = [_LABELS[label] for label in labels]
                    if len(set(fields)) != len(fields):
                        raise EvidenceError('Duplicate requested field')
                    result.extend(RequestedFieldEvidence(field=f, evidence=ref) for f in fields)
                break
    return tuple(result)


def question_context(text):
    """Only public deterministic questions establish a fieldless answer context."""
    last = text.split('\n')[-1]
    match = re.search(rf'What (?P<t>{_TOPIC})(?: options)? (?:are available|do you have available)\?$', last, re.I)
    if not match:
        match = re.fullmatch(rf'Is .+ available for (?P<t>{_TOPIC})\?', last, re.I)
    return TOPICS[match['t'].lower()] if match else None


def _values(raw):
    if _UNSAFE.search(raw) or re.search(r'\b(?:not|no|if|could|would|should|available|unavailable|is|are)\b', raw, re.I):
        return None
    values = tuple(re.split(r',\s*(?:and\s+)?|\s+and\s+', raw))
    if not values or len(set(values)) != len(values) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 /-]*', v) for v in values):
        return None
    return values


def availability(text, index, context=None, selected_model=None):
    result = []
    # A heading and its complete consecutive bullet list form a single claim.
    bullet_pattern = rf'^(?:Available )?(?P<t>{_TOPIC})(?: options)?(?: (?:are|include))?:\n(?P<rows>(?:[-*] [^\n]+(?:\n|$))+)'
    blocks = []
    for m in re.finditer(bullet_pattern, text, re.I | re.M):
        values = tuple(line[2:] for line in m['rows'].splitlines())
        if values and len(set(values)) == len(values) and all(_values(v) == (v,) for v in values):
            end = m.end() - (1 if m[0].endswith('\n') else 0)
            ref = reference(text, index, m.start(), end)
            result.append(AvailabilityEvidence(field=TOPICS[m['t'].lower()], values=values, status='OFFERED', evidence=ref))
            blocks.append((m.start(), m.end()))
    requests = requested_fields(text, index)
    topics = set(r.field for r in requests if r.field in TOPICS.values())
    local_context = next(iter(topics)) if len(topics) == 1 else None if topics else context
    for ref in units(text, index):
        if any(a <= ref.start < b for a, b in blocks):
            continue
        raw = ref.quote
        if raw.startswith('Supported values: '):
            try:
                vals = json.loads(raw.removeprefix('Supported values: '))
            except ValueError:
                continue
            if len(topics) == 1 and isinstance(vals, list) and vals and all(type(v) is str and v.strip() for v in vals) and len(set(vals)) == len(vals):
                result.append(AvailabilityEvidence(field=local_context, values=tuple(vals), status='OFFERED', evidence=ref))
            continue
        if _UNSAFE.search(raw):
            continue
        sized = re.fullmatch(r'Available table size options for (.+?) are (.+?)\.', raw, re.I)
        if sized:
            vals = _values(sized[2])
            if selected_model is not None and sized[1] == selected_model and vals:
                result.append(AvailabilityEvidence(field='table_size', values=vals, status='OFFERED', evidence=ref))
            continue
        match = re.fullmatch(rf'(?:Available )?(?P<t>{_TOPIC})(?: options)? (?:include|are) (?P<v>.+?)\.?', raw, re.I)
        field, status, vals = None, 'OFFERED', None
        if match:
            field, vals = TOPICS[match['t'].lower()], _values(match['v'])
        else:
            match = re.fullmatch(rf'(?:Yes, )?(?P<v>.+?) (?:is|are) (?P<s>available|unavailable|not available)(?: (?:for|as) (?P<t>{_TOPIC})(?: options)?)?\.?', raw, re.I)
            if match:
                field = TOPICS[match['t'].lower()] if match['t'] else local_context
                vals = _values(match['v'])
                status = 'OFFERED' if match['s'].lower() == 'available' else 'DENIED'
            else:
                match = re.fullmatch(r'We offer (?P<v>.+?)\.', raw, re.I)
                if match:
                    field, vals = local_context, _values(match['v'])
        if field and vals:
            result.append(AvailabilityEvidence(field=field, values=vals, status=status, evidence=ref))
    return tuple(result)


def _approval(raw, kind):
    lead = r'(?:(?:If everything (?:is correct|looks right|looks correct), )?(?:please|kindly) )'
    if kind == 'CONFIGURATION':
        patterns = [
            lead + r'(?:confirm|approve) (?:the|this) configuration(?: above| as described)?(?:,? or tell me what to change)?[.!]',
            r'(?:Can|Could|Would) you (?:please )?(?:confirm|approve) (?:the|this) configuration\?',
            r'Please confirm that the configuration above is correct\.',
        ]
    else:
        patterns = [
            lead + r'(?:explicitly )?confirm (?:that )?you (?:wish|want|would like) to place (?:this|the)(?: provisional)? order[.!]',
            r'(?:Do you wish|Would you like|Do you want) to place (?:this|the)(?: provisional)? order\?',
            lead + r'confirm the final order(?: and that you wish to place it)?[.!]',
            r'Please confirm that you would like us to place the order\.',
        ]
    return any(re.fullmatch(p, raw, re.I) for p in patterns)


def approval_requests(text, index, artifact):
    refs = []
    for ref in units(text, index):
        if ref.start >= artifact.evidence.end and _approval(ref.quote, artifact.kind):
            refs.append(ref)
    return tuple(refs)


def assert_framing_safe(text, index, artifact, request):
    """Check all surrounding material, not only the model's selected sentence.

    Courtesy/review prose is not enumerated. Potential material claims and
    unrecognized commands about approval/placement are conservatively rejected.
    """
    requests = approval_requests(text, index, artifact)
    if request not in requests or len(requests) != 1:
        raise EvidenceError('Missing or ambiguous scoped approval request')
    outside = text[:artifact.evidence.start] + text[artifact.evidence.end:]
    if _UNSAFE.search(outside) or _VETO.search(outside):
        raise EvidenceError('Quoted, conditional, or vetoed artifact framing')
    for ref in units(text, index):
        if artifact.evidence.start <= ref.start < artifact.evidence.end or ref == request:
            continue
        # Corrections invitations do not assert an actual change.
        if re.fullmatch(r'If you notice any errors or need adjustments, please let us know so we can make the necessary corrections\.', ref.quote, re.I):
            continue
        if re.search(r'\b(?:confirm|approve|place|created|placed|quantity|email|phone|address|timber|felt|bracket|profile|SKU|price|shipping|instead|actually|model|size|customer|company|instructions|table)\b|\d|[$€£]', ref.quote, re.I):
            raise EvidenceError('Unverified material artifact framing')


def assert_claims_safe(text, index, recognized):
    """Unrecognized potential claims cannot be ignored beside a cited claim."""
    for ref in units(text, index):
        if any(r.start <= ref.start and ref.end <= r.end for r in recognized):
            continue
        if _UNSAFE.search(ref.quote) or _VETO.search(ref.quote) or re.search(
            r'\b(?:available|offer|options|choose|select|provide|share|confirm|approve|order|model|size|timber|felt|cloth|bracket|profile|quantity|email|phone|name|address|room|company|instructions|if|unless|would|could|should)\b|\?', ref.quote, re.I):
            raise EvidenceError('Unverified consequential public language')


def terminal_evidence(text, index, category):
    patterns = {
        'ORDER_CREATED': r'Your order(?: [A-Za-z0-9-]+)? has been created\.',
        'UNAVAILABLE': r"(?:I (?:can't|cannot) (?:provide|retrieve|make|prepare) [A-Za-z ]+ here(?: yet)?|I don't have [A-Za-z ]+ (?:available )?here|(?:That information|Information) is unavailable here)\.",
    }
    pattern = patterns.get(category)
    refs = tuple(r for r in units(text, index) if pattern and re.fullmatch(pattern, r.quote))
    if not refs:
        raise EvidenceError('No verified terminal public claim')
    status_refs = tuple(r for r in units(text, index) if category == 'ORDER_CREATED' and re.fullmatch(r'Its status is [A-Z_]+\.', r.quote))
    assert_claims_safe(text, index, (*refs, *status_refs))
    return refs


# Body grammar adapted from frozen CS1; framing verification is CS2-local.
def _decode(value: str) -> str:
    # Decode exactly the shared renderer's encoding, then require round-trip.
    decoded = html.unescape(re.sub(r'\\([\\`*_{}\[\]()#+.!|>~-])', r'\1', value.replace('<br>', '\n')))
    encoded = html.escape(decoded, quote=False)
    encoded = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', encoded).replace('\n', '<br>')
    if encoded != value or not decoded.strip():
        raise ValueError("Noncanonical artifact escaping")
    return decoded


def extract_artifact(text: str, index: int) -> PublicArtifact | None:
    headings = list(re.finditer(r"^\*\*(Configuration Summary|Provisional Order)\*\*$", text, re.M))
    if len(headings) != 1 or sum(text.count('**' + title + '**') for title in ('Configuration Summary', 'Provisional Order')) != 1:
        return None
    heading = headings[0]
    kind = "CONFIGURATION" if heading[1] == "Configuration Summary" else "FINAL_ORDER"
    prefix = text[:heading.start()].strip()
    # Quoted/fenced documents are not presented as an artifact for approval.
    if re.search(r'["“”`]|^\s*>|\*\*', prefix, re.M):
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
    return PublicArtifact(kind=kind, values=tuple(values), evidence=reference(text, index, heading.start(), end),
                          approval_request=None)
