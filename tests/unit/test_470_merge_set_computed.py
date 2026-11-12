"""4.7.0 newcomer walkthrough: a MERGE's ON CREATE / ON MATCH SET of a computed value stores it with its type
(live: tests/e2e/test_470_merge_set_computed_e2e.py).

Measured on 4.7.0 (b9d779bc, IVG470MRG): `MERGE (n:T {id: 'c'}) ON CREATE SET n.count = 1 + 1` stored the text '2'
(read back '2', and `n.count > 1` answered null); `1.5 + 1` stored '2.5', `1 < 2` stored '1', `ON MATCH SET n.v = 2 * 3`
stored '6'. The value was rendered as SQL and CAST to VARCHAR. On a relationship (`MERGE (a)-[r:R]->(b) ON CREATE SET
r.w = 1 + 1`) the AST's repr was stored. `ON MATCH SET n.count = n.count + 1` failed SQLCODE -23 (`Label 'N0' is not
listed among the applicable tables`): the value read the node through the RETURN's alias n0, which the UPDATE of
rdf_props does not have.

Each expected value is worked by hand, as openCypher computes it: 1 + 1 is the integer 2, 1.5 + 1 the float 2.5,
'a' + 'b' the string 'ab', 1 < 2 true (stored as the text 'true', as `SET n.b = true` stores it), 2 * 3 the integer 6.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_UPDATE_BOUND = "UPDATE Graph_KG.rdf_props SET val = Graph_KG.Prop_Set(%ID, val, ?)"
_INSERT_BOUND = "INSERT INTO Graph_KG.rdf_props (graph_id, s, \"key\", val) SELECT '', node_id, ?, ? "


def _statements(q, params=None):
    t = translate_to_sql(parse_query(q), params or {})
    sql = t.sql if isinstance(t.sql, list) else [t.sql]
    return list(zip(sql, t.parameters))


def _typed(v):
    return (v, type(v).__name__)


CONSTANTS = [
    ("1 + 1", (2, "int")),
    ("1.5 + 1", (2.5, "float")),
    ("2 * 3", (6, "int")),
    ("'a' + 'b'", ("ab", "str")),
    ("1 < 2", ("true", "str")),
    ("1 > 2", ("false", "str")),
]


@pytest.mark.parametrize("action", ["ON CREATE", "ON MATCH"])
@pytest.mark.parametrize("expr, expected", CONSTANTS)
def test_keyed_merge_binds_the_computed_value_with_its_type(action, expr, expected):
    """`MERGE (n {id: ...})`: the UPDATE and the INSERT of the property bind the value worked out, not SQL text."""
    stmts = _statements(f"MERGE (n:T {{id: 'c'}}) {action} SET n.v = {expr}")
    updates = [p for s, p in stmts if s.startswith(_UPDATE_BOUND)]
    inserts = [p for s, p in stmts if s.startswith(_INSERT_BOUND)]
    assert [_typed(p[0]) for p in updates] == [expected]
    assert [_typed(p[1]) for p in inserts] == [expected]
    assert not any("VARCHAR(4000)" in s for s, _ in stmts if "rdf_props" in s and "__capture_ids__" not in s)


@pytest.mark.parametrize("expr, expected", CONSTANTS)
def test_merge_without_an_id_key_binds_the_computed_value_on_create(expr, expected):
    """`MERGE (n:T {name: 'q'})`: the node's id is generated; its ON CREATE INSERT binds the value."""
    stmts = _statements(f"MERGE (n:T {{name: 'q'}}) ON CREATE SET n.v = {expr}")
    values = [p[1] for s, p in stmts if s.startswith(_INSERT_BOUND)
              and p[0] == "v"]
    assert [_typed(v) for v in values] == [expected]


@pytest.mark.parametrize("expr, json_val, json_type", [
    ("1 + 1", "2", "number"), ("1.5 + 1", "2.5", "number"), ("'a' + 'b'", "ab", "string"), ("1 < 2", "true", ""),
])
def test_relationship_merge_binds_the_computed_value(expr, json_val, json_type):
    """A relationship's property: IVGJSONSET is handed the value's text and JSON type; it was handed the AST's repr."""
    stmts = _statements(
        f"MATCH (a:T {{id: 'd'}}), (b:T {{id: 'e'}}) MERGE (a)-[r:R]->(b) ON CREATE SET r.w = {expr}"
    )
    sets = [p for s, p in stmts if "CypherFn_IVGJSONSET" in s]
    assert [p[:3] for p in sets] == [["w", json_val, json_type]]


@pytest.mark.parametrize("q", [
    "MERGE (n:T {id: 'mt1'}) ON MATCH SET n.count = n.count + 1",
    "MERGE (n:T {id: 'mt1'}) ON MATCH SET n.z = n.count * 10",
    "MERGE (n:T {id: 'mt1'}) ON CREATE SET n.z = n.count * 10",
    "MERGE (n:T {name: 'q'}) ON MATCH SET n.count = n.count + 1",
    "MERGE (n:T {name: 'q'}) ON CREATE SET n.z = n.count * 10",
])
def test_a_value_reading_the_merged_node_names_no_return_alias(q):
    """The UPDATE / INSERT of rdf_props has no alias n0: a value naming it fails to prepare (SQLCODE -23). Nor is the
    value CAST to VARCHAR, which stores a number as its text."""
    writes = [s for s, _ in _statements(q) if s.startswith(("UPDATE Graph_KG.rdf_props", "INSERT INTO Graph_KG.rdf_props"))]
    assert any("count" in s for s in writes), writes
    for s in writes:
        assert "n0." not in s, s
        assert "AS VARCHAR(4000)" not in s, s

