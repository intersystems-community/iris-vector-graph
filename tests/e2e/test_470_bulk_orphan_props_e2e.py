"""4.7.0 gate: `bulk_create_nodes` of a node whose old property rows outlived it.

`rdf_props` has no foreign key on `nodes`, so a node whose row is deleted by SQL leaves its property rows. On 4.7.0
before this fix `bulk_create_nodes` of that id again returned the id and kept the old values: made with `{a: 1}`, its
`nodes` row deleted, then `bulk_create_nodes([{"id": ..., "properties": {"a": 2}}])`, and `RETURN n.a` answered 1
(the property INSERT is guarded, so it skipped the old row). `create_node` replaces such rows since the gate fix
before this one; now both writers do, on both of `bulk_create_nodes`' routes: the server loader
(`Graph.KG.EdgeScan.BulkIngestNodesSQL`, taken once `initialize_schema()` has found the classes and no node names a
graph) and the batch SQL (a fresh engine, or a node naming a graph). Only a node the call inserts is replaced: a node
already there is merged into as before, keeping every value it has and gaining what it lacks.

The known answers are the values the test writes: `a` is 1 before and must be 2 after for a new node, and must stay 1
for an existing one.

    IVG_R470_WR_NS=IVG470BK pytest tests/e2e/test_470_bulk_orphan_props_e2e.py
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]

#: (route, graph): the loader writes the default graph only; the SQL route is a fresh engine's (no setup run, so no
#: capabilities known), and a named graph always goes that way
ROUTES = [("loader", ""), ("sql", ""), ("sql", "g470bulk")]
ROUTE_IDS = ["loader", "sql-default_graph", "sql-named_graph"]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def engines():
    """{"loader": an engine after setup, "sql": a fresh one}, each on its own connection."""
    from iris_vector_graph import IRISGraphEngine

    conns = [_connect(), _connect()]
    loader = IRISGraphEngine(conns[0], embedding_dimension=rr.DIM)
    loader.initialize_schema()
    assert loader._known_capabilities.objectscript_deployed, "setup found no classes: the loader route cannot run"
    sql = IRISGraphEngine(conns[1], embedding_dimension=rr.DIM)
    assert not sql._known_capabilities.objectscript_deployed
    yield {"loader": loader, "sql": sql}
    for c in conns:
        c.close()


@pytest.fixture
def ids():
    """Fresh ids, and every row of them deleted after the test (props by SQL: an orphan has no node to delete)."""
    made: list = []

    def new() -> str:
        made.append("t470b" + uuid.uuid4().hex[:10])
        return made[-1]

    yield new
    conn = _connect()
    cur = conn.cursor()
    for nid in made:
        for table, col in (("rdf_labels", "s"), ("rdf_props", "s"), ("nodes", "node_id")):
            cur.execute(f"DELETE FROM Graph_KG.{table} WHERE {col} = ?", [nid])
    conn.commit()
    conn.close()


def _props(conn, nid, graph) -> dict:
    cur = conn.cursor()
    cur.execute('SELECT "key", val FROM Graph_KG.rdf_props WHERE s = ? AND graph_id = ?', [nid, graph])
    return {str(k): v for k, v in cur.fetchall()}


def _orphan(eng, nid, graph, props) -> None:
    """A node with ``props`` whose `nodes` row is then deleted by SQL: its property rows stay."""
    assert eng.create_node(nid, properties=props, graph=graph or None)
    cur = eng.conn.cursor()
    cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?", [nid, graph])
    eng.conn.commit()
    assert _props(eng.conn, nid, graph) == {**props, "id": nid}, "the setup left no orphaned rows"


def _read_a(eng, nid):
    """`n.a` as Cypher reads it in the default graph (the reported symptom)."""
    return [r[0] for r in eng.execute_cypher(f"MATCH (n {{id: '{nid}'}}) RETURN n.a", raise_on_error=True).rows]


def _bulk(eng, route, nodes, graph, caplog):
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        out = eng.bulk_create_nodes([{**n, "graph": graph} if graph else n for n in nodes])
    fell_back = [r.getMessage() for r in caplog.records if "falling back to SQL path" in r.getMessage()]
    assert not fell_back, f"the {route} route fell back to SQL: {fell_back}"
    return out


@pytest.mark.parametrize("route, graph", ROUTES, ids=ROUTE_IDS)
def test_a_new_node_over_orphaned_properties_holds_what_it_is_given(engines, ids, route, graph, caplog):
    eng, nid = engines[route], ids()
    _orphan(engines["sql"], nid, graph, {"a": 1, "b": "old"})
    assert _bulk(eng, route, [{"id": nid, "properties": {"a": 2}}], graph, caplog) == [nid]
    assert _props(eng.conn, nid, graph) == {"a": 2, "id": nid}
    if not graph:
        assert _read_a(eng, nid) == [2]


@pytest.mark.parametrize("route, graph", ROUTES, ids=ROUTE_IDS)
def test_an_existing_node_is_merged_into_as_before(engines, ids, route, graph, caplog):
    eng, nid = engines[route], ids()
    assert engines["sql"].create_node(nid, properties={"a": 1, "b": "kept"}, graph=graph or None)
    assert _bulk(eng, route, [{"id": nid, "properties": {"a": 2, "c": "new"}}], graph, caplog) == [nid]
    assert _props(eng.conn, nid, graph) == {"a": 1, "b": "kept", "c": "new", "id": nid}


@pytest.mark.parametrize("route, graph", ROUTES, ids=ROUTE_IDS)
def test_a_mixed_batch_replaces_only_the_new_nodes_orphans(engines, ids, route, graph, caplog):
    eng = engines[route]
    orphaned, existing, fresh = ids(), ids(), ids()
    _orphan(engines["sql"], orphaned, graph, {"a": 1})
    assert engines["sql"].create_node(existing, properties={"a": 1}, graph=graph or None)
    batch = [{"id": orphaned, "properties": {"a": 2}}, {"id": existing, "properties": {"a": 2, "c": 3}},
             {"id": fresh, "properties": {"a": 2}}, {"id": orphaned, "properties": {"a": 3, "d": 4}}]
    assert _bulk(eng, route, batch, graph, caplog) == [orphaned, existing, fresh, orphaned]
    assert _props(eng.conn, orphaned, graph) == {"a": 2, "d": 4, "id": orphaned}  # its repeat merges into the new one
    assert _props(eng.conn, existing, graph) == {"a": 1, "c": 3, "id": existing}
    assert _props(eng.conn, fresh, graph) == {"a": 2, "id": fresh}


def test_another_graphs_rows_of_that_id_stay(engines, ids, caplog):
    """The default graph's node goes to the loader; its id's rows in another graph are not its."""
    nid = ids()
    _orphan(engines["sql"], nid, "g470bulk", {"a": 9})
    _orphan(engines["sql"], nid, "", {"a": 1})
    assert _bulk(engines["loader"], "loader", [{"id": nid, "properties": {"a": 2}}], "", caplog) == [nid]
    assert _props(engines["loader"].conn, nid, "") == {"a": 2, "id": nid}
    assert _props(engines["loader"].conn, nid, "g470bulk") == {"a": 9, "id": nid}


def test_the_class_method_itself_replaces_them(engines, ids):
    """BulkIngestNodesSQL called directly, as a server-side caller would: no Python fallback can stand in for it."""
    import iris

    eng, nid, kept = engines["loader"], ids(), ids()
    _orphan(engines["sql"], nid, "", {"a": 1})
    assert engines["sql"].create_node(kept, properties={"a": 1})
    native = iris.createIRIS(eng.conn)
    nodes = [{"id": nid, "props": {"a": 2, "id": nid}}, {"id": kept, "props": {"a": 2, "id": kept}}]
    assert int(native.classMethodValue("Graph.KG.EdgeScan", "BulkIngestNodesSQL", json.dumps(nodes))) == 2
    assert _props(eng.conn, nid, "") == {"a": 2, "id": nid}
    assert _props(eng.conn, kept, "") == {"a": 1, "id": kept}
