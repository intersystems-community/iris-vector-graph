"""4.6.0 R180: `SET x += {map}` and `SET x = {map}` on a relationship or a node.

`MATCH ()-[r:T]->() SET r += {p: 2}` wrote the map into `rdf_props` through `SELECT e3.node_id` (a column
`rdf_edges` does not have: SQLCODE -29), so the relationship never changed. A relationship's properties are the
`qualifiers` JSON, which `SET r.p = 2` writes through `CypherFn_IVGJSONSET`; a map writes every key in one UPDATE of
that column, so a MATCH filtered on a key the map changes still finds the edge for the next key. On nodes, a map
value that is an expression (`{p: m.x}`) was bound as the AST object, a null (`{p: null}` or a `$map` holding None)
was written as a NULL row instead of removing the property, a list in a `$map` was bound as a Python list, and
`SET n = $map` added a label named after the parameter.

These pin the statements the translator emits; tests/e2e/test_460_set_map_props_e2e.py checks the stored graph.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _stmts(q, params=None):
    """(sql, params) per statement, table names without the IVG Schema (another test may have set a prefix)."""
    r = translate_to_sql(parse_query(q), params or {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _writes(stmts):
    return [(s, p) for s, p in stmts if re.match(r"\s*(WITH .*?\)\s*)?(UPDATE|INSERT|DELETE)", s, re.S)]


def _no_ast(stmts):
    for s, p in stmts:
        for v in p:
            assert "Variable(" not in str(v) and "PropertyReference(" not in str(v), (s, p)


def _edge_update(stmts):
    ups = [(s, p) for s, p in stmts if "UPDATE rdf_edges SET qualifiers" in s]
    assert len(ups) == 1, stmts
    assert not any("rdf_props" in s for s, _ in _writes(stmts)), stmts
    s, p = ups[0]
    assert re.search(r"WHERE edge_id IN \(SELECT e\d+\.edge_id\b", s), s
    return s, p


def test_rel_merge_one_key_writes_qualifiers():
    s, p = _edge_update(_stmts("MATCH ()-[r:T]->() SET r += {p: 2}"))
    assert re.search(r"CypherFn_IVGJSONSET\(COALESCE\(qualifiers, CAST\('\{\}'\s+AS VARCHAR\(256\)\)\), \?, \?, \?\)", s), s
    # the key, the value as text and its JSON type, as SET r.p = 2 binds them, then the MATCH's type
    assert p == ["p", "2", "number", "T"], p


def test_rel_merge_several_keys_one_update():
    s, p = _edge_update(_stmts("MATCH ()-[r:T]->() SET r += {p: 2, q: 'x', z: null, w: 0.5}"))
    assert s.count("CypherFn_IVGJSONSET(") == 3 and s.count("CypherFn_IVGJSONREMOVE(") == 1, s
    # nested in map order: the innermost call is the first key
    assert p == ["p", "2", "number", "q", "x", "string", "z", "w", "0.5", "number", "T"], p


def test_rel_merge_map_parameter():
    s, p = _edge_update(
        _stmts("MATCH ()-[r:T]->() SET r += $m", {"m": {"p": 2, "code": "007", "gone": None}})
    )
    assert p == ["p", "2", "number", "code", "007", "string", "gone", "T"], p


def test_rel_merge_is_what_set_property_writes():
    """Each key is bound as `SET r.k = v` binds it."""
    for v, bound in (("2", ["2", "number"]), ("'007'", ["007", "string"]), ("0.95", ["0.95", "number"])):
        _, single = _edge_update(_stmts(f"MATCH ()-[r:T]->() SET r.k = {v}"))
        _, merged = _edge_update(_stmts(f"MATCH ()-[r:T]->() SET r += {{k: {v}}}"))
        assert single[1:3] == bound and merged[1:3] == bound, (v, single, merged)


def test_rel_replace_starts_from_empty_map():
    s, p = _edge_update(_stmts("MATCH ()-[r:T]->() SET r = {a: 1, b: null}"))
    assert "COALESCE(qualifiers" not in s and "(qualifiers," not in s, s
    assert re.search(r"CypherFn_IVGJSONSET\(CAST\('\{\}' AS VARCHAR\(\d+\)\), \?, \?, \?\)", s), s
    # a null in a replacing map is not set: the key is simply absent
    assert "IVGJSONREMOVE" not in s and p == ["a", "1", "number", "T"], p


def test_rel_replace_with_empty_map_clears():
    s, p = _edge_update(_stmts("MATCH ()-[r:T]->() SET r = {}"))
    assert re.search(r"SET qualifiers = CAST\('\{\}' AS VARCHAR\(\d+\)\) WHERE", s), s
    assert p == ["T"], p


def test_rel_replace_map_parameter():
    s, p = _edge_update(_stmts("MATCH ()-[r:T]->() SET r = $m", {"m": {"a": "x"}}))
    assert "(qualifiers," not in s and p == ["a", "x", "string", "T"], p


def test_rel_merge_after_with_stage():
    stmts = _stmts("MATCH ()-[r:T]->() WITH r SET r += {p: 2}")
    assert not any("rdf_props" in s for s, _ in _writes(stmts)), stmts
    assert any("UPDATE rdf_edges SET qualifiers" in s for s, _ in stmts), stmts


def test_node_merge_null_removes_property():
    stmts = _stmts("MATCH (n:P) SET n += {p: null, q: 1}")
    w = _writes(stmts)
    dels = [(s, p) for s, p in w if s.lstrip().startswith("DELETE FROM rdf_props") and p[-1] == "p"]
    assert len(dels) == 1, w
    # no statement writes a NULL value for p
    for s, p in w:
        if s.lstrip().startswith(("UPDATE", "INSERT")):
            assert None not in p, (s, p)


def test_node_merge_pins_its_nodes_before_the_first_key():
    """`MATCH (n {k: 1}) SET n += {k: 2, z: 9}`: z's statements must not select through `k = 1` again."""
    stmts = _stmts("MATCH (n:P {k: 1}) SET n += {k: 2, z: 9}")
    w = _writes(stmts)
    assert "\x01ivg_set_replace" in w[0][1] and "SELECT n0.node_id" in w[0][0], w[0]
    for s, p in w[1:]:
        assert "p2.val" not in s and "SELECT _rm.s FROM rdf_props _rm" in s or s.startswith("DELETE FROM rdf_props WHERE"), s
    assert w[-1] == ('DELETE FROM rdf_props WHERE "key" = ?', ["\x01ivg_set_replace"]), w[-1]


def test_node_merge_map_parameter_null_and_list():
    stmts = _stmts("MATCH (n:P) SET n += $m", {"m": {"a": 1, "b": None, "c": [1, 2]}})
    w = _writes(stmts)
    assert any(s.lstrip().startswith("DELETE") and p[-1] == "b" for s, p in w), w
    for s, p in w:
        if s.lstrip().startswith(("UPDATE", "INSERT")):
            assert None not in p and [1, 2] not in p, (s, p)
    assert any("[1, 2]" in p for s, p in w), w


def test_node_merge_expression_value_is_sql_not_an_ast_object():
    stmts = _stmts("MATCH (n:P), (m:Q) SET n += {p: m.x}")
    _no_ast(stmts)


def test_node_replace_map_parameter_is_not_a_label():
    stmts = _stmts("MATCH (n:P {id: 'x'}) SET n = $m", {"m": {"a": 1}})
    _no_ast(stmts)
    assert not any("INSERT INTO rdf_labels" in s for s, _ in stmts), stmts
    assert any(s.lstrip().startswith("DELETE FROM rdf_props") for s, _ in stmts), stmts
    assert any("a" in p and 1 in p for s, p in stmts if s.lstrip().startswith("INSERT INTO rdf_props")), stmts


def test_rel_property_after_with_stage_writes_the_edge():
    stmts = _stmts("MATCH ()-[r:T]->() WITH r SET r.p = 2")
    assert not any("rdf_props" in s for s, _ in _writes(stmts)), stmts
    ups = [s for s, _ in stmts if "UPDATE rdf_edges SET qualifiers" in s]
    assert len(ups) == 1 and "SELECT Stage1.__edge_r_id" in ups[0], stmts


def test_set_variable_to_a_non_map_raises_instead_of_adding_a_label():
    for q in ("MATCH (n:P), (m:Q) SET n = m", "MATCH (n:P) SET n += $m"):
        with pytest.raises(SyntaxError, match="map literal or a map parameter"):
            _stmts(q, {"m": 3})


@pytest.mark.parametrize(
    "q",
    [
        "MATCH ()-[r:T]->() SET r.f = true",
        "MATCH ()-[r:T]->() SET r += {f: true}",
        "MATCH (a:A {id: 'a'}), (b:B {id: 'b'}) MERGE (a)-[r:T]->(b) ON MATCH SET r.f = true",
    ],
    ids=["set_property", "merge_map", "merge_on_match"],
)
def test_rel_boolean_is_stored_as_true_text(q):
    """`SET r.f = true` stored the JSON string "True", which `RETURN r.f` answered as the string 'True'."""
    ups = [(s, p) for s, p in _stmts(q) if "CypherFn_IVGJSONSET" in s]
    assert ups and all("True" not in p for _, p in ups), ups
    assert any(p[:2] == ["f", "true"] for _, p in ups), ups
