"""Spec 230 US2 (T040, FR-008) — `USE GRAPH` does not scope a CTE.

`USE GRAPH` puts the statement's graph on every graph-owned table a read touches.
A procedure's result stage is not one: `ivg.retrieve` fuses its two arms into a
`Retrieve` CTE that projects a node ID and a score, and the arms inside it already
carry their own graph predicate. Naming that CTE in the outer `WHERE` made IRIS
refuse the whole statement at Prepare:

    SQLCODE -29 Field 'RETRIEVE.GRAPH_ID' not found in the applicable tables

and the driver's error path logs it and answers zero rows, so the only visible
symptom of a scoped retrieval is an empty result — indistinguishable from a graph
with nothing in it.

Asserted on the generated SQL because that is where the defect is; the statement
executing on the server is `tests/e2e/test_230_retrieval_scope.py`.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

GRAPH = "ivg230:usegraph"

RETRIEVE = (
    f"USE GRAPH '{GRAPH}' "
    "CALL ivg.retrieve('zebra', 5, 'idx', '*', 60) "
    "YIELD node, rrf_score RETURN node, rrf_score"
)


def _sql(cypher: str) -> str:
    return translate_to_sql(parse_query(cypher), {}).sql


def test_the_fusion_stage_is_not_given_a_graph_predicate():
    sql = _sql(RETRIEVE)

    assert "Retrieve.graph_id" not in sql, sql
    assert "RETRIEVE.GRAPH_ID" not in sql.upper(), sql


def test_the_arms_inside_the_fusion_keep_theirs():
    """The scope has to survive the fix: `USE GRAPH` still reaches the vector arm's
    own table, which is where it belonged all along."""
    sql = _sql(RETRIEVE)

    assert GRAPH in sql, sql
    assert "e.graph_id" in sql, sql


def test_a_label_filtered_fusion_scopes_the_label_join_too():
    sql = _sql(
        f"USE GRAPH '{GRAPH}' "
        "CALL ivg.retrieve('zebra', 5, 'idx', 'Thing', 60) "
        "YIELD node, rrf_score RETURN node, rrf_score"
    )

    assert "Retrieve.graph_id" not in sql, sql
    assert "n.graph_id" in sql, sql


def test_an_ordinary_match_still_gets_its_predicate():
    """The fix narrows one alias, not the pass: a plain label-filtered read is the
    case `USE GRAPH` exists for."""
    sql = _sql(f"USE GRAPH '{GRAPH}' MATCH (n:Thing) RETURN n.id AS id")

    assert GRAPH in sql, sql
    assert "graph_id" in sql, sql
