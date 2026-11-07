"""4.6.0: a read or algorithm call whose query fails raises; it no longer logs a warning and answers nothing.

Found by the round-3 walkthrough: `MATCH p = shortestPath(...) RETURN [n IN nodes(p) | n.node_id]` run by a reading
role without R on the routines database answered no rows with `result.error` None. The only sign was a WARNING,
"ShortestPath failed: <PROTECT>...Graph.KG.Traversal.1". ADMIN_GUIDE §3 lists shortestPath among the calls that raise
<PROTECT>, and USER_GUIDE §3 says a failed read sets `result.error`. A sweep of iris_vector_graph/ for handlers that
log a failure and then answer an empty or default value on a read path found the sites below; each now lets the error
through (execute_cypher turns it into `result.error`, or raises with `raise_on_error=True`, as for every other failed
read). The handlers kept are fallbacks that answer the same another way (Arno to ObjectScript to SQL BFS, native
Cypher to SQL, the HLL count to the exact one, server-side KNN to client-side, a store algorithm to its ObjectScript
routine): the CHANGELOG lists them. Each site is checked with a fake that raises where IRIS refused.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

REFUSED = "<PROTECT> refused by the fake"


def _raising_cursor():
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError(REFUSED)
    return cur


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng.conn.cursor.side_effect = lambda *a, **k: _raising_cursor()
    eng._schema_prefix = "Graph_KG"
    store = MagicMock()
    store.conn = eng.conn
    store._schema_prefix = "Graph_KG"
    eng._store = store
    eng._store_capabilities = {}
    return eng


def _store():
    st = IRISGraphStore.__new__(IRISGraphStore)
    st._call_classmethod = MagicMock(side_effect=RuntimeError(REFUSED))
    return st


@pytest.mark.parametrize("call", [
    lambda e: e.count_nodes(),
    lambda e: e.count_nodes(label="L"),
    lambda e: e.get_nodes(["a"]),
    lambda e: e.get_node_ids_by_property("k", "v"),
    lambda e: e.get_property_pairs("k"),
    lambda e: e.get_property_values("k"),
    lambda e: e.property_value_exists("k", "a%"),
    lambda e: e.get_property_pairs_like("k", "a%"),
    lambda e: e.get_json_field_values("k", "f"),
    lambda e: e.get_node_ids_like("a%"),
    lambda e: e.count_subjects_with_property("k", "v"),
    lambda e: e._filter_edges_by_properties([{"s": "a", "p": "R1", "o": "b"}], {"w": 1}),
    lambda e: e.get_reifications(1),
    lambda e: e.list_active_queries(),
], ids=["count_nodes", "count_nodes_label", "get_nodes", "get_node_ids_by_property", "get_property_pairs",
        "get_property_values", "property_value_exists", "get_property_pairs_like", "get_json_field_values",
        "get_node_ids_like", "count_subjects_with_property", "filter_edges_by_properties", "get_reifications",
        "list_active_queries"])
def test_a_failed_engine_read_raises(call):
    with pytest.raises(RuntimeError, match="PROTECT"):
        call(_engine())


def test_get_kg_anchors_raises():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pytest.raises(RuntimeError, match="PROTECT"):
            _engine().get_kg_anchors(["I10"])


def test_the_prov_export_temporal_window_raises():
    eng = _engine()
    eng._iris_obj = MagicMock(return_value=MagicMock(classMethodValue=MagicMock(side_effect=RuntimeError(REFUSED))))
    with pytest.raises(RuntimeError, match="PROTECT"):
        eng._get_temporal_edges_window(None, None)


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_store_shortest_path_raises(direction):
    with pytest.raises(RuntimeError, match="PROTECT"):
        _store().execute_shortest_path("a2", "s3", [], 5, direction, False)


def test_store_temporal_aggregate_raises_not_zero():
    with pytest.raises(RuntimeError, match="PROTECT"):
        _store().get_temporal_aggregate("a", "p", "sum", 0, 10)


def test_cypher_shortest_path_raises_through_the_engine():
    eng = _engine()
    eng._store.execute_shortest_path.side_effect = RuntimeError(REFUSED)
    sql_query = MagicMock(var_length_paths=[{"shortest": True, "src_id_param": "a2", "dst_id_param": "s3",
                                             "types": [], "max_hops": 5, "direction": "out"}],
                          parameters=[[]], sql="")
    with pytest.raises(RuntimeError, match="PROTECT"):
        eng._execute_shortest_path_cypher(sql_query, {})


def _traversal_engine(bfs_error):
    eng = _engine()
    eng._store.execute_bfs.return_value = IVGResult(columns=[], rows=[], error=bfs_error)
    return eng


def test_a_one_hop_traversal_whose_walk_failed_raises():
    eng = _traversal_engine(REFUSED)
    traversal = {"source_id": "a", "predicates": [], "direction": "out", "is_count": True, "return_col": "c"}
    with pytest.raises(RuntimeError, match="PROTECT"):
        eng._execute_traversal(traversal, MagicMock(graph_context=None, query_metadata={}), MagicMock(), {})


def test_post_where_filter_whose_query_failed_raises_not_every_target():
    eng = _engine()
    sql = "SELECT a.node_id\nFROM Graph_KG.nodes a\nJOIN Graph_KG.nodes t ON 1=1\nWHERE a.node_id = ?"
    with pytest.raises(RuntimeError, match="PROTECT"):
        eng._filter_nodes_by_post_where(["t1", "t2"], "t", ["t.node_id = ?"], ["t1"], sql)


def _body(name: str) -> str:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "_engine" / "query.py").read_text()
    start = src.index(f"    def {name}(")
    return src[start:src.index("\n    def ", start + 10)]


@pytest.mark.parametrize("name", ["_route_var_length", "_execute_var_length_cypher"])
def test_a_var_length_count_does_not_turn_a_failed_walk_into_zero(name):
    # the count routes read the BFS rows when VarLength.EndsCount is not deployed; a failed walk counted 0
    assert "if not bfs_result.error else 0" not in _body(name)


def test_the_sorted_bfs_route_does_not_answer_nothing_on_failure():
    body = _body("_execute_var_length_cypher")
    assert 'logger.warning(f"BFSFastJsonSorted failed: {e}")' not in body


def test_an_empty_temporal_window_still_reads_zero():
    # GetAggregate answers "" for an empty window's min/max/avg and an unknown metric: 0.0 as documented, not a failure
    st = IRISGraphStore.__new__(IRISGraphStore)
    st._call_classmethod = MagicMock(return_value="")
    assert st.get_temporal_aggregate("a", "p", "avg", 0, 10).rows == [[0.0]]
    st._call_classmethod = MagicMock(return_value="2.5")
    assert st.get_temporal_aggregate("a", "p", "avg", 0, 10).rows == [[2.5]]


class _Refusing(IRISGraphEngine):
    """execute_cypher over a routed read that raises IRIS's error, as shortestPath's store call does."""

    def __init__(self, exc):
        self.conn = MagicMock()
        self._schema_prefix = "Graph_KG"
        self._exc = exc

    def _probe_if_idle(self):
        pass

    def _execute_parsed(self, parsed, parameters, procedures=None):
        raise self._exc


SHORTEST = "MATCH p = shortestPath((a {node_id: 'a'})-[*]->(d {node_id: 'd'})) RETURN length(p)"


def test_a_failed_cypher_read_sets_result_error():
    """USER_GUIDE §3: a read that fails in IRIS comes back with `result.error` set and no rows."""
    r = _Refusing(RuntimeError(REFUSED)).execute_cypher(SHORTEST)
    assert r.rows == [] and REFUSED in r.error


def test_raise_on_error_raises_the_failed_read():
    with pytest.raises(RuntimeError, match="PROTECT"):
        _Refusing(RuntimeError(REFUSED)).execute_cypher(SHORTEST, raise_on_error=True)


def test_a_failed_write_still_raises_whatever_raise_on_error_says():
    with pytest.raises(RuntimeError, match="PROTECT"):
        _Refusing(RuntimeError(REFUSED)).execute_cypher("MATCH (a {node_id: 'a'}) SET a.x = 1")


@pytest.mark.parametrize("exc", ["GraphWriteError", "ClassesUnreadable", "ValueError"])
def test_typed_errors_are_not_folded_into_result_error(exc):
    from iris_vector_graph.errors import GraphWriteError
    from iris_vector_graph.exceptions import ClassesUnreadable

    made = {"GraphWriteError": lambda: GraphWriteError("execute_cypher", RuntimeError("x"), ""),
            "ClassesUnreadable": lambda: ClassesUnreadable("routines database refused"),
            "ValueError": lambda: ValueError("shortestPath requires both source and target node IDs")}[exc]()
    with pytest.raises(type(made)):
        _Refusing(made).execute_cypher(SHORTEST)
