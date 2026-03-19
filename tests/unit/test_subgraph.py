"""Unit tests for kg_SUBGRAPH (023-kg-subgraph). Written FIRST per TDD."""
import json
from unittest.mock import MagicMock, patch

from iris_vector_graph.models import SubgraphData


class TestSubgraphDataModel:

    def test_has_expected_fields(self):
        sg = SubgraphData()
        assert hasattr(sg, 'nodes')
        assert hasattr(sg, 'edges')
        assert hasattr(sg, 'node_properties')
        assert hasattr(sg, 'node_labels')
        assert hasattr(sg, 'node_embeddings')
        assert hasattr(sg, 'seed_ids')

    def test_defaults_are_empty(self):
        sg = SubgraphData()
        assert sg.nodes == []
        assert sg.edges == []
        assert sg.node_properties == {}
        assert sg.node_labels == {}
        assert sg.node_embeddings == {}
        assert sg.seed_ids == []


class TestKgSubgraphMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_SUBGRAPH')

    def test_empty_seeds_returns_empty(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        result = ops.kg_SUBGRAPH(seed_ids=[])
        assert isinstance(result, SubgraphData)
        assert result.nodes == []
        assert result.edges == []

    def test_json_response_parsing(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_json = json.dumps({
            "nodes": ["A", "B", "C"],
            "edges": [
                {"s": "A", "p": "REL", "o": "B"},
                {"s": "B", "p": "REL", "o": "C"},
            ],
            "properties": {"A": {"name": "NodeA"}},
            "labels": {"A": ["Gene"], "B": ["Protein"]},
        })
        ops = IRISGraphOperators(MagicMock())
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_SUBGRAPH(seed_ids=["A"], k_hops=2)
        assert set(result.nodes) == {"A", "B", "C"}
        assert ("A", "REL", "B") in result.edges
        assert ("B", "REL", "C") in result.edges
        assert result.node_properties["A"]["name"] == "NodeA"
        assert result.node_labels["A"] == ["Gene"]
        assert result.seed_ids == ["A"]

    def test_edge_types_passed_to_json(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        with patch('iris_vector_graph.operators._call_classmethod', return_value='{"nodes":[],"edges":[],"properties":{},"labels":{}}') as mock_call:
            ops.kg_SUBGRAPH(seed_ids=["A"], edge_types=["MENTIONS", "CITES"])
        args = mock_call.call_args[0]
        # args: conn, class, method, seedJson, k_hops, edgeTypesJson, maxNodes
        edge_types_arg = args[5]
        parsed = json.loads(edge_types_arg)
        assert "MENTIONS" in parsed
        assert "CITES" in parsed

    def test_embeddings_fetched_via_sql(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("A", "0.1,0.2,0.3")]
        ops = IRISGraphOperators(mock_conn)
        mock_json = json.dumps({
            "nodes": ["A", "B"],
            "edges": [{"s": "A", "p": "R", "o": "B"}],
            "properties": {},
            "labels": {},
        })
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_SUBGRAPH(seed_ids=["A"], include_embeddings=True)
        assert mock_cursor.execute.called
        sql = mock_cursor.execute.call_args[0][0]
        assert "kg_NodeEmbeddings" in sql

    def test_embeddings_not_fetched_by_default(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        ops = IRISGraphOperators(mock_conn)
        mock_json = json.dumps({
            "nodes": ["A"],
            "edges": [],
            "properties": {},
            "labels": {},
        })
        with patch('iris_vector_graph.operators._call_classmethod', return_value=mock_json):
            result = ops.kg_SUBGRAPH(seed_ids=["A"], include_embeddings=False)
        assert result.node_embeddings == {}
        assert not mock_conn.cursor.called
