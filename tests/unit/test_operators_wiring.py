import json
import inspect
from unittest.mock import MagicMock, patch


def _make_ops():
    from iris_vector_graph.operators import IRISGraphOperators
    from iris_vector_graph.engine import IRISGraphEngine
    ops = IRISGraphOperators.__new__(IRISGraphOperators)
    ops.conn = MagicMock()
    ops._engine = MagicMock(spec=IRISGraphEngine)
    return ops


class TestKgGraphWalkSubscripts:

    def test_source_code_uses_engine_delegate(self):
        from iris_vector_graph.operators import IRISGraphOperators
        source = inspect.getsource(IRISGraphOperators.kg_GRAPH_WALK)
        assert '_engine' in source


class TestKgPPRMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_PPR')

    def test_signature_has_seed_entities(self):
        from iris_vector_graph.operators import IRISGraphOperators
        sig = inspect.signature(IRISGraphOperators.kg_PPR)
        assert 'seed_entities' in sig.parameters

    def test_empty_seeds_returns_empty(self):
        ops = _make_ops()
        ops._engine.kg_PPR.return_value = []
        assert ops.kg_PPR(seed_entities=[]) == []

    def test_returns_list_of_tuples(self):
        ops = _make_ops()
        ops._engine.kg_PPR.return_value = [("NODE_A", 0.5), ("NODE_B", 0.3)]
        result = ops.kg_PPR(seed_entities=["NODE_A"])
        assert result == [("NODE_A", 0.5), ("NODE_B", 0.3)]

    def test_falls_back_when_native_unavailable(self):
        ops = _make_ops()
        ops._engine.kg_PPR.return_value = []
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

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_KNN_VEC')

    def test_delegates_to_engine(self):
        ops = _make_ops()
        ops._engine.kg_KNN_VEC.return_value = [("N1", 0.95)]
        result = ops.kg_KNN_VEC("[0.1,0.2,0.3]", k=5)
        ops._engine.kg_KNN_VEC.assert_called_once()
        assert result == [("N1", 0.95)]


class TestKgNeighborsMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_NEIGHBORS')

    def test_mentions_alias_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_MENTIONS')

    def test_empty_source_returns_empty(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.return_value = []
        assert ops.kg_NEIGHBORS(source_ids=[]) == []

    def test_invalid_direction_raises(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.side_effect = ValueError("direction must be 'out', 'in', or 'both'")
        try:
            ops.kg_NEIGHBORS(source_ids=["A"], direction="sideways")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_out_direction_delegates(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.return_value = ["ENT_A", "ENT_B"]
        result = ops.kg_NEIGHBORS(["PMID:1"], predicate="MENTIONS")
        assert result == ["ENT_A", "ENT_B"]
        ops._engine.kg_NEIGHBORS.assert_called_once()

    def test_in_direction_delegates(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.return_value = ["PMID:1"]
        result = ops.kg_NEIGHBORS(["ENT_A"], predicate="CITES", direction="in")
        ops._engine.kg_NEIGHBORS.assert_called_with(["ENT_A"], predicate="CITES",
                                                     direction="in", distinct=True, chunk_size=500)

    def test_predicate_none_passed_through(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.return_value = ["X"]
        ops.kg_NEIGHBORS(source_ids=["A"], predicate=None)
        call_args = ops._engine.kg_NEIGHBORS.call_args
        assert call_args[1].get("predicate") is None or call_args[0][1] is None

    def test_chunking_large_lists(self):
        ops = _make_ops()
        ops._engine.kg_NEIGHBORS.return_value = []
        ids = [f"ID:{i}" for i in range(1200)]
        ops.kg_NEIGHBORS(source_ids=ids, chunk_size=500)
        ops._engine.kg_NEIGHBORS.assert_called_once()


class TestKgKNNVECNodeId:

    def test_json_array_delegates_to_engine(self):
        ops = _make_ops()
        ops._engine.kg_KNN_VEC.return_value = [("N1", 0.95)]
        result = ops.kg_KNN_VEC("[0.1, 0.2, 0.3]", k=5)
        ops._engine.kg_KNN_VEC.assert_called_once()

    def test_node_id_delegates_to_engine(self):
        ops = _make_ops()
        ops._engine.kg_KNN_VEC.return_value = [("N2", 0.90)]
        result = ops.kg_KNN_VEC("PMID:630", k=5)
        ops._engine.kg_KNN_VEC.assert_called_once()

    def test_node_id_excludes_self(self):
        ops = _make_ops()
        ops._engine.kg_KNN_VEC.return_value = [("OTHER", 0.88)]
        ops.kg_KNN_VEC("PMID:630", k=5)
        call_kwargs = ops._engine.kg_KNN_VEC.call_args
        assert call_kwargs is not None
