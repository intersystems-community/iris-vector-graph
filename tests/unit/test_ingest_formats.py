"""Unit tests for OBO and NetworkX ingest."""
import pytest
from unittest.mock import MagicMock


class TestLoadNetworkx:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.fetchone.return_value = (0,)
        engine.conn.cursor.return_value = cursor_mock
        return engine

    def test_loads_nodes_and_edges(self):
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("A", type="Gene", name="TP53")
        G.add_node("B", type="Gene", name="MDM2")
        G.add_edge("A", "B", predicate="interacts_with")

        engine = self._make_engine()
        result = engine.load_networkx(G)
        assert result["nodes"] == 2
        assert result["edges"] == 1

    def test_namespace_as_label(self):
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("DOID:123", namespace="disease_ontology", name="flu")
        engine = self._make_engine()
        result = engine.load_networkx(G, label_attr="namespace")
        assert result["nodes"] == 1

    def test_empty_graph(self):
        import networkx as nx
        engine = self._make_engine()
        result = engine.load_networkx(nx.DiGraph())
        assert result == {"nodes": 0, "edges": 0}


class TestLoadObo:

    def test_load_obo_imports_obonet(self):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.fetchone.return_value = (0,)
        engine.conn.cursor.return_value = cursor_mock
        try:
            import obonet
        except ImportError:
            pytest.skip("obonet not installed")
        result = engine.load_obo("http://purl.obolibrary.org/obo/doid.obo")
        assert result["nodes"] > 10000
        assert result["edges"] > 10000
