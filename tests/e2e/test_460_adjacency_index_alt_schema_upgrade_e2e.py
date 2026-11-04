"""4.6.0 R126: upgrading a namespace to a non-default IVG Schema (RAG) declares the adjacency functional index once the
schema's procedures exist, and says nothing false on the way.

Setup runs `ensure_adjacency_index` twice: in `ensure_indexes` (step 3, before the deploy) and after the deploy. On a
4.5.x namespace upgraded to RAG, step 3 found 4.5.x's `Graph.KG.AdjIndex`/`LabelIndex` compiled and RAG's tables
without the indexes, and called `RAG.backfill_temporal_rows()` first, which the deploy had not yet made (it lives in
`RAG.SqlProcs`). Setup logged "adjacency functional index not declared (^KG stays write-path maintained)" with
SQLCODE -359 ... 'RAG.BACKFILL_TEMPORAL_ROWS' does not exist. The second call then declared the index, so the warning
was false (a v4.5.1 namespace built with schema_prefix RAG and upgraded showed both: the warning, then
idx_edges_adj and idx_labels_adj created and a SQL edge in ^KG).

The fixture gives step 3 that state in `IVG_NAMESPACE`, set up on RAG: both indexes dropped, the class
`RAG.SqlProcs` deleted, and the deploy forced (a 4.5.x namespace's classes differ from 4.6.0's, so its deploy always
runs). Then `initialize_schema()`. By hand: a node pair and one edge r126:x -R126-> r126:y written with SQL INSERTs
alone appear in ^KG("out", 0, "r126:x", "R126", "r126:y") and ^KG("in", 0, "r126:y", "R126", "r126:x"), and a
variable-length read from r126:x along R126 answers r126:y.
"""

from __future__ import annotations

import logging

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

INDEXES = ("SELECT DISTINCT %EXACT(TABLE_NAME), %EXACT(INDEX_NAME) FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = ? "
           "AND INDEX_NAME IN ('idx_edges_adj', 'idx_labels_adj')")


@pytest.fixture(scope="module")
def upgraded():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn, native_cypher=False)
    engine.initialize_schema()
    cur = conn.cursor()
    for nid in ("r126:x", "r126:y"):
        engine.delete_node(nid, graph=None)
    cur.execute("DROP INDEX idx_edges_adj ON RAG.rdf_edges")
    cur.execute("DROP INDEX idx_labels_adj ON RAG.rdf_labels")
    conn.commit()
    nat = iris.createIRIS(conn)
    nat.classMethodValue("%SYSTEM.OBJ", "Delete", f"{alt.SCHEMA}.SqlProcs", "-d")
    before = {"indexes": alt.rows(conn, INDEXES, [alt.SCHEMA]),
              "sqlprocs": int(nat.isDefined("^oddCOM", f"{alt.SCHEMA}.SqlProcs") or 0)}

    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Keep(level=logging.WARNING)
    logging.getLogger("iris_vector_graph").addHandler(handler)
    mp = pytest.MonkeyPatch()
    mp.setenv("IVG_FORCE_CLASS_DEPLOY", "1")
    try:
        upgrader = alt.engine(conn, native_cypher=False)
        status = upgrader.initialize_schema()
    finally:
        mp.undo()
        logging.getLogger("iris_vector_graph").removeHandler(handler)
    cur.execute("INSERT INTO RAG.nodes (node_id, graph_id) VALUES ('r126:x', '')")
    cur.execute("INSERT INTO RAG.nodes (node_id, graph_id) VALUES ('r126:y', '')")
    cur.execute("INSERT INTO RAG.rdf_edges (s, p, o_id, graph_id) VALUES ('r126:x', 'R126', 'r126:y', '')")
    conn.commit()
    yield {"engine": upgrader, "conn": conn, "nat": nat, "before": before, "status": status,
           "warnings": [r.getMessage() for r in records]}
    for nid in ("r126:x", "r126:y"):
        upgrader.delete_node(nid, graph=None)
    conn.commit()
    conn.close()


def test_the_fixture_reached_the_upgrade_state(upgraded):
    assert upgraded["before"] == {"indexes": [], "sqlprocs": 0}


def test_setup_declares_both_indexes(upgraded):
    assert sorted(alt.rows(upgraded["conn"], INDEXES, [alt.SCHEMA])) == [
        ("rdf_edges", "idx_edges_adj"), ("rdf_labels", "idx_labels_adj")]


def test_setup_warns_of_no_index_it_then_declares(upgraded):
    assert not [w for w in upgraded["warnings"] if "adjacency" in w or "BACKFILL_TEMPORAL_ROWS" in w.upper()], \
        upgraded["warnings"]


def test_an_edge_written_with_sql_is_in_the_adjacency_index(upgraded):
    nat = upgraded["nat"]
    assert int(nat.isDefined("^KG", "out", 0, "r126:x", "R126", "r126:y")) in (1, 11)
    assert int(nat.isDefined("^KG", "in", 0, "r126:y", "R126", "r126:x")) in (1, 11)


def test_a_variable_length_read_finds_the_edge(upgraded):
    rows = upgraded["engine"].execute_cypher(
        "MATCH (a)-[:R126*1..2]->(b) WHERE id(a) = 'r126:x' RETURN id(b)", raise_on_error=True).rows
    assert rows == [["r126:y"]]
