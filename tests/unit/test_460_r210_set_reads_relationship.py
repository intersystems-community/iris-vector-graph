"""4.6.0 R210: a SET that reads a relationship's property, onto the same relationship or across to a node.

Since R187 these were refused with SyntaxError (before it they failed with SQLCODE -23): `SET r.p = r.q` (the UPDATE of
rdf_edges had no rdf_props row to read), `SET r.p = n.x` and `SET n.p = r.w`. Now:

- `SET r.p = r.q` copies the JSON leaf in the same UPDATE, by its JSON type (CY_JSON_TYPE): a number stays a number,
  a string a string ('007' is not 7); a missing `q` removes `p`.
- `SET r.p = n.x` reads, per edge, the row's other node through a pair capture (`__capture_pairs__`, edge id and node
  id, as R187 pairs two nodes), and stores the node's value with the type it has in the row (CY_VALUE_TYPE).
- `SET n.p = r.w` reads, per node, its row's edge the same way, and stores the leaf as a Cypher SET stores the
  value: one INSERT per JSON type (CY_JSON_TYPE: an integer as an integer, a float as a double, anything else as its
  text); a missing `w` removes `p`.

These pin the statements; tests/e2e/test_460_r210_set_reads_relationship_e2e.py checks the stored graph.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")
_FN = re.compile(r"\bGraph_KG\.")


def _stmts(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_FN.sub("", _SCHEMA_QUALIFIED.sub(r"\1", s)), p) for s, p in zip(sql, r.parameters)]


def _pairs(stmts):
    return [(s, p) for s, p in stmts if s.startswith("__capture_pairs__ ")]


@pytest.mark.parametrize("q", ["MATCH ()-[r:T]->() SET r.p = r.q", "MATCH ()-[r:T]->() SET r += {p: r.q}"])
def test_a_relationship_copies_its_own_property_by_its_json_type(q):
    stmts = _stmts(q)
    upd = [s for s, _ in stmts if s.startswith("UPDATE rdf_edges SET qualifiers = ")]
    assert len(upd) == 1, stmts
    assert "CY_JSON_TYPE(rdf_edges.qualifiers, '$.q') IN ('integer', 'float')" in upd[0], upd
    assert "CY_REL_PROP(rdf_edges.qualifiers, '$.q')" in upd[0], upd
    assert "CypherFn_IVGJSONREMOVE(" in upd[0], upd  # a missing q removes p
    assert not _pairs(stmts), stmts


def test_a_relationship_reads_the_rows_node():
    stmts = _stmts("MATCH (n:L)-[r:T]->() SET r.p = n.x")
    pairs = _pairs(stmts)
    assert len(pairs) == 1 and re.search(r"\nSELECT e\d+\.edge_id, n0\.node_id\b", pairs[0][0]), pairs
    key = pairs[0][0].split("\n", 1)[0].split()[1]
    upd = next(s for s, _ in stmts if s.startswith("UPDATE rdf_edges"))
    assert f"edge_id IN (__IDS_{key}__)" in upd, upd
    assert f"_upd.s = __PAIR_{key}__[rdf_edges.edge_id]" in upd and "CY_VALUE_TYPE(" in upd, upd


@pytest.mark.parametrize("q", ["MATCH (n:L)-[r:T]->() SET n.p = r.w", "MATCH (n:L)-[r:T]->() SET n += {p: r.w}"])
def test_a_node_reads_the_rows_relationship(q):
    stmts = _stmts(q)
    pairs = _pairs(stmts)
    assert len(pairs) == 1 and re.search(r"\nSELECT n0\.node_id, e\d+\.edge_id\b", pairs[0][0]), pairs
    key = pairs[0][0].split("\n", 1)[0].split()[1]
    writes = [s for s, _ in stmts if f"__IDS_{key}__" in s]
    assert writes[0] == f'DELETE FROM rdf_props WHERE s IN (__IDS_{key}__) AND "key" = ?', writes
    leaf = f"(SELECT _e.qualifiers FROM rdf_edges _e WHERE _e.edge_id = __PAIR_{key}__[nodes.node_id])"
    inserts = [s for s in writes if s.startswith("INSERT INTO rdf_props")]
    assert len(inserts) == 3, writes
    assert f"CAST(CY_REL_PROP({leaf}, '$.w') AS BIGINT)" in inserts[0] and "= 'integer'" in inserts[0], inserts
    assert f"CAST(CY_REL_PROP({leaf}, '$.w') AS DOUBLE)" in inserts[1] and "= 'float'" in inserts[1], inserts
    assert "NOT IN ('integer', 'float', 'unassigned', 'null')" in inserts[2], inserts


@pytest.mark.parametrize(
    "q",
    [
        "MATCH ()-[r:T]->() SET r.p = r.q + 1",
        "MATCH (n)-[r:T]->() SET r.p = n.x + 1",
        "MATCH (n)-[r:T]->() SET n.p = r.w + 1",
        "MATCH (n)-[r:T]->(m), ()-[s:T]->() SET r.p = s.q",
    ],
)
def test_an_expression_over_a_relationship_is_still_refused(q):
    with pytest.raises(SyntaxError):
        _stmts(q)
