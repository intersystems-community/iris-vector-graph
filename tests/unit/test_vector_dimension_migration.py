"""The empty-table vector migration must cover every vector column it created.

Reported 2026-09-18 against 3.0.1. The block at `_engine/schema.py:183-241` had
three problems:

1. The `db_dim is None` branch ALTERed `kg_NodeEmbeddings` and
   `kg_NodeEmbeddings_optimized` and never `kg_EdgeEmbeddings`, so an untyped
   edge column stayed untyped.
2. The mismatch branch did ALTER the edge table, under a bare
   `except Exception: pass` — a failure there was indistinguishable from success.
3. It read one dimension (`get_embedding_dimension(cursor)`, node table only)
   and compared it against its own configured `dim`, so it could not see the
   edge column at all. With the per-table read fixed, each column is compared
   against the configured dimension on its own.

The row-count guard stays: ALTER on a populated vector column is not free, so a
non-empty table with the wrong width is reported loudly and left alone.
"""

import logging

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph._engine.schema import VECTOR_TABLES


class FakeCursor:
    def __init__(self, dims, row_counts=None, alter_errors=None):
        # dims: {qualified_table: dimension or None}
        self.dims = dims
        self.row_counts = row_counts or {}
        self.alter_errors = alter_errors or {}
        self.altered = []
        self.statements = []
        self._result = None

    def execute(self, sql, params=None):
        self.statements.append(sql)
        upper = sql.upper()
        if upper.startswith("ALTER TABLE"):
            table = sql.split()[2]
            if table in self.alter_errors:
                raise RuntimeError(self.alter_errors[table])
            self.altered.append(table)
            self._result = None
        elif "COUNT(*)" in upper:
            table = sql.rsplit(" ", 1)[-1]
            self._result = [self.row_counts.get(table, 0)]
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return [self._result] if self._result else []

    def close(self):
        pass


def _engine(monkeypatch, cursor, dims, dim=384):
    """An engine whose conn hands out `cursor` and whose dimension reader is `dims`."""

    class FakeConn:
        def cursor(self_inner):
            return cursor

        def commit(self_inner):
            pass

    from iris_vector_graph import schema as schema_mod

    monkeypatch.setattr(
        schema_mod.GraphSchema,
        "get_embedding_dimension",
        staticmethod(lambda cur, table_name="Graph_KG.kg_NodeEmbeddings": dims.get(table_name)),
    )
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = FakeConn()
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = dim
    return engine


def test_every_vector_table_is_covered():
    """The list the migration walks — the edge table must be in it."""
    assert "kg_NodeEmbeddings" in VECTOR_TABLES
    assert "kg_NodeEmbeddings_optimized" in VECTOR_TABLES
    assert "kg_EdgeEmbeddings" in VECTOR_TABLES


def test_untyped_edge_column_gets_altered(monkeypatch):
    """The `db_dim is None` case — the branch that used to skip the edge table."""
    dims = {
        "Graph_KG.kg_NodeEmbeddings": None,
        "Graph_KG.kg_NodeEmbeddings_optimized": None,
        "Graph_KG.kg_EdgeEmbeddings": None,
    }
    cur = FakeCursor(dims)
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert "Graph_KG.kg_EdgeEmbeddings" in cur.altered
    assert set(report["altered"]) == set(dims)


def test_edge_column_at_the_wrong_width_is_altered_while_node_column_is_left_alone(monkeypatch):
    """The reported production shape: node column right, edge column wrong.

    The old code read only the node dimension, found it equal to its own, and
    never looked further — which is why 1,099 rejected writes an hour were silent.
    """
    dims = {
        "Graph_KG.kg_NodeEmbeddings": 384,
        "Graph_KG.kg_NodeEmbeddings_optimized": 384,
        "Graph_KG.kg_EdgeEmbeddings": 768,
    }
    cur = FakeCursor(dims)
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert cur.altered == ["Graph_KG.kg_EdgeEmbeddings"]
    assert report["altered"] == ["Graph_KG.kg_EdgeEmbeddings"]
    assert "Graph_KG.kg_NodeEmbeddings" in report["unchanged"]


def test_a_populated_table_with_the_wrong_width_is_reported_not_altered(monkeypatch):
    dims = {
        "Graph_KG.kg_NodeEmbeddings": 384,
        "Graph_KG.kg_NodeEmbeddings_optimized": 384,
        "Graph_KG.kg_EdgeEmbeddings": 768,
    }
    cur = FakeCursor(dims, row_counts={"Graph_KG.kg_EdgeEmbeddings": 1099})
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert cur.altered == []
    assert "Graph_KG.kg_EdgeEmbeddings" in report["needs_manual_migration"]


def test_a_failed_edge_alter_is_recorded_and_logged(monkeypatch, caplog):
    """The bare `except Exception: pass` on the edge ALTER is what made a failed
    migration read as a successful one."""
    dims = {
        "Graph_KG.kg_NodeEmbeddings": 768,
        "Graph_KG.kg_NodeEmbeddings_optimized": 768,
        "Graph_KG.kg_EdgeEmbeddings": 768,
    }
    cur = FakeCursor(
        dims,
        alter_errors={"Graph_KG.kg_EdgeEmbeddings": "SQLCODE -400 cannot alter populated vector"},
    )
    engine = _engine(monkeypatch, cur, dims, dim=384)

    with caplog.at_level(logging.WARNING):
        report = engine._migrate_vector_dimensions(cur, 384)

    assert "Graph_KG.kg_EdgeEmbeddings" in report["failed"]
    assert "SQLCODE -400" in report["failed"]["Graph_KG.kg_EdgeEmbeddings"]
    assert "kg_EdgeEmbeddings" in caplog.text


def test_a_missing_optional_table_does_not_stop_the_others(monkeypatch):
    """`kg_NodeEmbeddings_optimized` is absent in DDL-only namespaces."""
    dims = {
        "Graph_KG.kg_NodeEmbeddings": 768,
        "Graph_KG.kg_NodeEmbeddings_optimized": None,
        "Graph_KG.kg_EdgeEmbeddings": 768,
    }
    cur = FakeCursor(
        dims,
        alter_errors={
            "Graph_KG.kg_NodeEmbeddings_optimized": "SQLCODE -30 table does not exist",
        },
    )
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert "Graph_KG.kg_NodeEmbeddings" in report["altered"]
    assert "Graph_KG.kg_EdgeEmbeddings" in report["altered"]
    assert "Graph_KG.kg_NodeEmbeddings_optimized" in report["failed"]


def test_the_migration_respects_the_schema_prefix(monkeypatch):
    """The old block hardcoded `Graph_KG.`, which is wrong in a prefixed namespace."""
    dims = {
        "IVG_KG.kg_NodeEmbeddings": None,
        "IVG_KG.kg_NodeEmbeddings_optimized": None,
        "IVG_KG.kg_EdgeEmbeddings": None,
    }
    cur = FakeCursor(dims)
    engine = _engine(monkeypatch, cur, dims, dim=384)
    engine._schema_prefix = "IVG_KG"

    engine._migrate_vector_dimensions(cur, 384)

    assert cur.altered == list(dims)


def test_nothing_happens_when_every_column_already_matches(monkeypatch):
    dims = {
        "Graph_KG.kg_NodeEmbeddings": 384,
        "Graph_KG.kg_NodeEmbeddings_optimized": 384,
        "Graph_KG.kg_EdgeEmbeddings": 384,
    }
    cur = FakeCursor(dims)
    engine = _engine(monkeypatch, cur, dims, dim=384)

    report = engine._migrate_vector_dimensions(cur, 384)

    assert cur.altered == []
    assert report["altered"] == []
    assert report["failed"] == {}
    assert set(report["unchanged"]) == set(dims)


@pytest.mark.parametrize("dim", [0, -1, None])
def test_a_missing_or_nonsense_dimension_is_refused(monkeypatch, dim):
    dims = {"Graph_KG.kg_NodeEmbeddings": 768}
    cur = FakeCursor(dims)
    engine = _engine(monkeypatch, cur, dims, dim=384)

    with pytest.raises(ValueError):
        engine._migrate_vector_dimensions(cur, dim)
