"""Unit tests for graph analytics kernels (024-graph-kernels). TDD — written FIRST."""
import json
from unittest.mock import MagicMock, patch


class TestKgPagerank:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_PAGERANK')

    def test_returns_list_of_tuples(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_json = json.dumps([
            {"id": "HUB", "score": 0.4},
            {"id": "S1", "score": 0.15},
        ])
        ops = IRISGraphOperators(MagicMock())
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_PAGERANK(damping=0.85)
        assert isinstance(result, list)
        assert result[0] == ("HUB", 0.4)
        assert result[1] == ("S1", 0.15)


class TestKgWCC:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_WCC')

    def test_returns_dict(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_json = json.dumps({"A": "A", "B": "A", "C": "C"})
        ops = IRISGraphOperators(MagicMock())
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_WCC()
        assert isinstance(result, dict)
        assert result["A"] == "A"
        assert result["B"] == "A"
        assert result["C"] == "C"


class TestKgCDLP:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_CDLP')

    def test_returns_dict(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_json = json.dumps({"X": "comm1", "Y": "comm1", "Z": "comm2"})
        ops = IRISGraphOperators(MagicMock())
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_CDLP()
        assert isinstance(result, dict)
        assert result["X"] == "comm1"
