"""4.6.0 R212: MERGE ON MATCH after a MATCH writes the node the MERGE created only when a later row matches it.

openCypher runs MERGE once per incoming row, and each run sees what the earlier rows wrote. So the node a MERGE
creates is matched by every later row that reaches the same pattern: with two rows `MATCH (p:Person) MERGE
(c:City) ON MATCH SET c.name = p.bornIn` creates the City on the first row and sets its name on the second (TCK Merge3
[4]; Merge4 [1] adds `ON MATCH SET a:M1` to the node created by the first of two rows). With one row there is no
later row: ON MATCH writes only a node that existed before the MERGE (Merge7 [1]/[2], `MATCH (a:A), (b:B) MERGE
(a)-[:KNOWS]->(b) ON MATCH SET ...` on one row, writes nothing). R186 kept the capture after the inserts for every MERGE
after a MATCH, so one row wrote the created node too.

The rule, per merged node: a node that existed before the MERGE gets ON MATCH; the node it created gets ON MATCH when
more than one incoming row reaches it. The ON MATCH capture after the inserts keeps the created node only when the
MATCH has more than one row. And with no incoming row MERGE does not run: `MATCH (p:P) MERGE (c:C)` with no P
created a C.

These pin the statements; tests/e2e/test_460_r212_merge_on_match_rows_e2e.py checks the stored graph.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")


def _stmts(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _created_id(stmts):
    return next(v for s, p in stmts if "INSERT INTO nodes" in s for v in p if isinstance(v, str) and _UUID.fullmatch(v))


def _captures(stmts):
    return [(i, s, p) for i, (s, p) in enumerate(stmts) if s.startswith("__capture_ids__ ")]


@pytest.mark.parametrize(
    "action",
    ["ON MATCH SET c.hit = 1", "ON MATCH SET c:Hit", "ON CREATE SET c.made = 1 ON MATCH SET c.hit = 1"],
)
def test_after_a_match_the_created_node_is_written_only_with_more_than_one_row(action):
    stmts = _stmts(f"MATCH (p:P) MERGE (c:C) {action}")
    created = _created_id(stmts)
    caps = _captures(stmts)
    assert len(caps) == 1, stmts
    i, sql, params = caps[0]
    inserts = [j for j, (s, _) in enumerate(stmts) if "INSERT INTO nodes" in s]
    assert i > max(inserts), "read after the inserts: a later row matches the node the first created"
    # the created node unless the MATCH has more than one row
    assert "_om.node_id <> ?" in sql and "(SELECT COUNT(*) FROM nodes n0" in sql and ") > 1" in sql, sql
    assert created in params, (created, params)
    assert params.count("P") == 1, params  # the MATCH's label, once, in the row count


def test_the_row_count_reads_a_stage_through_its_cte():
    stmts = _stmts("MATCH (p:P) WITH p MERGE (c:C) ON MATCH SET c.hit = 1")
    _, sql, params = _captures(stmts)[0]
    assert sql.split("\n", 1)[1].startswith("WITH Stage1 AS ("), sql
    assert "SELECT COUNT(*) FROM Stage1" in sql, sql
    assert params[0] == "P", params  # the stage's markers come first


def test_with_no_incoming_rows_the_capture_stays_ahead_of_the_inserts():
    """`MERGE (c:C) ON MATCH ...` alone runs once: ON MATCH writes what existed before it (R186)."""
    stmts = _stmts("MERGE (c:C {k: 1}) ON MATCH SET c.hit = 1")
    caps = _captures(stmts)
    inserts = [j for j, (s, _) in enumerate(stmts) if "INSERT INTO nodes" in s]
    assert len(caps) == 1 and caps[0][0] < min(inserts), stmts
    assert "COUNT(*)" not in caps[0][1], caps


def test_a_bound_node_is_not_counted():
    """`MATCH (a:A) MERGE (a) ON MATCH SET ...`: the MERGE creates nothing, ON MATCH writes the bound nodes."""
    stmts = _stmts("MATCH (a:A) MERGE (a)-[:T]->(b:B) ON MATCH SET a.hit = 1")
    assert not any("_om.node_id <> ?" in s for s, _ in stmts), stmts


def test_after_a_match_the_merge_creates_only_when_there_is_a_row():
    """No incoming row, no MERGE run: `MATCH (p:P) MERGE (c:C)` on a graph without P created a C."""
    stmts = _stmts("MATCH (p:P) MERGE (c:C)")
    node = next((s, p) for s, p in stmts if "INSERT INTO nodes" in s)
    assert "AND EXISTS (SELECT 1 FROM nodes n0" in node[0], node
    assert node[0].count("?") == len(node[1]) and node[1][-1] == "P", node
    # the label goes only on a node that was inserted
    lbl = next((s, p) for s, p in stmts if s.startswith("INSERT INTO rdf_labels"))
    assert lbl[0].endswith("AND EXISTS (SELECT 1 FROM nodes WHERE node_id = ?)"), lbl
    assert lbl[1][0] == lbl[1][-1] == _created_id(stmts), lbl


def test_an_optional_match_keeps_its_null_row():
    """`OPTIONAL MATCH (p:P) MERGE (c:C)` runs once on the null row: no gate on the rows."""
    stmts = _stmts("OPTIONAL MATCH (p:P) MERGE (c:C)")
    node = next(s for s, _ in stmts if "INSERT INTO nodes" in s)
    assert "AND EXISTS (SELECT 1 FROM" not in node, node


@pytest.mark.parametrize("q, params", [("MERGE (n:OM {id: 'om'})", {}), ("MERGE (n:OM {id: $id})", {"id": "om"})])
def test_a_node_merged_by_its_id_writes_on_create_and_on_match_to_their_own_node(q, params):
    """R214: no generated id tells the node made from the one matched. ON CREATE wrote every node that lacked the key
    (`FROM nodes n0`, the whole table), ON MATCH wrote the node whose id is the alias `n0` (none). Both now write the
    node through a capture read before the inserts: created when no node has the id, matched when one has."""
    stmts = _stmts(q + " ON CREATE SET n.c = 2 ON MATCH SET n.m = 5, n:Hit", params)
    caps = _captures(stmts)
    node_insert = next(i for i, (s, _) in enumerate(stmts) if s.startswith("INSERT INTO nodes"))
    assert len(caps) == 2 and all(i < node_insert for i, _, _ in caps), stmts
    created, matched = (s.split("\n", 1)[0].split()[1] for _, s, _ in caps)
    assert all(p[0] == "om" for _, _, p in caps), caps
    writes = [(s, p) for s, p in stmts[node_insert:] if f"__IDS_{created}__" in s or f"__IDS_{matched}__" in s]
    assert [p for s, p in writes if f"__IDS_{created}__" in s and s.startswith("UPDATE")] == [[2, "c"]], writes
    assert [p for s, p in writes if f"__IDS_{matched}__" in s and s.startswith("UPDATE")] == [[5, "m"]], writes
    assert any(f"__IDS_{matched}__" in s and "rdf_labels" in s for s, _ in writes), writes
    assert not any("FROM nodes n0 WHERE NOT EXISTS" in s for s, _ in stmts), stmts
    assert not any("n0" in p for _, p in stmts), stmts
