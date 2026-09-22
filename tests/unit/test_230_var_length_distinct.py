"""Spec 230 — `RETURN DISTINCT` on a var-length path actually deduplicates.

Found by the T074 gate. `MATCH (a {node_id:$src})-[:KNOWS*1..2]-(b) RETURN DISTINCT
b.node_id LIMIT 20` translates to `SELECT DISTINCT n1.node_id ... FETCH FIRST 20 ROWS
ONLY`, but the ID-bound var-length route never runs that statement: it calls BFS and
returns the store's own `(id, hops, pred)` rows straight through. BFS reports one row
per reached edge, so an undirected walk reaches the same node at two hops and by two
predicates and the caller gets it twice — 16 rows for 11 nodes on a 15-node chain,
with `DISTINCT` in the query the caller wrote.

The cap compounds it: `max_results` goes to BFS, which truncates *before* anything
deduplicates, so `LIMIT 20` could answer 20 raw hits holding 11 nodes. Under `DISTINCT`
the cap therefore has to be applied after the dedupe, not inside BFS.

Deduping keeps the first row for an id, which is its shortest hop: BFS emits in hop
order, so `hops` stays meaningful for the row that survives.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.result import IVGResult


class _RecordingStore:
    """A store whose BFS reports the same node more than once, as a real one does."""

    _schema_prefix = "Graph_KG"

    def __init__(self, rows):
        self._rows = rows
        self.conn = MagicMock()
        self.calls: list = []

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results, *, graph=None):
        self.calls.append(
            {
                "source_id": source_id,
                "max_hops": max_hops,
                "direction": direction,
                "max_results": max_results,
                "graph": graph,
            }
        )
        rows = self._rows[:max_results] if max_results else self._rows
        return IVGResult(columns=["id", "hops", "pred"], rows=[list(r) for r in rows])

    def get_nodes(self, node_ids, prop_keys=None):
        return IVGResult(columns=["node_id", "labels"], rows=[[nid, "[]"] for nid in node_ids])


class _FakeSQLQuery:
    def __init__(self, sql):
        self.sql = sql
        self.parameters = [["a0"]]
        self.column_name_map = {}
        self.query_metadata = None
        self.graph_context = None
        self.var_length_paths = [
            {
                "source_var": "a",
                "source_alias": "n0",
                "target_var": "b",
                "target_alias": "n1",
                "rel_var": None,
                "types": ["KNOWS"],
                "direction": "both",
                "min_hops": 1,
                "max_hops": 2,
                "shortest": False,
                "all_shortest": False,
                "src_id_param": "$src",
                "dst_id_param": None,
                "return_path_funcs": [],
                "properties": {},
                "source_labels": [],
                "target_labels": [],
                "optional": False,
            }
        ]


def _engine(rows):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = _RecordingStore(rows)
    engine._nkg_dirty = False
    return engine


# One node reached twice: once directly, once back over the undirected second hop.
_DUPLICATED = [
    ["n1", 1, "KNOWS"],
    ["n2", 1, "KNOWS"],
    ["n1", 2, "KNOWS"],
    ["n3", 2, "KNOWS"],
    ["n2", 2, "LIKES"],
]


class TestDistinctOnTheIdBoundVarLengthRoute:
    def test_a_node_reached_twice_is_returned_once(self):
        engine = _engine(_DUPLICATED)
        sql_query = _FakeSQLQuery(
            "SELECT DISTINCT n1.node_id AS b_node_id FROM nodes n0 JOIN nodes n1 ON 1=1 "
            "WHERE n0.node_id = ? FETCH FIRST 20 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        ids = [row[0] for row in result.rows]
        assert ids == ["n1", "n2", "n3"], ids

    def test_the_surviving_row_is_the_shortest_hop(self):
        """BFS emits in hop order, so keeping the first row keeps the shortest path."""
        engine = _engine(_DUPLICATED)
        sql_query = _FakeSQLQuery(
            "SELECT DISTINCT n1.node_id AS b_node_id FROM nodes n0 "
            "WHERE n0.node_id = ? FETCH FIRST 20 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        hops = {row[0]: row[1] for row in result.rows}
        assert hops["n1"] == 1, result.rows

    def test_the_cap_is_applied_after_the_dedupe(self):
        """20 raw hits holding 11 nodes must answer 11 rows, not 20."""
        rows = [[f"n{i % 11}", 1 + (i // 11), "KNOWS"] for i in range(30)]
        engine = _engine(rows)
        sql_query = _FakeSQLQuery(
            "SELECT DISTINCT n1.node_id AS b_node_id FROM nodes n0 "
            "WHERE n0.node_id = ? FETCH FIRST 20 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        ids = [row[0] for row in result.rows]
        assert len(ids) == 11, ids
        assert len(set(ids)) == 11, ids

    def test_bfs_is_not_asked_to_truncate_under_distinct(self):
        """A cap inside BFS cuts raw hits, which is a cap on the wrong thing."""
        engine = _engine(_DUPLICATED)
        sql_query = _FakeSQLQuery(
            "SELECT DISTINCT n1.node_id AS b_node_id FROM nodes n0 "
            "WHERE n0.node_id = ? FETCH FIRST 2 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        assert engine._store.calls[-1]["max_results"] == 0, engine._store.calls
        assert len(result.rows) == 2, result.rows

    def test_a_limit_beyond_the_distinct_count_returns_them_all(self):
        engine = _engine(_DUPLICATED)
        sql_query = _FakeSQLQuery(
            "SELECT DISTINCT n1.node_id AS b_node_id FROM nodes n0 "
            "WHERE n0.node_id = ? FETCH FIRST 500 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        assert len(result.rows) == 3, result.rows


class TestWithoutDistinctNothingChanges:
    """The route without `DISTINCT` keeps reporting every hit, capped inside BFS."""

    def test_duplicates_survive_and_the_cap_reaches_bfs(self):
        engine = _engine(_DUPLICATED)
        sql_query = _FakeSQLQuery(
            "SELECT n1.node_id AS b_node_id FROM nodes n0 "
            "WHERE n0.node_id = ? FETCH FIRST 4 ROWS ONLY"
        )
        result = engine._route_var_length(sql_query, {"src": "a0"})
        assert engine._store.calls[-1]["max_results"] == 4, engine._store.calls
        ids = [row[0] for row in result.rows]
        assert ids == ["n1", "n2", "n1", "n3"], ids
