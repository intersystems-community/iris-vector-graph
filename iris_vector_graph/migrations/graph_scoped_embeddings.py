"""The 4.0.0 migration: namespace-wide embeddings become graph-scoped routes.

A 3.2.0 install holds one unscoped table per kind — `id VARCHAR PRIMARY KEY`, one
`emb` column, no `graph_id` — and one `adopted` or `claimed` registry row at
`graph_id = ''`. 4.0.0 keys a vector by `(graph_id, node_id)` and reads the pair
`(graph, model_key)` through the registry, so this has to move rows, not just alter
columns: no `ALTER` reaches `(graph_id, node_id)` from a VARCHAR primary key.

Four rules shape the implementation, and each one is a test in
`tests/unit/test_227_migration_bookkeeping.py` or `..._idempotence.py`:

* **Every pre-upgrade row is placed or quarantined** (FR-026). The report's arithmetic
  is what makes that checkable from outside, so it is derived from the state the
  migration left, not from a log of what this pass did — a resumed run reports the
  whole install, not its own half.
* **An unplaceable row is never given the default graph** (FR-028). A row whose node
  no graph holds, or that two graphs could both claim, goes to
  `Graph_KG.embedding_quarantine` by name and leaves only by operator placement. A
  single-graph install is the one case where there is nothing to guess: every node in
  it is in that graph, so naming the graph on its registry row is the whole migration
  for that table (FR-027, FR-031).
* **No vector is ever read into Python** (ADR-0005). A `VECTOR(DOUBLE, 768)` fetched
  and re-bound is reshaped by the driver's idea of the value, so every row moves by
  `INSERT ... SELECT` and stays inside IRIS.
* **It is re-runnable** (FR-029). The interruption this job actually suffers is being
  killed, so the fix has to be "run it again": the drain resumes from a watermark read
  off its own destinations, and the target's `(graph_id, node_id)` unique constraint —
  not a check-then-insert — absorbs a row an earlier pass already moved.

The re-key of `nodes`, `rdf_labels` and `rdf_props` (FR-036) is refused rather than
forced when a child row's node is not in exactly one graph: that row's graph is
genuinely unrecoverable, and `MIN(graph_id)` would be the silent version of this
migration. Those rows are counted and named in ``MigrationReport.structural_blockers``.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.routing import route_table_name
from iris_vector_graph.schema import GraphSchema

logger = logging.getLogger(__name__)

#: The tables a 3.2.0 install can hold node vectors in, in the order they are drained.
#:
#: ``kg_EdgeEmbeddings`` is absent because its rows are not placed by resolving a
#: node's graph: it is keyed `(s, p, o_id)`, so a row names two endpoints and a
#: predicate, and the graph that holds it is the graph holding the *edge*. Spec 230
#: ends the exemption — edge vectors are routed per `(graph, model_key)` like node
#: vectors — but they are placed by
#: :func:`iris_vector_graph.migrations.docs_and_edge_vectors.place_edge_vectors`,
#: which resolves an edge's graph from ``rdf_edges``, not a node's from ``nodes``.
SOURCE_TABLES = ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized")

#: The unique constraint a fresh 4.0.0 install declares on each of those tables.
#: The staging table carries the same name so the catalog after the rename matches a
#: fresh install column-for-column and constraint-for-constraint (T067).
_UNIQUE_CONSTRAINTS = {
    "kg_NodeEmbeddings": "uq_emb_graph_node",
    "kg_NodeEmbeddings_optimized": "uq_emb_opt_graph_node",
}

#: The composite reference to ``nodes`` on each of those tables (FR-008).  Same names
#: 3.2.0 used, re-declared over ``(graph_id, node_id)``: the constraint is not being
#: retired, only re-pointed, and a fresh install declares it with the same names.
_FOREIGN_KEYS = {
    "kg_NodeEmbeddings": "fk_emb_node",
    "kg_NodeEmbeddings_optimized": "fk_emb_node_opt",
}

#: Suffix of the table a drain fills before it is renamed into the source's place.
_STAGING_SUFFIX = "_ivg400"

#: The two child tables the re-key gives a ``graph_id`` of their own (FR-036).
_CHILD_TABLES = ("rdf_labels", "rdf_props")

#: Failures a re-run is expected to hit because a previous pass already did the work.
_ALREADY_DONE = (
    "already",
    "not unique",
    "-201",
    "not found",
    "does not exist",
    "-30>",
    "-315",
    # An `ADD CONSTRAINT` whose index IRIS has already built. It reports the *index*
    # name it derives from the constraint, inside a -400, and says nothing about
    # "already": `ERROR #5067: Index name conflict: uqnodesgraphnode`. The v3.2.0
    # `rdf_edges` rescue (FR-027) now adds `uq_nodes_graph_node` itself, so every
    # real upgrade reaches `_rekey_children` with that key in place.
    "index name conflict",
)

#: How IRIS reports the unique-constraint violation that makes a re-run idempotent.
_DUPLICATE = ("-119", "unique constraint")


@dataclass(frozen=True)
class AmbiguousVector:
    """A row the migration cannot place without being told which graph it is in."""

    node_id: str
    source_table: str
    dimension: int
    dtype: str
    #: Graph IDs that hold a node with this ID. Empty means no node holds it.
    candidate_graphs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationReport:
    """What the migration did, in enough detail to reconcile against a row count."""

    rows_placed: Dict[Tuple[str, str], int] = field(default_factory=dict)
    rows_quarantined: Dict[str, int] = field(default_factory=dict)
    quarantined_ids: List[str] = field(default_factory=list)
    tables_created: List[str] = field(default_factory=list)
    indexes_created: List[str] = field(default_factory=list)
    indexes_refused: Dict[str, str] = field(default_factory=dict)
    widths_reconciled: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    #: ``{child_table: [node_id, ...]}`` for rows whose node is not in exactly one
    #: graph. Non-empty means the structural re-key was refused, not attempted.
    structural_blockers: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def rows_accounted(self) -> int:
        """Placed + quarantined. Must equal the pre-upgrade row count (FR-026)."""
        return sum(self.rows_placed.values()) + sum(self.rows_quarantined.values())


@dataclass(frozen=True)
class _Row:
    """One source row's fate. ``graph`` is ``None`` exactly when ``reason`` is set."""

    node_id: str
    graph: Optional[str]
    reason: Optional[str]


@dataclass
class _Plan:
    """What one source table's rows are going to do, decided before anything moves."""

    source: str
    shape: str
    dimension: Optional[int]
    dtype: str
    model_key: Optional[str]
    identity_key: Optional[str]
    registry_graph: str
    table_graph: str
    rows: List[_Row]
    route_for: Dict[str, str]
    needs_routes: List[str]


def migrate_to_graph_scoped_embeddings(
    conn,
    *,
    resolver: Optional[Callable[[AmbiguousVector], Optional[str]]] = None,
    dry_run: bool = False,
) -> MigrationReport:
    """Move a 3.2.0 installation's embeddings into graph-scoped routed tables.

    Args:
        conn: A DB-API connection to the namespace to migrate.
        resolver: Offered every :class:`AmbiguousVector` — a row whose node ID more
            than one graph holds. Return one of ``candidate_graphs`` to place the row;
            return ``None`` to decline it. A declined row is quarantined, never
            defaulted, so declining and having no resolver reach the same placement
            and differ only in the reason recorded (FR-030).
        dry_run: Plan and report without writing. The prediction is the point: it
            reports the same placement, quarantine and table list as the run it
            predicts.

    Returns:
        A :class:`MigrationReport` describing the state the install is now in.
    """
    return _Migrator(conn, resolver=resolver, dry_run=dry_run).run()


class _Migrator:
    """One migration pass. Holds the cursor-level plumbing the steps share."""

    def __init__(self, conn, *, resolver, dry_run):
        self.conn = conn
        self.resolver = resolver
        self.dry_run = dry_run
        self.engine = None
        self.indexes_created: List[str] = []
        self.indexes_refused: Dict[str, str] = {}
        self.widths_reconciled: Dict[str, Tuple[int, int]] = {}

    # ------------------------------------------------------------------ the pass

    def run(self) -> MigrationReport:
        from iris_vector_graph.engine import IRISGraphEngine

        # An explicit dtype keeps construction from probing a stored vector for one
        # (ADR-0005). Nothing reads it: every route this migration creates is given
        # the dtype recorded for the table whose rows it is taking.
        self.engine = IRISGraphEngine(self.conn, vector_dtype="DOUBLE")
        cursor = self.conn.cursor()
        try:
            blockers = self._structural_blockers(cursor)
            if not blockers and not self.dry_run:
                self._rekey_children(cursor)

            # A refused re-key leaves `nodes` keyed by node_id alone, so the composite
            # reference a rebuilt embedding table declares has nothing to point at
            # (SQLCODE -121). Reshaping anyway would produce a default route with no
            # reference while every generated route has one — the asymmetry FR-008 is
            # there to prevent. Nothing is written, so the report is the predicted one
            # and it carries the blockers that stopped the pass.
            writing = not self.dry_run and not blockers

            plans: List[_Plan] = []
            reshaped = False
            for source in SOURCE_TABLES:
                plan = self._plan(cursor, source)
                if plan is None:
                    continue
                plans.append(plan)
                if not writing:
                    continue
                if plan.shape == "legacy":
                    self._drain(cursor, plan)
                    self._reshape(cursor, plan)
                    reshaped = True
                self._redistribute(cursor, plan)
                self._rewrite_registry(cursor, plan)

            if reshaped:
                self._install_procedures(cursor)

            if not writing:
                return self._predicted(plans, blockers)
            return self._observed(cursor, plans, blockers)
        finally:
            self._close(cursor)

    # --------------------------------------------------------- the structural half

    def _structural_blockers(self, cursor) -> Dict[str, List[str]]:
        """Child rows whose node is not in exactly one graph (FR-036).

        Read before anything is written, because the answer decides whether the re-key
        can run at all: its ``ALTER COLUMN graph_id NOT NULL`` fails on exactly these
        rows, and the failure would land half way through a statement list whose order
        is not interchangeable.
        """
        blockers: Dict[str, List[str]] = {}
        nodes = self._t("nodes")
        for table in _CHILD_TABLES:
            if not self._declares(cursor, table, "s"):
                continue  # not this schema's table
            if self._declares(cursor, table, "graph_id"):
                continue  # already re-keyed, so every row has a graph
            rows = self._read(
                cursor,
                f"SELECT c.s FROM {self._t(table)} c WHERE "
                f"(SELECT COUNT(DISTINCT n.graph_id) FROM {nodes} n "
                f"WHERE n.node_id = c.s) <> 1",
            )
            named = sorted({str(r[0]) for r in rows if r and r[0] is not None})
            if named:
                blockers[table] = named
        if blockers:
            logger.warning(
                "Refusing the graph re-key: %s. Their graph is unrecoverable from the "
                "data, and assigning one would be a guess (FR-036).",
                ", ".join(f"{t}: {len(ids)} row(s)" for t, ids in blockers.items()),
            )
        return blockers

    def _rekey_children(self, cursor) -> None:
        """Run `contracts/sql-schema.md` §4 in its exact order, once."""
        if self._declares(cursor, "rdf_labels", "graph_id"):
            return  # a previous pass already re-keyed this schema
        for sql in GraphSchema.get_graph_scope_migration_sql():
            self._tolerate(cursor, sql)
        self._commit()

    # ----------------------------------------------------------------- the plan

    def _plan(self, cursor, source: str) -> Optional[_Plan]:
        shape = self._shape(cursor, source)
        if shape is None:
            return None
        if shape == "reshaping":
            # A previous pass died between the DROP and the rename, so the rows are all
            # in staging and the source is gone. Finish the rename before planning.
            self._rename(cursor, source + _STAGING_SUFFIX, source)
            shape = "routed"

        registry = self._registry_row(cursor, source)
        dimension = self._declared_width(cursor, source) or self._int(
            registry.get("dimension")
        )
        dtype = str(registry.get("dtype") or self.engine.vector_dtype or "DOUBLE").upper()
        model_key = registry.get("model_key")
        identity_key = self.engine._offered_embedding_identity(config=model_key).model_key

        key_column = "node_id" if shape == "routed" else "id"
        rows: List[_Row] = []
        candidates_seen: set = set()
        for node_id in self._keys(cursor, source, key_column):
            graphs = self._graphs_of(cursor, node_id)
            candidates_seen.update(graphs)
            rows.append(self._fate(node_id, graphs, source, dimension, dtype))

        registry_graph = str(registry.get("graph_id") or DEFAULT_GRAPH)
        if shape == "routed" and registry:
            table_graph = registry_graph
        else:
            table_graph = (
                candidates_seen.copy().pop()
                if len(candidates_seen) == 1 and candidates_seen != {DEFAULT_GRAPH}
                else DEFAULT_GRAPH
            )

        route_for: Dict[str, str] = {}
        needs_routes: List[str] = []
        known = self._registry_pairs(cursor)
        for row in rows:
            if row.graph is None or row.graph == table_graph or row.graph in route_for:
                continue
            route = route_table_name(row.graph, identity_key)
            route_for[row.graph] = route
            if (route, row.graph) not in known:
                needs_routes.append(route)

        return _Plan(
            source=source,
            shape=shape,
            dimension=dimension,
            dtype=dtype,
            model_key=model_key,
            identity_key=identity_key,
            registry_graph=registry_graph,
            table_graph=table_graph,
            rows=rows,
            route_for=route_for,
            needs_routes=needs_routes,
        )

    def _fate(self, node_id, graphs, source, dimension, dtype) -> _Row:
        """Which graph a row belongs to, or why nobody can say."""
        if len(graphs) == 1:
            return _Row(node_id, graphs[0], None)
        if not graphs:
            return _Row(node_id, None, "no_node")
        answer = None
        if self.resolver is not None:
            answer = self.resolver(
                AmbiguousVector(
                    node_id=node_id,
                    source_table=source,
                    dimension=int(dimension or 0),
                    dtype=dtype,
                    candidate_graphs=tuple(graphs),
                )
            )
        if answer is not None and answer in graphs:
            return _Row(node_id, answer, None)
        # A resolver naming a graph that does not hold the node has declined: honouring
        # it would invent a membership no `nodes` row supports, and the route's
        # composite foreign key would refuse the INSERT anyway.
        reason = "resolver_declined" if self.resolver is not None else "ambiguous_graph"
        return _Row(node_id, None, reason)

    # ---------------------------------------------------------------- the drain

    def _drain(self, cursor, plan: _Plan) -> None:
        """Copy every planned row out of the 3.2.0 table, resuming from a watermark.

        The watermark is read off the drain's own two destinations — staging and the
        quarantine — rather than tracked anywhere, so a killed pass leaves the only
        state a resume needs. The re-read of the source is keyed above it: a resumed
        pass asks for the rows it still owes, not for every row it already copied.
        """
        staging = plan.source + _STAGING_SUFFIX
        self._create_embedding_table(cursor, staging, plan)
        watermark = self._later(
            self._max(cursor, staging, "node_id"),
            self._max_quarantined(cursor, plan.source),
        )
        pending = set(self._keys(cursor, plan.source, "id", after=watermark or ""))
        source = self._t(plan.source)
        quarantine = self.engine._quarantine_table()
        for row in plan.rows:
            if row.node_id not in pending:
                continue
            if row.graph is None:
                sql = (
                    f"INSERT INTO {quarantine} (node_id, source_table, dimension, "
                    "dtype, reason, emb, metadata, quarantined_at) "
                    "SELECT id, ?, ?, ?, ?, emb, metadata, CURRENT_TIMESTAMP "
                    f"FROM {source} WHERE id = ?"
                )
                params = [plan.source, plan.dimension, plan.dtype, row.reason, row.node_id]
            else:
                sql = (
                    f"INSERT INTO {self._t(staging)} (graph_id, node_id, emb, metadata) "
                    f"SELECT ?, id, emb, metadata FROM {source} WHERE id = ?"
                )
                params = [row.graph, row.node_id]
            self._move(cursor, sql, params)
        self._commit()

    def _reshape(self, cursor, plan: _Plan) -> None:
        """Put the drained table in the source's place, in the 4.0.0 shape."""
        staging = plan.source + _STAGING_SUFFIX
        self._tolerate(cursor, f"DROP TABLE {self._t(plan.source)}")
        self._commit()
        self._rename(cursor, staging, plan.source)
        state, error = self.engine._attempt_route_index(cursor, plan.source)
        self._note_index(plan.source, state, error)
        self.engine._record_route_index_state(
            cursor, plan.source, plan.registry_graph, state, error
        )
        self._commit()

    def _install_procedures(self, cursor) -> None:
        """Declare the procedures the pre-migration install had to defer.

        ``initialize_schema`` skips `kg_KNN_VEC` while `kg_NodeEmbeddings` is still
        keyed `id`, because the 4.0.0 body reads `node_id` and `graph_id`. The reshape
        above is what gives it those columns, so this is the only place that can finish
        the install — otherwise an operator who followed the documented order is left
        with a 4.0.0 schema and no server-side vector search, and nothing tells them.

        A failure here is logged rather than raised: the rows are placed and committed,
        and losing that outcome over a procedure an operator can re-declare by running
        ``initialize_schema`` again would be the worse trade.
        """
        try:
            self.engine._install_procedures(cursor)
        except Exception as e:
            logger.warning(
                "The vectors were placed but the stored procedures were not installed: "
                "%s. Run initialize_schema() to declare them; server-side vector "
                "search is unavailable until it succeeds.",
                e,
            )
        self._commit()

    def _rename(self, cursor, staging: str, target: str) -> None:
        if not self._declares(cursor, staging, "node_id"):
            return  # already renamed by a previous pass
        renamed = self._tolerate(
            cursor,
            # The new name goes in unqualified: a rename cannot move a table between
            # schemas, and IRIS refuses a qualified one at Prepare with SQLCODE -1.
            # Qualifying it sent every upgrade down the fallback below, copying every
            # vector a second time for no reason.
            f"ALTER TABLE {self._t(staging)} RENAME {target}",
            # A build that does not rename tables says so as a syntax error rather than
            # as "already". The rows are all in staging either way, so the fallback
            # below reaches the same catalog by building the table and moving them.
            also=("syntax", "<-1>", "not supported", "unexpected"),
        )
        if renamed:
            return
        self._create_embedding_table(cursor, target, self._plan_shape(cursor, target))
        self._move(
            cursor,
            f"INSERT INTO {self._t(target)} (graph_id, node_id, emb, metadata) "
            f"SELECT graph_id, node_id, emb, metadata FROM {self._t(staging)}",
            [],
        )
        self._tolerate(cursor, f"DROP TABLE {self._t(staging)}")

    # --------------------------------------------------------- the redistribution

    def _redistribute(self, cursor, plan: _Plan) -> None:
        """Move every row whose graph is not this table's graph into that graph's route.

        No watermark: the target's unique constraint absorbs a row a previous pass
        already moved, and the DELETE that follows is what advances the work.
        """
        source = self._t(plan.source)
        for row in plan.rows:
            if row.graph is None or row.graph == plan.table_graph:
                continue
            route = self.engine.resolve_route(
                row.graph,
                plan.model_key,
                create=True,
                dimension=plan.dimension,
                dtype=plan.dtype,
            )
            if route is None:
                raise RuntimeError(
                    f"No route for graph {row.graph!r} and the registry would not "
                    f"create one, so {row.node_id!r} cannot be placed. Migrate "
                    "Graph_KG.embedding_registry first (spec 226)."
                )
            self._move(
                cursor,
                f"INSERT INTO {self._t(route.table_name)} "
                "(graph_id, node_id, emb, metadata) "
                f"SELECT ?, node_id, emb, metadata FROM {source} WHERE node_id = ?",
                [row.graph, row.node_id],
                duplicate_is_done=True,
            )
            self._move(
                cursor,
                f"DELETE FROM {source} WHERE node_id = ? AND graph_id = ?",
                [row.node_id, row.graph],
            )
        self._commit()

    def _rewrite_registry(self, cursor, plan: _Plan) -> None:
        """Name the graph this table's rows were placed in (T066, FR-031)."""
        if plan.table_graph == plan.registry_graph:
            return
        self._tolerate(
            cursor,
            f"UPDATE {self.engine._registry_table()} SET graph_id = ?, "
            "set_at = CURRENT_TIMESTAMP WHERE table_name = ? AND graph_id = ?",
            [plan.table_graph, plan.source, plan.registry_graph],
        )
        self._commit()
        # The row moved from one graph to another, so both pairs' cached routes are now
        # wrong in opposite directions: one names a table it no longer owns, the other
        # does not know it has one.
        self.engine.invalidate_route_cache()

    # --------------------------------------------------------------- the report

    def _observed(self, cursor, plans: List[_Plan], blockers) -> MigrationReport:
        """Report the state the install is in, not what this pass did.

        A killed pass placed rows too, and an operator reconciling against the
        pre-upgrade count has no way to add up two partial reports.
        """
        placed: Dict[Tuple[str, str], int] = {}
        for row in self._registry_rows(cursor):
            table = str(row.get("table_name") or "")
            graph = str(row.get("graph_id") or DEFAULT_GRAPH)
            if not table or not self._declares(cursor, table, "node_id"):
                continue
            self._reconcile_width(cursor, table, row)
            self._note_index(table, row.get("index_state"), row.get("index_error"))
            count = len(self._keys(cursor, table, "node_id"))
            if count:
                placed[(graph, table)] = count
        quarantined, ids = self._quarantine_state(cursor)
        return MigrationReport(
            rows_placed=placed,
            rows_quarantined=quarantined,
            quarantined_ids=ids,
            tables_created=[r for plan in plans for r in plan.needs_routes],
            indexes_created=list(self.indexes_created),
            indexes_refused=dict(self.indexes_refused),
            widths_reconciled=dict(self.widths_reconciled),
            structural_blockers=blockers,
        )

    def _predicted(self, plans: List[_Plan], blockers) -> MigrationReport:
        placed: Dict[Tuple[str, str], int] = {}
        quarantined: Dict[str, int] = {}
        ids: List[str] = []
        for plan in plans:
            for row in plan.rows:
                if row.graph is None:
                    quarantined[row.reason] = quarantined.get(row.reason, 0) + 1
                    ids.append(row.node_id)
                    continue
                target = (
                    plan.source
                    if row.graph == plan.table_graph
                    else plan.route_for[row.graph]
                )
                placed[(row.graph, target)] = placed.get((row.graph, target), 0) + 1
        return MigrationReport(
            rows_placed=placed,
            rows_quarantined=quarantined,
            quarantined_ids=ids,
            tables_created=[r for plan in plans for r in plan.needs_routes],
            structural_blockers=blockers,
        )

    def _quarantine_state(self, cursor) -> Tuple[Dict[str, int], List[str]]:
        counts: Dict[str, int] = {}
        ids: List[str] = []
        rows = self._read(
            cursor,
            f"SELECT node_id, reason FROM {self.engine._quarantine_table()} "
            "ORDER BY q_rowid",
        )
        for row in rows:
            node_id = str(row[0]) if row and row[0] is not None else ""
            reason = str(row[1]) if len(row) > 1 and row[1] is not None else "unknown"
            counts[reason] = counts.get(reason, 0) + 1
            ids.append(node_id)
        return counts, ids

    def _reconcile_width(self, cursor, table: str, row: Dict[str, Any]) -> None:
        """Make a recorded width match its column (FR-031).

        The column is the truth: it is what the next INSERT is checked against, and a
        registry row that disagrees refuses writes naming a width no column has.
        """
        declared = self._declared_width(cursor, table)
        recorded = self._int(row.get("dimension"))
        if declared is None or declared == recorded:
            return
        self.engine._sync_recorded_dimension(table, declared)
        self.widths_reconciled[table] = (recorded or 0, declared)

    def _note_index(self, table: str, state, error) -> None:
        if state == "present":
            if table not in self.indexes_created:
                self.indexes_created.append(table)
        elif state == "refused":
            self.indexes_refused[table] = str(error or "")

    # --------------------------------------------------------------- SQL plumbing

    def _t(self, name: str) -> str:
        return self.engine._t(name)

    def _read(self, cursor, sql: str, params=None) -> List[Tuple[Any, ...]]:
        cursor.execute(sql, list(params or []))
        return list(cursor.fetchall() or [])

    def _declares(self, cursor, table: str, column: str) -> bool:
        rows = self._read(
            cursor,
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
            [self.engine._schema_prefix, table, column],
        )
        return bool(rows and rows[0] and rows[0][0])

    def _shape(self, cursor, source: str) -> Optional[str]:
        """``routed`` (4.0.0), ``legacy`` (3.2.0), ``reshaping``, or ``None`` if absent.

        This is the only thing that decides whether the drain runs, which is what makes
        a second complete run a no-op: it is a migration, not a sync.
        """
        if self._declares(cursor, source, "node_id"):
            return "routed"
        if self._declares(cursor, source, "id"):
            return "legacy"
        if self._declares(cursor, source + _STAGING_SUFFIX, "node_id"):
            return "reshaping"
        return None

    def _plan_shape(self, cursor, target: str) -> _Plan:
        """A stand-in plan carrying only what ``_create_embedding_table`` reads."""
        registry = self._registry_row(cursor, target)
        return _Plan(
            source=target,
            shape="routed",
            dimension=self._declared_width(cursor, target + _STAGING_SUFFIX)
            or self._int(registry.get("dimension")),
            dtype=str(registry.get("dtype") or "DOUBLE").upper(),
            model_key=registry.get("model_key"),
            identity_key=None,
            registry_graph=str(registry.get("graph_id") or DEFAULT_GRAPH),
            table_graph=DEFAULT_GRAPH,
            rows=[],
            route_for={},
            needs_routes=[],
        )

    def _declared_width(self, cursor, table: str) -> Optional[int]:
        try:
            return GraphSchema.get_embedding_dimension(cursor, self._t(table))
        except Exception as e:  # pragma: no cover - catalog shapes vary by build
            logger.debug("No declared width for %s: %s", table, e)
            return None

    def _keys(self, cursor, table: str, key_column: str, after: str = "") -> List[str]:
        """Every key in ``table`` above ``after``, in key order.

        Read in one statement rather than paged: the plan needs every key anyway, and a
        `TOP`/`FETCH FIRST` on a keyed scan is the shape that crashes `%qaqpre` on the
        AI builds (docs/KNOWN_ISSUES.md).
        """
        rows = self._read(
            cursor,
            f"SELECT {key_column} FROM {self._t(table)} "
            f"WHERE {key_column} > ? ORDER BY {key_column}",
            [after],
        )
        return [str(r[0]) for r in rows if r and r[0] is not None]

    def _graphs_of(self, cursor, node_id: str) -> List[str]:
        rows = self._read(
            cursor,
            f"SELECT DISTINCT graph_id FROM {self._t('nodes')} WHERE node_id = ?",
            [node_id],
        )
        return sorted({str(r[0] or DEFAULT_GRAPH) for r in rows if r})

    def _max(self, cursor, table: str, column: str) -> Optional[str]:
        try:
            rows = self._read(
                cursor, f"SELECT MAX({column}) FROM {self._t(table)}"
            )
        except Exception as e:
            logger.debug("No watermark from %s: %s", table, e)
            return None
        value = rows[0][0] if rows and rows[0] else None
        return None if value is None else str(value)

    def _max_quarantined(self, cursor, source: str) -> Optional[str]:
        """The highest key already quarantined *from this source*.

        Filtered by source table on purpose: a shared watermark would let the first
        table's progress skip the second table's rows, and the skipped rows are then
        dropped with the table they came from.
        """
        try:
            rows = self._read(
                cursor,
                f"SELECT MAX(node_id) FROM {self.engine._quarantine_table()} "
                "WHERE source_table = ?",
                [source],
            )
        except Exception as e:
            logger.debug("No quarantine watermark for %s: %s", source, e)
            return None
        value = rows[0][0] if rows and rows[0] else None
        return None if value is None else str(value)

    def _registry_rows(self, cursor) -> List[Dict[str, Any]]:
        columns = (
            "table_name",
            "graph_id",
            "model_key",
            "dimension",
            "dtype",
            "index_state",
            "index_error",
        )
        try:
            rows = self._read(
                cursor,
                f"SELECT {', '.join(columns)} FROM {self.engine._registry_table()} "
                "ORDER BY table_name",
            )
        except Exception as e:
            logger.debug("Registry is unreadable: %s", e)
            return []
        return [dict(zip(columns, row)) for row in rows]

    def _registry_row(self, cursor, table: str) -> Dict[str, Any]:
        """The registry row describing ``table``, preferring the unscoped one."""
        rows = [r for r in self._registry_rows(cursor) if r.get("table_name") == table]
        for row in rows:
            if not (row.get("graph_id") or ""):
                return row
        return rows[0] if rows else {}

    def _registry_pairs(self, cursor) -> set:
        return {
            (r.get("table_name"), str(r.get("graph_id") or DEFAULT_GRAPH))
            for r in self._registry_rows(cursor)
        }

    def _create_embedding_table(self, cursor, table: str, plan: _Plan) -> None:
        """A table in the fresh-4.0.0 legacy shape, constraint names included."""
        if plan.dimension is None:
            raise RuntimeError(
                f"{plan.source} declares no vector width, so the 4.0.0 table cannot be "
                "built. Record one on its registry row first (spec 226)."
            )
        constraint = _UNIQUE_CONSTRAINTS.get(plan.source, f"uq_{table}_graph_node")
        # The reference travels in the CREATE rather than a later ALTER because the
        # table is built as staging and renamed into place: constraint names carry
        # through the rename, and an ALTER after the rename would be a second window in
        # which the default route accepts an embedding for a node that does not exist
        # (FR-008). `_rekey_children` has already created `uq_nodes_graph_node`, which
        # is what this points at.
        reference = _FOREIGN_KEYS.get(plan.source, f"fk_{table}")
        self._tolerate(
            cursor,
            f"CREATE TABLE {self._t(table)} (\n"
            "    emb_rowid BIGINT IDENTITY PRIMARY KEY,\n"
            "    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
            "    node_id VARCHAR(256) %EXACT NOT NULL,\n"
            f"    emb VECTOR({plan.dtype}, {int(plan.dimension)}),\n"
            "    metadata %Library.DynamicObject,\n"
            f"    CONSTRAINT {constraint} UNIQUE (graph_id, node_id),\n"
            f"    CONSTRAINT {reference} FOREIGN KEY (graph_id, node_id) "
            f"REFERENCES {self._t('nodes')} (graph_id, node_id)\n"
            ")",
        )
        self._commit()

    def _move(self, cursor, sql: str, params, *, duplicate_is_done: bool = False) -> None:
        try:
            cursor.execute(sql, list(params or []))
        except Exception as e:
            if self._matches(e, _DUPLICATE) and duplicate_is_done:
                # The row is already at its target — by a previous pass or a concurrent
                # writer. The constraint is the idempotence mechanism, so the migration
                # has to survive the violation it relies on.
                logger.debug("Already placed: %s", e)
                return
            raise

    def _tolerate(self, cursor, sql: str, params=None, *, also=()) -> bool:
        """Run a statement a re-run may find already done. True if it took effect."""
        try:
            cursor.execute(sql, list(params or []))
            return True
        except Exception as e:
            if self._matches(e, _ALREADY_DONE + tuple(also)):
                logger.debug("Already done: %s (%s)", sql.split("\n")[0], e)
                return False
            raise

    @staticmethod
    def _matches(exc: Exception, needles) -> bool:
        text = str(exc).lower()
        return any(n.lower() in text for n in needles)

    @staticmethod
    def _int(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _later(*values) -> Optional[str]:
        present = [v for v in values if v is not None]
        return max(present) if present else None

    def _commit(self) -> None:
        try:
            self.conn.commit()
        except Exception as e:  # pragma: no cover - autocommit connections
            logger.debug("Commit not needed: %s", e)

    @staticmethod
    def _close(cursor) -> None:
        try:
            cursor.close()
        except Exception:  # pragma: no cover - driver-dependent
            pass
