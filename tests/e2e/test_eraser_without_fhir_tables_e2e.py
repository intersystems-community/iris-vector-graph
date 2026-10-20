"""An erase in a namespace that never created the FHIR stores.

`Graph.KG.Eraser` resets the FHIR graph rows (`fhir_graphs`, `fhir_unresolved`, `fhir_definitions`,
`fhir_canonical_refs`) as part of every erase. It used to infer a missing table from the SQLCODE: -30 tolerated, -29
retried without `interp_version`. On IRIS 2026.2.0AI Build 137U an `UPDATE` of a missing table whose SET list holds
`col = NULL` reports -400 instead, so every erase in a namespace without the FHIR tables failed ("erase failed on
Graph_KG.fhir_graphs with SQLCODE -400; nothing was erased"; reported from iris-agentic-memory, 2026-10-20). The eraser
now asks whether each table exists before touching it.

2026.3.0AI (the test image) reports -30 for the same statement, so this test passes there with or without the fix; it
pins the behaviour, an erase that succeeds and removes the graph's rows, on a namespace without the stores.

Runs against `IVG_FHIRLESS_NAMESPACE` (default `IVGTEST`, a scratch namespace with Graph.KG deployed and no FHIR
setup); it fails, rather than skips, when that namespace has the FHIR tables, since it would then prove nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

NAMESPACE = os.environ.get("IVG_FHIRLESS_NAMESPACE", "IVGTEST")
FHIR_TABLES = ("fhir_graphs", "fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")


@pytest.fixture
def engine():
    import iris

    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(
        os.environ.get("IRIS_HOST", "localhost"), int(os.environ.get("IVG_PORT", "31972")), NAMESPACE, "_SYSTEM", "SYS"
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME IN (?, ?, ?, ?)",
        list(FHIR_TABLES),
    )
    present = cur.fetchone()[0]
    assert present == 0, f"{NAMESPACE} has {present} FHIR tables: point IVG_FHIRLESS_NAMESPACE at one without them"
    yield IRISGraphEngine(conn, embedding_dimension=8)
    conn.close()


def test_erasing_a_graph_works_where_the_fhir_tables_were_never_created(engine):
    graph = f"nofhir:{uuid.uuid4().hex[:8]}"
    engine.create_node("nofhir:a", labels=["NoFhir"], properties={"k": "v"}, graph=graph)
    engine.create_node("nofhir:b", labels=["NoFhir"], graph=graph)
    engine.create_edge("nofhir:a", "R", "nofhir:b", graph=graph)

    engine.erase_graph(graph)

    cur = engine.conn.cursor()
    for table in ("nodes", "rdf_edges", "rdf_labels", "rdf_props"):
        cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE graph_id = ?", [graph])
        assert cur.fetchone()[0] == 0, table
