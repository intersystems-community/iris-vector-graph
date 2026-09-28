"""A Cypher node DELETE removes the (graph_id, node_id) rows its MATCH bound.

With no `USE GRAPH` clause a query reads the whole namespace
(`test_no_use_graph_clause_still_spans_the_namespace`), and the MATCH binds one row
per graph holding the node ID. The DELETE used to capture the bare IDs and delete
them by `node_id` alone, which also reached the labels, properties, edges, vectors
and node rows of graphs the MATCH never bound. It now captures `graph_id` beside the
ID, and each statement runs once per captured graph with `graph_id = '<g>'`.

Under `USE GRAPH` the capture is already confined to one graph and every statement
is scoped by `add_dml` (spec 227), so that path carries no graph token.
"""

import re
from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

ROW_TABLES = ("rdf_edges", "rdf_labels", "rdf_props", "kg_NodeEmbeddings", "nodes")


def sqls(cypher: str) -> list:
    t = translate_to_sql(parse_query(cypher), {})
    stmts = t.sql if isinstance(t.sql, list) else [t.sql]
    # Another test may have set the schema prefix (`set_schema_prefix`); compare bare names.
    return [s.replace("Graph_KG.", "") for s in stmts]


def _row_statements(stmts):
    return [s for s in stmts if not s.startswith("__capture_ids__ ")]


class TestTranslatorGraphLess:
    def test_node_capture_selects_the_graph(self):
        cap = sqls("MATCH (n:Foo {id:'x'}) DETACH DELETE n")[0]
        assert cap.startswith("__capture_ids__ ")
        assert re.search(r"SELECT n0\.node_id, n0\.graph_id\b", cap), cap

    def test_every_row_delete_is_keyed_by_the_captured_graph(self):
        stmts = _row_statements(sqls("MATCH (n:Foo {id:'x'}) DETACH DELETE n"))
        for table in ROW_TABLES:
            hits = [s for s in stmts if f"DELETE FROM {table} " in s]
            assert hits, (table, stmts)
            for s in hits:
                assert re.search(r"graph_id = __GRAPH_d0__", s), s

    def test_detach_keeps_both_endpoints_inside_the_graph(self):
        stmts = sqls("MATCH (n:Foo) DETACH DELETE n")
        edge = next(s for s in stmts if s.startswith("DELETE FROM rdf_edges"))
        assert edge == (
            "DELETE FROM rdf_edges WHERE (s IN (__IDS_d0__) OR o_id IN (__IDS_d0__)) "
            "AND graph_id = __GRAPH_d0__"
        )

    def test_connected_check_is_keyed_by_the_graph(self):
        stmts = sqls("MATCH (n:Foo) DELETE n")
        check = next(s for s in stmts if s.startswith("__constraint_check_delete_connected__"))
        assert "graph_id = __GRAPH_d0__" in check

    def test_folded_source_takes_the_edge_graph(self):
        stmts = sqls("MATCH p=(:X)-->() DETACH DELETE p")
        caps = [s for s in stmts if s.startswith("__capture_ids__ ")]
        assert any(re.search(r"SELECT e\d+\.s, e\d+\.graph_id\b", c) for c in caps), caps

    def test_relationship_deletes_stay_by_edge_id(self):
        stmts = sqls("MATCH (a)-[r]->(b) DELETE r, a, b")
        cap_r = stmts[0]
        assert re.search(r"SELECT e\d+\.edge_id\n", cap_r + "\n"), cap_r
        assert "DELETE FROM rdf_edges WHERE edge_id IN (__IDS_d0__)" in stmts


class TestTranslatorUseGraph:
    def test_use_graph_path_is_unchanged(self):
        stmts = sqls("USE GRAPH 'A' MATCH (n:Foo {id:'x'}) DETACH DELETE n")
        assert not any("__GRAPH_" in s for s in stmts), stmts
        for s in _row_statements(stmts):
            assert "graph_id = 'A'" in s, s

    def test_use_graph_connected_check_is_scoped(self):
        """`add_dml` scopes by DML verb; the connected check is a SELECT sentinel."""
        stmts = sqls("USE GRAPH 'A' MATCH (n:Foo {id:'x'}) DELETE n")
        check = next(s for s in stmts if s.startswith("__constraint_check_delete_connected__"))
        assert check.endswith(
            "WHERE (s IN (__IDS_d0__) OR o_id IN (__IDS_d0__)) AND graph_id = 'A'"
        ), check


def _store_with_cursor(select_rows):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    executed, state = [], {"rows": [], "desc": None}

    def execute(sql, params=None):
        executed.append(sql)
        if sql.lstrip().upper().startswith("SELECT"):
            state["rows"] = select_rows(sql)
            state["desc"] = [("c",)]
        else:
            state["desc"] = None

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: state["rows"]
    cursor.fetchone.side_effect = lambda: state["rows"][0] if state["rows"] else None
    type(cursor).description = property(lambda self: state["desc"])
    return IRISGraphStore(conn), executed


CAPTURE = "__capture_ids__ k\nSELECT n0.node_id, n0.graph_id FROM nodes n0"
NODE_DELETE = "DELETE FROM nodes WHERE node_id IN (__IDS_k__) AND graph_id = __GRAPH_k__"


class TestExecutorGraphPairs:
    def test_one_statement_per_captured_graph(self):
        store, executed = _store_with_cursor(lambda sql: [("x", "A"), ("y", "A"), ("x", "B")])
        store.execute_transaction([CAPTURE, NODE_DELETE], [[], []])
        deletes = [s for s in executed if s.startswith("DELETE")]
        assert deletes == [
            "DELETE FROM nodes WHERE node_id IN ('x', 'y') AND graph_id = 'A'",
            "DELETE FROM nodes WHERE node_id IN ('x') AND graph_id = 'B'",
        ]

    def test_null_graph_is_matched_as_null(self):
        store, executed = _store_with_cursor(lambda sql: [("x", None)])
        store.execute_transaction([CAPTURE, NODE_DELETE], [[], []])
        assert "DELETE FROM nodes WHERE node_id IN ('x') AND graph_id IS NULL" in executed

    def test_graph_quotes_are_escaped(self):
        store, executed = _store_with_cursor(lambda sql: [("x", "o'g")])
        store.execute_transaction([CAPTURE, NODE_DELETE], [[], []])
        assert "DELETE FROM nodes WHERE node_id IN ('x') AND graph_id = 'o''g'" in executed

    def test_nothing_captured_matches_nothing(self):
        store, executed = _store_with_cursor(lambda sql: [])
        store.execute_transaction([CAPTURE, NODE_DELETE], [[], []])
        deletes = [s for s in executed if s.startswith("DELETE")]
        assert deletes == ["DELETE FROM nodes WHERE node_id IN (NULL) AND graph_id = NULL"]

    def test_ids_without_a_graph_token_read_every_graph(self):
        """`_merge_excluded_nodes` reads `__IDS_k__` with no graph: the union."""
        store, executed = _store_with_cursor(lambda sql: [("x", "A"), ("x", "B"), ("y", "B")])
        store.execute_transaction(
            [CAPTURE, "DELETE FROM t WHERE s NOT IN (__IDS_k__)"], [[], []]
        )
        assert "DELETE FROM t WHERE s NOT IN ('x', 'y')" in executed

    def test_each_graph_is_chunked(self):
        from iris_vector_graph.stores import iris_sql_store as S

        rows = [(f"n{i}", "A") for i in range(S._CAPTURED_ID_CHUNK + 1)] + [("z", "B")]
        store, executed = _store_with_cursor(lambda sql: rows)
        store.execute_transaction([CAPTURE, NODE_DELETE], [[], []])
        deletes = [s for s in executed if s.startswith("DELETE")]
        assert len(deletes) == 3
        assert sum("graph_id = 'A'" in s for s in deletes) == 2
        assert deletes[-1] == "DELETE FROM nodes WHERE node_id IN ('z') AND graph_id = 'B'"

    def test_connected_check_counts_every_graph(self):
        def rows(sql):
            if "COUNT(*)" in sql:
                return [(1,)] if "'B'" in sql else [(0,)]
            return [("x", "A"), ("x", "B")]

        store, _ = _store_with_cursor(rows)
        import pytest

        with pytest.raises(Exception, match="Cannot delete node with existing relationships"):
            store.execute_transaction(
                [CAPTURE,
                 "__constraint_check_delete_connected__ SELECT COUNT(*) FROM e "
                 "WHERE s IN (__IDS_k__) AND graph_id = __GRAPH_k__"],
                [[], []],
            )
