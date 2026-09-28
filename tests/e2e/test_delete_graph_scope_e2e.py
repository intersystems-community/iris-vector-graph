"""Cypher node DELETE against live IRIS: it removes the rows its MATCH bound.

Three graphs share node ID `x`. A and B hold a node row for it; C holds a label and a
property for `x` and no node row, written through SQL. Edges and vectors cannot be
orphaned that way (`fk_edges_source`, `fk_emb_node` on `(graph_id, node_id)`); labels
and properties carry no foreign key.

- `USE GRAPH 'A'` deletes A's `x` and nothing of B's.
- With no `USE GRAPH` the MATCH reads the whole namespace and binds A's and B's
  `x`; both go. C's rows stay: the MATCH bound no `x` in C. Before the fix the
  DELETE ran by `node_id` alone and took C's label and property too.
"""

import contextlib

import pytest

pytestmark = [pytest.mark.e2e]

NODE = "ivgdel:x"
OTHER = "ivgdel:y"
A, B, C = "ivgdel-A", "ivgdel-B", "ivgdel-C"


def _count(conn, table, graph, col="s"):
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE graph_id = ? AND {col} = ?",
            (graph, NODE),
        )
        return cur.fetchone()[0]
    finally:
        cur.close()


def _wipe(conn):
    cur = conn.cursor()
    try:
        for g in (A, B, C):
            for table in ("rdf_edges", "rdf_labels", "rdf_props", "nodes"):
                with contextlib.suppress(Exception):
                    cur.execute(f"DELETE FROM Graph_KG.{table} WHERE graph_id = ?", (g,))
        conn.commit()
    finally:
        cur.close()


@pytest.fixture
def three_graphs(iris_connection):
    if iris_connection is None:
        pytest.fail("no live IRIS connection: this asserts what IRIS deletes")
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)
    for g, label in ((A, "Patient"), (B, "Member")):
        engine.create_node(NODE, labels=[label], properties={"name": g}, graph=g)
        engine.create_node(OTHER, labels=["Thing"], graph=g)
        engine.create_edge(NODE, "KNOWS", OTHER, graph=g)
    cur = iris_connection.cursor()
    cur.execute(
        "INSERT INTO Graph_KG.rdf_labels (s, label, graph_id) VALUES (?, 'Patient', ?)",
        (NODE, C),
    )
    cur.execute(
        'INSERT INTO Graph_KG.rdf_props (s, "key", val, graph_id) VALUES (?, ?, ?, ?)',
        (NODE, "name", C, C),
    )
    iris_connection.commit()
    cur.close()
    yield engine, iris_connection
    _wipe(iris_connection)


def _run(engine, cypher):
    res = engine.execute_cypher(cypher)
    assert getattr(res, "error", None) is None, res.error


def _holds_x(conn, g):
    return {
        "node": _count(conn, "nodes", g, "node_id"),
        "labels": _count(conn, "rdf_labels", g),
        "props": _count(conn, "rdf_props", g),
        "edges": _count(conn, "rdf_edges", g),
    }


GONE = {"node": 0, "labels": 0, "props": 0, "edges": 0}


def test_use_graph_delete_stays_in_its_graph(three_graphs):
    engine, conn = three_graphs
    b_before, c_before = _holds_x(conn, B), _holds_x(conn, C)
    assert b_before["node"] == 1 and b_before["edges"] == 1
    _run(engine, f"USE GRAPH '{A}' MATCH (n {{id: '{NODE}'}}) DETACH DELETE n")
    assert _holds_x(conn, A) == GONE
    assert _holds_x(conn, B) == b_before
    assert _holds_x(conn, C) == c_before


def test_graph_less_delete_removes_the_bound_rows_only(three_graphs):
    engine, conn = three_graphs
    c_before = _holds_x(conn, C)
    assert c_before == {"node": 0, "labels": 1, "props": 1, "edges": 0}
    _run(engine, f"MATCH (n {{id: '{NODE}'}}) DETACH DELETE n")
    for g in (A, B):
        assert _holds_x(conn, g) == GONE, g
    assert _holds_x(conn, C) == c_before


def test_graph_less_delete_without_detach_refuses_a_connected_node(three_graphs):
    engine, conn = three_graphs
    with pytest.raises(Exception, match="DETACH"):
        engine.execute_cypher(f"MATCH (n:Patient {{id: '{NODE}'}}) DELETE n")
    assert _holds_x(conn, A)["node"] == 1


def test_use_graph_delete_ignores_another_graphs_edges(three_graphs):
    """A's `x` has no edges left; B's `x` still has one. The check reads A only."""
    engine, conn = three_graphs
    _run(engine, f"USE GRAPH '{A}' MATCH ({{id: '{NODE}'}})-[r:KNOWS]->() DELETE r")
    assert _holds_x(conn, A)["edges"] == 0
    _run(engine, f"USE GRAPH '{A}' MATCH (n {{id: '{NODE}'}}) DELETE n")
    assert _holds_x(conn, A) == GONE
    assert _holds_x(conn, B)["node"] == 1 and _holds_x(conn, B)["edges"] == 1
