"""4.6.0 R310: a statement over captured ids stays under the size IRIS prepares (fixes64).

`MATCH (n:L) DETACH DELETE n` deletes the relationships touching its nodes with `s IN (<ids>) OR o_id IN (<ids>)`: the
captured ids appear twice. Ids were inlined in chunks of 500 per key, so each such statement held 1000 literals; with
UUID node ids that is ~40,000 characters, which IRIS refuses at Prepare (SQLCODE -202 or -1; measured on 2026.3:
400 + 400 UUIDs, 32,085 characters, prepares; 500 + 500 does not). Over 500 nodes the DETACH DELETE failed, and so
did the TCK's teardown of Return6 [4] (7251 nodes) and Create4 [2], which the harness used to drop. Chunks are now cut
so that the ids, as often as the statement names them, stay within a character budget.
"""

from __future__ import annotations

import uuid

from iris_vector_graph.stores.iris_sql_store import (
    _CAPTURED_LITERAL_BUDGET,
    _CAPTURED_SQL_BUDGET,
    _expand_captured_ids,
)

EDGES = "DELETE FROM Graph_KG.rdf_edges WHERE (s IN (__IDS_d0__) OR o_id IN (__IDS_d0__)) AND graph_id = NULL"


def _ids(n, width=36):
    return [str(uuid.uuid4())[:width].ljust(width, "x") for _ in range(n)]


def test_each_statement_stays_under_the_budget_and_every_id_is_deleted_once():
    ids = _ids(7251)
    out = _expand_captured_ids(EDGES, {"d0": ids})
    assert all(len(s) <= _CAPTURED_SQL_BUDGET + len(EDGES) for s in out), max(map(len, out))
    seen = [i for s in out for i in ids if f"s IN ('{i}'" in s or f", '{i}'" in s.split(" OR ")[0]]
    assert sorted(seen) == sorted(ids)


def test_the_budget_holds_for_long_ids():
    ids = _ids(300, width=200)
    out = _expand_captured_ids(EDGES, {"d0": ids})
    assert len(out) > 1
    assert all(len(s) <= _CAPTURED_SQL_BUDGET + len(EDGES) for s in out)


def test_the_budget_is_one_iris_prepares():
    # 400 + 400 UUIDs (32,085 characters) prepared on 2026.3, 500 + 500 did not
    assert _CAPTURED_SQL_BUDGET <= 32_000


def test_few_ids_are_one_statement():
    assert _expand_captured_ids(EDGES, {"d0": ["a", "b"]}) == [
        "DELETE FROM Graph_KG.rdf_edges WHERE (s IN ('a', 'b') OR o_id IN ('a', 'b')) AND graph_id = NULL"
    ]


def test_short_ids_stay_under_the_literal_budget():
    """IRIS refuses 1000 literals in a statement whatever their length: 500 short ids named twice failed (-202)."""
    ids = [f"r310b:{i}" for i in range(1200)]
    out = _expand_captured_ids(EDGES, {"d0": ids})
    assert all(s.count("'") // 2 <= _CAPTURED_LITERAL_BUDGET for s in out), max(s.count("'") // 2 for s in out)
    assert _CAPTURED_LITERAL_BUDGET < 1000


def test_the_literal_budget_holds_for_the_one_statement_path():
    ids = [f"r{i}" for i in range(499)]
    out = _expand_captured_ids(EDGES, {"d0": ids})
    assert len(out) > 1 and all(s.count("'") // 2 <= _CAPTURED_LITERAL_BUDGET for s in out)
