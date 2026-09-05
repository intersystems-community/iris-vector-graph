"""Spec 213 — unit tests for iris_vector_graph.ledger.client transport (no IRIS required).

T008: staging/chunking, CHUNKED reassembly, meta contents, enable→adoption, one Commit per commit().
T037 (TestPreValidation) and T043 (TestHeadContract) extend this module.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.ledger import client as CL
from iris_vector_graph.ledger.changeset import Changeset
from iris_vector_graph.ledger.errors import (
    ChangesetOperationError,
    EmptyChangesetError,
    StaleHeadError,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

REV = {
    "seq": 2,
    "revision_id": "a" * 32,
    "parent_id": "b" * 32,
    "kind": "changeset",
    "actor": "a",
    "actor_type": "human",
    "conn_user": "_SYSTEM",
    "committed_ms": 1_700_000_000_000,
    "message": None,
    "source": None,
    "correlation_id": None,
    "idempotency_key": None,
    "op_count": 1,
}


class FakeServer:
    """Records classMethodValue calls and returns scripted responses for Graph.KG.Ledger."""

    def __init__(self, commit_response=None, head_response=None, enable_response=None):
        self.calls = []
        self.staged = {}
        self.large_out = {}
        self.commit_response = commit_response or {
            "ok": True,
            "revision": REV,
            "replayed": False,
            "stmt_ids": {},
        }
        self.head_response = head_response or {"ok": True, "revision": REV}
        self.enable_response = enable_response or {
            "ok": True,
            "revision": REV,
            "adoption_needed": False,
        }
        self.meta_response = {
            "ok": True,
            "state": "enabled",
            "strict": False,
            "max_ops": 50000,
            "recon_bound": 250000,
            "head_seq": 2,
            "head_id": "a" * 32,
        }
        self.verify_adopt_calls = 0

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if cls == "%SYSTEM.OBJ" and method == "Exists":
            return 1
        if method == "StageChunk":
            token, idx, chunk = args
            self.staged.setdefault(token, {})[int(idx)] = chunk
            return 1
        if method == "Commit":
            resp = self.commit_response
            if callable(resp):
                resp = resp(args, self)
            out = json.dumps(resp)
            if len(out) > CL.CHUNK_CHARS:
                n = 0
                for i in range(0, len(out), CL.CHUNK_CHARS):
                    n += 1
                    self.large_out[("T1", n)] = out[i : i + CL.CHUNK_CHARS]
                return f"CHUNKED:T1:{n}"
            return out
        if method == "ReadLargeOutChunk":
            tag, i = args
            return self.large_out[(tag, int(i))]
        if method == "Head":
            return json.dumps(self.head_response)
        if method == "Meta":
            return json.dumps(self.meta_response)
        if method == "Enable":
            return json.dumps(self.enable_response)
        if method == "Count":
            return 1
        return ""


def _engine(server):
    eng = MagicMock()
    eng._iris_obj.return_value = server
    eng._schema_prefix = "Graph_KG"
    eng._nkg_dirty = False
    return eng


def _cs(n_ops=1, **meta):
    cs = Changeset(actor="a", actor_type="human", **meta)
    for i in range(n_ops):
        cs.create_node(f"n{i}", properties={"k": "v" * 50})
    return cs


class TestTransport:
    def test_small_payload_passed_inline(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        res = led.commit(_cs())
        commits = [c for c in srv.calls if c[1] == "Commit"]
        assert len(commits) == 1
        payload, meta_json = commits[0][2]
        assert json.loads(payload)["ops"][0]["op"] == "create_node"
        meta = json.loads(meta_json)
        assert meta["staged"] is False
        assert not any(c[1] == "StageChunk" for c in srv.calls)
        assert res.revision.revision_id == "a" * 32 and res.replayed is False

    def test_large_payload_is_staged_in_order_then_committed(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        cs = _cs(n_ops=CL.CHUNK_CHARS // 40 + 10)  # comfortably > 1 chunk
        payload = cs.canonical_json()
        assert len(payload) > CL.CHUNK_CHARS
        led.commit(cs)
        stages = [c for c in srv.calls if c[1] == "StageChunk"]
        assert len(stages) >= 2
        idxs = [int(c[2][1]) for c in stages]
        assert idxs == list(range(1, len(stages) + 1))
        assert all(len(c[2][2]) <= CL.CHUNK_CHARS for c in stages)
        token = stages[0][2][0]
        assert "".join(srv.staged[token][i] for i in idxs) == payload
        commit = [c for c in srv.calls if c[1] == "Commit"][0]
        assert commit[2][0] == token and json.loads(commit[2][1])["staged"] is True
        # Commit follows all StageChunk calls
        assert srv.calls.index(commit) > srv.calls.index(stages[-1])

    def test_chunked_response_reassembled(self):
        big = dict(REV, message="x" * (CL.CHUNK_CHARS + 500))
        srv = FakeServer(
            commit_response={"ok": True, "revision": big, "replayed": False, "stmt_ids": {}}
        )
        led = CL.GraphLedger(_engine(srv))
        res = led.commit(_cs())
        assert res.revision.message == big["message"]
        reads = [c for c in srv.calls if c[1] == "ReadLargeOutChunk"]
        assert [int(c[2][1]) for c in reads] == list(range(1, len(reads) + 1))

    def test_meta_contents(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        cs = _cs(expected_head="c" * 32, idempotency_key="K1")
        led.commit(cs)
        meta = json.loads([c for c in srv.calls if c[1] == "Commit"][0][2][1])
        assert meta["fingerprint"] == cs.fingerprint()
        assert meta["expected_head"] == "c" * 32
        assert meta["idempotency_key"] == "K1"
        assert meta["kind"] == "changeset"
        assert meta["schema"] == "Graph_KG"

    def test_exactly_one_commit_call_and_no_retry_on_error(self):
        srv = FakeServer(
            commit_response={"ok": False, "error": "stale_head", "reason": "s", "head": "d" * 32}
        )
        led = CL.GraphLedger(_engine(srv))
        with pytest.raises(StaleHeadError) as ei:
            led.commit(_cs(expected_head="c" * 32))
        assert ei.value.current_head == "d" * 32
        assert len([c for c in srv.calls if c[1] == "Commit"]) == 1

    def test_enable_runs_adoption_once_when_needed(self):
        srv = FakeServer(enable_response={"ok": True, "revision": REV, "adoption_needed": True})
        led = CL.GraphLedger(_engine(srv))
        adopted = dict(REV, seq=3, kind="adoption")
        led.verify = MagicMock(
            return_value=MagicMock(adoption_revision=CL.RevisionInfo.from_wire(adopted))
        )
        res = led.enable()
        led.verify.assert_called_once_with(adopt=True)
        assert res.seq == 3

    def test_enable_without_adoption_does_not_verify(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        led.verify = MagicMock()
        res = led.enable()
        led.verify.assert_not_called()
        assert res.seq == 2


class TestPreValidation:
    def test_empty_changeset_rejected_before_server(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        with pytest.raises(EmptyChangesetError):
            led.commit(Changeset(actor="a", actor_type="human"))
        assert not any(c[1] == "Commit" for c in srv.calls)

    def test_bad_op_ref_rejected_before_server(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        cs = Changeset(actor="a", actor_type="human")
        cs.create_node("n")
        from iris_vector_graph.ledger.changeset import OpRef

        cs.set_qualifier(OpRef(9), "k", "v")
        with pytest.raises(ChangesetOperationError) as ei:
            led.commit(cs)
        assert ei.value.op_index == 1
        assert not any(c[1] == "Commit" for c in srv.calls)


class TestHeadContract:
    def test_expected_head_passed_unchanged(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        led.commit(_cs(expected_head="e" * 32))
        meta = json.loads([c for c in srv.calls if c[1] == "Commit"][0][2][1])
        assert meta["expected_head"] == "e" * 32

    def test_head_reads_once_and_never_commits(self):
        srv = FakeServer()
        led = CL.GraphLedger(_engine(srv))
        h = led.head()
        assert h.revision_id == "a" * 32
        assert [c[1] for c in srv.calls if c[0] == "Graph.KG.Ledger"] == ["Head"]
