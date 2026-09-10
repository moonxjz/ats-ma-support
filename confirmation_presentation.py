"""Shared pure presentation of supplied snapshots; no workflow/state decisions."""

from decimal import Decimal, InvalidOperation
import html
import re


CONFIG_FIELDS = (
    ("product_model", "Product model"), ("table_size", "Table size"),
    ("timber", "Timber"), ("timber_painting", "Timber finish"),
    ("felt_color", "Cloth colour"), ("bracket", "Bracket"),
    ("top_profile", "Top profile"), ("quantity", "Quantity"),
)
CUSTOMER_FIELDS = (("customer_name", "Customer name"), ("company_name", "Company"),
                   ("phone", "Phone"), ("email", "Email"))
ADDRESS_FIELDS = (("address_line_1", "Address line 1"), ("address_line_2", "Address line 2"),
                  ("city", "City"), ("state", "State"), ("postcode", "Postcode"), ("country", "Country"))
PRICE_FIELDS = (("customisation_price", "Customisation price"), ("unit_price", "Unit price"),
                ("shipping_cost", "Delivery cost"), ("total_price", "Total"))
OPTIONAL = {"company_name", "customer_instructions", "address_line_2"}


def _object(value):
    if not isinstance(value, dict):
        raise TypeError("Snapshot/address must be a dictionary.")
    return value


def _escape(value):
    # Preserve visible text while preventing Markdown/HTML structure injection.
    value = html.escape(value, quote=False)
    value = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', value)
    return value.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Price must be a supplied finite non-negative numeric value.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Malformed price.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Price must be finite and non-negative.")
    # Fixed plain-decimal format, insignificant fractional zeros removed. No
    # rounding, quantization, arithmetic, currency or tax assumptions.
    rendered = format(amount, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return '0' if amount.is_zero() else rendered


def _rows(snapshot, fields):
    rows = []
    for key, label in fields:
        if key not in snapshot:
            raise ValueError(f"Snapshot is missing {key}.")
        value = snapshot[key]
        if value is None and key in OPTIONAL:
            continue
        if key == 'quantity':
            if type(value) is not int or value < 1:
                raise ValueError("Quantity must be a positive integer.")
            display = str(value)
        elif key in {field for field, _ in PRICE_FIELDS}:
            display = _price(value)
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Snapshot field {key} must be a nonblank string.")
            display = value
        rows.append(f"- **{label}:** {_escape(display)}")
    return '\n'.join(rows)


def render_configuration_summary(snapshot: dict) -> str:
    """Render all eight configuration fields directly; extra metadata is excluded."""
    return '**Configuration Summary**\n\n' + _rows(_object(snapshot), CONFIG_FIELDS)


def render_provisional_order(snapshot: dict) -> str:
    """Render all customer-visible final fields from the emitted snapshot.

    product_sku is shown; room_size_validation_result is excluded. No model,
    catalog, builder, controller, confirmation interpreter or store is invoked.
    Null optional fields are omitted, but missing keys are malformed evidence.
    """
    snapshot = _object(snapshot)
    address = _object(snapshot.get('delivery_address'))
    sections = [
        '**Provisional Order**',
        '**Customer details**\n\n' + _rows(snapshot, CUSTOMER_FIELDS),
        '**Delivery address**\n\n' + _rows(address, ADDRESS_FIELDS),
        '**Product configuration**\n\n' + _rows(snapshot, (("product_sku", "Product code"), *CONFIG_FIELDS)),
        '**Room**\n\n' + _rows(snapshot, (("room_size", "Room size"),)),
    ]
    instructions = _rows(snapshot, (("customer_instructions", "Customer instructions"),))
    if instructions:
        sections.append(instructions)
    sections.append('**Pricing**\n\n' + _rows(snapshot, PRICE_FIELDS))
    return '\n\n'.join(sections)
