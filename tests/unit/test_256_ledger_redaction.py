"""Spec 256: redacting a tenant from the revision ledger. Unit tests, no IRIS needed.

Covers the Python side of the redaction: how tombstone rows are decoded, how replay, diff and
reconstruction skip them, the ``ledger.redact`` call and its receipt, the wire error codes, the
readers that honour the in-progress marker, and a source check that the one exemption from the
immutability trigger (ADR-0013) stays narrow.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.ledger import client as CL
from iris_vector_graph.ledger import replay as R
from iris_vector_graph.ledger.errors import (
    IdempotencyConflictError,
    IdempotencyRedactedError,
    InvalidRedactionTargetError,
    LedgerDisabledError,
    RedactionInProgressError,
    RedactionTargetLiveError,
    from_commit_error,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "iris_src" / "src" / "Graph" / "KG"

TUPLE = json.dumps({"graph": "g1", "o": "b", "p": "R", "s": "a"}, sort_keys=True, separators=(",", ":"))
DIGEST = "ab" * 32


def _row(seq, ordinal, op, kind, eid, attr="", prior=None, new=None, graph=None, flags="", digest=None, redacted_op=None):
    """A Graph_KG.ledger_records row as the spec 256 server returns it (14 columns)."""
    return (
        seq,
        ordinal,
        op,
        kind,
        eid,
        attr,
        "" if prior is None else prior,
        1 if prior is None else 0,
        "" if new is None else new,
        1 if new is None else 0,
        "" if graph is None else graph,
        flags,
        "" if digest is None else digest,
        "" if redacted_op is None else redacted_op,
    )


def _tomb(seq, ordinal, kind="node", op="create_node", flags=""):
    return _row(seq, ordinal, "redacted", kind, "", flags=flags, digest=DIGEST, redacted_op=op)


def _rec(row):
    return R.MutationRecord.from_row(row)


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #


class TestTombstoneDecode:
    def test_a_tombstone_row_decodes_as_redacted_with_its_digest_and_op(self):
        r = _rec(_tomb(3, 2, kind="rel", op="set_qual", flags="cascade"))
        assert r.redacted is True
        assert (r.seq, r.ordinal) == (3, 2)
        assert r.digest == DIGEST and r.redacted_op == "set_qual"
        assert r.entity_id == "" and r.attr == "" and r.prior is None and r.new is None
        assert r.graph is None and r.flags == ["cascade"]

    def test_a_live_row_of_the_new_shape_is_not_redacted(self):
        r = _rec(_row(1, 1, "create_node", "node", "n1", prior=None, new=""))
        assert r.redacted is False and r.digest is None and r.redacted_op is None

    def test_a_legacy_twelve_column_row_still_decodes(self):
        r = _rec(_row(1, 1, "create_node", "node", "n1", new="")[:12])
        assert r.redacted is False and r.entity_id == "n1" and r.digest is None

    def test_wire_round_trip_keeps_the_tombstone(self):
        r = _rec(_tomb(4, 1))
        back = R.MutationRecord.from_wire(r.to_wire())
        assert back == r and back.redacted

    def test_wire_form_of_a_live_record_has_no_redaction_keys(self):
        wire = _rec(_row(1, 1, "create_node", "node", "n1", new="")).to_wire()
        assert "digest" not in wire and "redacted_op" not in wire


# --------------------------------------------------------------------------- #
# replay, reconstruction, diff
# --------------------------------------------------------------------------- #


def _history_with_tombstones():
    return [
        _row(1, 1, "create_node", "node", "a", new=""),
        _tomb(1, 2, kind="node", op="create_node"),
        _row(1, 3, "create_rel", "rel", "1", new=TUPLE, graph="g1"),
        _tomb(1, 4, kind="rel", op="set_qual"),
        _row(2, 1, "set_prop", "node", "a", attr="k", prior=None, new="v"),
        # the redaction revision: one content-free record per redacted revision
        _row(3, 1, "redact", "revision", "1", new="2", flags="redaction"),
    ]


class TestReplaySkipsTombstones:
    def test_apply_ignores_a_tombstone(self):
        st = R.GraphState()
        st.apply(_rec(_tomb(1, 1, kind="node", op="create_node")))
        st.apply(_rec(_tomb(1, 2, kind="rel", op="create_rel")))
        assert st.nodes == {} and st.statements == {}

    def test_apply_ignores_a_redaction_revision_record_even_when_its_seq_names_a_statement(self):
        st = R.GraphState()
        st.apply(_rec(_row(1, 1, "create_rel", "rel", "1", new=TUPLE, graph="g1")))
        before = st.as_comparable()
        st.apply(_rec(_row(2, 1, "redact", "revision", "1", new="1", flags="redaction")))
        assert st.as_comparable() == before

    def test_reconstruct_equals_the_history_without_its_tombstones(self):
        rows = _history_with_tombstones()
        with_t = R.reconstruct((_rec(r) for r in rows), bound=None)
        without = R.reconstruct(
            (_rec(r) for r in rows if r[2] not in ("redacted", "redact")), bound=None
        )
        assert with_t.as_comparable() == without.as_comparable()
        assert set(with_t.nodes) == {"a"} and with_t.nodes["a"].props == {"k": "v"}

    def test_tombstones_do_not_count_toward_the_reconstruction_bound(self):
        rows = [_tomb(1, i) for i in range(1, 50)] + [_row(2, 1, "create_node", "node", "x", new="")]
        st = R.reconstruct((_rec(r) for r in rows), bound=1)
        assert set(st.nodes) == {"x"}


class TestDiffMarksTombstones:
    def test_a_tombstone_contributes_no_entry_and_is_listed_as_redacted(self):
        d = R.compute_diff(_rec(r) for r in _history_with_tombstones())
        assert all(e.entity_id != "" for e in d.entries)
        assert d.redacted == [(1, 2), (1, 4)]

    def test_redaction_revision_records_contribute_no_entry(self):
        d = R.compute_diff([_rec(_row(3, 1, "redact", "revision", "1", new="2", flags="redaction"))])
        assert d.entries == [] and d.redacted == []

    def test_inverse_keeps_the_redacted_ordinals(self):
        d = R.compute_diff(_rec(r) for r in _history_with_tombstones())
        assert d.inverse().redacted == d.redacted


# --------------------------------------------------------------------------- #
# error codes
# --------------------------------------------------------------------------- #


class TestErrorCodes:
    @pytest.mark.parametrize(
        "code, cls",
        [
            ("target_live", RedactionTargetLiveError),
            ("redaction_in_progress", RedactionInProgressError),
            ("invalid_target", InvalidRedactionTargetError),
            ("idempotency_redacted", IdempotencyRedactedError),
            ("disabled", LedgerDisabledError),
        ],
    )
    def test_each_redaction_code_maps_to_its_exception(self, code, cls):
        err = from_commit_error({"ok": False, "error": code, "reason": "why"})
        assert type(err) is cls and "why" in str(err)

    def test_a_retry_of_a_redacted_key_is_an_idempotency_conflict(self):
        assert issubclass(IdempotencyRedactedError, IdempotencyConflictError)


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #

REV = {
    "seq": 9,
    "revision_id": "c" * 32,
    "parent_id": "b" * 32,
    "kind": "redaction",
    "actor": "system:ledger-redact",
    "actor_type": "system",
    "conn_user": "_SYSTEM",
    "committed_ms": 1_700_000_000_000,
    "message": "redaction: 5 record(s) in 2 revision(s)",
    "source": {"target_sha256": "d" * 64, "records": 5, "revisions": 2, "statements": 1},
    "correlation_id": None,
    "idempotency_key": None,
    "op_count": 2,
}

RECEIPT = {
    "ok": True,
    "complete": True,
    "target_sha256": "d" * 64,
    "revision_seqs": [2, 4],
    "records_tombstoned": 5,
    "metadata_cleared": 2,
    "idempotency_keys_removed": 1,
    "statements_removed": 1,
    "unattributed_records": 0,
    "batches": 1,
    "max_batch_ms": 3,
    "redaction": REV,
    "out_of_reach": ["exports", "journals", "backups"],
}


class FakeServer:
    def __init__(self, redact_response=None, in_progress=None):
        self.calls = []
        self.redact_response = redact_response or RECEIPT
        self.in_progress = in_progress

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if cls == "Graph.KG.LedgerRedact" and method == "Redact":
            return json.dumps(self.redact_response)
        if cls == "Graph.KG.LedgerRedact" and method == "InProgress":
            if self.in_progress is None:
                return json.dumps({"ok": True, "in_progress": False})
            return json.dumps(dict({"ok": True, "in_progress": True}, **self.in_progress))
        if method == "Head":
            return json.dumps({"ok": True, "revision": dict(REV, kind="changeset")})
        if method == "Meta":
            return json.dumps({"ok": True, "state": "enabled", "recon_bound": 250000, "head_seq": 9})
        if method == "SeqOf":
            return json.dumps({"ok": True, "seq": 9})
        if method == "RecordVerify":
            return 1
        return ""


def _ledger(server):
    eng = MagicMock()
    eng._iris_obj.return_value = server
    eng._schema_prefix = "Graph_KG"
    return CL.GraphLedger(eng), eng


class TestRedactCall:
    def test_redact_sends_target_options_and_schema_to_the_server(self):
        srv = FakeServer()
        led, _ = _ledger(srv)
        led.redact("tenantA", prefix="acme", separator="/", actor="ops:erase", batch_size=50, max_batches=3)
        calls = [c for c in srv.calls if c[1] == "Redact"]
        assert calls == [
            ("Graph.KG.LedgerRedact", "Redact", ("tenantA", "acme", "/", "ops:erase", 50, 3, "Graph_KG"))
        ]

    def test_the_default_graph_and_no_prefix_are_sent_as_empty_strings(self):
        srv = FakeServer()
        led, _ = _ledger(srv)
        led.redact(None)
        args = [c for c in srv.calls if c[1] == "Redact"][0][2]
        assert args[:3] == ("", "", "|")

    def test_the_receipt_is_decoded(self):
        led, _ = _ledger(FakeServer())
        r = led.redact("tenantA")
        assert isinstance(r, CL.RedactionReceipt)
        assert r.complete and r.revision_seqs == [2, 4] and r.revisions_touched == 2
        assert r.records_tombstoned == 5 and r.metadata_cleared == 2
        assert r.idempotency_keys_removed == 1 and r.statements_removed == 1
        assert r.unattributed_records == 0 and r.batches == 1 and r.max_batch_ms == 3
        assert r.redaction.kind == "redaction" and r.redaction.seq == 9
        assert r.out_of_reach == ["exports", "journals", "backups"]
        assert r.target_sha256 == "d" * 64

    def test_a_no_op_receipt_has_no_redaction_revision(self):
        led, _ = _ledger(FakeServer(redact_response=dict(RECEIPT, revision_seqs=[], records_tombstoned=0, redaction=None)))
        r = led.redact("tenantA")
        assert r.redaction is None and r.records_tombstoned == 0 and r.revisions_touched == 0

    def test_a_refusal_raises_the_mapped_exception(self):
        srv = FakeServer(redact_response={"ok": False, "error": "target_live", "reason": "graph 'g' is live"})
        led, _ = _ledger(srv)
        with pytest.raises(RedactionTargetLiveError, match="graph 'g' is live"):
            led.redact("g")

    def test_a_chunked_receipt_is_reassembled(self):
        big = dict(RECEIPT, revision_seqs=list(range(1, 400_000)))
        out = json.dumps(big)

        class Chunky(FakeServer):
            def classMethodValue(self, cls, method, *args):
                if method == "Redact":
                    self.calls.append((cls, method, args))
                    return "CHUNKED:T9:2"
                if method == "ReadLargeOutChunk":
                    self.calls.append((cls, method, args))
                    half = len(out) // 2
                    return out[:half] if int(args[1]) == 1 else out[half:]
                return super().classMethodValue(cls, method, *args)

        srv = Chunky()
        led, _ = _ledger(srv)
        r = led.redact("g")
        assert r.revisions_touched == len(big["revision_seqs"])
        reads = [c for c in srv.calls if c[1] == "ReadLargeOutChunk"]
        assert [c[0] for c in reads] == ["Graph.KG.Ledger", "Graph.KG.Ledger"]


class TestReadersHonourTheMarker:
    MARK = {"target_sha256": "e" * 64, "started_ms": 1}

    def test_redaction_in_progress_reports_the_marker(self):
        led, _ = _ledger(FakeServer(in_progress=self.MARK))
        assert led.redaction_in_progress()["target_sha256"] == "e" * 64

    def test_no_marker_is_none(self):
        led, _ = _ledger(FakeServer())
        assert led.redaction_in_progress() is None

    @pytest.mark.parametrize(
        "call",
        [
            lambda led: led.get_revision("x"),
            lambda led: led.diff("x", "y"),
            lambda led: led.reconstruct("x"),
            lambda led: led.export_reconstruction("x", "unused.ndjson"),
        ],
        ids=["get_revision", "diff", "reconstruct", "export_reconstruction"],
    )
    def test_record_readers_refuse_while_a_redaction_runs(self, call):
        led, eng = _ledger(FakeServer(in_progress=self.MARK))
        with pytest.raises(RedactionInProgressError):
            call(led)
        eng.conn.cursor.assert_not_called()

    def test_verify_reports_in_progress_without_reading_records_or_adopting(self):
        srv = FakeServer(in_progress=self.MARK)
        led, eng = _ledger(srv)
        report = led.verify(adopt=True)
        assert report.result == "redaction_in_progress" and not report.equal
        assert report.differences == [] and report.adoption_revision is None
        eng.conn.cursor.assert_not_called()
        assert not any(c[1] == "Commit" for c in srv.calls)
        assert ("Graph.KG.Ledger", "RecordVerify", ("redaction_in_progress",)) in srv.calls


# --------------------------------------------------------------------------- #
# the one exemption from immutability is narrow (ADR-0013)
# --------------------------------------------------------------------------- #

CLEARED = {"message", "source", "correlation_id", "idempotency_key"}


def _member(src: str, kind: str, name: str) -> str:
    m = re.search(rf"^{kind} {re.escape(name)}\b.*?^\}}", src, re.S | re.M)
    assert m, f"{kind} {name} not found"
    return m.group(0)


class TestTriggerExemptionIsNarrow:
    src = (SRC / "LedgerRevision.cls").read_text()

    def test_update_trigger_admits_only_the_redaction_path(self):
        body = _member(self.src, "Trigger", "RejectUpdate")
        assert "%IVGLedgerRedaction" in body and '"redactedRev"' in body
        assert "%ok = 0" in body

    def test_every_other_column_must_be_unchanged(self):
        # Trigger code names columns by their SQL field names.
        props = set(re.findall(r"^Property \w+ .*SqlFieldName = (\w+)", self.src, re.M))
        assert len(props) == len(re.findall(r"^Property ", self.src, re.M)), "a property has no SqlFieldName"
        body = _member(self.src, "Trigger", "RejectUpdate")
        for p in sorted(props - CLEARED):
            assert f"{{{p}*C}}" in body, f"RejectUpdate does not pin {p}"

    def test_the_cleared_columns_may_only_become_empty(self):
        body = _member(self.src, "Trigger", "RejectUpdate")
        for p in sorted(CLEARED):
            assert f'{{{p}*N}} = ""' in body, f"RejectUpdate lets {p} take a value"

    def test_delete_and_object_paths_have_no_exemption(self):
        for kind, name in (("Trigger", "RejectDelete"), ("Method", "%OnBeforeSave"), ("ClassMethod", "%OnDelete")):
            assert "%IVGLedgerRedaction" not in _member(self.src, kind, name)

    def test_only_ledger_redact_sets_the_exemption_variable(self):
        setters = sorted(
            p.name
            for p in (ROOT / "iris_src" / "src").rglob("*.cls")
            if re.search(r"\bSet %IVGLedgerRedaction\b", p.read_text())
        )
        assert setters == ["LedgerRedact.cls"]
