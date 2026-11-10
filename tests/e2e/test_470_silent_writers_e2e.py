"""4.7.0 row 3c: two silent failures in the loaders; row 11: DETACH DELETE and reifications; row 17: the writer lock.

(i) `BulkLoader.build_graph_globals` sent `Do ##class(Graph.KG.Traversal).BuildKG()` as SQL: IRIS answered SQLCODE -51
("SQL statement expected"), the method logged it and answered False, and `^KG` was never rebuilt. `load_networkx`
put that False in `stats["globals_built"]` and returned as if the load were complete. Now it runs the class method
on the loader's connection, as the engine's `sync()` does, and a `%Status` error raises.

(ii) `attach_embeddings_to_table` stores each row's vector for the node `<label>:<id>`; `store_embedding` refuses a
node that does not exist, and the method logged it and skipped the row, so a mapped table whose rows have no nodes
embedded 0 rows and answered `{"embedded": 0, ...}` with nothing else said.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_silent_writers_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
DATA_TABLE = "SQLUser.ivg470wr_data"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


class Fake:
    def encode(self, texts, **_):
        import numpy as np

        if isinstance(texts, str):
            return np.full(rr.DIM, 0.25)
        return np.full((len(texts), rr.DIM), 0.25)


@pytest.fixture
def conn():
    c = _connect()
    yield c
    c.close()


def _uid() -> str:
    return "t470w" + uuid.uuid4().hex[:10]


def _kg_out(conn, s, p, o) -> int:
    import iris

    return int(iris.createIRIS(conn).isDefined("^KG", "out", 0, s, p, o))


def _kill_kg(conn, *ids):
    import iris

    native = iris.createIRIS(conn)
    for nid in ids:
        native.kill("^KG", "out", 0, nid)
        native.kill("^KG", "in", 0, nid)


# ------------------------------------------------------------------------------- (i) build_graph_globals


def test_build_graph_globals_rebuilds_kg(conn):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    loader = BulkLoader(conn)
    loader.load_nodes([(f"{u}a", {"namespace": "T470W"}), (f"{u}b", {"namespace": "T470W"})])
    loader.load_edges([(f"{u}a", "R470W", f"{u}b", None)])
    loader.rebuild_all_indices()
    _kill_kg(conn, f"{u}a", f"{u}b")
    assert _kg_out(conn, f"{u}a", "R470W", f"{u}b") == 0, "the test could not take the edge out of ^KG"
    assert loader.build_graph_globals() is True
    assert _kg_out(conn, f"{u}a", "R470W", f"{u}b") > 0, "^KG was not rebuilt"
    eng = IRISGraphEngine(_connect(), embedding_dimension=rr.DIM)
    try:
        r = eng.execute_cypher("MATCH (a {id: $a})-[:R470W]->(b) RETURN b.id", {"a": f"{u}a"}, raise_on_error=True)
        assert [row[0] for row in r.rows] == [f"{u}b"]
    finally:
        eng.conn.close()


def test_load_networkx_builds_the_globals(conn):
    import networkx as nx

    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    g = nx.DiGraph()
    g.add_node(f"{u}a", namespace="T470W")
    g.add_node(f"{u}b", namespace="T470W")
    g.add_edge(f"{u}a", f"{u}b", predicate="R470W")
    stats = BulkLoader(conn).load_networkx(g)
    assert stats["globals_built"] is True, stats
    assert sum(_kg_out(conn, f"{u}a", p, f"{u}b") > 0 for p in ("R470W", "RELATED_TO", "related_to")) == 1


# ------------------------------------------------------------------------------- (ii) attach_embeddings_to_table


@pytest.fixture
def data_table(conn):
    cur = conn.cursor()
    try:
        cur.execute(f"DROP TABLE {DATA_TABLE}")
    except Exception:  # noqa: BLE001 - absent on the first run
        pass
    cur.execute(f"CREATE TABLE {DATA_TABLE} (k VARCHAR(80) PRIMARY KEY, txt VARCHAR(200))")
    yield DATA_TABLE
    cur.execute(f"DROP TABLE {DATA_TABLE}")


def test_attach_embeddings_reports_rows_without_a_node(conn, data_table):
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    label = f"L{u}"
    cur = conn.cursor()
    for i in range(3):
        cur.execute(f"INSERT INTO {data_table} (k, txt) VALUES (?, ?)", [f"{u}{i}", f"text {i}"])
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    eng.embedder = Fake()
    eng.map_sql_table(data_table, "k", label)
    assert eng.create_node(f"{label}:{u}0")  # one of the three rows has its node
    result = eng.attach_embeddings_to_table(label, ["txt"], force=True)
    assert result["embedded"] == 1, result
    assert result["no_node"] == 2, result
    assert sorted(result["no_node_ids"]) == [f"{label}:{u}1", f"{label}:{u}2"], result
    assert result["total"] == 3


# ------------------------------------------------------------------------------- row 11: DETACH DELETE and reifiers


def _edge_id(conn, s, p, o):
    cur = conn.cursor()
    cur.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?", [s, p, o])
    row = cur.fetchone()
    return None if row is None else int(row[0])


def _count(conn, sql, params):
    cur = conn.cursor()
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


@pytest.mark.parametrize("which", ("source", "target"))
def test_detach_delete_takes_the_reification_and_its_reifier_node(conn, which):
    """As delete_node does (API_REFERENCE: "... incident edges, and the reifier nodes of those edges"): the edge's
    rdf_reifications row and its reifier node go with it. On 4.6.5 the row stayed (1 before, 1 after; edges 0)."""
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    assert eng.create_edge(f"{u}a", "R470R", f"{u}b")
    eid = _edge_id(conn, f"{u}a", "R470R", f"{u}b")
    reifier = eng.reify_edge(eid, reifier_id=f"{u}r", props={"since": "2020"})
    assert reifier == f"{u}r"
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE edge_id = ?", [eid]) == 1
    victim = f"{u}a" if which == "source" else f"{u}b"
    eng.execute_cypher("MATCH (n {id: $v}) DETACH DELETE n", {"v": victim}, raise_on_error=True)
    assert _edge_id(conn, f"{u}a", "R470R", f"{u}b") is None
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE edge_id = ?", [eid]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}r"]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [f"{u}r"]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ?", [f"{u}r"]) == 0
    other = f"{u}b" if which == "source" else f"{u}a"
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [other]) == 1


def test_detach_delete_of_a_reifier_takes_its_reification_and_keeps_the_edge(conn):
    """A reifier is a node; DETACH DELETE of it is delete_reification's effect (its link, labels, props, row), and
    the edge it reified stays."""
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    assert eng.create_edge(f"{u}a", "R470R", f"{u}b")
    eid = _edge_id(conn, f"{u}a", "R470R", f"{u}b")
    eng.reify_edge(eid, reifier_id=f"{u}r")
    eng.execute_cypher("MATCH (n {id: $v}) DETACH DELETE n", {"v": f"{u}r"}, raise_on_error=True)
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}r"]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [f"{u}r"]) == 0
    assert _edge_id(conn, f"{u}a", "R470R", f"{u}b") == eid


def test_detach_delete_in_a_named_graph_takes_its_edges_reification(conn):
    """USE GRAPH: the edge is the named graph's, its reifier a default-graph node (reify_edge); both go, and a
    default-graph node of the same id as the deleted one keeps its own edge's reification."""
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    g = "g470r" + u
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    assert eng.create_edge(f"{u}a", "R470R", f"{u}b", graph=g)
    assert eng.create_edge(f"{u}a", "R470R", f"{u}b")  # the same triple in the default graph
    cur = conn.cursor()
    cur.execute("SELECT edge_id, graph_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'R470R'", [f"{u}a"])
    by_graph = {str(r[1] or ""): int(r[0]) for r in cur.fetchall()}
    eng.reify_edge(by_graph[g], reifier_id=f"{u}rg")
    eng.reify_edge(by_graph[""], reifier_id=f"{u}rd")
    eng.execute_cypher(f"USE GRAPH '{g}' MATCH (n {{id: $v}}) DETACH DELETE n", {"v": f"{u}a"}, raise_on_error=True)
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}rg"]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [f"{u}rg"]) == 0
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}rd"]) == 1
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [f"{u}rd"]) == 1
    assert _edge_id(conn, f"{u}a", "R470R", f"{u}b") == by_graph[""]


# ------------------------------------------------------------------------------- row 17: the adjacency writer lock
#
# Another process holds ^IVG.AdjW(0) exclusively. IRIS's SQL filing does not read the error a functional index callback
# returns or throws, so on 4.6.5 a writer whose shared lock was not granted in 5 s left the row written and ^KG without
# the edge (create_edge answered True after 5.02 s; a raw INSERT the same). Now the writer goes on without the lock
# (Graph.KG.Adjacency.WriterLock): the edge is in ^KG, ^KG("gen") and ^KG("__version") move as ever (so ^IVG.AlgoCache
# and ^ArnoKG miss), and ^IVG.AdjNoLock(graph) stops the degree cache storing until a rebuild clears it.


@pytest.fixture
def held_writer_lock():
    """^IVG.AdjW(0) held exclusively by a second connection, released in teardown whatever the test did."""
    import iris

    holder = _connect()
    other = iris.createIRIS(holder)
    other.lock("", 1, "^IVG.AdjW", 0)
    try:
        yield other
    finally:
        try:
            other.unlock("", "^IVG.AdjW", 0)
            other.kill("^IVG.AdjNoLock")  # the lock-less writes' marker, which sync() would clear (status() reads it)
        finally:
            holder.close()


def _lock_free(conn) -> bool:
    import iris

    probe = iris.createIRIS(_connect())
    try:
        probe.lock("", 0, "^IVG.AdjW", 0)
    except RuntimeError:
        return False
    probe.unlock("", "^IVG.AdjW", 0)
    return True


@pytest.mark.parametrize("writer", ("create_edge", "cypher_create", "raw_sql_insert"))
def test_a_write_while_the_writer_lock_is_held_still_writes_kg(conn, held_writer_lock, writer):
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    if writer == "create_edge":
        assert eng.create_edge(f"{u}a", "R470L", f"{u}b") is True
    elif writer == "cypher_create":
        eng.execute_cypher("CREATE (:T470L {id: $a})-[:R470L]->(:T470L {id: $b})", {"a": f"{u}a", "b": f"{u}b"},
                           raise_on_error=True)
    else:
        cur = conn.cursor()
        for n in (f"{u}a", f"{u}b"):
            cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, '')", [n])
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, 'R470L', ?, '')",
                    [f"{u}a", f"{u}b"])
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ?", [f"{u}a"]) == 1
    assert _kg_out(conn, f"{u}a", "R470L", f"{u}b") > 0, "the row is written and ^KG has no entry for it"


def test_no_cached_answer_from_during_the_hold_is_served_after_it(conn):
    """A WCC answer cached while the lock is held, before a write without the lock, is a miss after it: the next call
    recomputes and joins the two nodes the write connected. The degree cache stores nothing for the graph until sync()
    rebuilds ^KG, and stores again after."""
    import iris

    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    assert eng.create_edge(f"{u}a", "R470L", f"{u}c")  # two components: {a, c} and {b, d}
    assert eng.create_edge(f"{u}b", "R470L", f"{u}d")
    native = iris.createIRIS(conn)
    holder = _connect()
    other = iris.createIRIS(holder)
    other.lock("", 1, "^IVG.AdjW", 0)
    try:
        def component(rows, nid):
            return {r[0]: r[1] for r in rows}[nid]

        before = eng._store.execute_wcc().rows
        assert component(before, f"{u}a") != component(before, f"{u}b")
        assert eng.create_edge(f"{u}b", "R470L", f"{u}a") is True  # without the lock
        assert int(native.isDefined("^IVG.AdjNoLock", 0)) > 0
        after = eng._store.execute_wcc().rows
        assert component(after, f"{u}a") == component(after, f"{u}b"), "the answer cached before the write was served"
    finally:
        other.unlock("", "^IVG.AdjW", 0)
        holder.close()
    native.kill("^KG", "degc", 0, f"{u}a")
    assert int(native.classMethodValue("Graph.KG.Adj", "Degree", 0, f"{u}a")) == 1
    assert int(native.isDefined("^KG", "degc", 0, f"{u}a")) == 0, "a degree was stored while a lock-less write stands"
    eng.sync()
    assert int(native.isDefined("^IVG.AdjNoLock", 0)) == 0, "the rebuild did not clear the marker"
    assert int(native.classMethodValue("Graph.KG.Adj", "Degree", 0, f"{u}a")) == 1
    assert int(native.isDefined("^KG", "degc", 0, f"{u}a")) > 0


def test_no_writer_lock_is_left_behind(conn, held_writer_lock):
    from iris_vector_graph import IRISGraphEngine

    u = _uid()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    assert eng.create_edge(f"{u}a", "R470L", f"{u}b")
    held_writer_lock.unlock("", "^IVG.AdjW", 0)
    assert _lock_free(conn), "a writer kept ^IVG.AdjW(0) after its write"
    held_writer_lock.lock("", 1, "^IVG.AdjW", 0)  # the fixture releases it
