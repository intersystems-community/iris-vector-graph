"""4.6.0 R198, live: `[n IN nodes(p) | n.node_id]` answered `'[]'`, with no error, while `n.name` and `n.id` in the same
comprehension were right (walkthrough 1, Community 2026.1; the enterprise 2026.3 build answers the same).

`node_id` is the one property a node keeps outside `rdf_props`: it is the node's identity, the `nodes` row's key.
An element of `nodes(p)` is that id, and the comprehension read `n.node_id` as a property row
(`SELECT val FROM rdf_props WHERE s = <id> AND "key" = 'node_id'`), which no node has. Every element read NULL and
`JSON_ARRAYAGG` drops NULLs: `[]`. `nodes(p)[i].node_id` read the same row and answered null. Over a list of collected
nodes (`WITH collect(n) AS ns RETURN [x IN ns | x.node_id]`) the element is the node's JSON and the same row was read.
Over `collect(n)` itself (`RETURN [x IN collect(n) | ...]`) the element is the node's id, which was read as JSON:
every property, `name` too, answered `[]`.

`graph_id` is not a built-in on this side: `x.graph_id` of a MATCH-bound node reads a property of that name (null
here), and so does the comprehension. A relationship has no built-in property (R121: `r.s`, `r.p`, `r.o_id` are the
user's), so `relationships(p)` has none to check.

The graph, worked by hand (every edge ROAD, each node made by create_node, so each has an `id` property equal to its
node id, and a `name`):

    a -> b -> c -> f            the shortest a..f, 3 relationships
    a -> d -> e -> g -> f       a longer a..f

* shortestPath a..f: nodes a, b, c, f.
* `MATCH p = (x {node_id: a})-[:ROAD]->(y)`: two paths, [a, b] and [a, d].
* every R198 node collected: a, b, c, d, e, f, g (any order).

The spec-253 native planner declines a path variable, so with `native_cypher` on or off the translator answers these;
each case runs both ways so a later native family cannot answer them differently. The shortest path itself is found
by `Graph.KG.Traversal` either way.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

LABEL = "R198Path"
NAMES = list("abcdefg")
EDGES = [("a", "b"), ("b", "c"), ("c", "f"), ("a", "d"), ("d", "e"), ("e", "g"), ("g", "f")]


def _id(x: str) -> str:
    return f"r198:{x}"


SHORTEST = [_id(x) for x in "abcf"]


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for x in NAMES:
        e.delete_node(_id(x), graph=None)
    for x in NAMES:
        e.create_node(_id(x), labels=[LABEL], properties={"name": x.upper()})
    for s, t in EDGES:
        e.create_edge(_id(s), "ROAD", _id(t))
    try:
        yield e
    finally:
        for x in NAMES:
            e.delete_node(_id(x), graph=None)


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


SP = f"MATCH p = shortestPath((x {{node_id:'{_id('a')}'}})-[*]->(y {{node_id:'{_id('f')}'}})) "
PLAIN = f"MATCH p = (x {{node_id:'{_id('a')}'}})-[:ROAD]->(y) "


def test_shortest_path_node_ids(eng):
    assert _rows(eng, SP + "RETURN [n IN nodes(p) | n.node_id] AS ids") == [[SHORTEST]]


def test_shortest_path_node_ids_beside_name_and_id(eng):
    assert _rows(
        eng, SP + "RETURN [n IN nodes(p) | n.node_id] AS ids, [n IN nodes(p) | n.name] AS names, "
        "[n IN nodes(p) | n.id] AS idprops"
    ) == [[SHORTEST, list("ABCF"), SHORTEST]]


def test_shortest_path_node_ids_filtered(eng):
    assert _rows(eng, SP + f"RETURN [n IN nodes(p) WHERE n.node_id <> '{_id('b')}' | n.node_id] AS ids") == [
        [[_id(x) for x in "acf"]]
    ]


def test_shortest_path_element_node_id(eng):
    assert _rows(eng, SP + "RETURN nodes(p)[1].node_id AS second, nodes(p)[1].name AS name") == [[_id("b"), "B"]]


def test_plain_path_node_ids(eng):
    rows = _rows(eng, PLAIN + "RETURN [n IN nodes(p) | n.node_id] AS ids, [n IN nodes(p) | n.name] AS names")
    assert sorted(rows) == [[[_id("a"), _id("b")], ["A", "B"]], [[_id("a"), _id("d")], ["A", "D"]]]


def test_plain_path_element_node_id(eng):
    rows = _rows(eng, PLAIN + "RETURN nodes(p)[0].node_id AS first, nodes(p)[1].node_id AS second")
    assert sorted(rows) == [[_id("a"), _id("b")], [_id("a"), _id("d")]]


@pytest.mark.parametrize("key, expected", [("node_id", [_id(x) for x in NAMES]), ("name", [x.upper() for x in NAMES])])
def test_collected_nodes(eng, key, expected):
    # one comprehension per statement: two over the same collected column fail on 2026.3 with <SUBSCRIPT> in
    # ^||%t (the column's JSON as a subscript), whatever they read
    ((ids,),) = _rows(eng, f"MATCH (n:{LABEL}) WITH collect(n) AS ns RETURN [x IN ns | x.{key}] AS v")
    assert sorted(ids) == expected


def test_comprehension_over_collect_reads_the_nodes(eng):
    (row,) = _rows(
        eng, f"MATCH (n:{LABEL}) RETURN [x IN collect(n) | x.node_id] AS ids, [x IN collect(n) | x.name] AS names"
    )
    assert sorted(row[0]) == [_id(x) for x in NAMES]
    assert sorted(row[1]) == [x.upper() for x in NAMES]


def test_graph_id_is_a_property_on_a_bound_node_and_on_an_element(eng):
    # not a built-in: x.graph_id of a bound node is a property read (none here), and so is the element's. (The
    # comprehension's own answer is not pinned: JSON_ARRAYAGG drops a null element, so it answers [] where openCypher
    # answers [null, null, null, null].)
    assert _rows(eng, SP + "RETURN x.graph_id AS g, nodes(p)[0].graph_id AS e") == [[None, None]]
