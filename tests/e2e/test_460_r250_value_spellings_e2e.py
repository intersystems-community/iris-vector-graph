"""4.6.0 R250: a sum of floats is a float, and ``properties(r)`` spells a relationship's values as openCypher does,
under intersystems-irispython 5.4.0 and 5.4.1.

The graph, by hand (label ``T250``, ids unique to the run), every edge ``h -[:W250]-> t_k``:
- ``f1``, ``f2``: Cypher ``{w: 2.0}`` and ``{w: 3.0}`` (Cypher stores each as the JSON string ``"2.0"``);
- ``i1``, ``i2``: Cypher ``{w: 2}`` and ``{w: 3}``;
- ``m1``, ``m2``: Cypher ``{w: 2}`` and ``{w: 3.0}``;
- ``h2``, ``h3``: ``create_edge`` with ``{"w": 2.5}`` and ``{"w": 2.5}`` (JSON numbers);
- ``e``: Cypher, no properties;
- nodes ``n1``, ``n2`` (label ``T250N``): Cypher ``{f: 2.0}`` and ``{f: 3.0}``; ``n3``, ``n4`` (``T250I``): ``{f: 2}``, ``{f: 3}``.

So:
- ``sum(r.w)`` over f1, f2 is 5.0 (it answered 5); over i1, i2 5; over m1, m2 5.0 (one float makes a float); over
  h2, h3 5.0;
- ``sum(n.f)`` over T250N is 5.0, over T250I 5;
- ``properties(r)`` of f1 is ``{w: 2.0}`` (it answered ``{w: 2}``), of i1 ``{w: 2}``, of h2 ``{w: 2.5}``, of e ``{}``
  (it answered None), and through ``WITH r`` the same;
- ``properties(r)`` of a relationship an OPTIONAL MATCH did not find is null (Graph9 [3]).
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    h = f"r250_{run}_h"
    t = {k: f"r250_{run}_{k}" for k in ("f1", "f2", "i1", "i2", "m1", "m2", "h2", "h3", "e")}
    n = {k: f"r250_{run}_{k}" for k in ("n1", "n2", "n3", "n4")}
    r = e.execute_cypher("CREATE (:T250 {id: $id})", {"id": h})
    assert r.error is None, r.error
    for k, tid in t.items():
        r = e.execute_cypher("CREATE (:T250 {id: $id})", {"id": tid})
        assert r.error is None, r.error
    for k, w in (("f1", "2.0"), ("f2", "3.0"), ("i1", "2"), ("i2", "3"), ("m1", "2"), ("m2", "3.0")):
        q = "MATCH (a:T250 {id: $h}), (b:T250 {id: $t}) CREATE (a)-[:W250 {w: %s, g: $g}]->(b)" % w
        r = e.execute_cypher(q, {"h": h, "t": t[k], "g": k[0]})
        assert r.error is None, (q, r.error)
    for k in ("h2", "h3"):
        assert e.create_edge(h, "W250", t[k], qualifiers={"w": 2.5, "g": "h"})
    r = e.execute_cypher("MATCH (a:T250 {id: $h}), (b:T250 {id: $t}) CREATE (a)-[:W250]->(b)", {"h": h, "t": t["e"]})
    assert r.error is None, r.error
    for k, lbl, f in (("n1", "T250N", "2.0"), ("n2", "T250N", "3.0"), ("n3", "T250I", "2"), ("n4", "T250I", "3")):
        r = e.execute_cypher("CREATE (:%s {id: $id, f: %s})" % (lbl, f), {"id": n[k]})
        assert r.error is None, r.error
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, h, t
    for nid in [h, *t.values(), *n.values()]:
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


def _both(e, query, params):
    out = {}
    for native in (True, False):
        old = e.native_cypher
        e.native_cypher = native
        try:
            r = e.execute_cypher(query, params)
        finally:
            e.native_cypher = old
        assert r.error is None, (native, r.error)
        out["native" if native else "sql"] = r.rows
    return out


@pytest.mark.parametrize("group, want", [("f", 5.0), ("i", 5), ("m", 5.0), ("h", 5.0)])
def test_a_sum_of_relationship_values_has_their_type(g, group, want):
    e, h, _ = g
    q = "MATCH (:T250 {id: $h})-[r:W250]->() WHERE r.g = $g RETURN sum(r.w) AS s"
    for path, rows in _both(e, q, {"h": h, "g": group}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [(want, type(want).__name__)], path


def test_a_grouped_sum_has_each_groups_type(g):
    e, h, _ = g
    q = "MATCH (:T250 {id: $h})-[r:W250]->() WHERE r.g IS NOT NULL RETURN r.g AS g, sum(r.w) AS s ORDER BY g"
    for path, rows in _both(e, q, {"h": h}).items():
        assert [(k, v, type(v).__name__) for k, v in rows] == [
            ("f", 5.0, "float"),
            ("h", 5.0, "float"),
            ("i", 5, "int"),
            ("m", 5.0, "float"),
        ], path


@pytest.mark.parametrize("label, want", [("T250N", 5.0), ("T250I", 5)])
def test_a_sum_of_node_values_has_their_type(g, label, want):
    e, _, _ = g
    for path, rows in _both(e, f"MATCH (n:{label}) RETURN sum(n.f) AS s", {}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [(want, type(want).__name__)], path


@pytest.mark.parametrize(
    "k, want",
    [("f1", {"w": 2.0, "g": "f"}), ("i1", {"w": 2, "g": "i"}), ("h2", {"w": 2.5, "g": "h"}), ("e", {})],
)
@pytest.mark.parametrize("via", ["RETURN", "WITH r RETURN"])
def test_properties_of_a_relationship(g, k, want, via):
    e, h, t = g
    q = f"MATCH (:T250 {{id: $h}})-[r:W250]->(b:T250 {{id: $t}}) {via} properties(r) AS p"
    for path, rows in _both(e, q, {"h": h, "t": t[k]}).items():
        assert len(rows) == 1 and rows[0][0] is not None, (path, rows)
        got = json.loads(rows[0][0])
        assert {x: (v, type(v).__name__) for x, v in got.items()} == {
            x: (v, type(v).__name__) for x, v in want.items()
        }, path


def test_properties_of_a_missing_relationship_is_null(g):
    e, h, _ = g
    q = "MATCH (a:T250 {id: $h}) OPTIONAL MATCH (a)-[r:NOT_THERE_250]->() RETURN properties(r) AS p"
    for path, rows in _both(e, q, {"h": h}).items():
        assert rows == [[None]], path
