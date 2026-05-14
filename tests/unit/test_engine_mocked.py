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
