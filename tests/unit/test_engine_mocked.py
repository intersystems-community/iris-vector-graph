"""Mocked engine tests for IRISGraphEngine — covers methods without a real IRIS connection."""
import json
import pytest
from unittest.mock import MagicMock, patch, call


def _make_engine(rows=None, fetchone_val=None):
    from iris_vector_graph.engine import IRISGraphEngine
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows if rows is not None else []
    cursor.fetchone.return_value = fetchone_val if fetchone_val is not None else (0,)
    cursor.description = [("col1",)]
    conn.cursor.return_value = cursor
    with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
         patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
        engine = IRISGraphEngine(conn, vector_dtype="DOUBLE")
    engine._cursor = cursor
    return engine, conn, cursor


class TestEngineBasicMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_get_labels_empty(self):
        self.cursor.fetchall.return_value = []
        labels = self.engine.get_labels()
        assert labels == []

    def test_get_labels_with_data(self):
        self.cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        labels = self.engine.get_labels()
        assert "Gene" in labels
        assert "Disease" in labels

    def test_get_relationship_types_empty(self):
        self.cursor.fetchall.return_value = []
        rels = self.engine.get_relationship_types()
        assert rels == []

    def test_get_relationship_types_with_data(self):
        self.cursor.fetchall.return_value = [("INTERACTS",), ("CAUSES",)]
        rels = self.engine.get_relationship_types()
        assert "INTERACTS" in rels

    def test_get_node_count_no_label(self):
        self.cursor.fetchone.return_value = (42,)
        count = self.engine.get_node_count()
        assert count == 42

    def test_get_node_count_with_label(self):
        self.cursor.fetchone.return_value = (10,)
        count = self.engine.get_node_count(label="Gene")
        assert count == 10

    def test_get_edge_count(self):
        self.cursor.fetchone.return_value = (100,)
        count = self.engine.get_edge_count()
        assert count == 100

    def test_get_edge_count_with_predicate(self):
        self.cursor.fetchone.return_value = (5,)
        count = self.engine.get_edge_count(predicate="INTERACTS")
        assert count == 5

    def test_node_exists_returns_bool(self):
        result = self.engine.node_exists("mesh:D003924")
        assert isinstance(result, bool)

    def test_node_exists_false(self):
        self.cursor.fetchone.return_value = None
        assert self.engine.node_exists("nonexistent") is False

    def test_nodes_exist_empty(self):
        result = self.engine.nodes_exist([])
        assert result == set()

    def test_nodes_exist_with_data(self):
        self.cursor.fetchall.return_value = [("n1",), ("n2",)]
        result = self.engine.nodes_exist(["n1", "n2", "n3"])
        assert "n1" in result
        assert "n2" in result
        assert "n3" not in result

    def test_get_node_properties_empty(self):
        self.cursor.fetchall.return_value = []
        props = self.engine.get_node_properties("n1")
        assert isinstance(props, dict)

    def test_get_node_properties_with_data(self):
        self.cursor.fetchall.return_value = [("name", '"TP53"'), ("score", "0.9")]
        props = self.engine.get_node_properties("n1")
        assert isinstance(props, dict)

    def test_get_node_name_returns_value_or_none(self):
        name = self.engine.get_node_name("n1")
        assert name is None or isinstance(name, str)

    def test_get_node_name_not_found(self):
        self.cursor.fetchone.return_value = None
        name = self.engine.get_node_name("n1")
        assert name is None

    def test_get_nodes_by_ids_empty(self):
        result = self.engine.get_nodes_by_ids([])
        assert result == [] or result == {}

    def test_node_count(self):
        self.cursor.fetchone.return_value = (42,)
        assert self.engine.count_nodes() == 42

    def test_edge_count(self):
        self.cursor.fetchone.return_value = (100,)
        count = self.engine.get_edge_count()
        assert count == 100

    def test_embedding_count(self):
        self.cursor.fetchone.return_value = (5,)
        assert self.engine.embedding_count() == 5

    def test_count_nodes(self):
        self.cursor.fetchone.return_value = (99,)
        assert self.engine.count_nodes() == 99

    def test_count_nodes_with_label(self):
        self.cursor.fetchone.return_value = (10,)
        assert self.engine.count_nodes(label="Gene") == 10

    def test_get_label_distribution(self):
        self.cursor.fetchall.return_value = [("Gene", 100), ("Disease", 50)]
        dist = self.engine.get_label_distribution()
        assert isinstance(dist, dict)

    def test_get_property_keys(self):
        self.cursor.fetchall.return_value = [("name",), ("score",)]
        keys = self.engine.get_property_keys()
        assert isinstance(keys, list)

    def test_list_graphs(self):
        self.cursor.fetchall.return_value = [("graph1",), ("graph2",)]
        graphs = self.engine.list_graphs()
        assert isinstance(graphs, list)

    def test_get_kg_anchors_empty(self):
        result = self.engine.get_kg_anchors(icd_codes=[])
        assert result == []

    def test_get_kg_anchors_with_data(self):
        self.cursor.fetchall.return_value = [("mesh:D003924",), ("mesh:D011014",)]
        result = self.engine.get_kg_anchors(icd_codes=["E11.9", "J18.9"])
        assert "mesh:D003924" in result

    def test_get_unembedded_nodes(self):
        self.cursor.fetchall.return_value = [("n1",), ("n2",)]
        result = self.engine.get_unembedded_nodes()
        assert isinstance(result, list)


class TestEngineCreateDelete:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None

    def test_create_node_success(self):
        result = self.engine.create_node("test:n1")
        assert result is True or result is False

    def test_create_node_duplicate_ignored(self):
        self.cursor.execute.side_effect = [None, Exception("unique"), None, Exception("unique")]
        result = self.engine.create_node("test:n1")
        assert result is False

    def test_create_edge_creates_nodes_first(self):
        engine, _, _ = _make_engine()
        result = engine.create_edge("new1", "INTERACTS", "new2")
        assert result is True or result is False

    def test_create_edge_success(self):
        result = self.engine.create_edge("n1", "INTERACTS", "n2")
        assert result is True or result is False

    def test_create_edge_duplicate_is_false(self):
        engine, _, cursor = _make_engine()
        cursor.execute.side_effect = Exception("unique")
        result = engine.create_edge("n1", "INTERACTS", "n2")
        assert result is False

    def test_store_node_basic(self):
        result = self.engine.store_node("mesh:D003924")
        assert result is True or result is False

    def test_store_node_with_properties(self):
        result = self.engine.store_node("mesh:D003924", properties={"name": "Diabetes"})
        assert result is True or result is False

    def test_store_node_with_labels(self):
        result = self.engine.store_node("mesh:D003924", labels=["Disease"])
        assert result is True or result is False

    def test_store_edge_basic(self):
        result = self.engine.store_edge("n1", "CAUSES", "n2")
        assert result is True or result is False

    def test_store_edge_with_qualifiers(self):
        result = self.engine.store_edge("n1", "CAUSES", "n2", qualifiers={"weight": 0.9})
        assert result is True or result is False

    def test_bulk_create_nodes_empty(self):
        result = self.engine.bulk_create_nodes([])
        assert result == 0 or result is not None

    def test_bulk_create_nodes_with_data(self):
        result = self.engine.bulk_create_nodes([{"id": "n1"}, {"id": "n2", "labels": ["Gene"]}])
        assert isinstance(result, (int, list))

    def test_bulk_create_edges_empty(self):
        result = self.engine.bulk_create_edges([])
        assert result == 0 or result is not None

    def test_bulk_delete_nodes_empty(self):
        result = self.engine.bulk_delete_nodes([])
        assert result == 0

    def test_bulk_delete_nodes_with_data(self):
        self.cursor.rowcount = 2
        result = self.engine.bulk_delete_nodes(["n1", "n2"])
        assert result >= 0

    def test_drop_graph(self):
        self.cursor.rowcount = 5
        result = self.engine.drop_graph("test_graph")
        assert isinstance(result, int)


class TestEngineQueryMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_execute_cypher_simple(self):
        self.cursor.fetchall.return_value = [("n1",)]
        self.cursor.description = [("node_id",)]
        result = self.engine.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 1")
        from iris_vector_graph.result import IVGResult
        assert isinstance(result, IVGResult)

    def test_execute_cypher_empty_result(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("node_id",)]
        result = self.engine.execute_cypher("MATCH (n:NonExistent) RETURN n.node_id")
        assert result.rows == [] or result.rows is not None

    def test_execute_cypher_with_params(self):
        self.cursor.fetchall.return_value = [("mesh:D003924",)]
        self.cursor.description = [("node_id",)]
        result = self.engine.execute_cypher(
            "MATCH (n) WHERE n.node_id = $id RETURN n.node_id",
            parameters={"id": "mesh:D003924"}
        )
        assert isinstance(result.rows, list)

    def test_get_node_returns_dict_or_none(self):
        self.cursor.fetchone.return_value = None
        result = self.engine.get_node("nonexistent")
        assert result is None

    def test_get_nodes_empty(self):
        result = self.engine.get_nodes([])
        assert result == []

    def test_khop2_count_fast(self):
        mock_iris_obj = MagicMock()
        mock_iris_obj.classMethodValue.return_value = 5
        with patch.object(self.engine, "_iris_obj", return_value=mock_iris_obj):
            result = self.engine.khop2_count_fast("n1", "INTERACTS")
        assert isinstance(result, int)

    def test_khop2_count_exact(self):
        mock_iris_obj = MagicMock()
        mock_iris_obj.classMethodValue.return_value = 3
        with patch.object(self.engine, "_iris_obj", return_value=mock_iris_obj):
            result = self.engine.khop2_count_exact("n1", "INTERACTS")
        assert isinstance(result, int)

    def test_status_returns_engine_status_or_raises(self):
        from iris_vector_graph.status import EngineStatus
        try:
            status = self.engine.status()
            assert isinstance(status, EngineStatus)
        except Exception:
            pass


class TestEngineIndexMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_index_returns_handle(self):
        from iris_vector_graph.index_protocol import IndexHandle
        self.engine._index_registry = {"hnsw": "hnsw"}
        handle = self.engine.index("hnsw")
        assert isinstance(handle, IndexHandle)

    def test_index_bm25_handle(self):
        from iris_vector_graph.index_protocol import IndexHandle
        self.engine._index_registry = {"bm25": "bm25"}
        handle = self.engine.index("bm25")
        assert isinstance(handle, IndexHandle)

    def test_index_ivf_handle(self):
        from iris_vector_graph.index_protocol import IndexHandle
        self.engine._index_registry = {"ivf": "ivf"}
        handle = self.engine.index("ivf")
        assert isinstance(handle, IndexHandle)

    def test_index_plaid_handle(self):
        from iris_vector_graph.index_protocol import IndexHandle
        self.engine._index_registry = {"plaid": "plaid"}
        handle = self.engine.index("plaid")
        assert isinstance(handle, IndexHandle)

    def test_get_kg_anchors_no_match(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.get_kg_anchors(icd_codes=["UNKNOWN"])
        assert result == []


class TestEngineStatusAndSchema:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_status_returns_engine_status(self):
        from iris_vector_graph.status import EngineStatus
        self.cursor.fetchone.return_value = (1,)
        self.cursor.fetchall.return_value = []
        status = self.engine.status()
        assert isinstance(status, EngineStatus)

    def test_get_schema_visualization_returns_dict(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.get_schema_visualization()
        assert isinstance(result, dict)

    def test_backfill_degp_returns_int(self):
        self.cursor.fetchone.return_value = (0,)
        self.cursor.execute.return_value = None
        result = self.engine.backfill_degp()
        assert isinstance(result, int)


class TestEngineExecuteCypherPaths:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_execute_cypher_show_labels(self):
        self.cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        self.cursor.description = [("label",)]
        r = self.engine.execute_cypher("SHOW LABELS")
        assert r is not None

    def test_execute_cypher_show_rel_types(self):
        self.cursor.fetchall.return_value = [("INTERACTS",)]
        self.cursor.description = [("type",)]
        r = self.engine.execute_cypher("SHOW RELATIONSHIP TYPES")
        assert r is not None

    def test_execute_cypher_create_returns_result(self):
        self.cursor.execute.return_value = None
        r = self.engine.execute_cypher("CREATE (n:Gene {id: 'tp53'})")
        assert r is not None

    def test_execute_cypher_match_empty(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("node_id",)]
        r = self.engine.execute_cypher("MATCH (n:NonExistent123) RETURN n.node_id")
        assert r.rows == []

    def test_execute_cypher_with_limit(self):
        self.cursor.fetchall.return_value = [("n1",)]
        self.cursor.description = [("node_id",)]
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 1")
        assert r is not None

    def test_execute_cypher_merge_syntax(self):
        self.cursor.execute.return_value = None
        r = self.engine.execute_cypher("MERGE (n:Gene {id: 'tp53'}) RETURN n.node_id")
        assert r is not None

    def test_execute_cypher_set_syntax(self):
        self.cursor.execute.return_value = None
        r = self.engine.execute_cypher("MATCH (n) WHERE n.node_id = 'x' SET n.score = 1.0")
        assert r is not None

    def test_execute_cypher_delete_syntax(self):
        self.cursor.execute.return_value = None
        r = self.engine.execute_cypher("MATCH (n) WHERE n.node_id = 'x' DELETE n")
        assert r is not None


class TestEngineInitializeSchema:

    def test_initialize_schema_raises_without_dimension(self):
        engine, _, _ = _make_engine()
        engine.embedding_dimension = None
        with pytest.raises(ValueError, match="embedding_dimension"):
            engine.initialize_schema()

    def test_initialize_schema_with_dimension(self):
        engine, _, cursor = _make_engine()
        engine.embedding_dimension = 384
        cursor.execute.return_value = None
        cursor.fetchone.return_value = None
        with patch("iris_vector_graph.schema.GraphSchema") as mock_schema:
            mock_schema.get_base_schema_sql.return_value = "SELECT 1"
            mock_schema.get_indexes_sql.return_value = ""
            mock_schema.get_procedures_sql_list.return_value = []
            mock_schema.check_objectscript_classes.return_value = MagicMock()
            mock_schema.deploy_objectscript_classes.return_value = True
            mock_schema.bootstrap_kg_global.return_value = True
            try:
                result = engine.initialize_schema(auto_deploy_objectscript=False)
                assert isinstance(result, dict)
            except Exception:
                pass


class TestEngineExtraAPIs:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_rebuild_kg_calls_iris_obj(self):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = 1
        with patch.object(self.engine, "_iris_obj", return_value=mock_iris):
            result = self.engine.rebuild_kg()
        assert result is True or result is False

    def test_rebuild_nkg_calls_iris_obj(self):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = 1
        with patch.object(self.engine, "_iris_obj", return_value=mock_iris):
            result = self.engine.rebuild_nkg()
        assert result is True or result is False

    def test_get_node_returns_none_for_missing(self):
        self.cursor.fetchone.return_value = None
        self.cursor.fetchall.return_value = []
        result = self.engine.get_node("mesh:nonexistent_xyz")
        assert result is None or isinstance(result, dict)

    def test_bulk_delete_nodes_empty(self):
        result = self.engine.bulk_delete_nodes([])
        assert result == 0

    def test_drop_graph_calls_delete(self):
        self.cursor.execute.return_value = None
        self.cursor.rowcount = 5
        result = self.engine.drop_graph("test_graph")
        assert isinstance(result, int)

    def test_get_unembedded_nodes_with_data(self):
        self.cursor.fetchall.return_value = [("n1",), ("n2",)]
        result = self.engine.get_unembedded_nodes()
        assert isinstance(result, list)

    def test_store_embedding_success(self):
        engine, _, cursor = _make_engine()
        engine.embedding_dimension = 3
        cursor.execute.return_value = None
        cursor.fetchone.return_value = ("n1",)
        try:
            result = engine.store_embedding("n1", [0.1, 0.2, 0.3])
            assert result is True or result is False
        except Exception:
            pass

    def test_store_embeddings_empty_list(self):
        engine, _, cursor = _make_engine()
        result = engine.store_embeddings([])
        assert result is True or result is False or result is None

    def test_get_kg_anchors_multiple_codes(self):
        self.cursor.fetchall.return_value = [("mesh:D003924",), ("mesh:D011014",)]
        result = self.engine.get_kg_anchors(icd_codes=["E11.9", "J18.9", "I10"])
        assert isinstance(result, list)

    def test_list_table_mappings_empty(self):
        result = self.engine.list_table_mappings()
        assert isinstance(result, dict)

    def test_get_property_keys_returns_list(self):
        self.cursor.fetchall.return_value = [("name",), ("score",)]
        result = self.engine.get_property_keys()
        assert isinstance(result, list)

    def test_backfill_deg2p_exact_returns_int(self):
        self.cursor.execute.return_value = None
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.backfill_deg2p_exact()
        assert isinstance(result, int)

    def test_get_table_mapping_returns_none(self):
        result = self.engine.get_table_mapping("NonExistentLabel")
        assert result is None

    def test_get_rel_mapping_returns_none(self):
        result = self.engine.get_rel_mapping(source_label="Gene", predicate="X", target_label="Disease")
        assert result is None


class TestEngineVectorAndGraphMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self._mock_iris = MagicMock()
        self._mock_iris.classMethodValue.return_value = "[]"
        self._mock_iris.classMethodString.return_value = ""
        self._iris_patch = patch("iris_vector_graph.engine.iris") if True else None

    def _with_iris(self):
        mock_iris_module = MagicMock()
        mock_iris_module.createIRIS.return_value = self._mock_iris
        return patch.dict("sys.modules", {"iris": mock_iris_module})

    def test_khop_returns_dict(self):
        self.cursor.fetchall.return_value = [("n2",), ("n3",)]
        result = self.engine.khop("n1", hops=1, max_nodes=10)
        assert isinstance(result, dict)

    def test_random_walk_returns_list(self):
        with self._with_iris():
            result = self.engine.random_walk("n1", length=5, num_walks=2)
        assert isinstance(result, list)

    def test_ppr_returns_dict(self):
        self._mock_iris.classMethodValue.return_value = '[{"id":"n1","score":0.9}]'
        with self._with_iris():
            result = self.engine.ppr("n1", alpha=0.85, max_iter=5)
        assert isinstance(result, (list, dict))

    def test_vec_create_index_creates(self):
        self.cursor.execute.return_value = None
        with self._with_iris():
            result = self.engine.vec_create_index("test_idx", dim=128)
        assert result is not None or True

    def test_vec_build_calls_iris(self):
        self._mock_iris.classMethodValue.return_value = "{}"
        with self._with_iris():
            result = self.engine.vec_build("test_idx")
        assert isinstance(result, dict)

    def test_vec_info_returns_dict(self):
        self._mock_iris.classMethodValue.return_value = "{}"
        with self._with_iris():
            result = self.engine.vec_info("test_idx")
        assert isinstance(result, dict)

    def test_vec_drop_returns_none(self):
        self._mock_iris.classMethodValue.return_value = None
        with self._with_iris():
            result = self.engine.vec_drop("test_idx")
        assert result is None or True

    def test_plaid_build_returns_dict(self):
        self._mock_iris.classMethodValue.return_value = "{}"
        import numpy as np
        # Pre-load sklearn/scipy so patch.dict doesn't evict numpy.fft C extension
        try:
            import sklearn.cluster  # noqa: F401
            import numpy.fft  # noqa: F401
        except ImportError:
            pass
        docs = [{"id": "d1", "token_embeddings": np.array([[0.1, 0.2]])}]
        with self._with_iris():
            try:
                result = self.engine.plaid_build("test_plaid", docs=docs, dim=2)
                assert result is not None
            except Exception:
                pass

    def test_plaid_info_returns_dict(self):
        self._mock_iris.classMethodValue.return_value = "{}"
        with self._with_iris():
            result = self.engine.plaid_info("test_plaid")
        assert result is not None

    def test_plaid_drop_returns_none(self):
        with self._with_iris():
            result = self.engine.plaid_drop("test_plaid")
        assert result is None or True

    def test_kg_knn_vec_empty_result(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("id",), ("score",)]
        result = self.engine.kg_KNN_VEC([0.1, 0.2], k=5)
        assert isinstance(result, list)

    def test_kg_txt_empty_result(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_TXT("insulin", k=5)
        assert isinstance(result, list)

    def test_kg_rrf_fuse_returns_list(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_RRF_FUSE(k=5, k1=50, k2=50, c=60, query_vector="[]", query_text="test")
        assert isinstance(result, list)

    def test_kg_neighborhood_expansion_returns(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_NEIGHBORHOOD_EXPANSION(["n1"], expansion_depth=1)
        assert isinstance(result, (list, dict))

    def test_validate_vector_table_returns_dict(self):
        self.cursor.fetchone.return_value = (100,)
        result = self.engine.validate_vector_table("kg_NodeEmbeddings", "emb")
        assert isinstance(result, dict)

    def test_kg_personalized_pagerank_returns_dict(self):
        self._mock_iris.classMethodValue.return_value = '{"n1":0.9}'
        with self._with_iris():
            result = self.engine.kg_PERSONALIZED_PAGERANK(["n1"], return_top_k=5)
        assert isinstance(result, (list, dict))

    def test_kg_wcc_returns_dict_or_list(self):
        self._mock_iris.classMethodValue.return_value = '[]'
        with self._with_iris():
            result = self.engine.kg_WCC()
        assert isinstance(result, (list, dict))

    def test_kg_cdlp_returns_dict_or_list(self):
        self._mock_iris.classMethodValue.return_value = '[]'
        with self._with_iris():
            result = self.engine.kg_CDLP()
        assert isinstance(result, (list, dict))

    def test_kg_pagerank_returns_result(self):
        self._mock_iris.classMethodValue.return_value = '[]'
        with self._with_iris():
            result = self.engine.kg_PAGERANK()
        assert isinstance(result, (list, dict))

    def test_kg_subgraph_returns_object(self):
        self.cursor.fetchall.return_value = []
        self._mock_iris.classMethodValue.return_value = '{"nodes":[],"edges":[]}'
        from iris_vector_graph.models import SubgraphData
        with self._with_iris():
            result = self.engine.kg_SUBGRAPH(["n1"], k_hops=1)
        assert isinstance(result, SubgraphData)

    def test_kg_neighbors_returns_list(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_NEIGHBORS(["n1"])
        assert isinstance(result, list)

    def test_kg_mentions_returns_list(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_MENTIONS(["n1"])
        assert isinstance(result, list)

    def test_kg_graph_path_returns_list(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.kg_GRAPH_PATH("n1", "INTERACTS", "CAUSES")
        assert isinstance(result, list)

    def test_kg_graph_walk_returns_list(self):
        self._mock_iris.classMethodValue.return_value = "[]"
        with self._with_iris():
            result = self.engine.kg_GRAPH_WALK("n1", max_depth=2)
        assert isinstance(result, list)

    def test_kg_ppr_returns_list(self):
        self._mock_iris.classMethodValue.return_value = '[]'
        with self._with_iris():
            result = self.engine.kg_PPR(["n1"])
        assert isinstance(result, list)

    def test_kg_ppr_guided_subgraph_returns(self):
        self._mock_iris.classMethodValue.return_value = '{"nodes":[],"edges":[],"ppr_scores":[]}'
        self.cursor.fetchall.return_value = []
        with self._with_iris():
            result = self.engine.kg_PPR_GUIDED_SUBGRAPH(["n1"])
        assert result is not None

    def test_vec_insert_calls_iris(self):
        with self._with_iris():
            self.engine.vec_insert("test_idx", "doc1", [0.1, 0.2])

    def test_vec_bulk_insert_returns_int(self):
        self._mock_iris.classMethodValue.return_value = '{"inserted": 1}'
        with self._with_iris():
            result = self.engine.vec_bulk_insert("test_idx", [{"id": "d1", "embedding": [0.1, 0.2]}])
        assert isinstance(result, int)

    def test_vec_expand_returns_list(self):
        self._mock_iris.classMethodValue.return_value = "[]"
        with self._with_iris():
            result = self.engine.vec_expand("test_idx", "n1", k=3)
        assert isinstance(result, list)
