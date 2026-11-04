"""4.6.0 R256, live: comprehensions whose projection is not a property read (fixes29).

Before:
* a pattern comprehension with a map projection, ``[(a)-->(c) | c {.name}]``, failed SQLCODE -23 (the projection's
  property row was joined outside the comprehension);
* ``[x IN ns | id(x)]`` over a collected node list failed -29 on both builds (it read a node_id column the
  comprehension's table does not have);
* on 2026.3, two comprehensions with non-property projections over one collected list longer than 511 characters
  failed ``-400 <SUBSCRIPT>`` (or ``<UNDEFINED>``) in ``^||%t``: R228's REPLACE wrapper covered property projections
  only.

The graph, by hand: r256:a -LINK-> r256:b, r256:a -LINK-> r256:c (label R256, names A, B, C); and twelve nodes
r256l1 .. r256l12 (label R256L, names node-name-1 .. node-name-12), whose collected list is far over 511 characters
(each node's JSON is about 150), as is the list of their names (about 160).
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

ABC = ["r256:a", "r256:b", "r256:c"]
MANY = [f"r256l{i}" for i in range(1, 13)]
NAMES = [f"node-name-{i}" for i in range(1, 13)]
NS = "MATCH (n:R256L) WITH collect(n) AS ns "


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for nid in ABC + MANY:
        e.delete_node(nid, graph=None)
    for nid in ABC:
        assert e.create_node(nid, labels=["R256"], properties={"name": nid[-1].upper()}) is True
    for nid, name in zip(MANY, NAMES):
        assert e.create_node(nid, labels=["R256L"], properties={"name": name}) is True
    assert e.create_edge("r256:a", "LINK", "r256:b") is True
    assert e.create_edge("r256:a", "LINK", "r256:c") is True
    try:
        yield e
    finally:
        for nid in ABC + MANY:
            e.delete_node(nid, graph=None)


@pytest.fixture(params=[True, False], ids=["native-on", "native-off"])
def eng(engine, request):
    before = engine.native_cypher
    engine.native_cypher = request.param
    try:
        yield engine
    finally:
        engine.native_cypher = before


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _one_row(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    (row,) = r.rows
    return [_value(c) for c in row]


def _names(maps):
    return sorted(m["name"] for m in maps)


def test_a_pattern_comprehension_with_a_map_projection(eng):
    (v,) = _one_row(eng, "MATCH (a:R256 {name: 'A'}) RETURN [(a)-->(c) | c {.name}] AS v")
    assert sorted(v, key=lambda m: m["name"]) == [{"name": "B"}, {"name": "C"}], v


def test_id_of_collected_nodes(eng):
    (v,) = _one_row(eng, NS + "RETURN [x IN ns | id(x)] AS v")
    assert sorted(v) == sorted(MANY)


def test_two_id_comprehensions_over_one_collected_list(eng):
    a, b = _one_row(eng, NS + "RETURN [x IN ns | id(x)] AS a, [x IN ns | id(x)] AS b")
    assert sorted(a) == sorted(b) == sorted(MANY)


def test_an_id_and_a_map_comprehension_over_one_collected_list(eng):
    a, b = _one_row(eng, NS + "RETURN [x IN ns | id(x)] AS a, [x IN ns | x {.name}] AS b")
    assert sorted(a) == sorted(MANY) and _names(b) == sorted(NAMES)


def test_two_map_comprehensions_over_one_collected_list(eng):
    a, b = _one_row(eng, NS + "RETURN [x IN ns | x {.name}] AS a, [x IN ns | x {.name}] AS b")
    assert _names(a) == _names(b) == sorted(NAMES)


def test_two_computed_comprehensions_over_one_collected_name_list(eng):
    a, b = _one_row(
        eng, "MATCH (n:R256L) WITH collect(n.name) AS ns RETURN [x IN ns | x + '!'] AS a, [x IN ns | size(x)] AS b"
    )
    assert sorted(a) == sorted(n + "!" for n in NAMES)
    assert sorted(b) == sorted(len(n) for n in NAMES)


def test_a_filtered_and_a_mapped_comprehension_over_one_collected_list(eng):
    a, b = _one_row(
        eng,
        NS + "RETURN [x IN ns WHERE x.name ENDS WITH '1' | id(x)] AS a, [x IN ns | x {.name}] AS b",
    )
    assert sorted(a) == ["r256l1", "r256l11"] and _names(b) == sorted(NAMES)
