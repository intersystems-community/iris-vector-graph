"""Spec 213 US5 — existing non-versioned APIs keep working (live ivg-iris-enterprise).

T051: (1) never enabled → no ledger data; (2) default mode → legacy and Cypher writes work,
verification reports unrecorded writes; (3) strict mode → every guarded API is rejected and
tables unchanged, temporal mirror suppressed, counter bumped; (4) maintenance operations
leave ledger storage intact; (5) FR-048 namespace independence (skips when IVGTEST shares
USER's database, as it does on the enterprise container).
"""

import os
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.ledger import Changeset, LedgerStrictModeError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    return make_engine(iris_connection)


def _rev_count(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.ledger_revisions")
        return cur.fetchone()[0]
    finally:
        cur.close()


def _ledger_global_defined(engine) -> bool:
    return bool(int(engine._iris_obj().isDefined("^IVG.Ledger")))


def _seed_via_ledger(engine):
    cs = Changeset(actor="seed", actor_type="system")
    cs.create_node("a", labels=["L"], properties={"k": "v"})
    cs.create_node("b")
    cs.create_relationship("a", "R", "b", qualifiers={"weight": "1.0"})
    return engine.ledger.commit(cs)


class TestNeverEnabled:
    def test_legacy_writes_create_no_ledger_data(self, engine):
        engine.create_node("n1", labels=["L"], properties={"p": "1"})
        engine.create_edge("n1", "R", "n1") if False else None
        engine.create_node("n2")
        engine.create_edge("n1", "R", "n2")
        engine.execute_cypher("CREATE (x {node_id:'n3'})")
        engine.delete_edge("n1", "R", "n2")
        assert not _ledger_global_defined(engine)
        assert _rev_count(engine.conn) == 0  # table exists after compilation; no rows (FR-040)
        assert engine.ledger.meta()["state"] == "never-enabled"


class TestDefaultMode:
    def test_legacy_and_cypher_writes_work_and_are_detectable(self, engine):
        engine.ledger.enable()
        _seed_via_ledger(engine)
        head = engine.ledger.head().revision_id
        assert engine.create_node("legacy-1", labels=["X"]) is True
        assert engine.create_edge("legacy-1", "R", "a") is True
        assert engine.delete_edge("a", "R", "b") is True
        engine.execute_cypher("CREATE (c {node_id:'cy-1'})")
        engine.execute_cypher("MATCH (c {node_id:'cy-1'}) DETACH DELETE c")
        assert engine.ledger.head().revision_id == head  # no revision created
        report = engine.ledger.verify()
        assert report.result == "diverges"
        assert report.classification == "unrecorded_writes"
        touched = {e.entity_id for e in report.differences}
        assert "legacy-1" in touched


class TestStrictMode:
    def test_every_guarded_api_is_rejected_and_nothing_changes(self, engine, tmp_path):
        engine.ledger.enable(strict=True)
        _seed_via_ledger(engine)
        before = canonical_tables(engine.conn)
        strict_before = engine.ledger.stats().rejections.get("rej_strict_block", 0)
        calls = [
            lambda: engine.create_node("s1"),
            lambda: engine.store_node("s2"),
            lambda: engine.create_edge("a", "R", "a"),
            lambda: engine.store_edge("a", "Q", "b"),
            lambda: engine.set_edge_weight("a", "R", "b", 2.0),
            lambda: engine.delete_edge("a", "R", "b"),
            lambda: engine.delete_node("b"),
            lambda: engine.bulk_create_nodes([{"id": "bulk1"}]),
            lambda: engine.bulk_create_edges([{"s": "a", "p": "R", "o": "a"}]),
            lambda: engine.bulk_ingest_edges([{"s": "a", "o": "a"}]),
            lambda: engine.bulk_delete_nodes(["a"]),
            lambda: engine.drop_graph("g"),
            lambda: engine.reify_edge(1),
            lambda: engine.delete_reification("reif:1"),
            lambda: engine.materialize_inference(),
            lambda: engine.retract_inference(),
            lambda: engine.import_graph_ndjson(str(tmp_path / "nope.ndjson")),
            lambda: engine.restore_snapshot(str(tmp_path / "nope.snapshot")),
            lambda: engine.load_networkx(MagicMock()),
            lambda: engine.execute_cypher("CREATE (n {node_id:'cy-strict'})"),
            lambda: engine.execute_cypher("MATCH (n {node_id:'a'}) SET n.k = 'z'"),
            lambda: engine._store.write_nodes([{"id": "sn1"}]),
            lambda: engine._store.write_edges([{"s": "a", "p": "R", "o": "a"}]),
            lambda: engine._store.delete_nodes(["a"]),
            lambda: engine._store.delete_edges([("a", "R", "b")]),
        ]
        for i, call in enumerate(calls):
            with pytest.raises(LedgerStrictModeError):
                call()
            assert canonical_tables(engine.conn) == before, f"call #{i} changed state"
        assert engine.ledger.stats().rejections["rej_strict_block"] >= strict_before + len(calls)
        # reads still work
        assert engine.execute_cypher("MATCH (n {node_id:'a'}) RETURN n.k AS k")["rows"][0][0] == "v"
        # ledger commits still work
        cs = Changeset(actor="x", actor_type="human")
        cs.create_node("via-ledger")
        engine.ledger.commit(cs)
        assert "via-ledger" in canonical_tables(engine.conn)["nodes"]

    def test_temporal_insert_with_graph_skips_structural_mirror(self, engine):
        engine.ledger.enable(strict=True)
        _seed_via_ledger(engine)
        before = canonical_tables(engine.conn)
        assert (
            engine.create_edge_temporal("a", "T", "b", timestamp=1_700_000_000, graph="g") is True
        )
        tout = str(
            engine._iris_obj().classMethodValue(
                "Graph.KG.Meta", "GetKG", "tout", "1700000000", "a", "T", "b"
            )
        )
        assert tout != ""
        assert canonical_tables(engine.conn) == before  # no rdf_edges row for graph g
        # default mode: the mirror is written and shows up as an unrecorded write
        engine.ledger.set_strict(False)
        assert (
            engine.create_edge_temporal("a", "T2", "b", timestamp=1_700_000_001, graph="g") is True
        )
        assert ("a", "T2", "b", "g") in canonical_tables(engine.conn)["rels"]
        report = engine.ledger.verify()
        assert report.result == "diverges" and report.classification == "unrecorded_writes"


class TestMaintenanceSurvival:
    def test_sync_purge_and_buildkg_leave_ledger_intact(self, engine):
        engine.ledger.enable()
        res = _seed_via_ledger(engine)
        head = engine.ledger.head()
        n = _rev_count(engine.conn)
        engine.create_edge_temporal("a", "T", "b", timestamp=1_000)
        io = engine._iris_obj()
        io.classMethodValue("Graph.KG.TemporalIndex", "PurgeRawBefore", 10**12)
        engine.sync()
        io.classMethodValue("Graph.KG.Traversal", "BuildKG")
        assert engine.ledger.head().revision_id == head.revision_id
        assert _rev_count(engine.conn) == n
        rev = engine.ledger.get_revision(res.revision.revision_id)
        assert len(rev.records) == res.revision.op_count
        assert engine.ledger.verify().result == "equal"


class TestNamespaceIndependence:
    def test_two_namespaces_have_independent_ledgers(self, engine):
        import iris.dbapi as dbapi

        conn = engine.conn
        try:
            other = dbapi.connect(
                hostname=conn.hostname,
                port=conn.port,
                namespace="IVGTEST",
                username="_SYSTEM",
                password="SYS",
            )
        except Exception as e:
            pytest.skip(f"IVGTEST namespace not available: {e}")
        try:
            import iris as _iris

            io_user = engine._iris_obj()
            io_other = _iris.createIRIS(other)
            marker = "ns-probe"
            io_user.set(1, "^IVG.LedgerNsProbe", marker)
            shared = bool(int(io_other.isDefined("^IVG.LedgerNsProbe", marker)))
            io_user.kill("^IVG.LedgerNsProbe")
            if shared:
                pytest.skip(
                    "IVGTEST shares USER's globals database on this container; "
                    "FR-048 independence requires separate databases"
                )
            eng2 = make_engine(other, namespace="IVGTEST")
            engine.ledger.enable()
            eng2.ledger.enable()
            cs = Changeset(actor="a", actor_type="human", idempotency_key="shared-key")
            cs.create_node("ns-a")
            engine.ledger.commit(cs)
            cs2 = Changeset(actor="a", actor_type="human", idempotency_key="shared-key")
            cs2.create_node("ns-b")
            r2 = eng2.ledger.commit(cs2)
            assert r2.replayed is False  # key scoped per ledger
            assert engine.ledger.head().revision_id != eng2.ledger.head().revision_id
            assert engine.ledger.head().seq == 2 and eng2.ledger.head().seq == 2
        finally:
            other.close()
