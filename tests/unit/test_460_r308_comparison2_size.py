"""4.6.0 R308 follow-up, offline: a node value's property JSON reads CY_JSON_LISTMAP once.

TCK Comparison2 [3] (`<=` and `>=`) failed at Prepare on 2026.3 with -400 <LIST>LoadTableFunction+10^%qTable after
R307 put `WHEN CY_JSON_LISTMAP(val) IS NOT NULL THEN CY_JSON_LISTMAP(val)` into `_node_value_json_text`: the
query's `[n, r, p, ...]` list of a whole node carries that text in a JSON_TABLE source, and the two extra calls took
the statement past what IRIS loads (measured: the R307 line alone fails; the same line with the list/map test in the
ELSE's COALESCE answers). The ELSE is reached only by a value no earlier branch takes, which is never a stored number,
so JSON_ARRAY there cannot raise <ILLEGAL VALUE> (the reason the branches are a CASE, R307). Live:
tests/e2e/test_460_r308_cypher_differential_e2e.py::test_comparing_across_types_answers.
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


@pytest.mark.parametrize("fn", ["size(x)", "abs(n.k)", "floor(n.k)", "sqrt(n.k)", "round(n.k)", "sign(n.k)"])
def test_a_function_answering_a_number_is_not_type_checked(fn):
    sql = translate_to_sql(parse_query(f"MATCH (n:L) WITH n, [1] AS x RETURN {fn} - 1 AS v"), {}).sql
    assert "- needs two numbers" not in sql, sql


def test_every_unwind_over_the_stage_reads_a_hoisted_column():
    """The second UNWIND over the same stage is hoisted into R310's derived table too: its JSON_TABLE read the long
    `types` column, and a 20-element list failed at Fetch (-400 <SUBSCRIPT> ^||%t keyed by the column's text)."""
    sql = translate_to_sql(parse_query(Q), {}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    sources = re.findall(r"JSON_TABLE\(([^,]*)", sql)
    assert sources and all(re.fullmatch(r"Stage\d+\.__u\d+", s) for s in sources), sources
    assert sql.count("FROM %NOREDUCE Stage1) Stage1") == 1, sql
