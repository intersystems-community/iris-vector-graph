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
