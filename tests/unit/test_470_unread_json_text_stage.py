"""4.7.0 gate: the JSON text column a later clause reads is kept, when the stage before it has no FROM.

e521a35a (rows 21/22) drops a WITH stage's JSON text column (`u0.e__j AS e__j`) that no later clause reads. Its stage
pattern matched lazily from `StageN AS (\\nSELECT` to the next `\\nFROM `, and a parameter-list UNWIND's first stage
(`Stage1 AS (\\nSELECT ? AS __l0\\n)`) has none: the match ran on into Stage2's columns and tested them as Stage1's, so
`Stage2.e__j` was dropped while the RETURN read it, -29 at Prepare (`UNWIND $p AS e WITH e ...` over a mixed-kind $p;
tests/e2e/test_470_unwind_set_e2e.py, test_470_unwind_wrong_node_e2e.py, test_470_unwind_param_rewrite_e2e.py on the
Studio gate).
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import (
    _drop_unread_json_texts,
    rewrite_ambiguous_param_unwind,
    translate_to_sql,
)

MIXED = {"p": ["1", [1], True]}


def _statements(q: str, params: dict) -> list:
    sql = translate_to_sql(rewrite_ambiguous_param_unwind(parse_query(q), params), params=params).sql
    return [sql] if isinstance(sql, str) else [s for s in sql if isinstance(s, str)]


def _undefined_stage_reads(sql: str) -> list:
    """`StageN.x` read where stage N's SELECT list does not name x."""
    defined = {}
    for m in re.finditer(r"(?<![\w.])(Stage\d+) AS \(\nSELECT ((?:(?!\n\)).)*?)\n(?:FROM |\))", sql, re.S):
        defined[m.group(1)] = {a.strip('"') for a in re.findall(r' AS ("[^"]+"|\w+)(?=,|$)', m.group(2))}
    return [f"{s}.{c}" for s, c in re.findall(r"(?<![\w.])(Stage\d+)\.(\w+__j)\b", sql) if c not in defined.get(s, {c})]


@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e WHERE e <> '1' RETURN e",
    "UNWIND $p AS e WITH e WHERE e = true MATCH (n:US {id: 'x'}) SET n.f = e",
    "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.f = e DETACH DELETE n",
    "UNWIND $p AS e WITH e MATCH (n:UW {id: e}) DETACH DELETE n",
])
def test_a_read_json_text_column_is_kept_after_a_stage_without_from(q):
    for sql in _statements(q, MIXED):
        assert _undefined_stage_reads(sql) == [], sql


def test_a_stage_without_from_does_not_lend_its_name_to_the_next():
    sql = (
        "WITH Stage1 AS (\nSELECT ? AS __l0\n),\nStage2 AS (\nSELECT u0.e AS e, u0.e__j AS e__j\n"
        "FROM (SELECT Stage1.* FROM Stage1) Stage1\n)\nSELECT CHAR(1) || 'J' || Stage2.e__j AS e\nFROM Stage2"
    )
    assert _drop_unread_json_texts(sql) == sql


def test_an_unread_json_text_column_is_still_dropped():
    sql = (
        "WITH Stage1 AS (\nSELECT ? AS __l0\n),\nStage2 AS (\nSELECT u0.e AS e, u0.e__j AS e__j\n"
        "FROM (SELECT Stage1.* FROM Stage1) Stage1\n)\nSELECT Stage2.e AS e\nFROM Stage2"
    )
    assert ", u0.e__j AS e__j" not in _drop_unread_json_texts(sql)
