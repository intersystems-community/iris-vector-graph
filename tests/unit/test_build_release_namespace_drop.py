"""`scripts/fixtures/build_release_namespace.py` `_drop_schema_tables` raises when a table will not drop (R090).

It retries, because IRIS refuses to drop a parent a child still references and the next pass drops it once the child
is gone. But it swallowed every error, so a table that never dropped returned as if the schema were empty, and the
fixture built on top of it. A fake cursor stands in for IRIS: no container.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fixtures" / "build_release_namespace.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_release_namespace_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DropRefused(Exception):
    pass


class _FakeCursor:
    """No SQLUser views; `tables` are the schema's base tables; `refuse(name, dropped)` decides a DROP TABLE."""

    def __init__(self, tables, refuse):
        self.tables = list(tables)
        self.refuse = refuse
        self.drop_attempts = []
        self._rows = []
        self.closed = False

    def execute(self, sql, params=None):
        if "INFORMATION_SCHEMA.VIEWS" in sql:
            self._rows = [(0,)]
        elif "INFORMATION_SCHEMA.TABLES" in sql:
            self._rows = [(t,) for t in self.tables]
        elif sql.startswith("DROP TABLE "):
            name = sql.rsplit(".", 1)[1]
            self.drop_attempts.append(name)
            if self.refuse(name, self.tables):
                raise _DropRefused(f"SQLCODE -321 dropping {name}")
            self.tables.remove(name)
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def test_a_table_that_never_drops_raises_the_last_error():
    cur = _FakeCursor(["rdf_edges"], refuse=lambda name, tables: True)
    with pytest.raises(_DropRefused, match="dropping rdf_edges"):
        _load()._drop_schema_tables(_FakeConn(cur), "Graph_KG")
    assert len(cur.drop_attempts) == 10, "the retry is kept: ten passes before giving up"


def test_a_parent_refused_while_its_child_exists_drops_on_the_next_pass():
    """The retry's reason: `nodes` refuses until `rdf_edges` is gone, then drops; no error escapes."""
    cur = _FakeCursor(["nodes", "rdf_edges"], refuse=lambda name, tables: name == "nodes" and "rdf_edges" in tables)
    conn = _FakeConn(cur)
    _load()._drop_schema_tables(conn, "Graph_KG")
    assert cur.tables == []
    assert cur.drop_attempts == ["nodes", "rdf_edges", "nodes"]
    assert conn.commits == 1
