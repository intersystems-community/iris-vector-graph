import pytest
import warnings
from unittest.mock import MagicMock, patch


def _make_engine_for_embed():
    from iris_vector_graph.engine import IRISGraphEngine

    cursor = MagicMock()
    cursor.description = [("node_id",)]
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = "Graph_KG"
    engine.embedder = None
    engine.embedding_dimension = 768
    engine._connection_params = None
    return engine, cursor


class TestEmbedNodesLabelParam:
    def test_label_param_generates_join_where(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []

        with patch.object(engine, "embedder", None):
            try:
                engine.embed_nodes(label="Gene", model=MagicMock())
            except Exception:
                pass

        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        assert any("rdf_labels" in s or "Gene" in s for s in sqls), \
            f"Expected label filter in SQL, got: {sqls}"

    def test_label_param_accepted_without_error(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(label="Gene", model=mock_model)
        assert isinstance(result, dict)

    def test_label_and_where_mutually_exclusive(self):
        engine, _ = _make_engine_for_embed()
        with pytest.raises((ValueError, TypeError)):
            engine.embed_nodes(label="Gene", where="node_id LIKE 'G%'")


class TestEmbedNodesNodeIdsParam:
    def test_node_ids_param_accepted(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(node_ids=["n1", "n2", "n3"], model=mock_model)
        assert isinstance(result, dict)

    def test_node_ids_generates_in_clause(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []

        try:
            engine.embed_nodes(node_ids=["n1", "n2"], model=MagicMock())
        except Exception:
            pass

        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        assert any("n1" in s or "IN" in s.upper() for s in sqls), \
            f"Expected IN clause for node_ids, got: {sqls}"


class TestEmbedNodesWhereRemoved:
    def test_where_raises_type_error(self):
        engine, _ = _make_engine_for_embed()
        with pytest.raises(TypeError):
            engine.embed_nodes(where="node_id LIKE 'test:%'", model=MagicMock())

    def test_exclude_pattern_replaces_not_like(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(exclude_pattern="test:*", model=mock_model)
        assert isinstance(result, dict)

    def test_missing_only_replaces_not_in_subquery(self):
        engine, cursor = _make_engine_for_embed()
        cursor.fetchall.return_value = []
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(missing_only=True, model=mock_model)
        assert isinstance(result, dict)
