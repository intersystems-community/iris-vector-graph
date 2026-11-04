"""4.6.0 R192, live: without USE GRAPH every CALL answers the union of each graph's own answer, or raises.

CONTEXT.md, All-graphs read: a read that names no graph sees every graph, and its answer is the union of each graph's
own answer; a node is its own graph's node, so the same node ID in two graphs is two nodes, each with its own
properties. A procedure's rows are therefore one per (graph, node), each with that graph's own score, and a property
read after the CALL reads the row's own graph. A procedure that cannot answer per graph raises, as R184's seven
`^NKG` procedures do.

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up for `Graph_KG` by
`scripts/dev/make_scratch_namespace.py` (IVG460FXL when this was written). The module fixture empties it first
(`Graph.KG.Eraser.EraseAll`), so never point it at a namespace another run is using.

By hand. The default graph and graph r192g share the node IDs a and c:

    default   a -R-> b -R-> c            names a_def, b_def, c_def; text "apple pie" on a
    r192g     a -R-> c -R-> x            names a_g, c_g, x_g;       text "apple tart" on a
    vectors   default a = e0, b = e1;    r192g a = e1, x = e0      (e_i: the unit vector on axis i, 768 wide)

    degreeCentrality (out; n = 3 in each graph, score = degree / 2)
        default   a 1 0.5   b 1 0.5   c 0 0.0
        r192g     a 1 0.5   c 1 0.5   x 0 0.0
    ppr from a      each graph is the chain a -> . -> ., so every node of each graph is reached, and r192g's c and x
                    score what the default graph's b and c do (the chains are the same shape)
    neighbors(a, R, out)        default b_def; r192g c_g
    neighbors(c, R, both)       default b_def; r192g a_g, x_g
    shortestPath.weighted a->c  default a->b->c, cost 2; r192g a->c, cost 1 (every weight 1)
    vector.search(e0, k 1)      default a_def; r192g x_g, each at cosine 1.0
    bm25 over the default graph, "apple"                  a_def only (the index is the default graph's)
    ivf over the default graph, e0, k 5                   a_def 1.0, b_def 0.0

Before: degree, ppr and the weighted path answered the default graph alone, with a property read after them reaching
both graphs' rows of a (so a_def and a_g, both at the default graph's score); neighbors merged the two c's into one row
and read both graphs' names for it; vector.search answered the default graph alone; bm25 and ivf read both graphs'
names for a; gds.pageRank.stream and gds.betweenness.stream answered the default graph as if it were every graph.
"""

from __future__ import annotations

import os
from collections import Counter

import pytest

from tests.e2e import ns460alt as alt

#: an IVG460FX* scratch namespace set up for `Graph_KG` (scripts/dev/make_scratch_namespace.py IVG460FXL)
NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

GRAPH = "r192g"
DIM = 768
NODES = {None: ("a", "b", "c"), GRAPH: ("a", "c", "x")}
EDGES = {None: (("a", "b"), ("b", "c")), GRAPH: (("a", "c"), ("c", "x"))}
VECTORS = {None: {"a": 0, "b": 1}, GRAPH: {"a": 1, "x": 0}}
TEXT = {None: "apple pie", GRAPH: "apple tart"}


def _id(n: str) -> str:
    return f"r192:{n}"


def _name(n: str, graph) -> str:
    return f"{n}_{'def' if graph is None else 'g'}"


def _unit(axis: int) -> list:
    v = [0.0] * DIM
    v[axis] = 1.0
    return v


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=DIM)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for graph, nodes in NODES.items():
        for n in nodes:
            props = {"name": _name(n, graph)}
            if n == "a":
                props["text"] = TEXT[graph]
            eng.create_node(_id(n), labels=["R192"], properties=props, graph=graph)
        for s, o in EDGES[graph]:
            eng.create_edge(_id(s), "R", _id(o), graph=graph)
        for n, axis in VECTORS[graph].items():
            assert eng.store_embedding(_id(n), _unit(axis), graph=graph)
    conn.commit()
    eng.sync()
    eng.bm25_build("r192bm", ["text"])
    eng.ivf_build("r192ivf", nlist=1)
    yield eng
    conn.close()


def _rows(engine, query, params=None):
    return engine.execute_cypher(query, params or {}, raise_on_error=True).rows


def _bag(rows):
    return Counter(tuple(round(v, 6) if isinstance(v, float) else v for v in row) for row in rows)


def test_degree_centrality_answers_each_graph_s_own_degrees(engine):
    rows = _rows(
        engine,
        "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, score, degree "
        "RETURN node.node_id AS id, node.name AS name, degree, score",
    )
    assert _bag(rows) == Counter({
        (_id("a"), "a_def", 1, 0.5): 1, (_id("b"), "b_def", 1, 0.5): 1, (_id("c"), "c_def", 0, 0.0): 1,
        (_id("a"), "a_g", 1, 0.5): 1, (_id("c"), "c_g", 1, 0.5): 1, (_id("x"), "x_g", 0, 0.0): 1,
    })


def test_degree_centrality_orders_the_union(engine):
    rows = _rows(
        engine,
        "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, degree "
        "RETURN node.name AS name, degree ORDER BY degree DESC, name LIMIT 4",
    )
    assert rows == [["a_def", 1], ["a_g", 1], ["b_def", 1], ["c_g", 1]]


def test_ppr_walks_each_graph_from_the_seed(engine):
    rows = _rows(
        engine,
        f"CALL ivg.ppr(['{_id('a')}'], 0.85, 20) YIELD node, score RETURN node.node_id AS id, node.name AS name, score",
    )
    scores = {name: score for _, name, score in rows}
    assert len(rows) == 6 and set(scores) == {"a_def", "b_def", "c_def", "a_g", "c_g", "x_g"}, rows
    assert all(s > 0 for s in scores.values()), rows
    # the two graphs are the same chain a -> . -> ., so the scores pair up by position, and c's differ
    assert scores["a_g"] == pytest.approx(scores["a_def"])
    assert scores["c_g"] == pytest.approx(scores["b_def"])
    assert scores["x_g"] == pytest.approx(scores["c_def"])
    assert scores["c_g"] != pytest.approx(scores["c_def"])


def test_ppr_then_a_hop_stays_in_the_row_s_graph(engine):
    rows = _rows(
        engine,
        f"CALL ivg.ppr(['{_id('a')}'], 0.85, 20) YIELD node MATCH (node)-[:R]->(m) "
        "RETURN node.name AS s, m.name AS o",
    )
    assert _bag(rows) == Counter({("a_def", "b_def"): 1, ("b_def", "c_def"): 1, ("a_g", "c_g"): 1, ("c_g", "x_g"): 1})


def test_neighbors_out_are_each_graph_s_own(engine):
    rows = _rows(engine, f"CALL ivg.neighbors(['{_id('a')}'], 'R', 'out') YIELD neighbor RETURN neighbor.name AS n")
    assert _bag(rows) == Counter({("b_def",): 1, ("c_g",): 1})


def test_neighbors_both_keep_the_same_id_in_two_graphs_apart(engine):
    rows = _rows(engine, f"CALL ivg.neighbors(['{_id('c')}'], 'R', 'both') YIELD neighbor RETURN neighbor.name AS n")
    assert _bag(rows) == Counter({("b_def",): 1, ("a_g",): 1, ("x_g",): 1})


def test_neighbors_under_use_graph_read_that_graph_only(engine):
    rows = _rows(
        engine,
        f"USE GRAPH '{GRAPH}' CALL ivg.neighbors(['{_id('c')}'], 'R', 'both') YIELD neighbor RETURN neighbor.name AS n",
    )
    assert _bag(rows) == Counter({("a_g",): 1, ("x_g",): 1})


def test_weighted_shortest_path_handler_answers_each_graph_s_cost(engine):
    rows = _rows(engine, f"CALL ivg.shortestPath.weighted('{_id('a')}', '{_id('c')}') YIELD totalCost")
    assert sorted(float(r[-1]) for r in rows) == [1.0, 2.0]


def test_weighted_shortest_path_translated_answers_each_graph_s_cost(engine):
    rows = _rows(
        engine,
        f"CALL ivg.shortestPath.weighted('{_id('a')}', '{_id('c')}') YIELD path, totalCost "
        "RETURN totalCost ORDER BY totalCost",
    )
    assert [float(r[0]) for r in rows] == [1.0, 2.0]


def test_vector_search_handler_answers_each_graph_s_top_k(engine):
    rows = _rows(engine, "CALL ivg.vector.search('R192', 'embedding', $v, 1) YIELD node, score", {"v": _unit(0)})
    assert _bag(rows) == Counter({(_id("a"), 1.0): 1, (_id("x"), 1.0): 1})


def test_vector_search_translated_reads_each_graph_s_vectors_and_names(engine):
    rows = _rows(
        engine,
        "CALL ivg.vector.search('R192', 'embedding', $v, 1) YIELD node, score RETURN node.name AS n, score",
        {"v": _unit(0)},
    )
    assert _bag(rows) == Counter({("a_def", 1.0): 1, ("x_g", 1.0): 1})


def test_bm25_rows_are_the_index_s_graph_s_nodes(engine):
    rows = _rows(engine, "CALL ivg.bm25.search('r192bm', 'apple', 5) YIELD node, score RETURN node.name AS n")
    assert rows == [["a_def"]]


def test_ivf_rows_are_the_index_s_graph_s_nodes(engine):
    rows = _rows(
        engine,
        "CALL ivg.ivf.search('r192ivf', $v, 5, 1) YIELD node, score RETURN node.name AS n, score",
        {"v": _unit(0)},
    )
    assert _bag(rows) == Counter({("a_def", 1.0): 1, ("b_def", 0.0): 1})


NKG_PROCEDURES = [
    "ivg.betweenness({}) YIELD node, score RETURN node, score",
    "ivg.closeness({}) YIELD node, score RETURN node, score",
    "ivg.eigenvector({}) YIELD node, score RETURN node, score",
    "ivg.leiden({}) YIELD node, community RETURN node, community",
    "ivg.triangleCount({}) YIELD node, triangles RETURN node, triangles",
    "ivg.scc({}) YIELD node, component RETURN node, component",
    "ivg.kcore({}) YIELD node, coreness RETURN node, coreness",
]


@pytest.mark.parametrize("call", NKG_PROCEDURES, ids=lambda c: c.split("(")[0])
def test_an_nkg_procedure_raises_stating_the_all_graphs_rule(engine, call):
    with pytest.raises(ValueError) as raised:
        engine.execute_cypher(f"CALL {call}", raise_on_error=True)
    text = str(raised.value)
    assert "union of each graph's own answer" in text and repr(GRAPH) in text, text


@pytest.mark.parametrize(
    "call",
    [
        "gds.pageRank.stream('g') YIELD nodeId, score",
        "gds.betweenness.stream('g') YIELD nodeId, score",
        f"gds.shortestPath.dijkstra.stream('g', {{sourceNode: '{_id('a')}', targetNode: '{_id('c')}'}}) "
        "YIELD totalCost",
    ],
    ids=["pageRank", "betweenness", "dijkstra"],
)
def test_a_gds_procedure_over_the_default_graph_raises(engine, call):
    with pytest.raises(ValueError) as raised:
        engine.execute_cypher(f"CALL {call}", raise_on_error=True)
    text = str(raised.value)
    assert "union of each graph's own answer" in text and repr(GRAPH) in text, text


@pytest.mark.parametrize(
    "call",
    ["gds.louvain.stream('g') YIELD nodeId, communityId", "gds.nodeSimilarity.stream('g') YIELD node1, node2"],
    ids=["louvain", "nodeSimilarity"],
)
def test_a_gds_shim_without_a_handler_raises(engine, call):
    with pytest.raises(ValueError):
        engine.execute_cypher(f"CALL {call}", raise_on_error=True)


def test_retrieve_raises_without_use_graph(engine):
    with pytest.raises(ValueError) as raised:
        engine.execute_cypher("CALL ivg.retrieve('apple', 3, 'r192bm') YIELD node, rrf_score", raise_on_error=True)
    assert "USE GRAPH" in str(raised.value), str(raised.value)
