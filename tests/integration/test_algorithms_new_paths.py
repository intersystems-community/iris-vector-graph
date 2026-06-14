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

def _ppr_force_objectscript(algo_eng, mock_iris, **kwargs):
    """Force store to error so ObjectScript path (L69-100) runs."""
    err = IVGResult(rows=[], error="forced", metadata=QueryMetadata(elapsed_ms=0))
    caps = algo_eng.capabilities
    with patch.object(algo_eng._store, "execute_ppr", return_value=err):
        with patch.object(algo_eng, "_iris_obj", return_value=mock_iris):
            with patch.object(type(caps), "objectscript_deployed",
                               new_callable=lambda: property(lambda self: True)):
                with patch.object(type(caps), "kg_built",
                                   new_callable=lambda: property(lambda self: True)):
                    return algo_eng.kg_PERSONALIZED_PAGERANK(["algo_0"], max_iterations=5, **kwargs)


def _ppr_err():
    return IVGResult(rows=[], error="fail", metadata=QueryMetadata(elapsed_ms=0))


class TestPPRObjectScriptFastPath:

    def test_ppr_objectscript_returns_json(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([
            {"id": "algo_0", "score": 0.9},
            {"id": "algo_1", "score": 0.5},
            {"id": "algo_2", "score": 0.1},
        ])
        result = _ppr_force_objectscript(algo_eng, mock_iris)
        assert isinstance(result, dict)
        assert "algo_0" in result
        assert result["algo_0"] == pytest.approx(0.9)

    def test_ppr_objectscript_with_return_top_k(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([
            {"id": f"algo_{i}", "score": 1.0 / (i + 1)}
            for i in range(6)
        ])
        result = _ppr_force_objectscript(algo_eng, mock_iris, return_top_k=2)
        assert isinstance(result, dict)
        assert len(result) <= 2

    def test_ppr_objectscript_returns_empty_json(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = json.dumps([])
        result = _ppr_force_objectscript(algo_eng, mock_iris)
        assert isinstance(result, dict)

    def test_ppr_objectscript_raises_python_fallback(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("RunJson not found")
        result = _ppr_force_objectscript(algo_eng, mock_iris)
        assert isinstance(result, dict)

    def test_ppr_objectscript_null_result(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = None
        result = _ppr_force_objectscript(algo_eng, mock_iris)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# PPR Python path — bidirectional (L167)
# ---------------------------------------------------------------------------

class TestPPRBidirectionalPythonPath:

    def test_ppr_bidirectional_python_fallback(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("no ObjectScript")
        result = _ppr_force_objectscript(
            algo_eng, mock_iris,
            bidirectional=True, reverse_edge_weight=0.5
        )
        assert isinstance(result, dict)

    def test_ppr_bidirectional_zero_reverse_weight(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("no ObjectScript")
        result = _ppr_force_objectscript(
            algo_eng, mock_iris,
            bidirectional=True, reverse_edge_weight=0.0
        )
        assert isinstance(result, dict)

    def test_ppr_standard_python_path(self, algo_eng):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = RuntimeError("no ObjectScript")
        result = _ppr_force_objectscript(algo_eng, mock_iris)
        assert isinstance(result, dict)


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
        err_result = _ppr_err()
        with patch.object(algo_eng._store, "execute_wcc", return_value=err_result):
            result = algo_eng.kg_WCC(max_iterations=5)
        assert isinstance(result, dict)

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

    def test_closeness_via_cypher(self, algo_eng):
        result = algo_eng.execute_cypher(
            "CALL ivg.closeness('out', 5) YIELD id, score RETURN id, score LIMIT 5"
        )
        assert result is not None

    def test_eigenvector_via_cypher(self, algo_eng):
        result = algo_eng.execute_cypher(
            "CALL ivg.eigenvector(0.85, 5) YIELD id, score RETURN id, score LIMIT 5"
        )
        assert result is not None

    def test_betweenness_via_cypher(self, algo_eng):
        result = algo_eng.execute_cypher(
            "CALL ivg.betweenness(0, 'out', 3, 5, 32) YIELD id, score RETURN id, score LIMIT 5"
        )
        assert result is not None

    def test_degree_centrality_via_cypher(self, algo_eng):
        result = algo_eng.execute_cypher(
            "CALL ivg.degreeCentrality('out', '', 5) YIELD id, score RETURN id, score LIMIT 5"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# WCC / CDLP store capabilities = False → ObjectScript direct
# ---------------------------------------------------------------------------

class TestWCCCDLPStoreCapabilitiesFalse:

    def test_wcc_store_disabled_objectscript(self, algo_eng):
        orig = algo_eng._store_capabilities.get("wcc")
        try:
            algo_eng._store_capabilities["wcc"] = False
            result = algo_eng.kg_WCC(max_iterations=5)
            assert isinstance(result, dict)
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
