"""4.6.0 R246: an unlabelled `{node_id: ...}` anchor and `RETURN x.node_id` run natively, and compile to `IVGC.*`.

Since R204 the guides listed both as reads the native executor declines (walkthrough 1 W7's hop reads all went to the
SQL path, and none compiled). Each case here must be answered by the native path (the engine's `_native_answers`
moves), give the answer worked out by hand below, and give the SQL path's answer (an engine with `native_cypher=False`),
in both compile modes, `job` and `sync` (`Graph.KG.Cypher.Gen.Mode`, per process). Run the file again with
`IVG_CYPHER_SHADOW=1` and every native answer is also held to the SQL path's inside the engine (`NativeMismatch`).
A repeated shape of each kind (family A, family B, an aggregate, two stages) compiles to an `IVGC.*` class, in both
modes.

The graph (P{T} a label, K{T} a relationship type; ids made unique per run):

    default graph   a:P "A" -K-> b:P "B", a -K-> c:P "C", b -K-> c, c -K-> d "D" (no label),
                    a -K-> n9:P "N9", a -K-> n10:P "N10"; z "Z": no label, no relationship
    graph G         a:P "GA" -K-> b:P "GB", a -K-> e:P "GE", b -K-> e; o "GO": no label, no relationship

`id:k` in an answer below is node k's id. The SQL path sorts an id by CY_SORT_KEY(node_id, 'x'): strings before
numbers, n9 before n10.

A read with no USE GRAPH reads every graph, so `a` and `b` are two nodes each there (default and G). Two-stage reads are
scoped to one graph or start from a node only one graph holds: unscoped, the SQL path's first stage joins every graph's
rows that share an id (test_an_unscoped_two_stage_read_over_shared_ids, below).

    IVG_NAMESPACE=IVG460FXV IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/e2e/test_460_r246_native_node_id_e2e.py
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.helpers.dev_classes import EXEC, ensure_dev_classes

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G = f"r246{T}_g"
P, K = f"R246P{T}", f"R246K{T}"
#: a numeric prefix for the two ids that read as numbers (n9 < n10 as numbers, n10 < n9 as strings)
NUM = str(random.randint(100000, 999999))
#: how long a background build may take before a test calls it stuck
BUILD_WAIT_S = 30
MODES = ("job", "sync")


def n(k: str) -> str:
    """Node k's id: the same in every graph that holds it."""
    if k in ("n9", "n10"):
        return NUM + k[1:]
    return f"r246{T}_{k}"


DEFAULT_NODES = {
    "a": ([P], "A"),
    "b": ([P], "B"),
    "c": ([P], "C"),
    "d": ([], "D"),
    "z": ([], "Z"),
    "n9": ([P], "N9"),
    "n10": ([P], "N10"),
}
DEFAULT_EDGES = [("a", "b"), ("a", "c"), ("b", "c"), ("c", "d"), ("a", "n9"), ("a", "n10")]
G_NODES = {"a": ([P], "GA"), "b": ([P], "GB"), "e": ([P], "GE"), "o": ([], "GO")}
G_EDGES = [("a", "b"), ("a", "e"), ("b", "e")]


def _q(cypher: str) -> str:
    return cypher.replace("{P}", P).replace("{K}", K).replace("{G}", G)


#: (name, query, params, rows worked out by hand, ordered). Unordered answers are compared sorted.
ANCHOR_CASES = [
    ("A: an unlabelled id alone, every graph", "MATCH (x {node_id: $id}) RETURN x.name", {"id": n("a")}, [["A"], ["GA"]], False),
    ("A: a node with no label and no relationship", "MATCH (x {node_id: $id}) RETURN x.name", {"id": n("z")}, [["Z"]], False),
    ("A: one graph's node with no label or relationship", "USE GRAPH '{G}' MATCH (x {node_id: $id}) RETURN x.name", {"id": n("o")}, [["GO"]], False),
    ("A: a node another graph holds", "USE GRAPH '{G}' MATCH (x {node_id: $id}) RETURN x.name", {"id": n("z")}, [], False),
    ("A: an id that names no node", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.name", {"id": n("nobody")}, [], False),
    ("A: one hop from an unlabelled id", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.name ORDER BY y.name", {"id": n("a")},
     [["B"], ["C"], ["GB"], ["GE"], ["N10"], ["N9"]], True),
    ("A: one hop, one graph", "USE GRAPH '{G}' MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.name", {"id": n("a")}, [["GB"], ["GE"]], False),
    ("A: two hops from an unlabelled id", "MATCH (x {node_id: $id})-[:{K}]->()-[:{K}]->(w) RETURN w.name", {"id": n("a")},
     [["C"], ["D"], ["GE"]], False),
    ("A: incoming to an unlabelled id", "MATCH (x {node_id: $id})<-[:{K}]-(y) RETURN y.name ORDER BY y.name", {"id": n("c")}, [["A"], ["B"]], True),
    ("A: an unlabelled id on the far node", "MATCH (x:{P} {name: 'B'})-[:{K}]->(y {node_id: $id}) RETURN x.name", {"id": n("c")}, [["B"]], False),
    ("A: an unlabelled id and a property", "MATCH (x {node_id: $id, name: 'GA'})-[:{K}]->(y) RETURN y.name", {"id": n("a")},
     [["GB"], ["GE"]], False),
    ("A: an unlabelled {id: ...}", "MATCH (x {id: $id})-[:{K}]->(y)-[:{K}]->(w) RETURN w.name", {"id": n("b")}, [["D"]], False),
    ("B: the reach of an unlabelled id", "MATCH (x {node_id: $id})-[:{K}*1..2]->(y) RETURN DISTINCT y.name ORDER BY y.name", {"id": n("a")},
     [["B"], ["C"], ["D"], ["GB"], ["GE"], ["N10"], ["N9"]], True),
    ("B: two hops exactly", "MATCH (x {node_id: $id})-[:{K}*2..2]->(y) RETURN DISTINCT y.name", {"id": n("a")}, [["C"], ["D"], ["GE"]], False),
    ("B: distinct ends counted", "MATCH (x {node_id: $id})-[:{K}*1..2]->(y) RETURN count(DISTINCT y) AS n", {"id": n("b")}, [[3]], True),
    ("agg: matches of an unlabelled id", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN count(*) AS n", {"id": n("a")}, [[6]], True),
    ("agg: grouped by the far node", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.name AS name, count(*) AS n ORDER BY name", {"id": n("a")},
     [["B", 1], ["C", 1], ["GB", 1], ["GE", 1], ["N10", 1], ["N9", 1]], True),
    ("agg: a node with no relationship", "MATCH (x {node_id: $id}) RETURN count(*) AS n", {"id": n("z")}, [[1]], True),
    ("two stages: from an unlabelled id, one graph",
     "USE GRAPH '{G}' MATCH (x {node_id: $id})-[:{K}]->(y)-[:{K}]->(w) WITH w.name AS name, count(*) AS c RETURN name, c",
     {"id": n("a")}, [["GE", 1]], True),
    ("two stages: from an id one graph holds",
     "MATCH (x {node_id: $id})-[:{K}]->(y) WITH y.name AS name, count(*) AS c RETURN c, count(*) AS n", {"id": n("c")}, [[1, 1]], True),
    ("two stages: a label scan, one graph",
     "USE GRAPH '{G}' MATCH (x:{P})-[:{K}]->(y) WITH y.name AS name, count(*) AS c RETURN c, count(*) AS n ORDER BY c",
     {}, [[1, 1], [2, 1]], True),
]

#: `x.node_id`: the identity, in a projection, an ORDER BY, a grouping key and a first stage's key
ID_CASES = [
    ("A: the far node's node_id, ordered", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.node_id ORDER BY y.node_id", {"id": n("a")},
     [["id:b"], ["id:b"], ["id:c"], ["id:e"], ["id:n9"], ["id:n10"]], True),
    ("A: node_id and a property, descending, limited",
     "MATCH (x:{P} {name: 'A'})-[:{K}]->(y) RETURN y.node_id, y.name ORDER BY y.node_id DESC LIMIT 2", {},
     [["id:n10", "N10"], ["id:n9", "N9"]], True),
    ("A: the anchor's own node_id", "MATCH (x {node_id: $id}) RETURN x.node_id, x.name", {"id": n("z")}, [["id:z", "Z"]], False),
    ("A: node_id, one graph", "USE GRAPH '{G}' MATCH (x {node_id: $id})-[:{K}]->(y) RETURN y.node_id AS id", {"id": n("a")},
     [["id:b"], ["id:e"]], False),
    ("A: DISTINCT node_id over a label", "MATCH (x:{P})-[:{K}]->(y) RETURN DISTINCT y.node_id ORDER BY y.node_id", {},
     [["id:b"], ["id:c"], ["id:d"], ["id:e"], ["id:n9"], ["id:n10"]], True),
    ("A: ordered by node_id, not returned", "MATCH (x:{P} {name: 'A'})-[:{K}]->(y) RETURN y.name ORDER BY y.node_id", {},
     [["B"], ["C"], ["N9"], ["N10"]], True),
    ("B: node_id over a variable-length hop", "MATCH (x {node_id: $id})-[:{K}*1..2]->(y) RETURN DISTINCT y.node_id ORDER BY y.node_id",
     {"id": n("b")}, [["id:c"], ["id:d"], ["id:e"]], True),
    ("agg: grouped by node_id", "MATCH (x:{P})-[:{K}]->(y) RETURN x.node_id AS id, count(*) AS n ORDER BY id", {},
     [["id:a", 6], ["id:b", 2], ["id:c", 1]], True),
    ("agg: a label scan grouped by node_id", "MATCH (x:{P}) RETURN x.node_id AS id, count(*) AS n ORDER BY id", {},
     [["id:a", 2], ["id:b", 2], ["id:c", 1], ["id:e", 1], ["id:n9", 1], ["id:n10", 1]], True),
    ("agg: the most reached by count, then node_id",
     "MATCH (x:{P})-[:{K}]->(y) RETURN y.node_id AS id, count(*) AS n ORDER BY n DESC, id LIMIT 2", {},
     [["id:b", 2], ["id:c", 2]], True),
    ("agg: an unlabelled node's own node_id", "MATCH (x {node_id: $id}) RETURN x.node_id AS id, count(*) AS n", {"id": n("z")},
     [["id:z", 1]], True),
    ("two stages: node_id keys counted, one graph",
     "USE GRAPH '{G}' MATCH (x:{P})-[:{K}]->(y) WITH y.node_id AS id, count(*) AS n RETURN n, count(*) AS c ORDER BY n", {},
     [[1, 1], [2, 1]], True),
    ("two stages: node_id returned, one graph",
     "USE GRAPH '{G}' MATCH (x:{P})-[:{K}]->(y) WITH y.node_id AS id, count(*) AS n WHERE n > 1 RETURN id, n ORDER BY id", {},
     [["id:e", 2]], True),
    ("two stages: the anchor's node_id", "MATCH (x {node_id: $id})-[:{K}]->(y) WITH x.node_id AS id, count(*) AS n RETURN id, n",
     {"id": n("c")}, [["id:c", 1]], True),
]

CASES = ANCHOR_CASES + ID_CASES

#: (kind, query, params, rows): a repeated shape of each kind compiles (each has a hop: a plan with none is interpreted)
COMPILE = [
    ("A", "MATCH (x {node_id: $id})-[:{K}]->()-[:{K}]->(w) RETURN w.name ORDER BY w.name", {"id": n("a")}, [["C"], ["D"], ["GE"]]),
    ("B", "MATCH (x {node_id: $id})-[:{K}*1..2]->(y) RETURN DISTINCT y.name ORDER BY y.name", {"id": n("b")}, [["C"], ["D"], ["GE"]]),
    ("aggregate", "MATCH (x {node_id: $id})-[:{K}]->(y) RETURN count(*) AS n", {"id": n("a")}, [[6]]),
    ("two stages", "USE GRAPH '{G}' MATCH (x {node_id: $id})-[:{K}]->(y) WITH y.name AS name, count(*) AS c RETURN c, count(*) AS n",
     {"id": n("a")}, [[1, 2]]),
    ("A, node_id", "MATCH (x:{P} {name: 'A'})-[:{K}]->(y) RETURN y.node_id ORDER BY y.node_id", {},
     [["id:b"], ["id:c"], ["id:n9"], ["id:n10"]]),
    ("B, node_id", "MATCH (x {node_id: $id})-[:{K}*1..2]->(y) RETURN DISTINCT y.node_id ORDER BY y.node_id", {"id": n("b")},
     [["id:c"], ["id:d"], ["id:e"]]),
    ("aggregate, node_id", "MATCH (x:{P})-[:{K}]->(y) RETURN x.node_id AS id, count(*) AS n ORDER BY id", {},
     [["id:a", 6], ["id:b", 2], ["id:c", 1]]),
    ("two stages, node_id",
     "USE GRAPH '{G}' MATCH (x:{P})-[:{K}]->(y) WITH y.node_id AS id, count(*) AS n WHERE n > 1 RETURN id, n ORDER BY id", {},
     [["id:e", 2]]),
]


def _native(conn):
    return iris.createIRIS(conn)


def _run(conn, code: str) -> None:
    ensure_dev_classes(_native(conn), EXEC).classMethodVoid("User.Exec", "Run", code)


def _compiled_runs(conn) -> int:
    """Walks run compiled in the engine's process since the last call."""
    _run(conn, f"Set ^R246c{T}=+$Get(^||IVG.CypherCompiledRuns) Kill ^||IVG.CypherCompiledRuns")
    native = _native(conn)
    runs = int(native.get(f"R246c{T}") or 0)
    native.kill(f"R246c{T}")
    return runs


def _mode(conn, mode: str) -> str:
    return str(_native(conn).classMethodValue("Graph.KG.Cypher.Gen", "Mode", mode))


def _entries(conn) -> list:
    return json.loads(str(_native(conn).classMethodValue("Graph.KG.Cypher.Gen", "Entries")) or "[]")


def _settled(conn) -> list:
    """The entries once no build is pending (a job started by an earlier read could otherwise write its entry back
    after DropAll)."""
    deadline = time.monotonic() + BUILD_WAIT_S
    while True:
        entries = _entries(conn)
        if all(e["status"] != "pending" for e in entries):
            return entries
        assert time.monotonic() < deadline, f"a build was still pending after {BUILD_WAIT_S} s: {entries}"
        time.sleep(0.5)


def _class_exists(conn, name: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = ?", [name])
        return int(cur.fetchone()[0]) == 1
    finally:
        cur.close()


@pytest.fixture(scope="module")
def engines(iris_connection):
    nat = IRISGraphEngine(iris_connection, native_cypher=True)
    sql = IRISGraphEngine(iris_connection, native_cypher=False)
    for k, (labels, name) in DEFAULT_NODES.items():
        nat.create_node(n(k), labels=labels or None, properties={"name": name})
    for x, y in DEFAULT_EDGES:
        nat.create_edge(n(x), K, n(y))
    for k, (labels, name) in G_NODES.items():
        nat.create_node(n(k), labels=labels or None, properties={"name": name}, graph=G)
    for x, y in G_EDGES:
        nat.create_edge(n(x), K, n(y), graph=G)
    nat._native_privileges_cached = True
    # no per-shape route (spec 253's NativeRoute): it sends a small shape to SQL after a few native runs, where SQL
    # measures faster, and these tests hold the native path's answers
    nat.native_route_ms = 0
    try:
        yield nat, sql
    finally:
        _mode(iris_connection, "")
        nat.erase_graph(G)
        for k in DEFAULT_NODES:
            nat.delete_node(n(k))


def _rows(result) -> list:
    assert getattr(result, "error", None) is None, result.error
    return [list(r) for r in result.rows]


def _ids(rows: list) -> list:
    return [[n(v[3:]) if isinstance(v, str) and v.startswith("id:") else v for v in row] for row in rows]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,cypher,params,expected,ordered", CASES, ids=[c[0] for c in CASES])
def test_native_answers_as_by_hand_and_as_sql(engines, mode, name, cypher, params, expected, ordered):
    nat, sql = engines
    _mode(nat.conn, mode)
    query = _q(cypher)
    before = nat.__dict__.get("_native_answers", 0)
    got = _rows(nat.execute_cypher(query, params))
    assert nat.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
    want = _ids(expected)
    sql_rows = _rows(sql.execute_cypher(query, params))
    if not ordered:
        got, want, sql_rows = sorted(got, key=repr), sorted(want, key=repr), sorted(sql_rows, key=repr)
    assert got == want
    assert sql_rows == want, "the SQL path answers otherwise"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind,cypher,params,expected", COMPILE, ids=[c[0] for c in COMPILE])
def test_a_repeated_shape_compiles(engines, mode, kind, cypher, params, expected):
    nat, _ = engines
    conn = nat.conn
    query = _q(cypher)
    _settled(conn)
    _native(conn).classMethodValue("Graph.KG.Cypher.Gen", "DropAll")
    _mode(conn, mode)
    _compiled_runs(conn)
    assert _rows(nat.execute_cypher(query, params)) == _ids(expected)
    first = _compiled_runs(conn)
    if mode == "job":
        # the first run is interpreted while a background job builds the class
        assert first == 0
    else:
        assert first >= 1, f"sync: the first run did not walk compiled: {_entries(conn)}"
    entries = _settled(conn)
    assert len(entries) == 1 and entries[0]["status"] == "ok", entries
    assert entries[0]["class"].startswith("IVGC.P") and _class_exists(conn, entries[0]["class"]), entries
    before = nat.__dict__.get("_native_answers", 0)
    assert _rows(nat.execute_cypher(query, params)) == _ids(expected)
    assert nat.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
    assert _compiled_runs(conn) >= 1, "the repeated shape did not walk compiled"


@pytest.mark.xfail(
    strict=True,
    reason="SQL path: an unscoped WITH stage joins every graph's rows that share a node id (B counted 8 times, not once)",
)
def test_an_unscoped_two_stage_read_over_shared_ids(engines):
    """Found by this file: `a` and `b` are in both graphs, and the SQL path's first stage joins the edges, far nodes
    and properties of every graph to each `a` (a one-stage read joins them graph by graph). Native answers it right."""
    nat, sql = engines
    query = _q("MATCH (x {node_id: $id})-[:{K}]->(y) WITH y.name AS name, count(*) AS c RETURN name, c ORDER BY name")
    want = [["B", 1], ["C", 1], ["GB", 1], ["GE", 1], ["N10", 1], ["N9", 1]]
    assert _rows(nat.execute_cypher(query, {"id": n("a")})) == want
    assert _rows(sql.execute_cypher(query, {"id": n("a")})) == want
