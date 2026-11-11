"""4.7.0 (full E2E gate on ea5ced01): a range filter against a $parameter binds as many values as it has markers.

tests/e2e/test_460_r307_value_matrix_e2e.py::test_filter[ge_param-node-*] / [gt_param-node-*] failed for every
writer and value on both executors: `<ARGUMENT ERROR> ... Incorrect number of parameters`. b9c12438 (row 24) keeps
the plain `CAST(val AS DOUBLE) >= ?` beside the typed comparison for IRIS's plan, and that copy repeated the
parameter's `?` without binding it again: 5 markers, 4 values for `MATCH (n:L) WHERE n.v >= $p RETURN n.v`.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

OPS = (">=", ">", "<", "<=")
QUERIES = (
    [f"MATCH (n:L) WHERE n.v {op} $p RETURN n.v" for op in OPS]
    + [f"MATCH (n:L) WHERE $p {op} n.v RETURN n.v" for op in OPS]
    + [f"MATCH (n:L) WHERE n.k = $k AND n.v {op} $p RETURN n" for op in OPS]
    + [f"MATCH ()-[r:T]->() WHERE r.v {op} $p RETURN r.v" for op in OPS]
    + [f"MATCH (n:L) WHERE n.v {op} 5 RETURN n.v" for op in OPS]
)


def _statements(query, params):
    r = translate_to_sql(parse_query(query), params)
    sqls = r.sql if isinstance(r.sql, list) else [r.sql]
    binds = r.parameters if isinstance(r.sql, list) else [r.parameters[0] if r.parameters else []]
    return list(zip(sqls, binds))


@pytest.mark.parametrize("value", [5, 0.5, -3])
@pytest.mark.parametrize("query", QUERIES)
def test_each_marker_has_a_value(query, value):
    for sql, binds in _statements(query, {"p": value, "k": "a"}):
        assert sql.count("?") == len(binds), (query, sql, binds)


def test_the_long_list_fallback_has_no_plain_copy():
    """4.7.0 gate, test_460_r310 at n = 200: the 2026.3 long-list fallback (`unwind_by_index`) failed at Prepare
    (<LIST>LoadTableFunction+10^%qTable) with the plain `CAST(val AS DOUBLE)` copy beside the typed comparison; with
    either alone it answered 39,800. The usual translation keeps the copy (row 24's plan)."""
    from iris_vector_graph.cypher.translator import unwind_by_index

    q = ("MATCH (n:L310D) WHERE n.v <= 200 WITH collect(n) AS ns UNWIND range(0, size(ns) - 1) AS i "
         "UNWIND range(0, size(ns) - 1) AS j WITH i, j WHERE i <> j RETURN count(*) AS c")
    plain = "(CAST(p2.val AS DOUBLE) < 200)"
    usual = translate_to_sql(parse_query(q), {}).sql
    with unwind_by_index([]) as used:
        fallback = translate_to_sql(parse_query(q), {}).sql
    assert used == ["u3", "u4"]
    assert plain in usual and "BETWEEN 4 AND 9" in usual
    assert plain not in fallback and "BETWEEN 4 AND 9" in fallback
