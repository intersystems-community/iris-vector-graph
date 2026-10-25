"""`USE GRAPH 'g'` confines every read shape to graph g (tenant isolation).

The offline probe `tests/unit/test_use_graph_scope_every_alias.py` found read shapes whose generated SQL names a
graph-owned table with no graph predicate, or puts one on an alias that is not a table. This file runs each of them
against two graphs holding DIFFERENT data (distinct node ids per graph so the counts differ, and a few ids that both
graphs hold so an unscoped join has something to cross) and holds the answer to what the graph alone gives:

* a baseline is taken while only g1 exists (then while only g2 exists), and `USE GRAPH` on the two-graph namespace
  must return exactly that;
* the same answer is also written out by hand per case, so a baseline that is itself wrong (a shape that answers
  nothing in either graph) cannot pass;
* both execution paths are held to it: native Cypher (which declines most of these shapes, so they run on SQL) and the
  SQL path, and shadow mode, which compares the two and raises on a difference.

Data. Labels `L` and `M` (suffixed per run). The ids `a1`, `a2` and `sh` exist in both graphs with different
properties and edges.

    g1: L a1(name m) a2(n) sh(o) a5(p) | M b1 | a1-R->b1  a2-R->a1  sh-R->b1 | label Og1 on x1
    g2: L a1(z) a2(a) sh(w) a3(y) a4(x) | M b1 b2 | a1-R->b1 a1-R->b2 a3-R->b2 a4-R->b2 a2-R->b2 | label Og2 on x2

Run: IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_ARNO_PORT=31972.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

NONE = None

# (id, query with {L} {M} {O1} {O2} placeholders, rows in g1, rows in g2); rows compared as a multiset
CASES = [
    # (a) UNION: no table in any branch was scoped
    (
        "union-all-labelled",
        "MATCH (a:{L}) RETURN a.tag AS t UNION ALL MATCH (b:{L}) RETURN b.tag AS t",
        [["one"]] * 8,
        [["two"]] * 10,
    ),
    (
        "union-distinct-labelled",
        "MATCH (a:{L}) RETURN a.tag AS t UNION MATCH (b:{L}) RETURN b.tag AS t",
        [["one"]],
        [["two"]],
    ),
    (
        "union-all-with-hops",
        "MATCH (a:{L})-[:R]->(b) RETURN b.tag AS t UNION ALL MATCH (c:{M})<-[:R]-(d) RETURN d.tag AS t",
        # g1: a1->b1 (q1), a2->a1 (one), sh->b1 (q1) ; b1<-a1, b1<-sh (one, one)
        [["q1"], ["one"], ["q1"], ["one"], ["one"]],
        # g2: a1->b1, a1->b2, a3->b2, a4->b2, a2->b2 (five q2) ; five 'two' from the M side
        [["q2"]] * 5 + [["two"]] * 5,
    ),
    # (b) comma-FROM sub-SELECTs
    (
        "not-labelled-pattern-predicate",
        "MATCH (a:{L}) WHERE NOT (a)-[:R]->(:{M}) RETURN a.name AS n",
        [["n"], ["p"]],
        [["w"]],
    ),
    (
        "exists-subquery",
        "MATCH (a:{L}) WHERE EXISTS {{ MATCH (a)-[:R]->(:{M}) }} RETURN a.name AS n",
        [["m"], ["o"]],
        [["z"], ["a"], ["y"], ["x"]],
    ),
    # (c) pattern comprehension
    (
        "pattern-comprehension-size",
        "MATCH (a:{L}) RETURN a.name AS n, size([(a)-[:R]->(b) | b.tag]) AS c",
        [["m", 1], ["n", 1], ["o", 1], ["p", 0]],
        [["z", 2], ["a", 1], ["y", 1], ["x", 1], ["w", 0]],
    ),
    (
        "pattern-comprehension-list",
        "MATCH (a:{L}) WHERE a.name IN ['m', 'n', 'z', 'a'] RETURN a.name AS n, [(a)-[:R]->(b) | b.tag] AS ts",
        [["m", ["q1"]], ["n", ["one"]]],
        [["z", ["q2", "q2"]], ["a", ["q2"]]],
    ),
    # (d) CALL { } subquery
    (
        "call-subquery-count",
        # only c is returned: the CALL's column comes out ahead of the others whatever the RETURN says (a separate,
        # older defect), and this file is about which graph the rows come from
        "MATCH (a:{L}) CALL {{ WITH a MATCH (a)-[:R]->(b) RETURN count(b) AS c }} RETURN c",
        [[1], [1], [1], [0]],
        [[2], [1], [1], [1], [0]],
    ),
    # (e) the property lookup behind WITH a ORDER BY a.name
    (
        "with-order-by-limit-1",
        "MATCH (a:{L}) WITH a ORDER BY a.name LIMIT 1 RETURN a.name AS n",
        [["m"]],
        [["a"]],
    ),
    (
        "with-order-by-limit-2-desc",
        "MATCH (a:{L}) WITH a ORDER BY a.name DESC LIMIT 2 RETURN a.name AS n",
        [["p"], ["o"]],
        [["z"], ["y"]],
    ),
    # (f) a leading OPTIONAL MATCH: the null-row fallback read every graph
    (
        "leading-optional-label-only-in-g1",
        "OPTIONAL MATCH (a:{O1}) RETURN a.tag AS t",
        [["g1only"]],
        [[NONE]],
    ),
    (
        "leading-optional-label-only-in-g2",
        "OPTIONAL MATCH (a:{O2}) RETURN a.tag AS t",
        [[NONE]],
        [["g2only"]],
    ),
    # alias-guessing defects: a predicate on an alias that is not a table
    (
        "unwind-after-match",
        "MATCH (a:{L}) UNWIND [1, 2] AS x RETURN a.name AS n, x",
        [[n, x] for n in "mnop" for x in (1, 2)],
        [[n, x] for n in ("z", "a", "y", "x", "w") for x in (1, 2)],
    ),
    # ... and on the LEFT-joined relationship alias of an OPTIONAL MATCH, in WHERE
    (
        "optional-match-relationship-variable",
        "MATCH (a:{L}) OPTIONAL MATCH (a)-[r:R]->(b) RETURN a.name AS n, type(r) AS t",
        [["m", "R"], ["n", "R"], ["o", "R"], ["p", NONE]],
        [["z", "R"], ["z", "R"], ["a", "R"], ["y", "R"], ["x", "R"], ["w", NONE]],
    ),
    (
        "optional-match-relationship-variable-with-where",
        "MATCH (a:{L}) OPTIONAL MATCH (a)-[r:R]->(b:{M}) WHERE b.tag = 'q1' RETURN a.name AS n, b.tag AS t",
        [["m", "q1"], ["n", NONE], ["o", "q1"], ["p", NONE]],
        [["z", NONE], ["a", NONE], ["y", NONE], ["x", NONE], ["w", NONE]],
    ),
    (
        "optional-match-two-hops",
        "MATCH (a:{L}) OPTIONAL MATCH (a)-[:R]->(b)-[:R]->(c) RETURN a.name AS n, c.tag AS t",
        [["m", NONE], ["n", "q1"], ["o", NONE], ["p", NONE]],
        [["z", NONE], ["a", NONE], ["y", NONE], ["x", NONE], ["w", NONE]],
    ),
    # controls: shapes that were already scoped must stay so
    (
        "control-hop-count",
        "MATCH (a:{L})-[:R]->(b) RETURN count(*) AS c",
        [[3]],
        [[5]],
    ),
    (
        "control-with-then-match",
        "MATCH (a:{L}) WITH a MATCH (a)-[:R]->(b:{M}) RETURN a.name AS n",
        [["m"], ["o"]],
        [["z"], ["z"], ["y"], ["x"], ["a"]],
    ),
    (
        "control-unlabelled-pattern-predicate",
        "MATCH (a:{L}) WHERE NOT (a)-[:R]->() RETURN a.name AS n",
        [["p"]],
        [["w"]],
    ),
]


def _load_g1(e, t, g, ids):
    L, M = ids["L"], ids["M"]
    for nid, name in (("a1", "m"), ("a2", "n"), ("sh", "o"), ("a5", "p")):
        e.create_node(f"{t}{nid}", labels=[L], properties={"name": name, "tag": "one"}, graph=g)
    e.create_node(f"{t}b1", labels=[M], properties={"name": "b1", "tag": "q1"}, graph=g)
    e.create_node(f"{t}x1", labels=[ids["O1"]], properties={"tag": "g1only"}, graph=g)
    for s, o in (("a1", "b1"), ("a2", "a1"), ("sh", "b1")):
        e.create_edge(f"{t}{s}", "R", f"{t}{o}", graph=g)


def _load_g2(e, t, g, ids):
    L, M = ids["L"], ids["M"]
    for nid, name in (("a1", "z"), ("a2", "a"), ("sh", "w"), ("a3", "y"), ("a4", "x")):
        e.create_node(f"{t}{nid}", labels=[L], properties={"name": name, "tag": "two"}, graph=g)
    for nid in ("b1", "b2"):
        e.create_node(f"{t}{nid}", labels=[M], properties={"name": nid, "tag": "q2"}, graph=g)
    e.create_node(f"{t}x2", labels=[ids["O2"]], properties={"tag": "g2only"}, graph=g)
    for s, o in (("a1", "b1"), ("a1", "b2"), ("a3", "b2"), ("a4", "b2"), ("a2", "b2")):
        e.create_edge(f"{t}{s}", "R", f"{t}{o}", graph=g)


def _norm(v):
    """A cell as a comparable value: a list may arrive as JSON text, and its element order is not defined."""
    if isinstance(v, str) and v[:1] == "[":
        try:
            v = json.loads(v)
        except ValueError:
            return v
    if isinstance(v, (list, tuple)):
        return sorted((_norm(x) for x in v), key=repr)
    return v


def _bag(rows):
    return sorted(([_norm(c) for c in row] for row in rows), key=repr)


def _run(e, mode, graph, cypher):
    e.native_cypher = mode != "sql"
    e.cypher_shadow = mode == "shadow"
    try:
        r = e.execute_cypher(f"USE GRAPH '{graph}' " + cypher)
    finally:
        e.native_cypher = True
        e.cypher_shadow = False
    assert getattr(r, "error", None) is None, r.error
    return _bag(r.rows)


MODES = ["native", "sql", "shadow"]


@pytest.fixture(scope="module")
def world(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    e._native_privileges_cached = True
    t = uuid.uuid4().hex[:5]
    g1, g2 = f"us{t}_1", f"us{t}_2"
    ids = {"L": f"Ul{t}", "M": f"Um{t}", "O1": f"Uo{t}a", "O2": f"Uo{t}b"}

    def fmt(q):
        return q.format(**ids)

    baseline = {}
    try:
        # each graph alone: the answer `USE GRAPH` has to give when the other graph's data is absent
        _load_g1(e, t, g1, ids)
        for mode in MODES:
            baseline[(g1, mode)] = {c[0]: _try(e, mode, g1, fmt(c[1])) for c in CASES}
        e.erase_graph(g1)
        _load_g2(e, t, g2, ids)
        for mode in MODES:
            baseline[(g2, mode)] = {c[0]: _try(e, mode, g2, fmt(c[1])) for c in CASES}
        # the namespace holds both from here on
        _load_g1(e, t, g1, ids)
        yield e, g1, g2, ids, baseline
    finally:
        for g in (g1, g2):
            e.erase_graph(g)


def _try(e, mode, graph, cypher):
    try:
        return _run(e, mode, graph, cypher)
    except AssertionError as ex:  # a statement the engine refused; recorded, not raised, so the sanity test names it
        return f"ERROR: {ex}"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_the_hand_written_answer_is_what_each_graph_gives_alone(world, case, mode):
    """Sanity of the table: with the other graph absent, the answer is the one written out above."""
    _, g1, g2, _, baseline = world
    name, _, in_g1, in_g2 = case
    assert baseline[(g1, mode)][name] == _bag(in_g1), "g1 alone"
    assert baseline[(g2, mode)][name] == _bag(in_g2), "g2 alone"


@pytest.mark.parametrize("graph_n", [1, 2], ids=["g1", "g2"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_use_graph_reads_only_the_named_graph(world, case, mode, graph_n):
    e, g1, g2, ids, baseline = world
    name, body, in_g1, in_g2 = case
    graph, expect = (g1, in_g1) if graph_n == 1 else (g2, in_g2)
    got = _run(e, mode, graph, body.format(**ids))
    assert got == _bag(expect), f"USE GRAPH '{graph}' {body.format(**ids)}"
    assert got == baseline[(graph, mode)][name]
