"""4.6.0 R050 W5: `CALL ivg.kcore` and `CALL ivg.triangleCount` rank as `k_core()` and `triangle_count()` do: highest
score first, ties by node ID in IRIS collation (a canonical number first, by value, then the rest as text), and
`topK` keeps the first rows of that order.

The procedures broke a tie by the node's interned ^NKG index, the order it was first written in, so their `topK`
cut kept other nodes than the methods' (k_core(top_k=3) [c1, c2, co1], CALL ivg.kcore({topK: 3}) [p1, p2, c1]).

The graph, by hand, in the default graph of a namespace emptied first, written so that write order is not ID order:
    d -> c -> a -> d     (a triangle)       b -> 9 -> 10 -> b     (a triangle)      a -> e
Read undirected: coreness 2 for a, b, c, d, 9, 10 and 1 for e; one triangle each for those six, none for e.
IRIS collation: 9, 10, a, b, c, d, e.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

EDGES = [("d", "c"), ("c", "a"), ("a", "d"), ("b", "9"), ("9", "10"), ("10", "b"), ("a", "e")]
ORDER = ["9", "10", "a", "b", "c", "d", "e"]
CORENESS = {"9": 2, "10": 2, "a": 2, "b": 2, "c": 2, "d": 2, "e": 1}
TRIANGLES = {"9": 1, "10": 1, "a": 1, "b": 1, "c": 1, "d": 1, "e": 0}
TOP_K = [3, 4, 0]


def _expected(scores: dict, top_k: int) -> list:
    rows = [(n, scores[n]) for n in ORDER]  # ORDER is already score-descending, ties in IRIS collation
    return rows[:top_k] if top_k else rows


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for s, o in EDGES:
        eng.create_edge(s, "R", o)
    conn.commit()
    eng.sync()
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    conn.close()


@pytest.fixture(params=["arno", "no-arno"])
def route(request, monkeypatch):
    if request.param == "no-arno":
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    return request.param


@pytest.mark.parametrize("top_k", TOP_K)
def test_k_core(engine, route, top_k):
    assert [(r["id"], r["coreness"]) for r in engine.k_core(top_k=top_k)] == _expected(CORENESS, top_k)


@pytest.mark.parametrize("top_k", TOP_K)
def test_call_kcore(engine, top_k):
    rows = engine.execute_cypher(
        f"CALL ivg.kcore({{topK: {top_k}}}) YIELD node, coreness RETURN node.node_id AS id, coreness", raise_on_error=True
    ).rows
    assert [(r[0], int(r[1])) for r in rows] == _expected(CORENESS, top_k)


@pytest.mark.parametrize("top_k", TOP_K)
def test_triangle_count(engine, route, top_k):
    assert [(r["id"], r["triangles"]) for r in engine.triangle_count(top_k=top_k)] == _expected(TRIANGLES, top_k)


@pytest.mark.parametrize("top_k", TOP_K)
def test_call_triangle_count(engine, top_k):
    rows = engine.execute_cypher(
        f"CALL ivg.triangleCount({{topK: {top_k}}}) YIELD node, triangles RETURN node.node_id AS id, triangles", raise_on_error=True
    ).rows
    assert [(r[0], int(r[1])) for r in rows] == _expected(TRIANGLES, top_k)
