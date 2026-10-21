"""Spec 253, FR-013: under a LIMIT with no ORDER BY, shadow mode holds the native rows to the rows the query could
return, not to the SQL path's own pick.

Both paths may return any `limit` of the matching rows. The comparison already took the full answer
(`sql_unlimited_rows`); the engine never passed it, so a native answer that picked other rows than the SQL path (a
split scan's workers stop in another order) failed as "a row the query cannot return". The engine runs the SQL path
once more without the LIMIT for such a query.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.native_shadow import NativeMismatch
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

Q = "MATCH (m:P) RETURN m.sid LIMIT 2"


def _engine(full_rows):
    e = IRISGraphEngine(MagicMock(), native_cypher=True)
    e._execute_parsed_sql = MagicMock(return_value=IVGResult(columns=["m_sid"], rows=full_rows))
    return e


def test_native_rows_the_query_can_return_pass():
    e = _engine([["a"], ["b"], ["c"]])
    plan = {"limited": True, "ordered": False}
    sql = IVGResult(columns=["m_sid"], rows=[["a"], ["b"]])
    native = IVGResult(columns=["m_sid"], rows=[["c"], ["a"]])
    e._shadow_compare(parse_query(Q), {}, plan, native, sql)
    unlimited = e._execute_parsed_sql.call_args.args[0]
    assert unlimited.limit is None


def test_a_row_outside_the_full_answer_still_fails():
    e = _engine([["a"], ["b"], ["c"]])
    plan = {"limited": True, "ordered": False}
    with pytest.raises(NativeMismatch):
        native = IVGResult(columns=["m_sid"], rows=[["z"], ["a"]])
        e._shadow_compare(parse_query(Q), {}, plan, native, IVGResult(columns=["m_sid"], rows=[["a"], ["b"]]))


def test_an_ordered_or_unlimited_query_runs_sql_once():
    e = _engine([])
    for plan in ({"limited": False, "ordered": False}, {"limited": True, "ordered": True}):
        rows = [["a"], ["b"]]
        e._shadow_compare(parse_query(Q), {}, plan, IVGResult(columns=["m_sid"], rows=rows), IVGResult(columns=["m_sid"], rows=rows))
    e._execute_parsed_sql.assert_not_called()


def test_the_unlimited_rerun_drops_a_param_clash_twin():
    """A parameter named as a variable makes the translator keep a renamed copy of the tree (param_twin), LIMIT
    included: the rerun must not carry it."""
    q = parse_query("MATCH (m:P {sid: $m}) RETURN m.sid LIMIT 2")
    translate_to_sql(q, {"m": "x"})
    e = _engine([["a"], ["b"], ["c"]])
    rows = IVGResult(columns=["m_sid"], rows=[["a"], ["b"]])
    e._shadow_compare(q, {"m": "x"}, {"limited": True, "ordered": False}, rows, rows)
    rerun = e._execute_parsed_sql.call_args.args[0]
    assert "FETCH FIRST" not in translate_to_sql(rerun, {"m": "x"}).sql
