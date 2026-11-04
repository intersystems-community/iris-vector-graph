"""4.6.0 R144: `engine.degree_centrality(graph=...)` counts the degrees of that graph, not the default graph's.

`Graph.KG.Centrality.DegreeCentralityJson` set `pGraph = 0` (the default graph's `^KG` key) and took no graph, and
`degree_centrality` had no `graph` argument, so no caller could ask about a named graph. Both take one now.
`CALL ivg.degreeCentrality` takes no graph option (`CENTRALITY_ALLOWED_KEYS`) and still answers the default graph.

Two graphs of different degree, by hand, in a namespace emptied first (`Graph.KG.Eraser.EraseAll`), the same node ids
in both:
- the default graph, a star: c -S-> l1, c -S-> l2, c -S-> l3. Four nodes, so score = degree / 3.
- the graph r144g, an in-star: l1 -S-> c, l2 -S-> c. Three nodes, so score = degree / 2.

    graph     direction   c  l1  l2  l3
    default   out         3   0   0   0
    default   in          0   1   1   1
    default   both        3   1   1   1
    r144g     out         0   1   1   -
    r144g     in          2   0   0   -
    r144g     both        2   1   1   -
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

GRAPH = "r144g"
DEFAULT_EDGES = (("c", "S", "l1"), ("c", "S", "l2"), ("c", "S", "l3"))
NAMED_EDGES = (("l1", "S", "c"), ("l2", "S", "c"))
TABLE = {
    (None, "out"): {"c": 3, "l1": 0, "l2": 0, "l3": 0},
    (None, "in"): {"c": 0, "l1": 1, "l2": 1, "l3": 1},
    (None, "both"): {"c": 3, "l1": 1, "l2": 1, "l3": 1},
    (GRAPH, "out"): {"c": 0, "l1": 1, "l2": 1},
    (GRAPH, "in"): {"c": 2, "l1": 0, "l2": 0},
    (GRAPH, "both"): {"c": 2, "l1": 1, "l2": 1},
}


def _id(n: str) -> str:
    return f"r144:{n}"


@pytest.fixture(scope="module")
def graphs():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for graph, edges in ((None, DEFAULT_EDGES), (GRAPH, NAMED_EDGES)):
        for n in sorted({x for s, _, o in edges for x in (s, o)}):
            engine.create_node(_id(n), labels=["R144"], graph=graph)
        for s, p, o in edges:
            engine.create_edge(_id(s), p, _id(o), graph=graph)
    conn.commit()
    yield engine
    for graph, edges in ((None, DEFAULT_EDGES), (GRAPH, NAMED_EDGES)):
        for n in sorted({x for s, _, o in edges for x in (s, o)}):
            engine.delete_node(_id(n), graph=graph)
    conn.commit()
    conn.close()


def _expected(graph, direction) -> dict:
    degrees = TABLE[(graph, direction)]
    return {_id(n): (d, round(d / (len(degrees) - 1), 6)) for n, d in degrees.items()}


@pytest.mark.parametrize("graph,direction", sorted(TABLE, key=str))
def test_the_method_answers_the_degrees_of_the_graph_it_names(graphs, graph, direction):
    rows = graphs.degree_centrality(direction=direction, graph=graph)
    assert {r["id"]: (r["degree"], round(r["score"], 6)) for r in rows} == _expected(graph, direction)


def test_the_predicate_filter_reads_the_named_graph_too(graphs):
    rows = graphs.degree_centrality(direction="in", predicate="S", graph=GRAPH)
    assert {r["id"]: r["degree"] for r in rows} == {_id("c"): 2, _id("l1"): 0, _id("l2"): 0}


def test_the_call_takes_no_graph_and_answers_the_default_graph(graphs):
    rows = graphs.execute_cypher(
        "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, score, degree RETURN node, score, degree",
        raise_on_error=True,
    ).rows
    assert {row[0]: (int(row[-1]), round(float(row[-2]), 6)) for row in rows} == _expected(None, "out")
