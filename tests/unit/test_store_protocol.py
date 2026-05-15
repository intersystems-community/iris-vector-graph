import os
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.result import IVGResult
from iris_vector_graph.store_protocol import GraphStore

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class MockGraphStore:
    def __init__(self, capabilities_override=None):
        self.called_methods = []
        self.last_call = {}
        self._fixtures = {}
        self._caps = capabilities_override or {
            "native_sql": False,
            "bfs": True, "shortest_path": True, "weighted_shortest_path": True,
            "ppr": True, "pagerank": True, "wcc": True, "cdlp": True,
            "subgraph": True, "knn_vec": True,
            "temporal_edges": True, "temporal_window_query": True,
            "temporal_cypher": True, "temporal_aggregate": True,
        }

    def _record(self, method, **kwargs):
        self.called_methods.append(method)
        self.last_call = kwargs
        return self._fixtures.get(method, IVGResult(columns=[], rows=[]))

    def get_nodes(self, node_ids, properties=None):
        return self._record("get_nodes", node_ids=node_ids, properties=properties)

    def get_node_labels(self, node_ids):
        return self._record("get_node_labels", node_ids=node_ids)

    def query_nodes(self, label_filter=None, property_filters=None, return_properties=None, limit=0):
        return self._record("query_nodes", label_filter=label_filter,
                            property_filters=property_filters, return_properties=return_properties,
                            limit=limit)

    def write_nodes(self, nodes):
        return self._record("write_nodes", nodes=nodes)

    def write_edges(self, edges):
        return self._record("write_edges", edges=edges)

    def delete_nodes(self, node_ids):
        return self._record("delete_nodes", node_ids=node_ids)

    def delete_edges(self, edges):
        return self._record("delete_edges", edges=edges)

    def execute_sql(self, sql, params, read_only=True):
        return self._record("execute_sql", sql=sql, params=params, read_only=read_only)

    def execute_transaction(self, stmts, params_list):
        return self._record("execute_transaction", stmts=stmts, params_list=params_list)

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results):
        return self._record("execute_bfs", source_id=source_id, predicates=predicates,
                            max_hops=max_hops, direction=direction, max_results=max_results)

    def execute_shortest_path(self, source_id, target_id, predicates, max_hops, direction, find_all):
        return self._record("execute_shortest_path", source_id=source_id, target_id=target_id,
                            predicates=predicates, max_hops=max_hops, direction=direction, find_all=find_all)

    def execute_weighted_shortest_path(self, source_id, target_id, weight_property, max_hops):
        return self._record("execute_weighted_shortest_path", source_id=source_id,
                            target_id=target_id, weight_property=weight_property, max_hops=max_hops)

    def execute_ppr(self, seed_ids, damping, max_iterations):
        return self._record("execute_ppr", seed_ids=seed_ids, damping=damping, max_iterations=max_iterations)

    def execute_pagerank(self, damping, max_iterations):
        return self._record("execute_pagerank", damping=damping, max_iterations=max_iterations)

    def execute_wcc(self):
        return self._record("execute_wcc")

    def execute_cdlp(self, max_iterations):
        return self._record("execute_cdlp", max_iterations=max_iterations)

    def execute_subgraph(self, seed_ids, k_hops, edge_types, max_nodes):
        return self._record("execute_subgraph", seed_ids=seed_ids, k_hops=k_hops,
                            edge_types=edge_types, max_nodes=max_nodes)

    def execute_knn_vec(self, query_vector, k, label_filter):
        return self._record("execute_knn_vec", query_vector=query_vector, k=k, label_filter=label_filter)

    def write_temporal_edge(self, source_id, predicate, target_id, timestamp, weight=1.0, attrs=None, upsert=False):
        return self._record("write_temporal_edge", source_id=source_id, predicate=predicate,
                            target_id=target_id, timestamp=timestamp, weight=weight, attrs=attrs, upsert=upsert)

    def bulk_write_temporal_edges(self, edges, upsert=False):
        return self._record("bulk_write_temporal_edges", edges=edges, upsert=upsert)

    def execute_temporal_window_query(self, source_id, predicate, ts_start, ts_end, direction="out"):
        return self._record("execute_temporal_window_query", source_id=source_id,
                            predicate=predicate, ts_start=ts_start, ts_end=ts_end, direction=direction)

    def execute_temporal_cypher(self, source_id, predicates, ts_start, ts_end, direction, max_hops):
        return self._record("execute_temporal_cypher", source_id=source_id, predicates=predicates,
                            ts_start=ts_start, ts_end=ts_end, direction=direction, max_hops=max_hops)

    def get_temporal_aggregate(self, source_id, predicate, metric, ts_start, ts_end):
        return self._record("get_temporal_aggregate", source_id=source_id, predicate=predicate,
                            metric=metric, ts_start=ts_start, ts_end=ts_end)

    def capabilities(self):
        return dict(self._caps)

    def close(self):
        pass


class TestGraphStoreProtocol:

    def test_mock_satisfies_protocol(self):
        assert isinstance(MockGraphStore(), GraphStore)

    def test_mock_records_calls(self):
        store = MockGraphStore()
        store.execute_bfs("n1", [], 2, "out", 0)
        assert store.called_methods == ["execute_bfs"]
        assert store.last_call["source_id"] == "n1"

    def test_mock_returns_fixture(self):
        store = MockGraphStore()
        store._fixtures["execute_bfs"] = IVGResult(columns=["id"], rows=[["n2"]])
        result = store.execute_bfs("n1", [], 2, "out", 0)
        assert result.rows == [["n2"]]

    def test_mock_capabilities_override(self):
        store = MockGraphStore(capabilities_override={"native_sql": True, "ppr": False})
        assert store.capabilities()["native_sql"] is True
        assert store.capabilities()["ppr"] is False


class TestRoutingBFS:

    def _make_engine(self, store):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        cursor.description = [("col",)]
        conn.cursor.return_value = cursor
        from unittest.mock import patch
        with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
             patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
            engine = IRISGraphEngine(conn, store=store)
        return engine

    def test_bfs_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.execute_cypher(
            "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $src RETURN b.node_id",
            parameters={"src": "mesh:D003924"},
        )
        assert "execute_bfs" in store.called_methods
        assert store.last_call["source_id"] == "mesh:D003924"
        assert store.last_call["max_hops"] == 3

    def test_shortest_path_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.execute_cypher(
            "MATCH p = shortestPath((a)-[*]-(b)) WHERE a.node_id = $from AND b.node_id = $to RETURN p",
            parameters={"from": "n1", "to": "n2"},
        )
        assert "execute_shortest_path" in store.called_methods

    def test_knn_vec_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.execute_cypher(
            "CALL ivg.vector.search('Gene', 'emb', $vec, 5) YIELD node, score RETURN node, score",
            parameters={"vec": [0.1, 0.2, 0.3]},
        )
        assert "execute_knn_vec" in store.called_methods

    def test_sql_routes_to_execute_sql_when_native(self):
        store = MockGraphStore(capabilities_override={"native_sql": True})
        engine = self._make_engine(store)
        engine.execute_cypher("MATCH (n:Gene) RETURN n.node_id LIMIT 5")
        assert "execute_sql" in store.called_methods

    def test_match_routes_to_query_nodes_when_non_sql(self):
        store = MockGraphStore(capabilities_override={"native_sql": False})
        engine = self._make_engine(store)
        engine.execute_cypher("MATCH (n:Gene) RETURN n.node_id LIMIT 5")
        assert "query_nodes" in store.called_methods
        assert store.last_call.get("label_filter") == "Gene"

    def test_ppr_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.kg_PERSONALIZED_PAGERANK(seed_entities=["n1"])
        assert "execute_ppr" in store.called_methods

    def test_wcc_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.kg_WCC()
        assert "execute_wcc" in store.called_methods

    def test_subgraph_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.kg_SUBGRAPH(["n1"], k_hops=2)
        assert "execute_subgraph" in store.called_methods


class TestTemporalRouting:

    def _make_engine(self, store):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        conn.cursor.return_value = cursor
        from unittest.mock import patch
        with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
             patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
            engine = IRISGraphEngine(conn, store=store)
        return engine

    def test_temporal_write_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.create_edge_temporal("n1", "CITED", "n2", timestamp=1700000000, weight=0.9)
        assert "write_temporal_edge" in store.called_methods
        assert store.last_call["timestamp"] == 1700000000
        assert store.last_call["weight"] == 0.9

    def test_temporal_window_routes_to_store(self):
        store = MockGraphStore()
        engine = self._make_engine(store)
        engine.get_edges_in_window("n1", "CITED", start=1000000000, end=1700000000)
        assert "execute_temporal_window_query" in store.called_methods
        assert store.last_call["ts_start"] == 1000000000


class TestCapabilitiesFallback:

    def _make_engine(self, store):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        conn.cursor.return_value = cursor
        from unittest.mock import patch
        with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
             patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
            engine = IRISGraphEngine(conn, store=store)
        return engine

    def test_ppr_fallback_when_not_supported(self):
        store = MockGraphStore(capabilities_override={"native_sql": False, "ppr": False})
        engine = self._make_engine(store)
        result = engine.kg_PERSONALIZED_PAGERANK(seed_entities=["n1"])
        assert "execute_ppr" not in store.called_methods

    def test_match_uses_query_nodes_when_no_native_sql(self):
        store = MockGraphStore(capabilities_override={"native_sql": False})
        engine = self._make_engine(store)
        engine.execute_cypher("MATCH (n:Disease) RETURN n.node_id")
        assert "query_nodes" in store.called_methods


class TestIRISGraphStoreUnit:

    def _make_store(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        cursor.execute.return_value = None
        conn.cursor.return_value = cursor
        return IRISGraphStore(conn), conn, cursor

    def test_execute_sql_success(self):
        store, conn, cursor = self._make_store()
        cursor.fetchall.return_value = [("n1",)]
        cursor.description = [("node_id",)]
        result = store.execute_sql("SELECT node_id FROM Graph_KG.nodes LIMIT 1", [])
        assert result.columns == ["node_id"]
        assert result.rows == [["n1"]]

    def test_execute_sql_error_returns_ivgresult_with_error(self):
        store, conn, cursor = self._make_store()
        cursor.execute.side_effect = Exception("SQL error")
        result = store.execute_sql("BAD SQL", [])
        assert result.error is not None

    def test_execute_transaction_commits(self):
        store, conn, cursor = self._make_store()
        cursor.description = None
        store.execute_transaction(["INSERT INTO t VALUES (?)"], [["v1"]])
        conn.commit.assert_called()

    def test_execute_transaction_rollback_on_error(self):
        store, conn, cursor = self._make_store()
        cursor.execute.side_effect = [None, Exception("tx error")]
        with pytest.raises(Exception):
            store.execute_transaction(["INSERT 1", "INSERT 2"], [[], []])
        conn.rollback.assert_called()

    def test_write_nodes_commits(self):
        store, conn, cursor = self._make_store()
        cursor.execute.return_value = None
        result = store.write_nodes([{"id": "n1", "labels": ["Gene"], "properties": {"name": "TP53"}}])
        assert result.columns == ["written"]
        assert result.rows == [[1]]
        conn.commit.assert_called()

    def test_write_edges_commits(self):
        store, conn, cursor = self._make_store()
        result = store.write_edges([{"source": "n1", "predicate": "INTERACTS", "target": "n2"}])
        assert result.columns == ["written"]
        conn.commit.assert_called()

    def test_delete_nodes_empty(self):
        store, conn, cursor = self._make_store()
        result = store.delete_nodes([])
        assert result.rows == [[0]]

    def test_delete_edges_empty(self):
        store, conn, cursor = self._make_store()
        result = store.delete_edges([])
        assert result.rows == [[0]]

    def test_get_nodes_empty(self):
        store, conn, cursor = self._make_store()
        result = store.get_nodes([])
        assert result.rows == []

    def test_get_node_labels_empty(self):
        store, conn, cursor = self._make_store()
        result = store.get_node_labels([])
        assert result.columns == ["id", "labels"]

    def test_query_nodes_no_label(self):
        store, conn, cursor = self._make_store()
        cursor.fetchall.side_effect = [
            [("n1",), ("n2",)],
            [],
        ]
        result = store.query_nodes()
        assert isinstance(result.rows, list)

    def test_capabilities_returns_dict(self):
        store, conn, cursor = self._make_store()
        with MagicMock() as m:
            store._arno_available = False
            caps = store.capabilities()
        assert isinstance(caps, dict)
        assert caps["native_sql"] is True

    def test_bfs_arno_disabled_uses_objectscript(self):
        store, conn, cursor = self._make_store()
        store._arno_available = False
        from unittest.mock import patch
        with patch.object(store, "_call_classmethod", return_value='[{"id":"n2","hops":1,"pred":"R"}]'):
            result = store.execute_bfs("n1", [], 2, "out", 0)
        assert result.columns == ["id", "hops", "pred"]
        assert result.rows[0][0] == "n2"

    def test_execute_ppr_returns_empty_on_exception(self):
        store, conn, cursor = self._make_store()
        store._arno_available = False
        with MagicMock():
            store._arno_available = False
            from unittest.mock import patch
            with patch.object(store, "_call_classmethod", side_effect=Exception("no class")):
                result = store.execute_ppr(["n1"], 0.85, 20)
        assert result.columns == ["id", "score"]
        assert result.rows == []

    def test_write_temporal_edge_delegates(self):
        store, conn, cursor = self._make_store()
        from unittest.mock import patch
        with patch.object(store, "_call_classmethod", return_value="1"):
            result = store.write_temporal_edge("n1", "CITED", "n2", 1700000000)
        assert result.error is None

    def test_bulk_write_temporal_edges_empty(self):
        store, conn, cursor = self._make_store()
        result = store.bulk_write_temporal_edges([])
        assert result.rows == [[0]]

    def test_execute_temporal_window_query_returns_empty_on_error(self):
        store, conn, cursor = self._make_store()
        from unittest.mock import patch
        with patch.object(store, "_call_classmethod", side_effect=Exception("no temporal")):
            result = store.execute_temporal_window_query("n1", "CITED", 0, 9999999)
        assert result.columns == ["source", "predicate", "target", "timestamp", "weight"]

    def test_get_temporal_aggregate_returns_zero_on_error(self):
        store, conn, cursor = self._make_store()
        from unittest.mock import patch
        with patch.object(store, "_call_classmethod", side_effect=Exception("no agg")):
            result = store.get_temporal_aggregate("n1", "CITED", "count", 0, 9999999)
        assert result.rows == [[0.0]]

    def test_close_is_noop(self):
        store, conn, cursor = self._make_store()
        store.close()
