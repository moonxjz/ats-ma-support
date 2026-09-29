"""A single, fully LLM-driven customer-support agent (pure-Agent design).

Design
------
* One LLM agent drives the entire customer-facing conversation. There is no
  deterministic classifier, router, sub-agent or workflow state machine.
* The agent reasons about the order inside the prompt. It maintains a working
  ``order`` draft that is surfaced to it every turn as "CURRENT ORDER DRAFT".
* A hand-written JSON tool loop (no vendor tool-calling dependency) parses the
  JSON the model returns and executes the requested action:

    - ``respond``      -> send a customer-facing message and end the turn.
    - ``create_order`` -> the ONLY external write; persists the finalized,
                          fully-specified order snapshot to data/orders.json.
    - ``cancel_order`` -> explicit, deterministic cancellation (no string
                          guessing); marks a placed order CANCELLED or just ends
                          the flow if no order was created yet.

* Extraction, configuration/room validation and pricing are performed by the
  LLM itself using the authoritative catalogue / shipping / room knowledge that
  is embedded into the system prompt. The loop only enforces structural
  completeness before persisting (faithful to the "fully LLM-driven" choice:
  reliability is lower, but the LLM owns the business logic).

This replaces the previous classifier + root_agent + order_agent + support_agent
pipeline for the customer-facing core flow.
"""

from __future__ import annotations

import functools
import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from entity.conversation import ConversationMessage
from entity.order_creation_state import (
    FinalDeliveryAddress,
    FinalOrderSnapshot,
    REQUIRED_CUSTOMER_FIELDS,
)
from tools.llm_client import chat
from workflow.order.order_creation_order_store import (
    DEFAULT_ORDER_STORE_PATH,
    cancel_order,
    create_order,
)

MODEL_NAME = "qwen3:8b"
MAX_AGENT_STEPS = 8

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_PATH = _REPO_ROOT / "data" / "product_prices.json"
_SHIPPING_PATH = _REPO_ROOT / "data" / "shipping_rates.json"


# --------------------------------------------------------------------------- #
# Knowledge (authoritative data the LLM uses to validate & price in-prompt)
# --------------------------------------------------------------------------- #
def _build_knowledge() -> dict:
    """Load catalogue + shipping fixtures and project a compact knowledge block.

    This data is injected into the system prompt so the LLM performs extraction,
    validation and pricing itself (no separate tools for those steps).
    """
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    base_products = [
        {
            "product_model": r["product_model"],
            "table_size": r["table_size"],
            "unit_price": r["price"],
            "product_sku": r["sku"],
        }
        for r in catalog["records"]
        if r["category"] == "Table Design Model"
    ]
    customization_options: dict[str, list[dict[str, str]]] = {}
    for r in catalog["records"]:
        if r["category"] != "Table Design Model":
            customization_options.setdefault(r["category"], []).append(
                {"title": r["title"], "price": r["price"]}
            )

    shipping = json.loads(_SHIPPING_PATH.read_text(encoding="utf-8"))
    postcode_zones = {z["postcode"]: z["shipping_zone"] for z in shipping["postcode_zones"]}

    return {
        "base_products": base_products,
        "customization_options": customization_options,
        "shipping_rates": shipping["rates"],
        "postcode_zones": postcode_zones,
        "minimum_room_sizes_m": {
            "7ft": [4.90, 3.80],
            "8ft": [5.20, 4.00],
            "9ft": [5.50, 4.30],
            "10ft": [6.10, 4.60],
            "12ft": [6.70, 4.90],
        },
        "allowed_table_sizes": ["6ft", "7ft", "8ft", "9ft"],
    }


# --------------------------------------------------------------------------- #
# Deterministic pricing / validation (the tool owns this, not the LLM)
# --------------------------------------------------------------------------- #
# Order field -> catalogue category that supplies its title + price.
_OPTION_CATEGORY_BY_FIELD = {
    "top_profile": "Top Rail Profile",
    "bracket": "Bracket",
    "felt_color": "Felt",
    "timber": "Timber",
    "timber_painting": "Timber Paint",
}

# Minimum room dimensions (metres) per table size. MVP defaults aligned with the
# knowledge block; 6ft floor is conservative (a 6ft table fits any 7ft-suitable room).
_MIN_ROOM_M = {
    "6ft": (Decimal("4.50"), Decimal("3.50")),
    "7ft": (Decimal("4.90"), Decimal("3.80")),
    "8ft": (Decimal("5.20"), Decimal("4.00")),
    "9ft": (Decimal("5.50"), Decimal("4.30")),
    "10ft": (Decimal("6.10"), Decimal("4.60")),
    "12ft": (Decimal("6.70"), Decimal("4.90")),
}


@functools.lru_cache(maxsize=1)
def _load_catalog() -> list[dict]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))["records"]


@functools.lru_cache(maxsize=1)
def _load_shipping() -> tuple[dict[str, str], dict[tuple[int, str], Decimal]]:
    data = json.loads(_SHIPPING_PATH.read_text(encoding="utf-8"))
    zones = {z["postcode"]: z["shipping_zone"] for z in data["postcode_zones"]}
    rates = {
        (int(r["table_size_ft"]), r["shipping_zone"]): Decimal(r["per_table_shipping_rate"])
        for r in data["rates"]
    }
    return zones, rates


_POSTCODE_ZONES, _SHIPPING_RATES = _load_shipping()


# --------------------------------------------------------------------------- #
# System prompt
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT_TEMPLATE = """\
You are the ATS customer-support agent for a custom pool-table business. You handle
everything in one conversation: casual chat, product / price enquiries, and the
full order-creation flow (collecting information, validating the configuration and
room size, pricing, confirming, and creating the order).

=== TOOL PROTOCOL (mandatory) ===
On EVERY turn you must reply with exactly ONE JSON object and nothing else
(no Markdown fences, no commentary). The JSON shape is:

{
  "tool": "respond" | "create_order" | "cancel_order",
  "message": "<natural customer-facing text>",
  "order": { ...partial or full order fields... }   // optional; omit if no update
}

- "tool": "respond"  -> send "message" to the customer and finish this turn.
- "tool": "create_order" -> persist the finalized order described by "order"
  (and merged with the current draft) to the order system, then you will be asked
  to send a final confirmation message. ONLY call this after the customer has
  EXPLICITLY confirmed the full provisional order and every required field below
  is present and the room is SUITABLE.
- "tool": "cancel_order" -> the customer wants to abandon or not proceed with the
  order. Call this (instead of respond) ONLY when the customer clearly asks to
  cancel or stop. If an order was already placed, the tool marks it CANCELLED;
  otherwise it just ends the flow. After it runs you will be asked to send a final
  goodbye message. Never rely on guessing - only use this on an explicit request.
- "message": the natural reply the customer sees. Never include your JSON, the
  tool name, or internal field names in it.
- "order": update your working memory. Supply ONLY newly-known or corrected
  fields. Omit fields you do not yet know.

=== CURRENT ORDER DRAFT (your working memory) ===
It is supplied to you every turn as a separate system message. Read it, update it
via the "order" field, and keep it accurate. Do NOT clear required fields.

=== REQUIRED ORDER FIELDS ===
customer_name, email, phone, room_size, product_model, table_size, timber,
timber_painting, felt_color, bracket, top_profile, quantity (>=1), and a full
delivery_address with address, city, state, postcode, country. company_name and
customer_instructions are optional.

=== AUTHORITATIVE KNOWLEDGE (use EXACT titles / prices; never invent) ===
<<KNOWLEDGE>>

=== HOW TO VALIDATE, PRICE AND CONFIRM ===
1. Collect missing required fields ONE at a time, naturally. Greet first.
2. Configuration validation: every product_model/table_size/timber/timber_painting/
   felt_color/bracket/top_profile value MUST exactly match a title in the
   knowledge. If the customer names something not offered, tell them the allowed
   values from the knowledge and ask them to choose.
3. Room validation: parse room_size as two metre dimensions "W x H m" (order does
   not matter). It is SUITABLE only if BOTH dimensions are >= the minimum for the
   chosen table_size. If UNSUITABLE, tell the customer the suitable sizes from the
   knowledge. Never call create_order unless room is SUITABLE.
4. When configuration + room are complete and SUITABLE, present a configuration
   summary and ask the customer to confirm (or request changes).
5. After they confirm the configuration, present a provisional order (all
   configuration, the customer's delivery address and quantity) and ask the
   customer to explicitly confirm they want to PLACE the order. NOTE: you do NOT
   compute prices, product_sku or the room result. The create_order tool
   calculates unit_price, customisation_price, shipping_cost, total_price and
   product_sku from the catalogue + shipping data, and sets
   room_size_validation_result = "SUITABLE" automatically when the room fits.
6. Only after explicit final confirmation, call create_order with the FULL set
   of CUSTOMER-FACING fields only: every required configuration field, quantity,
   and the complete delivery_address. Do NOT include product_sku, prices, or
   room_size_validation_result - the tool fills those. If create_order returns
   {"ok": false}, read its "details"/"error", relay the issue to the customer,
   correct the draft, and retry the call.

=== GROUNDING RULES ===
- State only facts from the knowledge or the customer. Never invent prices,
  identifiers, currency, tax, delivery timing, promises or alternatives.
- Do not claim an order was created until create_order returns an order_id; then
  include that exact order_id and status in your confirmation message.
- For product/price enquiries, answer from the knowledge only.
- For casual chat, be friendly without making business claims or asking for
  customer/order details.
- Ask only for the next missing required item; do not dump a long questionnaire.
"""


def _system_prompt() -> str:
    knowledge = json.dumps(_build_knowledge(), ensure_ascii=False, indent=2)
    return _SYSTEM_PROMPT_TEMPLATE.replace("<<KNOWLEDGE>>", knowledge)


# --------------------------------------------------------------------------- #
# JSON envelope schema (passed to the LLM for structured output)
# --------------------------------------------------------------------------- #
_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["respond", "create_order", "cancel_order"]},
        "message": {"type": "string"},
        "order": {
            "type": ["object", "null"],
            "properties": {
                "customer_name": {"type": ["string", "null"]},
                "company_name": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "room_size": {"type": ["string", "null"]},
                "product_model": {"type": ["string", "null"]},
                "table_size": {"type": ["string", "null"]},
                "timber": {"type": ["string", "null"]},
                "timber_painting": {"type": ["string", "null"]},
                "felt_color": {"type": ["string", "null"]},
                "bracket": {"type": ["string", "null"]},
                "top_profile": {"type": ["string", "null"]},
                "quantity": {"type": ["integer", "null"]},
                "customer_instructions": {"type": ["string", "null"]},
                "delivery_address": {
                    "type": ["object", "null"],
                    "properties": {
                        "address": {"type": ["string", "null"]},
                        "city": {"type": ["string", "null"]},
                        "state": {"type": ["string", "null"]},
                        "postcode": {"type": ["string", "null"]},
                        "country": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "product_sku": {"type": ["string", "null"]},
                "customisation_price": {"type": ["number", "string", "null"]},
                "unit_price": {"type": ["number", "string", "null"]},
                "shipping_cost": {"type": ["number", "string", "null"]},
                "total_price": {"type": ["number", "string", "null"]},
                "room_size_validation_result": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
    },
    "required": ["tool", "message"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
class PureAgent:
    """A single LLM agent driving the whole customer-facing conversation."""

    def __init__(
        self,
        *,
        conversation_id: str | None = None,
        order_store_path: Path = DEFAULT_ORDER_STORE_PATH,
        model: str = MODEL_NAME,
    ) -> None:
        self.conversation_id = conversation_id or "SUP-" + uuid4().hex[:12].upper()
        self.workflow_id = "WF-" + uuid4().hex[:12].upper()
        self.order_store_path = Path(order_store_path)
        self.model = model
        self.history: list[ConversationMessage] = []
        self.notes: list[dict[str, str]] = []  # internal system/tool-feedback notes
        self.draft: dict[str, Any] = {}
        self.created_order_id: str | None = None
        self.cancelled: bool = False
        self.finished: bool = False  # set once the order is created or cancelled
        self._system = _system_prompt()

    # ---- public API -------------------------------------------------------- #
    def process(self, customer_message: str) -> str:
        """Process one customer turn and return the customer-facing response.

        Once the order is created or cancelled (``finished`` is set), the agent no
        longer acts on further customer input; it simply confirms the conversation
        is closed. The driving loop should stop after the terminal reply.
        """
        if self.finished:
            return (
                "This conversation is now closed — your order has already been "
                "placed (or cancelled). Please contact us separately for any changes."
            )
        self.notes = []  # clear internal notes from the previous turn
        self.history.append(ConversationMessage(role="user", content=customer_message))
        last_response: str | None = None

        for _ in range(MAX_AGENT_STEPS):
            parsed = self._call_llm()
            if parsed is None:
                last_response = "Sorry, something went wrong. Could you repeat that?"
                break

            tool = parsed.get("tool")
            message = parsed.get("message") or ""
            order_update = parsed.get("order")

            if isinstance(order_update, dict):
                self.draft = self._merge_order(self.draft, order_update)

            if tool == "create_order":
                result = self._execute_create_order(order_update or {})
                self.notes.append(
                    {
                        "role": "system",
                        "content": "TOOL create_order RESULT: "
                        + json.dumps(result, ensure_ascii=False),
                    }
                )
                if not result.get("ok"):
                    # Let the LLM repair (missing fields / validation error).
                    continue
                # Loop again so the agent emits the final confirmation message.
                continue

            if tool == "cancel_order":
                result = self._execute_cancel_order()
                self.notes.append(
                    {
                        "role": "system",
                        "content": "TOOL cancel_order RESULT: "
                        + json.dumps(result, ensure_ascii=False),
                    }
                )
                if not result.get("ok"):
                    # Let the LLM relay the failure and decide what to do next.
                    continue
                # Loop again so the agent emits the final cancellation message.
                continue

            if tool == "respond":
                last_response = message
                self.history.append(ConversationMessage(role="assistant", content=message))
                return last_response

            # Unknown tool / malformed -> ask the model to fix.
            self.notes.append(
                {
                    "role": "system",
                    "content": "TOOL ERROR: expected 'respond' or 'create_order'.",
                }
            )

        if last_response is None:
            last_response = "Sorry, I could not complete that. Could you try again?"
        return last_response

    # ---- helpers ----------------------------------------------------------- #
    @staticmethod
    def _merge_order(draft: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(draft)
        for key, value in update.items():
            if key == "delivery_address" and isinstance(value, dict):
                da = dict(merged.get("delivery_address") or {})
                for sub, sub_value in value.items():
                    if sub_value is not None:
                        da[sub] = sub_value
                merged["delivery_address"] = da
            elif value is not None:
                merged[key] = value
        return merged

    def _build_messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self._system}]
        for message in self.history:
            messages.append({"role": message.role, "content": message.content})
        messages.extend(self.notes)  # internal tool/error feedback for the LLM
        messages.append(
            {
                "role": "system",
                "content": "CURRENT ORDER DRAFT (your working memory; update via the "
                "order field):\n"
                + json.dumps(self.draft, ensure_ascii=False, indent=2),
            }
        )
        return messages

    def _call_llm(self) -> dict[str, Any] | None:
        try:
            response = chat(
                model=self.model,
                messages=self._build_messages(),
                format=_ENVELOPE_SCHEMA,
                think=False,
                options={"temperature": 0},
            )
        except Exception as exc:  # noqa: BLE001 - keep the conversation alive
            self.notes.append(
                {
                    "role": "system",
                    "content": f"TOOL ERROR: LLM call failed: {exc}",
                }
            )
            return None

        content = response.message.content
        if content is None or not content.strip():
            return None
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            self.notes.append(
                {
                    "role": "system",
                    "content": "TOOL ERROR: response was not valid JSON. Reply with the "
                    "required JSON envelope only.",
                }
            )
            return None
        if not isinstance(parsed, dict) or "tool" not in parsed:
            return None
        return parsed

    def _sanitize_full(self, full: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        da_raw = full.get("delivery_address") or {}
        clean["delivery_address"] = {
            sub: da_raw.get(sub) for sub in FinalDeliveryAddress.model_fields
        }
        for key in FinalOrderSnapshot.model_fields:
            if key == "delivery_address":
                continue
            clean[key] = full.get(key)
        return clean

    def _missing_fields(self, full: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for field in REQUIRED_CUSTOMER_FIELDS:
            if field.startswith("delivery_address."):
                sub = field.split(".", 1)[1]
                value = (full.get("delivery_address") or {}).get(sub)
            else:
                value = full.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        return missing

    def _validate_room(self, room_size: str, table_size: str) -> str:
        """Return 'SUITABLE' or 'UNSUITABLE' for the given table size."""
        match = re.fullmatch(
            r"\s*([0-9]+(?:\.[0-9]+)?)\s*m\s*[x×]\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*m\s*",
            room_size or "",
        )
        if not match:
            return "UNSUITABLE"
        dims = sorted((Decimal(v) for v in match.groups()), reverse=True)
        mins = _MIN_ROOM_M.get(table_size)
        if mins is None:
            return "SUITABLE"  # no documented constraint for this size
        return "SUITABLE" if dims[0] >= mins[0] and dims[1] >= mins[1] else "UNSUITABLE"

    def _price_and_validate(self, full: dict[str, Any]) -> dict[str, Any]:
        """Deterministically check configuration + room and compute all prices.

        Returns {"ok": True, <computed fields>} or {"ok": False, "errors": [...]}
        with human-readable, actionable messages for the LLM to relay.
        """
        catalog = _load_catalog()
        errors: list[str] = []

        product_model = full.get("product_model")
        table_size = full.get("table_size")
        base = None
        if product_model and table_size:
            for record in catalog:
                if (
                    record.get("category") == "Table Design Model"
                    and record.get("product_model") == product_model
                    and record.get("table_size") == table_size
                ):
                    base = record
                    break
            if base is None:
                models = sorted(
                    {r["product_model"] for r in catalog if r["category"] == "Table Design Model"}
                )
                errors.append(
                    f"Unknown product_model/table_size pair: '{product_model}' / "
                    f"'{table_size}'. product_model must be one of {models}; "
                    f"table_size must be 6ft/7ft/8ft/9ft."
                )
        else:
            errors.append("Both product_model and table_size are required.")

        for field, category in _OPTION_CATEGORY_BY_FIELD.items():
            value = full.get(field)
            if value in (None, ""):
                continue
            titles = {r["title"] for r in catalog if r.get("category") == category}
            if value not in titles:
                errors.append(
                    f"{field} '{value}' is not offered. Allowed values: {sorted(titles)}."
                )

        if table_size and full.get("room_size"):
            if self._validate_room(full["room_size"], table_size) != "SUITABLE":
                errors.append(
                    f"Room size {full['room_size']!r} is UNSUITABLE for {table_size}."
                )

        if errors:
            return {"ok": False, "errors": errors}

        base_price = Decimal(base["price"])
        customisation_price = Decimal(0)
        for field, category in _OPTION_CATEGORY_BY_FIELD.items():
            value = full.get(field)
            if value in (None, ""):
                continue
            for record in catalog:
                if record.get("category") == category and record["title"] == value:
                    customisation_price += Decimal(record["price"])
                    break

        unit_price = base_price + customisation_price
        quantity = int(full.get("quantity") or 1)
        postcode = (full.get("delivery_address") or {}).get("postcode")
        zone = _POSTCODE_ZONES.get(postcode)
        shipping_cost = Decimal(0)
        if zone and table_size:
            rate = _SHIPPING_RATES.get((int(table_size[:-2]), zone))
            if rate is not None:
                shipping_cost = rate * quantity
        total_price = unit_price * quantity + shipping_cost

        return {
            "ok": True,
            "product_sku": base["sku"],
            "unit_price": unit_price,
            "customisation_price": customisation_price,
            "shipping_cost": shipping_cost,
            "total_price": total_price,
            "room_size_validation_result": "SUITABLE",
        }

    def _execute_create_order(self, order_update: dict[str, Any]) -> dict[str, Any]:
        full = self._merge_order(self.draft, order_update)

        missing = self._missing_fields(full)
        if missing:
            return {
                "ok": False,
                "error": "Order is missing required customer fields.",
                "missing": missing,
            }

        computed = self._price_and_validate(full)
        if not computed["ok"]:
            return {
                "ok": False,
                "error": "Order configuration or room validation failed.",
                "details": computed["errors"],
            }

        # The tool fills every system-computed field; the LLM need not.
        full.update({key: value for key, value in computed.items() if key != "ok"})

        clean = self._sanitize_full(full)
        try:
            snapshot = FinalOrderSnapshot.model_validate(clean)
        except Exception as exc:  # noqa: BLE001 - surface to the LLM to repair
            return {"ok": False, "error": f"Order validation failed: {exc}"}

        try:
            result = create_order(
                workflow_id=self.workflow_id,
                conversation_id=self.conversation_id,
                confirmed_snapshot=snapshot,
                store_path=self.order_store_path,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Order persistence failed: {exc}"}

        self.created_order_id = result.record.order_id
        self.finished = True
        return {
            "ok": True,
            "order_id": result.record.order_id,
            "order_status": result.record.order_status,
            "created": result.created,
        }

    def _execute_cancel_order(self) -> dict[str, Any]:
        """Cancel the order flow (or an already-placed order) deterministically.

        If an order was already created, its persisted status is flipped to
        CANCELLED; otherwise the flow simply ends. No string matching is used —
        the decision comes from the explicit ``cancel_order`` tool call.
        """
        if self.created_order_id is None:
            self.cancelled = True
            self.finished = True
            return {
                "ok": True,
                "cancelled": True,
                "order_id": None,
                "note": "No order had been placed; the conversation is cancelled.",
            }
        try:
            result = cancel_order(
                workflow_id=self.workflow_id,
                store_path=self.order_store_path,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Order cancellation failed: {exc}"}
        self.cancelled = True
        self.finished = True
        return {
            "ok": True,
            "cancelled": True,
            "order_id": result.record.order_id,
            "order_status": result.record.order_status,
        }
