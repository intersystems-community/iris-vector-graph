"""4.6.0 R089: `bulk_create_nodes` writes the same rows on the server path and the SQL path; `plaid_build` checks `dim`.

With the classes deployed the batch went to `Graph.KG.EdgeScan.BulkIngestNodesSQL`, otherwise to batch SQL, and the two
disagreed (measured on IVG460FX4 before the fix):
  - a node's `graph` key: the SQL path wrote the node into that graph, the server path into the default graph;
  - an existing node's property: the SQL path kept the stored value, the server path overwrote it;
  - the `id` property: the SQL path stored it (as `create_node` does), the server path did not.
The SQL path's rows are the ones meant (the docstring's "skipping rows that already exist"; `store_node` is the
call that replaces properties), so both paths must write them.

The batch, worked by hand. Before it: `a` exists, labels [Old], properties {name: old, keep: k} (and id: a).
    {id: a, labels: [New], properties: {name: new, flag: True}}
    {id: b, labels: [L],   properties: {n: 5}}
    {id: c, labels: [G],   properties: {x: y}, graph: g89}
After it: a has labels New and Old; name stays old, flag is 'true', keep k, id a. b in the default graph with
label L, n '5', id b. c in graph g89 only, label G, x y, id c. Both answer [a, b, c].
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


def _engine(conn, server: bool):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.capabilities import IRISCapabilities

    engine = IRISGraphEngine(conn)
    engine.capabilities = IRISCapabilities(objectscript_deployed=server)
    return engine


def _state(engine, ids):
    marks = ",".join("?" * len(ids))
    cur = engine.conn.cursor()
    try:
        cur.execute(f"SELECT graph_id, node_id FROM {engine._t('nodes')} WHERE node_id IN ({marks})", ids)
        nodes = set(cur.fetchall())
        cur.execute(f"SELECT graph_id, s, label FROM {engine._t('rdf_labels')} WHERE s IN ({marks})", ids)
        labels = set(cur.fetchall())
        cur.execute(f'SELECT graph_id, s, "key", val FROM {engine._t("rdf_props")} WHERE s IN ({marks})', ids)
        props = set(cur.fetchall())
    finally:
        cur.close()
    return nodes, labels, props


@pytest.mark.parametrize("server", [True, False], ids=["server", "sql"])
def test_both_paths_write_the_hand_worked_rows(iris_connection, iris_master_cleanup, server, monkeypatch):
    import iris_vector_graph.schema as schema_mod

    loader_calls = []
    real = schema_mod._call_classmethod_large

    def spy(obj, cls, method, *args):
        loader_calls.append(method)
        return real(obj, cls, method, *args)

    monkeypatch.setattr(schema_mod, "_call_classmethod_large", spy)
    engine = _engine(iris_connection, server)
    run = uuid.uuid4().hex[:6]
    a, b, c = (f"r89{run}{k}" for k in "abc")
    assert engine.create_node(a, labels=["Old"], properties={"name": "old", "keep": "k"})
    # The default-graph batch is the server loader's when the classes are deployed; a batch naming a graph is SQL's.
    assert engine.bulk_create_nodes([
        {"id": a, "labels": ["New"], "properties": {"name": "new", "flag": True}},
        {"id": b, "labels": ["L"], "properties": {"n": 5}},
    ]) == [a, b]
    assert loader_calls == (["BulkIngestNodesSQL"] if server else [])
    ret = engine.bulk_create_nodes([
        {"id": c, "labels": ["G"], "properties": {"x": "y"}, "graph": "g89"},
    ])
    assert ret == [c]
    assert loader_calls == (["BulkIngestNodesSQL"] if server else [])
    nodes, labels, props = _state(engine, [a, b, c])
    assert nodes == {("", a), ("", b), ("g89", c)}
    assert labels == {("", a, "Old"), ("", a, "New"), ("", b, "L"), ("g89", c, "G")}
    assert props == {
        ("", a, "name", "old"),
        ("", a, "keep", "k"),
        ("", a, "id", a),
        ("", a, "flag", "true"),
        ("", b, "n", "5"),
        ("", b, "id", b),
        ("g89", c, "x", "y"),
        ("g89", c, "id", c),
    }


def test_plaid_build_refuses_tokens_of_another_width(iris_connection):
    pytest.importorskip("sklearn", reason="plaid_build clusters with scikit-learn (dev extra)")
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    name = f"r89plaid{uuid.uuid4().hex[:6]}"
    docs = [
        {"id": "d1", "tokens": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]},
        {"id": "d2", "tokens": [[0.0, 0.0, 1.0, 0.0]]},
    ]
    try:
        with pytest.raises(ValueError, match="dim"):
            engine.plaid_build(name, docs, n_clusters=2, dim=3)
        assert engine.plaid_info(name) == {}  # nothing written

        ragged = [{"id": "d1", "tokens": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]}]
        with pytest.raises(ValueError, match="dim"):
            engine.plaid_build(name, ragged, n_clusters=1)
        assert engine.plaid_info(name) == {}

        engine.plaid_build(name, docs, n_clusters=2, dim=4)
        info = engine.plaid_info(name)
        assert (info["dim"], info["indexed"], info["totalTokens"]) == (4, 2, 3)
    finally:
        engine.plaid_drop(name)
