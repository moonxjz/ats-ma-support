"""Temporary-file tests for the Stage 13 JSON create_order adapter."""

from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from workflow.order.order_creation_order_store import (
    DEFAULT_ORDER_STORE_PATH,
    ORDER_STATUS_CONFIRMED,
    OrderCreationRecord,
    OrderStoreIntegrityError,
    create_order,
)
from tests.test_order_creation_confirmation import final_waiting_state


class OrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_path = Path(self.tmp.name) / "orders.json"
        self.state = final_waiting_state()
        self.snapshot = self.state.final_order_snapshot

    def read_store(self):
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def create(self, **kwargs):
        return create_order(
            workflow_id=kwargs.get("workflow_id", self.state.workflow_id),
            conversation_id=kwargs.get("conversation_id", self.state.conversation_id),
            confirmed_snapshot=kwargs.get("snapshot", self.snapshot),
            store_path=kwargs.get("store_path", self.store_path),
        )

    def test_empty_store_commits_first_order_with_exact_snapshot(self):
        with patch("workflow.order.order_creation_order_store.current_utc_time", return_value="2026-09-08T00:00:00+00:00"):
            result = self.create()
        self.assertTrue(result.created)
        self.assertEqual(result.record.order_id, "ORD-000001")
        self.assertEqual(result.record.source_workflow_id, self.state.workflow_id)
        self.assertEqual(result.record.conversation_id, self.state.conversation_id)
        self.assertEqual(result.record.created_at, "2026-09-08T00:00:00+00:00")
        self.assertEqual(result.record.order_status, ORDER_STATUS_CONFIRMED)
        self.assertEqual(result.record.order, self.snapshot)
        raw = self.read_store()
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["order"], self.snapshot.model_dump(mode="json"))
        self.assertIsInstance(raw[0]["order"]["shipping_cost"], str)
        self.assertEqual(OrderCreationRecord.model_validate_json(json.dumps(raw[0])).order.shipping_cost, Decimal("530"))

    def test_new_address_round_trip_and_legacy_payload_rejection(self):
        from entity.order_creation_state import DeliveryAddress, OrderCreationState, FinalDeliveryAddress
        from workflow.order.order_creation_updates import ExtractedDeliveryAddress
        from agents.support_agent import render_required_input_request
        self.create()
        original = self.read_store()
        address = original[0]['order']['delivery_address']
        self.assertEqual(set(address), {'address', 'city', 'state', 'postcode', 'country'})
        self.assertEqual(address['address'], '1 Example Street')
        self.assertEqual(self.create().record.order.delivery_address.model_dump(), address)
        # Intentional legacy-contract rejection coverage; no aliases or conversion.
        for old_key in ('address_line_1', 'address_line_2'):
            with self.subTest(old_key=old_key):
                legacy = {**address, old_key: 'Unit A'}
                for model in (DeliveryAddress, FinalDeliveryAddress, ExtractedDeliveryAddress):
                    with self.assertRaises(ValidationError):
                        model.model_validate(legacy)
                with self.assertRaises(ValidationError):
                    OrderCreationState(conversation_id='legacy', delivery_address=legacy)
                with self.assertRaises(ValueError):
                    render_required_input_request(['delivery_address.' + old_key])
                raw = json.loads(json.dumps(original))
                raw[0]['order']['delivery_address'] = legacy
                encoded = json.dumps(raw)
                self.store_path.write_text(encoded)
                with self.assertRaises(OrderStoreIntegrityError):
                    self.create()
                self.assertEqual(self.store_path.read_text(), encoded)
        self.store_path.write_text(json.dumps(original))

    def test_sequence_uses_max_valid_id_not_record_count(self):
        first = self.create(workflow_id="WF-ONE")
        second = self.create(workflow_id="WF-TWO")
        raw = self.read_store()
        raw.pop(0)
        self.store_path.write_text(json.dumps(raw), encoding="utf-8")
        third = self.create(workflow_id="WF-THREE")
        self.assertEqual([second.record.order_id, third.record.order_id], ["ORD-000002", "ORD-000003"])

    def test_idempotent_replay_returns_original_record_unchanged(self):
        with patch("workflow.order.order_creation_order_store.current_utc_time", return_value="2026-09-08T00:00:00+00:00"):
            first = self.create()
        with patch("workflow.order.order_creation_order_store.current_utc_time", return_value="2026-09-09T00:00:00+00:00"):
            replay = self.create()
        self.assertFalse(replay.created)
        self.assertEqual(replay.record, first.record)
        self.assertEqual(len(self.read_store()), 1)

    def test_same_workflow_different_snapshot_raises_and_preserves_store(self):
        self.create()
        before = self.store_path.read_text(encoding="utf-8")
        changed = self.snapshot.model_copy(update={"phone": "0499999999"})
        with self.assertRaises(OrderStoreIntegrityError):
            self.create(snapshot=changed)
        self.assertEqual(self.store_path.read_text(encoding="utf-8"), before)

    def test_store_integrity_failures(self):
        valid = {
            "order_id": "ORD-000001",
            "source_workflow_id": "WF-1",
            "conversation_id": "C-1",
            "created_at": "2026-09-08T00:00:00+00:00",
            "order_status": "CONFIRMED",
            "order": self.snapshot.model_dump(mode="json"),
        }
        cases = [
            "not json",
            json.dumps({"records": []}),
            json.dumps([{**valid, "order_id": "BAD-1"}]),
            json.dumps([{**valid, "created_at": "2026-09-08T00:00:00"}]),
            json.dumps([{**valid, "extra": True}]),
            json.dumps([valid, {**valid, "order_id": "ORD-000002"}]),
            json.dumps([valid, {**valid, "source_workflow_id": "WF-2"}]),
        ]
        for payload in cases:
            with self.subTest(payload=payload[:40]):
                self.store_path.write_text(payload, encoding="utf-8")
                with self.assertRaises(OrderStoreIntegrityError):
                    self.create(workflow_id="WF-NEW")

    def test_invalid_inputs_and_snapshot_rejected(self):
        for kwargs in ({"workflow_id": ""}, {"conversation_id": "  "}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.create(**kwargs)
        with self.assertRaises(ValidationError):
            self.create(snapshot=self.snapshot.model_dump(mode="json") | {"quantity": 0})

    def test_atomic_replace_failure_does_not_report_success_or_replace_store(self):
        self.store_path.write_text("[]", encoding="utf-8")
        with patch("workflow.order.order_creation_order_store.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(self.store_path.read_text(encoding="utf-8"), "[]")

    def test_default_runtime_store_is_not_used_by_tests(self):
        self.assertNotEqual(self.store_path, DEFAULT_ORDER_STORE_PATH)


if __name__ == "__main__":
    unittest.main()
