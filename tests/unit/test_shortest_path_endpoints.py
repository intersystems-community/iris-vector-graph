"""shortestPath endpoints given as labels / property maps are read from the graph.

`MATCH path = shortestPath((a:Person {sid: $x})-[:KNOWS*..6]-(b:Person {sid: $y}))
RETURN length(path)` translates to a MATCH over rdf_labels / rdf_props whose bound
parameters are ['Person', 'sid', 'Person', 'sid', '1', '2']. The engine used to pick
node ids out of the user's parameters or the first strings of that list (the property
values '1', '2', or 'Person', 'sid') and `execute_shortest_path` then returned no rows.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult

_LABELLED = (
    "MATCH path = shortestPath((a:Person {sid: $x})-[:KNOWS*..6]-(b:Person {sid: $y})) "
    "RETURN length(path)"
)
_BY_NODE_ID = (
    "MATCH path = shortestPath((a {node_id: $x})-[:KNOWS*..6]-(b {node_id: $y})) "
    "RETURN length(path)"
)
_PARAMS = {"x": "1", "y": "2"}


class _Store:
    _schema_prefix = "Graph_KG"

    def __init__(self, pairs):
        self.pairs = pairs
        self.sql_calls: list = []
        self.sp_calls: list = []
        self.lookup_error = None

    def execute_sql(self, sql, params=None):
        self.sql_calls.append((sql, params))
        if self.lookup_error:
            return IVGResult(columns=[], rows=[], error=self.lookup_error)
        return IVGResult(columns=["a", "b"], rows=[list(p) for p in self.pairs])

    def execute_shortest_path(self, s, t, predicates, max_hops, direction, find_all):
        self.sp_calls.append((s, t, predicates, max_hops, direction, find_all))
        path = '{"nodes": ["%s", "%s"], "length": 1}' % (s, t)
        return IVGResult(columns=["path", "length"], rows=[[path, 1]])

    def shortest_path_length(self, s, t, predicates, max_hops, direction):
        # RETURN length(path) alone takes the distance search: the same endpoints, the same options
        self.sp_calls.append((s, t, predicates, max_hops, direction, False))
        return 1


def _engine(store):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store
    engine._nkg_dirty = False
    return engine


def _run(cypher, store):
    sql_query = translate_to_sql(parse_query(cypher), _PARAMS)
    return _engine(store)._execute_shortest_path_cypher(sql_query, _PARAMS), sql_query


class TestLabelledEndpoints:
    def test_endpoints_come_from_the_lookup_not_the_property_values(self):
        store = _Store([("p:alice", "p:bob")])
        result, _ = _run(_LABELLED, store)
        assert [c[:2] for c in store.sp_calls] == [("p:alice", "p:bob")]
        assert result.columns == ["length"]
        assert result.rows == [[1]]

    def test_lookup_sql_selects_the_endpoint_node_ids_with_original_parameters(self):
        store = _Store([("p:alice", "p:bob")])
        _, sql_query = _run(_LABELLED, store)
        sql, params = store.sql_calls[0]
        assert sql.startswith("SELECT DISTINCT n0.node_id, n3.node_id\nFROM ")
        assert "length_path_" not in sql
        assert params == sql_query.parameters[0]
        assert params == ["Person", "sid", "Person", "sid", "1", "2"]

    def test_search_options_are_forwarded(self):
        store = _Store([("p:alice", "p:bob")])
        _run(_LABELLED, store)
        _, _, predicates, max_hops, direction, find_all = store.sp_calls[0]
        assert (predicates, max_hops, direction, find_all) == (["KNOWS"], 6, "both", False)

    def test_every_pair_is_searched_and_rows_are_concatenated(self):
        store = _Store([("p:a", "p:b"), ("p:a", "p:c")])
        result, _ = _run(_LABELLED, store)
        assert [c[:2] for c in store.sp_calls] == [("p:a", "p:b"), ("p:a", "p:c")]
        assert result.rows == [[1], [1]]

    def test_a_node_paired_with_itself_is_skipped(self):
        store = _Store([("p:a", "p:a"), ("p:a", "p:b")])
        _run(_LABELLED, store)
        assert [c[:2] for c in store.sp_calls] == [("p:a", "p:b")]

    def test_no_matching_endpoints_gives_no_rows_and_no_search(self):
        store = _Store([])
        result, _ = _run(_LABELLED, store)
        assert store.sp_calls == []
        assert result.rows == []

    def test_a_failed_lookup_raises_rather_than_returning_nothing(self):
        store = _Store([])
        store.lookup_error = "SQLCODE -29 field not found"
        with pytest.raises(RuntimeError, match="SQLCODE -29"):
            _run(_LABELLED, store)
        assert store.sp_calls == []

    def test_more_than_1000_pairs_is_refused(self):
        store = _Store([(f"s{i}", f"t{i}") for i in range(1001)])
        with pytest.raises(ValueError, match="1000"):
            _run(_LABELLED, store)
        assert store.sp_calls == []

    def test_exactly_1000_pairs_is_allowed(self):
        store = _Store([(f"s{i}", f"t{i}") for i in range(1000)])
        _run(_LABELLED, store)
        assert len(store.sp_calls) == 1000


class TestUnlabelledNodeIdEndpoints:
    def test_node_id_endpoints_do_not_touch_the_lookup(self):
        store = _Store([("zzz", "zzz")])
        _run(_BY_NODE_ID, store)
        assert store.sql_calls == []
        assert [c[:2] for c in store.sp_calls] == [("1", "2")]


class TestSourceBoundEarlier:
    def test_an_empty_source_alias_keeps_the_old_path(self):
        store = _Store([("p:alice", "p:bob")])
        sql_query = translate_to_sql(parse_query(_LABELLED), _PARAMS)
        sql_query.var_length_paths[0]["source_alias"] = ""
        _engine(store)._execute_shortest_path_cypher(sql_query, _PARAMS)
        assert store.sql_calls == []
