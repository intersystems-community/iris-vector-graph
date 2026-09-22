"""The composite node key, proved against the constraint that enforces it.

`rdf_edges` carries `fk_edges_source (graph_id, s)` and `fk_edges_dest
(graph_id, o_id)` against `nodes (graph_id, node_id)`. A node created in the
default graph therefore does not satisfy an edge written in a named graph — which
is the pre-227 idiom every caller used, because the key was `node_id` alone.

The failure is silent by construction: `create_edge` catches `SQLCODE -121`, logs
it, and returns the same False it returns for a duplicate edge, so a named-graph
load looks idempotent while writing nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS is set",
)

_GRAPH = "edge-endpoints-graph"


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _edge_rows(conn, s: str, graph: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND graph_id = ?", [s, graph]
        )
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _node_rows(conn, node_id: str, graph: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?",
            [node_id, graph],
        )
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def test_create_edge_in_a_named_graph_writes_its_row(engine, iris_connection):
    """The pre-227 idiom: the nodes exist, but in the default graph."""
    s, o = f"ep_s_{_suffix()}", f"ep_o_{_suffix()}"
    engine.create_node(s)
    engine.create_node(o)

    assert engine.create_edge(s, "KNOWS", o, graph=_GRAPH) is True, (
        "create_edge returned False, which it also returns for a duplicate — the "
        "foreign key refused the row and the caller cannot tell"
    )
    assert _edge_rows(iris_connection, s, _GRAPH) == 1
    assert _node_rows(iris_connection, s, _GRAPH) == 1
    assert _node_rows(iris_connection, o, _GRAPH) == 1


def test_create_edge_with_no_nodes_at_all_writes_its_row(engine, iris_connection):
    """The other idiom: edges first, nodes never — `nodes` is a registry, not input."""
    s, o = f"ep_bare_s_{_suffix()}", f"ep_bare_o_{_suffix()}"

    assert engine.create_edge(s, "KNOWS", o) is True
    assert _edge_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, o, "") == 1


def test_bulk_create_edges_in_a_named_graph(engine, iris_connection):
    s, o = f"ep_bulk_s_{_suffix()}", f"ep_bulk_o_{_suffix()}"

    engine.bulk_create_edges(
        [{"source_id": s, "predicate": "KNOWS", "target_id": o}],
        graph=_GRAPH,
        disable_indexes=False,
        auto_sync=False,
    )

    assert _edge_rows(iris_connection, s, _GRAPH) == 1
    assert _node_rows(iris_connection, o, _GRAPH) == 1


def test_bulk_ingest_edges_writes_its_rows(engine, iris_connection):
    s, o = f"ep_ing_s_{_suffix()}", f"ep_ing_o_{_suffix()}"

    engine.bulk_ingest_edges([{"s": s, "p": "KNOWS", "o": o}], auto_sync=False)

    assert _edge_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, o, "") == 1


def test_bulk_ingest_edges_objectscript_path_registers_endpoints(engine, iris_connection):
    """`BulkIngestEdgesSQL` itself, with no Python fallback available to hide it.

    `bulk_ingest_edges` wraps the ObjectScript call in `except Exception: fall back
    to SQL`, so the test above passes whether or not the class method worked. This
    one calls the class method directly: if `BulkIngestEdgesSQL` failed to register
    the endpoints, the composite foreign keys refuse the edge, the `Continue` skips
    the row, and the returned count is 0 while nothing landed.
    """
    import json

    from iris_vector_graph.schema import _call_classmethod_large

    if not engine.capabilities.objectscript_deployed:
        pytest.skip("Graph.KG.* not deployed — nothing to exercise on this container")

    s, o = f"ep_os_s_{_suffix()}", f"ep_os_o_{_suffix()}"
    written = int(
        _call_classmethod_large(
            engine._iris_obj(),
            "Graph.KG.EdgeScan",
            "BulkIngestEdgesSQL",
            json.dumps([{"s": s, "p": "KNOWS", "o": o}]),
            "KNOWS",
        )
    )

    assert written == 1, "the class method counted no edges — the INSERT was refused"
    assert _edge_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, o, "") == 1


def test_the_store_writes_its_edge_rows(engine, iris_connection):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    s, o = f"ep_store_s_{_suffix()}", f"ep_store_o_{_suffix()}"
    IRISGraphStore(iris_connection).write_edges([{"source": s, "predicate": "KNOWS", "target": o}])

    assert _edge_rows(iris_connection, s, "") == 1
    assert _node_rows(iris_connection, o, "") == 1


def test_an_endpoint_is_not_borrowed_from_another_graph(engine, iris_connection):
    """Ensuring the endpoint must not mean reusing another graph's node row."""
    node = f"ep_scope_{_suffix()}"
    engine.create_node(node, graph="some-other-graph")

    engine.create_edge(node, "KNOWS", node, graph=_GRAPH)

    assert _node_rows(iris_connection, node, _GRAPH) == 1
    assert _node_rows(iris_connection, node, "some-other-graph") == 1
