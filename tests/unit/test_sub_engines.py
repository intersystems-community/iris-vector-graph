"""Unit tests for spec-arch-2 — IRISGraphEngine sub-engine namespaces.

All tests use mocks — no container required.
"""
from unittest.mock import MagicMock, patch
import pytest

from iris_vector_graph.engine import (
    _GraphSubEngine, _CypherSubEngine,
    _TemporalSubEngine, _AlgorithmsSubEngine,
)


class TestSubEngineClasses:
    """Sub-engine classes exist and delegate correctly without an engine instance."""

    def _graph(self):
        e = MagicMock()
        return _GraphSubEngine(e), e

    def _cypher(self):
        e = MagicMock()
        return _CypherSubEngine(e), e

    def _temporal(self):
        e = MagicMock()
        return _TemporalSubEngine(e), e

    def _algorithms(self):
        e = MagicMock()
        return _AlgorithmsSubEngine(e), e

    def test_graph_sub_engine_exists(self):
        sg, _ = self._graph()
        assert sg is not None

    def test_cypher_sub_engine_exists(self):
        sg, _ = self._cypher()
        assert sg is not None

    def test_temporal_sub_engine_exists(self):
        sg, _ = self._temporal()
        assert sg is not None

    def test_algorithms_sub_engine_exists(self):
        sg, _ = self._algorithms()
        assert sg is not None

    def test_graph_create_node_delegates(self):
        sg, mock_e = self._graph()
        sg.create_node("n1", labels=["Gene"])
        mock_e.create_node.assert_called_once_with("n1", labels=["Gene"])

    def test_cypher_execute_cypher_delegates(self):
        sg, mock_e = self._cypher()
        sg.execute_cypher("MATCH (n) RETURN n")
        mock_e.execute_cypher.assert_called_once_with("MATCH (n) RETURN n")

    def test_temporal_create_edge_temporal_delegates(self):
        sg, mock_e = self._temporal()
        sg.create_edge_temporal("s", "p", "t", timestamp=1000)
        mock_e.create_edge_temporal.assert_called_once_with("s", "p", "t", timestamp=1000)

    def test_algorithms_degree_centrality_delegates(self):
        sg, mock_e = self._algorithms()
        sg.degree_centrality(direction="out", top_k=10)
        mock_e.degree_centrality.assert_called_once_with(direction="out", top_k=10)


class TestEngineHasSubEngines:
    """IRISGraphEngine.__init__ attaches sub-engines."""

    def _make_engine(self):
        import iris_vector_graph.stores.iris_sql_store as _ss
        from iris_vector_graph.engine import IRISGraphEngine

        mock_store = MagicMock()
        mock_store.capabilities.return_value = {"native_sql": True, "bfs": False}
        mock_store.namespace = "USER"

        orig_cls = _ss.IRISGraphStore
        _ss.IRISGraphStore = MagicMock(return_value=mock_store)
        try:
            conn = MagicMock()
            conn.namespace = "USER"
            with patch.object(IRISGraphEngine, "_probe_nodes_graph_id", return_value=True):
                with patch("iris_vector_graph.engine.set_schema_prefix"):
                    eng = IRISGraphEngine(conn, embedding_dimension=768)
        finally:
            _ss.IRISGraphStore = orig_cls
        return eng

    def test_graph_attribute_exists(self):
        eng = self._make_engine()
        assert isinstance(eng.graph, _GraphSubEngine)

    def test_cypher_attribute_exists(self):
        eng = self._make_engine()
        assert isinstance(eng.cypher, _CypherSubEngine)

    def test_temporal_attribute_exists(self):
        eng = self._make_engine()
        assert isinstance(eng.temporal, _TemporalSubEngine)

    def test_algorithms_attribute_exists(self):
        eng = self._make_engine()
        assert isinstance(eng.algorithms, _AlgorithmsSubEngine)

    def test_top_level_methods_still_callable(self):
        eng = self._make_engine()
        assert callable(eng.create_node)
        assert callable(eng.execute_cypher)
        assert callable(eng.create_edge_temporal)
