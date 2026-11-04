"""4.6.0 R254, live: reads of a variable-length named path's elements (fixes29).

Before: with the start pinned by id, ``MATCH p = (x {node_id: ..})-[:ROAD*1..2]->(z) RETURN [r IN relationships(p) |
r.km]`` went to the engine's BFS path route, which builds its columns from bare path functions and end nodes only: it
answered node ids or node maps in place of the list, under other column names.

The graph, by hand: r254:a -ROAD {km: 1}-> r254:b -ROAD {km: 2}-> r254:c, names A, B, C. From a, ``-[:ROAD*1..2]->``
has two paths: a->b (length 1, km [1]) and a->b->c (length 2, km [1, 2]).

Each case runs with the start pinned by id (the BFS route) and by a property (the SQL path), native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

IDS = ["r254:a", "r254:b", "r254:c"]
STARTS = {
    "id-pinned": "MATCH p = (x {node_id: 'r254:a'})-[:ROAD*1..2]->(z) ",
    "by-name": "MATCH p = (x:R254 {name: 'A'})-[:ROAD*1..2]->(z) ",
}


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for nid in IDS:
        e.delete_node(nid, graph=None)
    for nid in IDS:
        assert e.create_node(nid, labels=["R254"], properties={"name": nid[-1].upper()}) is True
    assert e.create_edge("r254:a", "ROAD", "r254:b", qualifiers={"km": 1}) is True
    assert e.create_edge("r254:b", "ROAD", "r254:c", qualifiers={"km": 2}) is True
    try:
        yield e
    finally:
        for nid in IDS:
            e.delete_node(nid, graph=None)


@pytest.fixture(params=[True, False], ids=["native-on", "native-off"])
def eng(engine, request):
    before = engine.native_cypher
    engine.native_cypher = request.param
    try:
        yield engine
    finally:
        engine.native_cypher = before


@pytest.fixture(params=sorted(STARTS))
def start(request):
    return STARTS[request.param]


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _result(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    # rows in path-length order: each row's text is longer the longer its path
    return list(r.columns), sorted(([_value(c) for c in row] for row in r.rows), key=lambda row: (len(repr(row)), repr(row)))


def test_relationship_properties(eng, start):
    assert _result(eng, start + "RETURN [r IN relationships(p) | r.km] AS v") == (["v"], [[[1]], [[1, 2]]])


def test_relationship_types_length_and_size(eng, start):
    cols, rows = _result(
        eng,
        start + "RETURN [r IN relationships(p) | type(r)] AS t, length(p) AS l, size(relationships(p)) AS s",
    )
    assert cols == ["t", "l", "s"]
    assert rows == [[["ROAD"], 1, 1], [["ROAD", "ROAD"], 2, 2]]


def test_node_names(eng, start):
    cols, rows = _result(eng, start + "RETURN [n IN nodes(p) | n.name] AS v, size(nodes(p)) AS s")
    assert (cols, rows) == (["v", "s"], [[["A", "B"], 2], [["A", "B", "C"], 3]])


def test_bare_path_functions(eng, start):
    # the shape the BFS path route answers already: kept
    cols, rows = _result(eng, start + "RETURN length(p) AS l, nodes(p) AS ns")
    assert cols == ["l", "ns"]
    assert rows == [[1, ["r254:a", "r254:b"]], [2, ["r254:a", "r254:b", "r254:c"]]]


def test_end_property_next_to_a_comprehension(eng, start):
    cols, rows = _result(eng, start + "RETURN z.name AS z, [r IN relationships(p) | r.km] AS v")
    assert (cols, rows) == (["z", "v"], [["B", [1]], ["C", [1, 2]]])
