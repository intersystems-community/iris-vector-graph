"""4.6.0 R291: a variable-length path's path functions answer one shape on both execution routes.

A source named by id (`(a {node_id: 'r291a'})`) goes to the engine's path-function route
(`_execute_var_length_labeled_path_funcs`); a source found by label and property goes to SQL (`CY_VLP_PATHS`). The
first answered other columns (`RETURN b, length(p) AS l` gave `a, b, l`; `RETURN b, relationships(p) AS r` one column
named `relationships(p)`; `RETURN p, b.name AS n` dropped `n`), a node as one dict column, and relationships as
`":knows {w: 1}"` text or bare type names, where SQL gives the RETURN's columns, a node as `b_id, b_labels, b_props`
and relationships as `[{"type", "props"}]` (measured 2026-11-04, 2026.3.0AI). Each RETURN below is run both ways:
the answers are the same, and the hand-worked one.

The graph, worked by hand: r291a -knows {w: 1}-> r291b -knows {w: 2}-> r291c, each labelled R291, named a, b and c.
From a over 1..2 knows hops there are two paths: a-b (length 1) and a-b-c (length 2).

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the nodes are removed after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = (("r291a", "a"), ("r291b", "b"), ("r291c", "c"))
BY_ID = "MATCH p = (a {node_id: 'r291a'})-[:knows*1..2]->(b) "
BY_SQL = "MATCH p = (a:R291)-[:knows*1..2]->(b) WHERE a.name = 'a' "
R1 = {"type": "knows", "props": {"w": 1}}
R2 = {"type": "knows", "props": {"w": 2}}


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    for nid, _ in NODES:
        eng.delete_node(nid)
    for nid, name in NODES:
        eng.create_node(nid, labels=["R291"], properties={"name": name})
    eng.create_edge("r291a", "knows", "r291b", qualifiers={"w": 1})
    eng.create_edge("r291b", "knows", "r291c", qualifiers={"w": 2})
    eng.sync()
    yield eng
    try:
        for nid, _ in NODES:
            eng.delete_node(nid)
    finally:
        conn.close()


@pytest.fixture
def routes(engine, monkeypatch):
    """Run a RETURN both ways: (id route's result, SQL route's result), each checked to have taken its route."""
    cls = type(engine)
    real = cls._execute_var_length_labeled_path_funcs
    calls = []

    def spy(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(cls, "_execute_var_length_labeled_path_funcs", spy)

    def run(ret: str):
        out = []
        for match, route in ((BY_ID, 1), (BY_SQL, 0)):
            calls.clear()
            r = engine.execute_cypher(match + ret)
            assert getattr(r, "error", None) in (None, ""), r.error
            assert len(calls) == route, (match, "path-function route" if route else "SQL route", len(calls))
            out.append((list(r.columns), sorted((list(row) for row in r.rows), key=repr)))
        return out

    return run


def _same(answers):
    by_id, by_sql = answers
    assert by_id == by_sql
    return by_sql


def _node(row, at, nid, name):
    assert row[at] == nid and json.loads(row[at + 1]) == ["R291"]
    # n_props is a JSON list of JSON texts, one {"key", "value"} each, as every translated statement builds it
    props = {(e := json.loads(t))["key"]: e["value"] for t in json.loads(row[at + 2])}
    assert props.get("name") == name, props


def test_a_node_and_the_length(routes):
    cols, rows = _same(routes("RETURN b, length(p) AS l"))
    assert cols == ["b_id", "b_labels", "b_props", "l"]
    assert [(r[0], r[3]) for r in rows] == [("r291b", 1), ("r291c", 2)]
    _node(rows[0], 0, "r291b", "b")
    _node(rows[1], 0, "r291c", "c")


def test_both_ends_and_the_length(routes):
    cols, rows = _same(routes("RETURN a, b, length(p) AS l"))
    assert cols == ["a_id", "a_labels", "a_props", "b_id", "b_labels", "b_props", "l"]
    assert [(r[0], r[3], r[6]) for r in rows] == [("r291a", "r291b", 1), ("r291a", "r291c", 2)]
    _node(rows[0], 0, "r291a", "a")


def test_a_node_and_the_relationships(routes):
    cols, rows = _same(routes("RETURN b, relationships(p) AS r"))
    assert cols == ["b_id", "b_labels", "b_props", "r"]
    assert [(r[0], json.loads(r[3])) for r in rows] == [("r291b", [R1]), ("r291c", [R1, R2])]


def test_a_property_and_the_relationships(routes):
    cols, rows = _same(routes("RETURN b.name AS n, relationships(p) AS r"))
    assert cols == ["n", "r"]
    assert [(r[0], json.loads(r[1])) for r in rows] == [("b", [R1]), ("c", [R1, R2])]


def test_the_relationships_alone_unaliased(routes):
    cols, rows = _same(routes("RETURN relationships(p)"))
    assert cols == ["relationships(p)"]
    assert sorted(json.loads(r[0]) for r in rows) == sorted([[R1], [R1, R2]])


def test_a_property_and_the_nodes(routes):
    cols, rows = _same(routes("RETURN b.name AS n, nodes(p) AS ns"))
    assert cols == ["n", "ns"]
    assert [(r[0], json.loads(r[1])) for r in rows] == [("b", ["r291a", "r291b"]), ("c", ["r291a", "r291b", "r291c"])]


def test_the_path_and_a_property(routes):
    cols, rows = _same(routes("RETURN p, b.name AS n"))
    assert cols == ["p", "n"]
    assert sorted(((json.loads(r[0]), r[1]) for r in rows), key=lambda x: x[1]) == [
        ({"nodes": ["r291a", "r291b"], "rels": ["knows"]}, "b"),
        ({"nodes": ["r291a", "r291b", "r291c"], "rels": ["knows", "knows"]}, "c"),
    ]


def test_the_length_and_a_property(routes):
    cols, rows = _same(routes("RETURN length(p) AS l, b.name AS n"))
    assert (cols, rows) == (["l", "n"], [[1, "b"], [2, "c"]])
