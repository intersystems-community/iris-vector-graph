"""
Unit tests for iris_vector_graph/schema.py stored procedure installation fix.
Feature: 020-initialize-schema-stored-procedures

Tests are written FIRST (test-first / Principle III).
All tests must be RED before implementation begins.

Spec 226 (US4) adds the sections at the end of this file: the `embedding_dimension`
parameter is deprecated rather than completed, for the reason measured in ADR-0005.
"""

import ast
import os
import re
import warnings
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
    """FR-002 / SC-005: embedding_dimension param accepted; kg_KNN_VEC uses TO_VECTOR for IRIS compat.

    Still accepted, and since spec 226 also deprecated — passing it warns and changes
    nothing. The warning is suppressed here because this test is about acceptance; the
    deprecation itself is asserted in `TestEmbeddingDimensionParameterIsDeprecated`.
    """
    from iris_vector_graph.schema import GraphSchema

    # embedding_dimension is accepted (no TypeError) and the procedure uses TO_VECTOR inline
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
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


def _wire_cursor(cursor, execute_side_effect):
    """A mock cursor that answers every query with `(384,)`, as these tests always have.

    Since spec 226 `initialize_schema` also reads `Graph_KG.embedding_registry`, whose
    SELECT names five columns. A one-tuple is not a registry row, and
    `get_embedding_identity` reads anything it cannot interpret as "nothing recorded" —
    which is the state these tests describe, so the terse mock stays truthful enough.
    """
    cursor.execute.side_effect = execute_side_effect
    cursor.fetchone.return_value = (384,)


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

    _wire_cursor(cursor, execute_side_effect)

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

    _wire_cursor(cursor, execute_side_effect)

    engine = IRISGraphEngine(conn, embedding_dimension=384)

    with patch.object(engine, "_get_embedding_dimension", return_value=384):
        # Must NOT raise — "already exists" is idempotent
        engine.initialize_schema()


# ---------------------------------------------------------------------------
# US4 (spec 226) — the inert embedding_dimension parameter is deprecated
# ---------------------------------------------------------------------------


def _procedures(**kwargs):
    from iris_vector_graph.schema import GraphSchema

    return GraphSchema.get_procedures_sql_list(**kwargs)


def _knn_vec_statement() -> str:
    """The generated `kg_KNN_VEC` DDL, on its own."""
    for stmt in _procedures(table_schema="Graph_KG"):
        if "PROCEDURE Graph_KG.kg_KNN_VEC" in stmt:
            return stmt
    raise AssertionError("kg_KNN_VEC is not in the generated procedure list")


class TestEmbeddingDimensionParameterIsDeprecated:
    """T035 / FR-016: the parameter is accepted, warns, and does nothing.

    Deprecating rather than deleting is deliberate (ADR-0005): a caller who passes it
    today already gets the behaviour they get after the change, so removing it in a minor
    release would break callers for no behavioural gain. Removal is 4.0.0.
    """

    def test_passing_it_warns(self):
        with pytest.warns(DeprecationWarning, match="embedding_dimension"):
            _procedures(table_schema="Graph_KG", embedding_dimension=768)

    def test_the_warning_says_where_the_width_does_belong(self):
        """A bare "deprecated" invites someone to wire it in properly instead. The
        warning has to say the width is not wanted here, not merely not wanted yet."""
        with pytest.warns(DeprecationWarning) as caught:
            _procedures(table_schema="Graph_KG", embedding_dimension=768)
        message = str(caught[0].message)
        assert "ADR-0005" in message
        assert "4.0.0" in message

    def test_omitting_it_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            _procedures(table_schema="Graph_KG")

    def test_the_generated_sql_is_byte_identical_whatever_is_passed(self):
        """The point of the deprecation: the value has never reached SQL. Comparing
        against a nonsense width as well as the real default makes that concrete —
        if anyone wires the parameter in, this fails rather than shipping quietly."""
        omitted = _procedures(table_schema="Graph_KG")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            at_default = _procedures(table_schema="Graph_KG", embedding_dimension=768)
            at_nonsense = _procedures(table_schema="Graph_KG", embedding_dimension=17)

        assert at_default == omitted
        assert at_nonsense == omitted

    def test_the_engine_does_not_pass_it(self):
        """`initialize_schema` passed the configured width on every run. Left alone, the
        deprecation would fire on a path no caller can fix, which trains people to filter
        the warning out."""
        source = (
            Path(__file__).resolve().parents[2]
            / "iris_vector_graph"
            / "_engine"
            / "schema.py"
        ).read_text()
        call_site = source[source.index("GraphSchema.get_procedures_sql_list") :][:300]
        assert "embedding_dimension" not in call_site


class TestKnnVecLeavesTheQueryVectorUnlengthed:
    """T036 / FR-017: pins the measured behaviour behind ADR-0005.

    `TO_VECTOR(:q, DOUBLE, n)` pads or truncates the query vector to `n` and then scores
    against the reshaped value — a 6-element query truncated to 4 returned `1.0`. The
    unlengthed form raises `SQLCODE -257` instead. The loud error is the feature.
    """

    def test_query_conversion_is_the_two_argument_form(self):
        assert "TO_VECTOR(:queryInput, DOUBLE)" in _knn_vec_statement()

    def test_no_three_argument_to_vector_anywhere_in_the_procedures(self):
        combined = "\n".join(_procedures(table_schema="Graph_KG"))
        three_arg = re.findall(r"TO_VECTOR\s*\([^()]*,[^(),]*,[^()]*\)", combined)
        assert not three_arg, f"a width was declared inside TO_VECTOR: {three_arg}"

    def test_still_declares_four_parameters(self):
        """The signature is load-bearing even though one parameter is ignored:
        `kg_RRF_FUSE` calls `kg_KNN_VEC` with four arguments, so dropping
        `embeddingConfig` breaks the caller. It goes in 4.0.0, with that call site."""
        stmt = _knn_vec_statement()
        signature = stmt[stmt.index("kg_KNN_VEC(") : stmt.index("LANGUAGE SQL")]
        params = re.findall(r"\bIN\s+(\w+)", signature)
        assert params == ["queryInput", "k", "labelFilter", "embeddingConfig"]

    def test_rrf_fuse_still_calls_it_with_four_arguments(self):
        combined = "\n".join(_procedures(table_schema="Graph_KG"))
        assert "kg_KNN_VEC(:queryVector, :k1, NULL, NULL)" in combined


# ---------------------------------------------------------------------------
# Spec 226 — one policy for an unreadable registry row
# ---------------------------------------------------------------------------


class _StubCursor:
    """A cursor that returns one fixed row. A hand-written stub, not a MagicMock: an
    attribute this class does not have is an AttributeError, which is the point."""

    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return self._row

    def close(self):
        return None


class _StubConnection:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _StubCursor(self._row)


class TestUnreadableRegistryRow:
    """`get_embedding_identity` already reports a failed read as "nothing recorded".
    A read that succeeds with a shape it cannot interpret gets the same answer, so the
    method has one contract rather than two. The alternative — unpacking and raising —
    fails `initialize_schema` over a row that a fixed five-column SELECT against a real
    registry table cannot produce.
    """

    def _engine(self, row):
        from iris_vector_graph.engine import IRISGraphEngine

        return IRISGraphEngine(_StubConnection(row), embedding_dimension=384)

    def test_a_row_of_the_wrong_arity_reads_as_nothing_recorded(self):
        assert self._engine((384,)).get_embedding_identity("kg_NodeEmbeddings") is None

    def test_no_row_reads_as_nothing_recorded(self):
        assert self._engine(None).get_embedding_identity("kg_NodeEmbeddings") is None

    def test_a_five_value_row_is_still_read(self):
        identity = self._engine(
            ("iris-embedding-config", "my-model", "my-model", 384, "DOUBLE")
        ).get_embedding_identity("kg_NodeEmbeddings")
        assert identity is not None
        assert identity.model_key == "my-model"
        assert identity.dimension == 384
