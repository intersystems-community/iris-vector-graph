"""Spec 230 — `idx_docs_graph` on an install whose `docs` has no graph yet.

`Graph_KG.docs` gains `graph_id` in the 4.0.0 upgrade's `docs` step, not in
`initialize_schema`. So an upgraded package meeting a 3.2.0 install issues

    CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)

against a table without the column, and IRIS answers `SQLCODE -31 … No such field
found in table 'GRAPH_KG.DOCS'`. Measured during the 3.2.0 → 4.0.0 rehearsal, that
came out of `initialize_schema` as

    Schema setup warning: <SQL ERROR>; Details: [SQLCODE: <-31> …]

which reads like a broken install and names no next step — while `kg_KNN_VEC`, in the
identical position two log lines later, says what it is waiting for and what to run.
This is the same shape of thing and gets the same treatment: decided by the column's
absence before the statement is issued, and reported as a deferral that names the
migration.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

DOCS_INDEX = "CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)"
BENIGN = "CREATE TABLE Graph_KG.docs (id VARCHAR(256), text VARCHAR(4000))"


class SchemaCursor:
    """Records statements and answers the `INFORMATION_SCHEMA.COLUMNS` probe."""

    def __init__(self, columns: dict):
        #: ``{table: (column, ...)}`` — a table absent from the mapping does not exist.
        self.columns = columns
        self.statements: list[str] = []
        self._rows: list = []

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        args = list(params or [])
        if "INFORMATION_SCHEMA.COLUMNS" in text.upper():
            _, table, column = args[-3], args[-2], args[-1]
            declared = self.columns.get(str(table), ())
            self._rows = [(1 if str(column) in declared else 0,)]
            return self
        self.statements.append(text)
        self._rows = [(0,)]
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    @property
    def description(self):
        return [("node_id", None)]

    def issued(self, needle: str) -> list:
        return [s for s in self.statements if needle.lower() in s.lower()]


class Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass


def _engine(columns):
    cursor = SchemaCursor(columns)
    engine = IRISGraphEngine(Conn(cursor), embedding_dimension=4)
    return cursor, engine


# --- the shape probe --------------------------------------------------------------


def test_a_docs_table_without_graph_id_is_awaiting_the_migration():
    cursor, engine = _engine({"docs": ("id", "text")})

    assert engine._docs_awaits_graph_column(cursor) is True


def test_a_docs_table_that_carries_graph_id_is_not():
    cursor, engine = _engine({"docs": ("id", "text", "graph_id")})

    assert engine._docs_awaits_graph_column(cursor) is False


def test_an_absent_docs_table_is_not_treated_as_pre_migration():
    """A fresh install creates `docs` with the column in this same script.

    Absence here means the table is about to be created, so deferring the index would
    skip it on exactly the install that can have it.
    """
    cursor, engine = _engine({})

    assert engine._docs_awaits_graph_column(cursor) is False


# --- what the DDL loop does with it -----------------------------------------------


def _run(columns, script):
    cursor, engine = _engine(columns)
    with patch(
        "iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=script
    ), patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"), patch(
        "iris_vector_graph.schema.GraphSchema.get_procedures_sql_list", return_value=[]
    ):
        engine.initialize_schema(auto_deploy_objectscript=False)
    return cursor


def test_the_index_is_not_attempted_before_the_column_exists(caplog):
    with caplog.at_level("WARNING"):
        cursor = _run({"docs": ("id", "text")}, f"{BENIGN};\n{DOCS_INDEX};")

    assert cursor.issued("idx_docs_graph") == [], (
        "the statement cannot succeed against this table, and attempting it puts an "
        "SQLCODE -31 in the log of an install that is simply mid-upgrade"
    )
    assert "idx_docs_graph deferred" in caplog.text
    assert "upgrade_to_4_0_0" in caplog.text, (
        "a deferral with no next step reads as a broken install"
    )
    assert "sqlcode" not in caplog.text.lower()


def test_the_index_is_issued_once_the_column_is_there(caplog):
    with caplog.at_level("WARNING"):
        cursor = _run({"docs": ("id", "text", "graph_id")}, f"{BENIGN};\n{DOCS_INDEX};")

    assert cursor.issued("idx_docs_graph"), "the index was skipped on a 4.0.0 schema"
    assert "deferred" not in caplog.text


def test_a_fresh_install_still_gets_the_index():
    cursor = _run({}, f"{BENIGN};\n{DOCS_INDEX};")

    assert cursor.issued("idx_docs_graph")


def test_only_this_one_index_is_deferred():
    """The deferral is keyed to the statement, not to `docs`: the full-text index on
    `docs (text)` has nothing to do with the graph column."""
    other = "CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic"
    cursor = _run({"docs": ("id", "text")}, f"{other};\n{DOCS_INDEX};")

    assert cursor.issued("idx_docs_text_ifind")
    assert cursor.issued("idx_docs_graph") == []
