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

`USE GRAPH ''` is the default graph. With no `USE GRAPH` a CALL answers the union of each graph's own rows, one row per
graph and node (4.6.0 R192, CHANGELOG "Without `USE GRAPH`, a `CALL` and a `WITH` stage answer the union of each
graph's own rows"), so the answers are keyed by (graph, node): each node holds its graph's name in the property `g`,
and the query returns `node.g` beside `node.id`.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

GRAPH = "r163g"
DEFAULT_EDGES = (("c", "S", "l1"), ("c", "S", "l2"), ("c", "S", "l3"))
NAMED_EDGES = (("l1", "S", "c"), ("l2", "S", "c"))
#: each graph's own answer, by hand; "" is the default graph
GRAPH_DEGREES = {
    ("", "out"): {"c": 3, "l1": 0, "l2": 0, "l3": 0},
    ("", "in"): {"c": 0, "l1": 1, "l2": 1, "l3": 1},
    (GRAPH, "out"): {"c": 0, "l1": 1, "l2": 1},
    (GRAPH, "in"): {"c": 2, "l1": 0, "l2": 0},
}
GRAPH_REACHED = {
    ("", "c"): {"c", "l1", "l2", "l3"},
    (GRAPH, "c"): {"c"},
    ("", "l1"): {"l1"},
    (GRAPH, "l1"): {"l1", "c"},
}
#: a USE GRAPH (None: none) and the graphs it answers for
SCOPES = {"": [""], GRAPH: [GRAPH], None: ["", GRAPH]}
DEGREES = [(use, d) for use in SCOPES for d in ("out", "in")]
REACHED = [(use, seed) for use in SCOPES for seed in ("c", "l1")]


def _ids(cases) -> list:
    return [f"{'default' if use == '' else use}-{x}" for use, x in cases]


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
            engine.create_node(_id(n), labels=["R163"], properties={"g": graph or ""}, graph=graph)
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


@pytest.mark.parametrize("graph,direction", DEGREES, ids=_ids(DEGREES))
def test_degree_centrality_counts_the_use_graph_graph(graphs, graph, direction):
    rows = graphs.execute_cypher(
        f"{_use(graph)}CALL ivg.degreeCentrality({{direction: '{direction}'}}) YIELD node, score, degree "
        "RETURN node.g, node.id, score, degree",
        raise_on_error=True,
    ).rows
    got = [(g, n, int(degree), round(float(score), 6)) for g, n, score, degree in rows]
    expected = []
    for g in SCOPES[graph]:
        degrees = GRAPH_DEGREES[(g, direction)]
        expected += [(g, _id(n), d, round(d / (len(degrees) - 1), 6)) for n, d in degrees.items()]
    assert sorted(got) == sorted(expected)  # one row per graph and node


@pytest.mark.parametrize("graph,seed", REACHED, ids=_ids(REACHED))
def test_ppr_walks_the_use_graph_graph(graphs, graph, seed):
    rows = graphs.execute_cypher(
        f"{_use(graph)}CALL ivg.ppr(['{_id(seed)}'], 0.85, 20) YIELD node, score RETURN node.g, node.id, score",
        raise_on_error=True,
    ).rows
    scored = {(g, n) for g, n, score in rows if float(score) > 0}
    assert scored == {(g, _id(n)) for g in SCOPES[graph] for n in GRAPH_REACHED[(g, seed)]}


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
