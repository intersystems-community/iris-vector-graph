"""4.6.0 R310: an UNWIND whose list is computed from an earlier stage's column is not a correlated JSON_TABLE argument
(fixes64). The answers are pinned live in tests/e2e/test_460_r310_unwind_long_list_e2e.py.

On 2026.3, ``StageN CROSS JOIN JSON_TABLE(<expression over StageN.col>, ...)`` makes IRIS key a ``^||%t`` temporary
by the text of the column the expression reads. When the column is a collected node list longer than 511 characters,
the statement fails ``-400 <SUBSCRIPT>`` (measured by hand: GROUP BY, DISTINCT and NOT EXISTS have nothing to do with
it, and wrapping the column in REPLACE, SUBSTRING or ``|| ''`` does not help). The TCK setup of Match4 [4]
(``UNWIND range(0, size(nodeList) - 2, 1)`` over ``[a] + collect(n) + [b]``) failed this way, so the scenario ran on
an empty graph. The list is now computed as a column of the stage, ``(SELECT StageN.*, <expr> AS __uK FROM %NOREDUCE
StageN) StageN``, and JSON_TABLE reads that column. Without ``%NOREDUCE`` IRIS merges the derived table back and the
key is the long column again. A bare column (``UNWIND ns AS x``) is read as it was.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

MATCH4_4 = """CREATE (a {var: 'start'}), (b {var: 'end'})
WITH *
UNWIND range(1, 20) AS i
CREATE (n {var: i})
WITH a, b, [a] + collect(n) + [b] AS nodeList
UNWIND range(0, size(nodeList) - 2, 1) AS i
WITH nodeList[i] AS n1, nodeList[i+1] AS n2
CREATE (n1)-[:T]->(n2)"""

_JT_ARG = re.compile(r"JSON_TABLE\((.*?), '\$\[\*\]' COLUMNS", re.S)


def _sql(cypher: str, params=None) -> str:
    sql = translate_to_sql(parse_query(cypher), params).sql
    return "\n;\n".join(sql) if isinstance(sql, list) else sql  # a write is a list of statements


def _unwind_args(sql: str) -> list:
    """The first argument of every UNWIND JSON_TABLE (`CROSS JOIN JSON_TABLE(...)` or a FROM of one)."""
    return [m.group(1) for m in _JT_ARG.finditer(sql)]


def test_a_range_over_a_collected_list_reads_a_hoisted_column():
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND range(0, size(ns) - 1) AS i RETURN count(i) AS c")
    [arg] = _unwind_args(sql)
    assert re.fullmatch(r"Stage1\.__u\d+", arg), sql
    assert re.search(r"\(SELECT Stage1\.\*, .*IVGRANGE.* AS __u\d+ FROM %NOREDUCE Stage1\) Stage1", sql, re.S), sql


def test_the_match4_setup_reads_no_expression_over_a_stage_column():
    sql = _sql(MATCH4_4)
    for arg in _unwind_args(sql):
        assert not re.search(r"\bStage\d+\.", arg) or re.fullmatch(r"Stage\d+\.\w+", arg), arg
    assert "%NOREDUCE Stage2) Stage2" in sql, sql


def test_a_bare_column_is_read_as_it_was():
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND ns AS x RETURN count(x) AS c")
    assert "%NOREDUCE" not in sql, sql
    assert _unwind_args(sql) == ["Stage1.ns"], sql


def test_a_list_with_a_parameter_is_read_as_it_was():
    """Its `?` would move from the join's parameters to the FROM's: left alone."""
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND range(0, size(ns) - $k) AS i RETURN count(i) AS c", {"k": 1})
    assert "%NOREDUCE" not in sql, sql


def test_a_literal_list_is_read_as_it_was():
    sql = _sql("UNWIND range(1, 3) AS i RETURN i")
    assert "%NOREDUCE" not in sql, sql


def test_a_property_of_an_indexed_collected_node_reads_its_property_row():
    """`collect(n)[i].k`: the element is the node's JSON ({"_id", "_labels", "_props"}), whose `$.k` is no key of
    it; it answered null for every index (4.6.0 R310, found beside Match4 [4])."""
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns RETURN ns[0].var AS v")
    assert "WHERE s = Graph_KG.JSON_VALUE(Graph_KG.JSON_ARRAYGET(Stage1.ns, 0), '$._id') AND \"key\" = 'var'" in sql, sql


def test_the_node_id_of_an_indexed_collected_node_is_its_json_id():
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns RETURN ns[0].node_id AS v")
    assert "THEN Graph_KG.JSON_VALUE(Graph_KG.JSON_ARRAYGET(Stage1.ns, 0), '$._id')" in sql, sql


def test_a_property_of_an_indexed_map_list_still_reads_the_map():
    sql = _sql("WITH [{var: 1}] AS ms RETURN ms[0].var AS v")
    assert "'$._id'" not in sql, sql


def test_a_with_alias_of_an_indexed_collected_node_reads_its_property_row():
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns WITH ns[0] AS m RETURN m.var AS v")
    assert "WHERE s = Graph_KG.JSON_VALUE(Stage2.m, '$._id') AND \"key\" = 'var'" in sql, sql


def test_an_unwound_collected_node_carried_by_a_with_reads_its_property_row():
    sql = _sql("MATCH (n:L) WITH collect(n) AS ns UNWIND ns AS x WITH x RETURN x.var AS v")
    assert "WHERE s = Graph_KG.JSON_VALUE(Stage2.x, '$._id') AND \"key\" = 'var'" in sql, sql
