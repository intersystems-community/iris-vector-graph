"""Spec 025 amendment (2026-10-07): path functions on variable-length paths whose source is
named by id or property, on a graph with more than 1,000 nodes of the source's label.

Expected answers are computed in Python from the edge list the fixture wrote, never from the
engine under test. Runs in the scratch namespace IVG236 (sentinel asserted before the erase).
"""

from __future__ import annotations

import json
import random
from collections import deque

import pytest

N_PROP = 5000
N_GOAL = 50
SRC = "prop23"


def _edges():
    rnd = random.Random(2310)
    out = []
    for i in range(1, N_PROP + 1):
        for _ in range(3):
            out.append((f"prop{i}", "depends_on", f"prop{rnd.randrange(N_PROP) + 1}"))
        out.append((f"prop{i}", "related_to", f"goal{rnd.randrange(N_GOAL) + 1}"))
    return out


EDGES = _edges()


def bfs(source, preds, max_hops, undirected=False):
    """{target: shortest hop count}, source excluded, as a plain breadth-first search."""
    adj = {}
    for s, p, o in EDGES:
        if p in preds:
            adj.setdefault(s, []).append(o)
            if undirected:
                adj.setdefault(o, []).append(s)
    seen, q, out = {source}, deque([(source, 0)]), {}
    while q:
        node, d = q.popleft()
        if d == max_hops:
            continue
        for nxt in adj.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                out[nxt] = d + 1
                q.append((nxt, d + 1))
    return out


@pytest.fixture(scope="module")
def graph(ivg_236_scratch):
    scratch = ivg_236_scratch
    native = scratch.native()
    scratch.require_scratch(native)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    db = scratch.dbapi()
    cur = db.cursor()
    nodes = [(f"prop{i}",) for i in range(1, N_PROP + 1)] + [(f"goal{i}",) for i in range(1, N_GOAL + 1)] + [("lone1",)]
    cur.executemany("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, '')", nodes)
    labels = [(n[0], "Proposition" if n[0].startswith(("prop", "lone")) else "Goal") for n in nodes]
    cur.executemany("INSERT INTO Graph_KG.rdf_labels (s, label, graph_id) VALUES (?, ?, '')", labels)
    cur.executemany('INSERT INTO Graph_KG.rdf_props (s, "key", val, graph_id) VALUES (?, \'id\', ?, \'\')', [(n[0], n[0]) for n in nodes])
    cur.executemany("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, '')", EDGES)
    db.commit()
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(db, namespace="IVG236")
    engine.sync()
    scratch.require_scratch(scratch.native())
    engine = IRISGraphEngine(scratch.dbapi(), namespace="IVG236")
    yield engine
    if scratch.sentinel_ok(scratch.native()):
        scratch.native().classMethodValue("Graph.KG.Eraser", "EraseAll")


@pytest.fixture(autouse=True)
def _no_arno(monkeypatch):
    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")


def run(engine, q):
    r = engine.execute_cypher(q, {})
    assert getattr(r, "error", None) in (None, ""), r.error
    return r


DEP = "MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep) "


def test_the_graph_has_more_than_a_thousand_nodes_of_the_label():
    assert N_PROP > 1000


def test_scenario_3_target_and_depth_columns_and_rows(graph):
    r = run(graph, DEP + "RETURN dep.id, length(path) AS depth")
    assert r.columns == ["dep.id", "depth"]
    expected = bfs(SRC, {"depends_on"}, 3)
    assert expected, "fixture has no reachable targets"
    got = {row[0]: int(row[1]) for row in r.rows}
    assert got == expected
    assert all(1 <= d <= 3 for d in got.values())


def test_scenario_4_the_same_targets_without_the_path_function(graph):
    with_fn = run(graph, DEP + "RETURN dep.id, length(path) AS depth")
    without = run(graph, DEP + "RETURN dep.id")
    assert {r[0] for r in without.rows} == {r[0] for r in with_fn.rows}


def test_scenario_5_only_the_path_function(graph):
    r = run(graph, DEP + "RETURN length(path) AS d")
    assert r.columns == ["d"]
    assert len(r.rows) == len(bfs(SRC, {"depends_on"}, 3))


def test_scenario_6_nodes_and_relationships_belong_to_their_own_row(graph):
    r = run(graph, DEP + "RETURN dep.id, nodes(path) AS ns, relationships(path) AS rs")
    assert r.columns == ["dep.id", "ns", "rs"]
    assert r.rows
    for dep_id, ns, rs in r.rows:
        ns = json.loads(ns) if isinstance(ns, str) else ns
        rs = json.loads(rs) if isinstance(rs, str) else rs
        assert ns[0] == SRC and ns[-1] == dep_id
        assert len(rs) == len(ns) - 1 and set(rs) == {"depends_on"}


def test_scenario_7_a_single_hop(graph):
    r = run(graph, "MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..1]->(dep) RETURN dep.id, length(path) AS depth")
    expected = {o for s, p, o in EDGES if s == SRC and p == "depends_on"} - {SRC}
    assert {row[0] for row in r.rows} == expected
    assert {int(row[1]) for row in r.rows} == {1}


def test_scenario_8_node_id_source_without_a_label_keeps_the_path_function_column(graph):
    r = run(graph, "MATCH path = (p {node_id: 'prop23'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path) AS depth")
    assert r.columns == ["dep.id", "depth"]
    assert {row[0]: int(row[1]) for row in r.rows} == bfs(SRC, {"depends_on"}, 3)


def test_scenario_9_node_id_source_with_a_label(graph):
    r = run(graph, "MATCH path = (p:Proposition {node_id: 'prop23'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path) AS depth")
    assert {row[0]: int(row[1]) for row in r.rows} == bfs(SRC, {"depends_on"}, 3)


def test_scenario_9b_a_label_the_source_lacks_narrows_to_nothing(graph):
    r = run(graph, "MATCH path = (p:Goal {node_id: 'prop23'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path) AS depth")
    assert r.rows == []


def test_scenario_10_no_qualifying_edge_is_an_empty_result_with_its_columns(graph):
    r = run(graph, "MATCH path = (p:Proposition {id: 'lone1'})-[:depends_on*1..3]->(dep) RETURN dep.id, length(path) AS depth")
    assert r.rows == []
    assert r.columns == ["dep.id", "depth"]


def test_scenario_11_undirected_gives_each_target_its_shortest_hop_count(graph):
    r = run(graph, "MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..2]-(dep) RETURN dep.id, length(path) AS depth")
    assert r.columns == ["dep.id", "depth"]
    # The source itself can come back at 2 hops over a pair of distinct edges (a cycle), which
    # a plain breadth-first search does not report; the other targets are compared.
    got = {row[0]: int(row[1]) for row in r.rows if row[0] != SRC}
    assert got == bfs(SRC, {"depends_on"}, 2, undirected=True)


def test_the_label_population_is_irrelevant_to_the_answer(graph):
    """Another source on the same large label gives its own answer, not the label's."""
    r = run(graph, "MATCH path = (p:Proposition {id: 'prop77'})-[:depends_on*1..2]->(dep) RETURN dep.id, length(path) AS depth")
    assert {row[0]: int(row[1]) for row in r.rows} == bfs("prop77", {"depends_on"}, 2)


def test_a_failed_source_lookup_is_an_error_not_an_empty_result(graph, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("SQLCODE -1: too many parameters")

    # The label check on the named source is a lookup too; it failing must reach the caller.
    monkeypatch.setattr(graph._store, "get_node_labels", broken)
    with pytest.raises(RuntimeError, match="Proposition"):
        graph.execute_cypher(DEP + "RETURN dep.id, length(path) AS depth", {})
