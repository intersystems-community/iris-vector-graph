"""Spec 229: DELETE has to remove what it matched, measured as side effects.

The strict TCK harness diffs the graph around each query. Under it, every
`MATCH (n:X) DELETE n` removed nothing from `nodes`: each DELETE statement re-ran
the MATCH as a subquery, and the MATCH joined `rdf_labels`, which the first
statement had just emptied for those nodes. The ids a DELETE clause touches are now
captured once, before the clause writes anything, and every statement of the clause
reads the captured ids (`__capture_ids__` / `__IDS_<key>__`).

Also here: the undirected-edge property read that returned NULL (Delete2 [3]),
DELETE of a named path (Delete3), DELETE of a list / map element (Delete5), and the
stale cursor description that made a DML statement fetch rows (Delete6 [8]/[9],
Remove3 [8]/[9]).
"""

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import CypherParseError, parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def sqls(cypher: str, params: dict | None = None) -> list:
    t = tr(cypher, params)
    return t.sql if isinstance(t.sql, list) else [t.sql]


def _first(stmts, pattern):
    for i, s in enumerate(stmts):
        if re.search(pattern, s):
            return i
    raise AssertionError(f"no statement matches {pattern!r}: {stmts}")


def _captures(stmts):
    return [s for s in stmts if s.startswith("__capture_ids__ ")]


class TestNodeDeleteReadsCapturedIds:
    """Delete1 [1]-[3], Delete6 [1]-[7]."""

    Q = "MATCH (n:X) DELETE n"

    def test_ids_are_captured_before_any_write(self):
        stmts = sqls(self.Q)
        assert stmts[0].startswith("__capture_ids__ ")
        assert "rdf_labels" in stmts[0]  # the MATCH itself

    @pytest.mark.parametrize("table", ["rdf_labels", "rdf_props", "nodes"])
    def test_row_deletes_read_the_capture_not_the_match(self, table):
        stmts = sqls(self.Q)
        i = _first(stmts, rf"^DELETE FROM (?:\w+\.)?{table}\b")
        assert "__IDS_" in stmts[i]
        assert "JOIN" not in stmts[i]

    def test_node_row_delete_is_last(self):
        stmts = sqls(self.Q)
        assert re.match(r"DELETE FROM (?:\w+\.)?nodes\b", stmts[-1])

    def test_connected_check_reads_the_capture(self):
        stmts = sqls(self.Q)
        i = _first(stmts, r"^__constraint_check_delete_connected__")
        assert "__IDS_" in stmts[i]

    def test_detach_delete_removes_edges_by_captured_ids(self):
        stmts = sqls("MATCH (n:X) DETACH DELETE n")
        i = _first(stmts, r"^DELETE FROM (?:\w+\.)?rdf_edges\b")
        assert "__IDS_" in stmts[i]
        assert i < _first(stmts, r"^DELETE FROM (?:\w+\.)?rdf_labels\b")

    def test_capture_after_with_stage_keeps_the_cte(self):
        stmts = sqls("MATCH (n:N) WITH n WHERE n.num > 1 DELETE n")
        cap = _captures(stmts)
        assert len(cap) == 1 and "WITH " in cap[0]


class TestMultiTargetDeleteClause:
    """Delete2 [2], Delete4 [1]: all captures first, edges before nodes."""

    def test_every_target_captured_before_the_first_write(self):
        stmts = sqls("MATCH (a:X)-[r]-(b:X) DELETE r, a, b RETURN count(*) AS c")
        caps = [i for i, s in enumerate(stmts) if s.startswith("__capture_ids__ ")]
        writes = [i for i, s in enumerate(stmts) if s.startswith("DELETE ")]
        assert len(caps) == 3
        assert max(caps) < min(writes)

    def test_relationship_goes_before_the_connected_check(self):
        stmts = sqls("MATCH (a:X)-[r]-(b:X) DELETE r, a, b")
        edge = _first(stmts, r"^DELETE FROM (?:\w+\.)?rdf_edges\b")
        check = _first(stmts, r"^__constraint_check_delete_connected__")
        assert edge < check

    def test_variables_sharing_a_stage_are_all_deleted(self):
        # Delete5 [7]: every WITH-carried variable has the alias `Stage1`.
        stmts = sqls("MATCH (a:X)-[r]->(b:X) WITH a, r, b DELETE a, r, b")
        caps = _captures(stmts)
        assert len(caps) == 3
        assert any("__edge_r_id" in c for c in caps)
        node_deletes = [s for s in stmts if re.match(r"DELETE FROM (?:\w+\.)?nodes\b", s)]
        assert len(node_deletes) == 2

    def test_optional_relationship_goes_before_the_node_row(self):
        stmts = sqls("MATCH (n:X) OPTIONAL MATCH (n)-[r]-() DELETE n, r")
        edge = _first(stmts, r"^DELETE FROM (?:\w+\.)?rdf_edges\b")
        node = _first(stmts, r"^DELETE FROM (?:\w+\.)?nodes\b")
        assert edge < node

    def test_single_relationship_delete_keeps_its_subquery(self):
        # `DELETE r CREATE ...` / `DELETE t MERGE ...` rewrite that statement in place.
        stmts = sqls("MATCH ()-[r:R]->() DELETE r")
        assert not _captures(stmts)
        assert stmts[0].startswith("DELETE FROM") and "edge_id IN (SELECT" in stmts[0]


class TestUndirectedRelationshipProperty:
    """Delete2 [3]: `MATCH ()-[r:T]-() WHERE r.id = 42` compared NULL with 42."""

    @pytest.mark.parametrize("q", [
        "MATCH ()-[r:T]-() WHERE r.id = 42 RETURN r.id",
        "MATCH (a)-[r:T]-(b) WHERE r.num = 42 RETURN r.num",
    ])
    def test_reads_the_qualifiers(self, q):
        sql = sqls(q)[-1]
        assert "NULL = 42" not in sql
        assert "qualifiers" in sql


class TestDeleteNamedPath:
    """Delete3 [1]/[2]: `DETACH DELETE p` deletes every node and relationship of p."""

    def test_path_elements_are_captured(self):
        stmts = sqls("MATCH p = (:X)-->()-->() DETACH DELETE p")
        caps = _captures(stmts)
        assert len(caps) == 5  # three nodes, two relationships
        assert any(re.match(r"DELETE FROM (?:\w+\.)?nodes\b", s) for s in stmts)

    def test_optional_null_path_translates(self):
        stmts = sqls("OPTIONAL MATCH p = ()-->() DETACH DELETE p")
        assert any(re.match(r"DELETE FROM (?:\w+\.)?nodes\b", s) for s in stmts)


class TestDeleteContainerElement:
    """Delete5 [1]-[7]: DELETE of a list element, a map value, or a nested one."""

    @pytest.mark.parametrize("q,params", [
        ("MATCH (:User)-[:FRIEND]->(n) WITH collect(n) AS friends "
         "DETACH DELETE friends[$friendIndex]", {"friendIndex": 1}),
        ("MATCH (:User)-[r:FRIEND]->() WITH collect(r) AS friendships "
         "DETACH DELETE friendships[$friendIndex]", {"friendIndex": 1}),
        ("MATCH (u:User) WITH {key: u} AS nodes DELETE nodes.key", {}),
        ("MATCH (:User)-[r]->(:User) WITH {key: r} AS rels DELETE rels.key", {}),
        ("MATCH (u:User) WITH {key: collect(u)} AS nodeMap DETACH DELETE nodeMap.key[0]", {}),
        ("MATCH (:User)-[r]->(:User) WITH {key: {key: collect(r)}} AS rels "
         "DELETE rels.key.key[0]", {}),
        ("MATCH p = (:User)-[r]->(:User) WITH {key: collect(p)} AS pathColls "
         "DELETE pathColls.key[0], pathColls.key[1]", {}),
    ])
    def test_translates_to_a_delete(self, q, params):
        stmts = sqls(q, params)
        assert any(s.startswith("DELETE FROM") or "DELETE FROM" in s for s in stmts)

    def test_list_index_picks_one_row(self):
        from iris_vector_graph.cypher.translator import _lower_delete_containers

        q = parse_query(
            "MATCH (:User)-[:FRIEND]->(n) WITH collect(n) AS friends "
            "DETACH DELETE friends[$friendIndex]"
        )
        _lower_delete_containers(q, {"friendIndex": 1})
        w = q.query_parts[0].with_clause
        assert [it.alias for it in w.items] == ["n"]
        assert (w.skip, w.limit) == (1, 1)
        d = q.query_parts[1].clauses[0]
        assert [e.name for e in d.expressions] == ["n"] and d.detach

    def test_two_path_indices_take_two_rows(self):
        from iris_vector_graph.cypher.translator import _lower_delete_containers

        q = parse_query(
            "MATCH p = (:User)-[r]->(:User) WITH {key: collect(p)} AS pathColls "
            "DELETE pathColls.key[0], pathColls.key[1]"
        )
        _lower_delete_containers(q, {})
        w = q.query_parts[0].with_clause
        assert (w.skip, w.limit) == (0, 2)
        names = [e.name for e in q.query_parts[1].clauses[0].expressions]
        assert "r" in names and len(names) == 3

    def test_map_value_passes_the_variable_through(self):
        from iris_vector_graph.cypher.translator import _lower_delete_containers

        q = parse_query("MATCH (u:User) WITH {key: u} AS nodes DELETE nodes.key")
        _lower_delete_containers(q, {})
        w = q.query_parts[0].with_clause
        assert [it.alias for it in w.items] == ["u"] and w.skip is None and w.limit is None

    def test_integer_expression_is_invalid_argument_type(self):
        with pytest.raises(SyntaxError, match="InvalidArgumentType"):
            tr("MATCH () DELETE 1 + 1")

    def test_undefined_variable_still_reported(self):
        with pytest.raises(SyntaxError, match="Undefined variable: x"):
            tr("MATCH (a) DELETE x")

    def test_label_expression_still_rejected(self):
        with pytest.raises((SyntaxError, CypherParseError)):
            tr("MATCH (n) DELETE n:Person")


def _store_with_cursor(select_rows):
    """IRISGraphStore over a fake cursor: SELECTs answer from `select_rows(sql)`."""
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    state = {"rows": [], "desc": None, "last_dml": False}
    executed = []

    def execute(sql, params=None):
        executed.append((sql, list(params or [])))
        head = sql.lstrip().upper()
        if head.startswith("SELECT") or (head.startswith("WITH") and "DELETE " not in head):
            state["rows"] = select_rows(sql)
            state["desc"] = [("c",)]
            state["last_dml"] = False
        else:
            state["last_dml"] = not head.startswith("START")
            # IRIS keeps the previous SELECT's description after a DML statement.

    def fetchall():
        if state["last_dml"]:
            raise RuntimeError("null pointer exception")
        return state["rows"]

    def fetchone():
        return state["rows"][0] if state["rows"] else None

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = fetchall
    cursor.fetchone.side_effect = fetchone
    type(cursor).description = property(lambda self: state["desc"])
    return IRISGraphStore(conn), executed


class TestExecutorCapturedIds:
    def test_ids_are_inlined_into_later_statements(self):
        store, executed = _store_with_cursor(lambda sql: [("a",), ("b'c",), (None,), ("a",)])
        store.execute_transaction(
            ["__capture_ids__ k1\nSELECT n0.node_id FROM nodes n0",
             "DELETE FROM nodes WHERE node_id IN (__IDS_k1__)"],
            [[], []],
        )
        deletes = [s for s, _ in executed if s.startswith("DELETE")]
        assert deletes == ["DELETE FROM nodes WHERE node_id IN ('a', 'b''c')"]

    def test_no_ids_matches_nothing(self):
        store, executed = _store_with_cursor(lambda sql: [])
        store.execute_transaction(
            ["__capture_ids__ k1\nSELECT 1", "DELETE FROM nodes WHERE node_id IN (__IDS_k1__)"],
            [[], []],
        )
        assert ("DELETE FROM nodes WHERE node_id IN (NULL)", []) in executed

    def test_many_ids_are_chunked(self):
        from iris_vector_graph.stores import iris_sql_store as S

        ids = [(f"n{i}",) for i in range(S._CAPTURED_ID_CHUNK * 2 + 1)]
        store, executed = _store_with_cursor(lambda sql: ids)
        store.execute_transaction(
            ["__capture_ids__ k\nSELECT 1", "DELETE FROM x WHERE s IN (__IDS_k__) OR o IN (__IDS_k__)"],
            [[], []],
        )
        deletes = [s for s, _ in executed if s.startswith("DELETE")]
        assert len(deletes) == 3
        assert all(s.count("'n") <= 2 * S._CAPTURED_ID_CHUNK for s in deletes)

    def test_connected_check_counts_every_chunk(self):
        from iris_vector_graph.stores import iris_sql_store as S

        ids = [(f"n{i}",) for i in range(S._CAPTURED_ID_CHUNK + 1)]

        def rows(sql):
            if "COUNT(*)" in sql:
                return [(1,)] if "'n0'" not in sql else [(0,)]
            return ids

        store, _ = _store_with_cursor(rows)
        with pytest.raises(Exception, match="Cannot delete node with existing relationships"):
            store.execute_transaction(
                ["__capture_ids__ k\nSELECT 1",
                 "__constraint_check_delete_connected__ SELECT COUNT(*) FROM e WHERE s IN (__IDS_k__)"],
                [[], []],
            )

    def test_dml_after_an_empty_select_does_not_fetch(self):
        """Delete6 [8]: the final SELECT ran first and returned nothing; the DELETE
        after it kept that description, and fetchall() on it is an NPE in IRIS."""
        store, _ = _store_with_cursor(lambda sql: [])
        res = store.execute_transaction(
            ["DELETE FROM rdf_edges WHERE edge_id IN (SELECT 1)", "SELECT TOP 0 42 AS num"],
            [[], []],
        )
        assert res.rows == []
