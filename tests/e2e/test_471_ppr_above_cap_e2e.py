"""4.7.1 H2, live: personalized PageRank asked for more rows than the ObjectScript route's 1000-row cap.

4.7.0 row 9 made PPR answer every node of the graph, 0.0 for a node the walk never reaches (``_ppr_every_node``). The
store and RunJson routes cut their answer at 1000 rows unless sent a limit, and a ``return_top_k`` above that was not
sent: the 1000 rows came back and ``_ppr_every_node`` filled the rest with 0.0 for nodes the walk did reach. On a star
``a -> n0..n2999``, ``kg_PERSONALIZED_PAGERANK(['a'], return_top_k=2000)`` answered ``a``, 999 leaves at their score and
1000 leaves at 0.0. Now the route is sent the limit, so it answers that many true rows, and only a node the walk never
reaches is 0.0.

The known answers are networkx's ``pagerank(G, alpha=0.85, personalization={'a': 1})`` (IVG's convention, networkx's
``dangling=None``: a leaf hands its score back to the seed). By hand: a = 0.15 + 0.85 * (sum of the leaves) and each
leaf is 0.85 * a / 3000, so a = 0.15 / (1 - 0.85 ** 2) = 0.540541 and a leaf 0.000153153; a node with no edge is 0.0.
Ties go by id in IRIS collation (code point order for these ids), so 2000 rows are ``a`` and the first 1999 leaves.

    IVG_NAMESPACE=IVG470H2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_471_ppr_above_cap_e2e.py
"""

from __future__ import annotations

from unittest.mock import patch

import networkx as nx
import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import conn, native  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LEAVES = [f"n{i}" for i in range(3000)]
LONERS = [f"z{i}" for i in range(5)]
A_HAND = 0.15 / (1 - 0.85 ** 2)
LEAF_HAND = 0.85 * A_HAND / len(LEAVES)
#: IVG stops once the summed change is below N * 1e-6 (networkx's rule), so its scores are networkx's to about 0.1%
REL = 2e-3


def _networkx(nodes):
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from(("a", leaf) for leaf in LEAVES)
    return nx.pagerank(g, alpha=0.85, personalization={"a": 1}, max_iter=1000, tol=1e-12)


@pytest.fixture(scope="module")
def star(conn):  # noqa: F811
    """The star plus five nodes with no edge, and networkx's scores for it (checked against the hand-worked ones)."""
    nodes = ["a"] + LEAVES + LONERS
    want = _networkx(nodes)
    assert want["a"] == pytest.approx(A_HAND, abs=1e-9)
    assert all(want[leaf] == pytest.approx(LEAF_HAND, abs=1e-12) for leaf in LEAVES)
    assert all(want[z] == 0.0 for z in LONERS)
    native(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    engs = ns463.engines(conn)
    eng = engs["default"]
    eng.bulk_create_nodes([{"id": n, "labels": ["S"]} for n in nodes])
    eng.bulk_create_edges([{"source_id": "a", "predicate": "R", "target_id": leaf} for leaf in LEAVES])
    eng.sync()
    assert eng.node_count() == len(nodes), "the graph did not load"
    return engs, want


def _fast_path_engine(eng):
    """The RunJson route alone: the store's route off, the fallback pinned to fail."""
    from iris_vector_graph import IRISGraphEngine

    fast = IRISGraphEngine(eng.conn, namespace=ns463.NS, schema_prefix=eng._schema_prefix)
    fast._store_capabilities = dict(fast._store_capabilities, ppr=False)
    fast.capabilities.objectscript_deployed = True
    fast.capabilities.kg_built = True
    return fast


def _assert_true_rows(got, want, n):
    """``n`` rows, ``a`` first, then the first leaves in id order, each at networkx's score: none at 0.0."""
    assert len(got) == n, f"{len(got)} rows, asked for {n}"
    expected = ["a"] + sorted(LEAVES)[: n - 1]
    assert sorted(got) == sorted(expected)
    zeros = [k for k, v in got.items() if v == 0.0]
    assert not zeros, f"{len(zeros)} reached nodes at 0.0, e.g. {zeros[:3]}"
    for k, v in got.items():
        assert v == pytest.approx(want[k], rel=REL), k


ROUTES = {
    "store": lambda eng, **kw: _store_call(eng, **kw),
    "runjson": lambda eng, **kw: _fast_path_call(eng, **kw),
    "fallback": lambda eng, **kw: eng._kg_PERSONALIZED_PAGERANK_python_fallback(
        ["a"], 0.85, 100, kw.pop("tolerance", 1e-6), kw.pop("return_top_k", None), kw.pop("bidirectional", False),
        exclude_predicates=kw.pop("exclude_predicates", None)),
}


def _store_call(eng, **kw):
    """The store's route (Graph.KG.PageRank.RunJson through IRISSQLStore.execute_ppr), the other two pinned to fail."""
    assert eng._store_capabilities.get("ppr", True)
    with patch.object(eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")), \
            patch.object(eng, "_iris_obj", side_effect=AssertionError("RunJson fast path")):
        return eng.kg_PERSONALIZED_PAGERANK(["a"], **kw)


def _fast_path_call(eng, **kw):
    fast = _fast_path_engine(eng)
    with patch.object(fast, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")):
        return fast.kg_PERSONALIZED_PAGERANK(["a"], **kw)


@pytest.mark.parametrize("route", list(ROUTES))
def test_top_k_above_the_cap_answers_true_rows(star, route):
    """Known answer 1: 2000 rows, every leaf at networkx's leaf score."""
    engs, want = star
    _assert_true_rows(ROUTES[route](engs["default"], return_top_k=2000), want, 2000)


@pytest.mark.parametrize("route", list(ROUTES))
def test_top_k_above_the_graph_answers_every_node(star, route):
    """Known answer 2: top_k past the graph's 3006 nodes: every node, the five loners alone at 0.0."""
    engs, want = star
    got = ROUTES[route](engs["default"], return_top_k=5000)
    assert set(got) == set(want)
    assert sorted(k for k, v in got.items() if v == 0.0) == LONERS
    for k, v in got.items():
        assert v == pytest.approx(want[k], rel=REL, abs=1e-12), k
    # highest first, the zeros last
    assert list(got)[0] == "a" and list(got)[-5:] == LONERS


@pytest.mark.parametrize("route", list(ROUTES))
@pytest.mark.parametrize("kw", [
    {"tolerance": 1e-9},  # a tolerance but RunJson's default sends the trailing arguments
    {"exclude_predicates": []},  # fhir_concept_ppr's route: an exclusion lifts the cap and sends the limit
    {"exclude_predicates": [], "bidirectional": True},  # its bidirectional walk: a leaf hands its score back the same
], ids=["tolerance", "exclusion", "exclusion-bidirectional"])
def test_top_k_above_the_cap_with_other_arguments(star, route, kw):
    engs, want = star
    _assert_true_rows(ROUTES[route](engs["default"], return_top_k=2000, **kw), want, 2000)


def test_ppr_method_above_the_cap(star):
    engs, want = star
    scores = engs["default"].ppr("a", max_iter=100, top_k=2000)["scores"]
    _assert_true_rows({d["id"]: d["score"] for d in scores}, want, 2000)


def test_without_top_k_the_cap_is_true_rows(star):
    """No top_k: the route's own 1000 rows, all of them reached (kg_PPR and kg_PERSONALIZED_PAGERANK)."""
    engs, want = star
    _assert_true_rows(engs["default"].kg_PERSONALIZED_PAGERANK(["a"]), want, 1000)
    _assert_true_rows(dict(engs["default"].kg_PPR(["a"], max_iterations=100)), want, 1000)


def test_guided_subgraph_scores_only_reached_nodes(star):
    engs, want = star
    sub = engs["default"].kg_PPR_GUIDED_SUBGRAPH(["a"], ppr_top_k=2000, max_iterations=100)
    assert sub.ppr_scores and all(v > 0 for _k, v in sub.ppr_scores)
    assert sub.ppr_scores[0][0] == "a"


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("limit", [2000, 5000])
def test_call_ivg_ppr_with_a_limit_above_the_cap(star, route, limit):
    """CALL ivg.ppr reads at most kg_PPR's 1000 rows (translator._ppr_row_limit): a LIMIT above that answers those
    1000, ``a`` and 999 leaves at their score, not 0.0 rows for reached nodes."""
    engs, want = star
    r = engs[route].execute_cypher("CALL ivg.ppr(['a'], 0.85, 100) YIELD node, score "
                                   f"RETURN node.node_id AS id, score ORDER BY score DESC LIMIT {limit}", {},
                                   raise_on_error=True)
    _assert_true_rows({row[0]: float(row[1]) for row in r.rows}, want, 1000)
