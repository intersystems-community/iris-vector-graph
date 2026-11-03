"""4.6.0 R070: approx_count_distinct agrees with relationship uniqueness on undirected and incoming walks.

`approx_count_distinct(b)` over `(a {node_id: $x})-[:P*1..k]-(b)` must count what `count(DISTINCT b)` counts (N1's
rule, R057): the far ends of paths that use no relationship twice. The HLL route (`CountDistinctKHop`) gathers the
nodes within k-1 hops, walking the pattern's direction, and merges their `^NKG("$agg")` sketches, which hold each
node's OUTGOING neighbours. That is the far-end count for an outgoing walk; for an undirected one it walks back over
the relationship it arrived by, and for an incoming one it counts the wrong neighbours.

Graphs, over P, worked by hand (an HLL of 256 registers is exact at these sizes):

    chain     uz0->uz1->uz2->uz3
    triangle  t0->t1->t2->t0

    chain from uz0, *1..3, undirected:   uz1, uz2, uz3                       3
    chain from uz1, *1..2, undirected:   uz0, uz2 (1 hop), uz3 (2)           3
    chain from uz3, *1..2, incoming:     uz2, uz1                            2
    triangle from t0, *1..2, undirected: t1, t2; t0 only back over the relationship it left by   2
    triangle from t0, *1..3, undirected: t1, t2, and t0 round the cycle      3
    chain from uz0, *1..2, outgoing:     uz1, uz2                            2
    triangle from t0, *1..2, outgoing:   t1, t2                              2
    triangle from t0, *1..3, outgoing:   t1, t2, t0                          3

The route answered 3 for the triangle's undirected *1..2 (t0 counted) and 1 for the chain's incoming *1..2.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    p = f"IVG460ACD_{run}"
    ids = {k: f"ivg460acd_{run}_{k}" for k in ("uz0", "uz1", "uz2", "uz3", "t0", "t1", "t2")}
    for nid in ids.values():
        engine.create_node(nid, labels=["Ivg460Acd"])
    for s, o in (("uz0", "uz1"), ("uz1", "uz2"), ("uz2", "uz3"), ("t0", "t1"), ("t1", "t2"), ("t2", "t0")):
        engine.create_edge(ids[s], p, ids[o])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    engine.sync()  # the HLL route reads ^NKG's sketches
    yield engine, ids, p
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


def _approx(g, start: str, rel: str):
    engine, ids, p = g
    q = f"MATCH (a {{node_id: $x}}){rel.format(P=p)}(b) RETURN approx_count_distinct(b) AS n"
    r = engine.execute_cypher(q, {"x": ids[start]})
    assert getattr(r, "error", None) is None, r.error
    warnings = " ".join(getattr(getattr(r, "metadata", None), "warnings", None) or [])
    return r.rows[0][0], warnings


@pytest.mark.parametrize(
    "start,rel,expect",
    [
        ("uz0", "-[:{P}*1..3]-", 3),
        ("uz1", "-[:{P}*1..2]-", 3),
        ("uz3", "<-[:{P}*1..2]-", 2),
        ("t0", "-[:{P}*1..2]-", 2),
        ("t0", "-[:{P}*1..3]-", 3),
    ],
)
def test_undirected_and_incoming_count_the_paths_ends(g, start, rel, expect):
    n, _ = _approx(g, start, rel)
    assert n == expect


@pytest.mark.parametrize(
    "start,rel,expect",
    [("uz0", "-[:{P}*1..2]->", 2), ("t0", "-[:{P}*1..2]->", 2), ("t0", "-[:{P}*1..3]->", 3)],
)
def test_outgoing_keeps_the_hll_route_and_its_answer(g, start, rel, expect):
    n, warnings = _approx(g, start, rel)
    assert n == expect
    assert "HLL-" in warnings, warnings
