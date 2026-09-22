"""One call that takes a 3.2.0 install to 4.0.0 (spec 230, T067, SC-008).

Four steps ship in this package and an operator has to run all of them, in one
order, because each reads what the one before it wrote:

1. **embeddings** (spec 227) — node vectors move into graph-scoped routed tables and
   `nodes` gains `UNIQUE (graph_id, node_id)`. It goes first because it is what makes
   a node ID ambiguous; the two placements below exist to answer that ambiguity
   rather than to be surprised by it.
2. **docs** (spec 230 FR-018) — `Graph_KG.docs` is re-keyed `(graph_id, id)` and `id`
   becomes a node ID.
3. **edge_vectors** (FR-019) — `Graph_KG.kg_EdgeEmbeddings` is re-keyed
   `(graph_id, s, p, o_id)` behind an identity primary key.
4. **kg_node_stores** (FR-010/FR-011) — the four flat `^KG` subtrees are dropped and
   the server rebuilds them keyed by graph, from the SQL rows.

**Why a step reports "skipped" rather than just running again.** The two placements
are not no-ops on a finished install. `docs_finish_sql` rebuilds the table from
whatever `graph_id` currently holds, and the edge sequence re-derives each vector's
graph from `rdf_edges` into a fresh staging table before dropping the source over it.
Run against an install that is already 4.0.0 they would migrate migrated data: an
operator who has since moved a node between graphs, or asserted the same triple in a
second graph, would watch rows move or land in quarantine on a *second* run of a
migration that had already succeeded. So each placement reads the catalog first, and
a table already in the 4.0.0 shape is declined by name.

The other two steps are re-runnable by construction and are not guarded: spec 227's
pass reports the install's state and reshapes only a legacy-shaped table, and the
`^KG` re-key is a kill followed by a deterministic rebuild from the rows, so a second
pass lands exactly where the first did. That asymmetry is the whole design — a step
is guarded when re-running it would *decide* something again, not merely do work
again.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Tuple

from .docs_and_edge_vectors import (
    STAGING_SUFFIX,
    AmbiguousDocument,
    AmbiguousEdgeVector,
    PlacementReport,
    finish_docs,
    finish_edge_vectors,
    place_documents,
    place_edge_vectors,
    prepare_docs,
    prepare_edge_vectors,
)
from .graph_scoped_embeddings import AmbiguousVector, migrate_to_graph_scoped_embeddings
from .kg_node_stores import rekey_kg_node_stores

logger = logging.getLogger(__name__)

#: The schema the SQL steps qualify. Overridable for a non-default install.
DEFAULT_SCHEMA = "Graph_KG"

#: Every step, in the only order that works. Also the order a report lists them in.
UPGRADE_STEPS: Tuple[str, ...] = ("embeddings", "docs", "edge_vectors", "kg_node_stores")


@dataclass(frozen=True)
class StepResult:
    """What one step of the upgrade did, or why it did nothing."""

    name: str
    #: The step's own report — a :class:`MigrationReport`, :class:`PlacementReport` or
    #: :class:`KgRekeyReport`. ``None`` exactly when the step was skipped.
    report: Optional[Any] = None
    #: Why the step was declined, in words an operator can act on. ``None`` when it
    #: ran.
    skipped_because: Optional[str] = None

    @property
    def skipped(self) -> bool:
        return self.skipped_because is not None


@dataclass(frozen=True)
class UpgradeReport:
    """Every step's outcome, in execution order."""

    steps: Tuple[StepResult, ...] = field(default_factory=tuple)
    #: `{qualified table: tuned}` for the statistics collected after the last step.
    #: Empty on a dry run, which writes nothing, statistics included.
    tuned: dict = field(default_factory=dict)

    def __getitem__(self, name: str) -> StepResult:
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    @property
    def skipped(self) -> Tuple[str, ...]:
        """The steps that found their work already done."""
        return tuple(step.name for step in self.steps if step.skipped)

    @property
    def rows_accounted(self) -> int:
        """Placed + quarantined across the steps that count rows that way."""
        total = 0
        for step in self.steps:
            rows = getattr(step.report, "rows_accounted", None)
            if isinstance(rows, int):
                total += rows
        return total


def upgrade_to_4_0_0(
    conn,
    *,
    steps: Optional[Sequence[str]] = None,
    edge_dimension: Optional[int] = None,
    dtype: str = "DOUBLE",
    vector_resolver: Optional[Callable[[AmbiguousVector], Optional[str]]] = None,
    document_resolver: Optional[Callable[[AmbiguousDocument], Optional[str]]] = None,
    edge_resolver: Optional[Callable[[AmbiguousEdgeVector], Optional[str]]] = None,
    dry_run: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> UpgradeReport:
    """Run the 3.2.0 → 4.0.0 migration, reporting each step.

    Args:
        conn: A DB-API connection to the namespace to migrate. The `^KG` step also
            uses its Native API handle.
        steps: Run only these, from :data:`UPGRADE_STEPS`, still in that order.
            Omitted runs all four. Naming a subset is for an install that has already
            had one step run against it by hand, and for the tests that migrate a
            fixture in a scratch schema — the embeddings and `^KG` steps address the
            default schema and the real globals, so a scratch-schema run names the two
            placements only.
        edge_dimension: The width the rebuilt edge-vector table is declared at. Read
            off the source column when omitted. It has to match: IRIS checks a
            declared VECTOR width at INSERT (SQLCODE -104), so a narrower staging
            column refuses the rows it exists to receive.
        dtype: The rebuilt edge table's vector dtype.
        vector_resolver: Offered every ambiguous node vector (spec 227).
        document_resolver: Offered every `docs` row whose ID more than one graph holds
            a node for.
        edge_resolver: Offered every edge vector whose triple more than one graph
            asserts.
        dry_run: Decide and report, write nothing. Each step's own dry run.
        schema: The SQL schema the two placements address.

    Returns:
        An :class:`UpgradeReport`. Safe to re-run: a step whose table is already in
        the 4.0.0 shape reports itself skipped rather than migrating migrated data.

    Raises:
        ValueError: if `steps` names something that is not an upgrade step.
    """
    wanted = _resolve_steps(steps)
    results = []
    for name in UPGRADE_STEPS:
        if name not in wanted:
            continue
        results.append(
            _run_step(
                name,
                conn,
                edge_dimension=edge_dimension,
                dtype=dtype,
                vector_resolver=vector_resolver,
                document_resolver=document_resolver,
                edge_resolver=edge_resolver,
                dry_run=dry_run,
                schema=schema,
            )
        )
    # Three of the four steps re-key a table by copying its rows into a staging table
    # and dropping the source over it, so the statistics the optimizer holds describe a
    # table that no longer exists — and a freshly built one has none. With none, a
    # graph-scoped `(node_id, graph_id)` lookup reads the master map, which is a scan.
    # Collect them here, while the migration still owns the connection.
    tuned = {} if dry_run else _tune(conn, schema)
    return UpgradeReport(steps=tuple(results), tuned=tuned)


def _tune(conn, schema: str) -> dict:
    """Collect table statistics for the migrated schema, reporting per table."""
    from ..schema import GraphSchema

    cursor = conn.cursor()
    try:
        status = GraphSchema.tune_tables(cursor, schema=schema)
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass
    return status


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def _resolve_steps(steps: Optional[Sequence[str]]) -> Tuple[str, ...]:
    if steps is None:
        return UPGRADE_STEPS
    named = tuple(steps)
    unknown = [name for name in named if name not in UPGRADE_STEPS]
    if unknown:
        raise ValueError(
            f"not upgrade steps: {', '.join(sorted(unknown))}. "
            f"Known steps, in order: {', '.join(UPGRADE_STEPS)}"
        )
    return named


def _run_step(name: str, conn, **kw) -> StepResult:
    dry_run = kw["dry_run"]
    schema = kw["schema"]

    if name == "embeddings":
        # Unguarded on purpose: this pass already reads the install's shape per table
        # and reshapes only a legacy one, and its report describes the state it found
        # rather than the work it did.
        return StepResult(
            name,
            report=migrate_to_graph_scoped_embeddings(
                conn, resolver=kw["vector_resolver"], dry_run=dry_run
            ),
        )

    if name == "docs":
        already = _docs_already_migrated(conn, schema)
        if already:
            logger.info("Skipping the docs re-key: %s", already)
            return StepResult(name, skipped_because=already)
        return StepResult(name, report=_migrate_docs(conn, **kw))

    if name == "edge_vectors":
        already = _edge_vectors_already_migrated(conn, schema)
        if already:
            logger.info("Skipping the edge-vector re-key: %s", already)
            return StepResult(name, skipped_because=already)
        return StepResult(name, report=_migrate_edge_vectors(conn, **kw))

    # The kill-and-rebuild step. Re-running it is the same work, not a second
    # decision, so there is nothing to guard.
    return StepResult(
        name,
        report=rekey_kg_node_stores(conn, dry_run=dry_run, schema=schema),
    )


def _migrate_docs(conn, **kw) -> PlacementReport:
    schema = kw["schema"]
    dry_run = kw["dry_run"]
    if not dry_run:
        prepare_docs(conn, schema=schema)
    report = place_documents(
        conn, resolver=kw["document_resolver"], dry_run=dry_run, schema=schema
    )
    if not dry_run:
        finish_docs(conn, schema=schema)
    return report


def _migrate_edge_vectors(conn, **kw) -> PlacementReport:
    schema = kw["schema"]
    dry_run = kw["dry_run"]
    dimension = kw["edge_dimension"] or _edge_width(conn, schema)
    if not dimension:
        raise RuntimeError(
            f"{schema}.kg_EdgeEmbeddings declares no vector width, so the rebuilt "
            "table cannot be declared either. Pass edge_dimension= explicitly."
        )
    if not dry_run:
        prepare_edge_vectors(conn, dimension, dtype=kw["dtype"], schema=schema)
    report = place_edge_vectors(
        conn, resolver=kw["edge_resolver"], dry_run=dry_run, schema=schema
    )
    if not dry_run:
        finish_edge_vectors(conn, schema=schema)
    return report


# ---------------------------------------------------------------------------
# Reading the shape
# ---------------------------------------------------------------------------


def _docs_already_migrated(conn, schema: str) -> Optional[str]:
    """Why the `docs` re-key has nothing to do, or ``None`` if it does.

    ``graph_id NOT NULL`` is the signal because only ``docs_finish_sql`` produces it:
    ``docs_prepare_sql`` adds the column *nullable*, since ``NULL`` is the only thing
    that distinguishes "not yet placed" from "placed in the default graph". A staging
    table still present means a previous pass died mid-rebuild, and that install wants
    finishing, not declining.
    """
    cursor = conn.cursor()
    try:
        if not _declares(cursor, schema, "docs", "graph_id"):
            return None
        if _declares(cursor, schema, f"docs{STAGING_SUFFIX}", "graph_id"):
            return None
        if _nullable(cursor, schema, "docs", "graph_id"):
            return None
        return (
            f"{schema}.docs already declares graph_id NOT NULL, which only the 4.0.0 "
            "rebuild produces. Re-running the placement would re-derive every row's "
            "graph from the current nodes and rebuild the table around the answer."
        )
    finally:
        _close(cursor)


def _edge_vectors_already_migrated(conn, schema: str) -> Optional[str]:
    """Why the edge-vector re-key has nothing to do, or ``None`` if it does."""
    cursor = conn.cursor()
    try:
        if not _declares(cursor, schema, "kg_EdgeEmbeddings", "graph_id"):
            return None
        if _declares(cursor, schema, f"kg_EdgeEmbeddings{STAGING_SUFFIX}", "graph_id"):
            return None
        return (
            f"{schema}.kg_EdgeEmbeddings already declares graph_id, so it is in the "
            "4.0.0 shape. Re-running the placement would re-derive every vector's "
            "graph from rdf_edges and drop the table it read them from."
        )
    finally:
        _close(cursor)


def _edge_width(conn, schema: str) -> Optional[int]:
    from ..schema import GraphSchema

    cursor = conn.cursor()
    try:
        return GraphSchema.get_embedding_dimension(cursor, f"{schema}.kg_EdgeEmbeddings")
    except Exception as exc:  # pragma: no cover - probe, not a guarantee
        logger.debug("Could not read %s.kg_EdgeEmbeddings' width: %s", schema, exc)
        return None
    finally:
        _close(cursor)


def _declares(cursor, schema: str, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
        [schema, table, column],
    )
    rows = list(cursor.fetchall() or [])
    return bool(rows and rows[0] and rows[0][0])


def _nullable(cursor, schema: str, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
        [schema, table, column],
    )
    rows = list(cursor.fetchall() or [])
    if not rows or rows[0] is None or rows[0][0] is None:
        return True  # unknown reads as "not yet proven migrated"
    return str(rows[0][0]).strip().upper() in ("YES", "1", "TRUE")


def _close(cursor) -> None:
    try:
        cursor.close()
    except Exception:  # pragma: no cover
        pass
