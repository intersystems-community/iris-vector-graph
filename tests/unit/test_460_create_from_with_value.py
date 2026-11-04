"""4.6.0 R189: `WITH $v AS v CREATE (n:X {id: v, name: 'nm'})` binds each statement's parameters in text order and
writes the properties.

The node's id comes from the WITH stage, whose own `?` is the first marker of every statement. The label insert bound
`[label] + <stage params>`, so the label went where the stage's value goes and the id where the label goes: the row
named node 'X' and failed fk_labels_node (SQLCODE -121). The properties (`id`, `name`) had no statement at all.

tests/e2e/test_460_create_from_with_value_e2e.py checks the stored node.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _stmts(q, params):
    r = translate_to_sql(parse_query(q), params)
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _bound(sql, params):
    """(marker position, value) pairs: what each `?` of `sql` receives."""
    assert sql.count("?") == len(params), (sql, params)
    return params


Q = "WITH $v AS v CREATE (n:X {id: v, name: 'nm'})"


def test_every_statement_binds_the_stage_value_first():
    for s, p in _stmts(Q, {"v": "x1"}):
        if s.startswith("WITH Stage1 AS (\nSELECT ? AS v\n)"):
            assert p and p[0] == "x1", (s, p)


def test_the_label_insert_binds_the_label_after_the_stage():
    (s, p), = [(s, p) for s, p in _stmts(Q, {"v": "x1"}) if "INSERT INTO rdf_labels" in s]
    assert _bound(s, p) == ["x1", "X", "X"], (s, p)


def test_the_properties_are_written():
    props = [(s, p) for s, p in _stmts(Q, {"v": "x1"}) if "INSERT INTO rdf_props" in s]
    assert len(props) == 2, props
    keys = {p[1] for s, p in props}
    assert keys == {"id", "name"}, props
    for s, p in props:
        assert p[0] == "x1" and s.count("?") == len(p), (s, p)
        assert "FROM (SELECT Stage1.v AS node_id" in s, s
    (name_s, name_p), = [(s, p) for s, p in props if p[1] == "name"]
    assert name_p == ["x1", "name", "nm", "name"], (name_s, name_p)


def test_a_value_from_the_stage_is_read_from_its_column():
    stmts = _stmts("WITH $v AS v, $w AS w CREATE (n:X {id: v, name: w})", {"v": "x1", "w": "nm"})
    (s, p), = [(s, p) for s, p in stmts if "INSERT INTO rdf_props" in s and "name" in p]
    assert "Stage1.w" in s and s.count("?") == len(p), (s, p)
    assert p == ["x1", "nm", "name", "name"], (s, p)
