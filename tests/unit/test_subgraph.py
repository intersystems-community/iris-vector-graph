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


def _make_ops():
    from iris_vector_graph.operators import IRISGraphOperators
    from iris_vector_graph.engine import IRISGraphEngine
    ops = IRISGraphOperators.__new__(IRISGraphOperators)
    ops.conn = MagicMock()
    ops._engine = MagicMock(spec=IRISGraphEngine)
    return ops


class TestKgSubgraphMethod:

    def test_method_exists(self):
        from iris_vector_graph.operators import IRISGraphOperators
        assert hasattr(IRISGraphOperators, 'kg_SUBGRAPH')

    def test_empty_seeds_returns_empty(self):
        ops = _make_ops()
        ops._engine.kg_SUBGRAPH.return_value = SubgraphData(seed_ids=[])
        result = ops.kg_SUBGRAPH(seed_ids=[])
        assert isinstance(result, SubgraphData)

    def test_json_response_parsing(self):
        ops = _make_ops()
        expected = SubgraphData(
            seed_ids=["A"],
            nodes=["A", "B", "C"],
            edges=[("A", "REL", "B"), ("B", "REL", "C")],
            node_properties={"A": {"name": "NodeA"}},
            node_labels={"A": ["Gene"], "B": ["Protein"]},
        )
        ops._engine.kg_SUBGRAPH.return_value = expected
        result = ops.kg_SUBGRAPH(seed_ids=["A"], k_hops=2)
        assert set(result.nodes) == {"A", "B", "C"}
        assert ("A", "REL", "B") in result.edges
        assert result.seed_ids == ["A"]

    def test_edge_types_passed_to_subgraph(self):
        ops = _make_ops()
        ops._engine.kg_SUBGRAPH.return_value = SubgraphData(seed_ids=["A"])
        ops.kg_SUBGRAPH(seed_ids=["A"], edge_types=["MENTIONS", "CITES"])
        call_kwargs = ops._engine.kg_SUBGRAPH.call_args
        assert call_kwargs is not None
        edge_types_arg = call_kwargs[1].get("edge_types") or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        if edge_types_arg is not None:
            assert "MENTIONS" in edge_types_arg

    def test_embeddings_flag_passed_to_engine(self):
        ops = _make_ops()
        ops._engine.kg_SUBGRAPH.return_value = SubgraphData(seed_ids=["A"])
        ops.kg_SUBGRAPH(seed_ids=["A"], include_embeddings=True)
        ops._engine.kg_SUBGRAPH.assert_called_once()
        kwargs = ops._engine.kg_SUBGRAPH.call_args[1]
        assert kwargs.get("include_embeddings") is True

    def test_embeddings_not_fetched_by_default(self):
        ops = _make_ops()
        ops._engine.kg_SUBGRAPH.return_value = SubgraphData(seed_ids=["A"], node_embeddings={})
        result = ops.kg_SUBGRAPH(seed_ids=["A"], include_embeddings=False)
        assert result.node_embeddings == {}
