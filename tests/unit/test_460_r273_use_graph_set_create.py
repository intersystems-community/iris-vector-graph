"""4.6.0 R273 (the USE GRAPH case), pinned against the translation (no container): under USE GRAPH, a CREATE after a
SET and a WITH reads the stage from the rows the SET captured, without the MATCH's stale test on the written key.

The answers are proven live in tests/e2e/test_460_r273_use_graph_set_create_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _statements(q: str) -> list:
    t = translate_to_sql(parse_query(q), {})
    return t.sql if isinstance(t.sql, list) else [t.sql]


@pytest.mark.parametrize("use", ["USE GRAPH 'g' ", ""])
def test_the_create_s_stage_reads_the_captured_rows(use):
    stmts = _statements(use + "MATCH (n {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H {v: k})")
    creates = [s for s in stmts if "INSERT INTO" in s and "Stage1 AS (" in s]
    assert creates, stmts
    for s in creates:
        stage = s.split("Stage1 AS (", 1)[1].split("\n)", 1)[0]
        assert "IN (__IDSALL_w0__)" in stage, stage
        assert "val IN ('1', '1.0')" not in stage and "\"key\" = 'k')" not in stage, stage


def test_a_key_the_query_does_not_write_keeps_its_test_under_use_graph():
    stmts = _statements("USE GRAPH 'g' MATCH (n {j: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H {v: k})")
    stage = next(s for s in stmts if "INSERT INTO" in s and "Stage1 AS (" in s).split("Stage1 AS (", 1)[1]
    assert "IN ('1', '1.0')" in stage.split("\n)", 1)[0]
