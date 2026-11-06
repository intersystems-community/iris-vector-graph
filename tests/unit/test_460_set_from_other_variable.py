"""4.6.0 R187: a SET value that reads a property reads the variable it names.

`MATCH (n:X {id:'a'}), (m:X {id:'b'}) SET n.p = m.x` raised SQLCODE -23. Every property reference in a SET value read
the written node's own row (`_upd.s = rdf_props.s`), whatever its variable, and the INSERT for a node without `p`
selects FROM nodes, where `rdf_props.s` is not a table it lists. Another variable's property is now read through a
`__capture_pairs__` capture of (written node, other node) per row, taken ahead of the writes; the statements write the
pairs' nodes and read the other node as `__PAIR_<key>__[<row>]`, which the executor turns into a CASE over the ids.

tests/e2e/test_460_set_from_other_variable_e2e.py checks the stored values.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.unit._one_graph import ONE_GRAPH
from iris_vector_graph.stores.iris_sql_store import _capture_pairs, _expand_captured_ids

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _stmts(q, params=None):
    # one graph: the shapes pinned here are a one-graph namespace's (a namespace of several: tests/e2e/
    # test_460_all_graphs_writes_e2e.py)
    r = translate_to_sql(parse_query(q), params or {}, engine=ONE_GRAPH)
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _writes(stmts):
    return [(s, p) for s, p in stmts if re.match(r"\s*(UPDATE|INSERT|DELETE)\b", s)]


Q = "MATCH (n:X {id:'a'}), (m:X {id:'b'}) SET n.p = m.x"


def test_the_pairs_are_captured_ahead_of_the_writes():
    stmts = _stmts(Q)
    assert stmts[0][0].startswith("__capture_pairs__ v0\nSELECT n0.node_id, n2.node_id\n"), stmts[0]
    for s, _ in _writes(stmts):
        assert "IN (__IDS_v0__)" in s, s


def test_each_statement_reads_the_other_node_of_its_row():
    up, ins, dele = _writes(_stmts(Q))
    assert "_upd.s = __PAIR_v0__[rdf_props.s] AND _upd.\"key\" = 'x'" in up[0], up
    assert "_upd.s = __PAIR_v0__[nodes.node_id] AND _upd.\"key\" = 'x'" in ins[0], ins
    assert dele == ('DELETE FROM rdf_props WHERE s IN (__IDS_v0__) AND "key" = ? AND val IS NULL', ["p"])
    # the copy is the stored value as it is, not CAST to a number
    assert "NUMERIC" not in up[0], up


@pytest.mark.parametrize("q", ["MATCH (n:X {id:'a'}) SET n.c = n.x", "MATCH (n:X {id:'a'}) SET n.c = n.x + 1"])
def test_the_own_node_s_property_is_read_from_the_written_row(q):
    up, ins = _writes(_stmts(q))
    assert "_upd.s = rdf_props.s" in up[0], up
    assert "_upd.s = nodes.node_id" in ins[0] and "rdf_props.s" not in ins[0], ins


def test_a_map_value_reads_the_other_node():
    stmts = _stmts("MATCH (n:X {id:'a'}), (m:X {id:'b'}) SET n += {q: m.s}")
    assert any(s.startswith("__capture_pairs__ ") for s, _ in stmts), stmts
    assert any("__PAIR_" in s and "'s'" in s for s, _ in _writes(stmts)), stmts


@pytest.mark.parametrize(
    "q",
    [
        # R210 writes `SET n.p = r.w`, `SET r.p = r.q` and `SET r.p = n.x` (tests/unit/test_460_r210_set_reads_relationship.py)
        "MATCH (n:X)-[r:T]->() SET n.p = r.w + 1",
        "MATCH (n:X), (m:X), (o:X) SET n.p = m.x + o.x",
    ],
)
def test_what_it_cannot_write_is_refused(q):
    with pytest.raises(SyntaxError):
        _stmts(q)


def test_the_executor_writes_each_id_its_pair():
    captured: dict = {}
    _capture_pairs("v0", [("a1", "b1"), ("a2", "b2"), ("a1", "b9"), (None, "x")], captured)
    assert captured["v0"] == ["a1", "a2"]
    stmt = "UPDATE t SET val = (SELECT 1 WHERE s = __PAIR_v0__[t.s]) WHERE s IN (__IDS_v0__)"
    assert _expand_captured_ids(stmt, captured) == [
        "UPDATE t SET val = (SELECT 1 WHERE s = (CASE t.s WHEN 'a1' THEN 'b1' WHEN 'a2' THEN 'b2' END)) "
        "WHERE s IN ('a1', 'a2')"
    ]
    _capture_pairs("v1", [], captured)
    assert _expand_captured_ids(stmt.replace("v0", "v1"), captured) == [
        "UPDATE t SET val = (SELECT 1 WHERE s = NULL) WHERE s IN (NULL)"
    ]
