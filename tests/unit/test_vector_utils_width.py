"""`VectorOptimizer` must read the column's declared width, not assume 768.

Every method here formats `TO_VECTOR(?, DOUBLE, 768)` from a module constant, and IRIS
enforces the declared width at query open with `SQLCODE -257`. So against a namespace
declared at any other width — 384 is what `get_base_schema_sql()` produces by default —
each method fails and reports the failure as an absence:

* `check_hnsw_availability` answers `{'available': False}`, i.e. "HNSW is not available"
  for a table where it is,
* `migrate_to_optimized` compares `len(emb_array) != 768` and skips every row, then
  reports a successful migration of nothing,
* `benchmark_vector_search` files the error under `results['hnsw_error']` and reports the
  other leg's timings as the result.

The width belongs to the column, so it is read from the column.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.vector_utils import VectorOptimizer


def _conn(*, declared: int | None, count: int = 5, rows=None):
    """A connection whose embedding column is declared at `declared`.

    `GraphSchema.get_embedding_dimension` is left to run for real against this cursor: it
    reads `%Dictionary.CompiledProperty`, so the fake answers that shape and nothing else
    needs stubbing.
    """
    cursor = MagicMock()
    executed: list[tuple[str, object]] = []

    def execute(sql, params=None):
        executed.append((" ".join(str(sql).split()), params))
        upper = sql.upper()
        if "COMPILEDPROPERTY" in upper:
            cursor.fetchall.return_value = (
                [(f"MAXLEN,255,LEN,{declared},DATATYPE,double",)] if declared else []
            )
        elif "COMPILEDCLASS" in upper or "COMPILEDTABLE" in upper:
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = None
        elif "INFORMATION_SCHEMA" in upper:
            # `get_embedding_dimension` falls back here when the class dictionary has no
            # `emb`. Answering a row would hand back a width the column does not declare,
            # which is the exact confusion these tests exist to catch.
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = None
        elif "COUNT(*)" in upper:
            cursor.fetchone.return_value = (count,)
        else:
            cursor.fetchall.return_value = rows if rows is not None else []
            cursor.fetchone.return_value = (count,)
        return None

    cursor.execute.side_effect = execute

    # `migrate_to_optimized` drains with `while True: cursor.fetchmany(...)`, and a bare
    # MagicMock answers a truthy object forever — so hand out the rows once, then nothing.
    batches = [list(rows or [])] if rows else []

    def fetchmany(_size=None):
        return batches.pop(0) if batches else []

    cursor.fetchmany.side_effect = fetchmany

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.executed = executed
    return conn


def _vector_widths(conn) -> list[int]:
    """Every width this run asked `TO_VECTOR` for."""
    import re

    widths = []
    for sql, _ in conn.executed:
        for m in re.finditer(r"TO_VECTOR\([^)]*?,\s*DOUBLE\s*,\s*(\d+)\)", sql):
            widths.append(int(m.group(1)))
    return widths


@pytest.mark.parametrize("declared", [384, 1536])
def test_hnsw_probe_queries_at_the_declared_width(declared):
    conn = _conn(declared=declared)
    result = VectorOptimizer(conn).check_hnsw_availability()

    assert _vector_widths(conn) == [declared], (
        f"the probe must ask for the column's width, not a constant; "
        f"asked {_vector_widths(conn)}"
    )
    assert result["available"] is True


def test_hnsw_probe_says_so_when_the_column_has_no_declared_width():
    """No declared width is not 768, and it is not a healthy table either.

    `SQLCODE -260` is what IRIS answers for a vector column declared with no length, and
    guessing a width here would turn that into a wrong score instead of a report.
    """
    conn = _conn(declared=None)
    result = VectorOptimizer(conn).check_hnsw_availability()

    assert result["available"] is False
    assert "width" in result["reason"].lower() or "dimension" in result["reason"].lower(), (
        f"the reason must name the undeclared width, got {result['reason']!r}"
    )
    assert _vector_widths(conn) == [], "nothing may be queried at a guessed width"


def test_benchmark_queries_at_the_declared_width():
    conn = _conn(declared=384)
    results = VectorOptimizer(conn).benchmark_vector_search(iterations=1, k=3)

    assert "hnsw_error" not in results, results.get("hnsw_error")
    assert set(_vector_widths(conn)) == {384}, _vector_widths(conn)


def test_benchmark_generates_test_vectors_at_the_declared_width():
    """A random 768-long vector against a 384-wide column is `-257`, not a slow query."""
    conn = _conn(declared=384)
    VectorOptimizer(conn).benchmark_vector_search(iterations=2, k=3)

    for sql, params in conn.executed:
        if "TO_VECTOR" in sql and params:
            assert len(json.loads(params[0])) == 384, (
                f"query vector is {len(json.loads(params[0]))} long against a 384 column"
            )


def test_migration_keeps_rows_at_the_declared_width():
    """The length filter is against the target column, not against 768."""
    conn = _conn(declared=384, rows=[("gA", "N1", ",".join(["0.1"] * 384))])
    result = VectorOptimizer(conn).migrate_to_optimized(batch_size=10)

    assert result.get("migrated", result.get("migrated_count", 0)) == 1, result
