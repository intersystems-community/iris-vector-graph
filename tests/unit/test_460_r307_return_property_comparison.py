"""4.6.0 R307: a range comparison of two properties in RETURN or WITH compares by Cypher orderability, as WHERE does.

`RETURN a.p < b.p` compared CAST(val AS DOUBLE) on both sides, so two strings (each 0) were never less than each
other and two durations answered false where openCypher answers null (CIP2015-08-06-date-time). `WHERE a.p < b.p`
already calls CY_CMP, which ranks values by their sort key and answers null across kinds and between durations.
Found by tests/e2e/test_460_r307_value_matrix_e2e.py::test_durations_do_not_compare.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q", [
    "MATCH (a:L), (b:L) RETURN a.p < b.p",
    "MATCH (a:L), (b:L) RETURN a.p >= b.p AS ge",
    "MATCH (a:L), (b:L) WITH a.p > b.p AS gt RETURN gt",
])
def test_two_properties_compare_by_cy_cmp(q):
    sql = translate_to_sql(parse_query(q), {}).sql
    assert "CY_CMP(" in sql, sql
    assert "AS DOUBLE) <" not in sql and "AS DOUBLE) >" not in sql, sql


def test_where_is_unchanged():
    sql = translate_to_sql(parse_query("MATCH (a:L), (b:L) WHERE a.p < b.p RETURN a.id"), {}).sql
    assert "CY_CMP(" in sql
