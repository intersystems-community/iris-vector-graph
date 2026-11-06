"""4.6.0 R310: the 2026.3 long-list UNWIND fallback (fixes64). Pinned live in
tests/e2e/test_460_r310_unwind_by_index_e2e.py.

On 2026.3 a SELECT that groups (GROUP BY, DISTINCT, an aggregate, a later aggregating WITH) over `StageN CROSS JOIN
JSON_TABLE(<list from StageN>)` keys a ^||%t temporary by the JSON_TABLE argument and fails `-400 <SUBSCRIPT>` once the
list's text passes 511 characters. Measured by hand: wrapping, hoisting and a row-key subquery do not help (the last
answers wrongly: IRIS evaluates the subquery once for every row). What answers: the list's positions from two short
number ranges (blocks of 256 and a position within one), materialised with the stage row in a %NOREDUCE derived table,
and each element read back through a one-element JSON_TABLE so it has JSON_TABLE's own types. It reads the list once per
element, so it is quadratic in the list's length, and runs only when the first statement failed with that error.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, unwind_by_index

GROUPED = "MATCH (n:L) WITH n.k AS k, collect(n) AS ns UNWIND ns AS x RETURN k, count(x) AS c"
SIGNATURE = (
    "<SQL ERROR>; Details: [SQLCODE: <-400>:<Fatal error occurred>] [Location: <ServerLoop - Query Fetch>] "
    '[%msg: <Unexpected error occurred:  <SUBSCRIPT>%0AmOk2+19^%sqlcq.NS.cls1.1 ^||%t(1,"["{\\"_id\\"...>]'
)


def _sql(cypher, params=None):
    sql = translate_to_sql(parse_query(cypher), params).sql
    return "\n;\n".join(sql) if isinstance(sql, list) else sql


class TestTranslation:
    def test_without_the_flag_the_unwind_is_a_json_table_of_the_list(self):
        assert "JSON_TABLE(Stage1.ns," in _sql(GROUPED)

    def test_with_the_flag_the_list_is_read_by_position(self):
        used: list = []
        with unwind_by_index(used):
            sql = _sql(GROUPED)
        assert used, "the flag records that it rewrote an UNWIND"
        assert "JSON_TABLE(Stage1.ns," not in sql, sql
        assert "FROM %NOREDUCE Stage1" in sql, sql
        assert "CY_LIST_ELEM(Stage1.__u" in sql and "'$' COLUMNS (x VARCHAR(1000) PATH '$.v')) u" in sql, sql
        assert "CypherFn_IVGRANGE(0, 255, 1)" in sql, sql

    def test_a_range_keeps_its_integer_column(self):
        used: list = []
        with unwind_by_index(used):
            sql = _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND range(0, size(ns) - 1) AS i RETURN i % 3 AS m, count(*) AS c")
        assert used and "COLUMNS (i INTEGER PATH '$.v')" in sql, sql

    def test_a_literal_list_is_not_rewritten(self):
        used: list = []
        with unwind_by_index(used):
            sql = _sql("UNWIND [1, 2, 3] AS i RETURN count(i) AS c")
        assert not used and "CY_LIST_ELEM" not in sql, sql

    def test_a_list_with_a_parameter_is_not_rewritten(self):
        used: list = []
        with unwind_by_index(used):
            _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND range(0, size(ns) - $k) AS i RETURN count(i) AS c", {"k": 1})
        assert not used

    def test_the_flag_is_off_outside_its_block(self):
        with unwind_by_index([]):
            pass
        assert "JSON_TABLE(Stage1.ns," in _sql(GROUPED)


class TestSignature:
    def test_the_exact_error_is_recognised(self):
        from iris_vector_graph._engine.query import _is_long_list_temp_error

        assert _is_long_list_temp_error(SIGNATURE)

    @pytest.mark.parametrize("msg", [
        None,
        "",
        "SQLCODE: <-400> <SUBSCRIPT> somewhere else",  # no ^||%t(
        '[SQLCODE: <-400>] ^||%t(1,"x") <UNDEFINED>',  # not a SUBSCRIPT
        '[SQLCODE: <-1>] <SUBSCRIPT> ^||%t(1,"x")',  # not -400
    ])
    def test_another_error_is_not(self, msg):
        from iris_vector_graph._engine.query import _is_long_list_temp_error

        assert not _is_long_list_temp_error(msg)


class _Store:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sql = []

    def execute_sql(self, sql, params):
        self.sql.append(sql)
        err, rows = self.answers.pop(0)
        return SimpleNamespace(error=err, rows=rows, columns=["k", "c"], metadata=None)


def _engine(answers):
    from iris_vector_graph._engine.query import QueryMixin

    e = QueryMixin.__new__(QueryMixin)
    e._store = _Store(answers)
    e._store_capabilities = {"native_sql": True}
    e._schema_prefix = "Graph_KG"
    e._fetch_first_unsafe = False
    e.get_table_mapping = lambda *a, **k: None
    e.get_rel_mapping = lambda *a, **k: None
    return e


class TestRetry:
    def test_the_signature_retries_once_by_position(self, monkeypatch, caplog):
        import iris_vector_graph._engine.query as q

        monkeypatch.setattr(q, "_finish_sql_result", lambda r, s, **k: r)
        e = _engine([(SIGNATURE, []), (None, [[0, 15], [1, 15]])])
        with caplog.at_level(logging.INFO, logger=q.logger.name):
            r = e._execute_parsed_sql(parse_query(GROUPED), {})
            r2 = _engine([(SIGNATURE, []), (None, [[0, 1]])])
            r2._unwind_index_logged = e._unwind_index_logged
            r2._execute_parsed_sql(parse_query(GROUPED), {})
        assert r.error is None and r.rows == [[0, 15], [1, 15]]
        assert len(e._store.sql) == 2
        assert "JSON_TABLE(Stage1.ns," in e._store.sql[0] and "CY_LIST_ELEM" in e._store.sql[1]
        notes = [rec for rec in caplog.records if "UNWIND" in rec.getMessage()]
        assert len(notes) == 1 and notes[0].levelno == logging.INFO, [n.getMessage() for n in notes]

    def test_a_failed_fallback_answers_the_first_error_with_a_note(self, monkeypatch):
        import iris_vector_graph._engine.query as q

        monkeypatch.setattr(q, "_finish_sql_result", lambda r, s, **k: r)
        e = _engine([(SIGNATURE, []), ("SQLCODE -1 second", [])])
        r = e._execute_parsed_sql(parse_query(GROUPED), {})
        assert len(e._store.sql) == 2
        assert r.error.startswith(SIGNATURE) and "SQLCODE -1 second" in r.error and "fallback" in r.error

    def test_another_error_does_not_retry(self, monkeypatch):
        import iris_vector_graph._engine.query as q

        monkeypatch.setattr(q, "_finish_sql_result", lambda r, s, **k: r)
        e = _engine([("SQLCODE -29 Field not found", [])])
        r = e._execute_parsed_sql(parse_query(GROUPED), {})
        assert len(e._store.sql) == 1 and r.error == "SQLCODE -29 Field not found"

    def test_no_rewritable_unwind_does_not_retry(self, monkeypatch):
        import iris_vector_graph._engine.query as q

        monkeypatch.setattr(q, "_finish_sql_result", lambda r, s, **k: r)
        e = _engine([(SIGNATURE, [])])
        r = e._execute_parsed_sql(parse_query("MATCH (n:L) RETURN n.k AS k, count(*) AS c"), {})
        assert len(e._store.sql) == 1 and r.error == SIGNATURE


def test_a_second_unwind_over_the_same_stage_is_read_by_position_too():
    """`UNWIND range(..size(types)..) AS i UNWIND range(..size(types)..) AS j` (TCK Comparison2 [3] at 20 elements): the
    retry read only the first by position, and the second's JSON_TABLE of a range over the long list failed the same
    way, so the retry failed too."""
    used: list = []
    q = ("MATCH (n:L) WITH collect(n) AS types UNWIND range(0, size(types) - 1) AS i "
         "UNWIND range(0, size(types) - 1) AS j RETURN count(*) AS c")
    with unwind_by_index(used):
        sql = _sql(q)
    assert len(used) == 2, (used, sql)
    assert "CROSS JOIN JSON_TABLE(Graph_KG.CypherFn_IVGRANGE(0, (CASE" not in sql, sql
