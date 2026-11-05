"""4.6.0 R282: the var-length path executor answers each graph's paths with that graph's nodes and relationships.

A variable-length MATCH whose source is bound by id and whose RETURN uses a path function runs in the engine
(`_execute_var_length_labeled_path_funcs`, `_bfs_with_paths`), not in SQL. It fetched the path's nodes, their labels
and properties, and the relationships' properties by node id alone, so a node id used in two graphs came back with
both graphs' labels and properties merged, and a relationship with another graph's properties or none; without
`USE GRAPH` it walked the default graph for `RETURN p` and every graph for `RETURN b`. Each failure on the way was
logged at debug and answered as fewer or emptier rows.

The graphs (hand-built): the default graph a1:P {name: 'A-def'} -knows {w: 1}-> b1:P {name: 'B-def'} -knows {w: 2}->
c1:Q {name: 'C-def'}; graph g282 a1:P {name: 'A-g'} -knows {w: 9}-> b1:P {name: 'B-g'}. A query without USE GRAPH
answers each graph's paths (USER_GUIDE section 4).

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the nodes are removed after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

G = "g282"
SRC = "MATCH p = (a {node_id: 'a1'})-[:knows*1..2]->(b) "


def _clean(engine):
    for nid in ("a1", "b1", "c1"):
        for graph in (None, G):
            engine.delete_node(nid, graph=graph)


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, embedding_dimension=alt.DIM, namespace=alt.NS, schema_prefix=schema,
                          native_cypher=False)
    _clean(eng)
    eng.create_node("a1", labels=["P"], properties={"name": "A-def"})
    eng.create_node("b1", labels=["P"], properties={"name": "B-def"})
    eng.create_node("c1", labels=["Q"], properties={"name": "C-def"})
    eng.create_edge("a1", "knows", "b1", qualifiers={"w": 1})
    eng.create_edge("b1", "knows", "c1", qualifiers={"w": 2})
    eng.create_node("a1", labels=["P"], properties={"name": "A-g"}, graph=G)
    eng.create_node("b1", labels=["P"], properties={"name": "B-g"}, graph=G)
    eng.create_edge("a1", "knows", "b1", qualifiers={"w": 9}, graph=G)
    yield eng
    try:
        _clean(eng)
    finally:
        conn.close()


def _col(engine, query, name, graph=None):
    """The column `name` of each row: this route's columns are not the RETURN's (an unasked `a` before `b`, a
    `relationships(p)` named so whatever its alias, and `b` left out beside it; open, not this task's)."""
    from iris_vector_graph._engine import query as q

    seen = []
    original = q.QueryMixin._execute_var_length_labeled_path_funcs

    def spy(self, *a, **k):
        seen.append(1)
        return original(self, *a, **k)

    q.QueryMixin._execute_var_length_labeled_path_funcs = spy
    try:
        text = query if graph is None else f"USE GRAPH {graph} {query}"
        result = engine.execute_cypher(text, {}, raise_on_error=True)
    finally:
        q.QueryMixin._execute_var_length_labeled_path_funcs = original
    assert seen, f"{query!r} did not run the path-function route"
    i = result.columns.index(name)
    return [row[i] for row in result.rows]


def _node(value):
    """(id, labels, props) of a node value the executor answers."""
    props = {}
    for item in json.loads(value["_props"] or "[]"):
        kv = json.loads(item) if isinstance(item, str) else item
        props[kv["key"]] = kv["value"]
    return value["_id"], json.loads(value["_labels"]), props


def test_a_named_graphs_path_nodes_hold_that_graphs_labels_and_properties(engine):
    query = SRC + "RETURN b, length(p) AS l"
    assert [_node(b) for b in _col(engine, query, "b", graph=G)] == [("b1", ["P"], {"name": "B-g", "id": "b1"})]
    assert _col(engine, query, "l", graph=G) == [1]


def test_without_use_graph_each_graphs_paths_with_that_graphs_nodes(engine):
    query = SRC + "RETURN b, length(p) AS l"
    got = sorted(zip((_node(b) for b in _col(engine, query, "b")), _col(engine, query, "l")),
                 key=lambda r: (r[0][0], r[0][2]["name"]))
    assert got == [
        (("b1", ["P"], {"name": "B-def", "id": "b1"}), 1),
        (("b1", ["P"], {"name": "B-g", "id": "b1"}), 1),
        (("c1", ["Q"], {"name": "C-def", "id": "c1"}), 2)]


def test_a_property_of_the_end_is_read_in_its_graph(engine):
    query = SRC + "RETURN b.name, length(p)"
    assert sorted(zip(_col(engine, query, "b.name"), _col(engine, query, "length(p)"))) == [
        ("B-def", 1), ("B-g", 1), ("C-def", 2)]
    assert _col(engine, query, "b.name", graph=G) == ["B-g"]


def test_relationships_carry_their_graphs_properties(engine):
    query = SRC + "RETURN b, relationships(p) AS r"
    assert _col(engine, query, "relationships(p)", graph=G) == [[":knows {w: 9}"]]
    assert sorted(_col(engine, query, "relationships(p)")) == [
        [":knows {w: 1}"], [":knows {w: 1}", ":knows {w: 2}"], [":knows {w: 9}"]]


def test_return_p_without_use_graph_answers_every_graphs_paths(engine):
    paths = sorted(tuple(_node(n)[2]["name"] for n in json.loads(v)["nodes"]) for v in _col(engine, SRC + "RETURN p", "p"))
    assert paths == [("A-def", "B-def"), ("A-def", "B-def", "C-def"), ("A-g", "B-g")]
