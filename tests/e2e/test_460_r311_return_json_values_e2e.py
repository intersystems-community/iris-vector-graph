"""4.6.0 R311, live: a RETURNed list or map expression answers the list or map through execute_cypher, REST
/api/cypher and Bolt, and a string answers the string (fixes67; tests/unit/test_460_r311_return_json_values.py).

Before: execute_cypher and REST answered `[n.x, 'a']` as the text '[1,"a"]', and Bolt decoded any string that starts
with `[` or `{`, and read '1e5' as a float, so `RETURN '{"a":1}'` answered a map over Bolt. Nested lists and maps came
back as strings inside the list everywhere (`[[n.x], {k: n.s}]` answered ['[1]', '{"k":"a"}']), `WITH $p AS l RETURN
l` answered l as a node's three columns, and `MATCH p = ()-[:R]->() RETURN nodes(p)` failed -23.

The graph, by hand: (:J311 {i: 1, x: 1, s: 'a', lst: [1, 2]})-[:R311 {w: 2}]->(:J311 {i: 2, x: 2.5, s: 'b'}). Every
expected answer below is worked from it; none is read from IVG. Values compare with their Python type (1 is not 1.0
and not True). Run under both drivers (5.4.0 in the repo's .venv, 5.4.1 a fresh install).
"""

from __future__ import annotations

import asyncio
import math
import os
import socket
import threading
from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

A = "MATCH (n:J311 {i: 1}) "
P = "MATCH p = ()-[:R311]->() "

CASES = [
    # (id, query, params, the one value of the one row)
    ("list-of-prop-and-literal", A + "RETURN [n.x, 'a'] AS v", None, [1, "a"]),
    ("list-literal", "RETURN [1, 'a', 2.5, true, null] AS v", None, [1, "a", 2.5, True, None]),
    ("empty-list", "RETURN [] AS v", None, []),
    ("empty-map", "RETURN {} AS v", None, {}),
    ("keys-of-map", "RETURN keys({a: 1}) AS v", None, ["a"]),
    ("labels", A + "RETURN labels(n) AS v", None, ["J311"]),
    ("comprehension", A + "RETURN [x IN n.lst | x * 2] AS v", None, [2, 4]),
    ("filter-comprehension", A + "RETURN [x IN n.lst WHERE x > 1] AS v", None, [2]),
    ("slice", A + "RETURN n.lst[0..1] AS v", None, [1]),
    ("list-plus-list", A + "RETURN n.lst + [3] AS v", None, [1, 2, 3]),
    ("list-plus-front", A + "RETURN [0] + n.lst AS v", None, [0, 1, 2]),
    ("range", "RETURN range(1, 3) AS v", None, [1, 2, 3]),
    ("split", "RETURN split('a,b', ',') AS v", None, ["a", "b"]),
    ("map-literal", A + "RETURN {a: n.x, b: [n.s]} AS v", None, {"a": 1, "b": ["a"]}),
    ("map-projection", A + "RETURN n {.x, .s} AS v", None, {"x": 1, "s": "a"}),
    ("nested", A + "RETURN [[n.x], {k: n.s}] AS v", None, [[1], {"k": "a"}]),
    ("nested-map", A + "RETURN {k: [[n.x]], m: {z: [n.s]}} AS v", None, {"k": [[1]], "m": {"z": ["a"]}}),
    ("nested-deep", A + "RETURN [n.x, [n.s, [n.x, {q: n.s}]]] AS v", None, [1, ["a", [1, {"q": "a"}]]]),
    ("nested-literal-in-row-list", A + "RETURN [n.x, [1, 2]] AS v", None, [1, [1, 2]]),
    ("with-list-in-list", A + "WITH [n.x] AS l RETURN [l, l] AS v", None, [[1], [1]]),
    ("with-list-in-map", A + "WITH [n.x] AS l RETURN {a: l} AS v", None, {"a": [1]}),
    ("with-list", A + "WITH [n.x, n.s] AS l RETURN l AS v", None, [1, "a"]),
    ("collect", "MATCH (n:J311) WITH n ORDER BY n.i RETURN collect(n.x) AS v", None, [1, 2.5]),
    ("list-param", "RETURN $p AS v", {"p": [1, "a", [2]]}, [1, "a", [2]]),
    ("map-param", "RETURN $p AS v", {"p": {"a": 1, "b": ["x"]}}, {"a": 1, "b": ["x"]}),
    ("with-list-param", "WITH $p AS l RETURN l AS v", {"p": [1, "a"]}, [1, "a"]),
    # parameters: the query knows them before it runs (before: `$p[1]` failed -23, `$m.k` bound one parameter for two
    # `?`, `RETURN $b` of true answered 1 and `[$b]` [1])
    ("param-element-list", "RETURN $p[1] AS v", {"p": ["a", [1]]}, [1]),
    ("param-element-string", "RETURN $p[0] AS v", {"p": ["a", [1]]}, "a"),
    ("param-element-negative", "RETURN $p[-1] AS v", {"p": ["a", True]}, True),
    ("param-element-out-of-range", "RETURN $p[5] AS v", {"p": ["a"]}, None),
    ("param-slice", "RETURN $p[0..2] AS v", {"p": ["a", [1], {"k": 1}]}, ["a", [1]]),
    ("param-map-key", "RETURN $m.k AS v", {"m": {"k": [2]}}, [2]),
    ("param-map-subscript", "RETURN $m['k'] AS v", {"m": {"k": {"z": 1}}}, {"z": 1}),
    ("param-bool", "RETURN $b AS v", {"b": True}, True),
    ("list-of-bool-param", "RETURN [$b, [$b, 1]] AS v", {"b": True}, [True, [True, 1]]),
    ("map-of-params", "RETURN {k: $b, e: $p[1], s: $s} AS v", {"b": True, "p": [0, [1]], "s": 'it"s'},
     {"k": True, "e": [1], "s": 'it"s'}),
    ("with-list-of-param-element", "WITH [$p[1]] AS l RETURN l AS v", {"p": [0, [1]]}, [[1]]),
    ("path-nodes-anonymous", P + "RETURN [x IN nodes(p) | x.i] AS v", None, [1, 2]),
    ("path-nodes-size", P + "RETURN size(nodes(p)) AS v", None, 2),
    # strings that spell a list, a map or a number are strings
    ("string-spells-map", "RETURN '{\"a\":1}' AS v", None, '{"a":1}'),
    ("string-spells-list", "RETURN '[1]' AS v", None, "[1]"),
    ("string-spells-float", "RETURN '1e5' AS v", None, "1e5"),
    ("with-string-spells-map", "WITH '{\"a\":1}' AS s RETURN s AS v", None, '{"a":1}'),
    ("list-of-spelled-strings", "RETURN ['[1]', '{}'] AS v", None, ["[1]", "{}"]),
    ("map-of-with-strings", "WITH '7' AS s, '.95' AS d, 'a\nb' AS nl, 'true' AS t RETURN {s: s, d: d, nl: nl, t: t} AS v",
     None, {"s": "7", "d": ".95", "nl": "a\nb", "t": "true"}),
    ("map-of-string-functions", A + "RETURN {u: toUpper('true'), c: 'x' + n.s} AS v", None, {"u": "TRUE", "c": "xa"}),
    ("start-node-property", "MATCH (a:J311 {i: 1})-[r:R311]->() RETURN startNode(r).i AS v", None, 1),
    ("end-node-property", "MATCH (a:J311 {i: 1})-[r:R311]->() RETURN endNode(r).x AS v", None, 2.5),
    ("string-param-spells-list", "RETURN $p AS v", {"p": "[1, 2]"}, "[1, 2]"),
]


def _typed(x):
    if isinstance(x, float) and math.isnan(x):
        return ("float", "nan")
    if isinstance(x, (list, tuple)):
        return ("list", [_typed(e) for e in x])
    if isinstance(x, dict):
        return ("map", {k: _typed(e) for k, e in x.items()})
    return (type(x).__name__, x)


@pytest.fixture(scope="module")
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:J311) DETACH DELETE n", raise_on_error=True)
    e.execute_cypher(
        "CREATE (:J311 {i: 1, x: 1, s: 'a', lst: [1, 2]})-[:R311 {w: 2}]->(:J311 {i: 2, x: 2.5, s: 'b'})",
        raise_on_error=True,
    )
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:J311) DETACH DELETE n")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bolt(eng):
    """A real Bolt server over the test engine, and a neo4j driver session against it."""
    import neo4j  # the dev extra's (pyproject.toml): a missing driver is an error, not a skip

    from iris_vector_graph.bolt_server import start_tcp_bolt_server

    port = _free_port()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def serve():
        server = await start_tcp_bolt_server(lambda: eng, host="127.0.0.1", port=port)
        ready.set()
        return server

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(10), "the Bolt server did not start"
    driver = neo4j.GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("u", "p"))
    try:
        with driver.session() as s:
            yield s
    finally:
        driver.close()
        loop.call_soon_threadsafe(loop.stop)


def _engine_value(e, q, params):
    r = e.execute_cypher(q, params or {}, raise_on_error=True)
    assert not r.error, r.error
    assert r.columns == ["v"], r.columns
    [[v]] = r.rows
    return v


@pytest.mark.parametrize("q, params, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_execute_cypher(eng, q, params, want):
    assert _typed(_engine_value(eng, q, params)) == _typed(want)


@pytest.mark.parametrize("q, params, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_rest(eng, q, params, want):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    with patch.object(cypher_api, "_get_engine", return_value=eng):
        resp = TestClient(cypher_api.app).post("/api/cypher", json={"query": q, "parameters": params or {}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("columns") == ["v"], body
    [[v]] = body["rows"]
    assert _typed(v) == _typed(want)


@pytest.mark.parametrize("q, params, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_bolt(bolt, q, params, want):
    records = list(bolt.run(q, params or {}))
    assert [r.keys() for r in records] == [["v"]]
    assert _typed(records[0]["v"]) == _typed(want)


def _path_shape(v):
    # by hand: one R311 hop between the two J311 nodes; a path answers its node ids and relationship types
    assert isinstance(v, dict) and set(v) == {"nodes", "rels"}, v
    assert v["rels"] == ["R311"] and len(v["nodes"]) == 2 and all(isinstance(x, str) for x in v["nodes"]), v
    return True


@pytest.mark.parametrize("q", [
    "MATCH p = (:J311 {i: 1})-[:R311]->() RETURN p AS v",
    "MATCH p = (:J311 {i: 1})-[:R311*1..1]->() RETURN p AS v",
    "MATCH p = (:J311 {i: 1})-[:R311]->() WITH p RETURN p AS v",
])
def test_a_path_is_a_dict_on_every_surface(eng, bolt, q):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    assert _path_shape(_engine_value(eng, q, None))
    with patch.object(cypher_api, "_get_engine", return_value=eng):
        [[v]] = TestClient(cypher_api.app).post("/api/cypher", json={"query": q}).json()["rows"]
    assert _path_shape(v)
    records = list(bolt.run(q))
    assert _path_shape(records[0]["v"])


@pytest.mark.parametrize("q, params, want", [
    ("WITH ['[1]', 'true', '{\"k\":1}', 2] AS l UNWIND l AS x RETURN x", None, ["[1]", "true", '{"k":1}', 2]),
    ("UNWIND $l AS x RETURN x", {"l": ["[1]", "x", 3]}, ["[1]", "x", 3]),
    ("WITH [1, 2.5] AS list UNWIND list AS x RETURN *", None, None),
])
def test_an_unwound_known_list_keeps_its_strings(eng, q, params, want):
    r = eng.execute_cypher(q, params or {}, raise_on_error=True)
    if want is None:  # by hand: each row is the list and one of its elements, typed (5.4.1 answered x as text)
        assert [[_typed(c) for c in row] for row in r.rows] == [
            [_typed([1, 2.5]), _typed(1)], [_typed([1, 2.5]), _typed(2.5)]
        ]
    else:
        assert [_typed(row[0]) for row in r.rows] == [_typed(w) for w in want]
