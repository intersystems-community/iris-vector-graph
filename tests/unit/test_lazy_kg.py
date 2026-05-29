"""Unit tests for LazyKG adapter (Spec 163 T022a / FR-025).

Tests use MagicMock to stand in for `iris.createIRIS(conn)` so they can
verify caching behavior (call counts) without needing a live IRIS.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_iris():
    """Create a MagicMock that simulates iris.createIRIS() Native API."""
    return MagicMock()


@pytest.fixture
def lazy_kg(mock_iris):
    """Construct a LazyKG with the mocked IRIS instance attached."""
    with patch("iris.createIRIS", return_value=mock_iris):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        conn = MagicMock()
        return LazyKG(conn, include_sinks=True), mock_iris


class TestIterNodes:
    def test_walks_deg_global(self, lazy_kg):
        lkg, mock = lazy_kg

        def fake_next(direction, *path):
            if path[:2] == ("^KG", "deg"):
                cur = path[-1] if path else ""
                seq = ["", "alice", "bob", "carol", ""]
                idx = seq.index(cur)
                return seq[idx + 1] if idx + 1 < len(seq) else ""
            if path[:3] == ("^KG", "in", 0):
                return ""
            return ""
        mock.nextSubscript.side_effect = fake_next

        nodes = list(lkg.iter_nodes())
        assert nodes == ["alice", "bob", "carol"]

    def test_iter_nodes_caches_on_second_call(self, lazy_kg):
        lkg, mock = lazy_kg
        call_count = [0]

        def fake_next(direction, *path):
            call_count[0] += 1
            if path[:2] == ("^KG", "deg") and path[-1] == "":
                return "n1"
            if path[:2] == ("^KG", "deg") and path[-1] == "n1":
                return ""
            return ""
        mock.nextSubscript.side_effect = fake_next

        first_pass = list(lkg.iter_nodes())
        first_count = call_count[0]
        second_pass = list(lkg.iter_nodes())
        second_count = call_count[0]

        assert first_pass == second_pass
        assert second_count == first_count, "Second call must hit cache"


class TestOutNeighbors:
    def test_walks_out_global_dedups_predicates(self, lazy_kg):
        lkg, mock = lazy_kg

        def fake_next(direction, *path):
            if len(path) == 4 and path[:3] == ("^KG", "out", 0) and path[3] == "alice":
                return ""
            if path == ("^KG", "out", 0, "alice", ""):
                return "KNOWS"
            if path == ("^KG", "out", 0, "alice", "KNOWS"):
                return "CITES"
            if path == ("^KG", "out", 0, "alice", "CITES"):
                return ""
            if path == ("^KG", "out", 0, "alice", "KNOWS", ""):
                return "bob"
            if path == ("^KG", "out", 0, "alice", "KNOWS", "bob"):
                return ""
            if path == ("^KG", "out", 0, "alice", "CITES", ""):
                return "bob"
            if path == ("^KG", "out", 0, "alice", "CITES", "bob"):
                return ""
            return ""
        mock.nextSubscript.side_effect = fake_next

        neighbors = lkg.out_neighbors("alice")
        assert neighbors == ["bob"], "Multi-predicate same-target should dedup to one"

    def test_caches_first_call(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.nextSubscript.return_value = ""

        first = lkg.out_neighbors("alice")
        call_count_after_first = mock.nextSubscript.call_count

        second = lkg.out_neighbors("alice")
        assert second == first
        assert mock.nextSubscript.call_count == call_count_after_first, \
            "Cached node should not re-walk IRIS globals"


class TestDegree:
    def test_degree_uses_deg_global(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "5"

        deg = lkg.degree("alice")
        assert deg == 5
        mock.get.assert_called_with("^KG", "deg", "alice")

    def test_degree_returns_zero_for_missing(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = None

        assert lkg.degree("missing") == 0

    def test_degree_caches(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "3"

        first = lkg.degree("alice")
        second = lkg.degree("alice")
        assert first == second == 3
        assert mock.get.call_count == 1, "Cached degree should not re-fetch"


class TestDegreeForPredicate:
    def test_degree_for_predicate_uses_degp_global(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "2"

        deg = lkg.degree_for_predicate("alice", "CITES")
        assert deg == 2
        mock.get.assert_called_with("^KG", "degp", "alice", "CITES")

    def test_degree_for_predicate_caches_per_pair(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "7"

        lkg.degree_for_predicate("alice", "CITES")
        lkg.degree_for_predicate("alice", "CITES")
        lkg.degree_for_predicate("alice", "MENTIONS")

        assert mock.get.call_count == 2, \
            "Same (node, pred) should cache; different pred fetches separately"


class TestClearCache:
    def test_clear_cache_resets_state(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "5"

        lkg.degree("alice")
        assert lkg.cache_stats()["degree_cached_nodes"] == 1

        lkg.clear_cache()
        stats = lkg.cache_stats()
        assert stats["degree_cached_nodes"] == 0
        assert stats["out_cached_nodes"] == 0
        assert stats["in_cached_nodes"] == 0
        assert stats["nodes_enumerated"] is False


class TestCacheStats:
    def test_initial_stats_zero(self, lazy_kg):
        lkg, _ = lazy_kg
        stats = lkg.cache_stats()
        assert stats == {
            "out_cached_nodes": 0,
            "in_cached_nodes": 0,
            "degree_cached_nodes": 0,
            "degp_cached_pairs": 0,
            "nodes_enumerated": False,
            "total_nodes_known": 0,
        }

    def test_stats_increment_on_use(self, lazy_kg):
        lkg, mock = lazy_kg
        mock.get.return_value = "1"
        mock.nextSubscript.return_value = ""

        lkg.out_neighbors("alice")
        lkg.in_neighbors("bob")
        lkg.degree("carol")
        lkg.degree_for_predicate("dave", "EDGE")

        stats = lkg.cache_stats()
        assert stats["out_cached_nodes"] == 1
        assert stats["in_cached_nodes"] == 1
        assert stats["degree_cached_nodes"] == 1
        assert stats["degp_cached_pairs"] == 1
