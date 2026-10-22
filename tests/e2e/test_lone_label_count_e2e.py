"""A whole-label count read from the label rows answers as the join over `nodes` did, scoped and unscoped.

Three Person nodes in the default graph, two in a named graph (one sharing an id with the default graph), and a
node of another label: the counts per graph, unscoped, and `count(DISTINCT p)` (distinct ids across graphs).
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
L, OTHER, G = f"Lc{T}", f"Lo{T}", f"lc{T}_g"


def n(k):
    return f"lc{T}_{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    # the SQL path's answer (the native planner declines a plain whole-label count anyway; with count(DISTINCT p)
    # unscoped it would answer natively, counting (graph, id): KNOWN_ISSUES)
    e = IRISGraphEngine(iris_connection, native_cypher=False)
    for k in "abc":
        e.create_node(n(k), labels=[L])
    e.create_node(n("x"), labels=[OTHER])
    for k in "ad":
        e.create_node(n(k), labels=[L], graph=G)
    yield e
    for k in "abcx":
        e.delete_node(n(k))
    e.erase_graph(G)


@pytest.mark.parametrize(
    "prefix,ret,want",
    [
        ("USE GRAPH '' ", "count(p) AS c", [[3]]),
        (f"USE GRAPH '{G}' ", "count(p) AS c", [[2]]),
        (f"USE GRAPH '{G}' ", "count(*) AS c, count(DISTINCT p) AS d", [[2, 2]]),
        ("", "count(p) AS c, count(DISTINCT p) AS d", None),  # unscoped: compared with the join below
    ],
)
def test_the_label_rows_count_as_the_join_did(engine, prefix, ret, want):
    q = f"{prefix}MATCH (p:{L}) RETURN {ret}"
    r = engine.execute_cypher(q)
    assert getattr(r, "error", None) is None, r.error
    assert "nodes" not in (r.sql or ""), r.sql
    if want is not None:
        assert r.rows == want
    # the same counts through the join (a second label forces it), as the SQL path answered before
    cur = engine.conn.cursor()
    scope = "" if not prefix else " AND COALESCE(l.graph_id, '') = ?"
    args = [L] + ([prefix.split("'")[1]] if prefix else [])
    cur.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT n.node_id) FROM Graph_KG.nodes n JOIN Graph_KG.rdf_labels l "
        f"ON l.s = n.node_id AND l.graph_id = n.graph_id AND l.label = ?{scope}",
        args,
    )
    total, distinct = cur.fetchone()
    expected = [total] if ret == "count(p) AS c" else ([total, distinct] if "DISTINCT" in ret else [total])
    assert r.rows == [expected], (r.rows, expected)
