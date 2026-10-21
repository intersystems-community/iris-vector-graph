"""Spec 253: grouped counts over a label scan with no hop (BI01's shape) answer as the SQL path does.

The executor counts these in a tight loop: the scan's filters inline, each match's grouping keys once, the counts in a
local array, and the groups made once per key at the end. This holds the loop to the SQL path's answers, in shadow mode,
in one process and split across workers: filters and keys on the same and on other properties, a missing property, a
substring key, number-like strings, two labels, nodes in two graphs, and the shapes the loop leaves to the general
walk (count(DISTINCT), an ungrouped count).

Run against `IVG_NAMESPACE=TCKJ` (or any namespace with Graph.KG deployed and the adjacency index declared).
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
GRAPH = f"scg{T}"
POSTS = {
    # id: (d, cat, num, hot, graph); "d" is missing on p6, "cat" on p7
    "p1": ("2010-03-01", "x", "10", False, None),
    "p2": ("2010-11-30", "y", "9", True, None),
    "p3": ("2011-01-15", "x", "1.5", False, None),
    "p4": ("2011-06-01", "z", "01", True, None),
    "p5": ("2012-02-02", "x", "x9", False, None),
    "p6": (None, "y", "10", False, None),
    "p7": ("2011-07-07", None, "9", True, None),
    "q1": ("2010-05-05", "x", "10", False, GRAPH),
    "q2": ("2011-05-05", "w", "2", True, GRAPH),
}


def n(k):
    return f"sc{T}{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    for k, (d, cat, num, hot, graph) in POSTS.items():
        props = {"sid": n(k), "num": num}
        if d is not None:
            props["d"] = d
        if cat is not None:
            props["cat"] = cat
        labels = [f"ScPost{T}", f"ScHot{T}"] if hot else [f"ScPost{T}"]
        e.create_node(n(k), labels=labels, properties=props, graph=graph)
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for k, v in POSTS.items():
        if v[4] is None:
            e.delete_node(n(k))
    e.erase_graph(GRAPH)


P, H = f"ScPost{T}", f"ScHot{T}"
CASES = [
    ("BI01 shape", f"MATCH (m:{P}) WHERE m.d < $max RETURN substring(m.d, 0, 4) AS y, count(m) AS n ORDER BY y", {"max": "2012-01-01"}),
    ("key on another property", f"MATCH (m:{P}) WHERE m.d >= $min RETURN m.cat AS c, count(*) AS n ORDER BY c", {"min": "2010-06-01"}),
    (
        "two filters, ordered by the count",
        f"MATCH (m:{P}) WHERE m.d > $a AND m.d < $b RETURN m.cat AS c, count(m) AS n ORDER BY n DESC, c",
        {"a": "2010-01-01", "b": "2011-12-31"},
    ),
    ("no filter, a missing key", f"MATCH (m:{P}) RETURN m.cat AS c, count(m) AS n ORDER BY c", {}),
    ("number-like keys", f"MATCH (m:{P}) RETURN m.num AS k, count(m) AS n ORDER BY k", {}),
    (
        "two keys",
        f"MATCH (m:{P}) WHERE m.d < $max RETURN m.cat AS c, substring(m.d, 0, 7) AS ym, count(*) AS n ORDER BY c, ym",
        {"max": "2013-01-01"},
    ),
    ("two labels", f"MATCH (m:{P}:{H}) WHERE m.d < $max RETURN m.cat AS c, count(m) AS n ORDER BY c", {"max": "2013-01-01"}),
    ("a filter on the key's property, LIMIT", f"MATCH (m:{P}) WHERE m.cat > $c RETURN m.cat AS c, count(m) AS n ORDER BY n DESC, c LIMIT 2", {"c": "w"}),
    ("a number filter", f"MATCH (m:{P}) WHERE m.num < $k RETURN m.cat AS c, count(m) AS n ORDER BY c", {"k": "5"}),
    ("an ungrouped count", f"MATCH (m:{P}) WHERE m.d < $max RETURN count(m)", {"max": "2011-01-01"}),
    ("count(DISTINCT) of the scanned node", f"MATCH (m:{P}) RETURN m.cat AS c, count(DISTINCT m) AS n ORDER BY c", {}),
    ("substring past the end", f"MATCH (m:{P}) RETURN substring(m.d, 8, 10) AS t, count(m) AS n ORDER BY t", {}),
]


@pytest.mark.parametrize("parallel_min", [-1, 0], ids=["one process", "workers"])
@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_a_scan_group_equals_sql(engine, parallel_min, name, cypher, params):
    saved = engine.native_parallel_min
    engine.native_parallel_min = parallel_min
    try:
        before = engine.__dict__.get("_native_answers", 0)
        r = engine.execute_cypher(cypher, params)
    finally:
        engine.native_parallel_min = saved
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
    assert r.rows, "the fixture should give every case rows"


# A label of at least eight members in one graph key is shared out among workers by key range (Exec.Bounds), not every
# n-th member: 60 posts in one graph, read with USE GRAPH, each replying to the one before.
RG = f"scr{T}"
RP = f"ScRange{T}"


@pytest.fixture(scope="module")
def ranged(engine):
    for i in range(60):
        props = {"sid": f"{RG}-{i:02d}", "d": f"201{i % 4}-0{1 + i % 9}-01", "cat": "abc"[i % 3]}
        engine.create_node(f"{RG}-{i:02d}", labels=[RP], properties=props, graph=RG)
    for i in range(1, 60):
        engine.create_edge(f"{RG}-{i:02d}", f"SC_REPLY{T}", f"{RG}-{(i - 1) // 2:02d}", graph=RG)
    yield engine
    engine.erase_graph(RG)


RANGED = [
    ("grouped counts", f"USE GRAPH '{RG}' MATCH (m:{RP}) WHERE m.d < $max RETURN substring(m.d, 0, 4) AS y, count(m) AS n ORDER BY y", {"max": "2013-01-01"}),
    ("an ungrouped count", f"USE GRAPH '{RG}' MATCH (m:{RP}) WHERE m.cat <> $c RETURN count(m)", {"c": "a"}),
    ("a hop, grouped", f"USE GRAPH '{RG}' MATCH (c:{RP})-[:SC_REPLY{T}]->(m:{RP}) RETURN m.sid AS p, count(c) AS n ORDER BY n DESC, p LIMIT 5", {}),
    ("count(DISTINCT) over a hop", f"USE GRAPH '{RG}' MATCH (c:{RP})-[:SC_REPLY{T}]->(m:{RP}) RETURN m.cat AS k, count(DISTINCT c) AS n ORDER BY k", {}),
]


@pytest.mark.parametrize("name,cypher,params", RANGED, ids=[c[0] for c in RANGED])
def test_a_scan_shared_out_by_key_range_equals_sql(ranged, name, cypher, params):
    saved = ranged.native_parallel_min
    ranged.native_parallel_min = 0
    try:
        before = ranged.__dict__.get("_native_answers", 0), ranged.__dict__.get("_native_parallel_runs", 0)
        r = ranged.execute_cypher(cypher, params)
    finally:
        ranged.native_parallel_min = saved
    assert getattr(r, "error", None) is None, r.error
    assert ranged.__dict__.get("_native_answers", 0) == before[0] + 1, "the native path did not answer"
    assert ranged.__dict__.get("_native_parallel_runs", 0) == before[1] + 1, "the scan did not run in workers"
    assert r.rows
