"""Spec 252: with the adjacency index declared, SQLUser.CY_VLP_ENDS walks ^KG instead of one SQL statement per node.

The index keeps ^KG("out"/"in") equal to the rows in the same transaction as every write, which settles the
question the SF0.1 report left open (may a variable-length read trust ^KG?). The walk is the same trail walk
(edge-distinct paths, each end once with its least depth >= minh); only where the adjacency comes from changes.
Graph.KG.VarLength.Ends is that walk; the UDF calls it when the index is declared and the table is rdf_edges.

Expected answers come from a trail walk over the rows in Python. Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import collections
import json
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
N = {k: f"vk{T}_{k}" for k in "abcdefgxy"}
G2 = f"vk{T}_g2"
GM = f"vk{T}_gm"  # a multigraph: parallel edges


def _edges():
    a, b, c, d, e, f, g, x, y = (N[k] for k in "abcdefgxy")
    return [
        # default graph: triangle a-b-c, chain c-d-e, a self-loop on d, a second predicate b-f
        ("", a, "KNOWS", b), ("", b, "KNOWS", c), ("", c, "KNOWS", a), ("", c, "KNOWS", d),
        ("", d, "KNOWS", e), ("", d, "KNOWS", d), ("", b, "LIKES", f), ("", f, "KNOWS", g),
        # another graph: a-x
        (G2, a, "KNOWS", x),
    ]


@pytest.fixture(scope="module")
def world(iris_connection):
    from iris_vector_graph.schema import GraphSchema

    cur = iris_connection.cursor()
    status = GraphSchema.ensure_adjacency_index(cur)
    assert "skipped" not in status, status
    engine = IRISGraphEngine(iris_connection)
    for gname, s, p, o in _edges():
        engine.create_edge(s, p, o, graph=gname or None)
    engine.set_multigraph(GM, True)
    engine.create_node(N["a"], graph=GM)
    engine.create_node(N["y"], graph=GM)
    for ek in (0, 1):
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey) VALUES (?, 'KNOWS', ?, ?, ?)", [N["a"], N["y"], GM, ek]
        )
    iris_connection.commit()
    yield iris_connection
    ids = list(N.values())
    marks = ", ".join("?" * len(ids))
    cur.execute(f"DELETE FROM Graph_KG.rdf_edges WHERE s IN ({marks}) OR o_id IN ({marks})", ids + ids)
    cur.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id IN ({marks})", ids)
    iris_connection.commit()
    engine.set_multigraph(GM, False)


def _rows(conn):
    cur = conn.cursor()
    ids = list(N.values())
    marks = ", ".join("?" * len(ids))
    cur.execute(f"SELECT edge_id, graph_id, s, p, o_id FROM Graph_KG.rdf_edges WHERE s IN ({marks})", ids)
    return [(eid, (g or "").replace("\x00", ""), s, p, o) for eid, g, s, p, o in cur.fetchall()]


def _expected(rows, src, types, direction, minh, maxh, gall, gid):
    adj = {}
    for eid, g, s, p, o in rows:
        if not gall and g != gid:
            continue
        if types and p not in types:
            continue
        if direction in ("out", "both"):
            adj.setdefault(s, []).append((eid, o))
        if direction == "in" or (direction == "both" and s != o):
            adj.setdefault(o, []).append((eid, s))
    best = {}

    def walk(cur, dep, used):
        if dep >= minh and (cur not in best or dep < best[cur]):
            best[cur] = dep
        if dep < maxh:
            for eid, nb in adj.get(cur, ()):
                if eid not in used:
                    walk(nb, dep + 1, used | {eid})

    walk(src, 0, frozenset())
    return best


def _udf(conn, src, types, direction, minh, maxh, gall, gid):
    cur = conn.cursor()
    cur.execute(
        "SELECT SQLUser.CY_VLP_ENDS(?, ?, ?, ?, ?, ?, ?, 'Graph_KG.rdf_edges')",
        [src, json.dumps(types), direction, minh, maxh, gall, gid if not gall else "-"],
    )
    return {o["t"]: int(o["l"]) for o in json.loads(cur.fetchone()[0])}


def _direct(conn, src, types, direction, minh, maxh, gall, gid):
    import iris

    raw = iris.createIRIS(conn).classMethodValue(
        "Graph.KG.VarLength", "Ends", src, json.dumps(types), direction, minh, maxh, gall, gid if not gall else "-"
    )
    return {o["t"]: int(o["l"]) for o in json.loads(raw)}


CASES = [
    (src, types, direction, minh, maxh, gall, gid)
    for src in ("a", "d")
    for types in ([], ["KNOWS"])
    for direction in ("out", "in", "both")
    for minh, maxh in ((0, 2), (1, 1), (1, 3), (2, 2), (2, 3))
    for gall, gid in ((1, ""), (0, ""), (0, "g2"), (0, "gm"))
]


@pytest.mark.parametrize("src,types,direction,minh,maxh,gall,gid", CASES)
def test_ends_over_kg_equal_the_trail_walk_over_the_rows(world, src, types, direction, minh, maxh, gall, gid):
    gid = {"": "", "g2": G2, "gm": GM}[gid]
    want = _expected(_rows(world), N[src], types, direction, minh, maxh, gall, gid)
    assert _direct(world, N[src], types, direction, minh, maxh, gall, gid) == want
    assert _udf(world, N[src], types, direction, minh, maxh, gall, gid) == want


GR = f"vk{T}_rand"  # plain graph, random edges
GRM = f"vk{T}_randm"  # multigraph, random edges with parallels


@pytest.fixture(scope="module")
def random_world(world):
    import random

    conn = world
    engine = IRISGraphEngine(conn)
    engine.set_multigraph(GRM, True)
    rng = random.Random(252)
    nodes = [f"vk{T}_r{i}" for i in range(9)]
    cur = conn.cursor()
    for g in (GR, GRM):
        for n in nodes:
            engine.create_node(n, graph=g)
        seen = collections.Counter()
        for _ in range(26):
            s, o = rng.choice(nodes), rng.choice(nodes)  # self-loops included
            p = rng.choice(("KNOWS", "KNOWS", "LIKES"))
            if g == GR and (s, p, o) in seen:
                continue
            ek = seen[(s, p, o)] if g == GRM else 0
            seen[(s, p, o)] += 1
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey) VALUES (?, ?, ?, ?, ?)", [s, p, o, g, ek]
            )
    conn.commit()
    yield conn, nodes
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE graph_id IN (?, ?)", [GR, GRM])
    cur.execute("DELETE FROM Graph_KG.nodes WHERE graph_id IN (?, ?)", [GR, GRM])
    conn.commit()
    engine.set_multigraph(GRM, False)


def _graph_rows(conn, g):
    cur = conn.cursor()
    cur.execute("SELECT edge_id, graph_id, s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", [g])
    return [(eid, (gg or "").replace("\x00", ""), s, p, o) for eid, gg, s, p, o in cur.fetchall()]


@pytest.mark.parametrize("g", ["plain", "multi"])
@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_ends_on_random_graphs(random_world, g, direction):
    conn, nodes = random_world
    gname = {"plain": GR, "multi": GRM}[g]
    rows = _graph_rows(conn, gname)
    for src in nodes:
        for types in ([], ["KNOWS"]):
            for minh, maxh in ((0, 3), (1, 1), (1, 2), (1, 3), (1, 4), (2, 2), (2, 3)):
                want = _expected(rows, src, types, direction, minh, maxh, 0, gname)
                got = _direct(conn, src, types, direction, minh, maxh, 0, gname)
                assert got == want, (src, types, minh, maxh)


def _ordered(conn, src, types, direction, minh, maxh, gall, gid):
    """Ends's answer as it lists it: [(end, depth), ...] in its order."""
    import iris

    raw = iris.createIRIS(conn).classMethodValue(
        "Graph.KG.VarLength", "Ends", src, json.dumps(types), direction, minh, maxh, gall, gid if not gall else "-"
    )
    return [(o["t"], int(o["l"])) for o in json.loads(raw)]


def _set_ppg(conn, code):
    import iris

    iris.createIRIS(conn).classMethodVoid("User.Exec", "Run", code)


@pytest.mark.parametrize("g", ["plain", "multi"])
@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_ends_with_the_last_level_split_across_workers(random_world, g, direction):
    """The last level of the breadth-first walk split across worker processes (^||IVG.VLParallelMin = 0 forces it; the
    default needs 512 frontier nodes) gives the same ends as the walk in one process, and the split ran."""
    import iris

    conn, nodes = random_world
    gname = {"plain": GR, "multi": GRM}[g]
    rows = _graph_rows(conn, gname)
    cases = [(src, types, minh, maxh, gall) for src in nodes for types in ((), ("KNOWS",)) for minh, maxh in ((0, 3), (1, 2), (1, 3), (1, 4)) for gall in (0, 1)]
    # the serial walk's ends, in its order, for every case
    _set_ppg(conn, "Set ^||IVG.VLParallelMin=-1")
    serial = {c: _ordered(conn, c[0], list(c[1]), direction, c[2], c[3], c[4], gname) for c in cases}
    _set_ppg(conn, "Set ^||IVG.VLParallelMin=0 Kill ^||IVG.VLParallelRuns")
    try:
        for src, types, minh, maxh, gall in cases:
            if not gall:
                want = _expected(rows, src, list(types), direction, minh, maxh, 0, gname)
                assert _direct(conn, src, list(types), direction, minh, maxh, 0, gname) == want, (src, types, minh, maxh)
            # the same ends in the same order as one process, every graph's too
            assert _ordered(conn, src, list(types), direction, minh, maxh, gall, gname) == serial[(src, types, minh, maxh, gall)]
        _set_ppg(conn, "Set ^IVGTest252Runs=+$Get(^||IVG.VLParallelRuns)")
        native = iris.createIRIS(conn)
        runs = int(native.get("IVGTest252Runs") or 0)
        native.kill("IVGTest252Runs")
    finally:
        _set_ppg(conn, "Kill ^||IVG.VLParallelMin, ^||IVG.VLParallelRuns")
    assert runs > 0, "the last level never ran in workers"
