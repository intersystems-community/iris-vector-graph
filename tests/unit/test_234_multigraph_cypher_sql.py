"""Spec 234 US1/US2 — the SQL Cypher CREATE and MERGE emit for relationships.

Checked without IRIS:

- with multigraph mode off — no engine, an engine that says no, or a mock that
  answers anything — every statement is byte-identical to the SQL captured before
  the multigraph code landed (``golden/234_mode_off_sql.json``, FR-009);
- with the mode on, CREATE allocates ``ekey`` instead of skipping a triple that
  already has an edge (FR-007, FR-008), and MERGE matches and guards on the whole
  pattern, inline properties included, rather than on the bare triple (US2);
- the mode is asked once per statement, for the statement's own graph.

The live behaviour is in ``tests/e2e/test_234_multigraph_cypher_e2e.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.unit.golden.gen_234_mode_off_sql import GOLDEN, QUERIES, translate

GOLDEN_SQL = json.loads(Path(GOLDEN).read_text())


class _Engine:
    """Only what the translator asks of an engine about the mode."""

    def __init__(self, multi: bool):
        self.multi = multi
        self.asked: list = []

    def is_multigraph(self, graph):
        self.asked.append(graph)
        return self.multi

    def get_table_mapping(self, label):
        return None

    def get_rel_mapping(self, *a):
        return None


def _on(query: str) -> list:
    """(sql, params) pairs for ``query`` translated with the mode on."""
    out = translate(query, engine=_Engine(True))
    return list(zip(out["sql"], out["params"]))


def _edge_writes(stmts) -> list:
    return [(s, p) for s, p in stmts if "INSERT INTO rdf_edges" in s]


def _markers(sql: str) -> int:
    return len(re.findall(r"\?", re.sub(r"'(?:[^']|'')*'", "''", sql)))


# --- mode off: byte-identical ----------------------------------------------------------


def test_the_golden_file_covers_every_query():
    assert sorted(GOLDEN_SQL) == sorted(QUERIES)


@pytest.mark.parametrize("query", QUERIES)
def test_no_engine_is_todays_sql(query):
    assert translate(query) == GOLDEN_SQL[query]


@pytest.mark.parametrize("query", QUERIES)
def test_mode_off_is_todays_sql(query):
    assert translate(query, engine=_Engine(False)) == GOLDEN_SQL[query]


@pytest.mark.parametrize("query", QUERIES)
def test_an_engine_that_answers_anything_is_not_mode_on(query):
    # A MagicMock's is_multigraph returns a truthy MagicMock; only True turns it on.
    eng = MagicMock()
    eng.get_table_mapping.return_value = None
    eng.get_rel_mapping.return_value = None
    assert translate(query, engine=eng) == GOLDEN_SQL[query]


def test_an_engine_whose_lookup_fails_is_mode_off():
    class Broken(_Engine):
        def is_multigraph(self, graph):
            raise RuntimeError("no IRIS")

    q = "MATCH (a:A), (b:B) CREATE (a)-[:T]->(b)"
    assert translate(q, engine=Broken(True)) == GOLDEN_SQL[q]


# --- the mode lookup -------------------------------------------------------------------


def test_the_mode_is_asked_once_for_the_default_graph():
    eng = _Engine(True)
    translate("CREATE (a:A), (b:B) CREATE (a)-[:T]->(b) CREATE (a)-[:T]->(b)", engine=eng)
    assert eng.asked == [None]


def test_the_mode_is_asked_for_the_statements_graph():
    eng = _Engine(True)
    translate("USE GRAPH 'g1' MATCH (a:A), (b:B) CREATE (a)-[:T]->(b)", engine=eng)
    assert eng.asked == ["g1"]


def test_a_statement_without_relationship_writes_does_not_ask():
    eng = _Engine(True)
    translate("MATCH (a:A) RETURN a", engine=eng)
    translate("CREATE (a:A)", engine=eng)
    assert eng.asked == []


# --- US1: CREATE -----------------------------------------------------------------------


def test_create_from_values_allocates_the_next_ekey():
    writes = _edge_writes(
        _on("CREATE (a:A), (b:B) CREATE (a)-[:TYPE]->(b) CREATE (a)-[:TYPE]->(b)")
    )
    assert len(writes) == 2
    for sql, params in writes:
        assert "VALUES (" not in sql
        assert "(s, p, o_id, graph_id, ekey)" in sql
        assert "COALESCE(MAX(ekey) + 1, 0)" in sql
        assert "NOT EXISTS" not in sql
        assert _markers(sql) == len(params)
    # both INSERTs are the same statement: each sees the MAX the one before it left
    assert writes[0] == writes[1]


def test_create_from_values_scopes_the_ekey_to_the_triple_and_graph():
    (sql, params), = _edge_writes(_on("CREATE (a:A), (b:B) CREATE (a)-[:T]->(b)"))
    assert re.search(r"WHERE s = \? AND p = \? AND o_id = \? AND graph_id = \?", sql), sql
    assert params[:4] == params[4:8]  # the row's own s, p, o, graph again for the MAX


def test_create_from_values_keeps_its_properties():
    (sql, params), = _edge_writes(
        _on("CREATE (a:A), (b:B) CREATE (a)-[:T {name: 'r1'}]->(b)")
    )
    assert "(s, p, o_id, qualifiers, graph_id, ekey)" in sql
    assert repr('{"name": "r1"}') in params
    assert _markers(sql) == len(params)


def test_a_created_relationship_variable_binds_the_newest_parallel_edge():
    stmts = _on("CREATE (a:A), (b:B) CREATE (a)-[r:T]->(b) RETURN r")
    select = stmts[-1][0]
    assert "ekey = (SELECT MAX(" in select, select
    assert _markers(select) == len(stmts[-1][1])


def test_create_after_match_creates_one_edge_per_row():
    (sql, params), = _edge_writes(_on("MATCH (a:A), (b:B) CREATE (a)-[:T]->(b)"))
    assert "SELECT DISTINCT" not in sql
    assert "NOT EXISTS" not in sql
    assert "ROW_NUMBER() OVER (PARTITION BY _ge.c1, _ge.c2, _ge.c3" in sql
    assert "(s, p, o_id, graph_id, ekey)" in sql
    assert _markers(sql) == len(params)


def test_create_after_match_writes_literal_properties():
    (sql, params), = _edge_writes(
        _on("MATCH (a:A), (b:B) CREATE (a)-[r:T {name: 'x'}]->(b) RETURN r.name")
    )
    assert "(s, p, o_id, graph_id, ekey, qualifiers)" in sql
    assert repr('{"name": "x"}') in params
    assert _markers(sql) == len(params)


# --- US2: MERGE ------------------------------------------------------------------------


def test_merge_without_properties_guards_on_the_triple_in_its_graph():
    stmts = _on("MATCH (a:A), (b:B) MERGE (a)-[r:TYPE]->(b) RETURN count(r)")
    (sql, params), = _edge_writes(stmts)
    assert "SELECT DISTINCT" in sql  # one create per (a, b), however many rows bind it
    assert "COALESCE((SELECT MAX(_gx.ekey)" in sql
    assert re.search(r"NOT EXISTS \(SELECT 1 FROM rdf_edges _gm WHERE _gm\.s = _ge\.c1", sql)
    assert "_gm.graph_id = ?" in sql
    assert "JSON_VALUE" not in sql
    assert _markers(sql) == len(params)
    select, sparams = stmts[-1]
    assert "JSON_VALUE" not in select
    assert _markers(select) == len(sparams)


def test_merge_with_properties_matches_and_guards_on_them():
    stmts = _on("MATCH (a:A), (b:B) MERGE (a)-[r:TYPE {name: 'r2'}]->(b) RETURN count(r)")
    (sql, params), = _edge_writes(stmts)
    assert "SQLUser.JSON_VALUE(_gm.qualifiers, '$.name') = ?" in sql
    assert "(s, p, o_id, graph_id, ekey, qualifiers)" in sql
    assert repr('{"name": "r2"}') in params
    assert _markers(sql) == len(params)
    select, sparams = stmts[-1]
    assert re.search(r"JSON_VALUE\(e\d+\.qualifiers, '\$\.name'\) = \?", select), select
    assert "'r2'" in sparams
    assert _markers(select) == len(sparams)


def test_merge_relationship_property_from_a_with_stage_is_not_dropped():
    """Merge5 [14] "Using list properties via variable": `foobar: roles` where
    `roles` comes from `WITH ..., split(str, ',') AS roles` is bound to a stage
    column, not a literal or a FOREACH value — the only two things the
    literal-endpoint fast path's property resolution understood. Anything else
    was silently dropped rather than read from the row.
    """
    stmts = _on(
        "CREATE (a:Foo), (b:Bar) WITH a, b UNWIND ['a,b', 'a,b'] AS str "
        "WITH a, b, split(str, ',') AS roles "
        "MERGE (a)-[r:FB {foobar: roles}]->(b) RETURN count(*)"
    )
    (sql, params), = _edge_writes(stmts)
    assert "qualifiers" in sql
    assert "__dyn0" in sql  # the stage column the qualifiers expression reads
    assert repr('"foobar": ') in params
    assert _markers(sql) == len(params)


def test_merge_relationship_property_still_literal_when_not_stage_bound():
    # A literal alongside the fast path stays byte-identical: the new dynamic-
    # property machinery only engages when a property actually needs a stage.
    (sql, params), = _edge_writes(
        _on("CREATE (a:A), (b:B) MERGE (a)-[r:T {name: 'x'}]->(b) RETURN r")
    )
    assert "__dyn" not in sql
    assert repr('{"name": "x"}') in params


def test_merge_undirected_guards_both_directions():
    stmts = _on("MATCH (a:A), (b:B) MERGE (a)-[r:TYPE]-(b) RETURN r")
    (sql, params), = _edge_writes(stmts)
    assert "_gm.s = _ge.c3" in sql and "_gm.o_id = _ge.c1" in sql
    assert _markers(sql) == len(params)


def test_merge_on_created_nodes_keeps_todays_statement():
    # Nodes made in the same statement have no edges yet: nothing to fit against.
    q = "CREATE (a:A), (b:B) MERGE (a)-[r:R]->(b) RETURN r"
    on = translate(q, engine=_Engine(True))
    edge_on = [s for s in on["sql"] if "INSERT INTO rdf_edges" in s]
    edge_off = [s for s in GOLDEN_SQL[q]["sql"] if "INSERT INTO rdf_edges" in s]
    assert edge_on == edge_off


def test_delete_then_merge_reads_the_rows_before_the_delete():
    # Merge5 [21]: the MERGE and the RETURN see the rows MATCH bound, then the
    # DELETE runs, limited to edges that existed before the statement.
    stmts = _on(
        "MATCH (a)-[t:T]->(b) DELETE t MERGE (a)-[t2:T {name: 'rel3'}]->(b) RETURN t2.name"
    )
    kinds = [s.split()[0] for s, _ in stmts]
    assert stmts[0][0].startswith("__capture_edge_hwm__")
    ins = next(i for i, (s, _) in enumerate(stmts) if "INSERT INTO rdf_edges" in s)
    dele = next(i for i, (s, _) in enumerate(stmts) if "DELETE FROM rdf_edges" in s)
    assert ins < dele, kinds
    assert stmts[dele][0].startswith("__after_result__ ")
    assert stmts[dele][0].endswith("AND edge_id <= ?")
    assert stmts[dele][1][-1] == repr("__EDGE_HWM__")
    # the edges the DELETE removes are not a fit for the MERGE
    assert "_gm.edge_id NOT IN (SELECT" in stmts[ins][0]
    assert "_gm.edge_id > ? OR" in stmts[ins][0]
    assert _markers(stmts[ins][0]) == len(stmts[ins][1])
    # the RETURN reads before the DELETE: its MATCH binds only pre-statement edges,
    # and the edge MERGE made is not among those the DELETE will remove
    select, sparams = stmts[-1]
    assert select.startswith("SELECT")
    assert re.search(r"e\d+\.edge_id <= \?", select), select
    assert re.search(r"e\d+\.edge_id > \? OR e\d+\.edge_id NOT IN", select), select
    assert sparams.count(repr("__EDGE_HWM__")) == 2
    assert _markers(select) == len(sparams)


def test_delete_then_merge_keeps_todays_order_with_the_mode_off():
    q = "MATCH (a)-[t:T]->(b) DELETE t MERGE (a)-[t2:T {name: 'rel3'}]->(b) RETURN t2.name"
    assert not any("__after_result__" in s for s in GOLDEN_SQL[q]["sql"])


# --- the executor: `__after_result__` ----------------------------------------------------


def _store():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return IRISGraphStore(conn), conn, cur


def test_after_result_statements_run_after_the_result_is_read():
    store, conn, cur = _store()
    order = []
    state = {"desc": None}

    def execute(sql, params=None):
        order.append(sql)
        state["desc"] = [("t2_name",)] if sql.startswith("SELECT t2") else None

    cur.execute.side_effect = execute
    type(cur).description = property(lambda self: state["desc"])
    cur.fetchone.return_value = (7,)
    cur.fetchall.return_value = [("rel3",), ("rel3",)]
    result = store.execute_transaction(
        [
            "__capture_edge_hwm__ SELECT COALESCE(MAX(edge_id), 0) FROM rdf_edges",
            "INSERT INTO rdf_edges (s) SELECT 1",
            "__after_result__ DELETE FROM rdf_edges WHERE edge_id IN (SELECT 1) AND edge_id <= ?",
            "SELECT t2_name FROM x",
        ],
        [[], [], ["__EDGE_HWM__"], []],
    )
    assert order == [
        "START TRANSACTION",
        "SELECT COALESCE(MAX(edge_id), 0) FROM rdf_edges",
        "INSERT INTO rdf_edges (s) SELECT 1",
        "SELECT t2_name FROM x",
        "DELETE FROM rdf_edges WHERE edge_id IN (SELECT 1) AND edge_id <= ?",
    ]
    assert cur.execute.call_args_list[-1].args[1] == [7]
    # the deferred DELETE is not a reason to read the result before the writes
    assert result.columns == ["t2_name"]
    assert result.rows == [["rel3"], ["rel3"]]
    conn.commit.assert_called_once()
