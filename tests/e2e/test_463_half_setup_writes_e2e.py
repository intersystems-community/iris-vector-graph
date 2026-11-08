"""4.6.3: on a namespace an interrupted setup left half built, a write whose ^KG part fails raises and leaves no row.

The expert walkthrough of 4.6.0 (KNOWN_ISSUES "After an interrupted setup the engine can accept writes it does not
index"): `initialize_schema()` stopped after 4.6.0's SQL objects were made (16 of 18 tables), before IVG's classes
compiled and before the IVG Schema record and the adjacency index were written. The first-access check accepted the
namespace (its CY_CMP marker was there), and `create_edge` answered True with its row written and its ^KG write failed:
`create_edge ^KG write failed (BuildKG can recover): <CLASS DOES NOT EXIST> ... Graph.KG.EdgeScan`, a WARNING only.

The namespace here is built that way from a finished setup: the record killed, two tables dropped, the functional
adjacency index dropped, `Graph.KG.EdgeScan` deleted. Every edge write that kept ^KG by hand then raises
`GraphWriteError` naming its ^KG part, and the tables hold what they held before the call.

    IVG_R463_HALF_NS=IVG463D pytest tests/e2e/test_463_half_setup_writes_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R463_HALF_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R463_HALF_NS"),
]

DROPPED_TABLES = ("code_crosswalk", "fhir_bridges")


def _counts(conn) -> dict:
    cur = conn.cursor()
    out = {}
    for table in ("nodes", "rdf_edges"):
        cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{table}")
        out[table] = int(cur.fetchone()[0])
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = 'h1' AND p = 'R' AND o_id = 'h2'")
    out["h1_h2"] = [r[0] for r in cur.fetchall()]
    return out


@pytest.fixture(scope="module")
def half_set_up():
    """A finished setup taken back to where the walkthrough's interrupted one stopped; a row h1 -R-> h2 written
    before (with ^KG) so set_edge_weight and delete_edge have an edge to work on."""
    from iris_vector_graph import IRISGraphEngine

    conn = rr.connect(namespace=NS)
    owner = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    owner.erase_graph(None)
    assert owner.create_edge("h1", "R", "h2") is True
    cur = conn.cursor()
    for table in DROPPED_TABLES:
        cur.execute(f"DROP TABLE Graph_KG.{table}")
    cur.execute("DROP INDEX idx_edges_adj ON Graph_KG.rdf_edges")
    conn.commit()
    out = rr.session(['kill ^IVG.Deploy("schema")',
                      'set sc=$system.OBJ.Delete("Graph.KG.EdgeScan","-d")',
                      'write "<<DEL:",$system.Status.GetErrorText(sc),":DEL>>",!',
                      'write "<<EXISTS:",##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.EdgeScan"),":EXISTS>>",!'],
                     namespace=NS)
    assert rr.marked(out, "DEL") == "" and rr.marked(out, "EXISTS") == "0", out
    cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' "
                "AND TABLE_TYPE = 'BASE TABLE'")
    assert int(cur.fetchone()[0]) == 16
    conn.close()
    yield
    # put the namespace back: setup again to the end (it compiles the classes and records the schema)
    conn = rr.connect(namespace=NS)
    try:
        IRISGraphEngine(conn, embedding_dimension=rr.DIM).initialize_schema()
    finally:
        conn.close()


@pytest.fixture
def engine(half_set_up):
    from iris_vector_graph import IRISGraphEngine

    conn = rr.connect(namespace=NS)
    try:
        yield IRISGraphEngine(conn, embedding_dimension=rr.DIM), conn
    finally:
        conn.close()


def test_the_namespace_is_accepted_and_not_adjacency_indexed(engine):
    # the shape the walkthrough met: accepted (no IVGSchemaError), and the writers keep ^KG by hand
    eng, _ = engine
    assert eng.status().schema is None
    assert eng._adjacency_indexed is False


def test_create_edge_raises_naming_the_adjacency_write_and_writes_no_row(engine):
    from iris_vector_graph.errors import GraphWriteError

    eng, conn = engine
    before = _counts(conn)
    with pytest.raises(GraphWriteError) as raised:
        eng.create_edge("x1", "R", "x2")
    message = str(raised.value)
    assert "create_edge" in message and "^KG" in message and "Graph.KG.EdgeScan" in message, message
    assert "initialize_schema()" in message, message
    after = _counts(conn)
    assert after == before  # no edge row, no endpoint node rows
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id IN ('x1', 'x2')")
    assert int(cur.fetchone()[0]) == 0


def test_set_edge_weight_raises_and_keeps_the_old_weight(engine):
    from iris_vector_graph.errors import GraphWriteError

    eng, conn = engine
    before = _counts(conn)
    with pytest.raises(GraphWriteError, match=r"set_edge_weight.*\^KG"):
        eng.set_edge_weight("h1", "R", "h2", 7.5)
    assert _counts(conn) == before


def test_delete_edge_raises_and_keeps_the_row(engine):
    from iris_vector_graph.errors import GraphWriteError

    eng, conn = engine
    before = _counts(conn)
    assert before["h1_h2"], before
    with pytest.raises(GraphWriteError, match=r"delete_edge.*\^KG"):
        eng.delete_edge("h1", "R", "h2")
    assert _counts(conn) == before


def test_bulk_ingest_edges_raises_and_writes_no_row(engine):
    from iris_vector_graph.errors import GraphWriteError

    eng, conn = engine
    before = _counts(conn)
    with pytest.raises(GraphWriteError, match=r"bulk_ingest_edges.*\^KG"):
        eng.bulk_ingest_edges([("y1", "R", "y2")], auto_sync=False)
    assert _counts(conn) == before
