"""Every routine the installer declares has to exist afterwards.

`_install_procedures` treats anything that is not `kg_KNN_VEC` as optional: the DDL
error becomes `logger.debug("Optional procedure DDL skipped (non-fatal)")` and
`initialize_schema` returns success. That is deliberate — `kg_TXT` and `kg_RRF_FUSE`
need the full-text feature — but it also means a *syntax* error in a shipped body is
invisible. `kg_Betweenness` shipped a `$SELECT(sampleSize>0:sampleSize, 1:200)`, which
the DDL parser rejects (`<PARAMETER ERROR> Parameter Name error, First value cannot be
a digit: 2`), so the function did not exist at all and every caller got
`SQLCODE -359 ... 'GRAPH_KG.KG_BETWEENNESS' does not exist`. Nothing failed.

This asserts the outcome instead of the install: read the names out of
`get_procedures_sql_list` and look each one up in `INFORMATION_SCHEMA.ROUTINES`.
"""

import os
import re

import pytest

from iris_vector_graph.schema import GraphSchema


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_DECL = re.compile(
    r"CREATE OR REPLACE (FUNCTION|PROCEDURE)\s+([\w]+)\.([\w]+)", re.IGNORECASE
)


def _declared_routines():
    """(schema, name) for everything the installer declares, in order."""
    out = []
    for stmt in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
        m = _DECL.search(stmt)
        if m:
            out.append((m.group(2), m.group(3)))
    return out


def _installed(conn):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT ROUTINE_SCHEMA, ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES"
        )
        rows = [(r[0].upper(), r[1].upper()) for r in cursor.fetchall()]
    finally:
        cursor.close()
    return set(rows)


def test_the_declared_list_is_not_empty():
    """A parsing slip here would make the real test below vacuous."""
    declared = _declared_routines()
    assert len(declared) >= 15, declared
    assert ("Graph_KG", "kg_Betweenness") in declared


def test_every_declared_routine_exists(engine):
    """`initialize_schema` has run via the fixture; each declared name must be there.

    `kg_KNN_VEC` is the one documented exception: against a 3.2.0-shaped
    `kg_NodeEmbeddings` the installer defers it on purpose and says which migration
    finishes the job, so a deferred install is not a defect.
    """
    installed = _installed(engine.conn)
    deferred = set()
    cursor = engine.conn.cursor()
    try:
        if engine._embeddings_await_migration(cursor):
            deferred.add(("GRAPH_KG", "KG_KNN_VEC"))
    finally:
        cursor.close()

    missing = [
        f"{schema}.{name}"
        for schema, name in _declared_routines()
        if (schema.upper(), name.upper()) not in installed
        and (schema.upper(), name.upper()) not in deferred
    ]
    assert missing == [], (
        "declared by get_procedures_sql_list and absent from the database — the "
        f"install failed and was logged at debug level: {missing}"
    )


def test_betweenness_is_callable_with_no_arguments(engine):
    """Existence is not enough: every argument has a DEFAULT, so `()` must work.

    The rewritten body computes its sample size with an `if`, and this is what pins
    that the default path still reaches `BetweennessGlobal`.

    The edges and the `sync()` are load-bearing: `BetweennessGlobal` returns the
    string `ERROR:^NKG not built` when `^NKG("$NI")` is empty, so on a container
    whose adjacency has not been built the call answers with a sentinel instead of
    a JSON array. Building it here means this test measures the default argument
    path rather than whichever earlier test happened to leave ^NKG populated.
    """
    import uuid

    ring = [f"shipped_btw_{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for src, dst in zip(ring, ring[1:] + ring[:1]):
        engine.create_edge(src, "KNOWS", dst)
    engine.sync()

    cursor = engine.conn.cursor()
    try:
        cursor.execute("SELECT Graph_KG.kg_Betweenness()")
        result = cursor.fetchone()[0]
    finally:
        cursor.close()
    assert result is not None
    # JSON array of {"id": ..., "score": ...}, empty when the graph has no nodes.
    assert str(result).strip().startswith("[")
