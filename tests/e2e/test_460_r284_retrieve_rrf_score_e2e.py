"""4.6.0 R284: CALL ivg.retrieve yields rrf_score as a float, under driver 5.4.0 and 5.4.1.

The fused score was `SUM(1.0 / (k + ROW_NUMBER() ...))`, a NUMERIC, which both drivers answered as ObjectScript's text
(measured 2026-11-04: '.0325224748810153358', a str, under 5.4.0 and 5.4.1). It is cast to DOUBLE.

The graph, by hand (default graph, label R284, vectors 8 wide; the query embeds as [1, 0, ...] through a stub
embed_text, and the BM25 index is over `name`):

    node   name          cosine to q   BM25 for "alpha"            RRF, k = 60
    ret1   "alpha beta"  1.0  rank 1   rank 2 (the longer doc)     1/61 + 1/62 = 0.0325225
    ret2   "alpha"       0.0  rank 3   rank 1                      1/61 + 1/63 = 0.0322665
    ret3   "gamma"       0.6  rank 2   -                           1/62        = 0.0161290

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up, its default vector table 8
wide); the nodes and the index are removed after.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

VECS = {"ret1": [1.0] + [0.0] * 7, "ret2": [0.0, 1.0] + [0.0] * 6, "ret3": [0.6, 0.8] + [0.0] * 6}
NAMES = {"ret1": "alpha beta", "ret2": "alpha", "ret3": "gamma"}
INDEX = "r284idx"


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, embedding_dimension=8, namespace=alt.NS, schema_prefix=schema)
    for nid in VECS:
        eng.delete_node(nid, graph=None)
    for nid, vec in VECS.items():
        eng.create_node(nid, labels=["R284"], properties={"name": NAMES[nid]})
        eng.store_embedding(nid, vec)
    eng.bm25_build(INDEX, ["name"])
    eng.embed_text = lambda text: [1.0] + [0.0] * 7
    yield eng
    try:
        iris.createIRIS(conn).classMethodValue("Graph.KG.BM25Index", "Drop", INDEX)
        for nid in VECS:
            eng.delete_node(nid, graph=None)
    finally:
        conn.close()


@pytest.mark.parametrize("tail", ["", " ORDER BY rrf_score DESC"])
def test_rrf_score_is_a_float_with_the_hand_worked_value(engine, tail):
    r = engine.execute_cypher(
        f"CALL ivg.retrieve('alpha', 5, '{INDEX}', '*', 60) YIELD node, rrf_score RETURN node, rrf_score{tail}",
        {}, raise_on_error=True)
    i, n = r.columns.index("rrf_score"), r.columns.index("node_id")
    got = [(row[n], row[i]) for row in r.rows]
    assert [nid for nid, _ in got] == ["ret1", "ret2", "ret3"]
    assert all(type(score) is float for _, score in got), got
    for (_nid, score), want in zip(got, (1 / 61 + 1 / 62, 1 / 61 + 1 / 63, 1 / 62)):
        assert score == pytest.approx(want, rel=1e-9)
