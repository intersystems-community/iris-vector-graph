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


#: Every way a statement in this repository can name the edge table. `Graph_KG` is
#: the declared schema; `SQLUser` is the compatibility view IRIS generates over it;
#: the bare name resolves against whatever schema the session's search path names.
#: A guard that matches one of the three reports success having read a third of the
#: writers — which is the same failure mode the module docstring describes for
#: `Graph.KG.LedgerApply`, one spelling further out (spec 230, FR-004).
TABLE_SPELLINGS = ("Graph_KG.rdf_edges", "SQLUser.rdf_edges", "rdf_edges")

#: `INSERT` and `INTO` are not always adjacent: the bulk loader interpolates its
#: `%NOINDEX %NOCHECK` hint between them, so a pattern demanding the two words side
#: by side reads past the one writer that moves millions of rows. Nor is the column
#: list always in the same string literal as the table name — the same writer breaks
#: the statement across two adjacent literals, putting a quote and a newline where a
#: space would be. Both gaps are spelled out rather than papered over with `.*`: the
#: hint may not contain a paren, and the run up to the column list may hold only
#: quotes and whitespace, so neither can swallow a second statement.
_INSERT = re.compile(
    r"INSERT[^()]{0,40}?INTO\s+(?:Graph_KG\.|SQLUser\.)?rdf_edges[\s\"']*\(([^)]*)\)",
    re.IGNORECASE,
)


def _python_text(path: pathlib.Path) -> str:
    """One .py, with its runtime-built table names resolved to the real one.

    Three accessors, because three modules named them differently: the engine mixins
    use `self._t()`, `bulk_loader.py` uses `self._table()`, and `cypher/translator.py`
    calls a module-level `_table()` with no `self`. The scan resolved the first only,
    so every INSERT in the other two was invisible to it — and that is where the
    omission survived: `INSERT ... INTO rdf_edges (s, p, o_id, qualifiers)`, no
    `graph_id`, on the bulk path that writes millions of rows at a time and on the
    Cypher `CREATE` path (spec 230, FR-004).
    """
    text = path.read_text()
    for accessor in ("{self._t(", "{self._table(", "{_table("):
        text = text.replace("%s'rdf_edges')}" % accessor, "Graph_KG.rdf_edges")
    return text


#: Modules whose edge writers the scan has been blind to at least once. Named rather
#: than counted: a scan that silently stops covering one of them looks identical to a
#: module that stopped writing edges, and the two are not the same news.
MUST_BE_SCANNED = ("bulk_loader.py", "translator.py")


def test_the_scan_reaches_every_module_that_writes_an_edge():
    """The guard's own coverage, asserted rather than assumed (spec 230, FR-004).

    Both of these wrote `rdf_edges` under a table-name spelling `_python_text` did not
    resolve, so `test_every_writer_names_the_graph_column` passed over them: it read
    thirteen INSERTs in the engine mixins, found `graph_id` in all thirteen, and
    reported that every writer names the column.
    """
    scanned = {path.name for path, _cols in _edge_inserts()}
    missing = [name for name in MUST_BE_SCANNED if name not in scanned]
    assert not missing, (
        f"the scan sees no rdf_edges INSERT in {missing}, so whatever those modules "
        "write is unchecked. Either the table name is spelled a way _python_text "
        "does not resolve, or the writer moved and this list is stale"
    )


#: A column list that is nothing but one `{…}` interpolation names no column of its
#: own — it re-emits whatever list the statement it is rewriting carried. `MERGE`
#: does exactly that: it finds the `rdf_edges` INSERT this translation already
#: added and re-issues it as `SELECT ... WHERE NOT EXISTS`, copying the column list
#: across verbatim. Reading it as an omission flags the rewrite for a column the
#: original is asserted, three tests above, to name.
_REEMITTED = re.compile(r"^\s*\{[^{}]*\}\s*$")


def _edge_inserts() -> list[tuple[pathlib.Path, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for columns in _INSERT.findall(_python_text(path)):
            if _REEMITTED.match(columns):
                continue
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


# ---------------------------------------------------------------------------
# The table's name (spec 230, FR-004)
# ---------------------------------------------------------------------------


def _cursor_resolving_only(spelling: str):
    """A cursor that answers for one table name and raises `-30` for the others.

    `SQLCODE -30` is what IRIS returns for a table it cannot resolve, and it is
    indistinguishable — to a caller that catches everything — from a migration that
    had nothing to do.
    """
    cursor = MagicMock()
    cursor.rowcount = 5

    named = re.compile(r"(?:UPDATE|ALTER TABLE)\s+(\S+)", re.IGNORECASE)

    def execute(sql, *args, **kwargs):
        # The table the statement names, not a substring of it: `rdf_edges` occurs
        # inside `Graph_KG.rdf_edges`, so a substring test would let the declared
        # name pass on a namespace that only presents the bare one.
        match = named.search(sql)
        if not match or match.group(1) != spelling:
            raise RuntimeError("[SQLCODE: <-30>:<Table or view not found>]")
        return None

    cursor.execute.side_effect = execute
    return cursor


def test_the_repair_finds_the_table_under_every_spelling():
    """The migration hardcoded `Graph_KG.rdf_edges` and no other name.

    A namespace that presents the table under a different schema — the generated
    `SQLUser` view, or a bare name resolved through the session's search path — got a
    migration that raised on its first statement and a result that reported no rows
    repaired, which reads exactly like a database that needed no repair.
    """
    for spelling in TABLE_SPELLINGS:
        cursor = _cursor_resolving_only(spelling)

        result = GraphSchema.tighten_graph_id_column(cursor)

        assert result["resolved_table"] == spelling, (
            f"the namespace presents the table as {spelling} and the migration "
            f"resolved {result['resolved_table']!r}, so nothing was repaired"
        )
        assert result["rows_repaired"] == 5, (
            f"the repair did not run against {spelling}: {result}"
        )


def test_the_declared_name_is_tried_first():
    """`Graph_KG.rdf_edges` is the base table; `SQLUser.rdf_edges` is a view over it.

    Order is not cosmetic. DDL against the view fails, so resolving the view first on
    a database where both exist would repair the rows and then silently skip the
    `NOT NULL` half.
    """
    cursor = MagicMock()
    cursor.rowcount = 0

    GraphSchema.tighten_graph_id_column(cursor)

    first = _sql_calls(cursor)[0]
    assert "Graph_KG.rdf_edges" in first, (
        f"the first statement names something other than the base table: {first}"
    )


def test_an_unresolvable_table_is_reported_and_not_claimed():
    cursor = _cursor_resolving_only("some.table.that.is.not.rdf_edges")

    result = GraphSchema.tighten_graph_id_column(cursor)

    assert result["resolved_table"] is None
    assert result["rows_repaired"] == 0
    assert result["not_null"] is False
    assert list(result["spellings_tried"]) == list(TABLE_SPELLINGS), (
        "the result does not say which names were probed, so an operator cannot tell "
        f"a missing table from a differently-named one: {result}"
    )


def test_the_writer_scan_reads_every_spelling():
    """The guard has to see a writer whatever name it uses for the table.

    Synthetic, because the repository currently spells every INSERT `Graph_KG.` — and
    a guard that only works because nothing has drifted yet is not a guard.
    """
    for spelling in TABLE_SPELLINGS:
        sql = f"INSERT INTO {spelling} (s, p, o_id) VALUES (?, ?, ?)"
        assert _INSERT.findall(sql) == ["s, p, o_id"], (
            f"a writer spelling the table {spelling} is invisible to the scan, so it "
            "can omit graph_id and no test will say so"
        )


def test_the_writer_scan_reports_what_it_found():
    """A count, so a scan that silently stops matching cannot pass as a clean repo."""
    inserts = _edge_inserts() + _objectscript_edge_inserts()

    spellings = {
        spelling
        for path, _ in inserts
        for spelling in TABLE_SPELLINGS
        if f"INSERT INTO {spelling}" in _objectscript_text(path)
        or f"INSERT INTO {spelling}" in _python_text(path)
    }
    assert spellings, f"the scan found {len(inserts)} writers and no spelling for any"


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
