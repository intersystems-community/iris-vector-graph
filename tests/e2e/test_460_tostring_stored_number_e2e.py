"""4.6.0 R182: `toString()` of a stored number answers openCypher's text; a stored string comes back as it is.

Before: `.95` for a relationship's JSON number, `.94999999999999995559` for a fraction a Cypher CREATE wrote on a node,
`1` for the float 1.0 a Cypher CREATE wrote on a node. Nothing stored changes; only the read.

The graph, worked by hand (label L182, type T182, ids unique to the run). openCypher spells a float as Java's
`Double.toString` does: `0.95`, `1.0`, `1.23456789E7` (exponent above 6), `1.0E-4` (below -3), `0.001`.

Nodes, `toString` of `s`, `f`, `i`:
- n_api   `create_node(properties={s: 0.95, f: 1.0, i: 7})` (from 4.6.4 as Cypher stores them; the text '0.95',
          '1.0', '7' before): '0.95', '1.0', '7'
- n_cy    `CREATE (:L182 {s: 0.95, f: 1.0, i: 7})` (a $DOUBLE, a $DOUBLE, an integer): '0.95', '1.0', '7'
- n_set   `SET n.s = 0.95, n.f = 1.0, n.i = 7`: '0.95', '1.0', '7'
- n_param `SET n.s = $s, n.f = $f, n.i = $i` (0.95, 1.0, 7): '0.95', '1.0', '7'
- n_str   `create_node(properties={s: '.95', f: '1.0', i: '007'})` (strings): '.95', '1.0', '007'
- n_cystr `CREATE (:L182 {s: '.95', f: '1.0', i: '007'})` (strings): '.95', '1.0', '007'
- n_wide  `CREATE (:L182 {a: 12345678.9, b: 0.0001, c: 0.001, d: -0.5, e: 1.0E20, g: -7})`:
          '1.23456789E7', '1.0E-4', '0.001', '-0.5', '1.0E20', '-7'

Relationships a -[:T182]-> b<k>, `toString` of `s`, `f`, `i`:
- b1 `create_edge(qualifiers={s: 0.95, f: 1.0, i: 7})` (JSON numbers 0.95, 1.0, 7): '0.95', '1.0', '7'
- b2 `create_edge(qualifiers={s: '.95', f: '1.0', i: '007'})` (JSON strings): '.95', '1.0', '007'
- b3 `CREATE (a)-[:T182 {s: 0.95, f: 1.0, i: 7}]->(b)` (stored as the JSON strings "0.95", "1.0", "7"): '0.95', '1.0', '7'
- b4 `SET r.s = 0.95, r.f = 1.0, r.i = 7` (stored as the JSON numbers 0.95, 1, 7: the SET writes 1.0 as 1, so its
  float-ness is gone; docs/KNOWN_ISSUES.md): '0.95', '1', '7'
- b5 `SET r.s = '.95', r.f = '1.0', r.i = '007'` (JSON strings): '.95', '1.0', '007'
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NODES = ("n_api", "n_cy", "n_set", "n_param", "n_str", "n_cystr", "n_wide")
TARGETS = ("b1", "b2", "b3", "b4", "b5")


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r182_{run}_{k}" for k in NODES + TARGETS + ("a",)}

    def cy(q, **params):
        r = e.execute_cypher(q, params)
        assert r.error is None, (q, r.error)

    assert e.create_node(ids["n_api"], labels=["L182"], properties={"s": 0.95, "f": 1.0, "i": 7})
    assert e.create_node(ids["n_str"], labels=["L182"], properties={"s": ".95", "f": "1.0", "i": "007"})
    cy("CREATE (:L182 {id: $id, s: 0.95, f: 1.0, i: 7})", id=ids["n_cy"])
    cy("CREATE (:L182 {id: $id, s: '.95', f: '1.0', i: '007'})", id=ids["n_cystr"])
    cy("CREATE (:L182 {id: $id})", id=ids["n_set"])
    cy("MATCH (n:L182 {id: $id}) SET n.s = 0.95, n.f = 1.0, n.i = 7", id=ids["n_set"])
    cy("CREATE (:L182 {id: $id})", id=ids["n_param"])
    cy("MATCH (n:L182 {id: $id}) SET n.s = $s, n.f = $f, n.i = $i", id=ids["n_param"], s=0.95, f=1.0, i=7)
    cy(
        "CREATE (:L182 {id: $id, a: 12345678.9, b: 0.0001, c: 0.001, d: -0.5, e: 1.0E20, g: -7})",
        id=ids["n_wide"],
    )
    for k in ("a",) + TARGETS:
        assert e.create_node(ids[k], labels=["L182E"])
    assert e.create_edge(ids["a"], "T182", ids["b1"], qualifiers={"s": 0.95, "f": 1.0, "i": 7})
    assert e.create_edge(ids["a"], "T182", ids["b2"], qualifiers={"s": ".95", "f": "1.0", "i": "007"})
    cy(
        "MATCH (a:L182E {id: $a}), (b:L182E {id: $b}) CREATE (a)-[:T182 {s: 0.95, f: 1.0, i: 7}]->(b)",
        a=ids["a"], b=ids["b3"],
    )
    for b in ("b4", "b5"):
        assert e.create_edge(ids["a"], "T182", ids[b])
    cy("MATCH (:L182E {id: $a})-[r:T182]->(:L182E {id: $b}) SET r.s = 0.95, r.f = 1.0, r.i = 7", a=ids["a"], b=ids["b4"])
    cy(
        "MATCH (:L182E {id: $a})-[r:T182]->(:L182E {id: $b}) SET r.s = '.95', r.f = '1.0', r.i = '007'",
        a=ids["a"], b=ids["b5"],
    )
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
        e.delete_node(nid)


def _by_id(g, q, params=None):
    e, ids = g
    r = e.execute_cypher(q, dict(params or {}))
    assert r.error is None, (q, r.error)
    names = {v: k for k, v in ids.items()}
    return {names[row[0]]: list(row[1:]) for row in r.rows if row[0] in names}


NODE_WANT = {
    "n_api": ["0.95", "1.0", "7"],
    "n_cy": ["0.95", "1.0", "7"],
    "n_set": ["0.95", "1.0", "7"],
    "n_param": ["0.95", "1.0", "7"],
    "n_str": [".95", "1.0", "007"],
    "n_cystr": [".95", "1.0", "007"],
}


def test_node_property(g):
    got = _by_id(g, "MATCH (n:L182) WHERE n.s IS NOT NULL RETURN n.id, toString(n.s), toString(n.f), toString(n.i)")
    assert got == NODE_WANT


def test_node_property_through_a_with_column(g):
    got = _by_id(
        g,
        "MATCH (n:L182) WHERE n.s IS NOT NULL WITH n.id AS nid, n.s AS x, n.f AS y, n.i AS z "
        "RETURN nid, toString(x), toString(y), toString(z)",
    )
    assert got == NODE_WANT


def test_node_floats_wide_and_negative(g):
    got = _by_id(
        g,
        "MATCH (n:L182 {id: $id}) RETURN n.id, toString(n.a), toString(n.b), toString(n.c), toString(n.d), "
        "toString(n.e), toString(n.g)",
        {"id": g[1]["n_wide"]},
    )
    assert got == {"n_wide": ["1.23456789E7", "1.0E-4", "0.001", "-0.5", "1.0E20", "-7"]}


def test_node_filter_on_the_text(g):
    got = _by_id(g, "MATCH (n:L182) WHERE toString(n.s) = '0.95' RETURN n.id")
    assert sorted(got) == ["n_api", "n_cy", "n_param", "n_set"]


def test_relationship_property(g):
    e, ids = g
    got = _by_id(
        g,
        "MATCH (:L182E {id: $a})-[r:T182]->(b) WHERE r.s IS NOT NULL "
        "RETURN b.id, toString(r.s), toString(r.f), toString(r.i)",
        {"a": ids["a"]},
    )
    assert got == {
        "b1": ["0.95", "1.0", "7"],
        "b2": [".95", "1.0", "007"],
        "b3": ["0.95", "1.0", "7"],
        "b4": ["0.95", "1", "7"],
        "b5": [".95", "1.0", "007"],
    }


def test_relationship_filter_on_the_text(g):
    e, ids = g
    got = _by_id(g, "MATCH (:L182E {id: $a})-[r:T182]->(b) WHERE toString(r.s) = '0.95' RETURN b.id", {"a": ids["a"]})
    assert sorted(got) == ["b1", "b3", "b4"]


def test_the_stored_values_read_as_before(g):
    """Read-side only: RETURN of the property itself answers what 4.6.0's R168/R169 entry says it does."""
    got = _by_id(g, "MATCH (n:L182) WHERE n.s IS NOT NULL RETURN n.id, n.s")
    assert got == {
        "n_api": [0.95], "n_cy": [0.95], "n_set": [0.95], "n_param": [0.95], "n_str": [".95"], "n_cystr": [".95"],
    }
