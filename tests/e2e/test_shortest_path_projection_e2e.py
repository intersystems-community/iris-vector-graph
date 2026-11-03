"""4.6.0 (NR8): a shortestPath RETURN keeps its aliases and every column it asks for.

The newcomer walkthrough's graph, built by hand: A→B→C→D→E, A→F→E, E→G→H→I, I→E, and a separate cycle J→K→L→J. The
one shortest path from A to I is A, F, E, G, H, I: five LINK edges. Before 4.6.0 `RETURN length(p) AS len,
[n IN nodes(p) | n.name] AS names` answered columns ['length'] and rows [[5]]: the alias was ignored and the second
column dropped, with no error.

IVG returns a list as JSON text, on this route as on every other SQL-answered read; `_value` reads it back.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

NAMES = list("ABCDEFGHIJKL")
EDGES = [
    ("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"), ("A", "F"), ("F", "E"), ("E", "G"),
    ("G", "H"), ("H", "I"), ("I", "E"), ("J", "K"), ("K", "L"), ("L", "J"),
]
PATH_NAMES = ["A", "F", "E", "G", "H", "I"]
PATH_IDS = [f"sp460:{x}" for x in PATH_NAMES]
SP = "MATCH p = shortestPath((a {name:'A'})-[*]->(b {name:'I'})) "
SP_BY_ID = "MATCH p = shortestPath((a {node_id:'sp460:A'})-[*]->(b {node_id:'sp460:I'})) "


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.bulk_create_nodes([{"id": f"sp460:{x}", "labels": ["Sp460"], "properties": {"name": x}} for x in NAMES])
    e.bulk_create_edges([{"source_id": f"sp460:{s}", "predicate": "LINK", "target_id": f"sp460:{t}"} for s, t in EDGES])
    try:
        # the endpoints are found by name: another node named A or I would make this a different graph
        named = e.execute_cypher("MATCH (n) WHERE n.name IN ['A', 'I'] RETURN count(n)")
        assert named.rows == [[2]], f"other nodes named A or I in this namespace: {named.rows}"
        yield e
    finally:
        for x in NAMES:
            e.delete_node(f"sp460:{x}")


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _answer(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    return r.columns, [[_value(c) for c in row] for row in r.rows]


def test_aliases_and_a_list_comprehension(engine):
    assert _answer(engine, SP + "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names") == (
        ["len", "names"],
        [[5, PATH_NAMES]],
    )


def test_aliases_and_a_list_comprehension_with_id_bound_ends(engine):
    assert _answer(engine, SP_BY_ID + "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names") == (
        ["len", "names"],
        [[5, PATH_NAMES]],
    )


def test_a_list_comprehension_alone(engine):
    assert _answer(engine, SP + "RETURN [n IN nodes(p) | n.name] AS names") == (["names"], [[PATH_NAMES]])


def test_the_path(engine):
    assert _answer(engine, SP + "RETURN p") == (["p"], [[{"nodes": PATH_IDS, "rels": ["LINK"] * 5}]])


def test_length_alone(engine):
    assert _answer(engine, SP + "RETURN length(p)") == (["length(p)"], [[5]])


def test_relationships_alone(engine):
    assert _answer(engine, SP + "RETURN relationships(p)") == (["relationships(p)"], [[["LINK"] * 5]])


def test_nodes_beside_a_literal(engine):
    assert _answer(engine, SP + "RETURN nodes(p) AS ns, 1 AS one") == (["ns", "one"], [[PATH_IDS, 1]])


def test_projected_relationships_carry_their_stored_properties(engine):
    engine.create_node("sp460:X", labels=["Sp460"], properties={"name": "X"})
    engine.create_node("sp460:Y", labels=["Sp460"], properties={"name": "Y"})
    try:
        engine.create_edge("sp460:L", "VIA", "sp460:X", qualifiers={"since": 2020})
        engine.create_edge("sp460:X", "VIA", "sp460:Y")
        cols, rows = _answer(
            engine,
            "MATCH p = shortestPath((a {node_id:'sp460:L'})-[:VIA*]->(b {node_id:'sp460:Y'})) "
            "RETURN relationships(p) AS rs, b.name AS b",
        )
        assert (cols, rows) == (
            ["rs", "b"],
            [[[{"type": "VIA", "props": {"since": 2020}}, {"type": "VIA", "props": {}}], "Y"]],
        )
    finally:
        engine.delete_node("sp460:X")
        engine.delete_node("sp460:Y")
