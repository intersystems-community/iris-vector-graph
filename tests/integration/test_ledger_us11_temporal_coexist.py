"""Spec 213 US11 — coexistence with the temporal property graph (live ivg-iris-enterprise). T098."""

import os
import time

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

TS = 1_700_000_000


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    cs = Changeset(actor="seed", actor_type="system")
    cs.create_node("a", labels=["L"])
    cs.create_node("b")
    cs.create_relationship("a", "R", "b")
    eng.ledger.commit(cs)
    return eng


def _kg(engine, *subs):
    v = engine._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", *[str(s) for s in subs])
    return "" if v is None else str(v)


def _rev_count(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.ledger_revisions")
        return cur.fetchone()[0]
    finally:
        cur.close()


class TestUS11TemporalCoexistence:
    def test_temporal_writes_and_purges_create_no_revisions(self, engine):
        head = engine.ledger.head().revision_id
        n = _rev_count(engine.conn)
        for i in range(5):
            assert engine.create_edge_temporal("a", "CALLS", "b", timestamp=TS + i, weight=1.0)
        assert _kg(engine, "tout", TS, "a", "CALLS", "b") != ""
        bucket = TS // 300
        assert _kg(engine, "tagg", bucket, "a", "CALLS", "count") == "5"
        engine.purge_raw_before(TS + 3)
        assert (
            _kg(engine, "tout", TS, "a", "CALLS", "b") == ""
            and _kg(engine, "tout", TS + 3, "a", "CALLS", "b") != ""
        )
        assert (
            _kg(engine, "tagg", bucket, "a", "CALLS", "count") == "5"
        )  # aggregates untouched by raw purge
        engine.purge_bucket_range(bucket, bucket)
        assert _kg(engine, "tagg", bucket, "a", "CALLS", "count") == ""
        assert engine.ledger.head().revision_id == head and _rev_count(engine.conn) == n
        assert engine.ledger.verify().result == "equal"

    def test_rebuilds_leave_ledger_intact(self, engine):
        head = engine.ledger.head()
        n = _rev_count(engine.conn)
        engine.create_edge_temporal("a", "CALLS", "b", timestamp=TS)
        engine.sync()
        engine._iris_obj().classMethodValue("Graph.KG.Traversal", "BuildKG")
        assert engine.ledger.head().revision_id == head.revision_id and _rev_count(engine.conn) == n
        assert engine.ledger.verify().result == "equal"
        assert int(engine._iris_obj().isDefined("^IVG.Ledger")) > 0

    def test_commit_timestamp_is_system_utc_ms_not_event_time(self, engine):
        engine.create_edge_temporal("a", "CALLS", "b", timestamp=TS)
        before = int(time.time() * 1000)
        cs = Changeset(actor="x", actor_type="human")
        cs.set_property("a", "k", "v")
        rev = engine.ledger.commit(cs).revision
        after = int(time.time() * 1000)
        assert before - 5000 <= rev.committed_ms <= after + 5000
        assert rev.committed_ms not in (TS, TS * 1000)

    def test_temporal_edge_between_governed_nodes_is_not_structural(self, engine):
        engine.create_edge_temporal("a", "CALLS", "b", timestamp=TS)
        assert _kg(engine, "out", 0, "a", "CALLS", "b") != ""  # adjacency shadow exists
        st = engine.ledger.reconstruct(engine.ledger.head().revision_id)
        assert ("a", "CALLS", "b", None) not in {s.tuple for s in st.statements.values()}
        assert ("a", "CALLS", "b", None) not in canonical_tables(engine.conn)["rels"]
        assert engine.ledger.verify().result == "equal"

    def test_mirrored_temporal_row_classification(self, engine):
        assert engine.create_edge_temporal("a", "T2", "b", timestamp=TS + 1, graph="g")
        assert ("a", "T2", "b", "g") in canonical_tables(engine.conn)["rels"]
        report = engine.ledger.verify()
        assert report.result == "diverges" and report.classification == "unrecorded_writes"
        engine.ledger.verify(adopt=True)
        engine.ledger.set_strict(True)
        before = canonical_tables(engine.conn)
        assert engine.create_edge_temporal("a", "T3", "b", timestamp=TS + 2, graph="g")
        assert _kg(engine, "tout", TS + 2, "a", "T3", "b") != ""
        assert canonical_tables(engine.conn) == before
        assert engine.ledger.verify().result == "equal"
