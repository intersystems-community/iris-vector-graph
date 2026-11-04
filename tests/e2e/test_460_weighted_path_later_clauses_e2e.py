"""4.6.0 R140: `CALL ivg.shortestPath.weighted(...) YIELD ...` keeps the MATCH, WITH and RETURN that follow it.

The engine's handler answered any query that began with this CALL, so the clauses after YIELD were dropped: a WITH ..
WHERE filtered nothing, a MATCH added no columns, and `RETURN totalCost` answered the path too.

The graph, worked by hand, in the default graph's ^KG (what DijkstraJson walks; the value of an entry is its weight):
a -R(2)-> b -R(2)-> d, a -R(5)-> d, a -S(1)-> c -S(1)-> d. From a to d the cheapest path is a, c, d at cost 2; over R
alone it is a, b, d at cost 4. The nodes are also rows in `nodes`, for the MATCH.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    import iris

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    native = iris.createIRIS(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"ws140_{run}_{k}" for k in "abcd"}
    for nid in ids.values():
        assert engine.create_node(nid, labels=["Ws140"])
    edges = [("a", "R", "b", 2), ("b", "R", "d", 2), ("a", "R", "d", 5), ("a", "S", "c", 1), ("c", "S", "d", 1)]
    for s, p, o, w in edges:
        native.set(w, "KG", "out", 0, ids[s], p, ids[o])
        native.set(w, "KG", "in", 0, ids[o], p, ids[s])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids
    for nid in ids.values():
        native.kill("KG", "out", 0, nid)
        native.kill("KG", "in", 0, nid)
        with contextlib.suppress(Exception):
            engine.delete_node(nid)


def _run(g, rest, types=""):
    engine, ids = g
    r = engine.execute_cypher(
        f"CALL ivg.shortestPath.weighted($a, $d, 'weight', 9999, 10, 'out'{types}) YIELD path, totalCost {rest}",
        {"a": ids["a"], "d": ids["d"]},
    )
    assert r.error is None, r.error
    return r


def test_a_with_where_after_yield_filters(g):
    assert _run(g, "WITH totalCost WHERE totalCost < 3 RETURN totalCost", ", ['R']").rows == []
    assert _run(g, "WITH totalCost WHERE totalCost < 5 RETURN totalCost", ", ['R']").rows == [[4.0]]


def test_a_match_after_yield_runs(g):
    _, ids = g
    r = _run(g, "MATCH (n {node_id: $a}) RETURN n.node_id AS n, totalCost")
    assert r.columns == ["n", "totalCost"]
    assert r.rows == [[ids["a"], 2.0]]


def test_a_return_of_one_yielded_name_answers_that_column(g):
    r = _run(g, "RETURN totalCost")
    assert r.columns == ["totalCost"] and r.rows == [[2.0]]


def test_an_expression_in_the_return(g):
    r = _run(g, "RETURN totalCost * 2 AS c")
    assert r.columns == ["c"] and [[float(v) for v in row] for row in r.rows] == [[4.0]]


def test_the_path_carried_through_with(g):
    _, ids = g
    r = _run(g, "WITH path, totalCost RETURN path, totalCost", ", ['R']")
    assert len(r.rows) == 1
    path = json.loads(r.rows[0][0]) if isinstance(r.rows[0][0], str) else r.rows[0][0]
    assert path["nodes"] == [ids["a"], ids["b"], ids["d"]] and float(r.rows[0][1]) == 4.0
