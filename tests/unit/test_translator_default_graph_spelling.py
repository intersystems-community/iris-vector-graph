"""A default-graph CREATE reads both spellings of "no graph" (ADR-0003).

`ForName()` spells the default graph as the SQL value `''`, and that is what
every writer stores. But `rdf_edges.graph_id` is still nullable
(`_engine/schema.py:383`), and rows predating the spec-214 backfill hold NULL.
So a read predicate written as `graph_id = ''` alone is false for exactly the
rows an upgrade leaves behind: in SQL, `NULL = ''` is unknown, not true.

The translator's two default-graph node inserts guard themselves with
`WHERE NOT EXISTS (... AND graph_id = '')`. Against a NULL row the guard finds
nothing, so the insert fires and a second row for the same `node_id` is
attempted — today that hits `nodes`, which is `NOT NULL DEFAULT ''` on every
code path, so it is latent rather than live. It is still the wrong predicate,
and it is the shape that already bit the read side elsewhere.

The reference idiom is `_engine/snapshot.py:267`: COALESCE the *guard*, leave the
inserted payload as the literal `''`. Named-graph branches need no COALESCE — a
named graph is never NULL.

Companion: tests/unit/test_graph_id_tightening.py scans for the same defect
across the ObjectScript readers.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _dml(cypher: str) -> list[str]:
    """Every DML statement the translator emits, as SQL text."""
    result = translate_to_sql(parse_query(cypher), {})
    sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
    return [s for s in sql.split("\n") if s.strip()]


def _node_inserts(statements: list[str]) -> list[str]:
    return [s for s in statements if "INSERT INTO" in s and "nodes" in s]


# A literal node id, and a node id that comes from an earlier stage: the two
# default-graph insert paths, `_create_node_literal` and its projected sibling.
LITERAL = "CREATE (n {node_id: 'a'})"
PROJECTED = "UNWIND [1] AS x CREATE (n {node_id: 'a'})"


@pytest.mark.parametrize("cypher", [LITERAL, PROJECTED], ids=["literal", "projected"])
def test_the_default_graph_guard_reads_both_spellings(cypher):
    inserts = _node_inserts(_dml(cypher))

    assert inserts, "no node insert was emitted, so this test proves nothing"
    for sql in inserts:
        assert "COALESCE(graph_id, '') = ''" in sql, (
            "the NOT EXISTS guard matches only the '' spelling, so a NULL "
            f"graph_id row is invisible to it: {sql}"
        )


@pytest.mark.parametrize("cypher", [LITERAL, PROJECTED], ids=["literal", "projected"])
def test_the_inserted_value_is_still_the_canonical_spelling(cypher):
    """COALESCE belongs in the guard. The row written must still be `''`."""
    for sql in _node_inserts(_dml(cypher)):
        head = sql.split("WHERE NOT EXISTS")[0]
        assert "COALESCE" not in head, f"COALESCE leaked into the payload: {sql}"
        assert "''" in head


def test_a_named_graph_still_binds_its_name():
    """The named-graph branch is untouched: a named graph is never NULL."""
    inserts = _node_inserts(_dml("USE GRAPH 'acme' CREATE (n {node_id: 'a'})"))

    assert inserts
    for sql in inserts:
        assert "graph_id = ?" in sql, sql
        assert "COALESCE" not in sql, (
            "a named graph cannot be NULL, so COALESCE here only costs an index: "
            f"{sql}"
        )
