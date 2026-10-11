"""Embedded SQL treats SQLCODE 100 as an empty result; EmbeddedIRIS covers the Native calls migrations use.

Reported by Mindwalk against 4.2.0 (irispython, Build 154): an UPDATE/DELETE matching no row raised
``SQLError`` with sqlcode 100 and an empty message, which stopped ``upgrade_to_4_0_0`` at its first
statement; ``kg_node_stores`` also needed ``nextSubscript``, ``getString`` and ``kill``.
"""

from __future__ import annotations

import types

import pytest

from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedIRIS


class SQLError(Exception):
    def __init__(self, sqlcode, message=""):
        super().__init__(message)
        self.sqlcode = sqlcode


class Stmt:
    def __init__(self, error):
        self.error = error

    def execute(self, *params):
        if self.error:
            raise self.error
        return None


class FakeSql:
    def __init__(self, error=None):
        self.error = error
        self.exec_calls = []

    def prepare(self, sql):
        return Stmt(self.error)

    def exec(self, sql):
        self.exec_calls.append(sql)
        if self.error:
            raise self.error


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_a_statement_that_matches_no_row_is_an_empty_result(verb):
    cur = EmbeddedConnection(FakeSql(SQLError(100))).cursor()

    cur.execute(f"{verb} Graph_KG.rdf_labels SET label = ? WHERE 1 = 0", ["x"])

    assert cur.rowcount == 0
    assert cur.description is None
    assert cur.fetchall() == []
    assert cur.fetchone() is None


def test_the_empty_inlined_form_is_empty_too():
    cur = EmbeddedConnection(FakeSql(SQLError(100))).cursor()

    cur.execute("DELETE FROM Graph_KG.nodes WHERE graph_id = ?", [""])

    assert cur.rowcount == 0


def test_executemany_counts_a_no_row_statement_as_done():
    cur = EmbeddedConnection(FakeSql(SQLError(100))).cursor()

    cur.executemany("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [("a",), ("b",)])

    assert cur.rowcount == 2


@pytest.mark.parametrize("code", [-1, -30, -104, 0, None])
def test_every_other_error_still_raises(code):
    cur = EmbeddedConnection(FakeSql(SQLError(code, "boom"))).cursor()

    with pytest.raises(SQLError):
        cur.execute("UPDATE Graph_KG.nodes SET x = 1 WHERE node_id = ?", ["a"])


# ------------------------------------------------------------------ EmbeddedIRIS (Native API subset)


class Gref:
    """A global as ``iris.gref`` shows it, over a dict keyed by subscript tuples."""

    def __init__(self, data):
        self._d = data

    def __getitem__(self, k):
        return self._d.get(k if isinstance(k, tuple) else (k,))

    def __setitem__(self, k, v):
        self._d[k if isinstance(k, tuple) else (k,)] = v

    def kill(self, subs):
        subs = tuple(subs)
        for key in [k for k in self._d if k[: len(subs)] == subs]:
            del self._d[key]

    def order(self, subs, direction=1):
        subs = list(subs)
        parent, after = tuple(subs[:-1]), subs[-1]
        sibs = sorted({k[len(parent)] for k in self._d if k[: len(parent)] == parent and len(k) > len(parent)})
        if direction == -1:
            sibs.reverse()
        if after == "":
            return sibs[0] if sibs else None
        later = [s for s in sibs if (s > after if direction == 1 else s < after)]
        return later[0] if later else None

    def data(self, subs):
        subs = tuple(subs)
        has_child = any(k[: len(subs)] == subs and len(k) > len(subs) for k in self._d)
        return (10 if has_child else 0) + (1 if subs in self._d else 0)


@pytest.fixture
def native():
    store = {}
    return store, EmbeddedIRIS(types.SimpleNamespace(gref=lambda name: Gref(store)))


def test_next_subscript_walks_the_children_in_order(native):
    store, e = native
    for k in ("b", "a", "c"):
        e.set(1, "^KG", "out", k)

    seen, cur = [], e.nextSubscript(False, "^KG", "out", "")
    while cur is not None:
        seen.append(cur)
        cur = e.nextSubscript(False, "^KG", "out", cur)

    assert seen == ["a", "b", "c"]


def test_get_string_is_the_node_value_as_text_or_none(native):
    _, e = native
    e.set(5, "^KG", "n")

    assert e.getString("^KG", "n") == "5"
    assert e.getString("^KG", "missing") is None


def test_kill_removes_the_node_and_its_descendants(native):
    store, e = native
    e.set(1, "^KG", "out", "a", "x")
    e.set(1, "^KG", "out", "b")

    e.kill("^KG", "out", "a")

    assert ("out", "a", "x") not in store and ("out", "b") in store


def test_is_defined_reports_value_and_children(native):
    _, e = native
    e.set(1, "^KG", "a", "b")

    assert e.isDefined("^KG", "a") == 10
    assert e.isDefined("^KG", "a", "b") == 1
    assert e.isDefined("^KG", "zzz") == 0


# ----- many parameters ------------------------------------------------------------------------------


class RecordingSql:
    def __init__(self):
        self.prepared = []
        self.executed = []

    def prepare(self, sql):
        self.prepared.append(sql)

        class Stmt:
            def execute(inner, *params):
                raise RuntimeError("<STACK>execute+1^%SYS.Python.SQLStatement.1")

        return Stmt()

    def exec(self, sql):
        self.executed.append(sql)


def test_a_statement_with_many_parameters_is_sent_with_its_values_written_in():
    """Measured under irispython: stmt.execute(*400 params) dies with <STACK>. A batched placement
    (`WHERE id IN (?, ?, ...)`) is exactly that statement."""
    sql_engine = RecordingSql()
    cur = EmbeddedConnection(sql_engine).cursor()
    ids = [f"n{i}" for i in range(400)]

    cur.execute(f"SELECT 1 FROM t WHERE id IN ({', '.join('?' * len(ids))})", ids)

    assert sql_engine.prepared == []
    assert "'n399'" in sql_engine.executed[0] and "?" not in sql_engine.executed[0]
