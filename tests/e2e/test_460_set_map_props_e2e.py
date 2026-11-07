"""4.6.0 R180: `SET r += {map}`, `SET r = {map}` and `SET n += {map}` change what the graph holds.

`SET r += {p: 2}` failed with SQLCODE -29 (`E3.NODE_ID` not found): the map went to `rdf_props` through a node-id
subquery. A relationship's properties are its `qualifiers`, which `SET r.p` writes; the map now writes every key into
them in one UPDATE.

The graph, worked by hand (ids unique to the run): node `a` (label L180) with an edge `a-[:T180]->t<i>` to each target
`t1`..`t9`, every edge created by `create_edge(qualifiers={"k": 1, "keep": "x"})`, so `properties(r)` starts as
`{k: 1, keep: 'x'}`. Each case writes its own edge:

- t1 `SET r += {p: 2}`: `{k: 1, keep: 'x', p: 2}`
- t2 `SET r += {p: 3, q: 'y', keep: null}`: `{k: 1, p: 3, q: 'y'}` (the null removes `keep`)
- t3 `SET r += $m`, m = `{code: '007', w: 0.5}`: `{k: 1, keep: 'x', code: '007', w: 0.5}`
- t4 `MATCH ()-[r {k: 1}]->(t4) SET r += {k: 2, z: 9}`: `{k: 2, keep: 'x', z: 9}` (the filter key changes too)
- t5 `SET r = {only: 1}`: `{only: 1}`
- t6 `SET r = $m`, m = `{a: 'x', b: None}`: `{a: 'x'}` (a null in a replacing map is not set)
- t7 `SET r = {}`: `{}`
- t8 `SET r.p = 2` after `WITH r`: `{k: 1, keep: 'x', p: 2}`
- t9 is not written: `{k: 1, keep: 'x'}`
- t10 `SET r += {f: true, g: false}`: `{k: 1, keep: 'x', f: true, g: false}`
- t11 `SET r.f = true`: `{k: 1, keep: 'x', f: true}` (it stored the text 'True', which read back as the string)

Nodes (label L180N, each created by `create_node(properties={"k": 1, "keep": "x"})`, so `{id, k: 1, keep: 'x'}`):

- n1 `SET n += {p: 2, q: 'y'}`: `{id, k: 1, keep: 'x', p: 2, q: 'y'}`
- n2 `SET n += {keep: null}`: `{id, k: 1}`, and no `rdf_props` row for `keep`
- n3 `SET n += $m`, m = `{keep: None, w: 0.5}`: `{id, k: 1, w: 0.5}`
- n4 `MATCH (n:L180N {id: $id, k: 1}) SET n += {k: 2, z: 9}`: `{id, k: 2, keep: 'x', z: 9}`
- n5 `SET n = $m`, m = `{id: <n5>, a: 1}`: `{id, a: 1}`, labels still `['L180N']`
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

TARGETS = [f"t{i}" for i in range(1, 12)]
NODES = [f"n{i}" for i in range(1, 6)]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r180_{run}_{k}" for k in ["a"] + TARGETS + NODES}
    assert e.create_node(ids["a"], labels=["L180"])
    for t in TARGETS:
        assert e.create_node(ids[t], labels=["L180"])
        assert e.create_edge(ids["a"], "T180", ids[t], qualifiers={"k": 1, "keep": "x"})
    for n in NODES:
        assert e.create_node(ids[n], labels=["L180N"], properties={"k": 1, "keep": "x"})
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
        e.delete_node(nid)


def _run(g, query, params=None):
    e, _ = g
    r = e.execute_cypher(query, dict(params or {}))
    assert r.error is None, (query, r.error)
    return r.rows


def _rel_props(g, t):
    _, ids = g
    rows = _run(
        g, "MATCH (:L180 {id: $a})-[r:T180]->(:L180 {id: $t}) RETURN properties(r)", {"a": ids["a"], "t": ids[t]}
    )
    assert len(rows) == 1, rows
    return rows[0][0]  # 4.6.0 R311: properties() answers the dict


def _node_props(g, n):
    _, ids = g
    rows = _run(g, "MATCH (n:L180N) WHERE n.id = $id RETURN properties(n)", {"id": ids[n]})
    assert len(rows) == 1, rows
    return rows[0][0]  # 4.6.0 R311: properties() answers the dict


REL = "MATCH (:L180 {id: $a})-[r:T180]->(:L180 {id: $t}) "


@pytest.mark.parametrize(
    "t, tail, params, want",
    [
        ("t1", "SET r += {p: 2}", {}, {"k": 1, "keep": "x", "p": 2}),
        ("t2", "SET r += {p: 3, q: 'y', keep: null}", {}, {"k": 1, "p": 3, "q": "y"}),
        ("t3", "SET r += $m", {"m": {"code": "007", "w": 0.5}}, {"k": 1, "keep": "x", "code": "007", "w": 0.5}),
        ("t5", "SET r = {only: 1}", {}, {"only": 1}),
        ("t6", "SET r = $m", {"m": {"a": "x", "b": None}}, {"a": "x"}),
        ("t7", "SET r = {}", {}, {}),
        ("t8", "WITH r SET r.p = 2", {}, {"k": 1, "keep": "x", "p": 2}),
        ("t10", "SET r += {f: true, g: false}", {}, {"k": 1, "keep": "x", "f": True, "g": False}),
        ("t11", "SET r.f = true", {}, {"k": 1, "keep": "x", "f": True}),
    ],
    ids=[
        "merge_one", "merge_several_null", "merge_param", "replace", "replace_param", "replace_empty", "with_set",
        "merge_booleans", "set_boolean",
    ],
)
def test_relationship_map_set(g, t, tail, params, want):
    _, ids = g
    _run(g, REL + tail, {"a": ids["a"], "t": ids[t], **params})
    assert _rel_props(g, t) == want
    assert _rel_props(g, "t9") == {"k": 1, "keep": "x"}


def test_relationship_merge_changes_the_key_its_match_filters_on(g):
    _, ids = g
    _run(g, "MATCH (:L180 {id: $a})-[r:T180 {k: 1}]->(:L180 {id: $t}) SET r += {k: 2, z: 9}", {"a": ids["a"], "t": ids["t4"]})
    assert _rel_props(g, "t4") == {"k": 2, "keep": "x", "z": 9}


def test_relationship_merge_reads_back_through_return(g):
    _, ids = g
    rows = _run(g, REL + "RETURN r.p, r.keep, r.k", {"a": ids["a"], "t": ids["t1"]})
    assert rows == [[2, "x", 1]]


NODE = "MATCH (n:L180N) WHERE n.id = $id "


@pytest.mark.parametrize(
    "n, tail, params, want",
    [
        ("n1", "SET n += {p: 2, q: 'y'}", {}, {"k": 1, "keep": "x", "p": 2, "q": "y"}),
        ("n2", "SET n += {keep: null}", {}, {"k": 1}),
        ("n3", "SET n += $m", {"m": {"keep": None, "w": 0.5}}, {"k": 1, "w": 0.5}),
    ],
    ids=["merge", "merge_null", "merge_param_null"],
)
def test_node_map_merge(g, n, tail, params, want):
    _, ids = g
    _run(g, NODE + tail, {"id": ids[n], **params})
    assert _node_props(g, n) == {"id": ids[n], **want}


def test_node_merge_null_leaves_no_row(g, iris_connection):
    e, ids = g
    cur = iris_connection.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {e._t('rdf_props')} WHERE s = ? AND \"key\" = 'keep'", [ids["n2"]])
    assert cur.fetchone()[0] == 0


def test_node_merge_changes_the_key_its_match_filters_on(g):
    _, ids = g
    _run(g, "MATCH (n:L180N {id: $id, k: 1}) SET n += {k: 2, z: 9}", {"id": ids["n4"]})
    assert _node_props(g, "n4") == {"id": ids["n4"], "k": 2, "keep": "x", "z": 9}


def test_node_replace_with_a_map_parameter(g):
    _, ids = g
    _run(g, NODE + "SET n = $m", {"id": ids["n5"], "m": {"id": ids["n5"], "a": 1}})
    assert _node_props(g, "n5") == {"id": ids["n5"], "a": 1}
    rows = _run(g, NODE + "RETURN labels(n)", {"id": ids["n5"]})
    assert [r[0] for r in rows] == [["L180N"]]  # 4.6.0 R311: labels() answers the list
