"""4.6.0 R308, offline: a pattern comprehension reads its WHERE, its property maps and a variable-length hop.

On the release head (7b79df7d) `_expr_pattern_comprehension` translated a relationship's types and direction and the
far node's labels only: `[(a)-[:R*1..2]->(b) WHERE b.name = 'eve' | b.name]` from cat answered ["dan"] (hand answer
["eve"]), `[(a)-[:R {w: 1}]->(b) | b.name]` listed every R neighbour. A comprehension with any of those is now the
pattern's own MATCH translation in a correlated subquery. The live answers, worked by hand, are the hand cases pc_* of
tests/e2e/test_460_r308_cypher_differential_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

M = "MATCH (a:L {name: 'cat'}) RETURN "


def _sql(q):
    s = translate_to_sql(parse_query(q), {})
    assert not s.var_length_paths, s.sql
    return s.sql if isinstance(s.sql, str) else "\n".join(s.sql)


@pytest.mark.parametrize("comp, needles", [
    ("[(a)-[:R]->(b) WHERE b.kind = 'x' | b.name]", ["'x'"]),
    ("[(a)-[:R {w: 4}]->(b) | b.name]", ["$.w"]),
    ("[(a)-[:R]->(b {kind: 'x'}) | b.name]", ["'kind'", "'x'"]),
    ("[(a)-[:R*1..2]->(b) | b.name]", ["CY_VLP_PATHS("]),
    ("[(a)-[:R*1..2]->(b) WHERE b.name = 'eve' | b.name]", ["CY_VLP_PATHS(", "'eve'"]),
])
def test_the_comprehension_reads_what_its_pattern_says(comp, needles):
    sql = _sql(M + comp + " AS bs")
    select = sql.split("\nFROM Graph_KG.nodes n0", 1)[0]
    for needle in needles:
        assert needle in select, (needle, sql)
    assert "?" not in select, sql  # the subquery binds nothing: it can sit in any segment


def test_a_plain_comprehension_keeps_its_shape():
    sql = _sql(M + "[(a)-[:R]->(b) | b.name] AS bs")
    assert "epc" in sql and "CY_VLP_PATHS" not in sql, sql
