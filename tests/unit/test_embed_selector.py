import pytest


class TestEmbedSelector:

    def test_import(self):
        from iris_vector_graph import EmbedSelector
        assert EmbedSelector is not None

    def test_default_values(self):
        from iris_vector_graph import EmbedSelector
        sel = EmbedSelector()
        assert sel.label is None
        assert sel.node_ids is None
        assert sel.predicate is None
        assert sel.exclude_pattern is None
        assert sel.missing_only is False

    def test_label_param(self):
        from iris_vector_graph import EmbedSelector
        sel = EmbedSelector(label="Gene")
        assert sel.label == "Gene"

    def test_node_ids_param(self):
        from iris_vector_graph import EmbedSelector
        sel = EmbedSelector(node_ids=["n1", "n2"])
        assert sel.node_ids == ["n1", "n2"]

    def test_edge_params(self):
        from iris_vector_graph import EmbedSelector
        sel = EmbedSelector(predicate="INTERACTS_WITH", source_label="Gene", target_label="Disease")
        assert sel.predicate == "INTERACTS_WITH"
        assert sel.source_label == "Gene"
        assert sel.target_label == "Disease"

    def test_unsafe_exclude_pattern_rejected(self):
        from iris_vector_graph import EmbedSelector
        with pytest.raises((ValueError, Exception)):
            EmbedSelector(exclude_pattern="; DROP TABLE nodes;")

    def test_sql_injection_string_rejected(self):
        from iris_vector_graph import EmbedSelector
        with pytest.raises((ValueError, Exception)):
            EmbedSelector(exclude_pattern="EXEC xp_cmdshell")

    def test_missing_only_flag(self):
        from iris_vector_graph import EmbedSelector
        sel = EmbedSelector(missing_only=True)
        assert sel.missing_only is True


class TestBuildNodeWhere:

    def test_empty_selector_gives_no_where(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector()
        assert build_node_where(sel) == ""

    def test_label_generates_rdf_labels_join(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector(label="Gene")
        sql = build_node_where(sel)
        assert "rdf_labels" in sql
        assert "Gene" in sql

    def test_node_ids_generates_in_clause(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector(node_ids=["n1", "n2"])
        sql = build_node_where(sel)
        assert "IN" in sql.upper()
        assert "n1" in sql

    def test_empty_node_ids_generates_false(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector(node_ids=[])
        sql = build_node_where(sel)
        assert "1=0" in sql

    def test_exclude_pattern_generates_not_like(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector(exclude_pattern="NCIT:*")
        sql = build_node_where(sel)
        assert "NOT LIKE" in sql.upper()
        assert "%" in sql

    def test_missing_only_generates_not_in_embeddings(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_node_where
        sel = EmbedSelector(missing_only=True)
        sql = build_node_where(sel)
        assert "NOT IN" in sql.upper()
        assert "kg_NodeEmbeddings" in sql or "Embeddings" in sql


class TestBuildEdgeWhere:

    def test_empty_selector_gives_no_where(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector()
        assert build_edge_where(sel) == ""

    def test_predicate_generates_p_filter(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector(predicate="INTERACTS_WITH")
        sql = build_edge_where(sel)
        assert "p =" in sql
        assert "INTERACTS_WITH" in sql

    def test_source_label_generates_join(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector(source_label="Gene")
        sql = build_edge_where(sel)
        assert "rdf_labels" in sql
        assert "Gene" in sql

    def test_target_label_generates_o_id_filter(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector(target_label="Disease")
        sql = build_edge_where(sel)
        assert "o_id" in sql
        assert "Disease" in sql
        assert "rdf_labels" in sql

    def test_exclude_pattern_generates_not_like_on_s_and_o_id(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector(exclude_pattern="NCIT:*")
        sql = build_edge_where(sel)
        assert "NOT LIKE" in sql.upper()
        assert "o_id" in sql
        assert "%" in sql

    def test_missing_only_handled_at_python_level(self):
        from iris_vector_graph import EmbedSelector
        from iris_vector_graph.embed_selector import build_edge_where
        sel = EmbedSelector(missing_only=True)
        sql = build_edge_where(sel)
        assert sql == ""


class TestEmbedNodesMissingOnly:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from unittest.mock import MagicMock

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

    def test_missing_only_accepted(self):
        engine, cursor = self._make_engine()
        from unittest.mock import MagicMock
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(missing_only=True, model=mock_model)
        assert isinstance(result, dict)

    def test_exclude_pattern_accepted(self):
        engine, cursor = self._make_engine()
        from unittest.mock import MagicMock
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(exclude_pattern="NCIT:*", model=mock_model)
        assert isinstance(result, dict)

    def test_embed_edges_predicate_accepted(self):
        engine, cursor = self._make_engine()
        from unittest.mock import MagicMock
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_edges(predicate="INTERACTS_WITH", model=mock_model)
        assert isinstance(result, dict)
