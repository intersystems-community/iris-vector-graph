"""Temporal edges can own a structural ``rdf_edges`` row (spec 246, opt-in).

``IRISGraphEngine(temporal_structural_rows=True)`` makes ``create_edge_temporal`` write one
structural row per triple through ``create_edge``, marked ``qualifiers={"temporal": True}``.
With the flag off, the SQL the engine emits is the same as before.

Everything here runs against fakes: a MagicMock cursor for the engine tests, and a small
in-memory database for the backfill tests. The live check is in
``tests/e2e/test_temporal_structural_row_live.py``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.migrations import temporal_structural_rows as backfill_mod
from iris_vector_graph.migrations.temporal_structural_rows import backfill_structural_rows
from iris_vector_graph.result import IVGResult


# ---------------------------------------------------------------------------
# Engine fakes
# ---------------------------------------------------------------------------


def _engine(monkeypatch, *, flag=None, strict=False, exists=False):
    """An engine on a MagicMock connection, with the store replaced by a MagicMock.

    ``flag=None`` leaves the constructor keyword out, so the default is what runs.
    """
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (1,) if exists else (0,)
    cursor.fetchall.return_value = []
    cursor.description = []
    kwargs = {"embedding_dimension": 4}
    if flag is not None:
        kwargs["temporal_structural_rows"] = flag
    eng = IRISGraphEngine(conn, **kwargs)
    eng._store = MagicMock()
    eng._store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error=None)
    eng._store.delete_temporal_edge.return_value = True
    eng._store.execute_temporal_window_query.return_value = IVGResult(columns=[], rows=[])
    if strict:
        guard = MagicMock()
        guard.is_strict.return_value = True
        eng._ledger_guard = guard
    monkeypatch.setattr(eng, "_iris_obj", lambda: MagicMock())
    return eng, cursor


def _statements(cursor, verb: str, table: str = "rdf_edges") -> list[tuple[str, list]]:
    out = []
    for c in cursor.execute.call_args_list:
        sql = c.args[0]
        if table in sql and sql.lstrip().upper().startswith(verb):
            params = c.args[1] if len(c.args) > 1 else []
            out.append((sql, list(params)))
    return out


def _inserts(cursor):
    return _statements(cursor, "INSERT")


def _deletes(cursor):
    return _statements(cursor, "DELETE")


def _selects(cursor):
    return _statements(cursor, "SELECT")


# ---------------------------------------------------------------------------
# T001: default off
# ---------------------------------------------------------------------------


def test_the_flag_defaults_to_off(monkeypatch):
    eng, _ = _engine(monkeypatch)
    assert eng._temporal_structural_rows is False


def test_flag_off_without_graph_writes_no_rdf_edges_row(monkeypatch):
    """Today's behaviour: the temporal globals and the shadow, no SQL row."""
    eng, cursor = _engine(monkeypatch, flag=False)
    create = MagicMock(return_value=True)
    monkeypatch.setattr(eng, "create_edge", create)

    assert eng.create_edge_temporal("a", "R", "b", timestamp=100) is True

    assert _inserts(cursor) == []
    assert not create.called


# ---------------------------------------------------------------------------
# T002 to T006: write path, flag on
# ---------------------------------------------------------------------------


def test_flag_on_without_graph_inserts_one_marked_row(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)

    assert eng.create_edge_temporal("a", "R", "b", timestamp=100) is True

    inserts = _inserts(cursor)
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert params[:3] == ["a", "R", "b"]
    assert json.loads(params[3]) == {"temporal": True}


def test_flag_on_passes_the_graph_through_and_writes_one_insert(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)

    eng.create_edge_temporal("a", "R", "b", timestamp=100, graph="acme")

    inserts = _inserts(cursor)
    assert len(inserts) == 1, "the legacy unmarked mirror must not run as well"
    _sql, params = inserts[0]
    assert params[:3] == ["a", "R", "b"]
    assert json.loads(params[3]) == {"temporal": True}
    assert params[4] == "acme"


def test_flag_on_leaves_an_existing_row_alone(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True, exists=True)

    assert eng.create_edge_temporal("a", "R", "b", timestamp=100) is True

    assert _inserts(cursor) == []


def test_flag_on_under_strict_ledger_writes_no_row(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True, strict=True)

    eng.create_edge_temporal("a", "R", "b", timestamp=100, graph="acme")

    assert _inserts(cursor) == []


def test_flag_on_writes_no_row_when_the_temporal_write_failed(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)
    eng._store.write_temporal_edge.return_value = IVGResult(
        columns=[], rows=[], error="boom"
    )

    assert eng.create_edge_temporal("a", "R", "b", timestamp=100) is False

    assert _inserts(cursor) == []


# ---------------------------------------------------------------------------
# T007 to T011: delete path
# ---------------------------------------------------------------------------

_MARKED = (json.dumps({"temporal": True}),)


def _window(rows):
    """A window read result: columns named as QueryWindow names them."""
    return IVGResult(
        columns=["s", "p", "o", "ts", "w"],
        rows=[["a", "R", t, ts, 1.0] for t, ts in rows],
    )


def test_delete_removes_the_marked_row_when_no_temporal_edge_survives(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)
    cursor.fetchone.return_value = _MARKED
    eng._store.execute_temporal_window_query.return_value = _window([])

    assert eng.delete_edge_temporal("a", "R", "b", timestamp=100) is True

    assert len(_deletes(cursor)) == 1


def test_delete_keeps_the_row_while_a_sibling_timestamp_survives(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)
    cursor.fetchone.return_value = _MARKED
    eng._store.execute_temporal_window_query.return_value = _window([("b", 200)])

    eng.delete_edge_temporal("a", "R", "b", timestamp=100)

    assert _deletes(cursor) == []


def test_delete_keeps_the_row_when_the_window_read_fails(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=True)
    cursor.fetchone.return_value = _MARKED
    eng._store.execute_temporal_window_query.return_value = IVGResult(
        columns=[], rows=[], error="read failed"
    )

    eng.delete_edge_temporal("a", "R", "b", timestamp=100)

    assert _deletes(cursor) == [], "an unknown survivor count must keep the row"


def test_delete_leaves_an_unmarked_row_alone(monkeypatch):
    """A row an explicit create_edge wrote has no marker and is not ours to remove."""
    eng, cursor = _engine(monkeypatch, flag=True)
    cursor.fetchone.return_value = (None,)

    eng.delete_edge_temporal("a", "R", "b", timestamp=100)

    assert _deletes(cursor) == []


def test_delete_with_flag_off_issues_no_rdf_edges_statement(monkeypatch):
    eng, cursor = _engine(monkeypatch, flag=False)
    cursor.fetchone.return_value = _MARKED

    eng.delete_edge_temporal("a", "R", "b", timestamp=100)

    assert _selects(cursor) == []
    assert _deletes(cursor) == []


# ---------------------------------------------------------------------------
# T012 to T015: backfill, against an in-memory database
# ---------------------------------------------------------------------------


class _Db:
    """Rows of rdf_edges and the temporal triples, keyed by graph ('' is the default)."""

    def __init__(self, edges=(), temporal=()):
        # edges: (s, p, o, graph, qualifiers-json-or-None)
        self.edges = [(s, p, o, g or "", q) for (s, p, o, g, q) in edges]
        # temporal: (graph, s, p, o)
        self.temporal = [(g or "", s, p, o) for (g, s, p, o) in temporal]
        self.statements: list[str] = []

    def write_count(self) -> int:
        return sum(
            1
            for sql in self.statements
            if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        )


class _Cursor:
    def __init__(self, db):
        self.db = db
        self._rows = []

    def execute(self, sql, params=None):
        self.db.statements.append(sql)
        text = " ".join(sql.split())
        if text.startswith("SELECT COUNT(*) FROM Graph_KG.rdf_edges"):
            self._rows = [(len(self.db.edges),)]
        elif text.startswith("SELECT s, p, o_id FROM Graph_KG.rdf_edges"):
            graph = (params[0] or "") if params else ""
            self._rows = [
                (s, p, o) for (s, p, o, g, _q) in self.db.edges if g == graph
            ]
        else:
            raise AssertionError(f"unexpected statement in the fake: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, db):
        self.db = db
        self.commits = 0

    def cursor(self):
        return _Cursor(self.db)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class _FakeEngine:
    """The two engine calls the backfill makes: the temporal read and the structural write."""

    def __init__(self, db):
        self.db = db
        self.conn = _Conn(db)
        self.window_graphs: list = []
        self.created: list = []

    def _t(self, name):
        return f"Graph_KG.{name}"

    def get_edges_in_window(self, source="", predicate="", start=0, end=0,
                            direction="out", graph=None):
        self.window_graphs.append(graph)
        want = graph or ""
        return [
            {"s": s, "p": p, "o": o, "ts": 100, "w": 1.0}
            for (g, s, p, o) in sorted(set(self.db.temporal))
            if g == want
        ]

    def create_edge(self, source_id, predicate, target_id, weight=1.0,
                    qualifiers=None, graph=None):
        self.created.append((source_id, predicate, target_id, qualifiers, graph))
        self.db.edges.append(
            (source_id, predicate, target_id, graph or "",
             json.dumps(qualifiers) if qualifiers else None)
        )
        return True


def _db_with_temporal_only():
    return _Db(
        edges=[("x", "R", "y", "", None)],  # an ordinary structural edge, unmarked
        temporal=[
            ("", "a", "R", "b"),   # temporal-only: needs a row
            ("", "a", "R", "c"),   # temporal-only: needs a row
            ("", "x", "R", "y"),   # already has a row: left alone
        ],
    )


def test_backfill_dry_run_writes_nothing_and_reports_the_plan():
    db = _db_with_temporal_only()
    eng = _FakeEngine(db)
    before = len(db.edges)

    report = backfill_structural_rows(eng.conn, engine=eng, graphs=(None,), dry_run=True)

    assert len(db.edges) == before, "rdf_edges row count changed in a dry run"
    assert report.rows_before == report.rows_after == before
    assert db.write_count() == 0
    assert eng.created == []
    assert report.planned == 2
    assert report.inserted == 0


def test_backfill_is_idempotent():
    db = _db_with_temporal_only()
    eng = _FakeEngine(db)

    first = backfill_structural_rows(eng.conn, engine=eng, graphs=(None,))
    after_first = len(db.edges)
    second = backfill_structural_rows(eng.conn, engine=eng, graphs=(None,))

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.planned == 0
    assert len(db.edges) == after_first


def test_backfill_leaves_existing_rows_unmarked():
    db = _db_with_temporal_only()
    eng = _FakeEngine(db)

    backfill_structural_rows(eng.conn, engine=eng, graphs=(None,))

    assert ("x", "R", "y", "", None) in db.edges
    assert not any(
        e[:3] == ("x", "R", "y") and e[4] is not None for e in db.edges
    ), "a row that already existed must not be re-marked"


def test_backfill_passes_the_graph_through_and_marks_inserted_rows():
    db = _Db(temporal=[("acme", "a", "R", "b")])
    eng = _FakeEngine(db)

    report = backfill_structural_rows(eng.conn, engine=eng, graphs=("acme",))

    assert eng.window_graphs == ["acme"]
    assert eng.created == [("a", "R", "b", {"temporal": True}, "acme")]
    assert report.inserted == 1


def test_backfill_module_exposes_the_marker_the_engine_writes():
    """The marker is defined once, in the temporal mixin, and the backfill reuses it."""
    from iris_vector_graph._engine.temporal import TEMPORAL_QUALIFIERS

    assert TEMPORAL_QUALIFIERS == {"temporal": True}
    assert backfill_mod.TEMPORAL_QUALIFIERS is TEMPORAL_QUALIFIERS
