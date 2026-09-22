"""Spec 230 US5 — the shared legacy embedding table keeps the session's width.

`tests/conftest.py` bootstraps the session namespace with
`IRISGraphEngine(conn, embedding_dimension=768).initialize_schema(...)`, and most
of the e2e and integration suite assumes that width for
`Graph_KG.kg_NodeEmbeddings`.

`initialize_schema()` alters the `emb` declaration when the table is **empty**, so
a test that builds an engine at a different width against the *shared* connection
and initializes it narrows the column for every test that follows. That happened:
the column stood at `VECTOR(DOUBLE, 4)` holding 200 rows, and every later run
logged

    Graph_KG.kg_NodeEmbeddings.emb is VECTOR(DOUBLE, 4) but the engine is
    configured for 768, and the table is not empty (200 rows)

from `_engine/schema.py`. Once the table is non-empty the engine refuses to widen
it — correctly, since it cannot invent the missing dimensions — so the pollution
is sticky and every vector test after it is measuring a narrowed column.

A non-768 width is legitimate in plenty of live tests: spec 227 routed tables are
separate physical tables and carry their own widths, and a few tests exercise the
width migration itself. The invariant is specifically about the one shared legacy
table, so this test measures its declaration rather than scanning for
`embedding_dimension=` spellings, which would flag the cases that are fine.
`tests/conftest.py` restores the width at session end and reports having done so,
which catches a polluter that runs after this file.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS=true",
)

SESSION_EMBEDDING_DIM = 768


def test_legacy_node_embedding_table_is_at_the_session_width(iris_connection):
    """A narrowed `emb` means an earlier test re-declared the shared table."""
    from iris_vector_graph.schema import GraphSchema

    cur = iris_connection.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cur.close()

    if width is None:
        pytest.skip("Graph_KG.kg_NodeEmbeddings has no declared emb width here")

    assert width == SESSION_EMBEDDING_DIM, (
        f"Graph_KG.kg_NodeEmbeddings.emb is VECTOR(DOUBLE, {width}) but conftest "
        f"bootstrapped the session at {SESSION_EMBEDDING_DIM}, which the vector "
        "suite assumes. Some earlier test built an engine at another width on the "
        "shared connection and called initialize_schema() while the table was "
        "empty. Give that test its own routed table, or use the session width."
    )


def test_the_engine_does_not_report_a_width_mismatch(iris_connection):
    """The engine's own reconciliation agrees, so nothing needs a manual migration."""
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
    status = engine.initialize_schema(auto_deploy_objectscript=False)

    migration = (status or {}).get("needs_manual_migration") or []
    offenders = [m for m in migration if "kg_NodeEmbeddings" in str(m)]
    assert not offenders, (
        "initialize_schema reports the shared embedding table needs a manual "
        f"migration, so its column and the session width disagree: {offenders}"
    )


def test_a_test_may_narrow_the_shared_table(iris_connection):
    """Narrow it on purpose. The next test proves the guard put it back.

    These two run in file order, which is the order the guard has to survive: the
    damage and its victim are different tests, and before 4.0.0 the only restore
    happened at session teardown — after every victim had already failed.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    narrow = 64
    assert narrow != SESSION_EMBEDDING_DIM

    cur = iris_connection.cursor()
    try:
        # `initialize_schema` only re-declares the column while the table is empty —
        # which is exactly the state most of the suite leaves it in.
        cur.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings")
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cur.close()

    IRISGraphEngine(iris_connection, embedding_dimension=narrow).initialize_schema(
        auto_deploy_objectscript=False
    )

    cur = iris_connection.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings")
    finally:
        with contextlib.suppress(Exception):
            cur.close()
    assert width == narrow, (
        f"this test needs the shared table narrowed to prove the guard restores it, "
        f"and the column is VECTOR(DOUBLE, {width})"
    )


def test_the_next_test_finds_the_shared_table_back_at_the_session_width(iris_connection):
    """The test above left the column at 64. Nothing in this test repairs it."""
    from iris_vector_graph.schema import GraphSchema

    cur = iris_connection.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings")
    finally:
        with contextlib.suppress(Exception):
            cur.close()

    assert width == SESSION_EMBEDDING_DIM, (
        f"the previous test narrowed Graph_KG.kg_NodeEmbeddings.emb to "
        f"VECTOR(DOUBLE, {width}) and the per-test guard in tests/conftest.py did not "
        "restore it, so every vector test after it is measuring a narrowed column"
    )


def test_initialize_schema_reports_a_table_that_needs_a_manual_migration(iris_connection):
    """A width the engine cannot fix has to reach the caller, not just the log.

    `_migrate_vector_dimensions` builds a `needs_manual_migration` list for every
    vector table whose declared width disagrees with the configured one while the
    table holds rows — the case the engine deliberately refuses to fix, because
    widening cannot invent the missing dimensions. `initialize_schema` called it
    and discarded the return value, so the only report was a `logger.error` and
    the returned status still read as a clean setup. A caller checking the status
    could not tell that every configured-width write to that table would be
    rejected with SQLCODE -104.

    The report is faked here rather than by narrowing the shared table: the
    condition is sticky by design (the engine will not widen a populated column
    back), so producing it for real would leave the namespace broken for every
    test after this one — which is the defect the first test in this file guards.
    """
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
    faked = "Graph_KG.kg_NodeEmbeddings"

    def _fake_migrate(cursor, dim):
        return {
            "altered": [],
            "unchanged": [],
            "needs_manual_migration": [faked],
            "failed": {},
        }

    engine._migrate_vector_dimensions = _fake_migrate  # type: ignore[method-assign]
    status = engine.initialize_schema(auto_deploy_objectscript=False)

    assert faked in (status.get("needs_manual_migration") or []), (
        "initialize_schema dropped the migration report, so a table that rejects "
        f"every write is invisible in the returned status: {status.keys()}"
    )
    assert any(faked in w for w in status.get("warnings", [])), (
        "a table needing a manual migration is a setup warning, and the status "
        f"warnings do not mention it: {status.get('warnings')}"
    )
