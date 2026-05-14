"""Unit tests for models.py, fusion.py, and schema.py — all pure Python."""
import json
from unittest.mock import MagicMock, call, patch


class TestModels:

    def test_subgraph_data_defaults(self):
        from iris_vector_graph.models import SubgraphData
        s = SubgraphData()
        assert s.nodes == []
        assert s.edges == []
        assert s.node_properties == {}
        assert s.node_labels == {}
        assert s.node_embeddings == {}
        assert s.seed_ids == []

    def test_subgraph_data_with_values(self):
        from iris_vector_graph.models import SubgraphData
        s = SubgraphData(
            nodes=["n1", "n2"],
            edges=[("n1", "P", "n2")],
            node_properties={"n1": {"k": "v"}},
            node_labels={"n1": ["Gene"]},
            node_embeddings={"n1": [0.1, 0.2]},
            seed_ids=["n1"],
        )
        assert len(s.nodes) == 2
        assert s.edges[0] == ("n1", "P", "n2")
        assert s.node_properties["n1"]["k"] == "v"

    def test_ppr_guided_subgraph_data_defaults(self):
        from iris_vector_graph.models import PprGuidedSubgraphData
        p = PprGuidedSubgraphData()
        assert p.nodes == []
        assert p.edges == []
        assert p.ppr_scores == []
        assert p.seed_ids == []
        assert p.nodes_before_pruning == 0
        assert p.nodes_after_pruning == 0

    def test_ppr_guided_subgraph_data_with_values(self):
        from iris_vector_graph.models import PprGuidedSubgraphData
        p = PprGuidedSubgraphData(
            nodes=["n1"],
            ppr_scores=[("n1", 0.9)],
            nodes_before_pruning=100,
            nodes_after_pruning=5,
        )
        assert p.nodes_before_pruning == 100
        assert p.nodes_after_pruning == 5
        assert p.ppr_scores[0] == ("n1", 0.9)


class TestRRFFusion:

    def test_fuse_results_empty(self):
        from iris_vector_graph.fusion import RRFFusion
        result = RRFFusion.fuse_results([])
        assert result == []

    def test_fuse_results_single_list(self):
        from iris_vector_graph.fusion import RRFFusion
        ranked = [("n1", 0.9), ("n2", 0.7)]
        result = RRFFusion.fuse_results([ranked])
        ids = [r[0] for r in result]
        assert "n1" in ids
        assert "n2" in ids

    def test_fuse_results_two_lists(self):
        from iris_vector_graph.fusion import RRFFusion
        vec = [("n1", 0.9), ("n2", 0.7), ("n3", 0.5)]
        txt = [("n2", 0.8), ("n1", 0.6), ("n4", 0.4)]
        result = RRFFusion.fuse_results([vec, txt])
        ids = [r[0] for r in result]
        assert "n1" in ids
        assert "n2" in ids

    def test_fuse_results_top_item_ranks_first(self):
        from iris_vector_graph.fusion import RRFFusion
        vec = [("n1", 1.0), ("n2", 0.5)]
        txt = [("n1", 1.0), ("n2", 0.5)]
        result = RRFFusion.fuse_results([vec, txt])
        assert result[0][0] == "n1"

    def test_fuse_results_scores_are_floats(self):
        from iris_vector_graph.fusion import RRFFusion
        result = RRFFusion.fuse_results([[("n1", 0.9)]])
        assert isinstance(result[0][1], float)

    def test_weighted_fusion_equal_weights(self):
        from iris_vector_graph.fusion import RRFFusion
        lists = [[("n1", 0.9), ("n2", 0.5)], [("n1", 0.8), ("n3", 0.6)]]
        result = RRFFusion.weighted_fusion(lists, [1.0, 1.0])
        assert len(result) >= 1
        assert result[0][0] == "n1"

    def test_weighted_fusion_zero_weight_excludes(self):
        from iris_vector_graph.fusion import RRFFusion
        lists = [[("n1", 0.9)], [("n2", 0.8)]]
        result = RRFFusion.weighted_fusion(lists, [1.0, 0.0])
        ids = [r[0] for r in result]
        assert "n1" in ids

    def test_multi_modal_search_raises_without_query(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        import pytest
        eng = MagicMock()
        f = HybridSearchFusion(eng)
        with pytest.raises(ValueError):
            f.multi_modal_search()

    def test_multi_modal_search_vector_only(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        eng = MagicMock()
        eng.kg_KNN_VEC.return_value = [("n1", 0.9), ("n2", 0.7)]
        eng.kg_NEIGHBORS.return_value = []
        f = HybridSearchFusion(eng)
        result = f.multi_modal_search(query_vector="[0.1,0.2]", k=5)
        assert isinstance(result, list)

    def test_multi_modal_search_text_only(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        eng = MagicMock()
        eng.kg_TXT.return_value = [("n1", 0.8)]
        eng.kg_NEIGHBORS.return_value = []
        f = HybridSearchFusion(eng)
        result = f.multi_modal_search(query_text="insulin", k=5)
        assert isinstance(result, list)

    def test_multi_modal_search_both_modalities(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        eng = MagicMock()
        eng.kg_KNN_VEC.return_value = [("n1", 0.9)]
        eng.kg_TXT.return_value = [("n1", 0.8), ("n2", 0.6)]
        eng.kg_NEIGHBORS.return_value = [("n3", 0.5)]
        f = HybridSearchFusion(eng)
        result = f.multi_modal_search(query_vector="[0.1]", query_text="test", k=3)
        assert isinstance(result, list)

    def test_multi_modal_search_weighted_fusion(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        eng = MagicMock()
        eng.kg_KNN_VEC.return_value = [("n1", 0.9)]
        eng.kg_TXT.return_value = [("n1", 0.8)]
        eng.kg_NEIGHBORS.return_value = []
        f = HybridSearchFusion(eng)
        result = f.multi_modal_search(query_vector="[0.1]", query_text="x",
                                       fusion_method="weighted", k=3)
        assert isinstance(result, list)

    def test_adaptive_search_delegates_to_engine(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        eng = MagicMock()
        eng.kg_TXT.return_value = [("n1", 0.9)]
        eng.kg_KNN_VEC.return_value = []
        eng.kg_NEIGHBORS.return_value = []
        f = HybridSearchFusion(eng)
        result = f.adaptive_search("test query", k=5)
        assert isinstance(result, list)


class TestBulkLoader:

    def _make_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_init(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, _ = self._make_conn()
        bl = BulkLoader(conn, schema="TestSchema", batch_size=1000)
        assert bl.schema == "TestSchema"
        assert bl.batch_size == 1000

    def test_table_helper(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, _ = self._make_conn()
        bl = BulkLoader(conn)
        assert bl._table("nodes") == "Graph_KG.nodes"

    def test_executemany_batched_empty(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        bl = BulkLoader(conn, batch_size=100)
        inserted = bl._executemany_batched(cursor, "INSERT INTO t VALUES (?)", [], label="test")
        assert inserted == 0

    def test_executemany_batched_single_batch(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        bl = BulkLoader(conn, batch_size=100)
        params = [["a"], ["b"], ["c"]]
        inserted = bl._executemany_batched(cursor, "INSERT INTO t VALUES (?)", params, label="test")
        assert inserted == 3

    def test_executemany_batched_multiple_batches(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        bl = BulkLoader(conn, batch_size=2)
        params = [["a"], ["b"], ["c"], ["d"], ["e"]]
        inserted = bl._executemany_batched(cursor, "INSERT INTO t VALUES (?)", params, label="test")
        assert inserted == 5

    def test_executemany_batched_handles_unique_error(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        call_count = [0]
        def executemany_side_effect(sql, batch):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("failed unique check")
        cursor.executemany.side_effect = executemany_side_effect
        bl = BulkLoader(conn, batch_size=10)
        params = [["a"], ["b"]]
        bl._executemany_batched(cursor, "INSERT INTO t VALUES (?)", params, label="test")

    def test_stats_property(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, _ = self._make_conn()
        bl = BulkLoader(conn)
        assert isinstance(bl._stats, dict)

    def test_load_networkx_empty_graph(self):
        import networkx as nx
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        G = nx.DiGraph()
        bl = BulkLoader(conn, batch_size=100)
        with patch.object(bl, '_executemany_batched', return_value=0) as mock_batch:
            with patch.object(bl, '_rebuild_indices', return_value=None):
                stats = bl.load_networkx(G)
        assert isinstance(stats, dict)

    def test_load_networkx_with_nodes_and_edges(self):
        import networkx as nx
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        G = nx.DiGraph()
        G.add_node("n1", label="Gene")
        G.add_node("n2", label="Disease")
        G.add_edge("n1", "n2", predicate="CAUSES")
        bl = BulkLoader(conn, batch_size=100)
        with patch.object(bl, '_executemany_batched', return_value=2) as mock_batch:
            with patch.object(bl, '_rebuild_indices', return_value=None):
                stats = bl.load_networkx(G)
        assert "nodes" in stats or "edges" in stats or isinstance(stats, dict)

    def test_rebuild_indices_calls_build_indices(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        bl = BulkLoader(conn)
        bl._rebuild_indices(cursor, "Graph_KG.nodes")
        assert cursor.execute.called

    def test_load_nodes_empty(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        bl = BulkLoader(conn, batch_size=100)
        stats = bl.load_nodes([], label_attr="namespace")
        assert isinstance(stats, dict)
        assert stats.get("nodes", 0) == 0

    def test_load_nodes_with_data(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        cursor.fetchone.return_value = None
        bl = BulkLoader(conn, batch_size=100)
        nodes = [("n1", {"namespace": "Gene", "name": "TP53"}), ("n2", {"namespace": "Disease"})]
        stats = bl.load_nodes(nodes, label_attr="namespace")
        assert isinstance(stats, dict)

    def test_load_edges_empty(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        bl = BulkLoader(conn, batch_size=100)
        stats = bl.load_edges([])
        assert isinstance(stats, dict)

    def test_load_edges_with_data(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        bl = BulkLoader(conn, batch_size=100)
        edges = [("n1", "CAUSES", "n2", {}), ("n2", "TREATS", "n3", None)]
        stats = bl.load_edges(edges)
        assert isinstance(stats, dict)

    def test_load_networkx_empty_graph(self):
        import networkx as nx
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        G = nx.DiGraph()
        bl = BulkLoader(conn, batch_size=100)
        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: MagicMock() if name == "iris" else __import__(name, *a, **kw)):
            stats = bl.load_networkx(G, build_globals=False)
        assert isinstance(stats, dict)
        assert stats.get("input_nodes", 0) == 0

    def test_load_networkx_with_nodes_and_edges(self):
        import networkx as nx
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, cursor = self._make_conn()
        cursor.execute.return_value = None
        cursor.fetchone.return_value = None
        G = nx.DiGraph()
        G.add_node("n1", namespace="Gene")
        G.add_node("n2", namespace="Disease")
        G.add_edge("n1", "n2", predicate="CAUSES")
        bl = BulkLoader(conn, batch_size=100)
        with patch("builtins.__import__", side_effect=lambda name, *a, **kw: MagicMock() if name == "iris" else __import__(name, *a, **kw)):
            stats = bl.load_networkx(G, build_globals=False)
        assert isinstance(stats, dict)
        assert stats.get("input_nodes") == 2
        assert stats.get("input_edges") == 1

    def test_load_obo_nonexistent_file(self):
        from iris_vector_graph.bulk_loader import BulkLoader
        conn, _ = self._make_conn()
        bl = BulkLoader(conn)
        import pytest
        with pytest.raises((FileNotFoundError, Exception)):
            bl.load_obo("/nonexistent/path.obo")


class TestSchemaModule:

    def _make_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        return cursor

    def test_get_base_schema_sql_returns_string(self):
        from iris_vector_graph.schema import GraphSchema
        sql = GraphSchema.get_base_schema_sql()
        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql or "CREATE" in sql

    def test_get_base_schema_sql_with_dimension(self):
        from iris_vector_graph.schema import GraphSchema
        sql = GraphSchema.get_base_schema_sql(embedding_dimension=384)
        assert "384" in sql

    def test_get_indexes_sql_returns_string(self):
        from iris_vector_graph.schema import GraphSchema
        sql = GraphSchema.get_indexes_sql()
        assert isinstance(sql, str)

    def test_get_bulk_insert_sql_nodes(self):
        from iris_vector_graph.schema import GraphSchema
        sql = GraphSchema.get_bulk_insert_sql("nodes")
        assert isinstance(sql, str)
        assert "INSERT" in sql.upper() or "node" in sql.lower()

    def test_validate_schema_returns_dict(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.fetchone.return_value = (1,)
        cursor.execute.return_value = None
        result = GraphSchema.validate_schema(cursor)
        assert isinstance(result, dict)

    def test_ensure_indexes_returns_dict(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.execute.return_value = None
        result = GraphSchema.ensure_indexes(cursor)
        assert isinstance(result, dict)

    def test_disable_indexes_returns_dict(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.execute.return_value = None
        result = GraphSchema.disable_indexes(cursor)
        assert isinstance(result, dict)

    def test_add_graph_id_column_true_on_error(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.execute.side_effect = Exception("already exists")
        result = GraphSchema.add_graph_id_column(cursor)
        assert result is True

    def test_update_spo_unique_constraint_returns_bool(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.execute.return_value = None
        result = GraphSchema.update_spo_unique_constraint(cursor)
        assert isinstance(result, bool)

    def test_upgrade_val_column_returns_bool(self):
        from iris_vector_graph.schema import GraphSchema
        cursor = self._make_cursor()
        cursor.execute.return_value = None
        result = GraphSchema.upgrade_val_column(cursor)
        assert isinstance(result, bool)
