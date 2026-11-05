"""4.6.0 R295, offline: what the translation of fixes45's leftovers now says. The answers are checked live in
tests/e2e/test_460_r295_fixes45_leftovers_e2e.py; this pins the statement shapes and the executor's reads."""

from __future__ import annotations

import re
from pathlib import Path

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

ROOT = Path(__file__).resolve().parents[2]


def _sql(q: str) -> str:
    t = translate_to_sql(parse_query(q), {})
    return t.sql if isinstance(t.sql, str) else "\n".join(s if isinstance(s, str) else s[0] for s in t.sql)


def test_a_range_is_unwound_into_an_integer_column_and_its_arithmetic_is_numeric():
    assert "COLUMNS (i INTEGER PATH '$')" in _sql("UNWIND range(1, 2) AS i WITH i RETURN i")
    sql = _sql("UNWIND range(5, 6) AS i RETURN i + 1 AS j")
    assert "LIST_CONCAT" not in sql, sql
    # any other list keeps its text column: its elements may be of any kind
    assert "VARCHAR(1000) PATH '$'" in _sql("WITH [1, 'a'] AS l UNWIND l AS x RETURN x")


def test_id_and_element_id_of_a_staged_node_read_its_stage_column():
    for q in ("MATCH (n:L) WITH n RETURN id(n)", "MATCH (n:L) WITH n RETURN elementId(n)"):
        sql = _sql(q)
        assert "Stage1.node_id" not in sql and "Graph_KG.elementId(" not in sql, sql
        assert re.search(r"SELECT (Stage1\.)?n AS", sql), sql
    assert re.search(r"SELECT n0\.node_id AS", _sql("MATCH (n:L) RETURN elementId(n)"))


def test_a_leading_optional_match_with_a_property_then_a_with_tests_the_match_itself():
    sql = _sql("OPTIONAL MATCH (n:L {name: 'zzz'}) WITH n RETURN n.name")
    assert "NOT EXISTS (SELECT 1 FROM (SELECT" in sql and "__om" in sql, sql
    # a label alone still tests the label
    sql = _sql("OPTIONAL MATCH (n:L) WITH n RETURN n.name")
    assert "__om" not in sql and "rdf_labels WHERE label = ?" in sql, sql


def test_an_unlabelled_start_to_a_labelled_target_has_its_null_row():
    sql = _sql("OPTIONAL MATCH (x {name: 'b'})-[r]->(:L) RETURN r")
    assert "UNION ALL" in sql and "__om" in sql, sql


def test_the_native_executor_reads_a_stored_empty_string_as_empty():
    """Every direct read of a property value (Props.Get, and the executor's own reads of the property data global)
    turns $Char(0), IRIS's spelling of SQL's '', into "", and an anchor lookup binds the string '' as $Char(0)."""
    props = (ROOT / "iris_src/src/Graph/KG/Cypher/Props.cls").read_text()
    get = props[props.index("ClassMethod Get("):props.index("ClassMethod Begin(")]
    assert get.count("$Select(tVal = $Char(0): \"\", 1: tVal)") == 2
    exec_src = (ROOT / "iris_src/src/Graph/KG/Cypher/Exec.cls").read_text()
    reads = [m.start() for m in re.finditer(r"\$ListGet\(\$Get\(@(?:pQ\(\"pdata\"\)|tData)@\(tId\)\)", exec_src)]
    assert reads, "no direct reads found"
    for at in reads:
        following = exec_src[at:at + 260]
        assert "$Char(0)" in following, exec_src[at - 120:at + 260]
    assert exec_src.count('Set:tVal="" tVal = $Char(0)') == 2
