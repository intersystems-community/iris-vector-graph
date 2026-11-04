"""4.6.0 R228, live: reads of a path's elements (fixes23). Each was silent or failed to prepare, on 2026.1 and 2026.3:

* ``[r IN relationships(p) | r.km]`` on a fixed-length path answered ``[]``: the elements held only the type;
* ``UNWIND nodes(p) AS n RETURN n.name`` (and ``n.node_id``) answered null every row;
* ``head(nodes(p)).name`` / ``last(nodes(p)).name`` failed SQLCODE -25, ``[n IN nodes(p) | n {.name}]`` -23,
  ``[n IN nodes(p) | id(n)]`` -29;
* ``[n IN nodes(p) | n.missing]`` answered ``[]``, not ``[null, null, null]``;
* two comprehensions over one collected list in a RETURN failed ``-400 <SUBSCRIPT>`` on 2026.3.

The graph, by hand: r228:a -ROAD {km: 1}-> r228:b -ROAD {km: 2}-> r228:c, each node labelled R228 with name A, B, C
(and the ``id`` property create_node stores). ``MATCH p = (x {node_id: 'r228:a'})-[:ROAD]->()-[:ROAD]->(z)`` has one
path, a, b, c, with relationships km 1 then 2.

The native planner declines a path variable, so these run on the translator either way; each case runs with
``native_cypher`` on and off so a later native family cannot answer them differently.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

IDS = ["r228:a", "r228:b", "r228:c"]
P = "MATCH p = (x {node_id:'r228:a'})-[:ROAD]->()-[:ROAD]->(z) "


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for nid in IDS:
        e.delete_node(nid, graph=None)
    for nid in IDS:
        assert e.create_node(nid, labels=["R228"], properties={"name": nid[-1].upper()}) is True
    assert e.create_edge("r228:a", "ROAD", "r228:b", qualifiers={"km": 1}) is True
    assert e.create_edge("r228:b", "ROAD", "r228:c", qualifiers={"km": 2}) is True
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


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _rows(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    return [[_value(c) for c in row] for row in r.rows]


def test_relationship_properties_of_a_fixed_length_path(eng):
    # km 1 then 2, numbers as r.km reads them (R253: before, the list held their text, ['1', '2'])
    assert _rows(eng, P + "RETURN [r IN relationships(p) | r.km] AS v") == [[[1, 2]]]


def test_relationship_types_of_a_fixed_length_path(eng):
    assert _rows(eng, P + "RETURN [r IN relationships(p) | type(r)] AS v") == [[["ROAD", "ROAD"]]]


def test_a_filter_on_a_fixed_length_paths_relationships(eng):
    assert _rows(eng, P + "RETURN [r IN relationships(p) WHERE r.km > 1 | r.km] AS v") == [[[2]]]


def test_a_list_predicate_over_a_fixed_length_paths_relationships(eng):
    assert _rows(eng, P + "RETURN all(r IN relationships(p) WHERE r.km > 0) AS a, "
                 "any(r IN relationships(p) WHERE r.km > 1) AS b, none(r IN relationships(p) WHERE r.km > 2) AS c") \
        == [[True, True, True]]


@pytest.mark.parametrize("key, expected", [("name", ["A", "B", "C"]), ("node_id", IDS)])
def test_unwound_path_nodes(eng, key, expected):
    assert _rows(eng, P + f"UNWIND nodes(p) AS n RETURN n.{key} AS v ORDER BY v") == [[v] for v in expected]


def test_head_and_last_of_path_nodes(eng):
    assert _rows(eng, P + "RETURN head(nodes(p)).name AS h, last(nodes(p)).name AS l") == [["A", "C"]]


def _maps(cell):
    # a map inside a list is a map (R253: before, its JSON text, as collect(n {.name}) listed it)
    assert all(isinstance(m, dict) for m in cell), cell
    return cell


def test_map_projection_of_path_nodes(eng):
    ((v,),) = _rows(eng, P + "RETURN [n IN nodes(p) | n {.name}] AS v")
    assert _maps(v) == [{"name": "A"}, {"name": "B"}, {"name": "C"}]


def test_map_projection_of_collected_nodes(eng):
    ((v,),) = _rows(eng, "MATCH (n:R228) WITH collect(n) AS ns RETURN [x IN ns | x {.name}] AS v")
    assert sorted(m["name"] for m in _maps(v)) == ["A", "B", "C"]


def test_id_of_path_nodes(eng):
    assert _rows(eng, P + "RETURN [n IN nodes(p) | id(n)] AS v") == [[IDS]]


def test_a_comprehension_keeps_null_elements(eng):
    assert _rows(eng, P + "RETURN [n IN nodes(p) | n.missing] AS v") == [[[None, None, None]]]


def test_two_comprehensions_over_one_collected_list(eng):
    ((ids, names),) = _rows(
        eng, "MATCH (n:R228) WITH collect(n) AS ns RETURN [x IN ns | x.node_id] AS ids, [x IN ns | x.name] AS nm")
    assert sorted(ids) == IDS and sorted(names) == ["A", "B", "C"]
