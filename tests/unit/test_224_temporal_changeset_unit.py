"""Unit tests for spec-224 — Temporal Ops in Changeset (Phase 1).

No container required. Tests verify the Changeset op dict shape and wire format.
"""
import json
import pytest
from iris_vector_graph.ledger.changeset import Changeset


class TestCreateTemporalEdge:

    def test_creates_op_in_changeset(self):
        """T001: create_temporal_edge appends exactly one op."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("svc-a", "CALLS", "svc-b", ts=1000, weight=0.5)
        assert len(cs.ops) == 1
        assert cs.ops[0]["op"] == "create_temporal_edge"

    def test_op_dict_shape(self):
        """T002: op dict has tuple, ts (int), weight (float), mode (str)."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("svc-a", "CALLS", "svc-b", ts=1000, weight=0.5)
        op = cs.ops[0]
        assert "tuple" in op
        t = op["tuple"]
        assert set(t.keys()) >= {"s", "p", "o", "graph"}
        assert t["s"] == "svc-a"
        assert t["p"] == "CALLS"
        assert t["o"] == "svc-b"
        assert isinstance(op["ts"], int)
        assert isinstance(op["weight"], float)
        assert isinstance(op["mode"], str)

    def test_default_mode_is_update(self):
        """T003: no mode arg → mode="update" (idempotent last-write-wins)."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("s", "P", "o", ts=1)
        assert cs.ops[0]["mode"] == "update"

    def test_graph_none_stored_as_none(self):
        """T004: graph=None → tuple["graph"] is None."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("s", "P", "o", ts=1)
        assert cs.ops[0]["tuple"]["graph"] is None

    def test_graph_value_stored(self):
        """T004b: graph="acme" → tuple["graph"] == "acme"."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("s", "P", "o", ts=1, graph="acme")
        assert cs.ops[0]["tuple"]["graph"] == "acme"

    def test_mixed_structural_and_temporal_ops(self):
        """T005: create_node + create_temporal_edge → 2 ops in insertion order."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_node("svc-a")
        cs.create_temporal_edge("svc-a", "CALLS", "svc-b", ts=1000, weight=0.5)
        assert len(cs.ops) == 2
        assert cs.ops[0]["op"] == "create_node"
        assert cs.ops[1]["op"] == "create_temporal_edge"

    def test_wire_format_is_json_serializable(self):
        """T006: to_wire() includes the temporal op; result is JSON-serializable."""
        cs = Changeset(actor="test", actor_type="test")
        cs.create_temporal_edge("s", "P", "o", ts=1000, weight=0.7, graph="acme",
                                 attrs={"latency": "42"}, mode="insert")
        wire = cs.to_wire()
        assert any(op["op"] == "create_temporal_edge" for op in wire["ops"])
        serialized = json.dumps(wire)  # must not raise
        back = json.loads(serialized)
        te_op = next(op for op in back["ops"] if op["op"] == "create_temporal_edge")
        assert te_op["tuple"]["graph"] == "acme"
        assert te_op["weight"] == 0.7
        assert te_op["attrs"] == {"latency": "42"}
        assert te_op["mode"] == "insert"

    def test_returns_opref(self):
        """create_temporal_edge returns an OpRef pointing at the correct index."""
        from iris_vector_graph.ledger.changeset import OpRef
        cs = Changeset(actor="test", actor_type="test")
        ref = cs.create_temporal_edge("s", "P", "o", ts=1)
        assert isinstance(ref, OpRef)
        assert ref.index == 0
