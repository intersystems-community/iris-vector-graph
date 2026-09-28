"""The FHIR-graph tables are lazy: `initialize_schema` in a namespace without a FHIR
repository leaves them out, and every FHIR-graph entry point still answers. Runs in
USER on the enterprise container, which has no FHIR server."""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import FHIRGraphError

LAZY = ("fhir_graphs", "fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")
EAGER = ("fhir_bridges", "code_crosswalk")
PORT = int(os.environ.get("IVG_PORT", "31972"))


def _tables(cur):
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
    )
    return {r[0] for r in cur.fetchall()}


@pytest.fixture(scope="module")
def user_engine():
    import iris

    try:
        conn = iris.connect("localhost", PORT, "USER", "_SYSTEM", "SYS")
    except Exception as exc:  # pragma: no cover - environment
        pytest.skip(f"USER on port {PORT} not reachable ({exc})")
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES"
        " WHERE TABLE_SCHEMA = 'HS_FHIRServer' AND TABLE_NAME = 'Repo'"
    )
    if cur.fetchone()[0]:  # pragma: no cover - environment
        pytest.skip("USER has a FHIR repository; this test needs a namespace without one")
    # Older installs created the tables eagerly; without a repository they are empty.
    for t in LAZY:
        if t in _tables(cur):
            cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{t}")
            assert cur.fetchone()[0] == 0, f"Graph_KG.{t} holds rows in a namespace with no FHIR repository"
            cur.execute(f"DROP TABLE Graph_KG.{t}")
    conn.commit()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine.initialize_schema(auto_deploy_objectscript=False)
    yield engine, cur
    conn.close()


def test_initialize_schema_skips_fhir_graph_tables(user_engine):
    _, cur = user_engine
    have = _tables(cur)
    assert not have & set(LAZY)
    assert set(EAGER) <= have


def test_anchors_answer_without_tables(user_engine):
    engine, _ = user_engine
    assert engine.fhir_patient_anchors("p1") == {"graphs": [], "anchors": []}


def test_status_says_not_registered(user_engine):
    engine, _ = user_engine
    with pytest.raises(FHIRGraphError, match="not registered"):
        engine.fhir_graph_status("fhir:USER:X0001")


def test_register_without_repository_creates_nothing(user_engine):
    engine, cur = user_engine
    with pytest.raises(FHIRGraphError, match="no FHIR repository"):
        engine.fhir_graph_register()
    assert not _tables(cur) & set(LAZY)


def test_erase_and_verify_without_tables(user_engine):
    engine, _ = user_engine
    engine.erase_graph("lazy-fhir-probe")
    assert engine.verify_graph("lazy-fhir-probe") is not None
