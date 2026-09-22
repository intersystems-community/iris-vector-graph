"""Spec 227 T012 — a recorded width follows its column, always (FR-031).

The bug, open since spec 226 and recorded in `docs/KNOWN_ISSUES.md`: the registry's
width is carried forward only for tables *this* call altered.

    for name in altered_names:
        self._sync_recorded_dimension(name, dim)
        # iris_vector_graph/_engine/schema.py:751-752

Two writers is enough to break it. Writer A alters `kg_NodeEmbeddings` from 384 to
768 and updates its own registry row. Writer B, holding a registry row it wrote at
384, alters nothing on its next `initialize_schema` — the column is already 768 —
so its loop is empty, the stale 384 row survives, and the next write is refused by
`EmbeddingIdentityConflict` naming a width no column has. The column declaration is
the truth about width (FR-006), so reconciliation has to be unconditional rather
than a side effect of having altered something.

These tests pin the reconciliation as its own operation, reading the column and
writing the registry, for every embedding table on every schema initialisation.
"""

import pytest

from iris_vector_graph._engine.schema import VECTOR_TABLES
from iris_vector_graph.engine import IRISGraphEngine


class FakeCursor:
    """Records statements; answers COUNT(*) as 0 and swallows ALTER."""

    def __init__(self, alter_errors=None):
        self.statements = []
        self.altered = []
        self.alter_errors = alter_errors or {}
        self._result = None

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        upper = sql.upper()
        if upper.startswith("ALTER TABLE"):
            table = sql.split()[2]
            if table in self.alter_errors:
                raise RuntimeError(self.alter_errors[table])
            self.altered.append(table)
            self._result = None
        elif "COUNT(*)" in upper:
            self._result = [0]
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return [self._result] if self._result else []

    def close(self):
        pass


def _engine(monkeypatch, cursor, dims, dim=384):
    class FakeConn:
        def cursor(self_inner):
            return cursor

        def commit(self_inner):
            pass

    from iris_vector_graph import schema as schema_mod

    monkeypatch.setattr(
        schema_mod.GraphSchema,
        "get_embedding_dimension",
        staticmethod(
            lambda cur, table_name="Graph_KG.kg_NodeEmbeddings": dims.get(table_name)
        ),
    )
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = FakeConn()
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = dim
    return engine


@pytest.fixture
def synced(monkeypatch):
    """Capture every `_sync_recorded_dimension(table, dim)` call."""
    calls = []
    monkeypatch.setattr(
        IRISGraphEngine,
        "_sync_recorded_dimension",
        lambda self, table_name, dimension: calls.append((table_name, dimension)),
        raising=True,
    )
    return calls


def _all_at(dim):
    return {f"Graph_KG.{t}": dim for t in VECTOR_TABLES}


# --- the reconciliation is its own operation ---------------------------------


def test_reconcile_covers_every_embedding_table(monkeypatch, synced):
    dims = _all_at(768)
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=768)

    engine.reconcile_recorded_dimensions(cur)

    assert {t for t, _ in synced} == set(VECTOR_TABLES), (
        "reconciliation must visit every embedding table, not only altered ones"
    )


def test_reconcile_uses_the_column_width_not_the_configured_one(monkeypatch, synced):
    """The column declaration is the truth (FR-006).

    An engine configured for 384 that finds a 768 column must record 768 — the
    column is what the next INSERT is checked against.
    """
    dims = _all_at(768)
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=384)

    engine.reconcile_recorded_dimensions(cur)

    assert synced, "nothing was reconciled"
    assert all(d == 768 for _, d in synced), synced


def test_reconcile_skips_a_column_with_no_declared_width(monkeypatch, synced):
    """`None` means the catalog has no width to copy; inventing one is worse than
    leaving the row alone, and `-260` already tells the caller the column is
    undeclared."""
    dims = {f"Graph_KG.{t}": None for t in VECTOR_TABLES}
    dims["Graph_KG.kg_NodeEmbeddings"] = 384
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=384)

    engine.reconcile_recorded_dimensions(cur)

    assert [t for t, _ in synced] == ["kg_NodeEmbeddings"], synced


def test_reconcile_reports_what_it_changed(monkeypatch, synced):
    dims = _all_at(512)
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=512)

    report = engine.reconcile_recorded_dimensions(cur)

    assert isinstance(report, dict)
    assert report == {t: 512 for t in VECTOR_TABLES}


# --- and the migration no longer gates it on altered_names -------------------


def test_migration_reconciles_a_table_it_did_not_alter(monkeypatch, synced):
    """The second-writer case, stated as a test.

    Every column is already at the configured width, so `altered_names` is empty.
    The old code reconciled nothing here; that is exactly when a stale row from
    another writer survives.
    """
    dims = _all_at(384)
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert report["altered"] == [], "fixture is wrong — nothing should need altering"
    assert {t for t, _ in synced} == set(VECTOR_TABLES), (
        "an unaltered table still needs its recorded width reconciled (FR-031)"
    )


def test_migration_reconciles_even_when_an_alter_failed(monkeypatch, synced):
    """A table that could not be altered keeps whatever width it has, and the
    registry must describe *that* width rather than the one the ALTER wanted."""
    dims = _all_at(768)
    cur = FakeCursor(
        alter_errors={"Graph_KG.kg_NodeEmbeddings_optimized": "no such table"}
    )
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert "Graph_KG.kg_NodeEmbeddings_optimized" in report["failed"]
    assert "kg_NodeEmbeddings_optimized" in {t for t, _ in synced}, (
        "the failed table's recorded width must still match its column"
    )


def test_migration_still_reconciles_the_tables_it_did_alter(monkeypatch, synced):
    dims = _all_at(768)
    cur = FakeCursor()
    engine = _engine(monkeypatch, cur, dims, dim=384)

    engine._migrate_vector_dimensions(cur, 384)

    assert {t for t, _ in synced} == set(VECTOR_TABLES)
