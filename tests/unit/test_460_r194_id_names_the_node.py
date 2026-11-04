"""4.6.0 R194: `{id: 'x'}` in a pattern names the node x on every route, never an `id` property check as well.

The single-node pattern read it as the node (`_is_node_id_key`); a hop's inline-property pass (`_trp_apply_inline_props`
and the anonymous-source join) joined an `id` property row equal to x besides, so on nodes bulk_create_edges made
(no `id` property) `count(h)` was 1 and every hop from h answered nothing. A non-string `{id: 1}` is a property, as
CREATE stores it. Live: tests/e2e/test_460_r194_id_pattern_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(query):
    r = translate_to_sql(parse_query(query), {})
    return r.sql, [p for ps in r.parameters for p in ps] if r.parameters else []


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (h {id: 'h'}) RETURN count(h)",
        "MATCH (h {id: 'h'})-[:R]->(x) RETURN count(x)",
        "MATCH ({id: 'h'})-[:R]->(x) RETURN count(x)",
        "MATCH (x)<-[:R]-(h {id: 'h'}) RETURN count(x)",
        "MATCH (h {id: 'h'})-[:R]->(x {id: 't'}) RETURN count(*)",
    ],
)
def test_a_string_id_is_the_node_and_no_property_row(query):
    sql, params = _sql(query)
    assert "node_id = CAST('h' AS VARCHAR(256))" in sql or "= CAST('h' AS VARCHAR(256))" in sql, sql
    assert "id" not in params, (sql, params)


def test_a_number_id_is_a_property():
    sql, params = _sql("MATCH (h {id: 1})-[:R]->(x) RETURN count(x)")
    assert "id" in params, (sql, params)
