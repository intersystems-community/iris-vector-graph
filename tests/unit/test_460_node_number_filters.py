"""4.6.0 R168: a node-property filter against a number accepts every stored spelling of the number.

`rdf_props.val` is VARCHAR. `create_node(properties={"score": 0.95})` and the bulk loaders wrote `str(v)`, the text
`0.95`; a Cypher CREATE or SET binds the float, which IRIS keeps as a `$DOUBLE` whose text is `.94999999999999995559`
(probed on IVG460FXC). `WHERE n.score = 0.95` compared the column with an inlined `0.95` (the text `.95`: neither
matched), `= $v`, `{score: $v}` and `{score: 0.95}` with a bound or cast double (the `$DOUBLE` alone matched), so the
API's `0.95` never matched. The comparison is now: the text is one of the spellings, or the value equals the double.
R121 and R139 fixed the same comparison on relationships by naming every spelling (`prop_values.number_spellings`);
the node filters now use that comparison too.

4.7.0 row 24: a number filter matches a stored number of that value only. The row's `$LISTBUILD` type byte tells a
number from the string that spells it, so the strings '.95' and '0.95' no longer match (`<>` holds for them), nor does
the API's text from before 4.6.4. The live answers are in `tests/e2e/test_470_number_filters_e2e.py`; this pins the
SQL for each shape.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

SPELLED = "IN ('.95', '0.95')"


def _tr(query, params=None):
    return translate_to_sql(parse_query(query), dict(params or {}))


def _sql(query, params=None):
    sql = _tr(query, params).sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def _params(query, params=None):
    return [p for group in _tr(query, params).parameters for p in group]


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:L) WHERE n.score = 0.95 RETURN n.id",
        "MATCH (n:L) WHERE n.score = $v RETURN n.id",
        "MATCH (n:L) WHERE $v = n.score RETURN n.id",
        "MATCH (n:L {score: 0.95}) RETURN n.id",
        "MATCH (n:L {score: $v}) RETURN n.id",
        "MATCH (a:L {id: 'a'})-[:R]->(n {score: $v}) RETURN n.id",
        "MATCH (a:L {id: 'a'})-[:R]-(n {score: 0.95}) RETURN n.id",
    ],
    ids=["where_literal", "where_param", "param_on_left", "inline_literal", "inline_param", "inline_on_target",
         "inline_on_undirected_target"],
)
def test_a_number_compares_a_stored_number_only(query):
    sql = _sql(query, {"v": 0.95})
    typed = r"\(ASCII\(\$LISTBUILD\(%EXACT\((p\d+)\.val\)\)\) > 0 AND ASCII\(SUBSTRING\(\$LISTBUILD\(%EXACT\(\1\.val\)\), 2, 1\)\) BETWEEN 4 AND 9\)"
    assert re.search(typed + r" AND CAST\(\1\.val AS DOUBLE\) = CAST\('0\.95' AS DOUBLE\)", sql), sql
    assert "IN ('.95', '0.95')" not in sql, sql
    assert 0.95 not in _params(query, {"v": 0.95}), "inlined, not bound"


def test_not_equal_holds_for_anything_but_that_number():
    sql = _sql("MATCH (n:L) WHERE n.score <> $v RETURN n.id", {"v": 0.95})
    assert "(p2.val IS NOT NULL AND NOT ((ASCII($LISTBUILD(%EXACT(p2.val))) > 0" in sql, sql
    assert "AND CAST(p2.val AS DOUBLE) = CAST('0.95' AS DOUBLE)))" in sql, sql


def test_a_whole_number_compares_as_a_number():
    # 5.0 = 5 in Cypher: the stored integer 5 and the $DOUBLE 5.0 both compare equal as doubles
    sql = _sql("MATCH (n:L) WHERE n.score = 5.0 RETURN n.id")
    assert "CAST(p2.val AS DOUBLE) = CAST('5.0' AS DOUBLE)" in sql and "IN ('5', '5.0')" not in sql, sql


def test_a_string_filter_is_unchanged():
    q = "MATCH (n:L) WHERE n.score = $v RETURN n.id"
    assert "IN (" not in _sql(q, {"v": ".95"}) and ".95" in _params(q, {"v": ".95"})


def test_a_boolean_filter_still_names_every_boolean_spelling():
    assert "IN ('true', '1', 'True')" in _sql("MATCH (n:L) WHERE n.flag = true RETURN n.id")


def test_an_inline_parameter_number_binds_nothing_else():
    """`{score: $v}` used to bind 0.95 for the comparison: no parameter may be left over now that it is inlined."""
    tr = _tr("MATCH (n:L {score: $v}) RETURN n.id", {"v": 0.95})
    assert tr.sql.count("?") == sum(len(g) for g in tr.parameters), (tr.sql, tr.parameters)


def test_a_relationship_property_gets_no_double_comparison():
    """`qualifiers` is JSON text: a number in it is never a $DOUBLE."""
    sql = _sql("MATCH (a:L {id: 'a'})-[r:R]->(b) WHERE r.c = 0.95 RETURN b.id")
    assert SPELLED in sql and "AS DOUBLE" not in sql, sql


@pytest.mark.parametrize("query", ["MERGE (n:L {score: $v}) RETURN n.id", "MERGE (n:L {score: 0.95}) RETURN n.id"])
def test_merge_finds_the_existing_node_by_every_form_of_the_number(query):
    """MERGE's existence probe compared the text with `str(v)` ('0.95'): it missed a node a Cypher CREATE wrote (a
    $DOUBLE), so MERGE made a second one."""
    tr = _tr(query, {"v": 0.95})
    statements = tr.sql if isinstance(tr.sql, list) else [tr.sql]
    assert re.search(r"\((_?\w+)\.val IN \('\.95', '0\.95'\) OR \1\.val = CAST\('0\.95' AS DOUBLE\)\)",
                     "\n".join(statements)), statements
    assert [s.count("?") for s in statements] == [len(p) for p in tr.parameters], (statements, tr.parameters)
