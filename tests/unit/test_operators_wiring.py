"""Unit tests for operator wiring fixes (022-wire-up-operators)."""
import json
import inspect
from unittest.mock import MagicMock, patch


class TestKgGraphWalkSubscripts:

    def test_source_code_uses_out_prefix(self):
        from iris_vector_graph.operators import IRISGraphOperators
        source = inspect.getsource(IRISGraphOperators.kg_GRAPH_WALK)
        assert '["out",' in source or "['out'," in source, (
            "kg_GRAPH_WALK must use 'out' prefix in ^KG gref.order() calls"
        )


class TestKgPPRMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_PPR')

    def test_signature_has_seed_entities(self):
        from iris_vector_graph.operators import IRISGraphOperators
        sig = inspect.signature(IRISGraphOperators.kg_PPR)
        assert 'seed_entities' in sig.parameters

    def test_empty_seeds_returns_empty(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        assert ops.kg_PPR(seed_entities=[]) == []

    def test_returns_list_of_tuples(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        fake_json = json.dumps([
            {"id": "NODE_A", "score": 0.5},
            {"id": "NODE_B", "score": 0.3},
        ])
        with patch('iris_vector_graph.operators._call_classmethod', return_value=fake_json):
            result = ops.kg_PPR(seed_entities=["NODE_A"])
        assert result == [("NODE_A", 0.5), ("NODE_B", 0.3)]

    def test_falls_back_when_native_unavailable(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None
        ops = IRISGraphOperators(mock_conn)
        with patch('iris_vector_graph.operators._call_classmethod', side_effect=Exception("no native")):
            result = ops.kg_PPR(seed_entities=["X"])
        assert isinstance(result, list)


class TestKgPPRInProceduresList:

    def test_kg_ppr_present(self):
        from iris_vector_graph.schema import GraphSchema
        combined = " ".join(GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"))
        assert "kg_PPR" in combined

    def test_calls_runjon_not_pagerank_embedded(self):
        from iris_vector_graph.schema import GraphSchema
        combined = " ".join(GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"))
        assert "PageRank" in combined
        assert "PageRankEmbedded" not in combined


class TestKgKNNVECSource:

    def test_queries_kg_node_embeddings_not_optimized(self):
        from iris_vector_graph.operators import IRISGraphOperators
        source = inspect.getsource(IRISGraphOperators._kg_KNN_VEC_hnsw_optimized)
        assert "kg_NodeEmbeddings_optimized" not in source
        assert "kg_NodeEmbeddings" in source

    def test_uses_double_specifier(self):
        from iris_vector_graph.operators import IRISGraphOperators
        source = inspect.getsource(IRISGraphOperators._kg_KNN_VEC_hnsw_optimized)
        assert "DOUBLE" in source
