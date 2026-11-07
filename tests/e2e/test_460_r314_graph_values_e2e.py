"""4.6.0 R314, live: a node, relationship or path answers one shape whatever holds it, on every surface (fixes80;
tests/unit/test_460_r314_graph_values_in_containers.py has the rule).

Before (measured 2026-11-07 at be6cd07c, 2026.3 with driver 5.4.0 and Community 2026.1 with 5.4.1 alike):
`collect(n)` answered a list of the nodes' JSON text, `head(collect(n))` and an UNWIND of it the text, `RETURN r` the
text, `collect(r)` the types alone (['L314']), `collect(p)` and `[p]` the paths' text; `[n]`, `{f: n}`, `[[n]]`,
`[r]` and `RETURN p` answered the dicts this file expects of all of them.

The graph, by hand: (:F314 {node_id: 'f314a', t: 'A', y: 1979})-[:L314 {w: 2}]->(:F314 {node_id: 'f314b', t: 'B'}).
Every expected value is worked from it; none is read from IVG. Runs against the IRIS the R314 variables name
(IVG_NS_IVG460FXGV, an IVG460FX* scratch namespace with IVG set up; IVG_R314_HOST / IVG_R314_PORT, the enterprise test container by default), under whichever driver runs it.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NS_IVG460FXGV", "")
#: the enterprise test container by default (tests/e2e/ns460alt.py); IVG_R314_HOST / IVG_R314_PORT name another IRIS
HOST = os.environ.get("IVG_R314_HOST", "localhost")
PORT = int(os.environ.get("IVG_R314_PORT", str(alt.PORT)))
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NS_IVG460FXGV must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(HOST == "localhost" and not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

A = "MATCH (n:F314 {node_id: 'f314a'}) "
R = "MATCH (:F314)-[r:L314]->(:F314) "
P = "MATCH p = (:F314)-[:L314]->(:F314) "

NODE = {"_id": "f314a", "_labels": ["F314"], "_props": sorted([
    json.dumps({"key": "node_id", "value": "f314a"}, separators=(",", ":")),
    json.dumps({"key": "t", "value": "A"}, separators=(",", ":")),
    json.dumps({"key": "y", "value": 1979}, separators=(",", ":"))])}
NODE_B = {"_id": "f314b", "_labels": ["F314"], "_props": sorted([
    json.dumps({"key": "node_id", "value": "f314b"}, separators=(",", ":")),
    json.dumps({"key": "t", "value": "B"}, separators=(",", ":"))])}
REL = {"type": "L314", "props": {"w": 2}}
PATH = {"nodes": ["f314a", "f314b"], "rels": ["L314"]}

CASES = [
    ("[n]", A + "RETURN [n] AS v", [NODE]),
    ("{f:n}", A + "RETURN {f: n} AS v", {"f": NODE}),
    ("[[n]]", A + "RETURN [[n]] AS v", [[NODE]]),
    ("collect(n)", A + "RETURN collect(n) AS v", [NODE]),
    ("head(collect(n))", A + "RETURN head(collect(n)) AS v", NODE),
    ("UNWIND collect(n)", A + "WITH collect(n) AS ns UNWIND ns AS v RETURN v", NODE),
    ("collect(n)[0]", A + "RETURN collect(n)[0] AS v", NODE),
    ("{f:collect(n)}", A + "WITH collect(n) AS ns RETURN {f: ns} AS v", {"f": [NODE]}),
    ("r", R + "RETURN r AS v", REL),
    ("[r]", R + "RETURN [r] AS v", [REL]),
    ("{k:r}", R + "RETURN {k: r} AS v", {"k": REL}),
    ("collect(r)", R + "RETURN collect(r) AS v", [REL]),
    ("head(collect(r))", R + "RETURN head(collect(r)) AS v", REL),
    ("{k:collect(r)}", R + "RETURN {k: collect(r)} AS v", {"k": [REL]}),
    ("collect(nodes(p))", "MATCH p = (:F314)-[:L314]->(:F314) RETURN collect(nodes(p)) AS v", [[NODE, NODE_B]]),
    ("[n, 1][0]", A + "WITH [n, 1] AS l RETURN l[0] AS v", NODE),
    ("p", P + "RETURN p AS v", PATH),
    ("[p]", P + "RETURN [p] AS v", [PATH]),
    ("collect(p)", P + "RETURN collect(p) AS v", [PATH]),
    ("{k:p}", P + "RETURN {k: p} AS v", {"k": PATH}),
    # fixes80: a map's value was cast to VARCHAR without a length, which IRIS makes 30 characters
    ("{a:long-string}", A + "WITH n.t + '-abcdefghijklmnopqrstuvwxyz0123456789' AS s RETURN {a: s} AS v",
     {"a": "A-abcdefghijklmnopqrstuvwxyz0123456789"}),
    ("{a:5000-chars}", A + "WITH n.t + '" + "x" * 5000 + "' AS s RETURN {a: s} AS v", {"a": "A" + "x" * 5000}),
    ("{a:list}", "WITH ['aaaaaaaaaa', 'bbbbbbbbbb', 'cccccccccc'] AS l RETURN {a: l} AS v",
     {"a": ["aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc"]}),
]


def _same(v):
    """`v` with each node's `_props` in a fixed order, which the rule leaves to the catalog."""
    if isinstance(v, dict):
        return {k: sorted(x) if k == "_props" and isinstance(x, list) else _same(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_same(x) for x in v]
    return v


@pytest.fixture(scope="module", params=[False, True], ids=["sql", "native"])
def eng(request):
    import iris

    conn = iris.dbapi.connect(hostname=HOST, port=PORT, namespace=NS, username="_SYSTEM", password="SYS")
    e = IRISGraphEngine(conn, embedding_dimension=8, native_cypher=request.param)
    e.execute_cypher("MATCH (n:F314) DETACH DELETE n", raise_on_error=True)
    e.execute_cypher("CREATE (:F314 {node_id: 'f314a', t: 'A', y: 1979})-[:L314 {w: 2}]->"
                     "(:F314 {node_id: 'f314b', t: 'B'})", raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:F314) DETACH DELETE n")
        conn.close()


def _value(rows, columns):
    assert columns == ["v"], columns
    [[v]] = rows
    return v


@pytest.mark.parametrize("q, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_execute_cypher(eng, q, want):
    r = eng.execute_cypher(q, raise_on_error=True)
    assert _same(_value(r.rows, r.columns)) == _same(want)


@pytest.mark.parametrize("q, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_rest(eng, q, want):
    from fastapi.testclient import TestClient

    from iris_vector_graph import cypher_api

    with patch.object(cypher_api, "_get_engine", return_value=eng):
        resp = TestClient(cypher_api.app).post("/api/cypher", json={"query": q, "parameters": {}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _same(_value(body["rows"], body["columns"])) == _same(want)


@pytest.mark.parametrize("q, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_graphql(eng, q, want):
    from types import SimpleNamespace

    from iris_vector_graph.gql.resolvers import resolve_cypher

    out = asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": eng}), q))
    assert _same(_value(out.rows, out.columns)) == _same(want)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bolt(eng):
    neo4j = pytest.importorskip("neo4j")
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

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10), "the Bolt server did not start"
    driver = neo4j.GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("u", "p"))
    try:
        with driver.session() as s:
            yield s
    finally:
        driver.close()
        loop.call_soon_threadsafe(loop.stop)


def _bolt_value(v):
    """A relationship returned by itself is Bolt's Relationship structure, its type and properties the dict's."""
    neo4j_graph = pytest.importorskip("neo4j.graph")
    if isinstance(v, neo4j_graph.Relationship):
        return {"type": v.type, "props": dict(v)}
    return v


@pytest.mark.parametrize("q, want", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_bolt(bolt, q, want):
    records = list(bolt.run(q))
    assert [r.keys() for r in records] == [["v"]]
    assert _same(_bolt_value(records[0]["v"])) == _same(want)


def test_bolt_a_relationship_between_two_nodes_is_a_relationship(bolt):
    """`RETURN a, r, b`: the nodes are Bolt Nodes (their triplets recomposed) and r a Relationship from a to b with
    its type and properties; r went out as a map."""
    neo4j_graph = pytest.importorskip("neo4j.graph")
    [rec] = list(bolt.run("MATCH (a:F314)-[r:L314]->(b:F314) RETURN a, r, b"))
    a, r, b = rec["a"], rec["r"], rec["b"]
    assert isinstance(a, neo4j_graph.Node) and isinstance(b, neo4j_graph.Node), (a, b)
    assert isinstance(r, neo4j_graph.Relationship), r
    assert (r.type, dict(r)) == ("L314", {"w": 2})
    assert (dict(a)["id"], dict(b)["id"]) == ("f314a", "f314b")


@pytest.mark.parametrize("q, want", [
    (A + "WITH [n, 1] AS l RETURN l AS v", [NODE, 1]),
    (A + "WITH [n, 1] AS l UNWIND l AS v RETURN v", None),
])
def test_a_list_mixing_a_node_with_other_values(eng, q, want):
    r = eng.execute_cypher(q, raise_on_error=True)
    if want is None:  # the UNWIND: one row per element, the node's as its dict
        assert sorted((_same(v) for [v] in r.rows), key=str) == sorted([_same(NODE), 1], key=str)
    else:
        assert _same(_value(r.rows, r.columns)) == _same(want)
