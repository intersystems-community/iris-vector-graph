"""A snapshot carries a FHIR graph's bookkeeping and puts it back (spec 232).

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_snapshot_fhir_tables.py

`fhir_unresolved`, `fhir_definitions` and `fhir_canonical_refs` were in the store
inventory and not in the snapshot plan, so a restore dropped them. The repository is
not in the archive, so they cannot be rebuilt from it.

No FHIR server is needed: the tables are created from their DDL, rows are written
straight into them, and the tables are dropped again afterwards if this test was the
one that created them (they are lazy, and `test_235_lazy_fhir_tables_e2e` expects a
namespace without a FHIR repository to have none).
"""

from __future__ import annotations

import contextlib
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "snapfhir-g"
TABLES = ("fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")

ROWS = {
    "fhir_unresolved": [
        {"graph_id": GRAPH, "source": "Observation/1", "param": "subject",
         "target": "Patient/x", "reason": "missing"},
        {"graph_id": GRAPH, "source": "Observation/2", "param": "subject",
         "target": "Patient/y", "reason": "external"},
    ],
    "fhir_definitions": [
        {"graph_id": GRAPH, "rsrc_key": "Library/1", "url": "http://x/a", "version": "1"},
        {"graph_id": GRAPH, "rsrc_key": "Library/2", "url": "http://x/b", "version": None},
    ],
    "fhir_canonical_refs": [
        {"graph_id": GRAPH, "source": "Measure/1", "param": "library", "url": "http://x/a",
         "version": None, "origin": "index", "kind": "canonical"},
        {"graph_id": GRAPH, "source": "Measure/2", "param": "library", "url": "http://x/b",
         "version": "2", "origin": "index", "kind": "canonical"},
    ],
}


def _existing(cur) -> set:
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
        " AND TABLE_NAME IN ('fhir_unresolved', 'fhir_definitions', 'fhir_canonical_refs',"
        " 'fhir_graphs')"
    )
    return {str(r[0]) for r in cur.fetchall()}


def _clear(cur) -> None:
    for t in TABLES:
        with contextlib.suppress(Exception):
            cur.execute(f"DELETE FROM Graph_KG.{t} WHERE graph_id = ?", [GRAPH])


def _counts(cur) -> dict:
    out = {}
    for t in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{t} WHERE graph_id = ?", [GRAPH])
        out[t] = cur.fetchone()[0]
    return out


@pytest.fixture()
def fhir_tables(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema
    from iris_vector_graph.utils import _split_sql_statements

    conn = iris_connection
    cur = conn.cursor()
    before = _existing(cur)
    for stmt in _split_sql_statements(GraphSchema.get_fhir_graph_schema_sql()):
        if stmt.strip():
            with contextlib.suppress(Exception):
                cur.execute(stmt)
    _clear(cur)
    for t, rows in ROWS.items():
        for row in rows:
            cols = list(row)
            cur.execute(
                f"INSERT INTO Graph_KG.{t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row[c] for c in cols],
            )
    conn.commit()
    try:
        yield IRISGraphEngine(conn), cur
    finally:
        _clear(cur)
        conn.commit()
        for t in ("fhir_canonical_refs", "fhir_definitions", "fhir_unresolved", "fhir_graphs"):
            if t not in before:
                with contextlib.suppress(Exception):
                    cur.execute(f"DROP TABLE Graph_KG.{t}")
        conn.commit()
        cur.close()


def test_the_snapshot_exports_the_fhir_bookkeeping(fhir_tables, tmp_path):
    import json
    import zipfile

    engine, _ = fhir_tables
    path = str(tmp_path / "s.zip")
    engine.save_snapshot(path, layers=["sql"])

    with zipfile.ZipFile(path) as zf:
        for t in TABLES:
            lines = zf.read(f"sql/Graph_KG_{t}.ndjson").decode().splitlines()
            ours = [json.loads(line) for line in lines if json.loads(line)["graph_id"] == GRAPH]
            assert len(ours) == 2, t


def test_a_merge_restore_puts_every_row_back(fhir_tables, tmp_path):
    """Both rows of the graph: the merge guard used to match on `graph_id` alone."""
    engine, cur = fhir_tables
    path = str(tmp_path / "s.zip")
    engine.save_snapshot(path, layers=["sql"])
    _clear(cur)
    engine.conn.commit()
    assert _counts(cur) == {t: 0 for t in TABLES}

    result = engine.restore_snapshot(path, merge=True)

    assert _counts(cur) == {t: 2 for t in TABLES}
    assert not {f"Graph_KG.{t}" for t in TABLES} & set(result.get("failed_rows") or {})

    # A second merge adds nothing.
    engine.restore_snapshot(path, merge=True)
    assert _counts(cur) == {t: 2 for t in TABLES}
