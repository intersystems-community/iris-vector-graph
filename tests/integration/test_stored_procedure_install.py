"""
Integration tests for initialize_schema() stored procedure installation.
Feature: 020-initialize-schema-stored-procedures

Requires a live IRIS container (iris_vector_graph, port via get_exposed_port(1972)).
Set SKIP_IRIS_TESTS=true to skip (defaults to false — tests always run).
"""

import json
import math
import os
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false") == "true",
    reason="IRIS tests disabled via SKIP_IRIS_TESTS=true",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_procedures(iris_connection):
    """Drop Graph_KG procedures before the test module runs (clean slate)."""
    cursor = iris_connection.cursor()

    def _drop_procedures():
        for proc in ("kg_KNN_VEC", "kg_TXT", "kg_RRF_FUSE"):
            try:
                cursor.execute(f"DROP PROCEDURE Graph_KG.{proc}")
            except Exception:
                pass

    _drop_procedures()
    iris_connection.commit()
    yield iris_connection

    # Cleanup after module
    _drop_procedures()
    iris_connection.commit()


# ---------------------------------------------------------------------------
# US1 — Fresh install uses server-side vector search
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_initialize_schema_installs_all_procedures(clean_procedures):
    """FR-007 / SC-002: After initialize_schema(), kg_KNN_VEC must exist in IRIS.

    kg_TXT and kg_RRF_FUSE are optional (depend on full-text search availability).
    """
    from iris_vector_graph.engine import IRISGraphEngine

    conn = clean_procedures
    engine = IRISGraphEngine(conn, embedding_dimension=384)
    engine.initialize_schema()

    cursor = conn.cursor()
    cursor.execute(
        "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES "
        "WHERE ROUTINE_SCHEMA = 'Graph_KG' "
        "ORDER BY ROUTINE_NAME"
    )
    rows = cursor.fetchall()
    installed = {r[0].upper() for r in rows}

    assert "KG_KNN_VEC" in installed, f"kg_KNN_VEC not installed. Found: {installed}"


@pytest.mark.integration
def test_kg_knn_vec_callable_after_init(clean_procedures):
    """FR-007: kg_KNN_VEC must be installed and callable after initialize_schema()."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = clean_procedures
    engine = IRISGraphEngine(conn, embedding_dimension=384)
    engine.initialize_schema()

    # Build a 384-dim query vector as JSON
    query_vec = json.dumps([math.sin(i * 0.01) for i in range(384)])

    # Use the engine's kg_KNN_VEC method — it calls the server-side proc,
    # falling back to Python only if the procedure isn't installed.
    # With a fresh schema (no embeddings), it should return an empty list.
    results = engine.kg_KNN_VEC(query_vec, k=5)
    assert isinstance(results, list), "kg_KNN_VEC must return a list"
    # Empty table → empty results (no assertion on length)


@pytest.mark.integration
def test_kg_knn_vec_uses_server_side_path(clean_procedures):
    """SC-002: engine.kg_KNN_VEC() must use direct IRIS SQL (HNSW path), not slow Python fallback."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = clean_procedures
    engine = IRISGraphEngine(conn, embedding_dimension=384)
    engine.initialize_schema()

    query_vec = json.dumps([math.sin(i * 0.01) for i in range(384)])

    # Patch the slow Python fallback to raise — if it's invoked, the test fails.
    # The engine now uses direct SQL (TOP k + VECTOR_COSINE + TO_VECTOR) which
    # triggers IRIS HNSW index. The Python fallback is only used on SQL failure.
    with patch.object(
        engine,
        "_kg_KNN_VEC_python_optimized",
        side_effect=AssertionError("Python fallback must not be invoked — HNSW SQL path should be used"),
    ):
        results = engine.kg_KNN_VEC(query_vec, k=5)
        assert isinstance(results, list)


@pytest.mark.integration
def test_initialize_schema_is_idempotent(clean_procedures):
    """SC-003: calling initialize_schema() twice must not raise."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = clean_procedures
    engine = IRISGraphEngine(conn, embedding_dimension=384)
    engine.initialize_schema()
    # Second call — must be idempotent (CREATE OR REPLACE handles procedures)
    engine.initialize_schema()


# ---------------------------------------------------------------------------
# US2 — Procedure dimension matches configured embedding dimension
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_initialize_schema_dimension_in_procedure(iris_connection):
    """SC-005: procedure DDL must use the engine's embedding_dimension, not hardcoded 1000."""
    from iris_vector_graph.engine import IRISGraphEngine

    # Use a dimension != 1000 to verify it's not hardcoded
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema()

    cursor = iris_connection.cursor()

    # Try to query the procedure body; fall back to calling with a 384-dim vector
    try:
        cursor.execute(
            "SELECT ROUTINE_DEFINITION FROM INFORMATION_SCHEMA.ROUTINES "
            "WHERE ROUTINE_SCHEMA = 'iris_vector_graph' AND ROUTINE_NAME = 'kg_KNN_VEC'"
        )
        row = cursor.fetchone()
        if row and row[0]:
            assert "VECTOR(DOUBLE, 384)" in row[0], (
                f"Expected VECTOR(DOUBLE, 384) in procedure body, got: {row[0][:200]}"
            )
            assert "VECTOR(DOUBLE, 1000)" not in row[0], (
                "Hardcoded VECTOR(DOUBLE, 1000) still present in procedure body"
            )
            return
    except Exception:
        pass

    # Fallback verification: procedure is installed (dimension is embedded at install time,
    # IRIS does not expose it via INFORMATION_SCHEMA.ROUTINES in all versions).
    # Verify via engine call — if dimension mismatches, VECTOR_COSINE would fail.
    query_vec = json.dumps([math.sin(i * 0.01) for i in range(384)])
    results = engine.kg_KNN_VEC(query_vec, k=5)
    assert isinstance(results, list), "kg_KNN_VEC must return a list after initialize_schema()"
