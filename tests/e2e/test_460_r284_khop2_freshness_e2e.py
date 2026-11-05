"""4.6.0 R284: khop2_count_fast and khop2_count_exact read the precomputed two-hop counts only while they are current.

`Graph.KG.TraversalKHop.KHop2CountFast` answered `^KG("deg2p")` whenever it held an entry, so after any write the count
was the graph as the last `Build2HopStats` saw it. The counts are now stamped with the Graph-Version Token they were
built from (`^IVG.BuiltFrom("deg2p")`, `("deg2p_exact")`, ADR-0008) and read only while it equals `^KG("__version")`,
which every edge write bumps; otherwise the walk answers.

The graph, by hand, in the default graph, predicate p: s -> m1, s -> m2, m1 -> x1, m2 -> x1. After sync() the fast
count is the precomputed upper bound, the middle nodes' out-degrees summed, 1 + 1 = 2, and the exact count 1 (x1): the
two differ from each other, so each shows which source answered. Then m1 -> x3 and m2 -> x4 are written: the nodes
first reached at the second hop are x1, x3, x4, so the walk answers 3 for both (stale, they said 2 and 1). After the
next sync() the precomputed counts answer again: 2 + 2 = 4, and 3.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the nodes are removed after.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = ("r284s", "r284m1", "r284m2", "r284x1", "r284x3", "r284x4")


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, embedding_dimension=alt.DIM, namespace=alt.NS, schema_prefix=schema)
    for nid in NODES:
        eng.delete_node(nid, graph=None)
    for nid in NODES:
        eng.create_node(nid)
    for s, o in (("r284s", "r284m1"), ("r284s", "r284m2"), ("r284m1", "r284x1"), ("r284m2", "r284x1")):
        eng.create_edge(s, "r284p", o)
    eng.sync()
    yield eng
    try:
        for nid in NODES:
            eng.delete_node(nid, graph=None)
    finally:
        conn.close()


def test_after_sync_the_precomputed_counts_answer(engine):
    assert engine.khop2_count_fast("r284s", "r284p") == 2
    assert engine.khop2_count_exact("r284s", "r284p") == 1


def test_after_a_write_the_walk_answers_until_the_next_sync(engine):
    engine.create_edge("r284m1", "r284p", "r284x3")
    engine.create_edge("r284m2", "r284p", "r284x4")
    assert engine.khop2_count_fast("r284s", "r284p") == 3
    assert engine.khop2_count_exact("r284s", "r284p") == 3
    engine.sync()
    assert engine.khop2_count_fast("r284s", "r284p") == 4
    assert engine.khop2_count_exact("r284s", "r284p") == 3
