"""Spec 257: a native plan's walk compiled to a routine of its own answers as the interpreter does.

Graph.KG.Cypher.Gen writes one class per plan shape (IVGC.P<n>, extending Graph.KG.Cypher.Exec): a `For` loop per
fixed hop with its types, direction and node checks written in, and the grouping inline. Each case runs three ways: the
interpreter (`Gen.Mode("off")`), the compiled walk (`"sync"`), and, through shadow mode, the SQL path. The compiled
answer must equal the interpreter's row for row (the walk meets matches in the same order, so even ties at a LIMIT
agree), and a covered shape must have run compiled (^||IVG.CypherCompiledRuns).

Run against `IVG_NAMESPACE=TCKJ IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .test_253_family_b_e2e import CASES as FAMILY_B

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G, GM = f"cp{T}_g", f"cp{T}_m"
P, POST, PL = f"CpP{T}", f"CpM{T}", f"CpL{T}"
TAG = {G: "g", GM: "m"}
PART = {"pl1": "c1", "pl2": "c1", "pl3": "c2"}  # place -> the place it is part of


def n(k, g=None):
    """Node k's id in graph g: each graph its own ids, since a query with no USE GRAPH reads every graph, and
    count(DISTINCT) over one id in two graphs is a known difference between the paths (KNOWN_ISSUES)."""
    return f"cp{T}{TAG.get(g, '')}_{k}"


def _native(conn):
    return iris.createIRIS(conn)


def _run(conn, code):
    _native(conn).classMethodVoid("User.Exec", "Run", code)


def _compiled_runs(conn) -> int:
    """Walks run compiled in this process since the last call."""
    _run(conn, f"Set ^IVGCp{T}=+$Get(^||IVG.CypherCompiledRuns) Kill ^||IVG.CypherCompiledRuns")
    native = _native(conn)
    runs = int(native.get(f"IVGCp{T}") or 0)
    native.kill(f"IVGCp{T}")
    return runs


def _mode(conn, mode):
    return _native(conn).classMethodValue("Graph.KG.Cypher.Gen", "Mode", mode)


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    people = {"a": ("Ann", "pl1"), "b": ("Bo", "pl1"), "c": ("Ann", "pl2"), "d": ("Di", "pl2"), "e": ("Ann", "pl3"), "f": ("Fe", None)}
    for g in (None, G, GM):
        for k, (first, _) in people.items():
            props = {"sid": n(k, g), "firstName": first, "age": str(20 + ord(k) - 96)}
            if k != "f":
                props["lastName"] = f"L{k}"
            e.create_node(n(k, g), labels=[P, "CpStar"] if k in "ce" else [P], properties=props, graph=g)
        for k, (name, part) in {"pl1": ("X", "c1"), "pl2": ("Y", "c1"), "pl3": ("Z", "c2"), "c1": ("Land", None), "c2": ("Other", None)}.items():
            e.create_node(n(k, g), labels=[PL], properties={"name": name}, graph=g)
        for k, part in PART.items():
            e.create_edge(n(k, g), "CP_PART", n(part, g), graph=g)
        for k, (_, at) in people.items():
            if at:
                e.create_edge(n(k, g), "CP_AT", n(at, g), graph=g)
        # a-b-c triangle, c-d-e chain, e-e self-loop, a-f; a second type between a and c
        for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "e"), ("a", "f")):
            e.create_edge(n(x, g), "CP_KNOWS", n(y, g), graph=g)
        e.create_edge(n("a", g), "CP_LIKES", n("c", g), graph=g)
        for k, (by, title) in {"p1": ("b", "Tx"), "p2": ("c", "Tx"), "p3": ("c", "Ty"), "p4": ("d", "Ty"), "p5": ("f", None)}.items():
            props = {"sid": n(k, g)}
            if title:
                props["title"] = title
            e.create_node(n(k, g), labels=[POST], properties=props, graph=g)
            e.create_edge(n(k, g), "CP_BY", n(by, g), graph=g)
    e.set_multigraph(GM, True)
    # parallel relationships: in multigraph mode each CREATE is another relationship
    for _ in range(2):
        r = e.execute_cypher(
            f"USE GRAPH '{GM}' MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) CREATE (a)-[:CP_KNOWS]->(b)",
            {"a": n("a", GM), "b": n("b", GM)},
        )
        assert getattr(r, "error", None) is None, r.error
    # the degree cache warmed for the named graphs only: the default graph's tallies walk
    for g in (G, GM):
        _native(iris_connection).classMethodValue("Graph.KG.Adj", "WarmAll", g)
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    _mode(iris_connection, "")
    for g in (G, GM):
        e.erase_graph(g)
    e.set_multigraph(GM, False)
    for k in [*people, "pl1", "pl2", "pl3", "c1", "c2", "p1", "p2", "p3", "p4", "p5"]:
        e.delete_node(n(k))


def _sub(q):
    return q.replace("{P}", P).replace("{POST}", POST).replace("{PL}", PL)


OWN = [
    (
        "BI18 shape: mutual friends, count(DISTINCT) by a property, ordered, limited",
        "MATCH (p:{P})-[:CP_KNOWS]-(m:{P})-[:CP_KNOWS]-(f:{P}) WHERE p.sid = $id AND f.sid <> $id "
        "RETURN f.sid AS person, count(DISTINCT m) AS mutual ORDER BY mutual DESC, person LIMIT 2",
        {"id": "a"},
    ),
    (
        "BI04 shape: three incoming hops from a named place",
        "MATCH (c:{PL})<-[:CP_PART]-(x:{PL})<-[:CP_AT]-(p:{P})<-[:CP_BY]-(m:{POST}) WHERE c.name = 'Land' "
        "RETURN m.title AS title, count(DISTINCT p) AS n ORDER BY n DESC, title LIMIT 10",
        {},
    ),
    (
        "BI14 shape: five hops, count(*), a <> on the far end",
        "MATCH (c1:{PL})<-[:CP_PART]-(x:{PL})<-[:CP_AT]-(a:{P})-[:CP_KNOWS]-(b:{P})-[:CP_AT]->(y:{PL})-[:CP_PART]->(c2:{PL}) "
        "WHERE c1.name = 'Land' AND c2.name <> 'Land' RETURN c2.name AS other, count(*) AS n ORDER BY n DESC, other LIMIT 10",
        {},
    ),
    (
        "IC2 shape: an ordered projection over two hops",
        "MATCH (p:{P})-[:CP_KNOWS]-(q:{P})<-[:CP_BY]-(m:{POST}) WHERE p.sid = $id "
        "RETURN q.sid, m.sid ORDER BY m.sid DESC, q.sid LIMIT 3",
        {"id": "c"},
    ),
    ("an untyped hop", "MATCH (p:{P})-[]->(x) WHERE p.sid = $id RETURN x.sid AS s, count(*) AS n ORDER BY s", {"id": "a"}),
    (
        "two types on one hop",
        "MATCH (p:{P})-[:CP_KNOWS|CP_LIKES]->(x:{P}) WHERE p.sid = $id RETURN x.sid ORDER BY x.sid",
        {"id": "a"},
    ),
    (
        "both directions through a self-loop",
        "MATCH (p:{P})-[:CP_KNOWS]-(x) WHERE p.sid = $id RETURN x.sid ORDER BY x.sid",
        {"id": "e"},
    ),
    (
        "an unordered LIMIT ends the walk",
        "MATCH (p:{P})-[:CP_KNOWS]-(x:{P})-[:CP_KNOWS]-(y:{P}) RETURN y.sid LIMIT 3",
        {},
    ),
    (
        "DISTINCT over two hops",
        "MATCH (p:{P})-[:CP_KNOWS]-(x:{P})-[:CP_KNOWS]-(y:{P}) WHERE p.sid = $id RETURN DISTINCT y.firstName ORDER BY y.firstName",
        {"id": "a"},
    ),
    (
        "an id anchor",
        "MATCH (p:{P} {id: $id})-[:CP_KNOWS]->(x)-[:CP_KNOWS]->(y) RETURN y.sid ORDER BY y.sid",
        {"id": "a"},
    ),
    (
        "comparisons on the far node, then a grouping key it read",
        "MATCH (p:{P})-[:CP_KNOWS]-(x:{P}) WHERE x.age >= '22' AND x.age < '25' RETURN x.age AS age, count(p) AS n ORDER BY age",
        {},
    ),
    (
        "a property equality on the far node",
        "MATCH (p:{P})-[:CP_KNOWS]-(x:{P} {firstName: 'Ann'}) RETURN p.sid AS s, count(x) AS n ORDER BY s",
        {},
    ),
    (
        "a cross filter on the second hop",
        "MATCH (p:{P})-[:CP_KNOWS]-(x:{P})-[:CP_KNOWS]-(y:{P}) WHERE p.age < y.age RETURN p.sid, y.sid ORDER BY p.sid, y.sid",
        {},
    ),
    (
        "a missing grouping key",
        "MATCH (m:{POST})-[:CP_BY]->(p:{P})-[:CP_KNOWS]-(f:{P}) RETURN m.title AS t, count(DISTINCT f) AS n ORDER BY t",
        {},
    ),
    ("a keyless count over two hops", "MATCH (p:{P})-[:CP_KNOWS]-(x)-[:CP_KNOWS]-(y) RETURN count(*) AS n", {}),
    (
        "a tallied last hop, incoming (walked)",
        "MATCH (p:{P})<-[:CP_BY]-(m:{POST}) RETURN p.sid AS s, count(m) AS n ORDER BY s",
        {},
    ),
    (
        "a tallied last hop, outgoing and unchecked (the degree cache)",
        "MATCH (x:{PL})<-[:CP_AT]-(p:{P})-[r]->(m) RETURN x.name AS name, count(r) AS n ORDER BY name",
        {},
    ),
    (
        "a cycle closed on the anchor",
        "MATCH (a:{P})-[:CP_KNOWS]->(b)-[:CP_KNOWS]->(c)-[:CP_KNOWS]->(a) RETURN a.sid, b.sid, c.sid ORDER BY a.sid",
        {},
    ),
]

SCOPES = [("default", None, ""), ("named", G, f"USE GRAPH '{G}' "), ("multigraph", GM, f"USE GRAPH '{GM}' ")]


def _cases():
    out = []
    for name, cypher, params in OWN:
        for scope, g, prefix in SCOPES:
            out.append((f"{name} [{scope}]", prefix + _sub(cypher), {k: (n(v, g) if k == "id" else v) for k, v in params.items()}))
    return out


# the SQL path's statement for these does not finish preparing (the optimizer, %qaoinor, ran 15 billion lines on the
# five-hop chain): they are held to the interpreter only, which family A/B's tests hold to the SQL path
NO_SQL = ("BI14 shape",)


def _check(engine, cypher, params, covered=True, sql=True):
    conn = engine.conn
    engine.cypher_shadow = sql
    _mode(conn, "off")
    _compiled_runs(conn)
    off = engine.execute_cypher(cypher, params)
    assert getattr(off, "error", None) is None, off.error
    assert _compiled_runs(conn) == 0
    _mode(conn, "sync")
    on = engine.execute_cypher(cypher, params)
    assert getattr(on, "error", None) is None, on.error
    runs = _compiled_runs(conn)
    if covered:
        assert runs >= 1, "the compiled walk did not run"
    engine.cypher_shadow = True
    assert [list(map(str, r)) for r in on.rows] == [list(map(str, r)) for r in off.rows]


@pytest.mark.parametrize("name,cypher,params", _cases(), ids=[c[0] for c in _cases()])
def test_compiled_equals_interpreted_and_sql(engine, name, cypher, params):
    before = engine.__dict__.get("_native_answers", 0)
    _check(engine, cypher, params, sql=not name.startswith(NO_SQL))
    assert engine.__dict__.get("_native_answers", 0) == before + 2, "the native path did not answer"


@pytest.fixture(scope="module")
def family_b_engine(iris_connection):
    from . import test_253_family_b_e2e as fb

    e = IRISGraphEngine(iris_connection, native_cypher=True)
    fb.load(e)
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e, fb.n
    e.cypher_shadow = False
    _mode(iris_connection, "")
    fb.unload(e)


@pytest.mark.parametrize("name,cypher,params", FAMILY_B, ids=[c[0] for c in FAMILY_B])
def test_family_b_compiled_equals_interpreted(family_b_engine, name, cypher, params):
    e, fbn = family_b_engine
    params = {k: (fbn(v) if k == "id" else v) for k, v in params.items()}
    # every fixed-length chain of one or more hops is compiled; a variable-length hop or no hop is not
    _check(e, cypher, params, covered="*" not in cypher and "-[" in cypher)


def test_variable_length_stays_interpreted(engine):
    cypher = _sub("MATCH (p:{P})-[:CP_KNOWS*1..2]-(f:{P}) WHERE p.sid = $id RETURN count(DISTINCT f)")
    _mode(engine.conn, "sync")
    _compiled_runs(engine.conn)
    r = engine.execute_cypher(cypher, {"id": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert _compiled_runs(engine.conn) == 0


def test_parallel_workers_run_the_compiled_walk(engine):
    """A label scan split among workers: the workers read the coordinator's mode from the plan."""
    cypher = _sub("MATCH (p:{P})-[:CP_KNOWS]-(x:{P})-[:CP_KNOWS]-(y:{P}) RETURN y.sid AS s, count(DISTINCT x) AS n ORDER BY s")
    engine.native_parallel_min = 0
    try:
        _check(engine, cypher, {}, covered=False)
    finally:
        engine.native_parallel_min = -1


# --------------------------------------------------------------------------------------------- lifecycle


def _entries(conn):
    return json.loads(_native(conn).classMethodValue("Graph.KG.Cypher.Gen", "Entries"))


Q_LIFE = "MATCH (p:{P})-[:CP_KNOWS]-(m:{P})-[:CP_KNOWS]-(f:{P}) WHERE p.sid = $id RETURN f.sid AS s, count(*) AS n ORDER BY s"


def test_job_mode_answers_interpreted_until_the_build_is_done(engine):
    conn, q = engine.conn, _sub(Q_LIFE)
    _native(conn).classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")
    assert _entries(conn) == []
    _mode(conn, "job")
    _compiled_runs(conn)
    first = engine.execute_cypher(q, {"id": n("a")})
    assert getattr(first, "error", None) is None, first.error
    assert _compiled_runs(conn) == 0
    deadline = time.time() + 60
    while time.time() < deadline and not any(x["class"] and x["status"] == "ok" for x in _entries(conn)):
        time.sleep(0.5)
    assert any(x["class"] and x["status"] == "ok" for x in _entries(conn)), _entries(conn)
    second = engine.execute_cypher(q, {"id": n("a")})
    assert _compiled_runs(conn) == 1
    assert second.rows == first.rows


def test_drop_all_removes_the_classes(engine):
    conn, q = engine.conn, _sub(Q_LIFE)
    _mode(conn, "sync")
    engine.execute_cypher(q, {"id": n("b")})
    classes = [x["class"] for x in _entries(conn) if x["class"]]
    assert classes
    _native(conn).classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")
    assert _entries(conn) == []
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name %STARTSWITH 'IVGC.'")
    assert cur.fetchone()[0] == 0


def test_a_stale_stamp_builds_the_shape_again(engine):
    conn, q = engine.conn, _sub(Q_LIFE)
    _mode(conn, "sync")
    engine.execute_cypher(q, {"id": n("a")})
    _compiled_runs(conn)
    _run(conn, 'Set h="" For { Set h=$Order(^IVG.CypherCompiled(h)) Quit:h=""  Set $List(^IVG.CypherCompiled(h),3)="stale" }')
    r = engine.execute_cypher(q, {"id": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert _compiled_runs(conn) == 1
    assert all(x["stamp"] != "stale" for x in _entries(conn))


def test_a_failed_build_leaves_the_shape_interpreted(engine):
    conn, q = engine.conn, _sub(Q_LIFE)
    _native(conn).classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")
    _mode(conn, "sync")
    native = _native(conn)
    native.set(1, "IVG.CypherGen", "breaktest")
    try:
        r = engine.execute_cypher(q, {"id": n("c")})
    finally:
        native.kill("IVG.CypherGen", "breaktest")
    assert getattr(r, "error", None) is None, r.error
    assert _compiled_runs(conn) == 0
    failed = [x for x in _entries(conn) if x["status"] not in ("ok", "pending")]
    assert failed and not failed[0]["class"]
    # recorded once: the next run neither builds nor errors
    r = engine.execute_cypher(q, {"id": n("c")})
    assert getattr(r, "error", None) is None and _compiled_runs(conn) == 0
    native.classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")


def test_a_shape_the_generator_fails_on_is_interpreted_and_never_built_empty(engine):
    """An error writing a shape's code is recorded like a failed build; the entry has no lines, and Build refuses to
    compile it (an empty Walk1 would answer with nothing)."""
    conn, q = engine.conn, _sub(Q_LIFE)
    native = _native(conn)
    native.classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")
    _mode(conn, "sync")
    native.set(1, "IVG.CypherGen", "breakwrite")
    try:
        r = engine.execute_cypher(q, {"id": n("d")})
    finally:
        native.kill("IVG.CypherGen", "breakwrite")
    assert getattr(r, "error", None) is None, r.error
    assert _compiled_runs(conn) == 0
    entries = _entries(conn)
    assert entries and all(x["status"].startswith("not written") and not x["class"] for x in entries), entries
    _run(conn, 'Set h="" For { Set h=$Order(^IVG.CypherCompiled(h)) Quit:h=""  Set $List(^IVG.CypherCompiled(h),4)="pending" }')
    for x in entries:
        native.classMethodValue("Graph.KG.Cypher.Gen", "Build", x["hash"])
    assert all(not x["class"] for x in _entries(conn))
    native.classMethodVoid("Graph.KG.Cypher.Gen", "DropAll")
