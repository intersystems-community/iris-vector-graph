"""Spec 256: redacting a tenant from the revision ledger, end to end against live IRIS.

One test class per user story, plus the default-graph prefix case and the batch marker.

The world: tenant A and tenant B. Each one's relationships live in its own named graph and its
node ids carry a tenant prefix (the ledger's node operations are graph-blind, so every node record
is in the default graph). One revision spans both tenants. Tenant A also has a statement that was
created and deleted, and a node that was created and deleted.

A full erasure of tenant A is ``erase_graph("gTenantA")`` plus ``delete_nodes_by_prefix("tenantA|")``,
and the full redaction is ``redact("gTenantA")`` plus ``redact(None, prefix="tenantA")``.

The scans read every subscript and value of ^IVG.Ledger through the Native API and every row of
the ledger tables. Each scan is first shown to find tenant A before the redaction, so a scan that
cannot see is not mistaken for one that finds nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import (
    IdempotencyRedactedError,
    LedgerDisabledError,
    RedactionInProgressError,
    RedactionTargetLiveError,
)
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
    pytest.mark.requires_database,
    pytest.mark.integration,
]

GA, GB = "gTenantA", "gTenantB"
# Every string of tenant A's that the world writes contains one of these. None of them is
# made of hex digits only, so a SHA-256 digest cannot contain one by chance.
NEEDLES_A = ("tenantA", "gTenantA", "TenantALabel", "alphaSecret")
NEEDLES_B = ("tenantB", "gTenantB", "TenantBLabel", "bravoSecret")


# --------------------------------------------------------------------------- #
# scanning and snapshots
# --------------------------------------------------------------------------- #


def walk_global(o, gname: str, subs: Tuple = ()):
    d = int(o.isDefined(gname, *subs))
    if d in (1, 11):
        yield subs, bytes(o.getBytes(gname, *subs) or b"")
    if d >= 10:
        for sub, _ in o.iterator(gname, *subs).items():
            yield from walk_global(o, gname, subs + (sub,))


def ledger_global(eng) -> Dict[Tuple, bytes]:
    return dict(walk_global(eng._iris_obj(), "^IVG.Ledger"))


def table_rows(conn, table: str) -> List[Tuple]:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {table}")
        return sorted(tuple("" if v is None else str(v) for v in r) for r in cur.fetchall())
    finally:
        cur.close()


LEDGER_TABLES = ("Graph_KG.ledger_revisions", "Graph_KG.ledger_stats")
CONTENT_TABLES = ("Graph_KG.nodes", "Graph_KG.rdf_edges", "Graph_KG.rdf_labels", "Graph_KG.rdf_props")


def ledger_hits(eng, needles) -> List[str]:
    """Every place in ^IVG.Ledger or a ledger_* table that holds one of the needles."""
    hits = []
    for subs, val in ledger_global(eng).items():
        text = repr(subs).encode() + b" " + val
        hits += [f"^IVG.Ledger{subs!r}: {n}" for n in needles if n.encode() in text]
    for t in LEDGER_TABLES:
        for row in table_rows(eng.conn, t):
            hits += [f"{t} {row[:2]}: {n}" for n in needles if n in repr(row)]
    return hits


def reader_hits(eng, needles, tmp_path) -> List[str]:
    """Every reader's output that holds one of the needles."""
    led = eng.ledger
    head = led.head()
    first = led.history(limit=1).revisions[0]
    out = {
        "history": repr(led.history(limit=100_000).revisions),
        "records": repr([led.get_revision(r.revision_id).records for r in led.history(limit=100_000).revisions]),
        "diff": repr(led.diff(first.revision_id, head.revision_id).to_list()),
        "reconstruct": repr(led.reconstruct(head.revision_id).as_comparable()),
    }
    path = tmp_path / "export.ndjson"
    led.export_reconstruction(head.revision_id, str(path))
    out["export"] = path.read_text()
    return [f"{k}: {n}" for k, v in out.items() for n in needles if n in v]


def snapshot(eng) -> Dict[str, object]:
    """Every store a redaction could touch: the ledger global, ^KG, ledger and content tables."""
    snap: Dict[str, object] = {"ledger": ledger_global(eng), "kg": dict(walk_global(eng._iris_obj(), "^KG"))}
    for t in LEDGER_TABLES + CONTENT_TABLES:
        snap[t] = table_rows(eng.conn, t)
    return snap


def canonical(eng, seq: int, ordinal: int) -> str:
    return str(eng._iris_obj().classMethodValue("Graph.KG.Ledger", "CanonicalRecord", seq, ordinal) or "")


def all_records(eng) -> Dict[Tuple[int, int], str]:
    head = eng.ledger.head().seq
    out = {}
    for seq in range(1, head + 1):
        cur = eng.conn.cursor()
        try:
            cur.execute("SELECT ordinal FROM Graph_KG.ledger_records(?, ?, 1, 100000)", [seq, seq])
            ords = [int(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()
        for o in ords:
            out[(seq, o)] = canonical(eng, seq, o)
    return out


def revision_row(eng, seq: int) -> Dict[str, object]:
    cur = eng.conn.cursor()
    try:
        cur.execute(
            "SELECT seq, revision_id, parent_id, kind, actor, actor_type, conn_user, committed_ms, "
            "message, source, correlation_id, idempotency_key, op_count, stmt_first, stmt_last "
            "FROM Graph_KG.ledger_revisions WHERE seq = ?",
            [seq],
        )
        cols = [d[0].lower() for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, tuple(row))) if row is not None else {}
    finally:
        cur.close()


def tombstone_rows(eng) -> List[Dict[str, object]]:
    cur = eng.conn.cursor()
    try:
        cur.execute("SELECT * FROM Graph_KG.ledger_records(1, 1000000, 1, 1000000)")
        cols = [d[0].lower() for d in cur.description]
        rows = [dict(zip(cols, tuple(r))) for r in cur.fetchall()]
    finally:
        cur.close()
    return [r for r in rows if r["op"] == "redacted"]


# --------------------------------------------------------------------------- #
# the world
# --------------------------------------------------------------------------- #


@dataclass
class World:
    eng: object
    a1: object
    b1: object
    span: object
    a2: object
    a3: object
    b2: object


def _cs(message, corr, key, src):
    return Changeset(
        actor="ingest:etl",
        actor_type="ingest",
        message=message,
        correlation_id=corr,
        idempotency_key=key,
        source={"feed": src},
    )


def build_world(eng) -> World:
    led = eng.ledger
    led.enable()

    cs = _cs("tenantA load", "tenantA|c1", "tenantA-k1", "tenantA-feed")
    cs.create_node("tenantA|n1", labels=["TenantALabel"], properties={"tenantAKey": "alphaSecret1"})
    cs.create_node("tenantA|n2", properties={"name": "alphaSecretName"})
    cs.create_relationship("tenantA|n1", "LINKS", "tenantA|n2", qualifiers={"weight": "2", "note": "alphaSecretNote"}, graph=GA)
    a1 = led.commit(cs).revision

    cs = _cs("tenantB load", "tenantB|c1", "tenantB-k1", "tenantB-feed")
    cs.create_node("tenantB|n1", labels=["TenantBLabel"], properties={"tenantBKey": "bravoSecret1"})
    cs.create_node("tenantB|n2", properties={"name": "bravoSecretName"})
    cs.create_relationship("tenantB|n1", "LINKS", "tenantB|n2", qualifiers={"weight": "3", "note": "bravoSecretNote"}, graph=GB)
    b1 = led.commit(cs).revision

    cs = _cs("sync tenantA and tenantB", "shared|c1", "shared-k1", "shared-feed")
    cs.set_property("tenantA|n1", "tenantAKey", "alphaSecret2")
    cs.set_property("tenantB|n1", "tenantBKey", "bravoSecret2")
    cs.create_relationship("tenantA|n2", "BACK", "tenantA|n1", graph=GA)
    cs.create_relationship("tenantB|n2", "BACK", "tenantB|n1", graph=GB)
    span = led.commit(cs).revision

    cs = _cs("tenantA extra", "tenantA|c2", "tenantA-k2", "tenantA-feed")
    cs.create_node("tenantA|n3", properties={"tmp": "alphaSecretTemp"})
    cs.create_relationship("tenantA|n3", "LINKS", "tenantA|n1", qualifiers={"weight": "1"}, graph=GA)
    a2 = led.commit(cs).revision

    # a statement created and deleted, and a node created and deleted (detach)
    cs = _cs("tenantA cleanup", "tenantA|c3", None, "tenantA-feed")
    cs.set_qualifier(("tenantA|n1", "LINKS", "tenantA|n2", GA), "note", "alphaSecretNote2")
    cs.delete_node("tenantA|n3", mode="detach")
    a3 = led.commit(cs).revision

    cs = _cs("tenantB more", "tenantB|c2", "tenantB-k2", "tenantB-feed")
    cs.set_property("tenantB|n2", "name", "bravoSecretName2")
    b2 = led.commit(cs).revision
    return World(eng, a1, b1, span, a2, a3, b2)


def erase_tenant_a(eng):
    eng.erase_graph(GA)
    eng.delete_nodes_by_prefix("tenantA|")


def redact_tenant_a(eng):
    return eng.ledger.redact(GA), eng.ledger.redact(None, prefix="tenantA")


@pytest.fixture
def world(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    w = build_world(eng)
    yield w
    try:
        eng.ledger.enable()
    except Exception:
        pass


def records_of(recs: Dict[Tuple[int, int], str], needles) -> Dict[Tuple[int, int], str]:
    return {k: v for k, v in recs.items() if any(n in v for n in needles)}


# --------------------------------------------------------------------------- #
# US1: a tenant's history can be redacted
# --------------------------------------------------------------------------- #


class TestUS1RedactTenant:
    def test_the_scan_sees_tenant_a_after_the_erase_alone(self, world, tmp_path):
        erase_tenant_a(world.eng)
        assert ledger_hits(world.eng, NEEDLES_A), "the scan cannot see tenant A even before redaction"
        assert reader_hits(world.eng, ("tenantA",), tmp_path)

    def test_nothing_in_the_ledger_or_its_readers_holds_tenant_a(self, world, tmp_path):
        erase_tenant_a(world.eng)
        by_graph, by_prefix = redact_tenant_a(world.eng)
        assert by_graph.complete and by_prefix.complete
        assert ledger_hits(world.eng, NEEDLES_A) == []
        assert reader_hits(world.eng, NEEDLES_A, tmp_path) == []
        # and the scan still sees tenant B
        assert {h.split(": ")[-1] for h in ledger_hits(world.eng, NEEDLES_B)} == set(NEEDLES_B)

    def test_tenant_b_records_are_byte_identical(self, world):
        before = records_of(all_records(world.eng), NEEDLES_B)
        b_rows = {s: revision_row(world.eng, s) for s in (world.b1.seq, world.b2.seq)}
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        after = all_records(world.eng)
        assert before and {k: after[k] for k in before} == before
        assert {s: revision_row(world.eng, s) for s in b_rows} == b_rows

    def test_a_revision_wholly_in_tenant_a_keeps_its_shape_and_loses_its_metadata(self, world):
        before = revision_row(world.eng, world.a1.seq)
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        after = revision_row(world.eng, world.a1.seq)
        for col in ("seq", "revision_id", "parent_id", "kind", "actor", "actor_type", "committed_ms", "op_count"):
            assert after[col] == before[col], col
        for col in ("message", "source", "correlation_id", "idempotency_key"):
            assert before[col] and after[col] in (None, ""), col

    def test_a_spanning_revision_keeps_tenant_b_records_and_loses_its_metadata(self, world):
        recs = all_records(world.eng)
        span = {k: v for k, v in recs.items() if k[0] == world.span.seq}
        span_b = records_of(span, NEEDLES_B)
        span_a = records_of(span, NEEDLES_A)
        assert span_a and span_b
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        after = all_records(world.eng)
        assert {k: after[k] for k in span_b} == span_b
        assert all(json.loads(after[k])["op"] == "redacted" for k in span_a)
        assert set(after) >= set(span), "an ordinal of the spanning revision went missing"
        row = revision_row(world.eng, world.span.seq)
        assert all(row[c] in (None, "") for c in ("message", "source", "correlation_id", "idempotency_key"))

    def test_running_the_same_redaction_again_changes_nothing(self, world):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        before = snapshot(world.eng)
        again_g, again_p = redact_tenant_a(world.eng)
        for r in (again_g, again_p):
            assert r.complete and r.records_tombstoned == 0 and r.revisions_touched == 0
            assert r.redaction is None and r.metadata_cleared == 0 and r.statements_removed == 0
        assert snapshot(world.eng) == before

    def test_the_redaction_revision_is_content_free(self, world):
        erase_tenant_a(world.eng)
        by_graph, by_prefix = redact_tenant_a(world.eng)
        for r in (by_graph, by_prefix):
            rev = r.redaction
            assert rev is not None and rev.kind == "redaction"
            assert not any(n in repr(rev) for n in NEEDLES_A)
            records = world.eng.ledger.get_revision(rev.revision_id).records
            assert sorted(int(x.entity_id) for x in records) == sorted(r.revision_seqs)
            assert {x.op for x in records} == {"redact"} and {x.entity_kind for x in records} == {"revision"}
            assert sum(int(x.new) for x in records) == r.records_tombstoned
            assert not any(n in repr(records) for n in NEEDLES_A)
            assert rev.source["target_sha256"] == r.target_sha256
        # the graph redaction named every tenant A revision with a relationship record
        assert {world.a1.seq, world.span.seq, world.a2.seq, world.a3.seq} == set(by_graph.revision_seqs)

    def test_a_retry_with_a_redacted_idempotency_key_is_refused(self, world):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        before = snapshot(world.eng)
        cs = _cs("tenantA load", "tenantA|c1", "tenantA-k1", "tenantA-feed")
        cs.create_node("tenantA|n1", labels=["TenantALabel"], properties={"tenantAKey": "alphaSecret1"})
        with pytest.raises(IdempotencyRedactedError):
            world.eng.ledger.commit(cs)
        after = snapshot(world.eng)
        # a rejected commit bumps no success counter and writes nothing else
        assert {k: v for k, v in after.items() if k != "Graph_KG.ledger_stats"} == {
            k: v for k, v in before.items() if k != "Graph_KG.ledger_stats"
        }

    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE Graph_KG.ledger_revisions SET message = NULL WHERE seq = ?",
            "UPDATE Graph_KG.ledger_revisions SET message = 'rewritten' WHERE seq = ?",
            "UPDATE Graph_KG.ledger_revisions SET actor = 'someone' WHERE seq = ?",
            "DELETE FROM Graph_KG.ledger_revisions WHERE seq = ?",
        ],
    )
    def test_outside_the_redaction_a_redacted_revision_is_still_immutable(self, world, sql):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        before = revision_row(world.eng, world.a1.seq)
        b_before = revision_row(world.eng, world.b1.seq)
        cur = world.eng.conn.cursor()
        try:
            for seq in (world.a1.seq, world.b1.seq):
                with pytest.raises(Exception, match="immutable"):
                    cur.execute(sql, [seq])
        finally:
            cur.close()
        assert revision_row(world.eng, world.a1.seq) == before
        assert revision_row(world.eng, world.b1.seq) == b_before

    def test_the_receipt_counts_what_it_did_and_names_what_is_out_of_reach(self, world):
        erase_tenant_a(world.eng)
        by_graph, by_prefix = redact_tenant_a(world.eng)
        assert by_graph.records_tombstoned > 0 and by_prefix.records_tombstoned > 0
        cleared = by_graph.metadata_cleared + by_prefix.metadata_cleared
        assert cleared == len({world.a1.seq, world.span.seq, world.a2.seq, world.a3.seq})
        assert by_graph.idempotency_keys_removed + by_prefix.idempotency_keys_removed == 3
        text = " ".join(by_graph.out_of_reach).lower()
        assert "export" in text and "journal" in text and "backup" in text


class TestDefaultGraphTenantByPrefix:
    @pytest.fixture
    def acme(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        eng.ledger.enable()
        cs = Changeset(actor="ingest:etl", actor_type="ingest", message="acme and acme-health")
        cs.create_node("acme|x", properties={"v": "acmeSecretX"})
        cs.create_node("acme|z")
        cs.create_node("acme-health|y", properties={"v": "acmeHealthSecretY"})
        cs.create_node("acme-health|w")
        cs.create_relationship("acme|x", "R", "acme|z", qualifiers={"q": "acmeSecretQ"})
        cs.create_relationship("acme-health|y", "R", "acme-health|w", qualifiers={"q": "acmeHealthSecretQ"})
        eng.ledger.commit(cs)
        return eng

    def test_the_prefix_is_matched_with_its_separator(self, acme):
        acme.delete_nodes_by_prefix("acme|")
        r = acme.ledger.redact(None, prefix="acme")
        assert r.complete and r.records_tombstoned == 5  # 2 nodes, 1 prop, 1 relationship, 1 qualifier
        assert ledger_hits(acme, ("acme|", "acmeSecret")) == []
        assert {h.split(": ")[-1] for h in ledger_hits(acme, ("acme-health|", "acmeHealthSecret"))} == {
            "acme-health|",
            "acmeHealthSecret",
        }
        assert acme.ledger.verify().result == "equal"

    def test_a_prefix_that_already_ends_with_the_separator_is_the_same_target(self, acme):
        acme.delete_nodes_by_prefix("acme|")
        r = acme.ledger.redact(None, prefix="acme|")
        assert r.records_tombstoned == 5
        assert acme.ledger.redact(None, prefix="acme").records_tombstoned == 0

    def test_live_acme_health_nodes_do_not_block_redacting_acme(self, acme):
        acme.delete_nodes_by_prefix("acme|")
        acme.ledger.redact(None, prefix="acme")
        assert acme.get_node("acme-health|y") is not None


# --------------------------------------------------------------------------- #
# US2: the ledger stays verifiable
# --------------------------------------------------------------------------- #


class TestUS2Verifiable:
    def test_verify_diverges_after_the_erase_alone_and_is_equal_after_the_redaction(self, world):
        erase_tenant_a(world.eng)
        assert world.eng.ledger.verify().result == "diverges"
        redact_tenant_a(world.eng)
        report = world.eng.ledger.verify()
        assert report.result == "equal" and report.differences == []

    def test_each_tombstone_digest_is_sha256_of_the_canonical_record_it_replaced(self, world):
        erase_tenant_a(world.eng)
        before = all_records(world.eng)
        redact_tenant_a(world.eng)
        tombs = tombstone_rows(world.eng)
        assert tombs
        for t in tombs:
            original = before[(t["seq"], t["ordinal"])]
            assert t["digest"] == hashlib.sha256(original.encode("utf-8")).hexdigest()
            assert t["redacted_op"] == json.loads(original)["op"]
            assert t["entity_id"] in ("", None) and t["attr"] in ("", None) and t["graph"] in ("", None)
            assert int(t["prior_null"]) == 1 and int(t["new_null"]) == 1
        # every record that held tenant A became a tombstone, and nothing else did
        a_keys = set(records_of(before, NEEDLES_A))
        assert {(t["seq"], t["ordinal"]) for t in tombs} == a_keys

    def test_the_canonical_form_of_a_tombstone_holds_only_its_digest(self, world):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        t = tombstone_rows(world.eng)[0]
        c = json.loads(canonical(world.eng, t["seq"], t["ordinal"]))
        assert c["op"] == "redacted" and c["digest"] == t["digest"]
        assert set(c) == {"digest", "flags", "op", "ordinal", "redacted_op", "seq"}

    def test_replay_to_every_seq_holds_no_tenant_a_content(self, world):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        for rev in world.eng.ledger.history(limit=1000).revisions:
            state = world.eng.ledger.reconstruct(rev.revision_id)
            assert not any(n in repr(state.as_comparable()) for n in NEEDLES_A), rev.seq

    def test_diff_marks_tombstones_rather_than_hiding_them(self, world):
        erase_tenant_a(world.eng)
        redact_tenant_a(world.eng)
        led = world.eng.ledger
        d = led.diff(led.history(limit=1).revisions[0].revision_id, led.head().revision_id)
        assert d.redacted and all(isinstance(s, int) and isinstance(o, int) for s, o in d.redacted)
        assert sorted(d.redacted) == sorted((t["seq"], t["ordinal"]) for t in tombstone_rows(world.eng))


# --------------------------------------------------------------------------- #
# US3: redaction refuses to leave the ledger and the store inconsistent
# --------------------------------------------------------------------------- #


class TestUS3Refusals:
    def test_a_live_tenant_graph_is_refused_and_nothing_changes(self, world):
        before = snapshot(world.eng)
        with pytest.raises(RedactionTargetLiveError, match=GA):
            world.eng.ledger.redact(GA)
        assert snapshot(world.eng) == before

    def test_live_default_graph_nodes_refuse_the_prefix_and_nothing_changes(self, world):
        world.eng.erase_graph(GA)
        before = snapshot(world.eng)
        with pytest.raises(RedactionTargetLiveError):
            world.eng.ledger.redact(None, prefix="tenantA")
        assert snapshot(world.eng) == before

    def test_a_disabled_ledger_is_refused_and_nothing_changes(self, world):
        erase_tenant_a(world.eng)
        world.eng.ledger.disable()
        before = snapshot(world.eng)
        with pytest.raises(LedgerDisabledError):
            world.eng.ledger.redact(GA)
        assert snapshot(world.eng) == before


class TestBatchesAndMarker:
    def test_a_paused_redaction_is_honoured_and_resumes(self, world, tmp_path):
        eng = world.eng
        erase_tenant_a(eng)
        part = eng.ledger.redact(GA, batch_size=1, max_batches=1)
        assert part.complete is False and part.records_tombstoned == 1 and part.redaction is None
        marker = eng.ledger.redaction_in_progress()
        assert marker and marker["target_sha256"] == part.target_sha256

        assert eng.ledger.verify().result == "redaction_in_progress"
        with pytest.raises(RedactionInProgressError):
            eng.ledger.diff(world.a1.revision_id, world.b2.revision_id)

        # a commit that would add a record to the target is refused; another tenant's commits
        head = eng.ledger.head().seq
        cs = Changeset(actor="ingest:etl", actor_type="ingest")
        cs.create_relationship("tenantB|n1", "LATE", "tenantB|n2", graph=GA)
        with pytest.raises(RedactionInProgressError):
            eng.ledger.commit(cs)
        assert eng.ledger.head().seq == head
        cs = Changeset(actor="ingest:etl", actor_type="ingest")
        cs.set_property("tenantB|n1", "tenantBKey", "bravoSecret3")
        eng.ledger.commit(cs)

        # a different target waits for this one, and is refused without changing anything
        before = snapshot(eng)
        with pytest.raises(RedactionInProgressError):
            eng.ledger.redact(None, prefix="tenantA")
        assert snapshot(eng) == before

        done = eng.ledger.redact(GA, batch_size=2)
        assert done.complete and done.batches > 1 and done.redaction is not None
        assert done.records_tombstoned > part.records_tombstoned
        assert eng.ledger.redaction_in_progress() is None
        eng.ledger.redact(None, prefix="tenantA")
        assert eng.ledger.verify().result == "equal"
        assert ledger_hits(eng, NEEDLES_A) == []
        assert reader_hits(eng, NEEDLES_A, tmp_path) == []
