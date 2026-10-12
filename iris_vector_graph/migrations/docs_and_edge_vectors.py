"""The 4.0.0 migration for `Graph_KG.docs` and `Graph_KG.kg_EdgeEmbeddings`.

Spec 227 moved node vectors into graph-scoped routes. Two tables it left alone are
graph-blind in the same way, and both are re-keyed here (spec 230, FR-007, FR-018,
FR-019, FR-020):

* **`docs`** was `id VARCHAR(256) PRIMARY KEY` and `id` meant whatever the writer
  chose. 4.0.0 makes `id` a *node* ID keyed `(graph_id, id)`, which is what lets
  `kg_RRF_FUSE` fuse at all — its vector leg answers node IDs, so a join against
  free-form document IDs matched nothing and every "fused" row carried one leg and
  NULLs for the other.
* **`kg_EdgeEmbeddings`** was keyed on the triple alone, so two graphs asserting the
  same edge shared one row and the second write replaced the first. 4.0.0 keys it
  `(graph_id, s, p, o_id)` behind an identity primary key — the only shape IRIS
  accepts an HNSW index on (research R3).

Both re-keys are a placement, not an `ALTER`: the graph of a row is not in the row.
A document's graph is the graph holding the node its ID names; an edge vector's is
the graph *asserting the edge*, read from ``rdf_edges`` rather than from either
endpoint. Each row gets one of three answers — placed in the single graph that
claims it, handed to a resolver when more than one does, or quarantined by name.
Defaulting to ``''`` is not one of them: a document about graph B's node, or a
vector for graph B's edge, sitting in the default graph reads and ranks exactly
like an answer, and that silence is why this is a 4.0.0 change.

The order is what makes it re-runnable, and each step is separate because the
placement sits between them:

1. ``prepare_docs`` / ``prepare_edge_vectors`` — `docs` gains a **nullable**
   `graph_id` (``NULL`` is how the placement finds an unplaced row) and the edge
   vectors get a staging table in the 4.0.0 shape plus the two quarantines.
2. ``place_documents`` / ``place_edge_vectors`` — every row is placed or
   quarantined, and a second run finds nothing to do: a placed `docs` row is no
   longer ``NULL``, a quarantined one has moved to ``docs_quarantine``, and a
   staged or quarantined edge vector is skipped by key. Quarantining is a move —
   the copy carries the payload and only then is the original removed — so a
   killed pass leaves a row in two places, never in none.
3. ``finish_docs`` / ``finish_edge_vectors`` — the rebuilt table replaces the
   original, leaving the catalog a fresh 4.0.0 install's shape. The drop comes
   after the copy, so a killed pass never leaves rows in neither table.

No vector is ever read into Python (ADR-0005): an edge vector moves by
``INSERT ... SELECT`` and stays inside IRIS. A document's text is a VARCHAR and is
carried in a bind, which is why the two quarantines are written differently.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from iris_vector_graph.migrations.statements import (
    DOCS_FINISH,
    DOCS_PREPARE,
    EDGE_VECTORS_FINISH,
    EDGE_VECTORS_PREPARE,
    STAGING_SUFFIX,
    Statement,
    bind,
    bind_all,
    run_statement,
)
from iris_vector_graph.schema import GraphSchema

logger = logging.getLogger(__name__)

#: The schema every statement here qualifies. Overridable for a non-default install.
DEFAULT_SCHEMA = "Graph_KG"

#: Why a `docs` row could not be placed. A closed set, so a report can be read
#: without guessing at strings, and the same three reasons 227 records for a vector.
DOCS_QUARANTINE_REASONS: Tuple[str, ...] = (
    "no_node",
    "ambiguous_graph",
    "resolver_declined",
)

#: Why an edge vector could not be placed. ``no_edge`` rather than ``no_node``: the
#: row names a triple, and no graph asserting it is a different fact from a missing
#: node.
EDGE_VECTOR_QUARANTINE_REASONS: Tuple[str, ...] = (
    "no_edge",
    "ambiguous_graph",
    "resolver_declined",
)

#: Failures a re-run is expected to hit because a previous pass already did the work.
_ALREADY_DONE = (
    "already",
    "not unique",
    "-201",
    "not found",
    "does not exist",
    "-30>",
    "-315",
)

#: How IRIS reports the unique-constraint violation that makes a re-run idempotent.
_DUPLICATE = ("-119", "unique constraint")


@dataclass(frozen=True)
class AmbiguousDocument:
    """A `docs` row whose ID more than one graph holds a node for."""

    doc_id: str
    #: Graph IDs holding a node with this ID, in sort order. Never fewer than two:
    #: one graph is a placement and none is a quarantine, so neither reaches a
    #: resolver.
    candidate_graphs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AmbiguousEdgeVector:
    """An edge vector whose triple more than one graph asserts."""

    s: str
    p: str
    o_id: str
    candidate_graphs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PlacementReport:
    """What one placement pass decided, in enough detail to reconcile a row count.

    Deliberately a record of *this pass* rather than of the install's state, unlike
    :class:`~iris_vector_graph.migrations.graph_scoped_embeddings.MigrationReport`:
    the rows move in one statement each here, so a second run legitimately has
    nothing to report, and ``rows_accounted == 0`` is how a caller sees that the
    first run finished.
    """

    #: ``{graph_id: rows}`` — the empty string is the default graph, which is a
    #: placement like any other when a node lives only there.
    rows_placed: Dict[str, int] = field(default_factory=dict)
    #: ``{reason: rows}``, every key one of the module's declared reasons.
    rows_quarantined: Dict[str, int] = field(default_factory=dict)
    #: Keys quarantined by this pass, in the order they were read.
    quarantined_keys: List[str] = field(default_factory=list)

    @property
    def rows_accounted(self) -> int:
        """Placed + quarantined. Must equal the rows this pass found (FR-018)."""
        return sum(self.rows_placed.values()) + sum(self.rows_quarantined.values())


# ---------------------------------------------------------------------------
# The statement lists
# ---------------------------------------------------------------------------


def docs_prepare_sql(schema: str = DEFAULT_SCHEMA) -> List[str]:
    """What has to exist before a `docs` row can be placed.

    ``graph_id`` arrives nullable and with no default on purpose. ``NULL`` is the
    only thing that distinguishes "not yet placed" from "placed in the default
    graph", and a ``NOT NULL DEFAULT ''`` column would arrive with every
    pre-migration row already claiming the default graph — the placement would then
    find nothing to do and report a clean migration over a silent scope error.
    """
    return [s.sql for s in bind_all(DOCS_PREPARE, schema=schema)]


def docs_finish_sql(schema: str = DEFAULT_SCHEMA) -> List[str]:
    """Put `docs` in the fresh-4.0.0 shape, once every row has a graph.

    A rebuild rather than an ``ALTER``: 3.2.0 declared the primary key inline on
    ``id``, so there is no constraint name to drop, and the 4.0.0 key is composite.
    Rebuilding also leaves the catalog matching a fresh install column for column,
    which is the only way the two installs stay comparable (T067).

    Only placed rows are copied. The placement has already moved everything else to
    ``docs_quarantine``, text included, so the ``graph_id IS NOT NULL`` filter is
    there to keep a half-run upgrade from carrying an undecided row into the new
    table rather than because rows are expected to be sitting there.

    The indexes are recreated after the rename, under the fresh-install names, since
    the rebuild loses the original table's indexes along with the table. The iFind
    one is not optional: ``kg_TXT`` searches ``docs.text`` through it.
    """
    return [s.sql for s in bind_all(DOCS_FINISH, schema=schema)]


def edge_vectors_prepare_sql(
    dimension: int, *, dtype: str = "DOUBLE", schema: str = DEFAULT_SCHEMA
) -> List[str]:
    """The staging table an edge vector is placed into, and the quarantine.

    The staging table is shaped like a *generated* edge route rather than like the
    3.2.0 table: identity primary key (the only shape IRIS indexes with HNSW),
    ``graph_id`` in the unique key, and ``metadata``, which the routed writers name
    unconditionally. One INSERT then serves the default route and a generated one.
    """
    return [s.sql for s in bind_all(EDGE_VECTORS_PREPARE, schema=schema, dimension=dimension, dtype=dtype)]


def edge_vectors_finish_sql(schema: str = DEFAULT_SCHEMA) -> List[str]:
    """Put the staged edge vectors in the source's place.

    Nothing is copied here — the placement already wrote every row it could into
    staging — so the source is dropped and staging renamed over it. A quarantined
    vector lives in ``edge_vector_quarantine``, vector included.
    """
    return [s.sql for s in bind_all(EDGE_VECTORS_FINISH, schema=schema)]


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def prepare_docs(conn, *, schema: str = DEFAULT_SCHEMA) -> None:
    """Run the ``DOCS_PREPARE`` statements, tolerating a previous pass having run them."""
    _run_tolerantly(conn, bind_all(DOCS_PREPARE, schema=schema))


def finish_docs(conn, *, schema: str = DEFAULT_SCHEMA) -> None:
    """Run the ``DOCS_FINISH`` statements, tolerating a previous pass having run them."""
    _run_tolerantly(conn, bind_all(DOCS_FINISH, schema=schema))


def prepare_edge_vectors(
    conn, dimension: int, *, dtype: str = "DOUBLE", schema: str = DEFAULT_SCHEMA
) -> None:
    """Run the ``EDGE_VECTORS_PREPARE`` statements, tolerating a re-run.

    A staging or quarantine table a failed pass left at a different width is rebuilt first: a re-run that
    reused it refused every row again (-104), and the operator had to find and drop both by hand.
    """
    _reset_wrong_width_tables(conn, dimension, schema)
    _run_tolerantly(conn, bind_all(EDGE_VECTORS_PREPARE, schema=schema, dimension=dimension, dtype=dtype))


def _reset_wrong_width_tables(conn, dimension: int, schema: str) -> None:
    cursor = conn.cursor()
    try:
        for table in (f"{schema}.kg_EdgeEmbeddings{STAGING_SUFFIX}", f"{schema}.edge_vector_quarantine"):
            declared = GraphSchema.get_embedding_dimension(cursor, table)
            if declared is None or declared == int(dimension):
                continue
            rows = run_statement(conn, bind("edge_held_rows", schema=schema, table=table))
            held = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
            if held:
                raise RuntimeError(
                    f"{table} holds {held} row(s) at width {declared}, but the edge vectors are being placed "
                    f"at width {dimension}. Those rows cannot be moved to the new width; resolve them, then "
                    "drop the table and re-run."
                )
            logger.warning("Rebuilding empty %s: it was left at width %s, the run needs %s", table, declared, dimension)
            run_statement(conn, bind("edge_width_reset_drop", schema=schema, table=table))
        _commit(conn)
    finally:
        _close(cursor)


def finish_edge_vectors(conn, *, schema: str = DEFAULT_SCHEMA) -> None:
    """Run the ``EDGE_VECTORS_FINISH`` statements, tolerating a re-run."""
    _run_tolerantly(conn, bind_all(EDGE_VECTORS_FINISH, schema=schema))


def place_documents(
    conn,
    *,
    resolver: Optional[Callable[[AmbiguousDocument], Optional[str]]] = None,
    dry_run: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> PlacementReport:
    """Place every unplaced `docs` row in a graph, or quarantine it (FR-018).

    Args:
        conn: A DB-API connection to the namespace to migrate.
        resolver: Offered every :class:`AmbiguousDocument` — a row whose ID more than
            one graph holds a node for. Return one of ``candidate_graphs`` to place
            the row; return ``None``, or a graph that does not hold the node, to
            decline it. A declined row is quarantined, never defaulted. A quarantined
            row leaves `docs` for ``docs_quarantine`` with its text — the record an
            operator places it from — so every row left in `docs` has a graph.
        dry_run: Decide and report without writing. The prediction is the point: it
            reports the same placement and quarantine as the run it predicts.
        schema: The SQL schema holding the tables.

    Returns:
        A :class:`PlacementReport` for this pass. A second pass over a finished
        migration reports ``rows_accounted == 0``.

    Raises:
        RuntimeError: if `docs` has no ``graph_id`` column. Placing rows before the
            column exists cannot work, and pretending it did would report a clean
            migration over an untouched table.
    """
    cursor = conn.cursor()
    try:
        if not _declares(cursor, schema, "docs", "id"):
            # `finish_docs` drops `docs` one statement before renaming the rebuilt
            # table over it. A pass killed in between leaves no `docs` at all, and
            # every row already placed — in the staging table. Refusing here would
            # strand that install one statement from finished.
            if _declares(cursor, schema, f"docs{STAGING_SUFFIX}", "graph_id"):
                return PlacementReport()
            raise RuntimeError(
                f"{schema}.docs does not exist, and neither does the rebuilt "
                f"{schema}.docs{STAGING_SUFFIX}, so there is nothing to place. "
                "initialize_schema() creates it, and that call writes."
            )
        if _declares(cursor, schema, "docs", "graph_id"):
            unplaced = f"SELECT id, text FROM {schema}.docs WHERE graph_id IS NULL ORDER BY id"
        elif dry_run:
            # `prepare_docs` adds the column NULL, so until it runs every row is
            # unplaced — the prediction needs no column to read that off.
            unplaced = f"SELECT id, text FROM {schema}.docs ORDER BY id"
        else:
            raise RuntimeError(
                f"{schema}.docs has no graph_id column, so its rows cannot be placed. "
                "prepare_docs() adds it, and that call writes (ALTER TABLE); run it "
                "first, or run upgrade_to_4_0_0(), which does."
            )
        rows = _read(cursor, unplaced)
        graphs_of = _graph_map(
            cursor,
            f"SELECT DISTINCT n.node_id, n.graph_id FROM {schema}.nodes n "
            f"JOIN {schema}.docs d ON d.id = n.node_id",
            1,
        )
        report = _Tally()
        placed: Dict[str, List[str]] = {}
        moved: Dict[str, List[str]] = {}
        for row in rows:
            doc_id = str(row[0])
            graphs = graphs_of.get((doc_id,), [])
            graph, reason = _decide(
                graphs,
                resolver,
                lambda: AmbiguousDocument(doc_id=doc_id, candidate_graphs=tuple(graphs)),
                missing_reason="no_node",
            )
            if graph is not None:
                placed.setdefault(graph, []).append(doc_id)
                report.place(graph)
            else:
                moved.setdefault(reason, []).append(doc_id)
                report.quarantine(reason, doc_id)
        if not dry_run:
            for graph, ids in placed.items():
                for chunk in _chunks(ids):
                    _write(
                        cursor,
                        f"UPDATE {schema}.docs SET graph_id = ? "
                        f"WHERE graph_id IS NULL AND id IN ({_marks(len(chunk))})",
                        [graph, *chunk],
                    )
                    _commit(conn)
            for reason, ids in moved.items():
                for chunk in _chunks(ids):
                    marks = _marks(len(chunk))
                    # The row is *moved*: the copy carries the text, and only then is the
                    # original removed. A pass killed between the two statements leaves the
                    # row in both tables; NOT EXISTS makes the copy idempotent, so the next
                    # run finishes the delete -- the order that cannot lose a document.
                    _write(
                        cursor,
                        f"INSERT INTO {schema}.docs_quarantine "
                        "(doc_id, text, reason, quarantined_at) "
                        "SELECT id, text, ?, CURRENT_TIMESTAMP "
                        f"FROM {schema}.docs d WHERE d.graph_id IS NULL AND d.id IN ({marks}) "
                        f"AND NOT EXISTS (SELECT 1 FROM {schema}.docs_quarantine q "
                        "WHERE q.doc_id = d.id)",
                        [reason, *chunk],
                    )
                    _write(
                        cursor,
                        f"DELETE FROM {schema}.docs WHERE graph_id IS NULL AND id IN ({marks})",
                        list(chunk),
                    )
                    _commit(conn)
        if not dry_run:
            _commit(conn)
        return report.report()
    finally:
        _close(cursor)


def place_edge_vectors(
    conn,
    *,
    resolver: Optional[Callable[[AmbiguousEdgeVector], Optional[str]]] = None,
    dry_run: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> PlacementReport:
    """Place every pre-migration edge vector in a graph, or quarantine it (FR-019).

    The graph is the graph asserting the edge, read from ``rdf_edges``. Endpoint
    membership is not consulted: a node can sit in a graph that never asserted this
    triple, and placing a vector there would answer searches in a graph that has no
    such edge.

    Rows move by ``INSERT ... SELECT`` into the staging table
    :func:`prepare_edge_vectors` built, so no vector is read into Python (ADR-0005).
    Unlike a quarantined document, a placed or quarantined vector is not removed row
    by row: the source table keeps every row until :func:`finish_edge_vectors` drops
    it whole, because a row-by-row delete would leave a killed pass with vectors in
    neither table.

    Args:
        conn: A DB-API connection to the namespace to migrate.
        resolver: Offered every :class:`AmbiguousEdgeVector`. Same contract as
            :func:`place_documents`' resolver.
        dry_run: Decide and report without writing.
        schema: The SQL schema holding the tables.

    Returns:
        A :class:`PlacementReport` for this pass.
    """
    source = f"{schema}.kg_EdgeEmbeddings"
    staging = f"{schema}.kg_EdgeEmbeddings{STAGING_SUFFIX}"
    quarantine = f"{schema}.edge_vector_quarantine"
    cursor = conn.cursor()
    try:
        done = _quarantined_keys(cursor, f"SELECT s, p, o_id FROM {staging}")
        done |= _quarantined_keys(cursor, f"SELECT s, p, o_id, reason FROM {quarantine}")
        rows = _read(cursor, f"SELECT s, p, o_id FROM {source} ORDER BY s, p, o_id")
        graphs_of = _graph_map(
            cursor,
            f"SELECT DISTINCT r.s, r.p, r.o_id, r.graph_id FROM {schema}.rdf_edges r "
            f"JOIN {source} e ON e.s = r.s AND e.p = r.p AND e.o_id = r.o_id",
            3,
        )
        report = _Tally()
        each_row: List[Tuple[str, str, str]] = []  # decided only by a resolver: one statement each
        for row in rows:
            key = (str(row[0]), str(row[1]), str(row[2]))
            if key in done:
                continue
            graphs = graphs_of.get(key, [])
            graph, reason = _decide(
                graphs,
                resolver,
                lambda: AmbiguousEdgeVector(*key, candidate_graphs=tuple(graphs)),
                missing_reason="no_edge",
            )
            if len(graphs) > 1:
                each_row.append((key, graph, reason))
            if graph is not None:
                report.place(graph)
            else:
                report.quarantine(reason, "|".join(key))
        if not dry_run:
            # Edge vectors asserted by exactly one graph, and those no edge asserts, move in one
            # statement each: the join carries the decision the loop above reached for them.
            _execute(conn, bind("edge_place_unique_graph", schema=schema))
            _execute(conn, bind("edge_quarantine_no_edge", schema=schema))
            for key, graph, reason in each_row:
                if graph is not None:
                    _execute(
                        conn,
                        bind("edge_place_by_key", schema=schema),
                        [graph, *key],
                        duplicate_is_done=True,
                    )
                else:
                    _execute(
                        conn,
                        bind("edge_quarantine_by_key", schema=schema),
                        [reason, *key],
                        duplicate_is_done=True,
                    )
            _commit(conn)
        return report.report()
    finally:
        _close(cursor)


def migrate_docs_and_edge_vectors(
    conn,
    dimension: int,
    *,
    document_resolver: Optional[Callable[[AmbiguousDocument], Optional[str]]] = None,
    edge_resolver: Optional[Callable[[AmbiguousEdgeVector], Optional[str]]] = None,
    dtype: str = "DOUBLE",
    dry_run: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> Dict[str, PlacementReport]:
    """Prepare, place and finish both tables, in the one order that works.

    ``dimension`` is the declared width the rebuilt edge table gets; it has to match
    the source column, because IRIS checks a declared width at INSERT (SQLCODE -104)
    and a narrower staging column would refuse the rows it is there to receive.

    A dry run prepares and finishes nothing, so it reports what a real run would
    place without touching the catalog.

    Returns:
        ``{"docs": report, "edge_vectors": report}``.
    """
    if not dry_run:
        prepare_docs(conn, schema=schema)
        prepare_edge_vectors(conn, dimension, dtype=dtype, schema=schema)
    docs = place_documents(conn, resolver=document_resolver, dry_run=dry_run, schema=schema)
    edges = place_edge_vectors(conn, resolver=edge_resolver, dry_run=dry_run, schema=schema)
    if not dry_run:
        finish_docs(conn, schema=schema)
        finish_edge_vectors(conn, schema=schema)
    return {"docs": docs, "edge_vectors": edges}


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


class _Tally:
    """Counts one pass's decisions, so both placements report the same way."""

    def __init__(self):
        self.placed: Dict[str, int] = {}
        self.quarantined: Dict[str, int] = {}
        self.keys: List[str] = []

    def place(self, graph: str) -> None:
        self.placed[graph] = self.placed.get(graph, 0) + 1

    def quarantine(self, reason: str, key: str) -> None:
        self.quarantined[reason] = self.quarantined.get(reason, 0) + 1
        self.keys.append(key)

    def report(self) -> PlacementReport:
        return PlacementReport(
            rows_placed=dict(self.placed),
            rows_quarantined=dict(self.quarantined),
            quarantined_keys=list(self.keys),
        )


def _decide(
    graphs: Sequence[str],
    resolver,
    describe: Callable[[], object],
    *,
    missing_reason: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Which graph a row belongs to, or why nobody can say.

    The resolver is consulted only when there is a genuine choice: one candidate is
    already the answer, and none leaves nothing to choose between — a resolver
    answer there would be an invention, and the row is kept for a human instead.
    """
    if len(graphs) == 1:
        return graphs[0], None
    if not graphs:
        return None, missing_reason
    answer = resolver(describe()) if resolver is not None else None
    if answer is not None and answer in graphs:
        return answer, None
    # A resolver naming a graph that does not claim the row has declined: honouring
    # it would assert a membership no row in the database supports.
    return None, "resolver_declined" if resolver is not None else "ambiguous_graph"


def _declares(cursor, schema: str, table: str, column: str) -> bool:
    rows = _read(
        cursor,
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' "
        "AND COLUMN_NAME = ?",
        [column],
    )
    return bool(rows and rows[0] and rows[0][0])


def _read(cursor, sql: str, params=None) -> List[tuple]:
    cursor.execute(sql, list(params or []))
    return list(cursor.fetchall() or [])


#: Rows per statement when a placement is written; see graph_scoped_embeddings.BATCH_ROWS.
BATCH_ROWS = 400


def _chunks(items, size: Optional[int] = None):
    size = size or BATCH_ROWS  # read at call time, so a test can shrink the batch
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _marks(count: int) -> str:
    return ", ".join("?" * count)


def _graph_map(cursor, sql: str, width: int) -> Dict[tuple, List[str]]:
    """``{key: sorted graphs}`` from one read of ``key columns..., graph_id`` rows."""
    found: Dict[tuple, set] = {}
    for row in _read(cursor, sql):
        if row:
            found.setdefault(tuple(str(v) for v in row[:width]), set()).add(
                "" if row[width] is None else str(row[width])
            )
    return {key: sorted(graphs) for key, graphs in found.items()}


def _graphs_of(cursor, sql: str, params) -> List[str]:
    rows = _read(cursor, sql, params)
    return sorted({("" if r[0] is None else str(r[0])) for r in rows if r})


def _quarantined_keys(cursor, sql: str) -> set:
    """Keys a previous pass already accounted for, or an empty set if the table is not
    there yet — which is what a first run finds."""
    try:
        rows = _read(cursor, sql)
    except Exception as e:
        logger.debug("Nothing accounted for yet (%s): %s", sql, e)
        return set()
    keys = set()
    for row in rows:
        if not row:
            continue
        # The trailing `reason` column, where there is one, is not part of the key.
        width = 3 if len(row) >= 4 else len(row)
        if len(row) == 2:
            width = 1
        keys.add(tuple(str(v) for v in row[:width]))
    return keys


@contextmanager
def _tolerating_duplicate(duplicate_is_done: bool) -> Iterator[None]:
    try:
        yield
    except Exception as e:
        if duplicate_is_done and _matches(e, _DUPLICATE):
            # A previous pass, or a concurrent writer, already placed this row. The
            # constraint is the idempotence mechanism, so the migration has to
            # survive the violation it relies on.
            logger.debug("Already accounted for: %s", e)
            return
        raise


def _write(cursor, sql: str, params, *, duplicate_is_done: bool = False) -> None:
    with _tolerating_duplicate(duplicate_is_done):
        cursor.execute(sql, list(params or []))


def _run_tolerantly(conn, statements: Sequence[Statement]) -> None:
    for stmt in statements:
        try:
            run_statement(conn, stmt)
        except Exception as e:
            if _matches(e, _ALREADY_DONE):
                logger.debug("Already done: %s (%s)", stmt.sql.split("\n")[0], e)
                continue
            raise
    _commit(conn)


def _execute(conn, stmt: Statement, params=None, *, duplicate_is_done: bool = False) -> None:
    """Run one fixed statement; a unique-key violation is done work when the caller says so."""
    with _tolerating_duplicate(duplicate_is_done):
        run_statement(conn, stmt, params)


def _matches(exc: Exception, needles) -> bool:
    text = str(exc).lower()
    return any(n.lower() in text for n in needles)


def _commit(conn) -> None:
    try:
        conn.commit()
    except Exception as e:  # pragma: no cover - autocommit connections
        logger.debug("Commit not needed: %s", e)


def _close(cursor) -> None:
    try:
        cursor.close()
    except Exception:  # pragma: no cover - driver-dependent
        pass
