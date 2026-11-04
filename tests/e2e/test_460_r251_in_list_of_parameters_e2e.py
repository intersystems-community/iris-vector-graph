"""4.6.0 R251: ``WHERE n.id IN [$a, $b]`` answers on the native executor and the SQL path, under 5.4.0 and 5.4.1.

A null element (``IN [$a, $none]``, as ``IN [1, null]``) is not covered: a WHERE over a list with a null fails to
prepare (SQLCODE -14) with a literal null too, before and after R251.

The graph, by hand (label ``T251``, ids unique to the run): ``a``, ``b``, ``c`` written by Cypher with ``k`` 1, 2, 3, and
an edge ``a -[:R251]-> c``. So:
- ``n.id IN [$a, $b]`` with a and b's ids is a, b (it failed with ``<ARGUMENT ERROR>``), as ``IN $list`` is;
- ``n.id IN [$a, $c]`` is a, c;
- ``n.k IN [$x, $y]`` with 2 and 3 is b, c;
- ``(n)-[:R251]->(m) WHERE n.id IN [$a, $b]`` is c.
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
    ids = {k: f"r251_{run}_{k}" for k in ("a", "b", "c")}
    for k, n in (("a", 1), ("b", 2), ("c", 3)):
        r = e.execute_cypher("CREATE (:T251 {id: $id, k: $k})", {"id": ids[k], "k": n})
        assert r.error is None, r.error
    r = e.execute_cypher("MATCH (a:T251 {id: $a}), (c:T251 {id: $c}) CREATE (a)-[:R251]->(c)", {"a": ids["a"], "c": ids["c"]})
    assert r.error is None, r.error
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
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


@pytest.mark.parametrize(
    "where, params, want",
    [
        ("n.id IN [$a, $b]", lambda i: {"a": i["a"], "b": i["b"]}, ["a", "b"]),
        ("n.id IN $l", lambda i: {"l": [i["a"], i["b"]]}, ["a", "b"]),
        ("n.id IN [$a, $c]", lambda i: {"a": i["a"], "c": i["c"]}, ["a", "c"]),
        ("n.k IN [$x, $y]", lambda i: {"x": 2, "y": 3}, ["b", "c"]),
    ],
)
def test_in_a_list_of_parameters(g, where, params, want):
    e, ids = g
    q = f"MATCH (n:T251) WHERE {where} RETURN n.id AS id ORDER BY id"
    for path, rows in _both(e, q, params(ids)).items():
        assert rows == [[ids[k]] for k in want], path


def test_in_a_list_of_parameters_on_a_hop(g):
    e, ids = g
    q = "MATCH (n:T251)-[:R251]->(m) WHERE n.id IN [$a, $b] RETURN m.id"
    for path, rows in _both(e, q, {"a": ids["a"], "b": ids["b"]}).items():
        assert rows == [[ids["c"]]], path
