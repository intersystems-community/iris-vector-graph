"""4.6.0 R270: personalized PageRank is networkx's ``pagerank(G, alpha, personalization, tol)``, dangling nodes included.

Before R270 no IVG route gave networkx's answer on a graph with a node that has no out-edges. The Python fallback
passed such a node's score nowhere; the ObjectScript walk (``Graph.KG.PageRank.Run``) kept ``1 - damping`` of it and
lost the rest. Either way the scores summed to less than 1. networkx hands a dangling node's score to the seeds, in
the personalization's proportions, so every iterate sums to 1.

Nor did the ObjectScript walk read ``tolerance``: it stopped once no score moved by 1e-4. Every route now stops on
networkx's rule, the scores' summed change in one iteration below ``N * tolerance``, with N the nodes holding a score
(networkx's N is every node of the graph, so IVG stops no earlier).

The reference is networkx's pure-Python ``_pagerank_python``: ``nx.pagerank`` needs scipy, which the ``dev`` extra
does not carry, and the two compute the same thing. No IRIS here: the fallback's cursor and the store are fakes; the
ObjectScript walk is checked live in ``tests/e2e/test_460_r270_ppr_networkx_e2e.py``.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

#: a -> b, a -> c twice (two predicates: a multigraph edge of weight 2), b -> c, b -> e, c -> d, d -> a; e and f have
#: no out-edges, so their scores are what networkx hands back to the seeds; g is unreachable from a.
NODES = ["a", "b", "c", "d", "e", "f", "g"]
EDGES = [
    ("a", "KNOWS", "b"),
    ("a", "KNOWS", "c"),
    ("a", "LIKES", "c"),
    ("b", "KNOWS", "c"),
    ("b", "KNOWS", "e"),
    ("c", "KNOWS", "d"),
    ("c", "KNOWS", "f"),
    ("d", "KNOWS", "a"),
    ("g", "KNOWS", "a"),
]
TIGHT = 1e-12
_SELECT = re.compile(r"SELECT\s+(.+?)\s+FROM\s+(\S+)", re.S | re.I)


class _FakeCursor:
    """Answers the fallback's reads (``nodes``, ``rdf_edges``) from lists, with whatever columns the SELECT names."""

    def __init__(self, nodes, edges):
        self.nodes, self.edges, self._rows = nodes, edges, []

    def execute(self, sql, params=None):
        cols, table = _SELECT.search(sql).groups()
        cols = [c.strip() for c in cols.split(",")]
        if "nodes" in table:
            self._rows = [(n,) for n in self.nodes]
        else:
            pick = {"s": 0, "p": 1, "o_id": 2}
            self._rows = [tuple(e[pick[c]] for c in cols) for e in self.edges]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _engine(nodes=NODES, edges=EDGES):
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    conn.cursor.return_value = _FakeCursor(nodes, edges)
    return eng


def _fallback(seeds, *, edges=EDGES, damping=0.85, max_iterations=1000, tolerance=TIGHT, **kw):
    return _engine(edges=edges)._kg_PERSONALIZED_PAGERANK_python_fallback(
        seeds, damping, max_iterations, tolerance, None, kw.pop("bidirectional", False), 1.0, **kw
    )


def _networkx(seeds, *, edges=EDGES, damping=0.85, tolerance=TIGHT, reverse=False):
    g = nx.MultiDiGraph()
    g.add_nodes_from(NODES)
    for s, _p, o in edges:
        g.add_edge(s, o)
        if reverse:
            g.add_edge(o, s)
    return _pagerank_python(g, alpha=damping, personalization={s: 1 for s in seeds}, tol=tolerance, max_iter=10000)


def _assert_networkx(got, want):
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-9), got
    for node, score in want.items():
        assert got.get(node, 0.0) == pytest.approx(score, abs=1e-6), (node, got, want)


class TestFallbackIsNetworkx:
    def test_one_seed(self):
        _assert_networkx(_fallback(["a"]), _networkx(["a"]))

    def test_a_dangling_seed(self):
        """e has no out-edges: networkx sends all of its score back to it, so it holds every bit of the mass."""
        got = _fallback(["e"])
        _assert_networkx(got, _networkx(["e"]))
        # every node, as networkx answers (4.7.0 row 9: the zeros were left out)
        assert got == {n: pytest.approx(1.0 if n == "e" else 0.0) for n in NODES}

    def test_two_seeds(self):
        _assert_networkx(_fallback(["a", "d"]), _networkx(["a", "d"]))

    def test_a_repeated_seed_counts_once(self):
        """networkx's personalization is a dict: a seed named twice is one seed."""
        _assert_networkx(_fallback(["a", "a", "d"]), _networkx(["a", "d"]))

    def test_other_damping(self):
        _assert_networkx(_fallback(["b"], damping=0.5), _networkx(["b"], damping=0.5))

    def test_excluding_a_predicate_is_walking_the_graph_without_it(self):
        """Without KNOWS, a's one edge is LIKES to c, and c, with no edge left, hands its score back to a."""
        kept = [e for e in EDGES if e[1] != "KNOWS"]
        got = _fallback(["a"], exclude_predicates=["KNOWS"])
        _assert_networkx(got, _networkx(["a"], edges=kept))
        assert {n for n, v in got.items() if v > 0} == {"a", "c"}
        assert set(got) == set(NODES)  # the others at 0.0 (4.7.0 row 9)

    def test_exclusion_making_a_node_dangling(self):
        edges = EDGES + [("c", "SEES", "d")]
        kept = [e for e in edges if e[1] != "SEES"]
        _assert_networkx(_fallback(["a"], edges=edges, exclude_predicates=["SEES"]), _networkx(["a"], edges=kept))

    def test_bidirectional_at_weight_one_is_the_graph_with_every_edge_reversed_too(self):
        got = _fallback(["a"], bidirectional=True)
        _assert_networkx(got, _networkx(["a"], reverse=True))


class TestToleranceIsRead:
    def test_a_huge_tolerance_stops_after_one_iteration(self):
        assert _fallback(["a"], tolerance=10.0) == pytest.approx(_fallback(["a"], max_iterations=1))

    def test_a_huge_tolerance_is_not_converged(self):
        one = _fallback(["a"], tolerance=10.0)
        assert one != pytest.approx(_networkx(["a"]), abs=1e-3)

    def test_networkx_rule(self):
        """The fallback stops where networkx's own iteration stops, when both start from the same vector."""
        got = _fallback(["a"], tolerance=1e-3)
        want = _networkx(["a"], tolerance=1e-3)
        for node, score in want.items():
            assert got.get(node, 0.0) == pytest.approx(score, abs=2e-2)


class TestToleranceReachesTheServer:
    def test_engine_passes_tolerance_to_the_store(self):
        eng = _engine()
        store = MagicMock()
        store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["a", 1.0]])
        eng._store = store
        eng._store_capabilities = {"ppr": True}
        eng.kg_PERSONALIZED_PAGERANK(["a"], tolerance=1e-9)
        assert store.execute_ppr.call_args.kwargs["tolerance"] == 1e-9

    def test_store_sends_tolerance_to_runjson(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._native_answer = MagicMock(return_value=None)
        with patch.object(IRISGraphStore, "_call_classmethod", create=False, return_value="[]") as call:
            store._execute_ppr(["a"], 0.85, 20, tolerance=1e-9)
        args = call.call_args.args
        assert args[:2] == ("Graph.KG.PageRank", "RunJson")
        assert float(args[-1]) == 1e-9, args

    def test_the_cache_key_carries_tolerance(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        seen = []
        store._cached_algorithm = lambda name, graph, params, cols, compute: seen.append(params) or IVGResult(
            columns=cols, rows=[]
        )
        store.execute_ppr(["a"], 0.85, 20, tolerance=1e-3)
        store.execute_ppr(["a"], 0.85, 20, tolerance=1e-9)
        assert seen[0]["tolerance"] == 1e-3 and seen[1]["tolerance"] == 1e-9

    def test_fast_path_sends_tolerance(self):
        eng = _engine()
        eng._store_capabilities = {"ppr": False}
        eng.capabilities.objectscript_deployed = True
        eng.capabilities.kg_built = True
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = '[{"id":"a","score":1}]'
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            eng.kg_PERSONALIZED_PAGERANK(["a"], tolerance=1e-9)
        args = iris_obj.classMethodValue.call_args.args
        assert args[:2] == ("Graph.KG.PageRank", "RunJson")
        assert args[-1] == 1e-9, args


class TestDefaultRequestIsUnchanged:
    def test_the_default_tolerance_is_not_sent_to_the_store(self):
        """A store predating ``tolerance`` keeps answering the default request."""
        eng = _engine()
        store = MagicMock()
        store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["a", 1.0]])
        eng._store = store
        eng._store_capabilities = {"ppr": True}
        eng.kg_PERSONALIZED_PAGERANK(["a"])
        assert "tolerance" not in store.execute_ppr.call_args.kwargs

    def test_the_store_default_call_is_the_pre_235_call(self):
        """Classes from before 4.6.0 take six arguments; the default request sends six."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._native_answer = MagicMock(return_value=None)
        with patch.object(IRISGraphStore, "_call_classmethod", return_value="[]") as call:
            store._execute_ppr(["a"], 0.85, 20)
        assert len(call.call_args.args) == 8


class TestPprIsKgPersonalizedPagerank:
    def test_arno_loaded_does_not_change_the_walk(self):
        """``ppr`` answered from ``NKGAccel.PPRNative`` (a 2-hop walk that lost dangling mass) when the library
        reported ``ppr``; it is ``kg_PERSONALIZED_PAGERANK``'s answer whatever is loaded."""
        eng = _engine()
        eng._arno_available = True
        eng._arno_capabilities = {"algorithms": ["ppr"]}
        with patch.object(eng, "_arno_call", side_effect=AssertionError("PPRNative")), patch.object(
            eng, "kg_PERSONALIZED_PAGERANK", return_value={"a": 0.6, "b": 0.4}
        ) as kg:
            out = eng.ppr("a", alpha=0.5, max_iter=7, top_k=3)
        kg.assert_called_once_with(["a"], damping_factor=0.5, max_iterations=7, return_top_k=3)
        assert out == {"scores": [{"id": "a", "score": 0.6}, {"id": "b", "score": 0.4}]}
