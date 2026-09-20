"""JSON-backed MVP create_order adapter for ORDER_CREATE_WF.

This local store simulates the future ATS Order Management System/API. Runtime
demo execution may write to data/orders.json. The Stage 13 MVP assumes a single
process/single writer; it is idempotent for sequential retries, not concurrent
multi-process writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict

from entity.order_creation_state import FinalOrderSnapshot
from entity.workflow_state import current_utc_time

from entity.order_record import ORDER_ID_PATTERN, OrderCreationRecord

DEFAULT_ORDER_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "orders.json"
ORDER_STATUS_CONFIRMED = "CONFIRMED"

class OrderStoreIntegrityError(RuntimeError):
    """Raised when persisted order data violates the MVP store contract."""

class OrderCommitResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")

    record: OrderCreationRecord
    created: bool

def _load_store(store_path: Path) -> list[OrderCreationRecord]:
    if not store_path.exists():
        raw = []
    else:
        try:
            raw = json.loads(store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise OrderStoreIntegrityError("Order store contains malformed JSON.") from exc
    if not isinstance(raw, list):
        raise OrderStoreIntegrityError("Order store must contain a JSON array.")
    try:
        records = []
        for record in raw:
            if not isinstance(record, dict):
                raise ValueError("Order store records must be JSON objects.")
            prepared = record.copy()
            prepared["order"] = FinalOrderSnapshot.model_validate_json(json.dumps(record.get("order")))
            records.append(OrderCreationRecord.model_validate(prepared))
    except ValueError as exc:
        raise OrderStoreIntegrityError("Order store record violates schema.") from exc
    workflow_ids = [record.source_workflow_id for record in records]
    order_ids = [record.order_id for record in records]
    if len(workflow_ids) != len(set(workflow_ids)):
        raise OrderStoreIntegrityError("Order store contains duplicate source_workflow_id values.")
    if len(order_ids) != len(set(order_ids)):
        raise OrderStoreIntegrityError("Order store contains duplicate order_id values.")
    return records

def _next_order_id(records: list[OrderCreationRecord]) -> str:
    highest = 0
    for record in records:
        match = ORDER_ID_PATTERN.fullmatch(record.order_id)
        if match is None:
            raise OrderStoreIntegrityError("Order store contains a malformed order_id.")
        highest = max(highest, int(match.group(1)))
    return f"ORD-{highest + 1:06d}"

def _serialize_records(records: list[OrderCreationRecord]) -> str:
    payload = [json.loads(record.model_dump_json()) for record in records]
    return json.dumps(payload, indent=2) + "\n"

def _atomic_write_store(store_path: Path, records: list[OrderCreationRecord]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=store_path.parent, prefix=store_path.name + ".", delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(_serialize_records(records))
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, store_path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass

def create_order(
    *,
    workflow_id: str,
    conversation_id: str,
    confirmed_snapshot: FinalOrderSnapshot,
    store_path: Path = DEFAULT_ORDER_STORE_PATH,
) -> OrderCommitResult:
    """Commit or replay exactly one order for a workflow idempotency key."""
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("workflow_id must be a non-blank string.")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id must be a non-blank string.")
    snapshot = FinalOrderSnapshot.model_validate(confirmed_snapshot)
    records = _load_store(Path(store_path))
    for record in records:
        if record.source_workflow_id == workflow_id:
            if record.order != snapshot:
                raise OrderStoreIntegrityError(
                    "Workflow already committed with a different confirmed order snapshot."
                )
            return OrderCommitResult(record=record, created=False)

    record = OrderCreationRecord(
        order_id=_next_order_id(records),
        source_workflow_id=workflow_id,
        conversation_id=conversation_id,
        created_at=current_utc_time(),
        order_status=ORDER_STATUS_CONFIRMED,
        order=snapshot,
    )
    _atomic_write_store(Path(store_path), records + [record])
    committed = _load_store(Path(store_path))
    for stored_record in committed:
        if stored_record.source_workflow_id == workflow_id:
            if stored_record != record:
                raise OrderStoreIntegrityError("Committed order record does not match requested record.")
            return OrderCommitResult(record=stored_record, created=True)
    raise OrderStoreIntegrityError("Committed order record was not found after write.")
