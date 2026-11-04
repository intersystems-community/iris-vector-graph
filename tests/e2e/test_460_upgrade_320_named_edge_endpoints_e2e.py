"""4.6.0 R076: a 3.2.0 namespace whose named-graph edge has its endpoints only in the default graph upgrades to 4.x.

The 3.2.0 seed (`scripts/fixtures/generate_old_snapshot.py`) creates n1..n6 with `create_node`, which in 3.2.0 puts
every node in the default graph, and then writes the `acme` edge n5-knows->n6 with `create_edge(graph="acme")`, which in
3.2.0 checks nothing: under 3.2.0's key on `node_id` alone, n5's one row satisfied an edge in any graph. 4.0.0 keys
`nodes` on `(graph_id, node_id)` and re-points `fk_edges_source (graph_id, s)` and `fk_edges_dest (graph_id, o_id)`, so
the re-key's `ADD CONSTRAINT fk_edges_source` met the acme edge and failed with SQLCODE -127. Since 4.0.0 `create_edge`
registers an edge's endpoints in the edge's graph (`_ensure_edge_endpoints`); the migration now does the same for the
edges a 3.x release wrote, after the labels and properties are placed in their node's graph and before the keys.

Hand-worked from the seed, after 4.6.0's `initialize_schema()` and `upgrade_to_4_0_0()`:
- nodes: n1..n6 in the default graph `''`, and n5, n6 also in `acme` (the edge's endpoints; nothing else is there);
- rdf_labels / rdf_props: every row in the default graph, so the acme n5 and n6 hold no label and no name;
- each edge's source and target are a node row of the edge's own graph, and both foreign keys are declared;
- `MATCH (a)-[:knows]->(b) WHERE a.name < b.name` answers Ada<Bes and Bes<Cyd only (n5, n6 of acme have no name);
- `USE GRAPH 'acme' MATCH (a)-[:knows]->(b)` answers the one edge n5->n6.

IVG262FX6 is this file's own namespace: dropped and built anew by v3.2.0's own code each run.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262FX6"


@pytest.fixture(scope="module")
def upgraded():
    from build_release_namespace import build

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    build("v3.2.0", NS)
    conn = ns262.connect(NS)
    before = ns262.rows(conn, "SELECT s, o_id, graph_id FROM Graph_KG.rdf_edges WHERE p = 'knows'")
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    engine.initialize_schema()
    report = upgrade_to_4_0_0(conn)
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    yield {"conn": conn, "engine": engine, "report": report, "before": before}
    conn.close()


def test_the_fixture_has_the_320_named_edge_with_default_graph_endpoints(upgraded):
    # the starting point the defect needs: the acme edge, whose n5 and n6 rows 3.2.0 wrote in the default graph only
    assert sorted((str(s), str(o), str(g or "")) for s, o, g in upgraded["before"]) == [
        ("n1", "n2", ""), ("n2", "n3", ""), ("n5", "n6", "acme")]


def test_the_upgrade_runs_every_step(upgraded):
    report = upgraded["report"]
    assert report.blocked == ()
    embeddings = report["embeddings"].report
    assert (embeddings.structural_blockers, embeddings.key_conflicts, embeddings.node_key_blockers) == ({}, {}, {})
    # the two endpoints the acme edge names, registered there, and nothing else
    assert embeddings.endpoints_registered == [("acme", "n5"), ("acme", "n6")]


def test_the_named_edges_endpoints_are_in_its_graph(upgraded):
    graphs = {}
    for node_id, graph_id in ns262.rows(upgraded["conn"], "SELECT node_id, graph_id FROM Graph_KG.nodes"):
        graphs.setdefault(str(node_id), set()).add(str(graph_id or ""))
    assert graphs == {"n1": {""}, "n2": {""}, "n3": {""}, "n4": {""}, "n5": {"", "acme"}, "n6": {"", "acme"}}


def test_every_edge_endpoint_is_a_node_of_the_edges_graph(upgraded):
    for column in ("s", "o_id"):
        dangling = ns262.rows(
            upgraded["conn"],
            f"SELECT e.{column}, e.graph_id FROM Graph_KG.rdf_edges e WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes n "
            f"WHERE n.node_id = e.{column} AND COALESCE(n.graph_id, '') = COALESCE(e.graph_id, ''))",
        )
        assert dangling == [], column


def test_both_edge_foreign_keys_are_declared(upgraded):
    keys = {
        str(r[0]).lower()
        for r in ns262.rows(
            upgraded["conn"],
            "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_edges' AND CONSTRAINT_TYPE = 'FOREIGN KEY'",
        )
    }
    assert {"fk_edges_source", "fk_edges_dest"} <= keys


def test_labels_and_props_stay_in_the_default_graph(upgraded):
    conn = upgraded["conn"]
    labels = sorted((str(s), str(g or "")) for s, g in ns262.rows(conn, "SELECT s, graph_id FROM Graph_KG.rdf_labels"))
    assert labels == [("n1", ""), ("n2", ""), ("n3", ""), ("n4", ""), ("n5", ""), ("n6", "")]
    names = sorted(
        (str(s), str(v), str(g or ""))
        for s, v, g in ns262.rows(conn, "SELECT s, val, graph_id FROM Graph_KG.rdf_props WHERE \"key\" = 'name'")
    )
    assert names == [("n1", "Ada", ""), ("n2", "Bes", ""), ("n3", "Cyd", ""), ("n5", "Dov", ""), ("n6", "Eve", "")]


def test_cypher_reads_the_upgraded_graphs(upgraded):
    engine = upgraded["engine"]
    cmp_rows = engine.execute_cypher(
        "MATCH (a)-[:knows]->(b) WHERE a.name < b.name RETURN a.name, b.name", raise_on_error=True
    ).rows
    assert sorted([str(a), str(b)] for a, b in cmp_rows) == [["Ada", "Bes"], ["Bes", "Cyd"]]
    acme = engine.execute_cypher(
        "USE GRAPH 'acme' MATCH (a)-[:knows]->(b) RETURN a.node_id, b.node_id", raise_on_error=True
    ).rows
    assert [[str(a), str(b)] for a, b in acme] == [["n5", "n6"]]
