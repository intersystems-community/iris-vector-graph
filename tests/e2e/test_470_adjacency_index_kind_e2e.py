"""4.7.0 row 16: a plain index named `idx_edges_adj` is not the adjacency index.

`Graph.KG.Adjacency.Indexed()` asked only whether a compiled index of that name exists, and the engine's
`_adjacency_indexed` and setup's `ensure_adjacency_index` only whether INFORMATION_SCHEMA lists the name. With a plain
index in its place (`CREATE INDEX idx_edges_adj ON rdf_edges (s)`), every writer took `^KG` as the index's to keep and
wrote rows only, so a new edge was in `rdf_edges` and not in `^KG`, and `status()` said nothing. Now a plain index of
that name counts as no adjacency index: the engine's writers keep `^KG` themselves, setup drops it and declares the
functional one, and `status().adjacency.index` says `plain` (with the reason in `status().errors`) until it does. A
Cypher write has no fallback without the index, plain or absent, so setup's replacement is the fix for it.

The test makes the plain index in its own namespace and puts the functional one back at the end.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_adjacency_index_kind_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=rr.DIM)


def _type_class(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT ci.TypeClass FROM %Dictionary.CompiledIndex ci WHERE ci.SqlName = 'idx_edges_adj' AND ci.parent IN "
        "(SELECT Name FROM %Dictionary.CompiledClass WHERE SqlSchemaName = 'Graph_KG' AND SqlTableName = 'rdf_edges')"
    )
    rows = cur.fetchall()
    return [r[0] for r in rows]


def _kg_out(conn, s, p, o) -> int:
    import iris

    return int(iris.createIRIS(conn).isDefined("^KG", "out", 0, s, p, o))


@pytest.fixture
def plain_index():
    conn = _connect()
    cur = conn.cursor()
    assert _type_class(conn) == ["Graph.KG.AdjIndex"], "the namespace does not start with the adjacency index"
    cur.execute("DROP INDEX idx_edges_adj ON Graph_KG.rdf_edges")
    cur.execute("CREATE INDEX idx_edges_adj ON Graph_KG.rdf_edges (s)")
    assert _type_class(conn) == [None]
    yield conn
    try:
        if _type_class(conn) != ["Graph.KG.AdjIndex"]:
            if _type_class(conn):
                cur.execute("DROP INDEX idx_edges_adj ON Graph_KG.rdf_edges")
            cur.execute(
                "CREATE INDEX idx_edges_adj ON Graph_KG.rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) "
                "AS Graph.KG.AdjIndex"
            )
    finally:
        conn.close()


def test_with_a_plain_index_writes_keep_kg(plain_index):
    import iris

    conn = plain_index
    u = "t470i" + uuid.uuid4().hex[:10]
    assert int(iris.createIRIS(conn).classMethodValue("Graph.KG.Adjacency", "Indexed")) == 0
    eng = _engine(conn)
    assert eng._adjacency_indexed is False
    assert eng.create_edge(f"{u}a", "R470I", f"{u}b")
    assert _kg_out(conn, f"{u}a", "R470I", f"{u}b") > 0, "the edge's ^KG entry was not written"
    eng.bulk_ingest_edges([(f"{u}c", f"{u}d")], predicate="R470I", auto_sync=False)  # the server loader
    assert _kg_out(conn, f"{u}c", "R470I", f"{u}d") > 0
    # (A Cypher CREATE writes rows only and leaves ^KG to the index, with no fallback, here as on any namespace
    # without the adjacency index: setup's replacement below is what puts it right for every writer.)


def test_status_reports_the_plain_index(plain_index):
    st = _engine(plain_index).status()
    assert st.adjacency.index == "plain", st.adjacency
    assert any("idx_edges_adj" in e and "plain" in e for e in st.errors), st.errors


def test_setup_replaces_the_plain_index(plain_index):
    conn = plain_index
    u = "t470i" + uuid.uuid4().hex[:10]
    eng = _engine(conn)
    status = eng.initialize_schema()
    assert _type_class(conn) == ["Graph.KG.AdjIndex"], status
    eng2 = _engine(conn)
    assert eng2.status().adjacency.index == "functional"
    assert eng2._adjacency_indexed is True
    assert eng2.create_edge(f"{u}a", "R470I", f"{u}b")
    assert _kg_out(conn, f"{u}a", "R470I", f"{u}b") > 0
