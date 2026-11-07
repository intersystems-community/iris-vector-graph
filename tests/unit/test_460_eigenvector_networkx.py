"""Eigenvector centrality iterates as networkx's ``eigenvector_centrality(G)`` does (4.6.0, round-3 walkthrough).

On a directed graph whose only cycle was a 3-cycle (ann -> ben -> cat -> ann, plus tails), IVG's answer changed with
``max_iter`` mod 3 and matched networkx for no ``max_iter``: IVG iterated ``x A`` with no shift, which cycles with the
period of a periodic strongly connected component and never converges, and it answered the last round without a
word. networkx iterates ``x (A + I)``, normalises by the L2 norm, stops once the summed change is below ``N * tol``
and raises ``PowerIterationFailedConvergence`` after ``max_iter`` rounds otherwise.

This file checks the Python walk over ``^KG`` (the route where a named graph holds edges) against networkx itself on
periodic graphs, and the store and engine plumbing around both routes. ``Graph.KG.NKGAccelCentrality.
EigenvectorGlobal`` is checked live against networkx by tests/e2e/test_460_r306_algorithm_oracle_e2e.py over the
same families (tests/oracle/graphs.py, ``periodic``).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import networkx as nx
import pytest

from iris_vector_graph import PowerIterationFailedConvergence
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
from tests.oracle import graphs as oracle_graphs
from tests.oracle import reference as ref

WALKTHROUGH_TAIL = [("hal", "ann"), ("cat", "dan"), ("dan", "eve"), ("eve", "fay"), ("ann", "eve")]
#: The walkthrough's KNOWS cycle with its tails: the shape whose answer depended on max_iter mod 3.
PERIODIC = {
    "cycle2": [("a", "b"), ("b", "a")],
    "cycle3": [("a", "b"), ("b", "c"), ("c", "a")],
    "cycle4": [("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")],
    "cycle3_tails": [("ann", "ben"), ("ben", "cat"), ("cat", "ann")] + WALKTHROUGH_TAIL,
    "bipartite": [("u1", "v1"), ("v1", "u2"), ("u2", "v2"), ("v2", "u1"), ("u1", "v2"), ("v2", "u2")],
    "two_periods": [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "e"), ("e", "c")],
}


class _FakeKG:
    """LazyKG's reads over an edge list: the nodes with an edge, each out-neighbour once per edge."""

    edges: list = []

    def __init__(self, conn=None, include_sinks=False, **_):
        self._out = {}
        for s, t in self.edges:
            self._out.setdefault(s, []).append(t)
            self._out.setdefault(t, [])
        self._nodes = sorted(self._out)

    def iter_nodes(self):
        return iter(self._nodes)

    def out_neighbors(self, n):
        return list(self._out.get(n, []))


@pytest.fixture
def walk(monkeypatch):
    """A store whose ^NKG route is skipped (a named graph holds edges), so the Python walk over ^KG answers."""
    import iris_vector_graph.stores.lazy_kg as lazy_kg

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._named_graph_with_edges = lambda: "g"
    store._iris_obj = MagicMock
    store._default_graph_loners = lambda: []

    def use(edges):
        monkeypatch.setattr(lazy_kg, "LazyKG", type("KG", (_FakeKG,), {"edges": list(edges)}))
        return store

    return use


def test_a_store_called_without_loners_reads_them_itself(walk):
    """A direct store call answers every node, as the ObjectScript route (which reads them server-side) does."""
    store = walk([("a", "b"), ("b", "a")])
    store._default_graph_loners = lambda: ["solo"]
    got = dict(store.execute_eigenvector(100, 1e-6, 0).rows)
    assert got == pytest.approx(_nx([("a", "b"), ("b", "a")], loners=["solo"]), abs=1e-12)


def _nx(edges, loners=(), **kw):
    G = nx.DiGraph()
    G.add_nodes_from(loners)
    G.add_edges_from(edges)
    return nx.eigenvector_centrality(G, **kw)


@pytest.mark.parametrize("name", sorted(PERIODIC))
@pytest.mark.parametrize("max_iter", [100, 999, 1000])
def test_the_walk_matches_networkx_on_periodic_graphs(walk, name, max_iter):
    edges = PERIODIC[name]
    try:
        want = _nx(edges, max_iter=max_iter)
    except nx.PowerIterationFailedConvergence:
        with pytest.raises(PowerIterationFailedConvergence):
            walk(edges)._eigenvector_gref(max_iter, 1e-6, 0, None)
        return
    got = dict(walk(edges)._eigenvector_gref(max_iter, 1e-6, 0, None).rows)
    assert got == pytest.approx(want, abs=1e-12)


def test_the_walkthrough_answer_does_not_depend_on_max_iter_mod_3(walk):
    edges = PERIODIC["cycle3_tails"]
    answers = [dict(walk(edges)._eigenvector_gref(m, 1e-6, 0, None).rows) for m in (999, 1000, 1001)]
    assert answers[0] == answers[1] == answers[2]
    assert answers[0] == pytest.approx(_nx(edges, max_iter=1000), abs=1e-12)


def test_nodes_with_no_edge_take_part_as_in_networkx(walk):
    """networkx counts every node: one with no edge keeps its own score through the shift and is normalised with
    the rest, so it is neither 0 nor left out, and the norm and the N of N * tol count it."""
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]
    got = dict(walk(edges)._eigenvector_gref(100, 1e-6, 0, None, loners=["solo", "z9"]).rows)
    want = _nx(edges, loners=["solo", "z9"])
    assert set(got) == set(want)
    assert got == pytest.approx(want, abs=1e-12)
    assert got["solo"] == got["z9"] > 0


def test_a_graph_of_loners_alone_scores_each_one_over_root_n(walk):
    got = dict(walk([])._eigenvector_gref(100, 1e-6, 0, None, loners=["a", "b", "c", "d"]).rows)
    assert got == pytest.approx(_nx([], loners="abcd"), abs=1e-12)
    assert got == pytest.approx(dict.fromkeys("abcd", 0.5))


def test_an_edge_under_two_predicates_counts_once(walk):
    edges = [("a", "b"), ("a", "b"), ("b", "c"), ("c", "a"), ("c", "b")]
    got = dict(walk(edges)._eigenvector_gref(1000, 1e-9, 0, None).rows)
    assert got == pytest.approx(_nx(edges, max_iter=1000, tol=1e-9), abs=1e-12)


def test_a_self_loop_counts_as_networkx_counts_it(walk):
    edges = [("a", "a"), ("a", "b"), ("b", "c"), ("c", "a")]
    got = dict(walk(edges)._eigenvector_gref(1000, 1e-9, 0, None).rows)
    assert got == pytest.approx(_nx(edges, max_iter=1000, tol=1e-9), abs=1e-12)


def test_no_convergence_raises_as_networkx_does(walk):
    # a chain converges only polynomially under the shift: networkx gives up at 30 rounds
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    with pytest.raises(nx.PowerIterationFailedConvergence):
        _nx(edges, max_iter=30)
    with pytest.raises(PowerIterationFailedConvergence, match="30 iterations") as err:
        walk(edges)._eigenvector_gref(30, 1e-6, 0, None)
    assert err.value.max_iter == 30
    assert isinstance(err.value, RuntimeError)


def test_the_store_raises_a_failed_convergence_rather_than_answering_an_error(walk):
    store = walk([("a", "b"), ("b", "c"), ("c", "d")])
    with pytest.raises(PowerIterationFailedConvergence):
        store.execute_eigenvector(30, 1e-6, 0, None)


def test_the_objectscript_route_s_failed_convergence_raises_and_is_not_walked(monkeypatch):
    """EigenvectorGlobal's ``ERROR:PowerIterationFailedConvergence`` is the answer, not a reason to walk ^KG."""
    import iris_vector_graph.stores.lazy_kg as lazy_kg

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._named_graph_with_edges = lambda: None
    native = MagicMock()
    native.classMethodValue.return_value = (
        "ERROR:PowerIterationFailedConvergence: eigenvector centrality did not converge in 30 iterations")
    store._iris_obj = lambda: native
    monkeypatch.setattr(lazy_kg, "LazyKG", MagicMock(side_effect=AssertionError("walked ^KG")))
    with pytest.raises(PowerIterationFailedConvergence) as err:
        store.execute_eigenvector(30, 1e-6, 0, None)
    assert err.value.max_iter == 30
    args = native.classMethodValue.call_args.args
    assert args[:2] == ("Graph.KG.NKGAccel", "EigenvectorGlobal")
    # the schema, so the server reads the nodes with no edge itself
    assert args[2:] == (30, 1e-6, 0, "Graph_KG")


def test_the_engine_hands_the_loners_to_the_store_and_adds_no_zero_rows():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    store = MagicMock()
    store.capabilities.return_value = {"eigenvector": True}
    store.execute_eigenvector.return_value = IVGResult(columns=["id", "score"], rows=[["a", 0.9], ["solo", 0.1]])
    eng._store = store
    eng._loners = lambda graph="": {"solo": False}
    assert eng.eigenvector_centrality(top_k=5) == [{"id": "a", "score": 0.9}, {"id": "solo", "score": 0.1}]
    call = store.execute_eigenvector.call_args
    assert call.args[:3] == (100, 1e-6, 5)
    assert call.kwargs["loners"] == ["solo"]


def test_defaults_are_networkx_s():
    from iris_vector_graph._engine.algorithms import AlgorithmsMixin
    from iris_vector_graph._validate import EigenvectorInput
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    from iris_vector_graph.schema import GraphSchema

    nx_defaults = inspect.signature(nx.eigenvector_centrality).parameters
    sig = inspect.signature(AlgorithmsMixin.eigenvector_centrality).parameters
    assert sig["max_iter"].default == nx_defaults["max_iter"].default == 100
    assert sig["tol"].default == nx_defaults["tol"].default
    assert EigenvectorInput().max_iter == 100
    sql = translate_to_sql(parse_query("CALL ivg.eigenvector({topK: 5}) YIELD node, score RETURN node, score"), {}).sql
    assert "kg_Eigenvector(100, 1e-06, 5)" in (sql if isinstance(sql, str) else "\n".join(sql))
    ddl = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if ".kg_Eigenvector(" in s)
    assert "maxIter INT DEFAULT 100" in ddl
    # the schema, so EigenvectorGlobal reads the nodes with no edge, which take part (no zero rows added after)
    assert 'EigenvectorGlobal(maxIter, tol, topK, "Graph_KG")' in ddl
    assert "Complete(\"eigenvector\"" not in ddl


def test_the_cypher_error_is_the_same_exception():
    from iris_vector_graph._engine.query import _raise_if_error

    result = IVGResult(columns=[], rows=[], error=(
        "[SQLCODE: <-149>:<SQL Function encountered an error>] ... ERROR:PowerIterationFailedConvergence: "
        "eigenvector centrality did not converge in 7 iterations (tol .000001)"))
    with pytest.raises(PowerIterationFailedConvergence) as err:
        _raise_if_error(result, True)
    assert err.value.max_iter == 7


# ── the oracle's reference and its periodic families ─────────────────────────────────────────────────────────────


def test_the_oracle_reference_is_networkx():
    for g in oracle_graphs.periodic():
        try:
            want = nx.eigenvector_centrality(ref.digraph(g), max_iter=100)
        except nx.PowerIterationFailedConvergence:
            assert ref.eigenvector(g, max_iter=100) == (None, False)
            continue
        assert ref.eigenvector(g, max_iter=100) == (want, True)


def test_the_periodic_families_are_there_and_periodic():
    fams = {g.shapes[-1]: g for g in oracle_graphs.periodic()}
    assert {"cycle2", "cycle3", "cycle4", "cycle3_tails", "bipartite_scc", "two_periods", "walkthrough",
            "walkthrough_lab"} <= set(fams)
    for name, k in (("cycle2", 2), ("cycle3", 3), ("cycle4", 4), ("cycle3_tails", 3), ("walkthrough", 3)):
        G = ref.digraph(fams[name])
        sccs = [c for c in nx.strongly_connected_components(G) if len(c) > 1]
        assert [len(c) for c in sccs] == [k] and _period(G.subgraph(sccs[0])) == k
    two = ref.digraph(fams["two_periods"])
    periods = sorted(_period(two.subgraph(c)) for c in nx.strongly_connected_components(two) if len(c) > 1)
    assert len(set(periods)) == len(periods) >= 2
    bipartite = ref.digraph(fams["bipartite_scc"])
    assert _period(bipartite.subgraph(max(nx.strongly_connected_components(bipartite), key=len))) == 2
    assert fams["walkthrough_lab"].has_named_edges and not fams["walkthrough"].has_named_edges
    assert fams["walkthrough"].isolated() == ["gus"]
    for g in fams.values():
        assert g.seed < 0  # never a generated seed's number
        assert {e.s for e in g.edges} | {e.o for e in g.edges} <= set(g.nodes)


def _period(G) -> int:
    from math import gcd

    root = next(iter(G))
    level = nx.single_source_shortest_path_length(G, root)
    p = 0
    for u, v in G.edges():
        p = gcd(p, level[u] + 1 - level[v])
    return p
