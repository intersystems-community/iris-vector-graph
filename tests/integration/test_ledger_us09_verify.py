"""Spec 213 US9 — verify materialized view against replay; adoption; disable/enable (live). T083."""

import multiprocessing as mp
import os

import pytest

from iris_vector_graph.ledger import Changeset, LedgerDisabledError
from tests.integration._ledger_helpers import canonical_tables, make_engine
from tests.integration.test_ledger_us03_concurrency import _conn_params, _worker_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    cs = Changeset(actor="seed", actor_type="system")
    cs.create_node("a", labels=["L"], properties={"k": "v"})
    cs.create_node("b")
    cs.create_relationship("a", "R", "b", qualifiers={"weight": "1.0"})
    eng.ledger.commit(cs)
    return eng


def _rev_count(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.ledger_revisions")
        return cur.fetchone()[0]
    finally:
        cur.close()


def _slow_committer(params, out):
    conn, eng = _worker_engine(params)
    try:
        cs = Changeset(actor="agent:bg", actor_type="agent")
        cs.create_node("bg-node")
        out.put(eng.ledger.commit(cs).revision.revision_id)
    finally:
        conn.close()


class TestUS9Verify:
    def test_clean_ledger_is_equal_and_records_result(self, engine):
        report = engine.ledger.verify()
        assert (
            report.result == "equal" and report.differences == [] and report.classification is None
        )
        assert report.verified_head == engine.ledger.head().revision_id
        st = engine.ledger.stats()
        assert st.last_verify_result == "equal" and st.last_verify_ms

    def test_legacy_and_raw_sql_writes_are_unrecorded(self, engine):
        engine.create_node("legacy-n", labels=["X"], properties={"p": "1"})
        cur = engine.conn.cursor()
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES ('b', 'RAW', 'a', '{}')"
        )
        engine.conn.commit()
        cur.close()
        report = engine.ledger.verify()
        assert report.result == "diverges" and report.classification == "unrecorded_writes"
        ids = {(e.entity_kind, e.entity_id, e.attr) for e in report.differences}
        assert (
            ("node", "legacy-n", "") in ids
            and ("node", "legacy-n", "label:X") in ids
            and ("node", "legacy-n", "prop:p") in ids
        )
        assert any(k == "rel" and '"p":"RAW"' in eid for k, eid, _ in ids)
        assert engine.ledger.stats().last_verify_result == "diverges"

    def test_adoption_commits_divergence_then_equal(self, engine):
        engine.create_node("legacy-n", labels=["X"])
        engine.create_edge("legacy-n", "R", "a")
        head_before = engine.ledger.head()
        n = _rev_count(engine.conn)
        report = engine.ledger.verify(adopt=True)
        assert report.result == "diverges" and report.adoption_revision is not None
        adopted = report.adoption_revision
        assert (
            adopted.kind == "adoption"
            and adopted.actor_type == "system"
            and adopted.parent_id == head_before.revision_id
        )
        assert _rev_count(engine.conn) == n + 1
        rev = engine.ledger.get_revision(adopted.revision_id)
        assert len(rev.records) == len(report.differences)
        assert all("adoption" in r.flags for r in rev.records)
        assert engine.ledger.verify().result == "equal"
        # the adopted relationship now has a statement identity
        stmt = str(
            engine._iris_obj().classMethodValue(
                "Graph.KG.Ledger", "StmtForTuple", "legacy-n", "R", "a", ""
            )
        )
        assert stmt != ""

    def test_verify_is_read_only_without_adopt(self, engine):
        engine.create_node("legacy-n")
        head = engine.ledger.head().revision_id
        n = _rev_count(engine.conn)
        tables = canonical_tables(engine.conn)
        engine.ledger.verify()
        assert engine.ledger.head().revision_id == head and _rev_count(engine.conn) == n
        assert canonical_tables(engine.conn) == tables

    def test_disable_and_reenable_adopts(self, engine):
        head = engine.ledger.head()
        engine.ledger.disable()
        assert engine.ledger.meta()["state"] == "disabled"
        cs = Changeset(actor="x", actor_type="human")
        cs.create_node("blocked")
        with pytest.raises(LedgerDisabledError):
            engine.ledger.commit(cs)
        assert engine.ledger.stats().rejections["rej_disabled"] >= 1
        # history stays readable
        assert engine.ledger.head().revision_id == head.revision_id
        assert len(engine.ledger.history().revisions) == 2
        engine.ledger.disable()  # idempotent
        # legacy write while disabled, then re-enable → adoption
        engine.create_node("while-disabled", labels=["W"])
        adopted = engine.ledger.enable()
        assert adopted.kind == "adoption" and adopted.seq == 3
        assert engine.ledger.verify().result == "equal"
        # re-enable with nothing changed creates no revision
        engine.ledger.disable()
        again = engine.ledger.enable()
        assert again.revision_id == adopted.revision_id and engine.ledger.head().seq == 3

    def test_verify_reports_head_observed_at_start(self, engine):
        params = _conn_params(engine.conn)
        ctx = mp.get_context("spawn")
        out = ctx.Queue()
        head_before = engine.ledger.head().revision_id
        p = ctx.Process(target=_slow_committer, args=(params, out))
        p.start()
        report = engine.ledger.verify()
        bg_rev = out.get(timeout=120)
        p.join(timeout=30)
        assert report.verified_head in (head_before, bg_rev)
        # a report against the pre-commit head must not include the background node
        if report.verified_head == head_before:
            assert not any(e.entity_id == "bg-node" and e.after is None for e in report.differences)
        assert engine.ledger.verify().result == "equal"
