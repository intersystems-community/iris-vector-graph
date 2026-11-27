"""4.7.2: 4.7.0 row 24's number rule (a stored number only) holds of every way a node's property is read.

`_is_node_val_sql` says which SQL reads a node property row's `val` as stored: the join's column, or the one scalar
subquery a carried, unwound or collected node's property reads. The answers on a live namespace are
tests/e2e/test_472_number_filter_shapes_e2e.py; this pins the SQL each shape is translated to.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _is_node_val_sql, translate_to_sql

SUBQUERY = '(SELECT val FROM Graph_KG.rdf_props WHERE s = Graph_KG.JSON_VALUE(u2.x, \'$._id\') AND "key" = \'p\')'


@pytest.mark.parametrize("sql", ["p2.val", " p12.val ", "_mp0.val", SUBQUERY, "(SELECT val FROM rdf_props WHERE s = n.id)"])
def test_these_read_a_node_property_row(sql):
    assert _is_node_val_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "p2.val_key",
        "CAST(p2.val AS DOUBLE)",
        "(SELECT val FROM rdf_props WHERE s = a) + (SELECT val FROM rdf_props WHERE s = b)",
        "(SELECT val FROM rdf_props WHERE s = 'it''s (a)') = 5",
        "(SELECT label FROM rdf_labels WHERE s = a)",
        "r.qualifiers",
    ],
)
def test_these_do_not(sql):
    assert not _is_node_val_sql(sql)


def _sql(cypher):
    out = translate_to_sql(parse_query(cypher)).sql
    return out if isinstance(out, str) else " ".join(out)


@pytest.mark.parametrize("op", [">=", ">", "<", "<="])
def test_a_range_after_with_reads_the_stored_number(op):
    sql = _sql(f"MATCH (x:T) WITH x WHERE x.p {op} 0.95 RETURN x.id")
    assert "$LISTBUILD(%EXACT(p2.val))" in sql, sql
    assert f"p2.val {op} CAST('0.95' AS DOUBLE)" not in sql, "the row's text is compared with the number"


@pytest.mark.parametrize("op", [">=", "<="])
def test_a_range_over_an_unwound_collected_node_reads_the_stored_number(op):
    sql = _sql(f"MATCH (n:T) WITH collect(n) AS ns UNWIND ns AS y WITH y WHERE y.p {op} 0.95 RETURN y.id")
    assert "$LISTBUILD(%EXACT((SELECT val FROM" in sql, sql


@pytest.mark.parametrize("cmp", ["=", "<>"])
def test_equality_over_an_unwound_collected_node_is_the_stored_number_test(cmp):
    sql = _sql(f"MATCH (n:T) WITH collect(n) AS ns UNWIND ns AS y WITH y WHERE y.p {cmp} 5 RETURN y.id")
    assert "$LISTBUILD(%EXACT((SELECT val FROM" in sql, sql


def test_a_string_comparison_over_an_unwound_node_is_untouched():
    sql = _sql("MATCH (n:T) WITH collect(n) AS ns UNWIND ns AS y WITH y WHERE y.p >= 'a' RETURN y.id")
    assert "$LISTBUILD" not in sql, sql
