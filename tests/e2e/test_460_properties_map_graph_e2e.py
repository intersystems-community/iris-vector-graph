"""4.6.0 R157: `properties(n)` answers the properties of n's own graph, not every graph's node of that id.

`CY_PROPS_MAP` (the function `properties(n)` translates to) read `rdf_props WHERE s = ?` with no graph filter, so two
graphs holding a node of one id each got one merged map, the later row of a shared key winning.

The graph, worked by hand. Graphs G1 and G2 each hold a node x labelled Ivg157, created with `create_node`, which also
writes the node's id as its `id` property:
- G1's x: name "one", only1 1, so properties {id: x, name: "one", only1: 1}
- G2's x: name "two", only2 2, so properties {id: x, name: "two", only2: 2}
- the default graph's x: name "zero", so properties {id: x, name: "zero"}

`USE GRAPH G1 ... RETURN properties(n)` answers G1's map alone; an all-graphs read answers three rows, each with its own
graph's map (the row's `n.name` says which graph it is). `USE GRAPH ''` (the default graph, whose id reaches the
function as `$Char(0)`) answers the default graph's map.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PROPS = {"g1": {"name": "one", "only1": 1}, "g2": {"name": "two", "only2": 2}}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graphs = {k: f"ivg460pm_{run}_{k}" for k in PROPS}
    nid = f"ivg460pm_{run}_x"
    for k, graph in graphs.items():
        assert engine.create_node(nid, labels=["Ivg157"], properties=PROPS[k], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    assert engine.create_node(nid, labels=["Ivg157"], properties={"name": "zero"})
    yield engine, graphs, nid
    for graph in graphs.values():
        engine.erase_graph(graph)
    engine.delete_node(nid, graph="")


def _map(value):
    return json.loads(value) if isinstance(value, str) else value


def test_under_use_graph_the_map_is_that_graphs_alone(g):
    engine, graphs, nid = g
    for k, graph in graphs.items():
        r = engine.execute_cypher(
            f"USE GRAPH '{graph}' MATCH (n:Ivg157 {{node_id: $x}}) RETURN properties(n) AS p", {"x": nid}
        )
        assert r.error is None, r.error
        assert [_map(row[0]) for row in r.rows] == [{"id": nid, **PROPS[k]}]


def test_an_all_graphs_read_gives_each_row_its_own_graphs_map(g):
    engine, _, nid = g
    r = engine.execute_cypher("MATCH (n:Ivg157 {node_id: $x}) RETURN n.name AS name, properties(n) AS p", {"x": nid})
    assert r.error is None, r.error
    got = sorted((row[0], _map(row[1])) for row in r.rows)
    assert got == [
        ("one", {"id": nid, **PROPS["g1"]}),
        ("two", {"id": nid, **PROPS["g2"]}),
        ("zero", {"id": nid, "name": "zero"}),
    ]


def test_use_graph_of_the_default_graph(g):
    engine, _, nid = g
    r = engine.execute_cypher("USE GRAPH '' MATCH (n:Ivg157 {node_id: $x}) RETURN properties(n) AS p", {"x": nid})
    assert r.error is None, r.error
    assert [_map(row[0]) for row in r.rows] == [{"id": nid, "name": "zero"}]
