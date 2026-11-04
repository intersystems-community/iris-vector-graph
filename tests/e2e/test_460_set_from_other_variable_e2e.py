"""4.6.0 R187: `SET n.p = m.x` writes the property of the row's other node.

`MATCH (n:X {id:'a'}), (m:X {id:'b'}) SET n.p = m.x` raised SQLCODE -23: a property reference in a SET value was read
as the target node's own property (`_upd.s = rdf_props.s`), whatever its variable, and the INSERT for a node without
`p` selected from `nodes`, where `rdf_props.s` is no column. So `SET n.p = m.x` read n's own `x` where n had `p`, and
failed where it had none; `SET n.p = n.x` failed the same way, and cast a string to a number (`'hello'` became 0).

The graph, by hand (ids unique to the run): `a {x: 'hello', z: 1}`, `b {x: 42, s: 'str'}`, a pair `a1 -[:T]-> b1 {x:
1}`, `a2 -[:T]-> b2 {x: 2}`, all created by Cypher, so the numbers are numbers.

- `MATCH (n {id: a}), (m {id: b}) SET n.p = m.x`: a.p = 42
- `... SET n += {q: m.s}`: a.q = 'str'
- `... SET n.r = m.x + 1`: a.r = 43
- `... SET n.z = m.missing`: z removed from a
- `MATCH (n {id: a}) SET n.c = n.x`: a.c = 'hello'
- `MATCH (n:P)-[:T]->(m) SET n.p = m.x`: a1.p = 1, a2.p = 2 (each n reads its own row's m)
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    label, pair = f"LS{run}", f"LP{run}"
    ids = {k: f"r187_{run}_{k}" for k in ("a", "b", "a1", "b1", "a2", "b2")}
    r = e.execute_cypher(
        f"CREATE (:{label} {{id: $a, x: 'hello', z: 1}}), (:{label} {{id: $b, x: 42, s: 'str'}}), "
        f"(:{pair} {{id: $a1}})-[:T187]->(:{label} {{id: $b1, x: 1}}), "
        f"(:{pair} {{id: $a2}})-[:T187]->(:{label} {{id: $b2, x: 2}})",
        ids,
    )
    assert r.error is None, r.error
    yield e, label, pair, ids
    e.execute_cypher(f"MATCH (n) WHERE n:{label} OR n:{pair} DETACH DELETE n")


def _run(e, q, params=None):
    r = e.execute_cypher(q, dict(params or {}))
    assert r.error is None, (q, r.error)
    return r.rows


def _props(e, nid):
    rows = _run(e, "MATCH (n) WHERE n.id = $id RETURN properties(n)", {"id": nid})
    assert len(rows) == 1, rows
    p = json.loads(rows[0][0])
    p.pop("id")
    return p


PAIR = "MATCH (n {id: $a}), (m {id: $b}) "


@pytest.mark.parametrize(
    "tail, want",
    [
        ("SET n.p = m.x", {"x": "hello", "z": 1, "p": 42}),
        ("SET n += {q: m.s}", {"x": "hello", "z": 1, "q": "str"}),
        ("SET n.r = m.x + 1", {"x": "hello", "z": 1, "r": 43}),
        ("SET n.z = m.missing", {"x": "hello"}),
        ("SET n.z = m.x", {"x": "hello", "z": 42}),
        ("SET n.p = m.x, n.q = m.s", {"x": "hello", "z": 1, "p": 42, "q": "str"}),
    ],
    ids=["copy", "map", "expression", "null_removes", "overwrite", "two_items"],
)
def test_from_the_other_node(g, tail, want):
    e, _, _, ids = g
    _run(e, PAIR + tail, {"a": ids["a"], "b": ids["b"]})
    assert _props(e, ids["a"]) == want
    assert _props(e, ids["b"]) == {"x": 42, "s": "str"}


def test_the_value_reads_back_with_its_type(g):
    e, _, _, ids = g
    _run(e, PAIR + "SET n.p = m.x, n.q = m.s", {"a": ids["a"], "b": ids["b"]})
    assert _run(e, "MATCH (n {id: $a}) RETURN n.p, n.q", {"a": ids["a"]}) == [[42, "str"]]


def test_from_its_own_property(g):
    e, _, _, ids = g
    _run(e, "MATCH (n {id: $a}) SET n.c = n.x", {"a": ids["a"]})
    assert _props(e, ids["a"]) == {"x": "hello", "z": 1, "c": "hello"}


def test_each_row_reads_its_own_other_node(g):
    e, _, pair, ids = g
    _run(e, f"MATCH (n:{pair})-[:T187]->(m) SET n.p = m.x")
    assert _props(e, ids["a1"]) == {"p": 1}
    assert _props(e, ids["a2"]) == {"p": 2}
