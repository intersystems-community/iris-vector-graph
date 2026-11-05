"""4.6.0 R207 (walkthrough 1, P6): `initialize_schema(dry_run=True)` says what a different `embedding_dimension` does.

`IRISGraphEngine(conn, embedding_dimension=384).initialize_schema(dry_run=True)` on a namespace set up at 768 returned
the same dictionary as at 768, though a real run alters every empty vector column to 384 and leaves a populated one at
768 (every write at 384 then refused). The dry run reports it under `embedding_dimension`: the configured width, the
tables it would alter, those already at it, and those it cannot change (`needs_manual_migration`), reading only the
catalog and row counts. A table that does not exist yet is not listed: setup creates it at the configured width.
"""

from __future__ import annotations

from iris_vector_graph.engine import IRISGraphEngine

NODE, OPT, EDGE = ("Graph_KG.kg_NodeEmbeddings", "Graph_KG.kg_NodeEmbeddings_optimized", "Graph_KG.kg_EdgeEmbeddings")


class _Cursor:
    """COUNT(*) per table (a table missing from `rows` does not exist); anything that writes fails the test."""

    def __init__(self, rows):
        self.rows = rows
        self._next = None

    def execute(self, sql, params=None):
        assert sql.upper().startswith("SELECT"), f"a dry run wrote: {sql}"
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self._next = (int(f"Graph_KG.{params[0]}" in self.rows),)
            return
        table = sql.rsplit(" ", 1)[-1]
        assert table in self.rows, f"counted a table that does not exist: {table}"
        self._next = (self.rows[table],)

    def fetchone(self):
        return self._next

    def fetchall(self):
        return [self._next]

    def close(self):
        pass


def _plan(monkeypatch, dims, rows, dim):
    from iris_vector_graph import schema as schema_mod

    monkeypatch.setattr(schema_mod.GraphSchema, "get_embedding_dimension",
                        staticmethod(lambda cur, table_name=NODE: dims.get(table_name)))
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = dim
    return engine._plan_vector_dimensions(_Cursor(rows), dim)


def test_a_different_width_on_empty_tables_would_alter_them(monkeypatch):
    plan = _plan(monkeypatch, {NODE: 768, OPT: 768, EDGE: 768}, {NODE: 0, OPT: 0, EDGE: 0}, 384)
    assert plan == {"configured": 384, "alter": [NODE, OPT, EDGE], "unchanged": [], "needs_manual_migration": []}


def test_a_populated_table_at_another_width_is_named_as_needing_a_person(monkeypatch):
    plan = _plan(monkeypatch, {NODE: 768, OPT: 768, EDGE: 768}, {NODE: 6, OPT: 0, EDGE: 0}, 384)
    assert plan["needs_manual_migration"] == [NODE]
    assert plan["alter"] == [OPT, EDGE]


def test_the_same_width_changes_nothing(monkeypatch):
    plan = _plan(monkeypatch, {NODE: 768, OPT: 768, EDGE: 768}, {NODE: 6, OPT: 0, EDGE: 0}, 768)
    assert plan == {"configured": 768, "alter": [], "unchanged": [NODE, OPT, EDGE], "needs_manual_migration": []}


def test_an_untyped_column_would_be_typed_and_a_missing_table_is_not_listed(monkeypatch):
    plan = _plan(monkeypatch, {NODE: None, EDGE: 768}, {NODE: 0, EDGE: 0}, 768)
    assert plan == {"configured": 768, "alter": [NODE], "unchanged": [EDGE], "needs_manual_migration": []}


def test_the_dry_run_reports_the_plan(monkeypatch):
    from iris_vector_graph._engine import legacy

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = 384

    class Conn:
        def cursor(self):
            return _Cursor({NODE: 0, OPT: 0, EDGE: 0})

    engine.conn = Conn()
    monkeypatch.setattr(IRISGraphEngine, "_iris_obj", lambda self: object(), raising=False)
    monkeypatch.setattr("iris_vector_graph.schema_name.read_record", lambda native: "Graph_KG")
    monkeypatch.setattr("iris_vector_graph.schema_name.refuse_package_clash", lambda conn, schema: None)
    monkeypatch.setattr(legacy, "present_legacy_objects", lambda conn, native: [])
    monkeypatch.setattr(legacy, "copy_grants", lambda conn, found, schema, dry_run: [])
    monkeypatch.setattr(legacy, "copy_ledger_history", lambda conn, schema, dry_run: {})
    monkeypatch.setattr(legacy, "grant_read_path", lambda *a, **k: [])  # R170's grant plan: not this test's subject
    monkeypatch.setattr(legacy, "arno_copies", lambda conn: [])  # R293's Arno plan: not this test's subject either
    planned = {"configured": 384, "alter": [NODE], "unchanged": [], "needs_manual_migration": []}
    monkeypatch.setattr(IRISGraphEngine, "_plan_vector_dimensions", lambda self, cur, dim: planned, raising=False)
    status = engine._record_and_initialize(True, True)
    assert status["dry_run"] is True
    assert status["embedding_dimension"] == planned
