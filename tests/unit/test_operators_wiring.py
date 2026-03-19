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


class TestKgNeighborsMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_NEIGHBORS')

    def test_mentions_alias_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_MENTIONS')

    def test_empty_source_returns_empty(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        assert ops.kg_NEIGHBORS(source_ids=[]) == []

    def test_invalid_direction_raises(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(MagicMock())
        try:
            ops.kg_NEIGHBORS(source_ids=["A"], direction="sideways")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_out_direction_queries_s_column(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("ENT_A",), ("ENT_B",)]
        ops = IRISGraphOperators(mock_conn)
        result = ops.kg_NEIGHBORS(["PMID:1"], predicate="MENTIONS")
        assert result == ["ENT_A", "ENT_B"]
        sql = mock_cursor.execute.call_args[0][0]
        assert "e.s IN" in sql
        assert "e.o_id" in sql.split("SELECT")[1].split("FROM")[0]

    def test_in_direction_queries_o_id_column(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("PMID:1",)]
        ops = IRISGraphOperators(mock_conn)
        result = ops.kg_NEIGHBORS(["ENT_A"], predicate="CITES", direction="in")
        sql = mock_cursor.execute.call_args[0][0]
        assert "e.o_id IN" in sql

    def test_predicate_none_omits_filter(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("X",)]
        ops = IRISGraphOperators(mock_conn)
        ops.kg_NEIGHBORS(source_ids=["A"], predicate=None)
        sql = mock_cursor.execute.call_args[0][0]
        assert "e.p = ?" not in sql

    def test_chunking_large_lists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        ops = IRISGraphOperators(mock_conn)
        ids = [f"ID:{i}" for i in range(1200)]
        ops.kg_NEIGHBORS(source_ids=ids, chunk_size=500)
        assert mock_cursor.execute.call_count == 3


class TestKgKNNVECNodeId:

    def test_json_array_uses_hnsw_path(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("N1", 0.95)]
        ops = IRISGraphOperators(mock_conn)
        result = ops.kg_KNN_VEC("[0.1, 0.2, 0.3]", k=5)
        sql = mock_cursor.execute.call_args[0][0]
        assert "TO_VECTOR" in sql
        assert "SELECT e.emb" not in sql

    def test_node_id_uses_subquery_path(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("N2", 0.90)]
        ops = IRISGraphOperators(mock_conn)
        result = ops.kg_KNN_VEC("PMID:630", k=5)
        sql = mock_cursor.execute.call_args[0][0]
        assert "SELECT e.emb FROM Graph_KG.kg_NodeEmbeddings e WHERE e.id = ?" in sql
        assert "n.id != ?" in sql

    def test_node_id_excludes_self(self):
        from iris_vector_graph.operators import IRISGraphOperators
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("OTHER", 0.88)]
        ops = IRISGraphOperators(mock_conn)
        ops.kg_KNN_VEC("PMID:630", k=5)
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "PMID:630"
        assert params[1] == "PMID:630"
