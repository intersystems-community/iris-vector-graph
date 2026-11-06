"""4.6.0, live: after `initialize_schema()` (and `upgrade_to_4_0_0()` for a 2.x/3.x install) no row's graph is NULL.

4.6.0's Cypher joins and writes compare `graph_id` with `=` (fixes65: each matched node in its own graph). A NULL
graph_id equals nothing, so a row an older release left NULL would drop out of every such read and write, with no
error. 2.x and 3.x spelled the default graph three ways in `rdf_edges` (NULL, CHAR(0), ''), and 2.20's column is
nullable. This builds each release's install with that release's own code (scripts/fixtures/build_release_namespace.py),
adds an edge with a NULL graph where the release's column allows one (n3 -[:R68]-> n4, between two default-graph nodes
of the seed), upgrades with this checkout, and checks:

* nodes, rdf_labels, rdf_props, rdf_edges and kg_NodeEmbeddings hold no NULL graph_id, and each column is NOT NULL;
* the NULL-graph edge is the default graph's now, and Cypher reads and writes it as any default-graph edge: worked by
  hand, `MATCH (a {node_id: 'n3'})-[:R68]->(b) RETURN b.node_id` answers 'n4', `SET r.w = 1` on it stores w = 1 in
  the default graph, and `USE GRAPH ''` answers it too;
* the seed's own default-graph edges (n1-knows->n2, n2-knows->n3) answer the same.

Namespace IVG460FX68B, dropped and built anew per release (IVG_UPGRADE_NULL_NS names another IVG460FX*).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_UPGRADE_NULL_NS", "IVG460FX68B")
TABLES = ("nodes", "rdf_labels", "rdf_props", "rdf_edges", "kg_NodeEmbeddings")
#: the releases whose rdf_edges column takes a NULL graph (2.20), and the ones whose does not (they upgrade the same)
RELEASES = ("v2.20.0", "v3.2.0", "v4.5.1")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "fixtures"))


def _nullable(conn, table) -> bool:
    rows = ns262.rows(
        conn,
        "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ? "
        "AND COLUMN_NAME = 'graph_id'",
        [table],
    )
    return bool(rows) and str(rows[0][0]).upper() == "YES"


@pytest.fixture(scope="module", params=RELEASES)
def upgraded(request):
    from build_release_namespace import build

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    tag = request.param
    build(tag, NS)
    conn = ns262.connect(NS)
    # the release takes the NULL-graph edge or refuses it (3.2.0 on: SQLCODE -108, its column is required)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES ('n3', 'R68', 'n4', NULL)")
        conn.commit()
        injected = True
    except Exception as exc:
        conn.rollback()
        assert "-108" in str(exc), exc
        injected = False
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    engine.initialize_schema()
    if not tag.startswith("v4"):
        assert upgrade_to_4_0_0(conn).blocked == ()
        engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
        engine.initialize_schema()
    yield {"tag": tag, "conn": conn, "engine": engine, "injected": injected}
    conn.close()


def test_only_the_2_20_install_takes_a_null_graph(upgraded):
    # the starting point the defect needs, where the release allows it
    assert upgraded["injected"] == (upgraded["tag"] == "v2.20.0")


@pytest.mark.parametrize("table", TABLES)
def test_no_row_has_a_null_graph_and_none_can(upgraded, table):
    conn = upgraded["conn"]
    assert ns262.rows(conn, f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE graph_id IS NULL") == [(0,)]
    assert not _nullable(conn, table)


def test_cypher_reads_and_writes_the_edge_that_had_no_graph(upgraded):
    engine, conn = upgraded["engine"], upgraded["conn"]
    q = "MATCH (a {node_id: 'n3'})-[:R68]->(b) RETURN b.node_id"
    if not upgraded["injected"]:
        # the release refused the edge: there is none
        assert engine.execute_cypher(q, raise_on_error=True).rows == []
        return
    assert engine.execute_cypher(q, raise_on_error=True).rows == [["n4"]]
    assert engine.execute_cypher("USE GRAPH '' " + q, raise_on_error=True).rows == [["n4"]]
    engine.execute_cypher("MATCH ({node_id: 'n3'})-[r:R68]->() SET r.w = 1", raise_on_error=True)
    conn.commit()
    assert ns262.rows(
        conn, "SELECT graph_id, JSON_VALUE(qualifiers, '$.w') FROM Graph_KG.rdf_edges WHERE p = 'R68'"
    ) == [("", "1")]


def test_the_default_graphs_edges_answer(upgraded):
    rows = upgraded["engine"].execute_cypher(
        "MATCH (a)-[:knows]->(b) WHERE a.node_id IN ['n1', 'n2'] RETURN a.node_id, b.node_id ORDER BY a.node_id",
        raise_on_error=True,
    ).rows
    assert [[str(a), str(b)] for a, b in rows] == [["n1", "n2"], ["n2", "n3"]]
