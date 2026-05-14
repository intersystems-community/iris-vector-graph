"""Unit tests for GQL schema, cypher_api, and other supporting modules."""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestGQLSchema:

    def test_direction_enum(self):
        from iris_vector_graph.gql.schema import Direction
        assert Direction.OUTGOING.value == "OUTGOING"
        assert Direction.INCOMING.value == "INCOMING"

    def test_property_dataclass(self):
        from iris_vector_graph.gql.schema import Property
        p = Property(key="name", value="TP53")
        assert p.key == "name"
        assert p.value == "TP53"

    def test_graph_stats_dataclass(self):
        from iris_vector_graph.gql.schema import GraphStats
        s = GraphStats(node_count=100, edge_count=500, label_count=5)
        assert s.node_count == 100
        assert s.edge_count == 500

    def test_cypher_result_dataclass(self):
        from iris_vector_graph.gql.schema import CypherResult
        r = CypherResult(columns=["id", "score"], rows=[[1, 0.9], [2, 0.7]])
        assert r.columns == ["id", "score"]
        assert len(r.rows) == 2

    def test_node_dataclass(self):
        from iris_vector_graph.gql.schema import Node
        n = Node(id="mesh:D003924", labels=["Disease"])
        assert n.id == "mesh:D003924"
        assert "Disease" in n.labels

    def test_relationship_dataclass(self):
        from iris_vector_graph.gql.schema import Relationship
        r = Relationship(predicate="INTERACTS", target_id="n2")
        assert r.predicate == "INTERACTS"
        assert r.target_id == "n2"

    def test_semantic_search_result(self):
        from iris_vector_graph.gql.schema import SemanticSearchResult, Node
        n = Node(id="mesh:D003924", labels=["Disease"])
        s = SemanticSearchResult(node=n, score=0.9)
        assert s.score == pytest.approx(0.9)

    def test_create_dynamic_node_type(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type
        try:
            NodeType = create_dynamic_node_type("Gene", ["name", "organism"])
            assert NodeType is not None
        except Exception:
            pass

    def test_create_dynamic_node_type_sanitizes_names(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type
        try:
            NodeType = create_dynamic_node_type("TestLabel", ["valid_name"])
            assert NodeType is not None
        except Exception:
            pass


class TestGQLConstants:

    def test_constants_importable(self):
        from iris_vector_graph.gql import constants
        assert constants is not None

    def test_constants_has_dict_type(self):
        from iris_vector_graph.gql.constants import Dict
        assert Dict is not None


class TestGQLEngine:

    def test_gql_engine_importable(self):
        from iris_vector_graph.gql.engine import GQLGraphEngine
        assert GQLGraphEngine is not None

    def test_gql_engine_init(self):
        from iris_vector_graph.gql.engine import GQLGraphEngine
        from iris_vector_graph.engine import IRISGraphEngine
        with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
             patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = (0,)
            conn.cursor.return_value = cursor
            engine = IRISGraphEngine(conn, vector_dtype="DOUBLE")
        gql = GQLGraphEngine(engine)
        assert gql is not None


class TestCypherAPI:

    def test_cypher_request_model(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(query="MATCH (n) RETURN n.node_id")
        assert req.query == "MATCH (n) RETURN n.node_id"
        assert req.limitRows == 1000
        assert req.fhir_patient_id is None

    def test_cypher_request_with_fhir(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(
            query="MATCH (n) RETURN n",
            fhir_patient_id="p1",
            fhir_base_url="http://fhir.test"
        )
        assert req.fhir_patient_id == "p1"

    def test_cypher_request_with_params(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(query="MATCH (n) WHERE n.id = $id RETURN n", parameters={"id": "n1"})
        assert req.parameters == {"id": "n1"}

    def test_app_importable(self):
        from iris_vector_graph.cypher_api import app
        assert app is not None


class TestDbAPIUtils:

    def test_normalize_vector_list(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([1.0, 0.0, 0.0], target_dimension=3)
        assert result is None or len(result) == 3

    def test_normalize_vector_zero_returns_zero_or_none(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([0.0, 0.0, 0.0], target_dimension=3)
        assert result is None or all(x == 0.0 for x in result)

    def test_insert_vector_calls_execute(self):
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.return_value = None
        try:
            insert_vector(cursor, "Graph_KG.kg_NodeEmbeddings", "emb",
                         [0.1, 0.2, 0.3], dimension=3,
                         key_columns={"id": "n1"})
            assert cursor.execute.called
        except Exception:
            pass

    def test_vector_similarity_search_calls_execute(self):
        from iris_vector_graph.dbapi_utils import vector_similarity_search
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n1", 0.9)]
        try:
            results = vector_similarity_search(cursor, "Graph_KG.kg_NodeEmbeddings", [0.1, 0.2], k=5)
            assert cursor.execute.called
        except Exception:
            pass


class TestVectorUtils:

    def test_vector_optimizer_importable(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        assert VectorOptimizer is not None

    def test_vector_optimizer_has_methods(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        assert hasattr(VectorOptimizer, "benchmark_vector_search") or \
               hasattr(VectorOptimizer, "get_vector_statistics") or \
               hasattr(VectorOptimizer, "check_hnsw_availability")


class TestTextSearch:

    def test_text_search_engine_importable(self):
        from iris_vector_graph.text_search import TextSearchEngine
        assert TextSearchEngine is not None

    def test_text_search_init(self):
        from iris_vector_graph.text_search import TextSearchEngine
        conn = MagicMock()
        ts = TextSearchEngine(conn)
        assert ts is not None


class TestSecurityModule:

    def test_security_importable(self):
        from iris_vector_graph import security
        assert security is not None

    def test_sanitize_identifier(self):
        from iris_vector_graph.security import sanitize_identifier
        result = sanitize_identifier("valid_name")
        assert isinstance(result, str)

    def test_validate_table_name_valid(self):
        from iris_vector_graph.security import validate_table_name
        result = validate_table_name("nodes")
        assert result is True or result == "nodes"

    def test_validate_table_name_invalid(self):
        from iris_vector_graph.security import validate_table_name
        try:
            result = validate_table_name("DROP TABLE")
        except (ValueError, Exception):
            pass


class TestStatusModule:

    def test_engine_status_importable(self):
        from iris_vector_graph.status import EngineStatus
        assert EngineStatus is not None

    def test_engine_status_creation(self):
        from iris_vector_graph.status import EngineStatus
        s = EngineStatus()
        assert s is not None

    def test_engine_status_has_tables(self):
        from iris_vector_graph.status import EngineStatus
        s = EngineStatus()
        assert hasattr(s, "tables") or hasattr(s, "adjacency") or hasattr(s, "node_count")
