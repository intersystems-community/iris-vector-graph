"""4.6.0 R215: an edge written with ``create_edge(weight=...)`` reads back its weight, with its type, as the same
edge written by Cypher does.

Walkthrough 2 (W2): ``create_edge(weight=1.0)`` read back ``r.weight = None`` (a weight of 1.0 was not stored) and
``weight=2.0`` read back as the integer 2, so ``sum(r.weight)`` over weights 1.0 and 5.0 answered 5.

The graph, by hand: from ``w1``, edges R to ``w2`` (``weight=1.0``), ``w3`` (``weight=1``), ``w4`` (no weight),
``w5`` (``weight=2.0``) and ``w6`` (Cypher ``{weight: 1.0}``).
- ``r.weight`` in target order: 1.0, 1, null, 2.0, 1.0 (a float, an integer, null, a float, a float).
- ``sum(r.weight)`` = 1.0 + 1 + 2.0 + 1.0 = 5.0.
- ``WHERE r.weight = 1.0``: w2, w3, w6 (1 = 1.0 in openCypher); ``WHERE r.weight > 1.5``: w5.
- ``properties(r)``: ``{weight: 1.0}``, ``{weight: 1}``, ``{}``, ``{weight: 2.0}``, ``{weight: 1.0}``.
- the weighted path ``w1 -> w5`` costs 2.0, the stored weight, and ``w1 -> w2`` costs 1.0.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r215_{run}_{k}" for k in ("w1", "w2", "w3", "w4", "w5", "w6")}
    for nid in ids.values():
        assert e.create_node(nid, labels=["W215"])
    assert e.create_edge(ids["w1"], "R215", ids["w2"], weight=1.0)
    assert e.create_edge(ids["w1"], "R215", ids["w3"], weight=1)
    assert e.create_edge(ids["w1"], "R215", ids["w4"])
    assert e.create_edge(ids["w1"], "R215", ids["w5"], weight=2.0)
    r = e.execute_cypher(
        "MATCH (a {node_id: $a}), (b {node_id: $b}) CREATE (a)-[:R215 {weight: 1.0}]->(b)",
        {"a": ids["w1"], "b": ids["w6"]},
    )
    assert r.error is None
    yield e, ids
    for nid in ids.values():
        try:
            e.delete_node(nid, graph=None)
        except Exception:
            pass


def _both_paths(e, query, params):
    """The rows the native executor and the SQL path answer, which must be the same."""
    out = []
    for native in (True, False):
        old = e.native_cypher
        e.native_cypher = native
        try:
            r = e.execute_cypher(query, params)
        finally:
            e.native_cypher = old
        assert r.error is None, (native, r.error)
        out.append(r.rows)
    return out


def test_r_weight_reads_each_weight_with_its_type(g):
    e, ids = g
    for rows in _both_paths(
        e, "MATCH (a {node_id: $a})-[r:R215]->(b) RETURN b.node_id, r.weight ORDER BY b.node_id", {"a": ids["w1"]}
    ):
        got = [(b[-2:], w, type(w).__name__) for b, w in rows]
        assert got == [
            ("w2", 1.0, "float"),
            ("w3", 1, "int"),
            ("w4", None, "NoneType"),
            ("w5", 2.0, "float"),
            ("w6", 1.0, "float"),
        ]


def test_sum_of_the_weights(g):
    e, ids = g
    for rows in _both_paths(e, "MATCH (a {node_id: $a})-[r:R215]->() RETURN sum(r.weight)", {"a": ids["w1"]}):
        # The value. Its type is IRIS's: a SUM whose total is whole answers 5, not openCypher's 5.0, for weights
        # written by Cypher as much as by create_edge (reported, not fixed in R215).
        assert rows == [[5.0]]


@pytest.mark.parametrize("cond, ends", [("r.weight = 1.0", ["w2", "w3", "w6"]), ("r.weight > 1.5", ["w5"])])
def test_where_on_the_weight(g, cond, ends):
    e, ids = g
    for rows in _both_paths(
        e, f"MATCH (a {{node_id: $a}})-[r:R215]->(b) WHERE {cond} RETURN b.node_id ORDER BY b.node_id", {"a": ids["w1"]}
    ):
        assert [b[-2:] for (b,) in rows] == ends


def test_properties_of_the_relationship(g):
    e, ids = g
    for rows in _both_paths(
        e, "MATCH (a {node_id: $a})-[r:R215]->(b) RETURN b.node_id, properties(r) ORDER BY b.node_id", {"a": ids["w1"]}
    ):
        got = {b[-2:]: (json.loads(p) if isinstance(p, str) else p) for b, p in rows}
        # Values compared as numbers; properties(r) spells a whole-number weight as 1 or 1.0 (see the module docstring
        # of KNOWN_ISSUES, "A property cannot tell a number from a string"). w4 has no properties: openCypher's
        # properties() of it is the empty map, {} (4.5.x read None; R263 made it {}, CHANGELOG "A sum with a float").
        assert got == {"w2": {"weight": 1.0}, "w3": {"weight": 1}, "w4": {}, "w5": {"weight": 2.0}, "w6": {"weight": 1.0}}


@pytest.mark.parametrize("end, cost", [("w5", 2.0), ("w2", 1.0)])
def test_the_weighted_path_costs_the_stored_weight(g, end, cost):
    e, ids = g
    r = e.execute_cypher(
        "CALL ivg.shortestPath.weighted($a, $b) YIELD path, totalCost RETURN totalCost", {"a": ids["w1"], "b": ids[end]}
    )
    assert r.error is None
    assert r.rows == [[cost]]
