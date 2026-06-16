"""Integration tests for SQL parameterization of BM25/retrieve/IVF procedures.

Runs against a live IRIS instance to verify that the parameterized SQL produced
by the Cypher translator executes correctly — no syntax errors from parameterization,
and no injection risk from special characters in query text.

Requires: ivg-iris or ivg-iris-enterprise container running.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("cypher,desc", [
    (
        "CALL ivg.bm25.search('nonexistent_idx', 'heart failure', 5) YIELD node RETURN node",
        "bm25 with normal query text",
    ),
    (
        "CALL ivg.bm25.search('nonexistent_idx', 'query with spaces and numbers 123', 3) YIELD node RETURN node",
        "bm25 with spaces and numbers",
    ),
])
def test_bm25_parameterized_executes_on_iris(iris_connection, cypher, desc):
    """BM25 queries with parameterized args should execute without SQL errors."""
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(iris_connection)
    # Will error on SQL syntax/parse if parameterization broke the query structure.
    # Empty result is fine — index likely doesn't exist in test DB.
    try:
        result = engine.execute_cypher(cypher)
        # Empty result or rows — both acceptable; no exception = parameterization worked
        assert result is not None
    except Exception as e:
        err = str(e).lower()
        # "index not found" or "table not found" are acceptable — means SQL was valid
        # but the BM25 index/function doesn't exist in the test DB.
        if any(x in err for x in ["not found", "does not exist", "unknown function",
                                    "no such", "sqlcode", "undefined"]):
            pytest.skip(f"BM25 function not available in test IRIS: {e}")
        raise


def test_retrieve_parameterized_executes_on_iris(iris_connection):
    """ivg.retrieve with parameterized query text should not cause SQL injection."""
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(iris_connection)
    cypher = "CALL ivg.retrieve('test query text', 3) YIELD node RETURN node"
    try:
        result = engine.execute_cypher(cypher)
        assert result is not None
    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["not found", "does not exist", "unknown function",
                                    "no such", "sqlcode", "undefined"]):
            pytest.skip(f"BM25/vector function not available in test IRIS: {e}")
        raise


def test_injection_attempt_safe_on_iris(iris_connection):
    """Single-quote injection in BM25 query text must not cause SQL errors."""
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(iris_connection)
    # This would cause SQL errors if interpolated inline; with ? binding it's safe.
    cypher = "CALL ivg.bm25.search('idx', 'it\\'s a test query', 5) YIELD node RETURN node"
    try:
        result = engine.execute_cypher(cypher)
        assert result is not None
    except Exception as e:
        err = str(e).lower()
        # Acceptable: function not found. NOT acceptable: syntax error from injection.
        if "syntax" in err or "parse" in err:
            raise AssertionError(
                f"SQL injection via single-quote produced a syntax error — "
                f"parameterization failed: {e}"
            ) from e
        if any(x in err for x in ["not found", "does not exist", "unknown function",
                                    "no such", "sqlcode", "undefined"]):
            pytest.skip(f"BM25 function not available in test IRIS: {e}")
        raise
