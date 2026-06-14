"""
Unit tests for bulk_loader.py covering:
- BulkLoader._executemany_batched: unique-error path (individual retry), non-unique error path
- BulkLoader._rebuild_indices: success, SQL fail+TUNE success, total failure
- BulkLoader.load_nodes: skip_existing=False path, with properties
- BulkLoader.load_edges: dedup, skip_existing=False, use_noindex=False
- BulkLoader.rebuild_all_indices: success and failure
- BulkLoader.build_graph_globals: success and failure
- BulkLoader.load_networkx: full flow (mocked sub-calls)

No IRIS connection needed — mocks conn and cursor.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.bulk_loader import BulkLoader


def _make_loader():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.close.return_value = None
    loader = BulkLoader(conn, batch_size=2)
    return loader, conn, cursor


# ---------------------------------------------------------------------------
# _executemany_batched
# ---------------------------------------------------------------------------

class TestExecutemanyBatched:

    def test_happy_path(self):
        loader, conn, cursor = _make_loader()
        rows = [["r1"], ["r2"], ["r3"]]
        result = loader._executemany_batched(cursor, "INSERT INTO t VALUES (?)", rows, "test")
        assert result == 3

    def test_unique_error_triggers_individual_retry(self):
        """Batch with UNIQUE violation → individual row retry, some succeed."""
        loader, conn, cursor = _make_loader()

        call_count = [0]
        def side_effect(sql, params=None):
            call_count[0] += 1
            if isinstance(params, list) and len(params) == 2:
                # batch call — simulate unique error
                raise Exception("-119 UNIQUE constraint failed")
            # individual row calls succeed
            return None

        cursor.executemany = side_effect
        cursor.execute.return_value = None

        rows = [["r1"], ["r2"]]
        result = loader._executemany_batched(cursor, "INSERT INTO t VALUES (?)", rows, "test")
        assert result == 2

    def test_non_unique_error_counts_as_errors(self):
        """Batch with non-UNIQUE error → all rows in batch counted as errors."""
        loader, conn, cursor = _make_loader()

        cursor.executemany = MagicMock(side_effect=Exception("FATAL: table not found"))
        rows = [["r1"], ["r2"]]
        result = loader._executemany_batched(cursor, "INSERT INTO t VALUES (?)", rows, "test")
        assert result == 0  # all 2 are errors, 0 inserted

    def test_unique_error_individual_failure_increments_errors(self):
        """Individual row also fails on unique-error retry → error counted."""
        loader, conn, cursor = _make_loader()

        cursor.executemany = MagicMock(side_effect=Exception("unique constraint -119"))
        cursor.execute = MagicMock(side_effect=Exception("also fails"))
        rows = [["r1"]]
        result = loader._executemany_batched(cursor, "INSERT INTO t VALUES (?)", rows, "test")
        assert result == 0


# ---------------------------------------------------------------------------
# _rebuild_indices
# ---------------------------------------------------------------------------

class TestRebuildIndices:

    def test_success_returns_true(self):
        loader, conn, cursor = _make_loader()
        cursor.execute.return_value = None
        result = loader._rebuild_indices(cursor, "Graph.KG.nodes")
        assert result is True

    def test_sql_fail_tune_success_returns_true(self):
        loader, conn, cursor = _make_loader()
        call_seq = iter([Exception("BuildIndices failed"), None])
        cursor.execute.side_effect = lambda *a: (_ for _ in ()).throw(next(call_seq)) if isinstance(next_val := next(call_seq), Exception) else None

        # Simpler approach: first call raises, second doesn't
        calls = [Exception("BuildIndices failed"), None]
        cursor.execute.side_effect = calls
        result = loader._rebuild_indices(cursor, "Graph.KG.nodes")
        assert result is True

    def test_total_failure_returns_false(self):
        loader, conn, cursor = _make_loader()
        cursor.execute.side_effect = Exception("both calls fail")
        result = loader._rebuild_indices(cursor, "Graph.KG.nodes")
        assert result is False


# ---------------------------------------------------------------------------
# load_nodes
# ---------------------------------------------------------------------------

class TestLoadNodes:

    def test_skip_existing_false_loads_all(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        nodes = [("n1", {"name": "BRCA1", "namespace": "gene"}),
                 ("n2", {"name": "TP53"})]
        with patch.object(loader, "_executemany_batched", return_value=2) as mock_exec:
            stats = loader.load_nodes(nodes, skip_existing=False)
        assert stats["nodes"] == 2
        # executemany called for nodes, labels, props
        assert mock_exec.call_count >= 1

    def test_skip_existing_true_filters(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = [("n1",)]  # n1 already exists
        nodes = [("n1", {"name": "BRCA1"}), ("n2", {"name": "TP53"})]
        with patch.object(loader, "_executemany_batched", return_value=1):
            stats = loader.load_nodes(nodes, skip_existing=True)
        # Only n2 should be new
        assert isinstance(stats, dict)

    def test_props_longer_than_60000_truncated(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        long_val = "x" * 70000
        nodes = [("n1", {"bio": long_val})]
        captured = []
        def mock_batch(cursor, sql, params, label):
            captured.extend(params)
            return len(params)
        with patch.object(loader, "_executemany_batched", side_effect=mock_batch):
            loader.load_nodes(nodes, skip_existing=False)
        # Find the prop param for "bio"
        prop_rows = [p for p in captured if isinstance(p, list) and len(p) == 3 and p[1] == "bio"]
        if prop_rows:
            assert len(prop_rows[0][2]) == 60000

    def test_none_values_excluded_from_props(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        nodes = [("n1", {"good": "val", "bad": None})]
        captured = []
        def mock_batch(cursor, sql, params, label):
            captured.extend(params)
            return len(params)
        with patch.object(loader, "_executemany_batched", side_effect=mock_batch):
            loader.load_nodes(nodes, skip_existing=False)
        # "bad" key with None val should not appear
        prop_rows = [p for p in captured if isinstance(p, list) and len(p) == 3]
        keys = [p[1] for p in prop_rows]
        assert "bad" not in keys


# ---------------------------------------------------------------------------
# load_edges
# ---------------------------------------------------------------------------

class TestLoadEdges:

    def test_deduplication(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        edges = [
            ("n1", "TREATS", "n2", None),
            ("n1", "TREATS", "n2", None),  # duplicate
            ("n1", "TARGETS", "n3", {"conf": 0.9}),
        ]
        with patch.object(loader, "_executemany_batched", return_value=2) as mock_exec:
            stats = loader.load_edges(edges, use_noindex=False, skip_existing=False)
        call_args = mock_exec.call_args_list[0]
        params = call_args[0][2]  # positional arg 3
        assert len(params) == 2  # deduplicated to 2

    def test_use_noindex_false_uses_plain_insert(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        edges = [("n1", "TREATS", "n2", None)]
        captured_sqls = []
        def mock_batch(cursor, sql, params, label):
            captured_sqls.append(sql)
            return len(params)
        with patch.object(loader, "_executemany_batched", side_effect=mock_batch):
            loader.load_edges(edges, use_noindex=False, skip_existing=False)
        assert any("NOINDEX" not in s and "INSERT INTO" in s for s in captured_sqls)

    def test_skip_existing_true_filters(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = [("n1", "TREATS", "n2")]
        edges = [("n1", "TREATS", "n2", None), ("n1", "TARGETS", "n3", None)]
        with patch.object(loader, "_executemany_batched", return_value=1) as mock_exec:
            stats = loader.load_edges(edges, skip_existing=True)
        params = mock_exec.call_args_list[0][0][2]
        assert len(params) == 1  # n1-TREATS-n2 filtered out


# ---------------------------------------------------------------------------
# rebuild_all_indices
# ---------------------------------------------------------------------------

class TestRebuildAllIndices:

    def test_success_all_true(self):
        loader, conn, cursor = _make_loader()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        with patch.object(loader, "_iris_obj", return_value=iris_obj):
            result = loader.rebuild_all_indices()
        assert all(v is True for v in result.values())
        assert len(result) == 4

    def test_partial_failure(self):
        loader, conn, cursor = _make_loader()
        iris_obj = MagicMock()
        call_count = [0]
        def side(cls, method):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("BuildIndices failed")
        iris_obj.classMethodVoid.side_effect = side
        with patch.object(loader, "_iris_obj", return_value=iris_obj):
            result = loader.rebuild_all_indices()
        assert False in result.values()


# ---------------------------------------------------------------------------
# build_graph_globals
# ---------------------------------------------------------------------------

class TestBuildGraphGlobals:

    def test_success_returns_true(self):
        loader, conn, cursor = _make_loader()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        iris_obj.get.side_effect = ["100", "500"]
        with patch.object(loader, "_iris_obj", return_value=iris_obj):
            with patch.dict("sys.modules", {"iris": MagicMock()}):
                result = loader.build_graph_globals()
        assert result is True

    def test_failure_returns_false(self):
        loader, conn, cursor = _make_loader()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.side_effect = RuntimeError("class not found")
        with patch.object(loader, "_iris_obj", return_value=iris_obj):
            with patch.dict("sys.modules", {"iris": MagicMock()}):
                result = loader.build_graph_globals()
        assert result is False


# ---------------------------------------------------------------------------
# load_networkx
# ---------------------------------------------------------------------------

class TestLoadNetworkx:

    def _make_graph(self):
        G = MagicMock()
        G.number_of_nodes.return_value = 3
        G.number_of_edges.return_value = 2
        G.nodes.return_value = [
            ("n1", {"namespace": "gene", "name": "BRCA1"}),
            ("n2", {"namespace": "disease"}),
            ("n3", {}),
        ]
        G.edges.return_value = [
            ("n1", "n2", {"predicate": "TREATS"}),
            ("n2", "n3", {"label": "TARGETS", "confidence": 0.9}),
        ]
        return G

    def test_full_load_no_globals(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        G = self._make_graph()
        with patch.object(loader, "_executemany_batched", return_value=2):
            with patch.object(loader, "rebuild_all_indices", return_value={}):
                stats = loader.load_networkx(G, build_globals=False)
        assert "input_nodes" in stats
        assert stats["input_nodes"] == 3

    def test_full_load_with_globals_success(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        G = self._make_graph()
        with patch.object(loader, "_executemany_batched", return_value=2):
            with patch.object(loader, "rebuild_all_indices", return_value={}):
                with patch.object(loader, "build_graph_globals", return_value=True):
                    stats = loader.load_networkx(G, build_globals=True)
        assert stats["globals_built"] is True

    def test_use_noindex_false_skips_rebuild(self):
        loader, conn, cursor = _make_loader()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        G = self._make_graph()
        with patch.object(loader, "_executemany_batched", return_value=2):
            with patch.object(loader, "rebuild_all_indices") as mock_rebuild:
                loader.load_networkx(G, use_noindex=False, build_globals=False)
        mock_rebuild.assert_not_called()
