"""4.6.0 R226 and R260, live: a WITH stage keeps each row in its own graph.

CONTEXT.md, All-graphs read: a read that names no graph sees every graph, and its answer is the union of each graph's
own answer; the same node ID in two graphs is two nodes. Before this, a `WITH` stage projected no `graph_id`: the
stage's own reads were not correlated (R260: `WITH y.name AS name, count(*)` joined every graph's rows that share an
id), a property read or a hop after the stage read every graph's row of the id, and a stage aggregate grouped and
counted by id alone (R226: `CALL ivg.degreeCentrality ... WITH node, degree WHERE degree > 0 RETURN node.name`
paired each graph's degree with every graph's name).

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up for `Graph_KG` by
`scripts/dev/make_scratch_namespace.py` (IVG460FXX when this was written). The module fixture empties it first
(`Graph.KG.Eraser.EraseAll`), so never point it at a namespace another run is using.

By hand. The default graph and graph r226g share the node IDs a and b, with different edges and properties (every
node is labelled P, every edge K):

    default   a "a_def" w 1 -K-> b "b_def" w 2,  a -K-> c "c_def" w 3,  b -K-> c
    r226g     a "a_g" w 10 -K-> b "b_g" w 20,    b -K-> x "x_g" w 30

    out-degree       default a 2, b 1, c 0        r226g a 1, b 1, x 0
    (x:P)-[:K]->(y)  default a->b, a->c, b->c     r226g a->b, b->x

Each case below gives each graph's own answer; without USE GRAPH the answer is the bag union of the two (a whole-
query aggregate is the aggregate over that union: count(DISTINCT y) is 2 + 2 = 4, b being two nodes). Every case runs
on the native path and the SQL path (an engine with `native_cypher=False`), without USE GRAPH, with USE GRAPH 'r226g'
and with USE GRAPH '' (the default graph).

    IVG_NAMESPACE=IVG460FXX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/e2e/test_460_r226_with_stage_graph_e2e.py
"""

from __future__ import annotations

import json
import os
from collections import Counter

import pytest

from tests.e2e import ns460alt as alt

#: an IVG460FX* scratch namespace set up for `Graph_KG` (scripts/dev/make_scratch_namespace.py IVG460FXX)
NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r226g"
NODES = {
    None: {"a": ("a_def", 1), "b": ("b_def", 2), "c": ("c_def", 3)},
    G: {"a": ("a_g", 10), "b": ("b_g", 20), "x": ("x_g", 30)},
}
EDGES = {None: (("a", "b"), ("a", "c"), ("b", "c")), G: (("a", "b"), ("b", "x"))}


def _id(k: str) -> str:
    return f"r226:{k}"


A = {"a": _id("a")}
B = {"a": _id("b")}

#: (name, query, params, {graph: rows}) where "" is the default graph's own answer and G r226g's
PER_GRAPH = [
    ("MATCH WITH node RETURN property",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y RETURN y.name", A,
     {"": [["b_def"], ["c_def"]], G: [["b_g"]]}),
    ("R260: WITH a property and count(*)",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y.name AS name, count(*) AS c RETURN name, c", A,
     {"": [["b_def", 1], ["c_def", 1]], G: [["b_g", 1]]}),
    ("WITH node, count(*) groups per graph and node",
     "MATCH (x:P)-[:K]->(y) WITH y, count(*) AS c RETURN y.name, c", {},
     {"": [["b_def", 1], ["c_def", 2]], G: [["b_g", 1], ["x_g", 1]]}),
    ("WITH node, count(*) WHERE",
     "MATCH (x:P)-[:K]->(y) WITH y, count(*) AS c WHERE c > 1 RETURN y.name", {},
     {"": [["c_def"]], G: []}),
    ("WITH node, count(DISTINCT node)",
     "MATCH (x:P)-[:K]->(y) WITH x, count(DISTINCT y) AS c RETURN x.name, c", {},
     {"": [["a_def", 2], ["b_def", 1]], G: [["a_g", 1], ["b_g", 1]]}),
    ("WITH node, collect(property)",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH x, collect(y.name) AS names RETURN x.name, names", A,
     {"": [["a_def", ["b_def", "c_def"]]], G: [["a_g", ["b_g"]]]}),
    ("WITH node, collect(node)",
     "MATCH (x:P)-[:K]->(y) WITH x, collect(y) AS ys RETURN x.name, size(ys)", {},
     {"": [["a_def", 2], ["b_def", 1]], G: [["a_g", 1], ["b_g", 1]]}),
    ("WITH node, sum(property)",
     "MATCH (x:P)-[:K]->(y) WITH x, sum(y.w) AS s RETURN x.name, s", {},
     {"": [["a_def", 5], ["b_def", 3]], G: [["a_g", 20], ["b_g", 30]]}),
    ("two WITH stages",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH x, y WITH y, x RETURN x.name, y.name", A,
     {"": [["a_def", "b_def"], ["a_def", "c_def"]], G: [["a_g", "b_g"]]}),
    ("two WITH stages over an aggregate",
     "MATCH (x:P)-[:K]->(y) WITH y, count(*) AS c WITH y, c WHERE c > 0 RETURN y.name, c", {},
     {"": [["b_def", 1], ["c_def", 2]], G: [["b_g", 1], ["x_g", 1]]}),
    ("WITH then MATCH from the carried node",
     "MATCH (x {node_id: $a}) WITH x MATCH (x)-[:K]->(y) RETURN x.name, y.name", A,
     {"": [["a_def", "b_def"], ["a_def", "c_def"]], G: [["a_g", "b_g"]]}),
    ("WITH then a further hop",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y MATCH (y)-[:K]->(z) RETURN y.name, z.name", A,
     {"": [["b_def", "c_def"]], G: [["b_g", "x_g"]]}),
    ("R226: CALL YIELD WITH WHERE",
     "CALL ivg.degreeCentrality({}) YIELD node, degree WITH node, degree WHERE degree > 0 "
     "RETURN node.name, degree", {},
     {"": [["a_def", 2], ["b_def", 1]], G: [["a_g", 1], ["b_g", 1]]}),
    ("CALL YIELD WITH WHERE then MATCH",
     "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, degree WITH node WHERE degree > 0 "
     "MATCH (node)-[:K]->(m) RETURN node.name, m.name", {},
     {"": [["a_def", "b_def"], ["a_def", "c_def"], ["b_def", "c_def"]], G: [["a_g", "b_g"], ["b_g", "x_g"]]}),
    ("WITH DISTINCT node",
     "MATCH (x:P)-[:K]->(y) WITH DISTINCT y RETURN y.name", {},
     {"": [["b_def"], ["c_def"]], G: [["b_g"], ["x_g"]]}),
    ("WITH a renamed node",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y AS z RETURN z.name", A,
     {"": [["b_def"], ["c_def"]], G: [["b_g"]]}),
    ("WITH *",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH * RETURN x.name, y.name", A,
     {"": [["a_def", "b_def"], ["a_def", "c_def"]], G: [["a_g", "b_g"]]}),
    ("WITH WHERE reads the carried node's property",
     "MATCH (x {node_id: $a}) WITH x WHERE x.name = 'a_g' RETURN x.name", A,
     {"": [], G: [["a_g"]]}),
    ("WITH then OPTIONAL MATCH",
     "MATCH (x {node_id: $a}) WITH x OPTIONAL MATCH (x)-[:K]->(y) RETURN x.name, y.name", B,
     {"": [["b_def", "c_def"]], G: [["b_g", "x_g"]]}),
    ("WITH node RETURN properties",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y RETURN properties(y)", A,
     {"": [[{"name": "b_def", "w": 2, "id": _id("b")}], [{"name": "c_def", "w": 3, "id": _id("c")}]],
      G: [[{"name": "b_g", "w": 20, "id": _id("b")}]]}),
    # paths a->b, a->c, b->c, a->b->c by default; a->b, b->x, a->b->x in r226g
    ("a variable-length hop, WITH node, count(*)",
     "MATCH (x:P)-[:K*1..2]->(y) WITH y, count(*) AS c RETURN y.name, c", {},
     {"": [["b_def", 1], ["c_def", 3]], G: [["b_g", 1], ["x_g", 2]]}),
]

#: (name, query, params, {scope: rows}, ordered): each scope's rows by hand, where the all-graphs answer is not the
#: bag union of the graphs' own: a LIMIT, or an aggregate grouped by a value (a value is not a node, so equal values of
#: two graphs are one group, as in a one-stage `RETURN y.name, count(*)`)
SCOPED = [
    ("WITH ORDER BY a property LIMIT",
     "MATCH (x {node_id: $a})-[:K]->(y) WITH y ORDER BY y.name DESC LIMIT 2 RETURN y.name", A,
     {None: [["c_def"], ["b_g"]], G: [["b_g"]], "": [["c_def"], ["b_def"]]}, True),
    ("CALL YIELD WITH an aggregate by value",
     "CALL ivg.degreeCentrality({}) YIELD node, degree WITH degree, count(node) AS c RETURN degree, c", {},
     {None: [[2, 1], [1, 3], [0, 2]], G: [[1, 2], [0, 1]], "": [[2, 1], [1, 1], [0, 1]]}, False),
]

#: (name, query, {"": default's, G: r226g's, None: without USE GRAPH}): one row, the aggregate over the read's nodes
WHOLE = [
    ("RETURN count(DISTINCT node)", "MATCH (x:P)-[:K]->(y) RETURN count(DISTINCT y)", {"": 2, G: 2, None: 4}),
    ("WITH count(DISTINCT node)", "MATCH (x:P)-[:K]->(y) WITH count(DISTINCT y) AS c RETURN c", {"": 2, G: 2, None: 4}),
    # KNOWN_ISSUES had this as 1 per shared id on the SQL path and 2 on the native one until R226
    ("RETURN count(DISTINCT node) over a variable-length hop",
     "MATCH (x {node_id: $a})-[:K*1..2]->(y) RETURN count(DISTINCT y)", {"": 2, G: 2, None: 4}),
    ("WITH count(*)", "MATCH (x:P)-[:K]->(y) WITH count(*) AS c RETURN c", {"": 3, G: 2, None: 5}),
    ("WITH sum(property)", "MATCH (x:P)-[:K]->(y) WITH sum(y.w) AS s RETURN s", {"": 8, G: 50, None: 58}),
    ("WITH collect(property)", "MATCH (x:P)-[:K]->(y) WITH collect(y.name) AS ns RETURN size(ns)", {"": 3, G: 2, None: 5}),
    ("WITH collect(DISTINCT node)", "MATCH (x:P)-[:K]->(y) WITH collect(DISTINCT y) AS ys RETURN size(ys)",
     {"": 2, G: 2, None: 4}),
]

SCOPES = {"all graphs": None, "USE GRAPH r226g": G, "USE GRAPH ''": ""}


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for graph, nodes in NODES.items():
        for k, (name, w) in nodes.items():
            nat.create_node(_id(k), labels=["P"], properties={"name": name, "w": w}, graph=graph)
        for s, o in EDGES[graph]:
            nat.create_edge(_id(s), "K", _id(o), graph=graph)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    yield {"native": nat, "sql": sql}
    conn.close()


def _norm(value):
    # a list or map in a result row comes back as JSON text (USER_GUIDE, Reading results); collect() has no order
    if isinstance(value, str) and value[:1] in ("[", "{"):
        value = json.loads(value)
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    return tuple(sorted(value)) if isinstance(value, list) else value


def _bag(rows) -> Counter:
    return Counter(tuple(_norm(v) for v in row) for row in rows)


def _run(engine, scope, query, params):
    prefix = "" if scope is None else f"USE GRAPH '{scope}' "
    result = engine.execute_cypher(prefix + query, params, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return [list(r) for r in result.rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("scope", list(SCOPES.values()), ids=list(SCOPES))
@pytest.mark.parametrize("name,query,params,by_graph", PER_GRAPH, ids=[c[0] for c in PER_GRAPH])
def test_a_with_stage_answers_each_graph_s_own_rows(engines, path, scope, name, query, params, by_graph):
    want = _bag(by_graph[""] + by_graph[G]) if scope is None else _bag(by_graph[scope])
    assert _bag(_run(engines[path], scope, query, params)) == want


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("scope", list(SCOPES.values()), ids=list(SCOPES))
@pytest.mark.parametrize("name,query,by_scope", WHOLE, ids=[c[0] for c in WHOLE])
def test_a_whole_read_aggregate_counts_each_graph_s_nodes(engines, path, scope, name, query, by_scope):
    assert _run(engines[path], scope, query, A) == [[by_scope[scope]]]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("scope", list(SCOPES.values()), ids=list(SCOPES))
@pytest.mark.parametrize("name,query,params,by_scope,ordered", SCOPED, ids=[c[0] for c in SCOPED])
def test_a_stage_answers_each_scope_by_hand(engines, path, scope, name, query, params, by_scope, ordered):
    rows = _run(engines[path], scope, query, params)
    if ordered:
        assert rows == by_scope[scope]
    else:
        assert _bag(rows) == _bag(by_scope[scope])


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("scope", list(SCOPES.values()), ids=list(SCOPES))
def test_return_node_with_an_aggregate_groups_per_graph_and_node(engines, path, scope):
    """`RETURN y, count(*)`: one group per (graph, node), as a WITH stage's (the same defect on the RETURN)."""
    rows = _run(engines[path], scope, "MATCH (x:P)-[:K]->(y) RETURN y, count(*) AS c", {})
    by_graph = {"": [(_id("b"), 1), (_id("c"), 2)], G: [(_id("b"), 1), (_id("x"), 1)]}
    want = by_graph[""] + by_graph[G] if scope is None else by_graph[scope]
    assert Counter((row[0], row[-1]) for row in rows) == Counter(want)
