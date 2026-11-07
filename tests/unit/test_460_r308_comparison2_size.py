"""4.6.0 R308 follow-up, offline: a node value's property JSON reads CY_JSON_LISTMAP once.

TCK Comparison2 [3] (`<=` and `>=`) failed at Prepare on 2026.3 with -400 <LIST>LoadTableFunction+10^%qTable after
R307 put `WHEN CY_JSON_LISTMAP(val) IS NOT NULL THEN CY_JSON_LISTMAP(val)` into `_node_value_json_text`: the
query's `[n, r, p, ...]` list of a whole node carries that text in a JSON_TABLE source, and the two extra calls took
the statement past what IRIS loads (measured: the R307 line alone fails; the same line with the list/map test in the
ELSE's COALESCE answers). The ELSE is reached only by a value no earlier branch takes, which is never a stored number,
so JSON_ARRAY there cannot raise <ILLEGAL VALUE> (the reason the branches are a CASE, R307). Live:
tests/e2e/test_460_r308_cypher_differential_e2e.py::test_comparing_across_types_answers.

Left for 4.6.1, the R308 sweep of wrappers that repeat an operand's SQL (each copy lengthens a statement and, read
from a stage column inside a JSON_TABLE source, can tip 2026.3's Prepare: docs/KNOWN_ISSUES.md "On IRIS 2026.3 a list
of many whole nodes unwound by position can fail at Prepare"):

- size() of a column of unknown kind names it three times (SUBSTRING, JSON_ARRAYLENGTH, CY_STR_SIZE); one UDF would
  name it once.
- `CASE WHEN x IS NULL THEN NULL ELSE ... x ... END` and ISNUMERIC checks repeat x in: _preprocess_order_by_items (5
  sites), _expr_function_body (3), _scalar_string (2), _expr_property_reference (2), _tts_select_result,
  _expr_property_access, _typed_json_value_sql, _expr_propref_edge_alias, _expr_arith, _trp_vlp_native,
  properties_subquery, _translate_set_value (1 each).
- _zero_if_null repeats the SUM (capped at 2000 characters); _row_error_sql repeats its operands inside the message.
- (done in R308 cmptypes: size(n.i) of a stored number raises TypeError at run time.)
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _node_value_json_text, translate_to_sql

Q = ("MATCH p = (n)-[r]->() WITH [n, r, p, '', 1, 3.14, true, null, [], {}] AS types "
     "UNWIND range(0, size(types) - 1) AS i UNWIND range(0, size(types) - 1) AS j "
     "WITH types[i] AS lhs, types[j] AS rhs WHERE i <> j WITH lhs, rhs, lhs <= rhs AS result WHERE result "
     "RETURN lhs, rhs")


def test_the_list_or_map_test_is_one_call():
    text = _node_value_json_text("val")
    assert text.count("CY_JSON_LISTMAP(val)") == 1, text
    assert "IS NOT NULL THEN Graph_KG.CY_JSON_LISTMAP" not in text, text


def test_the_numbers_and_booleans_still_come_first():
    text = _node_value_json_text("val")
    assert text.index("CY_JSON_NUM") < text.index("'true'") < text.index("CY_JSON_LISTMAP"), text


def test_comparison2_s_statement_reads_it_once_per_node():
    sql = translate_to_sql(parse_query(Q), {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    assert sql.count("CY_JSON_LISTMAP(val)") == 1, sql


def test_a_size_is_not_type_checked_as_an_arithmetic_operand():
    """`size(x) - 1` is a number or null: R297's runtime TypeError CASE around it named the list three more times, and
    each range bound of Comparison2 [3]'s two UNWINDs did that, which took the statement past what 2026.3 prepares
    (measured: with the check dropped the statement prepares with one graph and with two)."""
    sql = translate_to_sql(parse_query(Q), {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    assert "- needs two numbers" not in sql, sql
    assert "TypeError" not in translate_to_sql(parse_query("RETURN size([1, 2]) - 1 AS x"), {}).sql


def test_a_stored_value_is_still_checked_per_row():
    """The exemption is size() and length() only: a stored value beside it keeps its check (R300's shape)."""
    sql = translate_to_sql(parse_query("MATCH (n:L) RETURN n.k ^ size(n.s) AS x"), {}).sql
    assert sql.count("ISNUMERIC(") == 1 and "^ needs two numbers" in sql, sql


@pytest.mark.parametrize("q", ["RETURN size(5) AS s", "RETURN size(true) AS s", "RETURN size(5) - 1 AS s",
                               "RETURN size(2.5) AS s", "WITH 5 AS x RETURN size(x) AS s"])
def test_size_of_a_number_or_a_boolean_raises_type_error(q):
    """openCypher's size() takes a list or a string. A number or boolean known before the query runs raises TypeError
    here, as `2 ^ 'a'` does (R300). Before, size(5) and size(true) answered 1, and `size(5) - 1` 0.0: the arithmetic
    check R308 dropped never caught them, since size() answered a number."""
    with pytest.raises(TypeError, match="TypeError: InvalidArgumentType"):
        translate_to_sql(parse_query(q), {})


def test_size_of_a_list_or_string_still_answers():
    for q in ("RETURN size([1, 2]) AS s", "RETURN size('ab') AS s", "WITH [1] AS x RETURN size(x) - 1 AS s"):
        translate_to_sql(parse_query(q), {})


def test_size_is_not_type_checked_as_an_operand():
    sql = translate_to_sql(parse_query("MATCH (n:L) WITH n, [1] AS x RETURN size(x) - 1 AS v"), {}).sql
    assert "- needs two numbers" not in sql, sql


@pytest.mark.parametrize("fn", ["floor(n.k)", "sqrt(n.k)", "round(n.k)", "sign(n.k)", "ceil(n.k)", "log(n.k)",
                                "exp(n.k)"])
def test_a_math_function_keeps_its_operand_check(fn):
    """A math function of a stored value may answer a number for a bad argument at run time (R308 cmptypes raises
    TypeError for one that is no number, but a stored '1' passes ISNUMERIC): the operand check stays theirs (the
    lead's review of 6d0320d5)."""
    sql = translate_to_sql(parse_query(f"MATCH (n:L) WITH n, 'a' AS s RETURN {fn} - 1 AS v"), {}).sql
    assert "- needs two numbers" in sql, sql  # (abs() had no check on release either: its SQL is unchanged)


@pytest.mark.parametrize("fn", ["floor(s)", "sqrt(true)", "round('x')"])
def test_a_math_function_of_a_value_known_to_be_no_number_raises(fn):
    """R308 cmptypes: abs('a') answered 0 and sqrt(true) 1; openCypher raises TypeError
    (tests/unit/test_460_r308_math_size_types.py)."""
    with pytest.raises(TypeError, match="needs a number"):
        translate_to_sql(parse_query(f"MATCH (n:L) WITH n, 'a' AS s RETURN {fn} - 1 AS v"), {})


def test_every_unwind_over_the_stage_reads_a_hoisted_column():
    """The second UNWIND over the same stage is hoisted into R310's derived table too: its JSON_TABLE read the long
    `types` column, and a 20-element list failed at Fetch (-400 <SUBSCRIPT> ^||%t keyed by the column's text)."""
    sql = translate_to_sql(parse_query(Q), {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    sources = re.findall(r"JSON_TABLE\(([^,]*)", sql)
    assert sources and all(re.fullmatch(r"Stage\d+\.__u\d+", s) for s in sources), sources
    assert sql.count("FROM %NOREDUCE Stage1) Stage1") == 1, sql


def test_size_of_a_with_column_known_to_be_a_list_reads_it_once():
    """`WITH [..] AS types ... size(types)`: the column holds a list (a list literal), so size() is its
    JSON_ARRAYLENGTH, naming the column once; the list-or-string CASE named it three times in each UNWIND bound, and a
    40-element list failed at Prepare on 2026.3 (-400 <LIST>LoadTableFunction, `<=` and `>=`, one graph)."""
    sql = translate_to_sql(parse_query(Q), {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    assert "SUBSTRING(Stage1.types, 1, 1)" not in sql, sql
    assert "JSON_ARRAYLENGTH(Stage1.types)" in sql, sql
    # a column of unknown kind keeps the list-or-string dispatch
    other = translate_to_sql(parse_query("MATCH (n:L) WITH n.p AS x RETURN size(x) AS s"), {}).sql
    assert "CY_STR_SIZE" in other, other
