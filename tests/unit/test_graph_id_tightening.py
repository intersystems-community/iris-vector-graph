"""The default graph has one spelling in `rdf_edges`, and it is `''`.

`graph_id` was added to `rdf_edges` as a nullable column with no default, so the
default graph acquired two spellings: `create_edge` writes `''`, and every INSERT
that omits the column — the bulk loaders, `store.write_edges`, the inference
materializer — writes NULL. A predicate matching one spelling silently sees half
the default graph, which is how `list_graphs` came to report `''` as a named
graph and how `delete_edge` came to delete rows it reported having deleted.

Two properties are asserted here, both cheaply and without a container:

1. `GraphSchema.tighten_graph_id_column` repairs the NULL rows *before* it tries
   to make the column reject them. The other order fails on the first NULL row
   and leaves the database exactly as it was.
2. No writer inserts into `rdf_edges` without naming `graph_id`. That is what
   keeps the second spelling from coming back on any database where the DDL half
   could not be applied.
3. No reader in ObjectScript matches the NULL spelling alone.

The scan used to glob `*.py` only, so `Graph.KG.LedgerApply` was invisible to it —
and that is exactly where the pair survived: an INSERT that omitted the column,
read back by `SELECT ... WHERE graph_id IS NULL`. The two agreed with each other
until the column acquired its default, at which point every ledger commit in the
default graph failed with "no row in the store (ledger inconsistency)". A guard
that cannot see half the writers is the reason the half it cannot see is wrong.

The behavioural half is tests/integration/test_graph_id_default_spelling.py.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock

from iris_vector_graph.schema import GraphSchema

PACKAGE = pathlib.Path("iris_vector_graph")
IRIS_SRC = pathlib.Path("iris_src/src")


def _sql_calls(cursor) -> list[str]:
    return [call.args[0] for call in cursor.execute.call_args_list if call.args]


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def test_the_null_rows_are_repaired_before_the_column_is_tightened():
    cursor = MagicMock()
    cursor.rowcount = 3

    GraphSchema.tighten_graph_id_column(cursor)

    calls = _sql_calls(cursor)
    repair = next(i for i, sql in enumerate(calls) if sql.startswith("UPDATE"))
    tighten = next(i for i, sql in enumerate(calls) if sql.startswith("ALTER TABLE"))
    assert repair < tighten, (
        "the column is tightened before the NULL rows are repaired, so the ALTER "
        "fails on the first NULL row and nothing is migrated"
    )
    assert "SET graph_id = ''" in calls[repair]
    assert "graph_id IS NULL" in calls[repair]


def test_the_tightening_does_not_restate_the_column_type():
    """A restated type would rewrite the column's collation.

    `graph_id` is declared `%EXACT`, and IRIS `ALTER COLUMN` has no way to say so
    — a form that repeats `VARCHAR(256)` would silently drop the column back to
    the default collation, and graph keys would stop comparing exactly.
    """
    cursor = MagicMock()

    GraphSchema.tighten_graph_id_column(cursor)

    alter = next(sql for sql in _sql_calls(cursor) if sql.startswith("ALTER TABLE"))
    assert "ALTER COLUMN graph_id NOT NULL" in alter
    assert "VARCHAR" not in alter, (
        "the ALTER restates the column type, which rewrites its collation"
    )


def test_the_tightened_column_defaults_to_the_default_graph():
    """`NOT NULL` alone turns an omitted `graph_id` from a silent NULL into an error.

    A fresh install declares the column `NOT NULL DEFAULT ''`; the migration has to
    reach the same place, or every SQL writer outside this package that never knew
    about `graph_id` starts failing instead of landing in the default graph.
    """
    cursor = MagicMock()

    GraphSchema.tighten_graph_id_column(cursor)

    assert any("SET DEFAULT ''" in sql for sql in _sql_calls(cursor)), (
        "the column is NOT NULL with no default, so an INSERT that omits graph_id "
        "now fails rather than defaulting to the default graph"
    )


def test_a_rejected_alter_still_reports_the_repair():
    """The two halves are reported separately because they can differ.

    On a database where another class also claims `rdf_edges`, IRIS cannot resolve
    the table for DDL and the ALTER fails. The repair still happened, and saying
    so is the difference between a migration that is partly applied and one that
    lies about it.
    """
    cursor = MagicMock()
    cursor.rowcount = 7
    cursor.execute.side_effect = lambda sql, *a: (
        _raise(RuntimeError("SQLCODE -400 CLASS DOES NOT EXIST"))
        if sql.startswith("ALTER TABLE")
        else None
    )

    result = GraphSchema.tighten_graph_id_column(cursor)

    assert result["rows_repaired"] == 7
    assert result["not_null"] is False


def _raise(exc):
    raise exc


def test_the_migration_runs_as_part_of_ensure_indexes():
    cursor = MagicMock()

    status = GraphSchema.ensure_indexes(cursor)

    assert "tighten_graph_id_column" in status, (
        "the migration exists but nothing calls it, so no existing database is "
        "ever repaired"
    )


# ---------------------------------------------------------------------------
# The writers
# ---------------------------------------------------------------------------


_INSERT = re.compile(r"INSERT INTO Graph_KG\.rdf_edges\s*\(([^)]*)\)")


def _edge_inserts() -> list[tuple[pathlib.Path, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        # The engine mixins address the table through self._t(), which resolves
        # the schema prefix at runtime; for this purpose it is the same table.
        text = path.read_text().replace("{self._t('rdf_edges')}", "Graph_KG.rdf_edges")
        for columns in _INSERT.findall(text):
            found.append((path, columns))
    return found


def test_every_writer_names_the_graph_column():
    inserts = _edge_inserts()

    assert len(inserts) >= 4, (
        "the INSERT scan found almost nothing, so it is no longer looking at the "
        "right table name"
    )
    omitted = [(str(p), cols) for p, cols in inserts if "graph_id" not in cols]
    assert not omitted, (
        "these writers insert an edge without naming graph_id, so the row lands "
        f"in the NULL spelling of the default graph: {omitted}"
    )


def _objectscript_text(path: pathlib.Path) -> str:
    """One .cls, with its runtime-built table names resolved to the real one.

    ObjectScript builds the table name by concatenation — `" _ pSchema _ ".rdf_edges`
    — so a scan for the literal `Graph_KG.rdf_edges` finds nothing in these files
    and reports success having read no writers at all.
    """
    text = path.read_text()
    for var in ("pSchema", "tSchema"):
        text = text.replace('" _ ' + var + ' _ "', "Graph_KG")
    return text


def _objectscript_edge_inserts() -> list[tuple[pathlib.Path, str]]:
    found = []
    for path in sorted(IRIS_SRC.rglob("*.cls")):
        for columns in _INSERT.findall(_objectscript_text(path)):
            found.append((path, columns))
    return found


def test_every_objectscript_writer_names_the_graph_column():
    inserts = _objectscript_edge_inserts()

    assert len(inserts) >= 3, (
        "the ObjectScript INSERT scan found almost nothing, so the concatenated "
        f"table name is spelled some way this scan does not resolve: {inserts}"
    )
    omitted = [(str(p), cols) for p, cols in inserts if "graph_id" not in cols]
    assert not omitted, (
        "these ObjectScript writers insert an edge without naming graph_id, so the "
        "row lands in whichever spelling the column default happens to be — and a "
        f"reader that assumes the other one finds nothing: {omitted}"
    )


def test_no_objectscript_reader_matches_the_null_spelling_alone():
    """`COALESCE(graph_id, '') = ''`, not `graph_id IS NULL`.

    One spelling is one half of the default graph on any database that was upgraded
    rather than created, and which half depends on which writer produced the row.
    """
    offenders = [
        (str(path), line.strip())
        for path in sorted(IRIS_SRC.rglob("*.cls"))
        for line in path.read_text().splitlines()
        if "graph_id IS NULL" in line.upper() and "COALESCE" not in line.upper()
    ]

    assert not offenders, (
        "these predicates see only the NULL-spelled half of the default graph: "
        f"{offenders}"
    )


def test_the_ledger_key_never_reaches_the_index_globals():
    """`GKey` is `ForLedger`, and `^KG` is keyed by `ForIndex` (ADR-0003).

    The default graph is `$Char(1)` in `^IVG.Ledger("tuple", ...)` and the integer
    `0` in `^KG`. `Graph.KG.LedgerApply` derived the `^KG` key with `GKey`, so every
    committed relationship in the default graph landed at `^KG("out", $Char(1), ...)`
    — a subscript no reader walks. The SQL row was right and the traversal was empty.
    """
    # A local array mirroring the ledger's tuple index is a legitimate GKey caller,
    # so what is flagged is the derivation reaching an index store: an ^KG subscript,
    # or the adjacency writers, which take an already-derived index key.
    index_stores = ("^KG(", "WriteAdjacency(", "WriteAdjacencyShadow(", "DeleteAdjacency(")
    misuses = [
        (str(path), line.strip())
        for path in sorted(IRIS_SRC.rglob("*.cls"))
        for line in path.read_text().splitlines()
        if "GKey(" in line and any(store in line for store in index_stores)
    ]

    assert not misuses, (
        "the ledger's own graph-key derivation is being used somewhere other than "
        f"an ^IVG.Ledger subscript: {misuses}"
    )
