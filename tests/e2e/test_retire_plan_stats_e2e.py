"""Retiring a bare node costs the same whatever the table statistics say (opsreview drop bench, 2026-11-02).

The statement that retires a node (``Graph.KG.Eraser``, ``DeleteBareSet``) carries ``NOT EXISTS`` for an edge
naming the node as target, and ``fk_edges_dest (graph_id, o_id)`` checks the same thing again when the row goes.
With statistics that call ``o_id`` and ``graph_id`` unselective, both planned a scan of ``u_spo_graph_ekey`` (``s``
leads it), so every retired node read the whole edge index: a 500,000-row drop ran at 54 rows/s, against 7,747
after ``TUNE TABLE``. A ``(graph_id, o_id)`` index gives both lookups a plan whose key is given in full, which the
optimizer takes under any statistics; a hint could fix only the ``NOT EXISTS``, never the foreign key's check.

The test sets those statistics on purpose (Fixed: an extent of 10,000,000 and every field at 100%), retires bare
nodes, and counts the global references the process made. It puts the namespace back with ``TUNE TABLE``.
"""

from __future__ import annotations

import os
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_database,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

STATS = "%SYSTEM.SQL.Stats.Table"
EDGES = 5000
ROOT = "IVGTestRetirePlan"


@pytest.fixture
def unselective(iris_connection):
    """A graph of EDGES edges, under statistics that call o_id, graph_id and s unselective; adds bare nodes."""
    o = iris.createIRIS(iris_connection)
    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
    graph = f"g_rp_{uuid.uuid4().hex[:6]}"
    engine.bulk_create_edges(
        [{"source_id": f"{graph}:s{k}", "predicate": "RP", "target_id": f"{graph}:o{k}"} for k in range(EDGES)],
        disable_indexes=False,
        graph=graph,
    )
    cur = iris_connection.cursor()

    def bare(tag, n):
        ids = [f"{graph}:{tag}{k}" for k in range(n)]
        for nid in ids:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [nid, graph])
        return ids

    o.classMethodValue(STATS, "SetExtentSize", "Graph_KG", "rdf_edges", 10_000_000)
    for field in ("o_id", "graph_id", "s"):
        o.classMethodValue(STATS, "SetFieldSelectivity", "Graph_KG", "rdf_edges", field, "100%")
    o.classMethodVoid("%SYSTEM.SQL", "Purge")
    try:
        yield o, graph, bare
    finally:
        o.kill(ROOT)
        o.classMethodValue(STATS, "ClearTableStats", "Graph_KG.rdf_edges")
        cur.execute("TUNE TABLE Graph_KG.rdf_edges")
        o.classMethodVoid("%SYSTEM.SQL", "Purge")
        engine.erase_graph(graph)


def _refs(o) -> int:
    return int(o.classMethodValue("%SYSTEM.Process", "GlobalReferences"))


def test_the_edge_table_has_a_graph_and_target_index(iris_connection):
    cur = iris_connection.cursor()
    cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_edges'"
        " AND INDEX_NAME = 'idx_edges_graph_oid' ORDER BY ORDINAL_POSITION"
    )
    assert [r[0] for r in cur.fetchall()] == ["graph_id", "o_id"]


def test_retiring_a_bare_node_does_not_read_the_edge_index_under_unselective_statistics(unselective):
    o, graph, bare = unselective
    key = o.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph)
    name = o.classMethodValue("Graph.KG.GraphKey", "ForName", graph)

    def retire(ids):
        o.kill(ROOT)
        for nid in ids:
            o.set("", ROOT, nid)
        before = _refs(o)
        erased = o.classMethodValue("Graph.KG.Eraser", "RetireBareNodes", key, name, "^" + ROOT)
        return erased, _refs(o) - before

    # the first call prepares the statement and clears the derived caches; neither is per node
    assert retire(bare("warm", 1))[0] == 1
    erased10, refs10 = retire(bare("a", 10))
    erased30, refs30 = retire(bare("b", 30))
    assert (erased10, erased30) == (10, 30)
    per_node = (refs30 - refs10) / 20
    # a scan of the edge index reads at least EDGES entries, and there were two per node; a keyed lookup reads a few
    assert per_node < EDGES / 10, f"{per_node:.0f} global references per retired node"
