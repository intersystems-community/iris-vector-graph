"""Spec 230 — `RETURN b.node_id` on the property-bound var-length route answers the id.

Found by the T074 gate. `MATCH (a {node_id: 'chain:0'})-[:NEXT*1..2]->(b) RETURN
b.node_id` comes back as `[[None], [None]]`: the right number of rows, every value
NULL. The route that answers it (`_execute_var_length_labeled`, taken whenever the
source is not bound to a `?` parameter — a literal in the pattern, or a label) walks
BFS correctly and then projects the RETURN by asking the store for `node_id` as a
property. `node_id` is the node's identity; there is no `rdf_props` row for it, so
every lookup misses and the column is NULL.

`RETURN b` and `RETURN count(b)` were unaffected — they never take the property
projection — which is why the class only shows up on `b.node_id`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.result import IVGResult


class _FakeStore:
    """Labels answer the source lookup; BFS answers the walk; props answer only props."""

    _schema_prefix = "Graph_KG"

    def __init__(self, reachable, props=None):
        self._reachable = reachable
        self._props = props or {}
        self.conn = MagicMock()

    def query_nodes(self, label_filter=None, **_kwargs):
        return IVGResult(columns=["node_id"], rows=[["chain:0"]])

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results, *, graph=None):
        return IVGResult(
            columns=["id", "hops", "pred"],
            rows=[[nid, hop, "NEXT"] for nid, hop in self._reachable],
        )

    def get_nodes(self, node_ids, properties=None):
        """Exactly the real store's contract: `node_id` is not among the properties."""
        cols = ["id", "labels"] + list(properties or [])
        rows = []
        for nid in node_ids:
            stored = self._props.get(nid, {})
            rows.append([nid, "[]"] + [stored.get(p) for p in (properties or [])])
        return IVGResult(columns=cols, rows=rows)


class _FakeSQLQuery:
    def __init__(self, sql, column_name_map):
        self.sql = sql
        self.parameters = [[]]
        self.column_name_map = column_name_map
        self.query_metadata = None
        self.graph_context = None


_VL0 = {
    "source_var": "a",
    "source_alias": "n0",
    "target_var": "b",
    "target_alias": "n1",
    "rel_var": None,
    "types": ["NEXT"],
    "direction": "out",
    "min_hops": 1,
    "max_hops": 2,
    "shortest": False,
    "all_shortest": False,
    "src_id_param": None,
    "dst_id_param": None,
    "return_path_funcs": [],
    "properties": {},
    "source_labels": ["Link"],
    "target_labels": [],
    "optional": False,
}


def _engine(store):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store
    engine._nkg_dirty = False
    return engine


class TestNodeIdIsProjectedFromTheIdentity:
    def test_return_node_id_answers_the_ids(self):
        engine = _engine(_FakeStore([("chain:1", 1), ("chain:2", 2)]))
        sql_query = _FakeSQLQuery(
            "SELECT n1.node_id AS b_node_id FROM Graph_KG.nodes n0 "
            "JOIN Graph_KG.nodes n1 ON 1=1",
            {"b_node_id": "b.node_id"},
        )
        result = engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert result.columns == ["b.node_id"], result.columns
        assert [row[0] for row in result.rows] == ["chain:1", "chain:2"], result.rows

    def test_node_id_alongside_a_real_property(self):
        """The identity and a stored property arrive in the order the RETURN asked."""
        store = _FakeStore(
            [("chain:1", 1)],
            props={"chain:1": {"name": "first"}},
        )
        engine = _engine(store)
        sql_query = _FakeSQLQuery(
            "SELECT n1.node_id AS b_node_id, p1.val AS b_name FROM Graph_KG.nodes n0 "
            "JOIN Graph_KG.nodes n1 ON 1=1",
            {"b_node_id": "b.node_id", "b_name": "b.name"},
        )
        result = engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert result.columns == ["b.node_id", "b.name"], result.columns
        assert result.rows == [["chain:1", "first"]], result.rows

    def test_a_property_the_node_lacks_is_still_null(self):
        """Only `node_id` changes: a missing property keeps answering NULL."""
        engine = _engine(_FakeStore([("chain:1", 1)]))
        sql_query = _FakeSQLQuery(
            "SELECT p1.val AS b_weight FROM Graph_KG.nodes n0 "
            "JOIN Graph_KG.nodes n1 ON 1=1",
            {"b_weight": "b.weight"},
        )
        result = engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert result.rows == [[None]], result.rows

    def test_the_store_is_not_asked_for_node_id_as_a_property(self):
        """Asking for it at all is the defect; the store must not see it."""
        store = _FakeStore([("chain:1", 1)])
        asked: list = []
        real_get_nodes = store.get_nodes

        def _recording(node_ids, properties=None):
            asked.append(list(properties or []))
            return real_get_nodes(node_ids, properties)

        store.get_nodes = _recording
        engine = _engine(store)
        sql_query = _FakeSQLQuery(
            "SELECT n1.node_id AS b_node_id FROM Graph_KG.nodes n0 "
            "JOIN Graph_KG.nodes n1 ON 1=1",
            {"b_node_id": "b.node_id"},
        )
        engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert all("node_id" not in keys for keys in asked), asked
