"""4.6.0 R088: `export_graph_ndjson` writes the default graph's edges, and the file re-imports to the same graph.

Before the fix it wrote one line per node and none per edge (`edges` was always 0), so `export_graph_ndjson` then
`import_graph_ndjson` lost every relationship.

The graph, worked by hand (default graph):
    ann:Person {name: Ann}  -KNOWS {since: 2020}-> bob:Person {name: Bob}
    ann -LIVES_IN-> oslo:City {name: Oslo}
    bob -LIVES_IN-> oslo
Three nodes, three edges. Not exported: a named graph's node and edge (`other`), and a temporal edge (its own
export is `export_temporal_edges_ndjson`).
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.e2e]

NODES = {
    "ann": (["Person"], {"name": "Ann"}),
    "bob": (["Person"], {"name": "Bob"}),
    "oslo": (["City"], {"name": "Oslo"}),
}
EDGES = {
    ("ann", "KNOWS", "bob", (("since", 2020),)),
    ("ann", "LIVES_IN", "oslo", ()),
    ("bob", "LIVES_IN", "oslo", ()),
}


def _build(engine):
    for nid, (labels, props) in NODES.items():
        engine.create_node(nid, labels=labels, properties=props)
    for s, p, o, quals in EDGES:
        engine.create_edge(s, p, o, qualifiers=dict(quals) or None)
    engine.create_node("x", labels=["Other"], properties={"name": "X"}, graph="other")
    engine.create_node("y", labels=["Other"], graph="other")
    engine.create_edge("x", "LINKS", "y", graph="other")
    engine.create_edge_temporal("ann", "PINGED", "bob", timestamp=1800000000, weight=1.0)


def _edges(engine):
    cur = engine.conn.cursor()
    try:
        cur.execute(
            f"SELECT s, p, o_id, qualifiers FROM {engine._t('rdf_edges')} WHERE graph_id = '' "
            "AND (qualifiers IS NULL OR NOT (qualifiers %STARTSWITH '{\"temporal\"'))"
        )
        return {
            (s, p, o, tuple(sorted(json.loads(q).items())) if q else ())
            for s, p, o, q in cur.fetchall()
        }
    finally:
        cur.close()


def _nodes(engine):
    out = {}
    for nid in NODES:
        n = engine.get_node(nid)
        assert n is not None, nid
        out[nid] = (sorted(n["labels"]), {"name": n["name"]})
    return out


def test_export_writes_three_edges_and_reimports_to_the_same_graph(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _build(engine)
    assert _edges(engine) == EDGES  # the graph as built, before the export

    path = tmp_path / "graph.ndjson"
    counts = engine.export_graph_ndjson(str(path))
    assert counts == {"nodes": 3, "edges": 3}
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    edge_lines = [line for line in lines if line["kind"] == "edge"]
    assert sorted((e["source"], e["predicate"], e["target"]) for e in edge_lines) == sorted(
        (s, p, o) for s, p, o, _ in EDGES
    )
    assert sorted(line["id"] for line in lines if line["kind"] == "node") == ["ann", "bob", "oslo"]
    # nodes before edges, so an import creates the endpoints first
    kinds = [line["kind"] for line in lines]
    assert kinds == ["node"] * 3 + ["edge"] * 3

    import iris as _iris

    _iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Eraser", "EraseAll")
    iris_connection.commit()
    assert _edges(engine) == set()

    # R213 added the `skipped` count (CHANGELOG Added): a clean export skips no line.
    assert engine.import_graph_ndjson(str(path)) == {"nodes": 3, "edges": 3, "temporal_edges": 0, "skipped": 0}
    assert _edges(engine) == EDGES
    assert _nodes(engine) == {nid: (sorted(labels), props) for nid, (labels, props) in NODES.items()}
    rows = engine.execute_cypher(
        "MATCH (a {node_id: 'ann'})-[r:KNOWS]->(b) RETURN b.name, r.since"
    ).rows
    assert rows == [["Bob", 2020]]
