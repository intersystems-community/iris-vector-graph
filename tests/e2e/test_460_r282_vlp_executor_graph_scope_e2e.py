"""4.6.0 R282: the var-length path executor answers each graph's paths with that graph's nodes and relationships.

A variable-length MATCH whose source is bound by id and whose RETURN uses a path function runs in the engine
(`_execute_var_length_labeled_path_funcs`, `_bfs_with_paths`), not in SQL. It fetched the path's nodes, their labels
and properties, and the relationships' properties by node id alone, so a node id used in two graphs came back with
both graphs' labels and properties merged, and a relationship with another graph's properties or none; without
`USE GRAPH` it walked the default graph for `RETURN p` and every graph for `RETURN b`. Each failure on the way was
logged at debug and answered as fewer or emptier rows.

Since 4.6.0 R308 (3ff6a049) a named path expands in SQL whatever its RETURN, so these reads no longer reach that
executor (marked for removal in 4.6.1): each read below is checked to run in SQL, with the same per-graph answers.

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


def _rows(engine, query, graph=None):
    """(columns, rows) of `query` (with USE GRAPH `graph`), checked to run in SQL, not the engine's path-function route
    (R308). A node is n_id, n_labels, n_props; relationships(p) a list of {"type", "props"}; p {"nodes": [ids],
    "rels": [types]} (R311: the Python values)."""
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
    assert not seen, f"{query!r} ran the path-function route, which R308 retired"
    return list(result.columns), [list(r) for r in result.rows]


def _col(engine, query, name, graph=None):
    cols, rows = _rows(engine, query, graph)
    i = cols.index(name)
    return [row[i] for row in rows]


def _node(row, at):
    """(id, labels, props) of the node in columns at, at + 1, at + 2 (n_id, n_labels, n_props)."""
    props = {}
    for item in json.loads(row[at + 2] or "[]"):
        kv = json.loads(item) if isinstance(item, str) else item
        props[kv["key"]] = kv["value"]
    return row[at], json.loads(row[at + 1]), props


def _rel(w):
    return {"type": "knows", "props": {"w": w}}


def test_a_named_graphs_path_nodes_hold_that_graphs_labels_and_properties(engine):
    cols, rows = _rows(engine, SRC + "RETURN b, length(p) AS l", graph=G)
    assert cols == ["b_id", "b_labels", "b_props", "l"]
    assert [(_node(r, 0), r[3]) for r in rows] == [(("b1", ["P"], {"name": "B-g", "id": "b1"}), 1)]


def test_without_use_graph_each_graphs_paths_with_that_graphs_nodes(engine):
    _, rows = _rows(engine, SRC + "RETURN b, length(p) AS l")
    got = sorted(((_node(r, 0), r[3]) for r in rows), key=lambda x: (x[0][0], x[0][2]["name"]))
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
    assert [v for v in _col(engine, query, "r", graph=G)] == [[_rel(9)]]
    assert sorted((v for v in _col(engine, query, "r")), key=repr) == sorted(
        [[_rel(1)], [_rel(1), _rel(2)], [_rel(9)]], key=repr)


def test_return_p_without_use_graph_answers_every_graphs_paths(engine):
    _, rows = _rows(engine, SRC + "RETURN p, b.name AS n")
    assert sorted((tuple(p["nodes"]), n) for p, n in rows) == [
        (("a1", "b1"), "B-def"), (("a1", "b1"), "B-g"), (("a1", "b1", "c1"), "C-def")]
