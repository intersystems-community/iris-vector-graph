"""4.6.0 R253, live: a value keeps its type inside a list (fixes29).

Before: ``[x IN [r.km] | x]`` answered ``['1']`` where ``r.km`` answers 1, and ``collect(n {.name})`` listed JSON
strings (``['{"name":"A"}', ...]``), not maps.

The graph, by hand: r253:a -ROAD {km: 1}-> r253:b -ROAD {km: 2}-> r253:c, written by ``create_edge`` (a JSON number),
and r253:c -TRAIL {km: 3, w: 0.5}-> r253:a written by a Cypher CREATE (which stores a number as the JSON text "3"),
and r253:b -CODE {code: '007'}-> r253:c by ``create_edge`` (a string that is not a number's spelling). The nodes are
labelled R253 with names A, B, C and a Cypher-written ``rank`` 1, 2, 3. r253:q (label R253Q) has the name
``say "hi" \\ x``, which JSON must escape.

A relationship property reads as a number when its JSON holds a number or a number's canonical spelling, and as a
string otherwise (KNOWN_ISSUES, "A property cannot tell a number from a string that spells it"); inside a list it
reads the same. A list comes back as its JSON text, which the test decodes once.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

IDS = ["r253:a", "r253:b", "r253:c", "r253:q"]
QUOTED = 'say "hi" \\ x'


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for nid in IDS:
        e.delete_node(nid, graph=None)
    for nid in IDS[:3]:
        assert e.create_node(nid, labels=["R253"], properties={"name": nid[-1].upper()}) is True
    assert e.create_node("r253:q", labels=["R253Q"], properties={"name": QUOTED}) is True
    assert e.create_edge("r253:a", "ROAD", "r253:b", qualifiers={"km": 1}) is True
    assert e.create_edge("r253:b", "ROAD", "r253:c", qualifiers={"km": 2}) is True
    assert e.create_edge("r253:b", "CODE", "r253:c", qualifiers={"code": "007"}) is True
    for rank, nid in enumerate(IDS[:3], start=1):
        r = e.execute_cypher(f"MATCH (n {{node_id: '{nid}'}}) SET n.rank = {rank}")
        assert not r.error, r.error
    r = e.execute_cypher(
        "MATCH (c {node_id: 'r253:c'}), (a {node_id: 'r253:a'}) CREATE (c)-[:TRAIL {km: 3, w: 0.5}]->(a)"
    )
    assert not r.error, r.error
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


def test_a_relationship_property_in_a_list_is_a_number(eng):
    rows = _rows(eng, "MATCH (:R253 {name: 'A'})-[r:ROAD]->() RETURN r.km AS k, [r.km] AS l, [x IN [r.km] | x] AS v")
    assert rows == [[1, [1], [1]]]


def test_collected_relationship_properties_are_numbers(eng):
    ((v,),) = _rows(eng, "MATCH (:R253)-[r:ROAD]->() RETURN collect(r.km) AS v")
    assert sorted(v) == [1, 2] and all(type(x) is int for x in v), v


def test_a_cypher_written_relationship_number_in_a_list(eng):
    rows = _rows(eng, "MATCH (:R253 {name: 'C'})-[r:TRAIL]->() RETURN r.km AS k, [x IN [r.km, r.w] | x] AS v, "
                      "collect(r.w) AS w")
    assert rows == [[3, [3, 0.5], [0.5]]]


def test_a_relationship_string_that_spells_no_number_stays_a_string(eng):
    rows = _rows(eng, "MATCH (:R253)-[r:CODE]->() RETURN r.code AS k, [r.code] AS l, collect(r.code) AS c")
    assert rows == [["007", ["007"], ["007"]]]


def test_collected_map_projections_are_maps(eng):
    ((v,),) = _rows(eng, "MATCH (n:R253) RETURN collect(n {.name}) AS v")
    assert sorted(v, key=lambda m: m["name"]) == [{"name": "A"}, {"name": "B"}, {"name": "C"}], v


def test_a_map_projection_in_a_list_keeps_its_value_types(eng):
    assert _rows(eng, "MATCH (n:R253 {name: 'A'}) RETURN [n {.name, .rank}] AS v") == [[[{"name": "A", "rank": 1}]]]


def test_a_collected_map_escapes_its_strings(eng):
    assert _rows(eng, "MATCH (n:R253Q) RETURN collect(n {.name}) AS v") == [[[{"name": QUOTED}]]]


def test_collected_node_properties_keep_their_types(eng):
    ((ranks, names),) = _rows(eng, "MATCH (n:R253) RETURN collect(n.rank) AS r, collect(n.name) AS m")
    assert sorted(ranks) == [1, 2, 3] and all(type(x) is int for x in ranks), ranks
    assert sorted(names) == ["A", "B", "C"]


def test_computed_and_range_lists(eng):
    rows = _rows(eng, "RETURN [x IN [1, 2, 3] | x * 2] AS a, range(1, 3) AS b, [x IN range(1, 3) | x] AS c")
    assert rows == [[[2, 4, 6], [1, 2, 3], [1, 2, 3]]]


def test_a_comprehension_over_a_collected_list(eng):
    assert _rows(eng, "UNWIND [1, 2, 3] AS i WITH collect(i) AS l RETURN [x IN l | x] AS v") == [[[1, 2, 3]]]


def test_relationship_properties_of_a_fixed_length_path(eng):
    rows = _rows(eng, "MATCH p = (:R253 {name: 'A'})-[:ROAD]->()-[:ROAD]->() RETURN [r IN relationships(p) | r.km] AS v")
    assert rows == [[[1, 2]]]
