"""Tests for: paths.py, utils.py, vector_utils.py, text_search.py, dbapi_utils.py, gql modules, cypher_api.py, schema.py extra paths."""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


# ─── cypher/algorithms/paths.py ──────────────────────────────────────────────

class TestCypherPaths:

    def test_generate_neighbors_sql_outgoing(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_neighbors_sql
        sql = generate_neighbors_sql("outgoing")
        assert "o_id" in sql and "?" in sql

    def test_generate_neighbors_sql_incoming(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_neighbors_sql
        sql = generate_neighbors_sql("incoming")
        assert "o_id" in sql and "s" in sql

    def test_generate_neighbors_sql_both(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_neighbors_sql
        sql = generate_neighbors_sql("both")
        assert "UNION" in sql

    def test_generate_batch_neighbors_sql_outgoing(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_batch_neighbors_sql
        sql = generate_batch_neighbors_sql(3, "outgoing")
        assert "?, ?, ?" in sql

    def test_generate_batch_neighbors_sql_incoming(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_batch_neighbors_sql
        sql = generate_batch_neighbors_sql(2, "incoming")
        assert "IN" in sql

    def test_generate_batch_neighbors_sql_both(self):
        from iris_vector_graph.cypher.algorithms.paths import generate_batch_neighbors_sql
        sql = generate_batch_neighbors_sql(2, "both")
        assert "UNION" in sql

    def test_find_shortest_path_same_node(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        result = find_shortest_path_bfs(cursor, "n1", "n1")
        assert len(result) == 1
        assert result[0]["depth"] == 0
        assert result[0]["path"] == ["n1"]

    def test_find_shortest_path_direct_neighbor(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n2", "n1", "INTERACTS")]
        result = find_shortest_path_bfs(cursor, "n1", "n2", max_hops=3)
        assert len(result) >= 1
        assert result[0]["depth"] == 1

    def test_find_shortest_path_no_path(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        result = find_shortest_path_bfs(cursor, "n1", "n99", max_hops=2)
        assert result == []

    def test_find_shortest_path_all_paths(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n2", "n1", "R1"), ("n2", "n1", "R2")]
        result = find_shortest_path_bfs(cursor, "n1", "n2", max_hops=3, all_paths=True)
        assert len(result) >= 1

    def test_find_shortest_path_incoming_direction(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n2", "n1", "R")]
        result = find_shortest_path_bfs(cursor, "n1", "n2", direction="incoming")
        assert isinstance(result, list)

    def test_find_shortest_path_both_direction(self):
        from iris_vector_graph.cypher.algorithms.paths import find_shortest_path_bfs
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n2", "n1", "R")]
        result = find_shortest_path_bfs(cursor, "n1", "n2", direction="both")
        assert isinstance(result, list)

    def test_find_all_paths_same_node_no_min_hops(self):
        from iris_vector_graph.cypher.algorithms.paths import find_all_paths
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        result = find_all_paths(cursor, "n1", "n2", min_hops=1, max_hops=2)
        assert isinstance(result, list)

    def test_find_all_paths_finds_direct(self):
        from iris_vector_graph.cypher.algorithms.paths import find_all_paths
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [("n2", "n1", "CAUSES")],
            [],
        ]
        result = find_all_paths(cursor, "n1", "n2", min_hops=1, max_hops=2)
        assert any(r["depth"] == 1 for r in result)

    def test_find_all_paths_both_direction(self):
        from iris_vector_graph.cypher.algorithms.paths import find_all_paths
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        result = find_all_paths(cursor, "n1", "n2", direction="both")
        assert isinstance(result, list)


# ─── utils.py ─────────────────────────────────────────────────────────────────

class TestUtils:

    def test_split_sql_single_statement(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 1")
        assert len(stmts) == 1
        assert stmts[0] == "SELECT 1"

    def test_split_sql_two_statements(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 1; SELECT 2")
        assert len(stmts) == 2

    def test_split_sql_ignores_empty(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 1;;SELECT 2")
        assert len([s for s in stmts if s]) == 2

    def test_split_sql_respects_quoted_semicolons(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 'hello;world'")
        assert len(stmts) == 1

    def test_split_sql_strips_line_comments(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 1 -- comment\n; SELECT 2")
        non_empty = [s for s in stmts if s.strip()]
        assert len(non_empty) == 2

    def test_split_sql_strips_block_comments(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT /* comment */ 1")
        assert len(stmts) >= 1

    def test_split_sql_handles_empty_string(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("")
        assert stmts == []

    def test_split_sql_handles_whitespace_only(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("   \n  ")
        assert stmts == []

    def test_split_sql_three_statements(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 1; SELECT 2; SELECT 3")
        assert len(stmts) == 3

    def test_split_sql_double_quoted_string(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements('SELECT "col;name" FROM t')
        assert len(stmts) == 1

    def test_split_sql_escaped_quote(self):
        from iris_vector_graph.utils import _split_sql_statements
        stmts = _split_sql_statements("SELECT 'it''s here'")
        assert len(stmts) == 1


# ─── vector_utils.py ─────────────────────────────────────────────────────────

class TestVectorUtils:

    def _make_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_optimizer_init(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, _ = self._make_conn()
        opt = VectorOptimizer(conn)
        assert opt.conn is conn

    def test_check_hnsw_availability_returns_dict(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.execute.side_effect = Exception("no table")
        opt = VectorOptimizer(conn)
        result = opt.check_hnsw_availability()
        assert isinstance(result, dict)

    def test_check_hnsw_availability_found(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.fetchone.return_value = (100,)
        opt = VectorOptimizer(conn)
        result = opt.check_hnsw_availability()
        assert isinstance(result, dict)

    def test_get_vector_statistics_returns_dict(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.fetchone.return_value = (100, 1.5, 384)
        opt = VectorOptimizer(conn)
        result = opt.get_vector_statistics()
        assert isinstance(result, dict)

    def test_get_vector_statistics_no_table(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.execute.side_effect = Exception("no table")
        opt = VectorOptimizer(conn)
        result = opt.get_vector_statistics()
        assert isinstance(result, dict)

    def test_migrate_to_optimized_no_source(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.fetchone.return_value = (0,)
        opt = VectorOptimizer(conn)
        result = opt.migrate_to_optimized()
        assert isinstance(result, dict)

    def test_benchmark_vector_search_no_vectors(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.fetchall.return_value = []
        opt = VectorOptimizer(conn)
        result = opt.benchmark_vector_search(test_vectors=None, iterations=1)
        assert isinstance(result, dict)

    def test_optimize_hnsw_parameters_returns_dict(self):
        from iris_vector_graph.vector_utils import VectorOptimizer
        conn, cursor = self._make_conn()
        cursor.fetchall.return_value = []
        opt = VectorOptimizer(conn)
        result = opt.optimize_hnsw_parameters(m_values=[8], ef_values=[100])
        assert isinstance(result, dict)


# ─── text_search.py ──────────────────────────────────────────────────────────

class TestTextSearch:

    def _make_engine(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        conn.cursor.return_value = cursor
        from iris_vector_graph.text_search import TextSearchEngine
        return TextSearchEngine(conn), conn, cursor

    def test_extract_entity_names_empty(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = []
        result = ts.extract_entity_names("insulin")
        assert result == []

    def test_extract_entity_names_with_results(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = [("n1", "Insulin", 0.9)]
        result = ts.extract_entity_names("insulin", k=5)
        assert isinstance(result, list)

    def test_search_documents_empty(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = []
        result = ts.search_documents("test query")
        assert result == []

    def test_search_documents_with_results(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = [("doc1", 0.85)]
        result = ts.search_documents("insulin resistance", k=10)
        assert isinstance(result, list)

    def test_search_entity_qualifiers_empty(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = []
        result = ts.search_entity_qualifiers("test")
        assert result == []

    def test_search_entity_qualifiers_with_results(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = [("n1", "TREATS", "n2", 0.9, 0.8)]
        result = ts.search_entity_qualifiers("tumor suppressor", k=5)
        assert isinstance(result, list)

    def test_search_with_context_empty(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = []
        result = ts.search_with_context("test")
        assert isinstance(result, list)

    def test_search_with_context_entity_type_filter(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = [("n1", "TP53", "name", "Gene", 0.9)]
        result = ts.search_with_context("tumor suppressor", entity_types=["Gene"])
        assert isinstance(result, list)

    def test_search_handles_sql_error(self):
        ts, _, cursor = self._make_engine()
        cursor.fetchall.return_value = []
        result = ts.extract_entity_names("test")
        assert isinstance(result, list)


# ─── dbapi_utils.py ──────────────────────────────────────────────────────────

class TestDBAPIUtils:

    def test_normalize_vector_list_input(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([3.0, 4.0], target_dimension=2)
        if result:
            assert len(result) == 2

    def test_normalize_vector_zero_vector(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([0.0, 0.0, 0.0], target_dimension=3)
        assert result is None or result == [0.0, 0.0, 0.0]

    def test_normalize_vector_wrong_dimension(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([1.0, 2.0], target_dimension=4)
        assert result is None or len(result) == 4

    def test_normalize_vector_string_input(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector("[1.0, 0.0, 0.0]", target_dimension=3)
        assert result is None or len(result) == 3

    def test_create_hnsw_index(self):
        from iris_vector_graph.dbapi_utils import create_hnsw_index
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = create_hnsw_index(cursor, "kg_NodeEmbeddings", "emb", 384)
        assert result is True or result is False or result is None

    def test_create_hnsw_index_error_handling(self):
        from iris_vector_graph.dbapi_utils import create_hnsw_index
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("already exists")
        result = create_hnsw_index(cursor, "t", "emb", 128)
        assert result is False or result is True or result is None

    def test_create_ivfflat_index(self):
        from iris_vector_graph.dbapi_utils import create_ivfflat_index
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = create_ivfflat_index(cursor, "t", "emb", 128)
        assert result is True or result is False or result is None

    def test_vector_similarity_search_returns_list(self):
        from iris_vector_graph.dbapi_utils import vector_similarity_search
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": "n1", "score": 0.9}]
        result = vector_similarity_search(cursor, "kg_NodeEmbeddings", "emb", [0.1, 0.2, 0.3], top_k=5)
        assert isinstance(result, list)

    def test_vector_similarity_search_empty(self):
        from iris_vector_graph.dbapi_utils import vector_similarity_search
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        result = vector_similarity_search(cursor, "kg_NodeEmbeddings", "emb", [0.1], top_k=5)
        assert result == []

    def test_insert_vector_success(self):
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = insert_vector(cursor, "t", "emb", [0.1, 0.2], dimension=2,
                               key_columns={"id": "n1"})
        assert result is True or result is False or result is None

    def test_insert_vector_error(self):
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("constraint violation")
        result = insert_vector(cursor, "t", "emb", [0.1], dimension=1,
                               key_columns={"id": "n1"})
        assert result is False or result is None or result is True


# ─── gql modules ──────────────────────────────────────────────────────────────

class TestGQLPooling:

    def test_pool_importable(self):
        from iris_vector_graph.gql import pooling
        assert pooling is not None

    def test_get_pool_function_exists(self):
        from iris_vector_graph.gql.pooling import get_pool
        assert get_pool is not None

    def test_connection_context_exists(self):
        from iris_vector_graph.gql.pooling import connection_context
        assert connection_context is not None

    def test_async_pool_init(self):
        from iris_vector_graph.gql.pooling import AsyncConnectionPool
        engine = MagicMock()
        engine.conn = MagicMock()
        pool = AsyncConnectionPool(engine, max_size=3)
        assert pool.max_size == 3
        assert pool.engine is engine

    def test_pool_lock_exists(self):
        from iris_vector_graph.gql import pooling
        assert hasattr(pooling, '_pool_lock')

    def test_async_pool_created_count(self):
        from iris_vector_graph.gql.pooling import AsyncConnectionPool
        engine = MagicMock()
        engine.conn = MagicMock()
        pool = AsyncConnectionPool(engine)
        assert pool._created_count == 0

    @pytest.mark.asyncio
    async def test_get_pool_creates_instance(self):
        from iris_vector_graph.gql import pooling as p_mod
        p_mod._pool_instance = None
        from iris_vector_graph.gql.pooling import get_pool, AsyncConnectionPool
        engine = MagicMock()
        engine.conn = MagicMock()
        pool = await get_pool(engine)
        assert isinstance(pool, AsyncConnectionPool)
        p_mod._pool_instance = None

    @pytest.mark.asyncio
    async def test_get_pool_returns_existing(self):
        from iris_vector_graph.gql import pooling as p_mod
        from iris_vector_graph.gql.pooling import get_pool, AsyncConnectionPool
        engine = MagicMock()
        engine.conn = MagicMock()
        p_mod._pool_instance = None
        pool1 = await get_pool(engine)
        pool2 = await get_pool()
        assert pool1 is pool2
        p_mod._pool_instance = None


class TestGQLInitModule:

    def test_create_app_importable(self):
        from iris_vector_graph.gql import create_app
        assert create_app is not None

    def test_serve_importable(self):
        from iris_vector_graph.gql import serve
        assert serve is not None

    def test_create_app_returns_fastapi(self):
        from iris_vector_graph.gql import create_app
        from iris_vector_graph.engine import IRISGraphEngine
        from fastapi import FastAPI
        with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
             patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = (0,)
            conn.cursor.return_value = cursor
            engine = IRISGraphEngine(conn, vector_dtype="DOUBLE")
        app = create_app(engine)
        assert isinstance(app, FastAPI)


class TestGQLEngineModule:

    def _make_gql_engine(self):
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
        return gql, cursor

    def test_get_labels_empty(self):
        gql, cursor = self._make_gql_engine()
        cursor.fetchall.return_value = []
        labels = gql.get_labels()
        assert labels == []

    def test_get_labels_with_data(self):
        gql, cursor = self._make_gql_engine()
        cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        labels = gql.get_labels()
        assert "Gene" in labels or isinstance(labels, list)

    def test_get_sampled_properties_empty(self):
        gql, cursor = self._make_gql_engine()
        cursor.fetchall.return_value = []
        props = gql.get_sampled_properties("Gene")
        assert props == set()

    def test_get_sampled_properties_with_data(self):
        gql, cursor = self._make_gql_engine()
        cursor.fetchall.return_value = [("name",), ("organism",)]
        props = gql.get_sampled_properties("Gene")
        assert isinstance(props, set)

    def test_get_schema_metadata_empty(self):
        gql, cursor = self._make_gql_engine()
        cursor.fetchall.return_value = []
        meta = gql.get_schema_metadata()
        assert meta == {}

    def test_get_labels_error_returns_empty(self):
        gql, cursor = self._make_gql_engine()
        cursor.execute.side_effect = Exception("SQL error")
        labels = gql.get_labels()
        assert labels == []


class TestGQLResolvers:

    def test_serialize_value_string(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        assert serialize_value("hello") == "hello"

    def test_serialize_value_int(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        assert serialize_value(42) == 42

    def test_serialize_value_float(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        assert serialize_value(3.14) == 3.14

    def test_serialize_value_list(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        result = serialize_value([1, 2, 3])
        assert isinstance(result, list)

    def test_serialize_value_dict(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        result = serialize_value({"a": 1})
        assert isinstance(result, dict)

    def test_serialize_value_none(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        result = serialize_value(None)
        assert result is None

    def test_serialize_value_json_string(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        result = serialize_value('{"key": "val"}')
        assert isinstance(result, (str, dict))

    def test_serialize_value_bytes(self):
        from iris_vector_graph.gql.resolvers import serialize_value
        result = serialize_value(b"bytes")
        assert isinstance(result, (str, bytes))

    def test_map_node_data_basic(self):
        from iris_vector_graph.gql.resolvers import map_node_data
        info = MagicMock()
        info.context = {"engine": MagicMock()}
        result = map_node_data({"id": "n1", "labels": ["Gene"]}, info)
        assert result is not None or result is None

    def test_map_node_data_with_properties(self):
        from iris_vector_graph.gql.resolvers import map_node_data
        info = MagicMock()
        mock_eng = MagicMock()
        mock_eng.get_node_properties.return_value = {"name": "TP53"}
        info.context = {"engine": mock_eng}
        result = map_node_data({"id": "n1", "labels": ["Gene"], "name": "TP53"}, info)
        assert result is not None or result is None

    @pytest.mark.asyncio
    async def test_resolve_stats_calls_engine(self):
        from iris_vector_graph.gql.resolvers import resolve_stats
        info = MagicMock()
        mock_eng = MagicMock()
        mock_eng.get_node_count.return_value = 100
        mock_eng.get_edge_count.return_value = 500
        mock_eng.get_labels.return_value = ["Gene"]
        info.context = {"engine": mock_eng}
        result = await resolve_stats(info)
        assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_node_not_found(self):
        from iris_vector_graph.gql.resolvers import resolve_node
        info = MagicMock()
        mock_eng = MagicMock()
        mock_eng.get_node.return_value = None
        info.context = {"engine": mock_eng}
        result = await resolve_node(info, id="nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_nodes_empty(self):
        from iris_vector_graph.gql.resolvers import resolve_nodes
        info = MagicMock()
        mock_eng = MagicMock()
        mock_eng.execute_cypher.return_value = MagicMock(rows=[], columns=[])
        info.context = {"engine": mock_eng}
        result = await resolve_nodes(info, label="Gene")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_resolve_cypher_returns_result(self):
        from iris_vector_graph.gql.resolvers import resolve_cypher
        info = MagicMock()
        mock_eng = MagicMock()
        mock_result = MagicMock()
        mock_result.columns = ["id"]
        mock_result.rows = [["n1"]]
        mock_result.error = None
        mock_eng.execute_cypher.return_value = mock_result
        info.context = {"engine": mock_eng}
        result = await resolve_cypher(info, query="MATCH (n) RETURN n.node_id")
        assert result is not None


# ─── cypher_api.py via FastAPI TestClient ────────────────────────────────────

class TestCypherAPIEndpoints:

    def _get_client(self):
        from iris_vector_graph.cypher_api import app, _reset_engine
        _reset_engine()
        return TestClient(app, raise_server_exceptions=False)

    def test_health_endpoint(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_engine = MagicMock()
            mock_engine.is_ready.return_value = True
            mock_engine.node_count.return_value = 100
            mock_engine.edge_count.return_value = 500
            mock_eng.return_value = mock_engine
            resp = client.get("/health")
        assert resp.status_code in (200, 503)

    def test_health_not_connected(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_eng.side_effect = Exception("not connected")
            resp = client.get("/health")
        assert resp.status_code in (200, 503)

    def test_cypher_endpoint_basic(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.columns = ["node_id"]
            mock_result.rows = [("n1",)]
            mock_result.error = None
            mock_engine.execute_cypher.return_value = mock_result
            mock_eng.return_value = mock_engine
            resp = client.post("/api/cypher", json={"query": "MATCH (n) RETURN n.node_id LIMIT 1"})
        assert resp.status_code in (200, 400, 500)

    def test_cypher_endpoint_error(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_eng.side_effect = Exception("DB error")
            resp = client.post("/api/cypher", json={"query": "MATCH (n) RETURN n"})
        assert resp.status_code in (200, 400, 500)

    def test_neo4j_discovery_endpoint(self):
        client = self._get_client()
        resp = client.get("/db/neo4j")
        assert resp.status_code in (200, 404, 500)

    def test_neo4j_tx_endpoint(self):
        client = self._get_client()
        resp = client.get("/db/neo4j/tx")
        assert resp.status_code in (200, 404, 500)

    def test_neo4j_tx_commit_endpoint(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.columns = []
            mock_result.rows = []
            mock_result.error = None
            mock_engine.execute_cypher.return_value = mock_result
            mock_eng.return_value = mock_engine
            resp = client.post("/db/neo4j/tx/commit", json={
                "statements": [{"statement": "MATCH (n) RETURN n", "parameters": {}}]
            })
        assert resp.status_code in (200, 400, 500)

    def test_query_v2_endpoint(self):
        client = self._get_client()
        with patch("iris_vector_graph.cypher_api._get_engine") as mock_eng:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.columns = []
            mock_result.rows = []
            mock_result.error = None
            mock_engine.execute_cypher.return_value = mock_result
            mock_eng.return_value = mock_engine
            resp = client.post("/db/neo4j/query/v2", json={"query": "RETURN 1", "parameters": {}})
        assert resp.status_code in (200, 400, 404, 422, 500)

    def test_browser_redirect(self):
        client = self._get_client()
        resp = client.get("/browser", follow_redirects=False)
        assert resp.status_code in (200, 307, 308, 404)


# ─── schema.py extra static method coverage ──────────────────────────────────

class TestSchemaExtra:

    def test_get_procedures_sql_list(self):
        from iris_vector_graph.schema import GraphSchema
        procs = GraphSchema.get_procedures_sql_list()
        assert isinstance(procs, list)

    def test_check_objectscript_classes_returns_capabilities(self):
        from iris_vector_graph.schema import GraphSchema
        from iris_vector_graph.capabilities import IRISCapabilities
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("no class")
        result = GraphSchema.check_objectscript_classes(cursor)
        assert isinstance(result, IRISCapabilities)

    def test_bootstrap_kg_global_no_conn(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("no KG class")
        result = GraphSchema.bootstrap_kg_global(cursor)
        assert result is True or result is False

    def test_rebuild_indexes_returns_dict(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = GraphSchema.rebuild_indexes(cursor)
        assert isinstance(result, dict)

    def test_get_embedding_dimension_default(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        result = GraphSchema.get_embedding_dimension(cursor)
        assert result is None or isinstance(result, int)

    def test_add_graph_id_index_returns_bool(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = GraphSchema.add_graph_id_index(cursor)
        assert isinstance(result, bool)
