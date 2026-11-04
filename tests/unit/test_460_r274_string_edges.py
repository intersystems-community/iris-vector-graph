"""4.6.0 R274 (the string and percentile cases), pinned against the source (no container).

Graph_KG.STR_SPLIT and Graph_KG.REGEX_MATCH, which the translator calls, read SQL NULL ("") as null and SQL's ''
($Char(0)) as the empty string, R269's rule for the CypherFn copies; `=~` is a predicate whose NOT keeps a null; and
IVG.Percentile.PCONT over no values is null. The answers are proven live in
tests/e2e/test_460_r274_string_edges_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema

ROOT = Path(__file__).resolve().parents[2]


def _udf(name: str) -> str:
    for stmt in GraphSchema.get_procedures_sql_list("Graph_KG"):
        if re.search(rf"FUNCTION\s+\w+\.{name}\(", stmt):
            return stmt
    raise AssertionError(f"no {name} in the procedure list")


def _sql(q: str) -> str:
    t = translate_to_sql(parse_query(q), {})
    return t.sql if isinstance(t.sql, str) else "\n".join(t.sql)


def test_regex_match_reads_null_then_the_empty_string():
    body = _udf("REGEX_MATCH")
    assert 'If (s = "") || (p = "") { Quit "" }' in body
    assert body.index('Quit "" }') < body.index("If s = $Char(0) { Set s = \"\" }")
    # the empty pattern matches only the empty string ($MATCH cannot take it)
    assert 'If p = $Char(0) { Quit (s = "") }' in body


def test_str_split_reads_null_then_the_empty_string_and_splits_by_nothing_into_characters():
    body = _udf("STR_SPLIT")
    assert 'If (str = "") || (delim = "") { Quit "" }' in body
    assert 'If str = $Char(0) { Set str = "" }' in body
    assert "If delim = $Char(0) {" in body and "$EXTRACT(str, i)" in body


def test_no_apostrophe_or_host_variable_shape_in_the_bodies():
    """A ' or a :name in a LANGUAGE OBJECTSCRIPT body breaks the DB-API's DDL tokenizer."""
    for name in ("REGEX_MATCH", "STR_SPLIT"):
        body = _udf(name).split("LANGUAGE OBJECTSCRIPT", 1)[1]
        assert "'" not in body and not re.search(r":\w", body), name


def test_regex_predicate_keeps_a_null_under_not():
    sql = _sql("WITH null AS s RETURN s =~ 'a*', NOT s =~ 'a*'")
    assert "(NULLIF(Graph_KG.REGEX_MATCH(Stage1.s, 'a*'), -1) = 1)" in sql
    # the RETURN item is the function's own 1/0/NULL, not a CASE over the predicate
    assert "SELECT NULLIF(Graph_KG.REGEX_MATCH(Stage1.s, 'a*'), -1) AS" in sql


def test_regex_in_a_where_is_the_predicate():
    sql = _sql("UNWIND ['a'] AS s WITH s WHERE NOT s =~ 'a*' RETURN s")
    assert "WHERE NOT ((NULLIF(Graph_KG.REGEX_MATCH(" in sql


def test_percentile_cont_of_no_values_is_null():
    text = (ROOT / "iris_src" / "src" / "IVG" / "Percentile.cls").read_text()
    m = re.search(r"^ClassMethod PCONT\(.*?^\}", text, re.S | re.M)
    assert m and 'If n = 0 { Quit "" }' in m.group(0)
