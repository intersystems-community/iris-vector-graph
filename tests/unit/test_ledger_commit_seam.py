"""Unit tests for spec-arch-3 — GraphLedger._send_changeset transport seam.

All tests use mocks — no container required.
"""
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call
import pytest

from iris_vector_graph.ledger.changeset import Changeset
from iris_vector_graph.ledger.client import GraphLedger, CommitResult


def _make_ledger():
    eng = MagicMock()
    eng.conn = MagicMock()
    eng._schema_prefix = "Graph_KG"
    eng.namespace = "USER"
    eng._nkg_dirty = False
    return GraphLedger(eng), eng


class TestSendChangesetExists:
    def test_send_changeset_method_exists(self):
        gl, _ = _make_ledger()
        assert hasattr(gl, "_send_changeset")
        assert callable(gl._send_changeset)

    def test_commit_wire_result_dataclass_exists(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        assert _CommitWireResult is not None


class TestSendChangesetBehaviour:
    def _cs(self):
        cs = Changeset(actor="test", actor_type="test")
        cs.upsert_node("n1")
        return cs

    def test_send_changeset_returns_wire_result_on_success(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        gl, _ = _make_ledger()
        success = json.dumps({
            "ok": True,
            "revision": {"seq": 1, "revision_id": "abc123", "parent_id": None,
                         "kind": "changeset", "actor": "test", "actor_type": "test",
                         "conn_user": "_SYSTEM", "committed_ms": 1000,
                         "message": None, "source": None, "correlation_id": None,
                         "idempotency_key": None, "op_count": 1},
            "replayed": False,
            "stmt_ids": {},
        })
        with patch.object(gl, "_call", return_value=success):
            result = gl._send_changeset(self._cs())
        assert isinstance(result, _CommitWireResult)
        assert result.ok is True
        assert result.revision_id == "abc123"
        assert result.replayed is False

    def test_send_changeset_handles_failed_op(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        gl, _ = _make_ledger()
        failure = json.dumps({"ok": False, "error": "failed_op", "reason": "node_not_found: 'x'"})
        with patch.object(gl, "_call", return_value=failure):
            result = gl._send_changeset(self._cs())
        assert isinstance(result, _CommitWireResult)
        assert result.ok is False
        assert result.error_code == "failed_op"
        assert result.reason == "node_not_found: 'x'"

    def test_send_changeset_handles_non_json(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        gl, _ = _make_ledger()
        with patch.object(gl, "_call", return_value="not-json-garbage"):
            result = gl._send_changeset(self._cs())
        assert isinstance(result, _CommitWireResult)
        assert result.ok is False
        assert result.error_code == "inconsistent"

    def test_send_changeset_raises_nothing(self):
        """_send_changeset must not raise — all errors go into the result."""
        gl, _ = _make_ledger()
        with patch.object(gl, "_call", side_effect=RuntimeError("network down")):
            # Should not raise — errors become ok=False
            try:
                result = gl._send_changeset(self._cs())
                assert result.ok is False
            except RuntimeError:
                pytest.fail("_send_changeset must not propagate exceptions")


class TestCommitCallsSendChangeset:
    def _cs(self):
        cs = Changeset(actor="test", actor_type="test")
        cs.upsert_node("n1")
        return cs

    def test_commit_delegates_to_send_changeset(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        gl, eng = _make_ledger()
        wire = _CommitWireResult(
            ok=True, revision_id="rev1", replayed=False, stmt_ids={},
            error_code=None, reason=None,
            raw_resp={"ok": True, "revision": {
                "seq": 1, "revision_id": "rev1", "parent_id": None,
                "kind": "changeset", "actor": "test", "actor_type": "test",
                "conn_user": "_SYSTEM", "committed_ms": 1000,
                "message": None, "source": None, "correlation_id": None,
                "idempotency_key": None, "op_count": 1,
            }, "replayed": False, "stmt_ids": {}},
        )
        with patch.object(gl, "_send_changeset", return_value=wire) as mock_send:
            result = gl.commit(self._cs())
        mock_send.assert_called_once()
        assert isinstance(result, CommitResult)

    def test_nkg_dirty_not_set_on_transport_failure(self):
        from iris_vector_graph.ledger.client import _CommitWireResult
        gl, eng = _make_ledger()
        wire = _CommitWireResult(
            ok=False, revision_id=None, replayed=False, stmt_ids={},
            error_code="failed_op", reason="node_not_found: 'x'",
            raw_resp={"ok": False, "error": "failed_op", "reason": "node_not_found: 'x'"},
        )
        with patch.object(gl, "_send_changeset", return_value=wire):
            with pytest.raises(Exception):
                gl.commit(self._cs())
        # Engine's _nkg_dirty must not have been set to True
        assert eng._nkg_dirty is False
