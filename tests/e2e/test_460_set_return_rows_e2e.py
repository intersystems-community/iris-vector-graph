"""4.6.0 R063: `MATCH ...-[r]->... SET r.z = 7 RETURN r.z` answers one row per matched relationship.

The graph: x -R460-> y, both in the default graph, and a second node with x's id (and its label) in a named graph,
with no edge there. The pattern matches one relationship, so one row. The read without SET correlated each join to
one graph and answered one row; the SET statement's RETURN joined on node_id alone, so x's row in the named graph
met the default graph's edge too: two rows ([[7], [7]]), four with the label joined.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    x, y, other, label = f"ivg460set_{run}_x", f"ivg460set_{run}_y", f"ivg460set{run}", f"Set460{run}"
    engine.create_node(x, labels=[label])
    engine.create_node(y)
    engine.create_edge(x, "R460", y)
    engine.create_node(x, labels=[label], graph=other)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, x, y, label
    for nid, graph in ((x, None), (y, None), (x, other)):
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=graph)


def _ask(engine, q, params):
    r = engine.execute_cypher(q, params)
    assert not getattr(r, "error", None), (q, r.error)
    return r.rows


def test_the_read_is_one_row(g):
    engine, x, _, _ = g
    assert _ask(engine, "MATCH (a {node_id: $x})-[r:R460]->(b) RETURN b.node_id AS b", {"x": x}) == [[g[2]]]


def test_set_then_return_is_one_row(g):
    engine, x, _, _ = g
    assert _ask(engine, "MATCH (a {node_id: $x})-[r:R460]->(b) SET r.z = 7 RETURN r.z", {"x": x}) == [[7]]


def test_set_then_return_by_label_is_one_row(g):
    engine, _, _, label = g
    assert _ask(engine, f"MATCH (a:{label})-[r]->(b) SET r.z = 8 RETURN r.z", {}) == [[8]]


def test_the_write_reached_the_one_relationship(g):
    engine, x, _, _ = g
    _ask(engine, "MATCH (a {node_id: $x})-[r:R460]->(b) SET r.z = 9 RETURN r.z", {"x": x})
    cur = engine.conn.cursor()
    try:
        cur.execute("SELECT %EXACT(qualifiers), %EXACT(COALESCE(graph_id, '')) FROM Graph_KG.rdf_edges WHERE s = ?", [x])
        rows = cur.fetchall()
    finally:
        cur.close()
    assert [(q.replace(" ", ""), gr) for q, gr in rows] == [('{"z":9}', "")], rows
