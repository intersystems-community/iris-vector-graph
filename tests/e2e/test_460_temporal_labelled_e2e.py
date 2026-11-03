"""4.6.0 R069: `WHERE r.ts = 100` on a labelled pattern answers instead of "Incorrect number of parameters".

The temporal route (`_trp_temporal_edge`) answers `[r]` from a CTE of the window's edges (`s`, `p`, `o`, `ts`,
`weight`) and drops the source node's table. It dropped the source's label join with it, but left the label's
parameter and its `l.s IS NOT NULL` condition: "Incorrect number of parameters". A target label joined on
`<cte>.node_id`, a column the CTE does not have (SQLCODE -29). Also 4.5.x.

The graph, worked by hand: a:L, b:L, c:M, d:M; temporal P edges a->b @100, a->c @100, c->b @100, d->a @100, a->b @200.
At ts 100 the edges are a->b, a->c, c->b, d->a; the L sources among them are a (a->b, a->c); the L targets b and a
(a->b, c->b, d->a); both ends L only a->b. At ts 200 only a->b.
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
    ids = {k: f"ivg460tl_{run}_{k}" for k in "abcd"}
    labels = {"a": "L", "b": "L", "c": "M", "d": "M"}
    lab = {k: f"Ivg460T{v}{run}" for k, v in labels.items()}
    for k, nid in ids.items():
        engine.create_node(nid, labels=[lab[k]])
    p = f"IVG460TL_{run}"
    for s, o, ts in (("a", "b", 100), ("a", "c", 100), ("c", "b", 100), ("d", "a", 100), ("a", "b", 200)):
        engine.create_edge_temporal(ids[s], p, ids[o], ts)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, p, f"Ivg460TL{run}", f"Ivg460TM{run}"
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


def _pairs(g, pattern: str, where: str = "r.ts = 100", ret: str = "x.node_id, y.node_id"):
    engine, ids, p, L, M = g
    q = f"MATCH {pattern.format(P=p, L=L, M=M)} WHERE {where} RETURN {ret}"
    result = engine.execute_cypher(q)
    assert not getattr(result, "error", None), (q, result.error)
    inverse = {v: k for k, v in ids.items()}
    return sorted(tuple(inverse.get(v, v) for v in row) for row in result.rows)


def test_no_label_is_the_window(g):
    assert _pairs(g, "(x)-[r:{P}]->(y)") == [("a", "b"), ("a", "c"), ("c", "b"), ("d", "a")]


def test_a_labelled_source(g):
    assert _pairs(g, "(x:{L})-[r:{P}]->(y)") == [("a", "b"), ("a", "c")]


def test_a_labelled_target(g):
    assert _pairs(g, "(x)-[r:{P}]->(y:{L})") == [("a", "b"), ("c", "b"), ("d", "a")]


def test_both_ends_labelled(g):
    assert _pairs(g, "(x:{L})-[r:{P}]->(y:{L})") == [("a", "b")]


def test_both_ends_labelled_at_200(g):
    assert _pairs(g, "(x:{L})-[r:{P}]->(y:{L})", where="r.ts = 200") == [("a", "b")]


def test_a_labelled_source_over_a_range(g):
    assert _pairs(g, "(x:{L})-[r:{P}]->(y)", where="r.ts >= 50 AND r.ts <= 150") == [("a", "b"), ("a", "c")]


def test_incoming_with_the_label_on_the_pattern_s_first_node(g):
    # (y:L)<-[r]-(x): y is each edge's target
    assert _pairs(g, "(y:{L})<-[r:{P}]-(x)") == [("a", "b"), ("c", "b"), ("d", "a")]


def test_incoming_labelled_both_ends(g):
    # (x:M)<-[r]-(y:L): an L source, an M target: a->c only
    assert _pairs(g, "(x:{M})<-[r:{P}]-(y:{L})") == [("c", "a")]
