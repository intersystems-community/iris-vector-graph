"""Unit tests for spec 217 — NodeNotFoundError and auto_stub_missing_nodes."""
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.ledger.changeset import Changeset
from iris_vector_graph.ledger.errors import LedgerError


class TestNodeNotFoundError:

    def test_node_not_found_error_exists_and_is_ledger_error(self):
        """T002: NodeNotFoundError is a subclass of LedgerError."""
        from iris_vector_graph.ledger.errors import NodeNotFoundError
        assert issubclass(NodeNotFoundError, LedgerError)

    def test_node_not_found_error_has_missing_node_attribute(self):
        """T001 (attr): NodeNotFoundError has .missing_node."""
        from iris_vector_graph.ledger.errors import NodeNotFoundError
        err = NodeNotFoundError("msg", missing_node="B")
        assert err.missing_node == "B"

    def test_node_not_found_exported_from_ledger_init(self):
        """T005: NodeNotFoundError exported from iris_vector_graph.ledger."""
        from iris_vector_graph import ledger as ledger_pkg
        assert hasattr(ledger_pkg, "NodeNotFoundError")

    def test_commit_raises_node_not_found_on_pattern(self):
        """T001: commit() raises NodeNotFoundError when response has node_not_found."""
        from iris_vector_graph.ledger.errors import NodeNotFoundError
        from iris_vector_graph.ledger.client import GraphLedger

        eng = MagicMock()
        eng.conn = MagicMock()
        eng.namespace = "USER"
        # Avoid JSON serialization of MagicMock in _call_json path
        eng._schema_prefix = "Graph_KG"
        gl = GraphLedger(eng)

        # Patch the low-level _call method to return a wire failure response
        fake_wire = '{"ok":false,"error":"failed_op","reason":"node_not_found: \'target-node-B\'"}'
        with patch.object(gl, "_call", return_value=fake_wire):
            cs = Changeset(actor="test", actor_type="test")
            cs.create_node("source-node-A")
            with pytest.raises(NodeNotFoundError) as exc_info:
                gl.commit(cs)
            assert exc_info.value.missing_node == "target-node-B"
            assert "target-node-B" in str(exc_info.value)


class TestAutoStubMissingNodes:

    def test_auto_stub_false_no_extra_ops(self):
        """Default: create_relationship does not prepend upsert_node ops."""
        cs = Changeset(actor="test", actor_type="test", auto_stub_missing_nodes=False)
        cs.create_relationship("A", "CALLS", "B")
        op_kinds = [op["op"] for op in cs.ops]
        assert "create_rel" in op_kinds or "upsert_rel" in op_kinds
        # No upsert_node stubs prepended
        assert op_kinds[0] in ("create_rel", "upsert_rel")

    def test_auto_stub_true_prepends_upsert_for_both_endpoints(self):
        """T007: auto_stub=True prepends upsert_node(A) and upsert_node(B)."""
        cs = Changeset(actor="test", actor_type="test", auto_stub_missing_nodes=True)
        cs.create_relationship("A", "CALLS", "B")
        ops = cs.ops
        op_kinds = [op["op"] for op in ops]
        # First ops should be upsert_node for A and B
        node_ids_in_stubs = [
            op["id"] for op in ops if op["op"] == "upsert_node"
        ]
        assert "A" in node_ids_in_stubs
        assert "B" in node_ids_in_stubs
        # Relationship op present
        assert any(op["op"] in ("create_rel", "upsert_rel") for op in ops)

    def test_auto_stub_skips_existing_node_op(self):
        """T008: existing create_node('B') prevents duplicate stub for B."""
        cs = Changeset(actor="test", actor_type="test", auto_stub_missing_nodes=True)
        cs.create_node("B")
        cs.create_relationship("A", "CALLS", "B")
        # Only one op for B (the original create_node, not an extra upsert_node)
        b_ops = [op for op in cs.ops if op.get("id") == "B"]
        assert len(b_ops) == 1
        assert b_ops[0]["op"] == "create_node"

    def test_auto_stub_skips_existing_upsert_node_op(self):
        """T008 variant: existing upsert_node also prevents duplicate."""
        cs = Changeset(actor="test", actor_type="test", auto_stub_missing_nodes=True)
        cs.upsert_node("B")
        cs.create_relationship("A", "CALLS", "B")
        b_ops = [op for op in cs.ops if op.get("id") == "B"]
        # Should have exactly 1 op for B — the original upsert_node
        assert len(b_ops) == 1
