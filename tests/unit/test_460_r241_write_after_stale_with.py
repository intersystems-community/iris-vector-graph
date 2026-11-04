"""4.6.0 R241: a MERGE or CREATE after a WITH that reads a key an earlier SET changed writes the rows openCypher writes.

`MATCH (n:X {k: 1}) SET n.k = 5 WITH n WHERE n.k = 5 CREATE ...` wrote nothing: each statement after the WITH re-ran its
MATCH, which the SET had moved off the row (R208 refused a SET there; a CREATE or MERGE went through). The stage is now
built as R238's RETURN is: the MATCH's tests on the keys the query writes go, `n0.node_id IN (__IDSALL_<key>__)` keeps
the rows the SET captured, and the WITH reads them after the SET. A WITH that reads a label the writes changed is still
refused, a CREATE and MERGE now included (tests/unit/test_460_r208_write_after_with.py).

tests/e2e/test_460_r241_write_after_stale_with_e2e.py checks the graph after each write.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _stmts(q):
    r = translate_to_sql(parse_query(q), {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [_SCHEMA_QUALIFIED.sub(r"\1", s) for s in sql]


@pytest.mark.parametrize(
    "tail",
    [
        "WITH n WHERE n.k = 5 CREATE (n)-[:R]->(:M)",
        "WITH n WHERE n.k = 5 MERGE (n)-[:R]->(:M {id: 'm'})",
        "WITH n WHERE n.k = 5 MERGE (:M {id: 'm'})",
        "WITH n WHERE n.k = 5 CREATE (:M)",
        "WITH n, n.k AS k CREATE (:M {v: k})",
    ],
)
def test_the_stage_reads_the_captured_rows_after_the_set(tail):
    stmts = _stmts(f"MATCH (n:X {{k: 1}}) SET n.k = 5 {tail}")
    caps = [s.split("\n", 1)[0].split()[1] for s in stmts if s.startswith("__capture_ids__ ")]
    assert caps, stmts
    first_set = next(i for i, s in enumerate(stmts) if s.startswith("UPDATE rdf_props"))
    writes = [s for s in stmts[first_set + 2 :] if "Stage1 AS (" in s and not s.startswith("__")]
    assert writes, stmts
    for s in writes:
        stage = s.split("Stage1 AS (", 1)[1].split("\n)", 1)[0]
        assert f"n0.node_id IN (__IDSALL_{caps[0]}__)" in stage, stage
        # the MATCH's test on the old k is gone
        assert "IN ('1', '1.0')" not in stage, stage
        assert "__PINNED_" not in s, s


def test_a_with_that_reads_no_written_key_is_pinned_as_before():
    stmts = _stmts("MATCH (n:X {k: 1}) SET n.k = 5 WITH n CREATE (:M)")
    assert any(s.startswith("__pin_stage__ Stage1") for s in stmts), stmts
    assert not any("__IDSALL_" in s for s in stmts), stmts


def test_a_capture_through_the_stage_reads_the_captured_ids():
    from iris_vector_graph.stores.iris_sql_store import _expand_all_ids

    stmts = _stmts("MATCH (n:X {k: 1}) SET n.k = 5 WITH n WHERE n.k = 5 SET n.q = 3")
    later = [s for s in stmts if s.startswith("__capture_ids__ ") and "Stage1 AS (" in s]
    assert later and "__IDSALL_w0__" in later[0], stmts
    assert "IN ('a', 'b')" in _expand_all_ids(later[0], {"w0": ["a", "b"]})
