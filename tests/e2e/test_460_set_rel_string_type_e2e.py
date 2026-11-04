"""4.6.0: `SET r.code = '007'` reads back as the string '007' through `RETURN r.code` and `properties(r)`.

IVGJSONSET stored any `$IsValidNum` text a SET gave it as a JSON number, so `SET r.code = '007'` stored 7 and both
readers answered 7. The translator now names the value's type.

The graph, worked by hand: a -R007-> b and a -R007-> c (label L007, ids unique to the run), created with `{k: 1}`.
- on a->b: `SET r.code = '007'` and `SET r.n = 7`; on a->c: `SET r.code = $v` with v = '007' and `SET r.n = $w`, w = 7.
So `RETURN r.code` is the string '007' for both and `r.n` the int 7; `properties(r)` holds code '007' and n 7.
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
    ids = {k: f"r007_{run}_{k}" for k in "abc"}
    for nid in ids.values():
        assert e.create_node(nid, labels=["L007"])
    for q, params in (
        ("MATCH (a:L007 {id: $a}), (b:L007 {id: $b}) CREATE (a)-[:R007 {k: 1}]->(b)", {"b": ids["b"]}),
        ("MATCH (a:L007 {id: $a}), (b:L007 {id: $b}) CREATE (a)-[:R007 {k: 1}]->(b)", {"b": ids["c"]}),
        ("MATCH (:L007 {id: $a})-[r:R007]->(:L007 {id: $b}) SET r.code = '007', r.n = 7", {"b": ids["b"]}),
        ("MATCH (:L007 {id: $a})-[r:R007]->(:L007 {id: $b}) SET r.code = $v, r.n = $w",
         {"b": ids["c"], "v": "007", "w": 7}),
    ):
        r = e.execute_cypher(q, dict(params, a=ids["a"]))
        assert r.error is None, (q, r.error)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
        e.delete_node(nid)


def _rows(g, query):
    e, ids = g
    r = e.execute_cypher(query, {"a": ids["a"]})
    assert r.error is None, (query, r.error)
    by_id = {v: k for k, v in ids.items()}
    return {by_id[row[0]]: row[1:] for row in r.rows if row[0] in by_id}


def _typed(v):
    return (type(v).__name__, v)


def test_return_reads_the_string(g):
    got = _rows(g, "MATCH (:L007 {id: $a})-[r:R007]->(b) RETURN b.id, r.code, r.n")
    assert {k: tuple(map(_typed, v)) for k, v in got.items()} == {
        "b": (("str", "007"), ("int", 7)),
        "c": (("str", "007"), ("int", 7)),
    }


def test_properties_reads_the_string(g):
    got = _rows(g, "MATCH (:L007 {id: $a})-[r:R007]->(b) RETURN b.id, properties(r)")
    props = {k: (json.loads(v[0]) if isinstance(v[0], str) else v[0]) for k, v in got.items()}
    assert {k: (_typed(p["code"]), _typed(p["n"])) for k, p in props.items()} == {
        "b": (("str", "007"), ("int", 7)),
        "c": (("str", "007"), ("int", 7)),
    }
