"""4.6.0 R184, live: an algorithm method and its Cypher procedure answer the default graph without USE GRAPH.

`engine.strongly_connected_components()` (and triangle_count, k_core, eigenvector_centrality) answered the default
graph while `CALL ivg.scc()` (and the others) answered every graph merged; betweenness_centrality,
closeness_centrality and leiden_communities answered every graph merged too. Now, where a named graph holds edges,
the methods answer the default graph's and the procedures, which read `^NKG` (every graph's edges, no graph key),
raise naming the method that answers it. Where only the default graph holds edges both answer it, as before
(tests/unit/test_460_r184_default_graph_scope.py pins the dispatch).

By hand, in a namespace emptied first (`Graph.KG.Eraser.EraseAll`):

- the default graph, a directed 3-cycle a -R-> b -R-> c -R-> a;
- the graph r184g, a 2-cycle through the same a: a -R-> x -R-> a.

The default graph's answers (n = 3):

    betweenness   each node is the one middle node of one shortest path (b on a->c, c on b->a, a on c->b): 1.0
    closeness     harmonic, out: from each node one node at 1 hop and one at 2, 1 + 1/2 = 1.5 (the raw sum)
    eigenvector   A.x keeps the uniform vector (one in-edge each), L2-normalised: 1/sqrt(3) = 0.57735
    leiden        one community of 3 (modularity 0; any split is negative)
    triangles     one triangle each, local clustering 1.0
    scc           one component of 3
    kcore         the symmetrised triangle: coreness 2 each

Merged with r184g, a has a fourth neighbour x and the SCC is {a, b, c, x}: what the procedures answered before.
"""

from __future__ import annotations

import math
import os

import pytest

from tests.e2e import ns460alt as alt

#: an IVG460FX* scratch namespace set up for `Graph_KG` (scripts/dev/make_scratch_namespace.py IVG460FXI)
NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

GRAPH = "r184g"
DEFAULT_EDGES = (("a", "R", "b"), ("b", "R", "c"), ("c", "R", "a"))
NAMED_EDGES = (("a", "R", "x"), ("x", "R", "a"))
DEFAULT_NODES = {"a", "b", "c"}

#: each pair: the procedure's CALL, the column it yields, the method and the key it answers in, the default answer
PAIRS = {
    "betweenness": ("ivg.betweenness({})", "score", "betweenness_centrality", "score", {n: 1.0 for n in "abc"}),
    "closeness": ("ivg.closeness({})", "score", "closeness_centrality", "score", {n: 1.5 for n in "abc"}),
    "eigenvector": (
        "ivg.eigenvector({})", "score", "eigenvector_centrality", "score", {n: 1 / math.sqrt(3) for n in "abc"},
    ),
    "leiden": ("ivg.leiden({})", "community", "leiden_communities", "community", None),
    "triangleCount": ("ivg.triangleCount({})", "triangles", "triangle_count", "triangles", {n: 1 for n in "abc"}),
    "scc": ("ivg.scc({})", "component", "strongly_connected_components", "component", None),
    "kcore": ("ivg.kcore({})", "coreness", "k_core", "coreness", {n: 2 for n in "abc"}),
}


def _id(n: str) -> str:
    return f"r184:{n}"


def _bare(node_id: str) -> str:
    return node_id.split(":", 1)[1]


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for n in sorted(DEFAULT_NODES):
        eng.create_node(_id(n), labels=["R184"])
    for s, p, o in DEFAULT_EDGES:
        eng.create_edge(_id(s), p, _id(o))
    for n in ("a", "x"):
        eng.create_node(_id(n), labels=["R184"], graph=GRAPH)
    conn.commit()
    eng.sync()
    yield eng
    conn.close()


def _graphs_with_edges(engine) -> list:
    """The graph keys under ^KG("out"), read here with the Native API rather than asked of the code under test."""
    import iris

    native = iris.createIRIS(engine.conn)
    keys, key = [], native.nextSubscript(False, "^KG", "out", "")
    while key not in (None, ""):
        keys.append(str(key))
        key = native.nextSubscript(False, "^KG", "out", key)
    return keys


def _named_edges(engine, present: bool) -> None:
    """r184g's edges, written or deleted, and ^NKG rebuilt to match."""
    for s, p, o in NAMED_EDGES:
        if present:
            engine.create_edge(_id(s), p, _id(o), graph=GRAPH)
        else:
            engine.delete_edge(_id(s), p, _id(o), graph=GRAPH)
    engine.conn.commit()
    engine.sync()
    assert _graphs_with_edges(engine) == (["0", GRAPH] if present else ["0"])


@pytest.fixture
def default_only(engine):
    if GRAPH in _graphs_with_edges(engine):
        _named_edges(engine, False)
    return engine


@pytest.fixture
def with_named(engine):
    if GRAPH not in _graphs_with_edges(engine):
        _named_edges(engine, True)
    return engine


def _method_answer(engine, name):
    _, _, method, key, _ = PAIRS[name]
    rows = [d for d in getattr(engine, method)() if "id" in d and str(d["id"]).startswith("r184:")]
    return {_bare(d["id"]): d[key] for d in rows}


def _call_answer(engine, name):
    call, col, _, _, _ = PAIRS[name]
    res = engine.execute_cypher(f"CALL {call} YIELD node, {col} RETURN node, {col}", raise_on_error=True)
    # `node` comes back as a node (id, labels, properties), then the yielded column
    return {_bare(r[0]): r[-1] for r in res.rows if str(r[0]).startswith("r184:")}


def _assert_default_answer(name, answer):
    expected = PAIRS[name][4]
    assert set(answer) == DEFAULT_NODES, answer
    if expected is None:
        # one community / component holding all three
        assert len({answer[n] for n in DEFAULT_NODES}) == 1, answer
    else:
        assert {n: round(float(v), 5) for n, v in answer.items()} == {
            n: round(float(v), 5) for n, v in expected.items()
        }, answer


@pytest.mark.parametrize("name", list(PAIRS))
def test_with_a_named_graph_the_method_answers_the_default_graph(with_named, name):
    _assert_default_answer(name, _method_answer(with_named, name))


@pytest.mark.parametrize("name", list(PAIRS))
def test_with_a_named_graph_the_procedure_raises_naming_the_method(with_named, name):
    call, col, method, _, _ = PAIRS[name]
    with pytest.raises(ValueError) as raised:
        with_named.execute_cypher(f"CALL {call} YIELD node, {col} RETURN node, {col}", raise_on_error=True)
    text = str(raised.value)
    assert f"ivg.{name}" in text and f"engine.{method}()" in text and repr(GRAPH) in text, text


def test_with_a_named_graph_gds_betweenness_answers_the_default_graph(with_named):
    rows = with_named.execute_cypher("CALL gds.betweenness.stream('g') YIELD nodeId, score").rows
    assert {_bare(n): round(float(s), 5) for n, s in rows if str(n).startswith("r184:")} == {
        n: 1.0 for n in DEFAULT_NODES
    }


@pytest.mark.parametrize("name", list(PAIRS))
def test_with_the_default_graph_alone_the_two_agree(default_only, name):
    by_method = _method_answer(default_only, name)
    by_call = _call_answer(default_only, name)
    _assert_default_answer(name, by_method)
    _assert_default_answer(name, by_call)
