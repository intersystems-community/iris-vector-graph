"""4.6.0 R163, live: under `USE GRAPH` the centrality CALLs answer that graph, or refuse.

`CALL ivg.degreeCentrality` and `CALL ivg.ppr` answered the default graph whatever `USE GRAPH` said; the procedures
over `^NKG` (betweenness, closeness, eigenvector, leiden, triangleCount, scc, kcore) answered every graph merged.
The first two now pass the graph to their SQL function; the others raise, naming `^NKG`
(tests/unit/test_460_r163_centrality_call_graph.py pins the SQL).

Two graphs over the same node ids, by hand, in a namespace emptied first (`Graph.KG.Eraser.EraseAll`):
- the default graph, a star: c -S-> l1, c -S-> l2, c -S-> l3. Four nodes, so degree score = degree / 3.
- the graph r163g, an in-star: l1 -S-> c, l2 -S-> c. Three nodes, so degree score = degree / 2.

    graph     direction   c  l1  l2  l3
    default   out         3   0   0   0
    r163g     out         0   1   1   -
    r163g     in          2   0   0   -

PPR from c reaches what c's out-edges reach: every node of the default graph's star, and in r163g (where c has no
out-edge) only c. From l1: only l1 in the default graph, l1 and c in r163g.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

GRAPH = "r163g"
DEFAULT_EDGES = (("c", "S", "l1"), ("c", "S", "l2"), ("c", "S", "l3"))
NAMED_EDGES = (("l1", "S", "c"), ("l2", "S", "c"))
DEGREES = {
    (None, "out"): {"c": 3, "l1": 0, "l2": 0, "l3": 0},
    (GRAPH, "out"): {"c": 0, "l1": 1, "l2": 1},
    (GRAPH, "in"): {"c": 2, "l1": 0, "l2": 0},
}
REACHED = {
    (None, "c"): {"c", "l1", "l2", "l3"},
    (GRAPH, "c"): {"c"},
    (None, "l1"): {"l1"},
    (GRAPH, "l1"): {"l1", "c"},
}


def _id(n: str) -> str:
    return f"r163:{n}"


def _use(graph) -> str:
    return "" if graph is None else f"USE GRAPH '{graph}' "


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
            engine.create_node(_id(n), labels=["R163"], graph=graph)
        for s, p, o in edges:
            engine.create_edge(_id(s), p, _id(o), graph=graph)
    conn.commit()
    engine.sync()
    yield engine
    for graph, edges in ((None, DEFAULT_EDGES), (GRAPH, NAMED_EDGES)):
        for n in sorted({x for s, _, o in edges for x in (s, o)}):
            engine.delete_node(_id(n), graph=graph)
    conn.commit()
    conn.close()


@pytest.mark.parametrize("graph,direction", sorted(DEGREES, key=str))
def test_degree_centrality_counts_the_use_graph_graph(graphs, graph, direction):
    rows = graphs.execute_cypher(
        f"{_use(graph)}CALL ivg.degreeCentrality({{direction: '{direction}'}}) YIELD node, score, degree "
        "RETURN node, score, degree",
        raise_on_error=True,
    ).rows
    degrees = DEGREES[(graph, direction)]
    expected = {_id(n): (d, round(d / (len(degrees) - 1), 6)) for n, d in degrees.items()}
    # `node` comes back as a node (id, labels, properties), then the yielded columns
    assert {row[0]: (int(row[-1]), round(float(row[-2]), 6)) for row in rows} == expected


@pytest.mark.parametrize("graph,seed", sorted(REACHED, key=str))
def test_ppr_walks_the_use_graph_graph(graphs, graph, seed):
    rows = graphs.execute_cypher(
        f"{_use(graph)}CALL ivg.ppr(['{_id(seed)}'], 0.85, 20) YIELD node, score RETURN node, score",
        raise_on_error=True,
    ).rows
    scored = {row[0] for row in rows if float(row[-1]) > 0}
    assert scored == {_id(n) for n in REACHED[(graph, seed)]}


@pytest.mark.parametrize(
    "call",
    [
        "ivg.betweenness({}) YIELD node, score RETURN node, score",
        "ivg.closeness({}) YIELD node, score RETURN node, score",
        "ivg.eigenvector({}) YIELD node, score RETURN node, score",
        "ivg.leiden({}) YIELD node, community RETURN node, community",
        "ivg.triangleCount({}) YIELD node, triangles RETURN node, triangles",
        "ivg.scc({}) YIELD node, component RETURN node, component",
        "ivg.kcore({}) YIELD node, coreness RETURN node, coreness",
    ],
)
def test_a_procedure_over_nkg_refuses_use_graph(graphs, call):
    with pytest.raises(Exception) as raised:
        graphs.execute_cypher(f"USE GRAPH '{GRAPH}' CALL {call}", raise_on_error=True)
    assert "^NKG" in str(raised.value) and call.split("(")[0] in str(raised.value), raised.value
