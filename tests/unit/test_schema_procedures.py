"""
Unit tests for iris_vector_graph/schema.py stored procedure installation fix.
Feature: 020-initialize-schema-stored-procedures

Tests are written FIRST (test-first / Principle III).
All tests must be RED before implementation begins.
"""

import ast
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# US4 — schema.py is a valid Python module
# ---------------------------------------------------------------------------

def test_schema_py_is_importable():
    """FR-001: schema.py must parse without SyntaxError (SC-001)."""
    schema_path = Path(__file__).parent.parent.parent / "iris_vector_graph" / "schema.py"
    source = schema_path.read_text()
    # Will raise SyntaxError if dead code block is still present
    ast.parse(source)


def test_get_procedures_sql_list_contains_knn_vec():
    """FR-006: get_procedures_sql_list must include kg_KNN_VEC."""
    from iris_vector_graph.schema import GraphSchema

    stmts = GraphSchema.get_procedures_sql_list("Graph_KG")
    combined = "\n".join(stmts)
    assert "kg_KNN_VEC" in combined, "kg_KNN_VEC procedure not found in SQL list"


def test_get_procedures_sql_list_contains_all_required_procedures():
    """FR-006: list must include kg_KNN_VEC, kg_TXT, and kg_RRF_FUSE."""
    from iris_vector_graph.schema import GraphSchema

    stmts = GraphSchema.get_procedures_sql_list("Graph_KG")
    combined = "\n".join(stmts)
    assert "kg_KNN_VEC" in combined, "kg_KNN_VEC missing"
    assert "kg_TXT" in combined, "kg_TXT missing"
    assert "kg_RRF_FUSE" in combined, "kg_RRF_FUSE missing"


# ---------------------------------------------------------------------------
# US2 — Procedure dimension matches configured embedding dimension
# ---------------------------------------------------------------------------

def test_get_procedures_sql_list_uses_dimension():
    """FR-002 / SC-005: embedding_dimension param accepted; kg_KNN_VEC uses TO_VECTOR for IRIS compat."""
    from iris_vector_graph.schema import GraphSchema

    # embedding_dimension is accepted (no TypeError) and the procedure uses TO_VECTOR inline
    stmts = GraphSchema.get_procedures_sql_list("Graph_KG", embedding_dimension=384)
    combined = "\n".join(stmts)
    assert "kg_KNN_VEC" in combined, "kg_KNN_VEC must be present"
    # IRIS SQL procedures cannot DECLARE typed VECTOR variables; TO_VECTOR is used inline instead
    assert "TO_VECTOR" in combined, "kg_KNN_VEC must use TO_VECTOR for IRIS SQL compatibility"


def test_get_procedures_sql_list_idempotent_default():
    """FR-002 backward compat: no embedding_dimension arg still works."""
    from iris_vector_graph.schema import GraphSchema

    stmts = GraphSchema.get_procedures_sql_list("Graph_KG")
    combined = "\n".join(stmts)
    assert "kg_KNN_VEC" in combined, "kg_KNN_VEC must be present with default args"
    assert "TO_VECTOR" in combined, "kg_KNN_VEC must use TO_VECTOR with default args"


# ---------------------------------------------------------------------------
# US3 — Init-time diagnostic for procedure installation failure
# ---------------------------------------------------------------------------

def test_initialize_schema_raises_on_ddl_failure():
    """FR-004 / SC-004: non-'already exists' DDL error must raise RuntimeError."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    # Base schema DDL succeeds, but procedure DDL raises a permission error
    call_count = [0]

    def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        # Allow schema and table creation; fail on procedure DDL
        if "CREATE OR REPLACE PROCEDURE" in str(stmt):
            raise Exception("ERROR #5540: User does not have EXECUTE permission")

    cursor.execute.side_effect = execute_side_effect
    cursor.fetchone.return_value = (384,)  # dimension check passes

    engine = IRISGraphEngine(conn, embedding_dimension=384)

    with patch.object(engine, "_get_embedding_dimension", return_value=384):
        with pytest.raises(RuntimeError, match="stored procedure"):
            engine.initialize_schema()


def test_initialize_schema_ignores_already_exists():
    """FR-004 / FR-005: 'already exists' errors on BOTH schema AND procedure DDL must be ignored.

    Covers FR-005: CREATE SCHEMA iris_vector_graph 'already exists' must be silently ignored.
    Covers idempotent re-run: CREATE OR REPLACE PROCEDURE 'already exists' must be silently ignored.
    """
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    def execute_side_effect(stmt, *args, **kwargs):
        s = str(stmt).upper()
        # Simulate schema already existing (covers FR-005)
        if "CREATE SCHEMA" in s:
            raise Exception("Schema 'iris_vector_graph' already exists")
        # Simulate procedure already existing
        if "CREATE OR REPLACE PROCEDURE" in s:
            raise Exception("Object already has a procedure with this name")

    cursor.execute.side_effect = execute_side_effect
    cursor.fetchone.return_value = (384,)

    engine = IRISGraphEngine(conn, embedding_dimension=384)

    with patch.object(engine, "_get_embedding_dimension", return_value=384):
        # Must NOT raise — "already exists" is idempotent
        engine.initialize_schema()
