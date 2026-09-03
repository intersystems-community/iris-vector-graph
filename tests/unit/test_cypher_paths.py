"""Unit tests for iris_vector_graph.cypher.algorithms.paths — no IRIS required."""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.algorithms.paths import (
    find_all_paths,
    find_shortest_path_bfs,
    generate_batch_neighbors_sql,
    generate_neighbors_sql,
)


class TestGenerateNeighborsSql:
    def test_outgoing(self):
        sql = generate_neighbors_sql("outgoing")
        assert "o_id AS neighbor" in sql
        assert "s = ?" in sql

    def test_incoming(self):
        sql = generate_neighbors_sql("incoming")
        assert "s AS neighbor" in sql
        assert "o_id = ?" in sql

    def test_both(self):
        sql = generate_neighbors_sql("both")
        assert "UNION" in sql
        assert "o_id AS neighbor" in sql
        assert "s AS neighbor" in sql

    def test_default_is_outgoing(self):
        assert generate_neighbors_sql() == generate_neighbors_sql("outgoing")


class TestGenerateBatchNeighborsSql:
    def test_outgoing_single(self):
        sql = generate_batch_neighbors_sql(1, "outgoing")
        assert "IN (?)" in sql

    def test_outgoing_multi(self):
        sql = generate_batch_neighbors_sql(3, "outgoing")
        assert "IN (?, ?, ?)" in sql

    def test_incoming(self):
        sql = generate_batch_neighbors_sql(2, "incoming")
        assert "o_id IN (?, ?)" in sql

    def test_both(self):
        sql = generate_batch_neighbors_sql(2, "both")
        assert "UNION" in sql

    def test_default_direction(self):
        assert generate_batch_neighbors_sql(1) == generate_batch_neighbors_sql(1, "outgoing")


class TestFindShortestPathBfs:
    def _cursor(self, rows_by_call):
        """cursor.fetchall() returns successive lists from rows_by_call."""
        cursor = MagicMock()
        cursor.fetchall.side_effect = list(rows_by_call)
        return cursor

    def test_same_node_returns_zero_hop(self):
        cursor = MagicMock()
        result = find_shortest_path_bfs(cursor, "a", "a")
        assert result == [{"path": ["a"], "depth": 0, "relationships": []}]
        cursor.execute.assert_not_called()

    def test_direct_neighbor(self):
        # a → b
        cursor = self._cursor([[("b", "a", "REL")]])
        result = find_shortest_path_bfs(cursor, "a", "b")
        assert len(result) == 1
        assert result[0]["path"] == ["a", "b"]
        assert result[0]["depth"] == 1
        assert result[0]["relationships"] == ["REL"]

    def test_two_hop_path(self):
        # a → c → b
        cursor = self._cursor(
            [
                [("c", "a", "R1")],  # depth 1: a's neighbours
                [("b", "c", "R2")],  # depth 2: c's neighbours
            ]
        )
        result = find_shortest_path_bfs(cursor, "a", "b")
        assert result[0]["path"] == ["a", "c", "b"]
        assert result[0]["depth"] == 2

    def test_no_path_returns_empty(self):
        cursor = self._cursor([[], []])
        result = find_shortest_path_bfs(cursor, "a", "z", max_hops=2)
        assert result == []

    def test_all_paths_finds_multiple(self):
        # a → b (direct) and a → c → b
        cursor = self._cursor(
            [
                [("b", "a", "R1"), ("c", "a", "R2")],  # depth 1
            ]
        )
        result = find_shortest_path_bfs(cursor, "a", "b", all_paths=True)
        paths = [r["path"] for r in result]
        assert ["a", "b"] in paths

    def test_both_direction_doubles_params(self):
        cursor = self._cursor([[("b", "a", "R")]])
        find_shortest_path_bfs(cursor, "a", "b", direction="both")
        args = cursor.execute.call_args[0][1]
        # direction=both passes frontier + frontier
        assert len(args) == 2

    def test_max_hops_respected(self):
        # Return a neighbour at every depth but never the target
        cursor = MagicMock()
        call_count = [0]

        def _fetchall():
            call_count[0] += 1
            return [(f"n{call_count[0]}", f"n{call_count[0]-1}", "R")]

        cursor.fetchall.side_effect = _fetchall
        result = find_shortest_path_bfs(cursor, "n0", "target", max_hops=3)
        assert result == []
        assert cursor.execute.call_count == 3


class TestFindAllPaths:
    def _cursor_for_graph(self, adjacency):
        """cursor returns rows from adjacency[current_node]."""
        cursor = MagicMock()

        def _execute(sql, params):
            node = params[0] if params else None
            cursor._last_node = node

        def _fetchall():
            return [(nbr, cursor._last_node, "R") for nbr in adjacency.get(cursor._last_node, [])]

        cursor.execute.side_effect = _execute
        cursor.fetchall.side_effect = _fetchall
        return cursor

    def test_direct_path(self):
        # a → b
        cursor = self._cursor_for_graph({"a": ["b"], "b": []})
        result = find_all_paths(cursor, "a", "b", min_hops=1, max_hops=3)
        paths = [r["path"] for r in result]
        assert ["a", "b"] in paths

    def test_no_path_returns_empty(self):
        cursor = self._cursor_for_graph({"a": ["c"], "c": []})
        result = find_all_paths(cursor, "a", "b", max_hops=2)
        assert result == []

    def test_min_hops_filters_short(self):
        # a → b exists but min_hops=2 should exclude it
        cursor = self._cursor_for_graph({"a": ["b"], "b": []})
        result = find_all_paths(cursor, "a", "b", min_hops=2, max_hops=3)
        assert result == []

    def test_cycle_avoidance(self):
        # a → b → a loop — should not infinite recurse
        cursor = self._cursor_for_graph({"a": ["b"], "b": ["a", "c"], "c": []})
        result = find_all_paths(cursor, "a", "c", max_hops=3)
        paths = [r["path"] for r in result]
        assert any("c" in p for p in paths)

    def test_both_direction_doubles_params(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        find_all_paths(cursor, "a", "b", direction="both", max_hops=1)
        if cursor.execute.called:
            args = cursor.execute.call_args[0][1]
            assert len(args) == 2
