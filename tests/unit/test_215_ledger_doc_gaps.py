"""Unit tests for spec 215 — DiffEntry.rel_info property and Changeset.fingerprint() docstring."""
import json
import pytest
from iris_vector_graph.ledger.replay import DiffEntry
from iris_vector_graph.ledger.changeset import Changeset


# ── Phase 1: DiffEntry.rel_info ────────────────────────────────────────────

class TestDiffEntryRelInfo:

    def test_rel_info_returns_dict_for_create_rel(self):
        """T001: create_rel entry — after holds TupleJson → rel_info parsed."""
        entry = DiffEntry(
            entity_kind="rel",
            entity_id="42",
            attr="",
            before=None,
            after='{"s":"A","p":"CALLS","o":"B","graph":""}',
        )
        assert entry.rel_info == {"s": "A", "p": "CALLS", "o": "B", "graph": ""}

    def test_rel_info_uses_before_when_after_is_none(self):
        """T002: delete_rel entry — after is None, before holds TupleJson."""
        entry = DiffEntry(
            entity_kind="rel",
            entity_id="42",
            attr="",
            before='{"s":"A","p":"CALLS","o":"B","graph":"umls"}',
            after=None,
        )
        assert entry.rel_info is not None
        assert entry.rel_info["s"] == "A"
        assert entry.rel_info["graph"] == "umls"

    def test_rel_info_returns_none_for_node_entry(self):
        """T003: entity_kind != 'rel' → rel_info is None."""
        entry = DiffEntry(
            entity_kind="node",
            entity_id="node-123",
            attr="",
            before=None,
            after=None,
        )
        assert entry.rel_info is None

    def test_to_wire_unchanged_no_rel_info_key(self):
        """T004: to_wire() must NOT contain 'rel_info' — no wire format change."""
        entry = DiffEntry(
            entity_kind="rel",
            entity_id="42",
            attr="",
            before=None,
            after='{"s":"A","p":"CALLS","o":"B","graph":""}',
        )
        wire = entry.to_wire()
        assert "rel_info" not in wire
        assert set(wire.keys()) == {"entity_kind", "entity_id", "attr", "before", "after"}

    def test_rel_info_returns_none_when_both_none(self):
        """Extra: both after and before None → rel_info is None."""
        entry = DiffEntry(
            entity_kind="rel",
            entity_id="42",
            attr="",
            before=None,
            after=None,
        )
        assert entry.rel_info is None

    def test_rel_info_returns_none_on_invalid_json(self):
        """Extra: malformed after → rel_info is None (no exception)."""
        entry = DiffEntry(
            entity_kind="rel",
            entity_id="42",
            attr="",
            before=None,
            after="not-json",
        )
        assert entry.rel_info is None


# ── Phase 2: fingerprint scope ─────────────────────────────────────────────

class TestChangsetFingerprintScope:

    def test_fingerprint_same_with_different_expected_head(self):
        """T007: expected_head excluded from fingerprint."""
        cs1 = Changeset(actor="ingest", actor_type="ingest", expected_head="rev-aaa")
        cs2 = Changeset(actor="ingest", actor_type="ingest", expected_head="rev-bbb")
        cs1.create_node("node-1")
        cs2.create_node("node-1")
        assert cs1.fingerprint() == cs2.fingerprint()

    def test_fingerprint_differs_with_different_ops(self):
        """T008: different ops → different fingerprint."""
        cs1 = Changeset(actor="ingest", actor_type="ingest")
        cs2 = Changeset(actor="ingest", actor_type="ingest")
        cs1.create_node("node-A")
        cs2.create_node("node-B")
        assert cs1.fingerprint() != cs2.fingerprint()

    def test_fingerprint_same_with_different_message(self):
        """Extra: message excluded from fingerprint."""
        cs1 = Changeset(actor="ingest", actor_type="ingest", message="msg-1")
        cs2 = Changeset(actor="ingest", actor_type="ingest", message="msg-2")
        cs1.create_node("node-1")
        cs2.create_node("node-1")
        assert cs1.fingerprint() == cs2.fingerprint()

    def test_fingerprint_differs_with_different_actor(self):
        """Extra: actor IS in fingerprint."""
        cs1 = Changeset(actor="actor-A", actor_type="ingest")
        cs2 = Changeset(actor="actor-B", actor_type="ingest")
        cs1.create_node("node-1")
        cs2.create_node("node-1")
        assert cs1.fingerprint() != cs2.fingerprint()
