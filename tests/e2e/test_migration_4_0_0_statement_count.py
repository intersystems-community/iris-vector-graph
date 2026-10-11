"""The 4.0.0 upgrade places rows in batches, not one statement per row.

Mindwalk (production-size copy: 206K node vectors, 448K docs, 299K edge vectors) measured an 82 minute
dry run and an apply killed after 9 hours. The cost was one SELECT, INSERT..SELECT or INSERT+DELETE per
row. This pins the shape of the fix on a fixture of a few thousand rows: the number of statements the
whole upgrade sends grows with the number of batches, and the database ends in the state the row-by-row
path produced.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_upgrade_dry_run_2x import (
    SCHEMA,
    _connect,
    _ensure_namespace,
    _quietly,
    _wipe,
)

pytestmark = pytest.mark.e2e

NODES = 1500
ORPHAN_DOCS = 40
EDGE_VECTORS = 600
DIM = 8
# One batch is at most a few hundred rows; the fixed overhead of the upgrade (DDL, catalog reads,
# renames) is under a hundred statements. A per-row path would send at least NODES statements.
STATEMENT_BUDGET = 250


class CountingConnection:
    def __init__(self, conn):
        self._conn = conn
        self.statements = 0

    def cursor(self):
        return _CountingCursor(self._conn.cursor(), self)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _CountingCursor:
    def __init__(self, cursor, owner):
        self._cursor = cursor
        self._owner = owner

    def execute(self, *a, **k):
        self._owner.statements += 1
        return self._cursor.execute(*a, **k)

    def executemany(self, sql, seq):
        seq = list(seq)
        self._owner.statements += len(seq)
        return self._cursor.executemany(sql, seq)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


@pytest.fixture(scope="module")
def legacy():
    pytest.importorskip("iris")
    from tests.e2e.fixtures.old_releases import OLD_RELEASES

    _ensure_namespace()
    try:
        conn = _connect()
    except Exception as exc:
        pytest.skip(f"IVGLEGACY not reachable: {exc}")
    release = [r for r in OLD_RELEASES if r.tag == "v3.2.0"][0]
    _wipe(conn)
    cursor = conn.cursor()
    for statement in release.ddl_statements():
        _quietly(cursor, statement)
    vec = ",".join(["0.1"] * DIM)
    for start in range(0, NODES, 500):
        ids = range(start, min(start + 500, NODES))
        cursor.executemany(f"INSERT INTO {SCHEMA}.nodes (node_id, graph_id) VALUES (?, ?)", [(f"n{i}", "") for i in ids])
        cursor.executemany(
            f"INSERT INTO {SCHEMA}.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))",
            [(f"n{i}", vec) for i in ids],
        )
        cursor.executemany(f"INSERT INTO {SCHEMA}.docs (id, text) VALUES (?, ?)", [(f"n{i}", "t") for i in ids])
    cursor.executemany(
        f"INSERT INTO {SCHEMA}.docs (id, text) VALUES (?, ?)", [(f"orphan{i}", "x") for i in range(ORPHAN_DOCS)]
    )
    _quietly(cursor, f"ALTER TABLE {SCHEMA}.kg_EdgeEmbeddings ALTER COLUMN emb VECTOR(DOUBLE, {DIM})")
    cursor.executemany(
        f"INSERT INTO {SCHEMA}.kg_EdgeEmbeddings (s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR(?, DOUBLE))",
        [(f"e{i}", "R", f"e{i + 1}", vec) for i in range(EDGE_VECTORS)],
    )
    conn.commit()
    # What a consumer does after pip-upgrading: initialize_schema adds the quarantine tables and the
    # new columns, and leaves the legacy tables for upgrade_to_4_0_0 to re-shape.
    from iris_vector_graph.engine import IRISGraphEngine

    IRISGraphEngine(conn, embedding_dimension=DIM).initialize_schema(auto_deploy_objectscript=False)
    return conn


@pytest.fixture(scope="module")
def upgraded(legacy):
    from iris_vector_graph.migrations.upgrade import upgrade_to_4_0_0

    counting = CountingConnection(legacy)
    report = upgrade_to_4_0_0(counting, steps=["embeddings", "docs", "edge_vectors"])
    return counting.statements, report


def _count(conn, sql, params=None):
    cursor = conn.cursor()
    cursor.execute(sql, params or [])
    return int(cursor.fetchone()[0])


def test_the_upgrade_sends_batches_not_a_statement_per_row(upgraded):
    statements, _ = upgraded
    assert statements < STATEMENT_BUDGET, f"{statements} statements for {NODES} vectors: still per-row"


def test_every_node_vector_is_placed_in_the_default_graph(legacy, upgraded):
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.kg_NodeEmbeddings WHERE graph_id = ''") == NODES
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.kg_NodeEmbeddings") == NODES


def test_documents_with_a_node_are_placed_and_orphans_are_quarantined_with_their_text(legacy, upgraded):
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.docs") == NODES
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.docs WHERE graph_id = ''") == NODES
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.docs_quarantine WHERE reason = 'no_node'") == ORPHAN_DOCS
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.docs_quarantine WHERE text = 'x'") == ORPHAN_DOCS


def test_edge_vectors_no_edge_asserts_are_quarantined(legacy, upgraded):
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.edge_vector_quarantine WHERE reason = 'no_edge'") == EDGE_VECTORS
    assert _count(legacy, f"SELECT COUNT(*) FROM {SCHEMA}.kg_EdgeEmbeddings") == 0


def test_the_report_accounts_for_every_row(upgraded):
    _, report = upgraded
    assert report["docs"].report.rows_placed == {"": NODES}
    assert report["docs"].report.rows_quarantined == {"no_node": ORPHAN_DOCS}
    assert report["edge_vectors"].report.rows_quarantined == {"no_edge": EDGE_VECTORS}
