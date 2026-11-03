"""4.6.0 R065: `engine.algorithms.*` forwards to methods the engine has.

`engine.algorithms.execute_wcc()` forwarded to `engine.execute_wcc`, which does not exist (AttributeError); so did
`execute_scc`, `execute_k_core`, `execute_triangle_count` and `pagerank`. The graph here, worked by hand: a->b->c (one
weakly connected component; a, b, c each their own strongly connected one) and d<->e (a second component, one strongly
connected component of two); no triangle anywhere.
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
    ids = {k: f"ivg460alg_{run}_{k}" for k in "abcde"}
    for nid in ids.values():
        engine.create_node(nid, labels=["Ivg460Alg"])
    for s, o in (("a", "b"), ("b", "c"), ("d", "e"), ("e", "d")):
        engine.create_edge(ids[s], "IVG460_ALG", ids[o])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


def test_execute_wcc_answers_the_two_components(g):
    engine, ids = g
    comp = engine.algorithms.execute_wcc()
    a, b, c, d, e = (comp[ids[k]] for k in "abcde")
    assert a == b == c and d == e and a != d, comp


def _by_node(rows, ids, key):
    inverse = {v: k for k, v in ids.items()}
    return {inverse[r["node"] if "node" in r else r["id"]]: r[key] for r in rows if (r.get("node") or r.get("id")) in inverse}


def test_execute_scc_answers_the_strong_components(g):
    engine, ids = g
    rows = engine.algorithms.execute_scc()
    assert rows, rows
    key = next(k for k in rows[0] if k not in ("node", "id"))
    comp = _by_node(rows, ids, key)
    assert comp["d"] == comp["e"] and len({comp["a"], comp["b"], comp["c"], comp["d"]}) == 4, comp


def test_execute_triangle_count_finds_none(g):
    engine, ids = g
    rows = engine.algorithms.execute_triangle_count()
    counts = _by_node(rows, ids, "triangles")
    assert counts and set(counts.values()) == {0}, counts


def test_execute_k_core_puts_the_path_in_the_1_core(g):
    engine, ids = g
    core = _by_node(engine.algorithms.execute_k_core(), ids, "coreness")
    assert (core["a"], core["b"], core["c"]) == (1, 1, 1), core


def test_pagerank_ranks_the_chain_in_order(g):
    # a has no in-link, b's is from a, c's from b: rank grows along a->b->c
    engine, ids = g
    ranks = dict(engine.algorithms.pagerank())
    assert ranks[ids["a"]] < ranks[ids["b"]] < ranks[ids["c"]], {k: ranks.get(v) for k, v in ids.items()}
