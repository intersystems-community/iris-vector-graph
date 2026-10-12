"""The statements of the 4.0.0 docs and edge-vector step, declared as data.

``migrations/docs_and_edge_vectors.py`` used to carry its SQL as strings inside its
control flow, so a test could only check the SQL that was sent. Here each statement
is a :class:`Statement` with a name, a template, the parameters it takes, and what the
caller expects from it. The step looks a statement up by name, binds the schema and
the vector width with :func:`bind`, and runs it with :func:`run_statement`, which is
the one place this step executes migration SQL.

Templates use ``str.format`` fields: ``{schema}``, ``{suffix}`` (the 4.0.0 staging
suffix), ``{dim}`` and ``{dtype}`` (vector width and type), and ``{table}`` (a full
table name, for the width reset). A template is never executed as it stands; it is
bound first, so :attr:`Statement.sql` on an entry of :data:`STATEMENTS` is a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

#: Suffix of the table a rebuild fills before it is renamed into place. Same suffix
#: spec 227's node migration uses, so a half-finished upgrade looks the same either
#: side of the two migrations.
STAGING_SUFFIX = "_ivg400"

#: What a caller does with a statement's outcome.
#:
#: * ``"rows-changed"``: an INSERT, UPDATE or DELETE; its row count is not read.
#: * ``"rows-selected"``: a SELECT; :func:`run_statement` returns the rows.
#: * ``"ddl"``: a schema change; a re-run may find it already done.
EXPECTS: Tuple[str, ...] = ("rows-changed", "rows-selected", "ddl")

_DEFAULT_SCHEMA = "Graph_KG"


@dataclass(frozen=True)
class Statement:
    """One statement of a migration step.

    Attributes:
        name: The key this statement is looked up by. Equal to its key in
            :data:`STATEMENTS`.
        sql: The SQL text. A template on :data:`STATEMENTS`; the bound text after
            :func:`bind`. Never ends in ``;``.
        params_doc: The names of the bound parameters, in the order they appear as
            ``?`` placeholders. ``()`` for a statement with none.
        expects: One of :data:`EXPECTS`.
    """

    name: str
    sql: str
    params_doc: Tuple[str, ...]
    expects: str

    def __post_init__(self) -> None:
        if self.expects not in EXPECTS:
            raise ValueError(f"{self.name}: expects must be one of {EXPECTS}, not {self.expects!r}")


#: Every statement of the step, by name. Templates; see :func:`bind`.
STATEMENTS: Mapping[str, Statement] = {
    s.name: s
    for s in (
        # -- docs: prepare -------------------------------------------------
        # `graph_id` arrives nullable and with no default: NULL is what the placement
        # reads as "not yet placed".
        Statement(
            name="docs_add_graph_column",
            sql="ALTER TABLE {schema}.docs ADD COLUMN graph_id VARCHAR(256) %EXACT NULL",
            params_doc=(),
            expects="ddl",
        ),
        Statement(
            name="docs_quarantine_create",
            sql=(
                "CREATE TABLE IF NOT EXISTS {schema}.docs_quarantine (\n"
                "    doc_id VARCHAR(256) %EXACT NOT NULL PRIMARY KEY,\n"
                "    text VARCHAR(4000) %EXACT,\n"
                "    reason VARCHAR(64) NOT NULL,\n"
                "    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
                ")"
            ),
            params_doc=(),
            expects="ddl",
        ),
        # -- docs: finish --------------------------------------------------
        Statement(
            name="docs_staging_create",
            sql=(
                "CREATE TABLE {schema}.docs{suffix} (\n"
                "  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
                "  id       VARCHAR(256) %EXACT NOT NULL,\n"
                "  text     VARCHAR(4000) %EXACT,\n"
                "  CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)\n"
                ")"
            ),
            params_doc=(),
            expects="ddl",
        ),
        # Only placed rows are copied; the placement has moved the rest to docs_quarantine.
        Statement(
            name="docs_copy_placed",
            sql=(
                "INSERT INTO {schema}.docs{suffix} (graph_id, id, text) "
                "SELECT graph_id, id, text FROM {schema}.docs WHERE graph_id IS NOT NULL"
            ),
            params_doc=(),
            expects="rows-changed",
        ),
        Statement(
            name="docs_drop_original",
            sql="DROP TABLE {schema}.docs",
            params_doc=(),
            expects="ddl",
        ),
        # The new name is unqualified: IRIS refuses a qualified target at Prepare
        # (SQLCODE -1), because a rename cannot move a table between schemas.
        Statement(
            name="docs_rename_staging",
            sql="ALTER TABLE {schema}.docs{suffix} RENAME docs",
            params_doc=(),
            expects="ddl",
        ),
        Statement(
            name="docs_index_graph",
            sql="CREATE INDEX idx_docs_graph ON {schema}.docs (graph_id)",
            params_doc=(),
            expects="ddl",
        ),
        Statement(
            name="docs_index_text_ifind",
            sql="CREATE INDEX idx_docs_text_ifind ON TABLE {schema}.docs (text) AS %iFind.Index.Basic",
            params_doc=(),
            expects="ddl",
        ),
        # -- edge vectors: prepare -----------------------------------------
        # The staging table is shaped like a generated edge route: identity primary
        # key, graph_id in the unique key, and metadata.
        Statement(
            name="edge_staging_create",
            sql=(
                "CREATE TABLE {schema}.kg_EdgeEmbeddings{suffix} (\n"
                "    emb_rowid BIGINT IDENTITY PRIMARY KEY,\n"
                "    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
                "    s    VARCHAR(256) %EXACT NOT NULL,\n"
                "    p    VARCHAR(512) %EXACT NOT NULL,\n"
                "    o_id VARCHAR(256) %EXACT NOT NULL,\n"
                "    emb  VECTOR({dtype}, {dim}),\n"
                "    metadata VARCHAR(4000),\n"
                "    CONSTRAINT uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)\n"
                ")"
            ),
            params_doc=(),
            expects="ddl",
        ),
        Statement(
            name="edge_quarantine_create",
            sql=(
                "CREATE TABLE IF NOT EXISTS {schema}.edge_vector_quarantine (\n"
                "    q_rowid BIGINT IDENTITY PRIMARY KEY,\n"
                "    s    VARCHAR(256) %EXACT NOT NULL,\n"
                "    p    VARCHAR(512) %EXACT NOT NULL,\n"
                "    o_id VARCHAR(256) %EXACT NOT NULL,\n"
                "    reason VARCHAR(64) NOT NULL,\n"
                "    emb  VECTOR({dtype}, {dim}),\n"
                "    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
                "    CONSTRAINT uq_edge_quarantine_spo UNIQUE (s, p, o_id)\n"
                ")"
            ),
            params_doc=(),
            expects="ddl",
        ),
        # -- edge vectors: finish ------------------------------------------
        Statement(
            name="edge_drop_source",
            sql="DROP TABLE {schema}.kg_EdgeEmbeddings",
            params_doc=(),
            expects="ddl",
        ),
        Statement(
            name="edge_rename_staging",
            sql="ALTER TABLE {schema}.kg_EdgeEmbeddings{suffix} RENAME kg_EdgeEmbeddings",
            params_doc=(),
            expects="ddl",
        ),
        # -- edge vectors: width reset (table is a full name) --------------
        Statement(
            name="edge_held_rows",
            sql="SELECT COUNT(*) FROM {table}",
            params_doc=(),
            expects="rows-selected",
        ),
        Statement(
            name="edge_width_reset_drop",
            sql="DROP TABLE {table}",
            params_doc=(),
            expects="ddl",
        ),
        # -- edge vectors: placement ---------------------------------------
        # A vector asserted by exactly one graph, and one no edge asserts, move in one
        # statement each; the join carries the decision.
        Statement(
            name="edge_place_unique_graph",
            sql=(
                "INSERT INTO {schema}.kg_EdgeEmbeddings{suffix} (graph_id, s, p, o_id, emb, metadata) "
                "SELECT COALESCE(r.graph_id, ''), e.s, e.p, e.o_id, e.emb, NULL "
                "FROM {schema}.kg_EdgeEmbeddings e JOIN ("
                "SELECT s, p, o_id, MIN(graph_id) AS graph_id "
                "FROM {schema}.rdf_edges GROUP BY s, p, o_id HAVING COUNT(DISTINCT graph_id) = 1"
                ") r ON r.s = e.s AND r.p = e.p AND r.o_id = e.o_id "
                "WHERE NOT EXISTS (SELECT 1 FROM {schema}.kg_EdgeEmbeddings{suffix} t "
                "WHERE t.s = e.s AND t.p = e.p AND t.o_id = e.o_id)"
            ),
            params_doc=(),
            expects="rows-changed",
        ),
        Statement(
            name="edge_quarantine_no_edge",
            sql=(
                "INSERT INTO {schema}.edge_vector_quarantine (s, p, o_id, reason, emb, quarantined_at) "
                "SELECT e.s, e.p, e.o_id, 'no_edge', e.emb, CURRENT_TIMESTAMP "
                "FROM {schema}.kg_EdgeEmbeddings e WHERE NOT EXISTS (SELECT 1 FROM {schema}.rdf_edges r "
                "WHERE r.s = e.s AND r.p = e.p AND r.o_id = e.o_id) "
                "AND NOT EXISTS (SELECT 1 FROM {schema}.edge_vector_quarantine q "
                "WHERE q.s = e.s AND q.p = e.p AND q.o_id = e.o_id)"
            ),
            params_doc=(),
            expects="rows-changed",
        ),
        # One vector, by key, into the graph a resolver chose.
        Statement(
            name="edge_place_by_key",
            sql=(
                "INSERT INTO {schema}.kg_EdgeEmbeddings{suffix} (graph_id, s, p, o_id, emb, metadata) "
                "SELECT ?, s, p, o_id, emb, NULL FROM {schema}.kg_EdgeEmbeddings "
                "WHERE s = ? AND p = ? AND o_id = ?"
            ),
            params_doc=("graph_id", "s", "p", "o_id"),
            expects="rows-changed",
        ),
        # One vector, by key, quarantined with the reason the resolver step gave.
        Statement(
            name="edge_quarantine_by_key",
            sql=(
                "INSERT INTO {schema}.edge_vector_quarantine (s, p, o_id, reason, emb, quarantined_at) "
                "SELECT s, p, o_id, ?, emb, CURRENT_TIMESTAMP "
                "FROM {schema}.kg_EdgeEmbeddings WHERE s = ? AND p = ? AND o_id = ?"
            ),
            params_doc=("reason", "s", "p", "o_id"),
            expects="rows-changed",
        ),
    )
}

#: The names each step runs, in order. The step functions bind and run these.
DOCS_PREPARE: Tuple[str, ...] = ("docs_add_graph_column", "docs_quarantine_create")
DOCS_FINISH: Tuple[str, ...] = (
    "docs_staging_create",
    "docs_copy_placed",
    "docs_drop_original",
    "docs_rename_staging",
    "docs_index_graph",
    "docs_index_text_ifind",
)
EDGE_VECTORS_PREPARE: Tuple[str, ...] = ("edge_staging_create", "edge_quarantine_create")
EDGE_VECTORS_FINISH: Tuple[str, ...] = ("edge_drop_source", "edge_rename_staging")


def bind(
    name: str,
    *,
    schema: str = _DEFAULT_SCHEMA,
    dimension: Optional[int] = None,
    dtype: str = "DOUBLE",
    table: Optional[str] = None,
) -> Statement:
    """The statement ``name`` with its template fields filled in.

    ``dimension`` is required for the statements that declare a vector and is
    rendered as an integer, so it cannot smuggle SQL into the text. ``table`` is the
    full name a width-reset statement works on.
    """
    template = STATEMENTS[name]
    fields = {
        "schema": schema,
        "suffix": STAGING_SUFFIX,
        "dtype": dtype.upper(),
        "dim": "" if dimension is None else int(dimension),
        "table": table or "",
    }
    return Statement(
        name=template.name,
        sql=template.sql.format(**fields),
        params_doc=template.params_doc,
        expects=template.expects,
    )


def bind_all(names: Sequence[str], **fields: Any) -> List[Statement]:
    """:func:`bind` each name, in order."""
    return [bind(n, **fields) for n in names]


def run_statement(conn, stmt: Statement, params: Optional[Sequence[Any]] = None):
    """Execute one bound statement on ``conn``. The only place this step runs migration SQL.

    The trailing ``;`` and surrounding whitespace are stripped. No parameter list is
    passed when there are none, and a list whose length differs from
    ``stmt.params_doc`` is refused before anything is sent. For ``rows-selected`` the
    rows are returned; otherwise the result is ``None``. The cursor is closed either
    way.

    It does not commit: the step decides when its work is durable.
    """
    values = list(params) if params else []
    if len(values) != len(stmt.params_doc):
        raise ValueError(
            f"{stmt.name} takes {len(stmt.params_doc)} parameter(s) {stmt.params_doc}, "
            f"got {len(values)}"
        )
    sql = stmt.sql.strip()
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    cursor = conn.cursor()
    try:
        if values:
            cursor.execute(sql, values)
        else:
            cursor.execute(sql)
        if stmt.expects == "rows-selected":
            return list(cursor.fetchall() or [])
        return None
    finally:
        try:
            cursor.close()
        except Exception:  # pragma: no cover - driver-dependent
            pass
