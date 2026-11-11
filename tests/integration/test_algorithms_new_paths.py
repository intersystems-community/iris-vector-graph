"""
Integration tests for uncovered paths in _engine/algorithms.py.

Targets:
  L69-100  — kg_PERSONALIZED_PAGERANK ObjectScript fast path (RunJson)
  L88-93   — return_top_k filtering after RunJson
  L167     — PPR bidirectional reverse_edge_weight > 0 Python path
  L228-230 — WCC / CDLP store path
  L279     — CDLP max_iterations path
  L297-301 — random_walk arno returns dict with "error" key
  L355-357 — kg_WCC fallback _call_classmethod path
  L363-365 — kg_CDLP fallback _call_classmethod path
  L384-416 — kg_SUBGRAPH include_embeddings=True
  L431-432 — kg_PPR_GUIDED_SUBGRAPH empty seed
  L489,491 — kg_NEIGHBORS direction="in" and "out" branches
  L547-548 — kg_SUBGRAPH store error path
  L627-628 — kg_NEIGHBORS store fallback
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.cypher.translator import QueryMetadata


@pytest.fixture
def algo_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(6):
        eng.create_node(f"algo_{i}", labels=["ALGO"], properties={"val": i})
    for i in range(5):
        eng.create_edge(f"algo_{i}", "ALGO_REL", f"algo_{i + 1}", qualifiers={"w": str(float(i))})
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# kg_PERSONALIZED_PAGERANK — ObjectScript fast path (L69-100) and return_top_k
# ---------------------------------------------------------------------------

CHAIN = [(f"algo_{i}", f"algo_{i + 1}") for i in range(5)]


def _oracle(*, both_ways=False):
    """networkx's personalized PageRank of the fixture's chain algo_0 -> ... -> algo_5, seeded at algo_0, alpha 0.85;
    with ``both_ways`` every edge also runs back at weight 1. Computed apart from IVG."""
    import networkx as nx

    g = nx.DiGraph()
    g.add_nodes_from(f"algo_{i}" for i in range(6))
    g.add_edges_from(CHAIN)
    if both_ways:
        g.add_edges_from((b, a) for a, b in CHAIN)
    return nx.pagerank(g, alpha=0.85, personalization={"algo_0": 1.0}, tol=1e-12, max_iter=1000)


def _assert_scores(got, want):
    assert set(got) == set(want), got
    for k, v in want.items():
        assert got[k] == pytest.approx(v, abs=1e-4), (k, got[k], v)


def _ppr_via_runjson(algo_eng, mock_iris, **kwargs):
    """The fast path, Graph.KG.PageRank.RunJson, as the engine takes it: the store has no PPR (its capability), the
    classes are deployed and ^KG is built. The store's own failure raises since 4.6.0 R127 (44898ff1) and no longer
    reaches this path, so the store is not made to fail."""
    caps = algo_eng.capabilities
    with patch.dict(algo_eng._store_capabilities, {"ppr": False}):
        with patch.object(algo_eng, "_iris_obj", return_value=mock_iris):
            with patch.object(type(caps), "objectscript_deployed",
                               new_callable=lambda: property(lambda self: True)):
                with patch.object(type(caps), "kg_built",
                                   new_callable=lambda: property(lambda self: True)):
                    return algo_eng.kg_PERSONALIZED_PAGERANK(["algo_0"], max_iterations=5, **kwargs)


def _ppr_in_python(algo_eng, **kwargs):
    """The Python fallback, as the engine takes it: no store PPR and no ObjectScript classes to call."""
    caps = algo_eng.capabilities
    with patch.dict(algo_eng._store_capabilities, {"ppr": False}):
        with patch.object(type(caps), "objectscript_deployed",
                           new_callable=lambda: property(lambda self: False)):
            return algo_eng.kg_PERSONALIZED_PAGERANK(["algo_0"], max_iterations=1000, **kwargs)


def _ppr_err():
    return IVGResult(rows=[], error="fail", metadata=QueryMetadata(elapsed_ms=0))


class TestPPRObjectScriptFastPath:

    def test_ppr_objectscript_returns_json(self, algo_eng):
        """RunJson's scores, highest first, and 0.0 for each node of the graph it did not score (4.7.0 row 9)."""
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([
            {"id": "algo_0", "score": 0.9},
            {"id": "algo_1", "score": 0.5},
            {"id": "algo_2", "score": 0.1},
        ])
        result = _ppr_via_runjson(algo_eng, mock_iris)
        assert result == {"algo_0": 0.9, "algo_1": 0.5, "algo_2": 0.1, "algo_3": 0.0, "algo_4": 0.0, "algo_5": 0.0}
        assert list(result)[:3] == ["algo_0", "algo_1", "algo_2"]
        assert mock_iris.classMethodValue.call_args.args[:3] == ("Graph.KG.PageRank", "RunJson", '["algo_0"]')

    def test_ppr_objectscript_with_return_top_k(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([
            {"id": f"algo_{i}", "score": 1.0 / (i + 1)}
            for i in range(6)
        ])
        result = _ppr_via_runjson(algo_eng, mock_iris, return_top_k=2)
        assert result == {"algo_0": 1.0, "algo_1": 0.5}

    def test_ppr_objectscript_returns_empty_json(self, algo_eng):
        """RunJson scored nothing: no seed is a node of the walk, and nothing scores (4.7.0 row 8b)."""
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([])
        assert _ppr_via_runjson(algo_eng, mock_iris) == {}

    def test_ppr_objectscript_failure_raises(self, algo_eng):
        """A RunJson that fails, other than by not being there, raises (4.6.0 R276); it was answered from the Python
        fallback, which hid the failure. No fallback runs for it."""
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("RunJson not found")
        with pytest.raises(RuntimeError, match=r"Graph.KG.PageRank.RunJson failed: RunJson not found"):
            _ppr_via_runjson(algo_eng, mock_iris)

    def test_ppr_runjson_not_compiled_answers_from_python(self, algo_eng):
        """The one RunJson failure that still falls back: the method is not there to call (4.6.0 R276)."""
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("<METHOD DOES NOT EXIST> *RunJson,Graph.KG.PageRank")
        caps = algo_eng.capabilities
        with patch.dict(algo_eng._store_capabilities, {"ppr": False}):
            with patch.object(algo_eng, "_iris_obj", return_value=mock_iris):
                with patch.object(type(caps), "objectscript_deployed",
                                   new_callable=lambda: property(lambda self: True)):
                    with patch.object(type(caps), "kg_built",
                                       new_callable=lambda: property(lambda self: True)):
                        result = algo_eng.kg_PERSONALIZED_PAGERANK(["algo_0"], max_iterations=1000)
        _assert_scores(result, _oracle())

    def test_ppr_objectscript_null_result_answers_from_python(self, algo_eng):
        """RunJson answering nothing (null) leaves the answer to the Python fallback: networkx's scores."""
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = None
        caps = algo_eng.capabilities
        with patch.dict(algo_eng._store_capabilities, {"ppr": False}):
            with patch.object(algo_eng, "_iris_obj", return_value=mock_iris):
                with patch.object(type(caps), "objectscript_deployed",
                                   new_callable=lambda: property(lambda self: True)):
                    with patch.object(type(caps), "kg_built",
                                       new_callable=lambda: property(lambda self: True)):
                        result = algo_eng.kg_PERSONALIZED_PAGERANK(["algo_0"], max_iterations=1000)
        _assert_scores(result, _oracle())


# ---------------------------------------------------------------------------
# PPR Python path — bidirectional (L167)
# ---------------------------------------------------------------------------

class TestPPRBidirectionalPythonPath:

    def test_ppr_bidirectional_python_fallback(self, algo_eng):
        """Each edge also walked back at weight 1: the chain read both ways (networkx)."""
        _assert_scores(_ppr_in_python(algo_eng, bidirectional=True, reverse_edge_weight=1.0), _oracle(both_ways=True))

    def test_ppr_bidirectional_zero_reverse_weight(self, algo_eng):
        """A reverse weight of 0 adds no reverse edge: the forward chain's scores."""
        _assert_scores(_ppr_in_python(algo_eng, bidirectional=True, reverse_edge_weight=0.0), _oracle())

    def test_ppr_standard_python_path(self, algo_eng):
        _assert_scores(_ppr_in_python(algo_eng), _oracle())


# ---------------------------------------------------------------------------
# kg_WCC / kg_CDLP — L228-230, L355-357, L363-365
# ---------------------------------------------------------------------------

class TestWCCCDLPPaths:

    def test_wcc_via_store(self, algo_eng):
        result = algo_eng.kg_WCC(max_iterations=10)
        assert isinstance(result, dict)

    def test_cdlp_via_store(self, algo_eng):
        result = algo_eng.kg_CDLP(max_iterations=5)
        assert isinstance(result, dict)

    def test_wcc_store_fallback_objectscript(self, algo_eng):
        """The store failing, Graph.KG.Algorithms.WCCJson answers: the chain is one component. Its six nodes need
        more than 5 rounds for the labels to settle (c82e230c, 4.6.0: WCC loops until they do)."""
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_wcc", return_value=err_result):
            result = algo_eng.kg_WCC(max_iterations=20)
        assert set(result) == {f"algo_{i}" for i in range(6)}
        assert len(set(result.values())) == 1, result

    def test_wcc_store_fallback_refuses_an_unsettled_answer(self, algo_eng):
        """At 5 rounds the chain's labels are still changing: WCC raises rather than answer them (c82e230c)."""
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_wcc", return_value=err_result):
            with pytest.raises(RuntimeError, match=r"WCC did not converge in 5 rounds"):
                algo_eng.kg_WCC(max_iterations=5)

    def test_cdlp_store_fallback_objectscript(self, algo_eng):
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_cdlp", return_value=err_result):
            result = algo_eng.kg_CDLP(max_iterations=10)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# random_walk — L297-301 (arno dict "error" key)
# ---------------------------------------------------------------------------

class TestRandomWalkArnoPaths:

    def test_random_walk_basic(self, algo_eng):
        result = algo_eng.random_walk("algo_0", length=5, num_walks=3)
        assert isinstance(result, list)

    def test_random_walk_arno_dict_error(self, algo_eng):
        with patch.object(algo_eng, "_detect_arno", return_value=True):
            algo_eng._arno_capabilities = {"algorithms": ["random_walk"]}
            with patch.object(algo_eng, "_arno_call",
                               return_value=json.dumps({"error": "walk failed"})):
                result = algo_eng.random_walk("algo_0", length=5, num_walks=2)
        assert result == []

    def test_random_walk_arno_exception(self, algo_eng):
        with patch.object(algo_eng, "_detect_arno", return_value=True):
            algo_eng._arno_capabilities = {"algorithms": ["random_walk"]}
            with patch.object(algo_eng, "_arno_call", side_effect=RuntimeError("arno fail")):
                result = algo_eng.random_walk("algo_0", length=5, num_walks=2)
        assert result == []

    def test_random_walk_arno_returns_list(self, algo_eng):
        with patch.object(algo_eng, "_detect_arno", return_value=True):
            algo_eng._arno_capabilities = {"algorithms": ["random_walk"]}
            with patch.object(algo_eng, "_arno_call",
                               return_value=json.dumps([["algo_0", "algo_1"], ["algo_0", "algo_2"]])):
                result = algo_eng.random_walk("algo_0", length=5, num_walks=2)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# kg_SUBGRAPH — L384-416 (include_embeddings), store error path
# ---------------------------------------------------------------------------

class TestKGSubgraphPaths:

    def test_kg_subgraph_via_store(self, algo_eng):
        result = algo_eng.kg_SUBGRAPH(["algo_0"], k_hops=1)
        assert result is not None

    def test_kg_subgraph_empty_seeds(self, algo_eng):
        from iris_vector_graph.models import SubgraphData
        result = algo_eng.kg_SUBGRAPH([], k_hops=2)
        assert isinstance(result, SubgraphData)

    def test_kg_subgraph_with_edge_types(self, algo_eng):
        result = algo_eng.kg_SUBGRAPH(["algo_0"], k_hops=1, edge_types=["ALGO_REL"])
        assert result is not None

    def test_kg_subgraph_include_embeddings_store_fallback(self, algo_eng):
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_subgraph", return_value=err_result):
            result = algo_eng.kg_SUBGRAPH(["algo_0"], k_hops=1, include_embeddings=True)
        assert result is not None

    def test_kg_subgraph_include_embeddings_store_ok(self, algo_eng):
        result = algo_eng.kg_SUBGRAPH(["algo_0"], k_hops=2, include_embeddings=True)
        assert result is not None

    def test_kg_subgraph_store_error_falls_to_objectscript(self, algo_eng):
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_subgraph", return_value=err_result):
            result = algo_eng.kg_SUBGRAPH(["algo_0"], k_hops=1)
        assert result is not None


# ---------------------------------------------------------------------------
# kg_PPR_GUIDED_SUBGRAPH — L431-432 (empty seeds)
# ---------------------------------------------------------------------------

class TestPPRGuidedSubgraph:

    def test_ppr_guided_subgraph_basic(self, algo_eng):
        result = algo_eng.kg_PPR_GUIDED_SUBGRAPH(["algo_0"], ppr_top_k=5, k_hops=1)
        assert result is not None

    def test_ppr_guided_subgraph_empty_seeds(self, algo_eng):
        result = algo_eng.kg_PPR_GUIDED_SUBGRAPH([], ppr_top_k=5, k_hops=1)
        assert result is not None
        assert not result.nodes


# ---------------------------------------------------------------------------
# kg_NEIGHBORS — L489 (in), L491 (out), both directions
# ---------------------------------------------------------------------------

class TestKGNeighborsPaths:

    def test_kg_neighbors_out(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_0"], direction="out")
        assert isinstance(result, list)

    def test_kg_neighbors_in(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_5"], direction="in")
        assert isinstance(result, list)

    def test_kg_neighbors_both(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_2"], direction="both")
        assert isinstance(result, list)

    def test_kg_neighbors_empty_seeds(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS([])
        assert result == []

    def test_kg_neighbors_invalid_direction(self, algo_eng):
        with pytest.raises(ValueError):
            algo_eng.kg_NEIGHBORS(["algo_0"], direction="sideways")

    def test_kg_neighbors_with_predicate(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_0"], predicate="ALGO_REL", direction="out")
        assert isinstance(result, list)

    def test_kg_neighbors_multi_source(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_0", "algo_1"], direction="out")
        assert isinstance(result, list)

    def test_kg_neighbors_distinct_false(self, algo_eng):
        result = algo_eng.kg_NEIGHBORS(["algo_0"], direction="out", distinct=False)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Additional algorithm paths via store Cypher wrappers
# ---------------------------------------------------------------------------

class TestAlgorithmCypherWrappers:

    def test_ppr_via_cypher(self, algo_eng):
        result = algo_eng.execute_cypher(
            "CALL ivg.ppr($seeds, 0.85, 5) YIELD id, score RETURN id, score LIMIT 5",
            parameters={"seeds": ["algo_0"]}
        )
        assert result is not None

    # The centrality procedures take one options map since 4.6.5 (c2f2f191) and yield `node` and `score`. Known
    # answers from networkx on the fixture's chain algo_0 -> ... -> algo_5.

    @staticmethod
    def _rows(algo_eng, call):
        r = algo_eng.execute_cypher(f"CALL {call} YIELD node, score RETURN node.id AS id, score ORDER BY id")
        assert r.error is None, r.error
        return {row[0]: row[1] for row in r.rows}

    @staticmethod
    def _chain():
        import networkx as nx

        return nx.DiGraph(CHAIN)

    def test_closeness_via_cypher(self, algo_eng):
        """Harmonic closeness over out-paths (the default formula): networkx's harmonic_centrality of the reversed
        chain, whose distances are the out-distances."""
        import networkx as nx

        got = self._rows(algo_eng, "ivg.closeness({direction: 'out', topK: 6})")
        want = nx.harmonic_centrality(self._chain().reverse())
        assert set(got) == set(want)
        for k, v in want.items():
            assert got[k] == pytest.approx(v), k

    def test_eigenvector_via_cypher(self, algo_eng):
        """A chain has no dominant eigenvector: IVG refuses, as networkx's eigenvector_centrality does."""
        import networkx as nx

        with pytest.raises(nx.PowerIterationFailedConvergence):
            nx.eigenvector_centrality(self._chain(), max_iter=100)
        r = algo_eng.execute_cypher("CALL ivg.eigenvector({maxIter: 100, topK: 6}) YIELD node, score RETURN node, score")
        assert r.error is not None and "did not converge in 100 iterations" in r.error, r.error

    def test_betweenness_via_cypher(self, algo_eng):
        """Exact (sampleSize 0), unnormalised: networkx's betweenness_centrality(normalized=False); a node on no
        shortest path has no row."""
        import networkx as nx

        got = self._rows(algo_eng, "ivg.betweenness({sampleSize: 0, direction: 'out', topK: 6})")
        want = {k: v for k, v in nx.betweenness_centrality(self._chain(), normalized=False).items() if v}
        assert got == pytest.approx(want)

    def test_degree_centrality_via_cypher(self, algo_eng):
        """Out-degree over n - 1: networkx's out_degree_centrality."""
        import networkx as nx

        got = self._rows(algo_eng, "ivg.degreeCentrality({direction: 'out', topK: 6})")
        assert got == pytest.approx(nx.out_degree_centrality(self._chain()))


# ---------------------------------------------------------------------------
# WCC / CDLP store capabilities = False → ObjectScript direct
# ---------------------------------------------------------------------------

class TestWCCCDLPStoreCapabilitiesFalse:

    def test_wcc_store_disabled_objectscript(self, algo_eng):
        orig = algo_eng._store_capabilities.get("wcc")
        try:
            algo_eng._store_capabilities["wcc"] = False
            result = algo_eng.kg_WCC(max_iterations=20)
            assert set(result) == {f"algo_{i}" for i in range(6)}
            assert len(set(result.values())) == 1, result
            with pytest.raises(RuntimeError, match=r"WCC did not converge in 5 rounds"):
                algo_eng.kg_WCC(max_iterations=5)
        finally:
            if orig is None:
                algo_eng._store_capabilities.pop("wcc", None)
            else:
                algo_eng._store_capabilities["wcc"] = orig

    def test_cdlp_store_disabled_objectscript(self, algo_eng):
        orig = algo_eng._store_capabilities.get("cdlp")
        try:
            algo_eng._store_capabilities["cdlp"] = False
            result = algo_eng.kg_CDLP(max_iterations=5)
            assert isinstance(result, dict)
        finally:
            if orig is None:
                algo_eng._store_capabilities.pop("cdlp", None)
            else:
                algo_eng._store_capabilities["cdlp"] = orig
