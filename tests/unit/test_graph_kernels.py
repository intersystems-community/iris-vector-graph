import json
from unittest.mock import MagicMock, patch


def _make_ops():
    from iris_vector_graph.operators import IRISGraphOperators
    from iris_vector_graph.engine import IRISGraphEngine
    ops = IRISGraphOperators.__new__(IRISGraphOperators)
    ops.conn = MagicMock()
    ops._engine = MagicMock(spec=IRISGraphEngine)
    return ops


class TestKgPagerank:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_PAGERANK')

    def test_returns_list_of_tuples(self):
        ops = _make_ops()
        ops._engine.kg_PAGERANK.return_value = [("HUB", 0.4), ("S1", 0.15)]
        result = ops.kg_PAGERANK(damping=0.85)
        assert isinstance(result, list)
        assert result[0] == ("HUB", 0.4)
        assert result[1] == ("S1", 0.15)


class TestKgWCC:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_WCC')

    def test_returns_dict(self):
        ops = _make_ops()
        ops._engine.kg_WCC.return_value = {"A": "A", "B": "A", "C": "C"}
        result = ops.kg_WCC()
        assert isinstance(result, dict)
        assert result["A"] == "A"
        assert result["C"] == "C"


class TestKgCDLP:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_CDLP')

    def test_returns_dict(self):
        ops = _make_ops()
        ops._engine.kg_CDLP.return_value = {"X": "comm1", "Y": "comm1", "Z": "comm2"}
        result = ops.kg_CDLP()
        assert isinstance(result, dict)
        assert result["X"] == "comm1"
