"""4.6.0 R138: setup alters `rdf_edges.graph_id` only when the column is not yet `NOT NULL DEFAULT ''`.

`tighten_graph_id_column` ran `ALTER TABLE rdf_edges ALTER COLUMN graph_id NOT NULL` and `... SET DEFAULT ''` on every
`initialize_schema()`, on a column a fresh install already creates both ways, and each ALTER recompiles the table's
class `Graph.KG.rdfedges` (measured by T061, `tests/e2e/test_262_inventory.py`). The column is now read from
`INFORMATION_SCHEMA.COLUMNS` first, and each ALTER runs only for the half that is missing. The live half is T061's
re-run test: a second setup leaves the class's `TimeChanged` as it was.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.schema import GraphSchema


class _Cursor:
    """`rdf_edges` resolves; `INFORMATION_SCHEMA.COLUMNS` answers `column` (IS_NULLABLE, COLUMN_DEFAULT), or no row."""

    def __init__(self, column):
        self.column, self.sql, self._row, self.rowcount = column, [], None, 0

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._row = None
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            self._row = self.column

    def fetchone(self):
        return self._row

    def alters(self):
        return [s for s in self.sql if s.startswith("ALTER TABLE")]


@pytest.mark.parametrize("default", ["$c(0)", "''", ""])  # $c(0): what IRIS 2026.3 reports for DEFAULT ''
def test_a_column_already_not_null_with_the_empty_default_is_not_altered(default):
    cur = _Cursor(("NO", default))
    result = GraphSchema.tighten_graph_id_column(cur)
    assert cur.alters() == []
    assert result["not_null"] is True and result["default_set"] is True
    assert result["already_tight"] is True


def test_a_nullable_column_with_no_default_gets_both_alters_in_order():
    cur = _Cursor(("YES", None))
    result = GraphSchema.tighten_graph_id_column(cur)
    assert cur.alters() == [
        "ALTER TABLE Graph_KG.rdf_edges ALTER COLUMN graph_id NOT NULL",
        "ALTER TABLE Graph_KG.rdf_edges ALTER COLUMN graph_id SET DEFAULT ''",
    ]
    assert result["already_tight"] is False
    update = next(i for i, s in enumerate(cur.sql) if s.startswith("UPDATE"))
    assert update < cur.sql.index(cur.alters()[0]), "the NULL rows are repaired before the column refuses them"


def test_a_not_null_column_without_the_default_gets_only_the_default():
    cur = _Cursor(("NO", None))
    GraphSchema.tighten_graph_id_column(cur)
    assert cur.alters() == ["ALTER TABLE Graph_KG.rdf_edges ALTER COLUMN graph_id SET DEFAULT ''"]


def test_a_nullable_column_with_the_default_gets_only_not_null():
    cur = _Cursor(("YES", "''"))
    GraphSchema.tighten_graph_id_column(cur)
    assert cur.alters() == ["ALTER TABLE Graph_KG.rdf_edges ALTER COLUMN graph_id NOT NULL"]


def test_another_default_is_replaced():
    cur = _Cursor(("NO", "'g0'"))
    GraphSchema.tighten_graph_id_column(cur)
    assert cur.alters() == ["ALTER TABLE Graph_KG.rdf_edges ALTER COLUMN graph_id SET DEFAULT ''"]


def test_no_catalog_row_alters_as_before():
    """The column could not be read (a spelling the catalog does not list): both ALTERs, as before R138."""
    cur = _Cursor(None)
    GraphSchema.tighten_graph_id_column(cur)
    assert len(cur.alters()) == 2


def test_the_probe_reads_the_ivg_schema_table_by_name():
    cur = _Cursor(("NO", "''"))
    GraphSchema.tighten_graph_id_column(cur)
    probe = next(s for s in cur.sql if "INFORMATION_SCHEMA.COLUMNS" in s)
    assert "TABLE_SCHEMA = 'Graph_KG'" in probe and "TABLE_NAME = 'rdf_edges'" in probe
    assert "COLUMN_NAME = 'graph_id'" in probe
