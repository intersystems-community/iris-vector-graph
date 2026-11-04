"""4.6.0 R218: a node property reads back with the type it is stored with, on the native and SQL paths alike and
from ``get_node`` / ``get_property_values``, under intersystems-irispython 5.4.0 and 5.4.1.

Run it under both drivers (the repo's .venv has 5.4.0; a fresh install gets 5.4.1). Before R218, 5.4.1 read a
Cypher-written number on the SQL path as IRIS text: ``'.94999999999999995559'`` for 0.95, ``'.5'``, ``'29'``, and
``'5'`` for ``2.5 * 2``, while the native path read 0.95 (walkthrough 2 W6, W7).

The graph, by hand (label ``T218``, ids unique to the run):
- ``c``, written by Cypher: ``CREATE {f: 0.95, h: 0.5, i: 29, s: 2.5}``, then ``SET g = 0.95, k = 2.5 * 2``;
- ``a``, written by ``create_node``: ``{f: 0.95, h: 0.5, i: 29}``, which the API stores as the text '0.95', '0.5', '29';
- ``z1`` and ``z2``, written by Cypher: ``f`` 10.5 and 9.5.

So:
- ``c``'s ``f, g, h, i, s, k`` read 0.95, 0.95, 0.5, 29, 2.5, 5 (floats, the integer 29, and 5: Cypher stores
  ``2.5 * 2`` as an IRIS decimal, which reads as the integer the native path has always answered);
- ``a``'s read the strings '0.95', '0.5', '29' on every path (the API's text, as in 4.5.x);
- ``c.s * 2`` is 5 on both paths;
- ``ORDER BY n.f`` over z1, z2 is 9.5 then 10.5 (a text order would put 10.5 first), through a WITH alias too;
- ``max(n.f)`` over z1, z2 is 10.5;
- ``WHERE n.f = 0.95`` over c, a, z1, z2 matches c and a (R168: every form of the number), ``<> 0.95`` z1 and z2;
- ``get_node`` reads c as 0.95, 29, 5 and a as '0.95', '29'; ``get_property_values`` of a key only c and a hold
  answers 0.95 and '0.95'.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r218_{run}_{k}" for k in ("c", "a", "z1", "z2")}
    key = f"pv{run}"
    for q, params in (
        ("CREATE (:T218 {id: $id, f: 0.95, h: 0.5, i: 29, s: 2.5})", {"id": ids["c"]}),
        ("MATCH (n:T218 {id: $id}) SET n.g = 0.95, n.k = 2.5 * 2", {"id": ids["c"]}),
        (f"MATCH (n:T218 {{id: $id}}) SET n.{key} = 0.95", {"id": ids["c"]}),
        ("CREATE (:T218 {id: $id, f: 10.5})", {"id": ids["z1"]}),
        ("CREATE (:T218 {id: $id, f: 9.5})", {"id": ids["z2"]}),
    ):
        r = e.execute_cypher(q, params)
        assert r.error is None, (q, r.error)
    assert e.create_node(ids["a"], labels=["T218"], properties={"f": 0.95, "h": 0.5, "i": 29, key: 0.95})
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids, key
    for nid in ids.values():
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


def _both(e, query, params):
    """The native executor's rows and the SQL path's; each test asserts both."""
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


def _typed(rows):
    return [[(v, type(v).__name__) for v in row] for row in rows]


@pytest.mark.parametrize(
    "shape",
    ["MATCH (n:T218 {id: $id})", "MATCH (n {node_id: $id})"],
)
def test_cypher_written_numbers_read_as_numbers(g, shape):
    e, ids, _ = g
    for path, rows in _both(e, f"{shape} RETURN n.f, n.g, n.h, n.i, n.s, n.k", {"id": ids["c"]}).items():
        assert _typed(rows) == [
            [(0.95, "float"), (0.95, "float"), (0.5, "float"), (29, "int"), (2.5, "float"), (5, "int")]
        ], path


@pytest.mark.parametrize("shape", ["MATCH (n:T218 {id: $id})", "MATCH (n {node_id: $id})"])
def test_api_written_numbers_stay_text(g, shape):
    e, ids, _ = g
    for path, rows in _both(e, f"{shape} RETURN n.f, n.h, n.i", {"id": ids["a"]}).items():
        assert _typed(rows) == [[("0.95", "str"), ("0.5", "str"), ("29", "str")]], path


def test_arithmetic_over_a_stored_number(g):
    e, ids, _ = g
    for path, rows in _both(e, "MATCH (n:T218 {id: $id}) RETURN n.s * 2", {"id": ids["c"]}).items():
        assert rows == [[5]], path


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:T218) WHERE n.id IN $zs RETURN n.f ORDER BY n.f",
        "MATCH (n:T218) WHERE n.id IN $zs WITH n.f AS value RETURN value ORDER BY value",
        "MATCH (n:T218) WHERE n.id IN $zs WITH n.f AS idx RETURN idx ORDER BY idx",
    ],
)
def test_order_by_is_numeric(g, query):
    e, ids, _ = g
    for path, rows in _both(e, query, {"zs": [ids["z1"], ids["z2"]]}).items():
        assert _typed(rows) == [[(9.5, "float")], [(10.5, "float")]], path


def test_max_of_stored_floats(g):
    e, ids, _ = g
    for path, rows in _both(
        e, "MATCH (n:T218) WHERE n.id IN $zs RETURN max(n.f) AS m", {"zs": [ids["z1"], ids["z2"]]}
    ).items():
        assert _typed(rows) == [[(10.5, "float")]], path


@pytest.mark.parametrize("cond, ends", [("n.f = 0.95", ["a", "c"]), ("n.f <> 0.95", ["z1", "z2"])])
def test_filters_match_what_r168_matches(g, cond, ends):
    e, ids, _ = g
    q = f"MATCH (n:T218) WHERE n.id IN $ids AND {cond} RETURN n.id ORDER BY n.id"
    back = {v: k for k, v in ids.items()}
    for path, rows in _both(e, q, {"ids": list(ids.values())}).items():
        assert sorted(back[r[0]] for r in rows) == ends, path


def test_get_node_reads_the_stored_types(g):
    e, ids, _ = g
    c = e.get_node(ids["c"])
    a = e.get_node(ids["a"])
    assert [(c[k], type(c[k]).__name__) for k in ("f", "i", "k")] == [(0.95, "float"), (29, "int"), (5, "int")]
    assert [(a[k], type(a[k]).__name__) for k in ("f", "i")] == [("0.95", "str"), ("29", "str")]


def test_get_property_values_reads_the_stored_types(g):
    e, _, key = g
    got = e.get_property_values(key)
    assert sorted((repr(v) for v in got)) == sorted([repr(0.95), repr("0.95")])
