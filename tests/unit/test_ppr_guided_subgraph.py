"""Unit tests for kg_PPR_GUIDED_SUBGRAPH and PprGuidedSubgraphData."""
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.models import PprGuidedSubgraphData, SubgraphData


class TestPprGuidedSubgraphDataModel:

    def test_defaults_are_empty(self):
        d = PprGuidedSubgraphData()
        assert d.nodes == []
        assert d.edges == []
        assert d.ppr_scores == []
        assert d.seed_ids == []
        assert d.nodes_before_pruning == 0
        assert d.nodes_after_pruning == 0

    def test_roundtrip_fields(self):
        d = PprGuidedSubgraphData(
            nodes=["A", "B"],
            edges=[{"src": "A", "dst": "B", "type": "KNOWS"}],
            ppr_scores=[("A", 0.8), ("B", 0.2)],
            seed_ids=["A"],
            nodes_before_pruning=100,
            nodes_after_pruning=2,
        )
        assert len(d.nodes) == 2
        assert d.nodes_after_pruning <= d.nodes_before_pruning


class TestKgPprGuidedSubgraph:

    def _make_ops(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators.__new__(IRISGraphOperators)
        ops.conn = MagicMock()
        return ops

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, "kg_PPR_GUIDED_SUBGRAPH")

    def test_empty_seeds_returns_empty(self):
        ops = self._make_ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[])
        assert isinstance(result, PprGuidedSubgraphData)
        assert result.nodes == []
        assert result.ppr_scores == []

    def test_nodes_after_le_nodes_before(self):
        ops = self._make_ops()
        ppr_scores = [(f"N{i}", 1.0 / (i + 1)) for i in range(200)]
        sub = SubgraphData(nodes=["N0", "N1", "N2"], edges=[], seed_ids=["N0"])
        with patch.object(ops, "kg_PAGERANK", return_value=ppr_scores), \
             patch.object(ops, "kg_SUBGRAPH", return_value=sub):
            result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["N0"], top_k=50)
        assert result.nodes_after_pruning <= result.nodes_before_pruning

    def test_top_k_enforced(self):
        ops = self._make_ops()
        ppr_scores = [(f"N{i}", 1.0) for i in range(200)]
        sub = SubgraphData(nodes=[f"N{i}" for i in range(10)], edges=[], seed_ids=["N0"])
        with patch.object(ops, "kg_PAGERANK", return_value=ppr_scores), \
             patch.object(ops, "kg_SUBGRAPH", return_value=sub):
            result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["N0"], top_k=30)
        assert result.nodes_after_pruning <= 30

    def test_alpha_converted_to_damping(self):
        ops = self._make_ops()
        sub = SubgraphData(nodes=["A"], edges=[], seed_ids=["A"])
        with patch.object(ops, "kg_PAGERANK", return_value=[("A", 1.0)]) as mock_pr, \
             patch.object(ops, "kg_SUBGRAPH", return_value=sub):
            ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["A"], alpha=0.15)
        _, kwargs = mock_pr.call_args
        assert abs(kwargs["damping"] - 0.85) < 1e-9

    def test_eps_sparsification(self):
        ops = self._make_ops()
        ppr_scores = [("HIGH", 1.0), ("MED", 0.01), ("LOW", 1e-8)]
        sub = SubgraphData(nodes=["HIGH", "MED"], edges=[], seed_ids=["HIGH"])
        with patch.object(ops, "kg_PAGERANK", return_value=ppr_scores), \
             patch.object(ops, "kg_SUBGRAPH", return_value=sub):
            result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["HIGH"], eps=1e-5)
        pruned_ids = [n for n, _ in result.ppr_scores]
        assert "HIGH" in pruned_ids
        assert "MED" in pruned_ids
        assert "LOW" not in pruned_ids

    def test_edge_format(self):
        ops = self._make_ops()
        sub = SubgraphData(
            nodes=["A", "B"],
            edges=[("A", "KNOWS", "B")],
            seed_ids=["A"],
        )
        with patch.object(ops, "kg_PAGERANK", return_value=[("A", 1.0), ("B", 0.5)]), \
             patch.object(ops, "kg_SUBGRAPH", return_value=sub):
            result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["A"])
        assert result.edges == [{"src": "A", "dst": "B", "type": "KNOWS"}]

    def test_ppr_returns_empty_gives_empty_result(self):
        ops = self._make_ops()
        with patch.object(ops, "kg_PAGERANK", return_value=[]):
            result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["NONEXISTENT"])
        assert result.nodes == []
        assert result.seed_ids == ["NONEXISTENT"]
