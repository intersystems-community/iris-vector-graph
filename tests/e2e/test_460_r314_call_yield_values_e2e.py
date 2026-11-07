"""4.6.0 R314 (part B), live: every procedure IVG answers to CALL yields its columns typed, through execute_cypher
(native_cypher on and off), REST /api/cypher, Bolt (a neo4j driver against IVG's Bolt server) and GraphQL's `cypher`
field (tests/unit/test_460_r314_call_yield_values.py drives the same over fakes).

Before (be6cd07c): `ivg.shortestPath.weighted`'s `path` answered its JSON text on both routes (the handler's and the
translated CTE's), and `path.nodes` too; `apoc.meta.schema`'s `value` was an IVGResult object (REST: its pydantic dump,
Bolt: its repr string).

Runs only when IVG_R314_HOST and IVG_R314_NAMESPACE are set (IVG_R314_PORT, IVG_R314_USER, IVG_R314_PASSWORD): a namespace
with IVG's classes deployed. It loads the graph below in the default graph, builds ^NKG, a BM25 and an IVF index, and
removes them all after; other data in the namespace is left alone, and an answer over the whole namespace is read for
these nodes (and counts are read with SQL before the load).

The graph, by hand: four :R314Spot nodes and four R314_LIKES edges, with weights
    r314_a -> r314_b 2.5, r314_a -> r314_c 0.5, r314_c -> r314_b 1.25, r314_b -> r314_d 1.0
texts a 'red planet', b 'green country', c 'red country', d 'blue', and 8-wide vectors a e0, b e1, c 0.6 e0 + 0.8 e1,
d e2. Every expected answer below is worked from it; none is read from IVG:
- the cheapest r314_a -> r314_b is through r314_c: 0.5 + 1.25 = 1.75, two hops; unweighted, the direct edge, cost 1;
- out-degrees a 2, b 1, c 1, d 0; undirected a, b, c are a triangle (coreness 2, one triangle, lcc 1, 1/3 for b of
  degree 3) and d hangs off b (coreness 1, lcc 0); no cycle, so each node is its own component;
- every shortest path through a node goes through r314_b (a -> b -> d, c -> b -> d), so only b has betweenness;
- harmonic out-closeness a 1 + 1 + 1/2, c 1 + 1/2, b 1, d 0: a > c > b > d;
- cosine to e0: a 1, c 0.6, b and d 0; 'red' is in a's and c's texts only.
"""

from __future__ import annotations

import asyncio
import math
import os
import socket
import threading
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris_vector_graph.cypher.procedure_signatures import PROCEDURE_SIGNATURES, path_keys, signature
from iris_vector_graph.engine import IRISGraphEngine

HOST = os.environ.get("IVG_R314_HOST")
NAMESPACE = os.environ.get("IVG_R314_NAMESPACE", "")
#: IVG_R314_NO_IVF=1: build no IVF index and skip ivg.ivf.search, for an interpreter whose sklearn cannot be imported
#: (ivf_build's k-means; the fixes64 5.4.1 venv hangs in `import sklearn.cluster`)
NO_IVF = os.environ.get("IVG_R314_NO_IVF") == "1"
pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not (HOST and NAMESPACE), reason="IVG_R314_HOST or IVG_R314_NAMESPACE is not set")]

DIM = 8


def _vec(*pairs):
    v = [0.0] * DIM
    for i, x in pairs:
        v[i] = x
    return v


IDS = ["r314_a", "r314_b", "r314_c", "r314_d"]
MINE = set(IDS)
TEXT = {"r314_a": "red planet", "r314_b": "green country", "r314_c": "red country", "r314_d": "blue"}
VEC = {"r314_a": _vec((0, 1.0)), "r314_b": _vec((1, 1.0)), "r314_c": _vec((0, 0.6), (1, 0.8)), "r314_d": _vec((2, 1.0))}
EDGES = [("r314_a", "r314_b", 2.5), ("r314_a", "r314_c", 0.5), ("r314_c", "r314_b", 1.25), ("r314_b", "r314_d", 1.0)]
E0 = _vec((0, 1.0))
WEIGHTED = {"nodes": ["r314_a", "r314_c", "r314_b"], "rels": ["R314_LIKES", "R314_LIKES"], "costs": [0.5, 1.25],
            "length": 2, "totalCost": 1.75}
PROPS = {"id", "name", "text"}  # create_node stores `id`, equal to the node ID


def _connect():
    import iris

    return iris.dbapi.connect(
        hostname=HOST, port=int(os.environ.get("IVG_R314_PORT", "1972")),
        namespace=NAMESPACE,
        username=os.environ.get("IVG_R314_USER", "_SYSTEM"), password=os.environ.get("IVG_R314_PASSWORD", "SYS"),
    )


def _clear(e):
    for drop in (lambda: e.bm25_drop("r314_bm25"), lambda: e.ivf_drop("r314_ivf")):
        try:
            drop()
        except Exception as exc:  # an index the run never built
            print(f"r314 cleanup: {exc}")
    e.execute_cypher("MATCH (n:R314Spot) DETACH DELETE n", raise_on_error=True)


def _rebuild_nkg(e):
    """^NKG from ^KG (the community and centrality procedures read it); rebuild_nkg(), not sync(), which would
    rebuild ^KG of the whole namespace too."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        e.rebuild_nkg()


@pytest.fixture(scope="module")
def world():
    conn = _connect()
    sql = IRISGraphEngine(conn, embedding_dimension=DIM, native_cypher=False)
    native = IRISGraphEngine(conn, embedding_dimension=DIM, native_cypher=True)
    _clear(sql)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {sql._ivg_schema}.nodes")
    other_nodes = int(cur.fetchone()[0])
    cur.execute(f"SELECT COUNT(*) FROM {sql._ivg_schema}.rdf_edges")
    other_edges = int(cur.fetchone()[0])
    for n in IDS:
        assert sql.create_node(n, ["R314Spot"], {"name": n.upper(), "text": TEXT[n]})
        sql.store_embedding(n, VEC[n])
    for s, o, w in EDGES:
        assert sql.create_edge(s, "R314_LIKES", o, weight=w)
    _rebuild_nkg(sql)
    sql.bm25_build("r314_bm25", ["text"])
    if not NO_IVF:
        sql.ivf_build("r314_ivf", nlist=1, node_ids=IDS)
    try:
        yield SimpleNamespace(sql=sql, native=native, nodes=other_nodes + 4, edges=other_edges + 4)
    finally:
        _clear(sql)
        _rebuild_nkg(sql)
        conn.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bolt(world):
    neo4j = pytest.importorskip("neo4j")
    from iris_vector_graph.bolt_server import start_tcp_bolt_server

    port = _free_port()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def serve():
        await start_tcp_bolt_server(lambda: world.sql, host="127.0.0.1", port=port)
        ready.set()

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


# ---------------------------------------------------------------------------
# a surface's answer, as rows of {returned name: value}; a node column is its ID, after checking its form
# ---------------------------------------------------------------------------


def _node_columns(columns, row):
    out, i = {}, 0
    while i < len(columns):
        c = columns[i]
        if c.endswith("_id") and columns[i + 1:i + 3] == [c[:-3] + "_labels", c[:-3] + "_props"]:
            out[c[:-3]] = ("node", row[i])
            i += 3
        else:
            out[c] = row[i]
            i += 1
    return out


def _rows(world, bolt, surface, q):
    if surface in ("cypher-sql", "cypher-native"):
        eng = world.sql if surface == "cypher-sql" else world.native
        r = eng.execute_cypher(q, {}, raise_on_error=True)
        assert not r.error, r.error
        return [_node_columns(list(r.columns), list(row)) for row in r.rows]
    if surface == "rest":
        from fastapi.testclient import TestClient

        from iris_vector_graph import cypher_api

        with patch.object(cypher_api, "_get_engine", return_value=world.sql):
            resp = TestClient(cypher_api.app).post("/api/cypher", json={"query": q})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return [_node_columns(body["columns"], row) for row in body["rows"]]
    if surface == "graphql":
        import json

        from iris_vector_graph.gql.resolvers import resolve_cypher

        res = asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": world.sql}), q, None))
        return [_node_columns(list(res.columns), row) for row in json.loads(json.dumps(res.rows))]
    if surface == "bolt":
        from neo4j.graph import Node

        out = []
        for rec in bolt.run(q):
            out.append({k: ("node", v["id"]) if isinstance(v, Node) else v for k, v in rec.items()})
        return out
    raise AssertionError(surface)


_SCALAR = {"STRING": str, "INTEGER": int, "FLOAT": float, "BOOLEAN": bool}


def _check_types(proc, rows, renamed=None, node_as_id=False):
    """Every answered column has its registry type: a node column a node (three columns; Bolt a Node), a LIST a list,
    a MAP or PATH a dict (a PATH exactly its keys), a scalar its Python type."""
    back = {v: k for k, v in (renamed or {}).items()}
    types = dict(signature(proc))
    for row in rows:
        for name, value in row.items():
            column = back.get(name, name)
            kind = types[column]
            if kind == "NODE":
                if node_as_id:
                    assert isinstance(value, str), (proc, name, value)
                else:
                    assert isinstance(value, tuple) and value[0] == "node", (proc, name, value)
                continue
            if value is None:
                continue
            if kind == "LIST":
                assert isinstance(value, list), (proc, name, value)
            elif kind in ("MAP", "PATH"):
                assert isinstance(value, dict), (proc, name, value)
                if kind == "PATH":
                    assert set(value) == set(path_keys(proc, column)), value
            elif kind in _SCALAR:
                assert type(value) is _SCALAR[kind], (proc, name, type(value), value)


def _node(v):
    return v[1] if isinstance(v, tuple) else v


def _mine(rows, col="node"):
    return {_node(r[col]): r for r in rows if _node(r[col]) in MINE}


def _typed(x):
    if isinstance(x, (list, tuple)):
        return ("list", [_typed(e) for e in x])
    if isinstance(x, dict):
        return ("map", {k: _typed(e) for k, e in x.items()})
    return (type(x).__name__, x)


# ---------------------------------------------------------------------------
# the cases: (procedure, query, check(rows, world), options)
# ---------------------------------------------------------------------------

W = "CALL ivg.shortestPath.weighted('r314_a', 'r314_b') "


def _weighted(col="path"):
    def check(rows, world):
        assert len(rows) == 1, rows
        assert _typed(rows[0][col]) == _typed(WEIGHTED) and _typed(rows[0]["totalCost"]) == _typed(1.75), rows
    return check


def _vector(rows, world):
    assert [_node(r["node"]) for r in rows] == ["r314_a", "r314_c"], rows
    assert [round(r["score"], 6) for r in rows] == [1.0, 0.6], rows


def _score_order(want):
    def check(rows, world):
        mine = [_node(r["node"]) for r in rows if _node(r["node"]) in MINE]
        assert mine == want, rows
    return check


def _betweenness(col):
    def check(rows, world):
        # by hand: b is on a -> b -> d and c -> b -> d, directed and unnormalized 2; a node with no score may have no row
        scored = {n: r["score"] for n, r in _mine(rows, col).items() if r["score"] != 0}
        assert scored == {"r314_b": 2.0}, rows
    return check


def _leiden(rows, world):
    sizes = {}
    for r in rows:
        sizes[r["community"]] = sizes.get(r["community"], 0) + 1
    assert MINE <= set(_mine(rows)), rows
    assert all(r["size"] == sizes[r["community"]] for r in rows), rows


def _pagerank(rows, world):
    mine = _mine(rows, "nodeId")
    assert set(mine) == MINE and math.isclose(sum(r["score"] for r in rows), 1.0, rel_tol=1e-6), rows
    assert mine["r314_a"]["score"] < mine["r314_b"]["score"]  # a has no in-edge


def _jmx(rows, world):
    attrs = rows[0]["attributes"]
    assert rows[0]["name"] == "org.neo4j:instance=kernel#0,name=Store file sizes", rows[0]
    assert attrs["TotalStoreSize"] == {"value": world.nodes * 200}
    assert attrs["RelationshipStoreSize"] == {"value": world.edges * 100}
    assert rows[1]["attributes"]["NumberOfNodeIdsInUse"] == {"value": world.nodes}


SPOT_PROP = {"type": "STRING", "indexed": False, "unique": False, "existence": False}

CASES = [
    ("ivg.shortestPath.weighted", W + "YIELD path, totalCost RETURN path, totalCost", _weighted(), {}, "handler"),
    ("ivg.shortestPath.weighted", W + "YIELD path, totalCost", _weighted(), {}, "handler-no-return"),
    ("ivg.shortestPath.weighted", W + "YIELD path, totalCost RETURN path, totalCost ORDER BY totalCost", _weighted(),
     {}, "translated"),
    ("ivg.shortestPath.weighted", W + "YIELD path AS q, totalCost RETURN q, totalCost LIMIT 1", _weighted("q"),
     {"renamed": {"path": "q"}}, "yield-alias"),
    ("ivg.shortestPath.weighted", W + "YIELD path, totalCost WITH path, totalCost WHERE totalCost < 5 "
     "RETURN path, totalCost", _weighted(), {}, "with"),
    ("ivg.shortestPath.weighted", W + "YIELD path, totalCost RETURN path.nodes AS nodes, path.rels AS rels, "
     "path.costs AS costs, path.length AS length, totalCost",
     lambda rows, w: _typed(rows) == _typed([{"nodes": WEIGHTED["nodes"], "rels": WEIGHTED["rels"],
                                               "costs": [0.5, 1.25], "length": 2, "totalCost": 1.75}]) or
     pytest.fail(repr(rows)), {"untyped": True}, "path-keys"),
    ("ivg.vector.search", "CALL ivg.vector.search('R314Spot', 'emb', $v, 2) YIELD node, score", _vector,
     {"params": True, "node_as_id": True}, "handler"),
    ("ivg.vector.search", "CALL ivg.vector.search('R314Spot', 'emb', $v, 2) YIELD node, score "
     "RETURN node, score ORDER BY score DESC", _vector, {"params": True}, "translated"),
    ("ivg.neighbors", "CALL ivg.neighbors(['r314_a'], 'R314_LIKES') YIELD neighbor RETURN neighbor",
     lambda rows, w: {_node(r["neighbor"]) for r in rows} == {"r314_b", "r314_c"} or pytest.fail(repr(rows)), {}, ""),
    ("ivg.ppr", "CALL ivg.ppr(['r314_a']) YIELD node, score RETURN node, score",
     lambda rows, w: ("r314_a" in {_node(r["node"]) for r in rows}
                      and {_node(r["node"]) for r in rows} <= MINE) or pytest.fail(repr(rows)), {}, ""),
    ("ivg.bm25.search", "CALL ivg.bm25.search('r314_bm25', 'red', 2) YIELD node, score RETURN node, score",
     lambda rows, w: ({_node(r["node"]) for r in rows} == {"r314_a", "r314_c"} and rows[0]["score"] > 0
                      and math.isclose(rows[0]["score"], rows[1]["score"])) or pytest.fail(repr(rows)), {}, ""),
    ("ivg.ivf.search", "CALL ivg.ivf.search('r314_ivf', $v, 2, 1) YIELD node, score RETURN node, score",
     lambda rows, w: [_node(r["node"]) for r in rows] == ["r314_a", "r314_c"] or pytest.fail(repr(rows)),
     {"params": True}, ""),
    ("ivg.retrieve", "USE GRAPH '' CALL ivg.retrieve('red', 2, 'r314_bm25', 'R314Spot') YIELD node, rrf_score "
     "RETURN node, rrf_score",
     # by hand: a and c are BM25's only two (tied) and the vector arm's first two, so they are the two fused; a's
     # vector rank is 1, its BM25 rank 1 or 2
     lambda rows, w: ([_node(r["node"]) for r in rows][0] == "r314_a"
                      and {_node(r["node"]) for r in rows} == {"r314_a", "r314_c"}
                      and any(math.isclose(rows[0]["rrf_score"], x) for x in (2 / 61, 1 / 61 + 1 / 62)))
     or pytest.fail(repr(rows)), {"embed": True}, ""),
    ("ivg.degreeCentrality", "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, score, degree "
     "RETURN node, score, degree",
     lambda rows, w: ({n: (r["degree"], r["score"]) for n, r in _mine(rows).items()}["r314_a"]
                      == (2, 2 / (w.nodes - 1))
                      and {n: r["degree"] for n, r in _mine(rows).items()}
                      == {"r314_a": 2, "r314_b": 1, "r314_c": 1, "r314_d": 0}) or pytest.fail(repr(rows)), {}, ""),
    ("ivg.betweenness", "CALL ivg.betweenness() YIELD node, score RETURN node, score", _betweenness("node"), {}, ""),
    ("ivg.closeness", "CALL ivg.closeness() YIELD node, score RETURN node, score",
     _score_order(["r314_a", "r314_c", "r314_b", "r314_d"]), {}, ""),
    # the graph has no cycle, so the power iteration does not converge in the default 100 rounds, and IVG raises as
    # networkx does (99b00ce9); by hand with networkx 3: eigenvector_centrality(DiGraph(EDGES)) raises
    # PowerIterationFailedConvergence at max_iter=100
    ("ivg.eigenvector", "CALL ivg.eigenvector() YIELD node, score RETURN node, score", None,
     {"raises": "PowerIterationFailedConvergence", "error_dropped_on": ("graphql", "bolt")}, ""),
    ("ivg.leiden", "CALL ivg.leiden({randomSeed: 1}) YIELD node, community, size RETURN node, community, size",
     _leiden, {}, ""),
    ("ivg.triangleCount", "CALL ivg.triangleCount() YIELD node, triangles, lcc RETURN node, triangles, lcc",
     lambda rows, w: {n: (r["triangles"], round(r["lcc"], 6)) for n, r in _mine(rows).items()}
     == {"r314_a": (1, 1.0), "r314_b": (1, round(1 / 3, 6)), "r314_c": (1, 1.0), "r314_d": (0, 0.0)}
     or pytest.fail(repr(rows)), {}, ""),
    ("ivg.scc", "CALL ivg.scc() YIELD node, component, size RETURN node, component, size",
     lambda rows, w: (all(r["size"] == 1 for r in _mine(rows).values())
                      and len({r["component"] for r in _mine(rows).values()}) == 4) or pytest.fail(repr(rows)), {}, ""),
    ("ivg.kcore", "CALL ivg.kcore() YIELD node, coreness RETURN node, coreness",
     lambda rows, w: {n: r["coreness"] for n, r in _mine(rows).items()}
     == {"r314_a": 2, "r314_b": 2, "r314_c": 2, "r314_d": 1} or pytest.fail(repr(rows)), {}, ""),
    ("db.labels", "CALL db.labels() YIELD label RETURN label",
     lambda rows, w: "R314Spot" in {r["label"] for r in rows} or pytest.fail(repr(rows)), {}, ""),
    ("db.relationshipTypes", "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
     lambda rows, w: "R314_LIKES" in {r["relationshipType"] for r in rows} or pytest.fail(repr(rows)), {}, ""),
    ("db.propertyKeys", "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey",
     lambda rows, w: PROPS <= {r["propertyKey"] for r in rows} or pytest.fail(repr(rows)), {}, ""),
    ("db.schema.visualization", "CALL db.schema.visualization() YIELD nodes, relationships",
     lambda rows, w: ("R314Spot" in {n["name"] for n in rows[0]["nodes"]}
                      and "R314_LIKES" in {r["type"] for r in rows[0]["relationships"]}) or pytest.fail(repr(rows)),
     {}, ""),
    ("db.schema.nodeTypeProperties",
     "CALL db.schema.nodeTypeProperties() YIELD nodeType, nodeLabels, propertyName, propertyTypes, mandatory",
     lambda rows, w: sorted((r["propertyName"], tuple(r["nodeLabels"]), tuple(r["propertyTypes"]), r["mandatory"])
                            for r in rows if r["nodeType"] == ":`R314Spot`")
     == [(p, ("R314Spot",), ("String",), False) for p in sorted(PROPS)] or pytest.fail(repr(rows)), {}, ""),
    ("db.schema.relTypeProperties",
     "CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes, mandatory",
     lambda rows, w: [(r["propertyName"], r["propertyTypes"], r["mandatory"]) for r in rows
                      if r["relType"] == "R314_LIKES"] == [("weight", ["STRING"], False)] or pytest.fail(repr(rows)),
     {}, ""),
    ("dbms.components", "CALL dbms.components() YIELD name, versions, edition",
     lambda rows, w: _typed(rows) == _typed([{"name": "iris-vector-graph", "versions": ["5.0.0"],
                                              "edition": "community"}]) or pytest.fail(repr(rows)), {}, ""),
    ("dbms.procedures", "CALL dbms.procedures() YIELD name, signature, description, mode, admin, option, "
     "defaultBuiltInRoles, isDeprecated, worksOnSystem, argumentDescription",
     lambda rows, w: _typed(rows[0]) == _typed({
         "name": "db.labels", "signature": "db.labels() :: (label :: STRING)", "description": "List all labels",
         "mode": "READ", "admin": False, "option": {}, "defaultBuiltInRoles": "neo4j", "isDeprecated": False,
         "worksOnSystem": True, "argumentDescription": []}) or pytest.fail(repr(rows)), {}, ""),
    ("dbms.functions", "CALL dbms.functions()", lambda rows, w: rows == [] or pytest.fail(repr(rows)), {}, ""),
    ("dbms.clientConfig", "CALL dbms.clientConfig() YIELD key, value",
     lambda rows, w: rows[0] == {"key": "browser.allow_outgoing_connections", "value": "false"}
     or pytest.fail(repr(rows)), {}, ""),
    ("dbms.security.showCurrentUser", "CALL dbms.security.showCurrentUser() YIELD username, roles, flags",
     lambda rows, w: rows == [{"username": "neo4j", "roles": [], "flags": []}] or pytest.fail(repr(rows)), {}, ""),
    ("dbms.showCurrentUser", "CALL dbms.showCurrentUser() YIELD username, roles, flags",
     lambda rows, w: rows == [{"username": "neo4j", "roles": [], "flags": []}] or pytest.fail(repr(rows)), {}, ""),
    ("dbms.queryJmx", "CALL dbms.queryJmx('*:*') YIELD name, description, attributes", _jmx, {}, ""),
    ("apoc.meta.data", "CALL apoc.meta.data() YIELD label, property, type, elementType, unique, index, existence",
     lambda rows, w: (sorted(r["property"] for r in rows if r["label"] == "R314Spot") == sorted(PROPS)
                      and [r for r in rows if r["label"] == "R314_LIKES"]
                      == [{"label": "R314_LIKES", "property": None, "type": "RELATIONSHIP",
                           "elementType": "relationship", "unique": False, "index": False, "existence": False}])
     or pytest.fail(repr(rows)), {}, ""),
    ("apoc.meta.schema", "CALL apoc.meta.schema() YIELD value",
     lambda rows, w: (_typed(rows[0]["value"]["R314Spot"]) == _typed(
         {"type": "node", "properties": {p: SPOT_PROP for p in PROPS}})
         and rows[0]["value"]["R314_LIKES"] == {"type": "relationship", "properties": {}}) or pytest.fail(repr(rows)),
     {}, ""),
    ("gds.pageRank.stream", "CALL gds.pageRank.stream('g') YIELD nodeId, score", _pagerank, {}, ""),
    ("gds.betweenness.stream", "CALL gds.betweenness.stream('g') YIELD nodeId, score", _betweenness("nodeId"), {},
     ""),
    ("gds.shortestPath.dijkstra.stream",
     "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'r314_a', targetNode: 'r314_b'}) "
     "YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs",
     lambda rows, w: _typed(rows) == _typed([{"index": 0, "sourceNode": "r314_a", "targetNode": "r314_b",
                                              "totalCost": 1.0, "nodeIds": ["r314_a", "r314_b"],
                                              "costs": [0.0, 1.0]}]) or pytest.fail(repr(rows)), {}, ""),
    ("gds.shortestPath.dijkstra",
     "CALL gds.shortestPath.dijkstra('g', {sourceNode: 'r314_a', targetNode: 'r314_b', "
     "relationshipWeightProperty: 'weight'}) YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs",
     lambda rows, w: _typed(rows) == _typed([{"index": 0, "sourceNode": "r314_a", "targetNode": "r314_b",
                                              "totalCost": 1.75, "nodeIds": ["r314_a", "r314_c", "r314_b"],
                                              "costs": [0.0, 0.5, 1.75]}]) or pytest.fail(repr(rows)), {}, ""),
    ("gds.nodeSimilarity.stream", "CALL gds.nodeSimilarity.stream('R314Spot', 'emb', $v, 2) YIELD node, score",
     _vector, {"params": True, "node_as_id": True}, ""),
    ("gds.louvain.stream", "CALL gds.louvain.stream('g') YIELD nodeId, communityId", None, {"raises": "ivg.leiden"},
     ""),
]

SURFACES = ["cypher-sql", "cypher-native", "rest", "graphql", "bolt"]


def test_every_procedure_has_a_live_case():
    assert sorted({c[0] for c in CASES}) == sorted(PROCEDURE_SIGNATURES)


def _inline(q):
    """$v as the literal list (REST, GraphQL and Bolt are sent the query alone)."""
    return q.replace("$v", "[" + ", ".join(repr(x) for x in E0) + "]")


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("proc, q, check, opts", [c[:4] for c in CASES],
                         ids=[c[0] + ("-" + c[4] if c[4] else "") for c in CASES])
def test_a_call_yields_typed_values(request, world, bolt, surface, proc, q, check, opts):
    if NO_IVF and proc == "ivg.ivf.search":
        pytest.skip("IVG_R314_NO_IVF=1: no IVF index was built")
    if surface in opts.get("error_dropped_on", ()):
        # 2026-11-07 (fixes83): the GraphQL resolver and the Bolt server do not read result.error, so a failed read
        # answers no rows and no error there; strict, so the fix of that shows here
        request.applymarker(pytest.mark.xfail(strict=True, reason=f"{surface} drops result.error: no rows, no error"))
    q = _inline(q)
    with ExitStackish(opts, world):
        if opts.get("raises"):
            with pytest.raises(Exception, match=opts["raises"]):
                _rows(world, bolt, surface, q)
            return
        rows = _rows(world, bolt, surface, q)
    if not opts.get("untyped"):
        _check_types(proc, rows, opts.get("renamed"), opts.get("node_as_id", False))
    check(rows, world)


class ExitStackish:
    """ivg.retrieve embeds its query text; the namespace has no embedder, so the query's vector is e0's."""

    def __init__(self, opts, world):
        self.patches = []
        if opts.get("embed"):
            self.patches = [patch.object(e, "embed_text", lambda text: list(E0)) for e in (world.sql, world.native)]

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
