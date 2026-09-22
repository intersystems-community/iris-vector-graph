import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List

from iris_vector_graph.schema import (
    GraphSchema,
    RdfEdgesRescueError,
    _call_classmethod,
    repair_ifind_helpers,
)
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.constants import DEFAULT_GRAPH, VECTOR_TABLE_NAMES
from iris_vector_graph.routing import graph_scope_predicate, route_table_name
from iris_vector_graph.security import (
    is_routed_edge_embedding_table,
    is_routed_embedding_table,
    sanitize_identifier,
)
from iris_vector_graph.embedding_identity import (
    MECHANISMS,
    EmbeddingIdentity,
    conflicts,
    identity_from_config,
)
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph._engine.ledger import ledger_check as _ledger_check

logger = logging.getLogger(__name__)

#: Every table whose ``emb`` column the dimension migration must keep in step.
#: ``kg_EdgeEmbeddings`` is in this list because it was the one left out: before
#: 3.1.0 the untyped-column branch altered only the two node tables, and the
#: mismatch branch altered the edge table under a bare ``except: pass``.
VECTOR_TABLES = VECTOR_TABLE_NAMES

#: The qualifier ``materialize_inference`` stamps on every edge it derives.
INFERRED_QUALIFIER_JSON = '{"inferred":true}'

#: The LIKE pattern that recognises that stamp — declared beside it so the writer and
#: its readers cannot drift apart again. They had: the writer stored the bare boolean
#: above while ``retract_inference`` looked for ``"inferred":"true"``, a quoted string,
#: so retraction matched nothing the inference pass had written and reported a row count
#: of 0 as though there had been nothing to retract (spec 230, FR-001b).
#: ``create_edge(qualifiers={"inferred": "true"})`` is a third spelling again, because
#: ``json.dumps`` puts a space after the colon. Matching the key and its colon is the
#: only predicate all three satisfy, and it still spares ``{"inferredBy": ...}``.
INFERRED_QUALIFIER_LIKE = '%"inferred":%'


def _alter_tolerated_errors() -> tuple:
    """Exception types ``initialize_schema`` tolerates from the dimension migration.

    Spec 226, FR-012: the migration step used to sit under a bare ``except Exception``,
    so an embedding identity refusal — or a plainly invalid ``embedding_dimension`` —
    became a log line and ``initialize_schema`` returned success. Only what the driver
    raises for a DDL statement belongs here. ``ValueError`` (hence
    ``EmbeddingIdentityConflict``) and ``TypeError`` are programming and contract errors
    and must reach the caller.

    Falls back to the DB-API base classes' names via the embedded driver when
    ``iris.dbapi`` is unavailable; an empty tuple means nothing is tolerated, which is the
    safe direction — a real driver error then surfaces instead of hiding.
    """
    errors: list = []
    for module_name in ("iris.dbapi", "intersystems_iris.dbapi"):
        try:
            module = __import__(module_name, fromlist=["Error"])
        except Exception:
            continue
        base = getattr(module, "Error", None)
        if isinstance(base, type) and issubclass(base, Exception):
            errors.append(base)
    return tuple(errors)


#: See :func:`_alter_tolerated_errors`. Resolved once at import.
_ALTER_TOLERATED_ERRORS = _alter_tolerated_errors()

#: The registry table (spec 226). Written exactly as ``contracts/embedding_registry.sql``
#: specifies; no trailing semicolon, because the Python DB-API rejects one.
EMBEDDING_REGISTRY_TABLE = "embedding_registry"

_EMBEDDING_REGISTRY_DDL = """CREATE TABLE {table} (
    table_name      VARCHAR(128)  NOT NULL,
    graph_id        VARCHAR(256)  NOT NULL DEFAULT '',
    kind            VARCHAR(16)   DEFAULT 'node',
    mechanism       VARCHAR(32),
    model_key       VARCHAR(256),
    declared_config VARCHAR(512),
    dimension       INTEGER,
    dtype           VARCHAR(16)   NOT NULL DEFAULT 'DOUBLE',
    set_at          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    set_by          VARCHAR(128),
    index_state     VARCHAR(16),
    index_error     VARCHAR(4000),
    recall_measured DOUBLE,
    recall_measured_at TIMESTAMP,
    CONSTRAINT pk_embedding_registry PRIMARY KEY (table_name, graph_id)
)"""

#: Spec 230 (FR-006): what kind of vector a route holds.
#:
#: The route lookup keys on ``(graph_id, model_key)``, which a node route and an
#: edge route for the same pair now share. Without this discriminator an edge route
#: is a candidate answer for a node read, and the reader would be handed a table of
#: ``(s, p, o_id)`` triples where it expects ``node_id``.
#:
#: Nullable with a default rather than ``NOT NULL``: every row written before 4.0.0
#: names a node route and has no value here, and an ``ADD COLUMN ... NOT NULL`` over
#: a populated table is refused. Readers ``COALESCE(kind, 'node')`` for that reason —
#: absent means node, which is what every pre-230 row is.
ROUTE_KINDS = ("node", "edge")

#: The default a missing ``kind`` means, and the value a node route records.
ROUTE_KIND_NODE = "node"

#: The value an edge route records.
ROUTE_KIND_EDGE = "edge"

#: Spec 227 (FR-019, FR-021) and spec 230 (FR-006): the columns an existing registry
#: gains on upgrade. The first four let a route describe its own index and recall;
#: ``kind`` says which kind of vector it holds.
#:
#: The four are declared nullable with no default on purpose — a row written before
#: the upgrade has no measurement, and a default would claim an index state nobody
#: looked at. ``kind`` does carry a default, because an existing row's kind is not
#: unknown: everything written before 4.0.0 is a node route.
#:
#: ``ADD COLUMN`` needs the type; ``ALTER COLUMN`` must not restate one
#: (SQLCODE -25), which is why these are only ever added.
_REGISTRY_ROUTE_COLUMNS = (
    ("index_state", "VARCHAR(16)"),
    ("index_error", "VARCHAR(4000)"),
    ("recall_measured", "DOUBLE"),
    ("recall_measured_at", "TIMESTAMP"),
    ("kind", "VARCHAR(16) DEFAULT 'node'"),
)

#: The reverse lookup routing needs: ``(graph, model) → table_name`` (FR-011).
_REGISTRY_ROUTE_INDEX = "idx_registry_route"

#: Spec 227 (FR-039): where a row goes when its graph cannot be recovered.
#:
#: No ``graph_id`` — the absence is the point. ``emb`` declares a type and no
#: length so rows of different widths coexist in one table (ADR-0005); the width
#: each row actually has is recorded in ``dimension``, because the column no longer
#: states it. No index of any kind: nothing searches this table, and a vector index
#: over mixed widths is not a meaningful object (FR-040).
_EMBEDDING_QUARANTINE_DDL = """CREATE TABLE {table} (
    q_rowid      BIGINT IDENTITY PRIMARY KEY,
    node_id      VARCHAR(256) %EXACT NOT NULL,
    source_table VARCHAR(256) NOT NULL,
    emb          VECTOR(DOUBLE),
    dimension    INTEGER NOT NULL,
    dtype        VARCHAR(16) NOT NULL,
    metadata     VARCHAR(4000),
    reason       VARCHAR(256) NOT NULL, -- ambiguous_graph | no_node | resolver_declined
    quarantined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

#: The closed set ``reason`` may hold, so a migration report can group by it (FR-030).
QUARANTINE_REASONS = ("ambiguous_graph", "no_node", "resolver_declined")

EMBEDDING_QUARANTINE_TABLE = "embedding_quarantine"

#: Spec 227 (FR-012, FR-017, FR-018): one routed embedding table per ``(graph, model)``.
#:
#: ``emb_rowid`` exists so the HNSW index is legal: IRIS refuses a vector index on a
#: table whose row identity is a composite or a VARCHAR (research R2), and the natural
#: key here is ``(graph_id, node_id)``. The natural key is therefore enforced by a
#: separate UNIQUE constraint rather than by the primary key — one vector per node per
#: route, which is what makes a routed read need no DISTINCT.
#:
#: ``graph_id`` is carried even though a routed table holds exactly one graph's rows
#: (FR-037): the FK to ``nodes`` is composite now, so the column has to be there to
#: point at it, and a row that names its own graph survives being copied out of the
#: table by a migration or an export.
_ROUTED_EMBEDDING_DDL = """CREATE TABLE {table} (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id  VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    node_id   VARCHAR(256) %EXACT NOT NULL,
    emb       VECTOR({dtype}, {dimension}),
    metadata  VARCHAR(4000),
    CONSTRAINT uq_{short} UNIQUE (graph_id, node_id),
    CONSTRAINT fk_{short} FOREIGN KEY (graph_id, node_id) REFERENCES {nodes} (graph_id, node_id)
)"""

#: Spec 230 (FR-006): one routed *edge* embedding table per ``(graph, model)``.
#:
#: Same shape as the node route for the same reasons — ``emb_rowid`` so the HNSW index
#: is legal, the natural key enforced by a UNIQUE constraint, ``graph_id`` carried on
#: every row so a row copied out of the table still names its graph. The natural key is
#: the triple, so one vector per edge per route.
#:
#: Two deliberate differences from ``_ROUTED_EMBEDDING_DDL``:
#:
#: * ``p VARCHAR(512)``, matching the pre-230 ``kg_EdgeEmbeddings`` rather than
#:   ``rdf_edges.p`` (128). Narrowing it here would refuse a predicate the old table
#:   accepted, which the 4.0.0 migration then could not copy forward.
#: * No foreign key. A node route points one FK at ``nodes (graph_id, node_id)``; an
#:   edge has *two* endpoints and its own row in ``rdf_edges``, whose primary key is an
#:   IDENTITY ``edge_id`` — not the triple — so there is nothing composite to point at.
#:   Pointing at ``rdf_edges``'s UNIQUE ``(s, p, o_id, graph_id)`` instead would order
#:   the columns differently from the key here and would make an edge vector
#:   unwritable until its edge row exists, which reverses the order ``embed_edges``
#:   works in.
_ROUTED_EDGE_EMBEDDING_DDL = """CREATE TABLE {table} (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id  VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s         VARCHAR(256) %EXACT NOT NULL,
    p         VARCHAR(512) %EXACT NOT NULL,
    o_id      VARCHAR(256) %EXACT NOT NULL,
    emb       VECTOR({dtype}, {dimension}),
    metadata  VARCHAR(4000),
    CONSTRAINT uq_{short} UNIQUE (graph_id, s, p, o_id)
)"""

#: The ANN index over a routed table. Attempted, never required (FR-019): a build that
#: refuses it leaves a route that scans, and the refusal is recorded verbatim.
_ROUTED_INDEX_DDL = (
    "CREATE INDEX idx_{short}_ann ON {table} (emb) AS HNSW(Distance='Cosine')"
)

#: What ``embedding_registry.index_state`` may hold for a routed table.
#: ``present`` — the index exists. ``refused`` — the build would not create it, and
#: ``index_error`` says what it said. Nothing means "never attempted", which is what a
#: row adopted from 3.2.0 has.
ROUTE_INDEX_STATES = ("present", "refused")

#: Column order of the inventory's registry read, and of the tuple
#: :meth:`SchemaMixin._registry_inventory_rows` unpacks. One tuple rather than a
#: literal in the SELECT because the registry carries ``dimension`` and
#: ``index_state`` a few columns apart: a list that drifts from the unpacking reads
#: a width out of an index state, and both are the kind of value that looks plausible.
_INVENTORY_REGISTRY_COLUMNS = (
    "table_name",
    "graph_id",
    "model_key",
    "dimension",
    "dtype",
    "index_state",
    "index_error",
    "recall_measured",
    "recall_measured_at",
)


@dataclass(frozen=True)
class EmbeddingInventoryRow:
    """One line of the embedding inventory (spec 227, FR-020, contract §4).

    ``table_name=None`` is a graph that has nodes and no route — reported rather than
    omitted, because "the vectors are in another route" and "there are no vectors" call
    for opposite actions (US4-3).

    ``index_state`` is ``present`` only when ``%Dictionary.CompiledIndex`` holds an
    HNSW index on ``table_name``. ``refused`` means a build was attempted and IRIS said
    no, with its wording in ``index_error``. ``absent`` covers both "nobody tried" and
    "the route has no table", which are indistinguishable from the index's point of
    view and equally mean searches over this route scan.

    Frozen: a report an operator can edit is a report that can be made to agree with
    whatever they expected.
    """

    graph_id: str
    model_key: Optional[str]
    table_name: Optional[str]
    dimension: Optional[int]
    dtype: Optional[str]
    row_count: int
    index_name: Optional[str]
    index_state: str
    index_error: Optional[str]
    recall_measured: Optional[float]
    recall_measured_at: Optional[str]


#: Column order of the quarantine read, and of the tuple :class:`QuarantinedVector`
#: is built from. The table has no ``graph_id`` — that absence is why its rows are
#: here — so nothing in this list is a scope.
_QUARANTINE_COLUMNS = (
    "q_rowid",
    "node_id",
    "source_table",
    "dimension",
    "dtype",
    "metadata",
    "reason",
    "quarantined_at",
)


@dataclass(frozen=True)
class QuarantinedVector:
    """One vector the migration could not place in a graph (spec 227, FR-039, §5).

    Deliberately without the vector itself. A placement copies it inside IRIS with an
    ``INSERT ... SELECT``, so the floats never become text and back; and a listing that
    carried every vector would hand an operator a few thousand 768-wide arrays to page
    through the reasons in.

    ``dimension`` is the width this row's vector actually has, read from the column that
    records it rather than from the quarantine table's own ``emb``, which declares a type
    and no length precisely so rows of different widths can sit next to each other.
    """

    q_rowid: int
    node_id: str
    source_table: str
    dimension: Optional[int]
    dtype: Optional[str]
    metadata: Optional[str]
    reason: str
    quarantined_at: Optional[str]


def _embedder_model_name(embedder) -> Optional[str]:
    """The model name an embedder can state about itself, or None.

    Returns None rather than a placeholder when nothing is discoverable — a callable with
    no name genuinely does not know what produced its vectors, and the honest result is the
    undeclared identity (width enforced, model not compared). Inventing a key here would
    let two unrelated embedders compare equal.
    """
    if embedder is None:
        return None
    for attr in ("model_name", "model_name_or_path", "model_id", "name"):
        value = getattr(embedder, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    inner = getattr(embedder, "model", None)
    for attr in ("name_or_path", "model_name"):
        value = getattr(inner, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


class SchemaMixin:
    """Schema management and graph initialization mixin for IRISGraphEngine.
    
    Provides schema creation, status checking, graph building, and inference."""

    def is_ready(self) -> bool:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            cur.fetchone()
            return True
        except Exception:
            return False


    def get_labels(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
        return [r[0] for r in cur.fetchall()]


    def get_relationship_types(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        return [r[0] for r in cur.fetchall()]


    def get_node_count(self, label: str = None) -> int:
        cur = self.conn.cursor()
        if label:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label])
        else:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
        return int(cur.fetchone()[0])


    def get_edge_count(self, predicate: str = None) -> int:
        cur = self.conn.cursor()
        if predicate:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = ?", [predicate])
        else:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        return int(cur.fetchone()[0])


    def get_label_distribution(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT label, COUNT(*) AS cnt FROM Graph_KG.rdf_labels GROUP BY label ORDER BY cnt DESC"
        )
        return {r[0]: int(r[1]) for r in cur.fetchall()}


    def get_property_keys(self, label: str = None) -> List[str]:
        cur = self.conn.cursor()
        if label:
            cur.execute(
                'SELECT DISTINCT rp."key" FROM Graph_KG.rdf_props rp'
                " JOIN Graph_KG.rdf_labels rl ON rl.s = rp.s"
                ' WHERE rl.label = ? ORDER BY rp."key"',
                [label],
            )
        else:
            cur.execute('SELECT DISTINCT "key" FROM Graph_KG.rdf_props ORDER BY "key"')
        return [r[0] for r in cur.fetchall()]


    def node_exists(self, node_id: str, *, graph: Optional[str] = None) -> bool:
        """Does this node exist in this graph? (spec 227)

        `graph=None` asks about the default graph, not about every graph. "Does it
        exist anywhere" is a different question with a different answer now that a
        node ID can exist in two graphs, and a caller asking this one almost always
        wants to know whether they can write next to it.
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.nodes"
            f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
            [node_id, graph_id],
        )
        row = cur.fetchone()
        return row is not None and int(row[0]) > 0

    # ----------------------------------------------------------------- embedding identity

    def _registry_table(self) -> str:
        return self._t(EMBEDDING_REGISTRY_TABLE)

    def _quarantine_table(self) -> str:
        return self._t(EMBEDDING_QUARANTINE_TABLE)

    def _ensure_embedding_registry(self, cursor) -> bool:
        """Create ``embedding_registry`` if absent. True when it exists afterwards.

        A registry that already exists is brought up to the 4.0.0 shape in place:
        the four route columns and the reverse route index are added if missing, so
        a 3.2.0 install and a fresh one end up describing a route the same way.
        """
        created = True
        try:
            cursor.execute(_EMBEDDING_REGISTRY_DDL.format(table=self._registry_table()))
            self.conn.commit()
        except Exception as e:
            err = str(e).lower()
            if "already exists" in err or "already has a" in err:
                created = True
            else:
                logger.warning(
                    "Could not create %s: %s — embedding identity will not be enforced",
                    self._registry_table(),
                    e,
                )
                return False
        self._ensure_registry_route_columns(cursor)
        return created

    def _ensure_registry_route_columns(self, cursor) -> Dict[str, bool]:
        """Add spec 227's route columns and the ``(graph_id, model_key)`` index.

        Each statement is attempted independently and an "already exists" is a
        success: this runs on every ``initialize_schema``, against registries at
        three different ages. A genuine failure is logged and reported ``False``
        rather than raised — the registry keeps working without recall numbers,
        and an installation that cannot add a column should not lose its schema
        initialisation over it.
        """
        table = self._registry_table()
        outcomes: Dict[str, bool] = {}
        for name, decl in _REGISTRY_ROUTE_COLUMNS:
            outcomes[name] = self._try_registry_ddl(
                cursor, f"ALTER TABLE {table} ADD COLUMN {name} {decl}", name
            )
        outcomes[_REGISTRY_ROUTE_INDEX] = self._try_registry_ddl(
            cursor,
            f"CREATE INDEX {_REGISTRY_ROUTE_INDEX} ON {table} (graph_id, model_key)",
            _REGISTRY_ROUTE_INDEX,
        )
        return outcomes

    def _try_registry_ddl(self, cursor, sql: str, what: str) -> bool:
        try:
            cursor.execute(sql)
            self.conn.commit()
            return True
        except Exception as e:
            err = str(e).lower()
            if "already" in err or "duplicate" in err:
                return True
            logger.debug("Registry migration step %s skipped: %s", what, e)
            return False

    def _ensure_embedding_quarantine(self, cursor) -> bool:
        """Create ``embedding_quarantine`` if absent. True when it exists afterwards.

        Created unconditionally at schema initialisation rather than lazily by the
        migration: a row with no recoverable graph has to have somewhere to go at the
        moment it is found, and discovering the table is missing halfway through a
        placement pass would leave the caller choosing between losing the row and
        defaulting it (FR-039).
        """
        try:
            cursor.execute(_EMBEDDING_QUARANTINE_DDL.format(table=self._quarantine_table()))
            self.conn.commit()
            return True
        except Exception as e:
            err = str(e).lower()
            if "already exists" in err or "already has a" in err:
                return True
            logger.warning(
                "Could not create %s: %s — rows with no recoverable graph have nowhere to go",
                self._quarantine_table(),
                e,
            )
            return False

    # ------------------------------------------------------- stored procedures

    def _embeddings_await_migration(self, cursor) -> bool:
        """True when ``kg_NodeEmbeddings`` is still in 3.2.0's shape (spec 227).

        Asked by shape, not by error text: the 4.0.0 ``kg_KNN_VEC`` body reads
        ``n.node_id`` and ``n.graph_id``, and a table whose key column is still ``id``
        has neither, so the procedure cannot compile there no matter what IRIS calls
        the failure.

        A table declaring *neither* column is not reported as pre-migration. Absence
        means the earlier DDL did not land, which is a different problem, and blaming
        it on a pending migration would send an operator to a call that cannot help.
        """
        legacy = self._probe_column("kg_NodeEmbeddings", "id", cursor=cursor)
        routed = self._probe_column("kg_NodeEmbeddings", "node_id", cursor=cursor)
        return bool(legacy and not routed)

    def _docs_awaits_graph_column(self, cursor) -> bool:
        """True when ``Graph_KG.docs`` exists but has no ``graph_id`` yet (spec 230).

        The column is the 4.0.0 upgrade's ``docs`` step, not ``initialize_schema``'s, so
        an upgraded package meets a 3.2.0 install with a table it cannot index yet.

        A table that is *absent* is not reported as pre-migration: the same DDL script
        creates it, with the column, a few statements earlier, so treating absence as
        "awaiting" would skip the index on exactly the install that can have it.
        """
        if not self._probe_column("docs", "id", cursor=cursor):
            return False
        return not self._probe_column("docs", "graph_id", cursor=cursor)

    def _install_procedures(self, cursor) -> None:
        """Declare the stored procedures, raising if a required one will not compile.

        Called by ``initialize_schema``, and again by the 4.0.0 migration once it has
        reshaped the embedding tables. The two callers exist because of an ordering
        trap: an upgraded package meets a 3.2.0 table, ``kg_KNN_VEC`` cannot compile
        against it, and the migration that fixes the table needs
        ``embedding_quarantine`` — which only ``initialize_schema`` creates. Failing
        here would make each call a prerequisite of the other.

        So a pre-migration install defers the core procedure instead, and the message
        names the call that finishes the job. Only ``kg_KNN_VEC`` is required;
        ``kg_TXT`` and ``kg_RRF_FUSE`` depend on the full-text feature being present.
        """
        deferred = self._embeddings_await_migration(cursor)
        procedure_errors: List[Any] = []
        # No width is passed: `get_procedures_sql_list` ignores it and, since 3.2.0,
        # deprecates it (ADR-0005 — the query vector is converted unlengthed on purpose).
        # Passing a width here would fire that warning on every initialize_schema, on a
        # path no caller can fix, which teaches people to filter the warning out.
        for stmt in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
            if not stmt.strip():
                continue
            is_core = "procedure graph_kg.kg_knn_vec" in stmt.lower()
            if is_core and deferred:
                logger.warning(
                    "kg_KNN_VEC deferred: Graph_KG.kg_NodeEmbeddings is still keyed "
                    "`id`, so the 4.0.0 procedure body has no columns to read. Run "
                    "iris_vector_graph.migrations.migrate_to_graph_scoped_embeddings"
                    "(conn) to move the vectors into graph-scoped tables; it installs "
                    "the procedure when it is done. Server-side vector search is "
                    "unavailable until then."
                )
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already has" in err:
                    continue  # idempotent re-run — schema or procedure already installed
                if is_core:
                    _sqlcode = ""
                    import re as _re_proc

                    m = _re_proc.search(r"sqlcode.*?<(-?\d+)>", err)
                    if m:
                        _sqlcode = m.group(1)
                    if _sqlcode == "-260":
                        logger.debug(
                            "kg_KNN_VEC skipped: vector dimension mismatch in "
                            "kg_NodeEmbeddings (table has mixed-dim vectors from "
                            "tests). Non-fatal. | Error: %s",
                            e,
                        )
                    else:
                        procedure_errors.append((stmt[:80], e))
                        logger.error("Procedure DDL failed: %s | Error: %s", stmt[:80], e)
                else:
                    logger.debug(
                        "Optional procedure DDL skipped (non-fatal): %s | Error: %s",
                        stmt[:80],
                        e,
                    )

        if procedure_errors:
            raise RuntimeError(
                f"initialize_schema() failed to install {len(procedure_errors)} "
                f"stored procedure(s). Server-side vector search will be unavailable. "
                f"First error: {procedure_errors[0][1]}"
            )

    # -------------------------------------------------------------------- routes

    def routed_table_ddl(
        self,
        table_name: str,
        *,
        dimension: int,
        dtype: str = "DOUBLE",
        kind: str = ROUTE_KIND_NODE,
    ) -> str:
        """The ``CREATE TABLE`` for a routed embedding table (spec 227 FR-017, spec 230 FR-006).

        ``dimension`` and ``dtype`` are interpolated, not bound — a column declaration
        cannot take a parameter — so both are checked here rather than trusted. The
        table name is checked too: it reaches the allowlist as a *shape*
        (``kg_emb_<16 hex>``, or ``kg_eemb_<16 hex>`` for an edge route), and this is
        the one place that shape becomes an identifier inside DDL.

        ``kind`` decides which shape is built *and* which name is accepted, together:
        a ``kg_emb_`` name can only ever get node columns and a ``kg_eemb_`` name can
        only ever get edge columns. Checking them independently would let a caller
        create a table whose name says node and whose columns say edge, and every later
        reader classifies by the name.
        """
        if kind not in ROUTE_KINDS:
            raise ValueError(f"kind must be one of {ROUTE_KINDS}, got {kind!r}")
        is_edge = kind == ROUTE_KIND_EDGE
        if is_edge:
            if not is_routed_edge_embedding_table(table_name):
                raise ValueError(
                    f"{table_name!r} is not a routed edge embedding table name "
                    "(kg_eemb_<16 hex>); routing derives it from "
                    "routing.edge_route_table_name"
                )
        elif not is_routed_embedding_table(table_name):
            raise ValueError(
                f"{table_name!r} is not a routed embedding table name "
                "(kg_emb_<16 hex>); routing derives it from routing.route_table_name"
            )
        width = int(dimension)
        if width <= 0:
            raise ValueError(f"dimension must be a positive integer, got {dimension!r}")
        element = str(dtype or "DOUBLE").upper()
        if not element.isalpha():
            raise ValueError(f"dtype {dtype!r} is not a vector element type")
        template = _ROUTED_EDGE_EMBEDDING_DDL if is_edge else _ROUTED_EMBEDDING_DDL
        return template.format(
            table=self._t(table_name),
            short=table_name,
            dimension=width,
            dtype=element,
            nodes=self._t("nodes"),
        )

    def _create_routed_table(
        self,
        cursor,
        table_name: str,
        *,
        dimension: int,
        dtype: str = "DOUBLE",
        kind: str = ROUTE_KIND_NODE,
    ) -> bool:
        """Create a routed table. True when this call created it, False when it was there.

        "It was there" is the expected outcome of losing a race, not a failure: the
        winner's ``CREATE TABLE`` and its registry row are two statements, and a second
        creator that arrives between them has to carry on to the row.
        """
        ddl = self.routed_table_ddl(table_name, dimension=dimension, dtype=dtype, kind=kind)
        try:
            cursor.execute(ddl)
            self.conn.commit()
            return True
        except Exception as e:
            err = str(e).lower()
            if "already exists" in err or "not unique" in err or "already has a" in err:
                logger.debug("Routed table %s already existed: %s", table_name, e)
                return False
            raise

    def _attempt_route_index(self, cursor, table_name: str):
        """Try to build the ANN index over a routed table. Returns ``(state, error)``.

        A refusal is recorded, never raised (FR-019). The alternative is worse in both
        directions: a caller whose first write fails because the build has no HNSW
        support, or a route that silently scans while ``SHOW INDEXES`` claims an index
        — which is exactly what spec 226 removed.
        """
        sql = _ROUTED_INDEX_DDL.format(short=table_name, table=self._t(table_name))
        try:
            cursor.execute(sql)
            self.conn.commit()
            return "present", None
        except Exception as e:
            message = str(e)
            if "already" in message.lower():
                return "present", None
            logger.info(
                "No ANN index on %s; searches over it will scan. IRIS said: %s",
                table_name,
                message,
            )
            return "refused", message[:4000]

    def _record_route_index_state(self, cursor, table_name: str, graph_id: str, state, error):
        """Write a route's index state onto its registry row.

        Separate from the insert because the index attempt happens between the table and
        the row, and because a later rebuild has to be able to correct the state without
        touching the identity the row also carries.
        """
        try:
            cursor.execute(
                f"UPDATE {self._registry_table()} SET index_state = ?, index_error = ? "
                "WHERE table_name = ? AND graph_id = ?",
                [state, error, table_name, graph_id],
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Could not record index state for %s: %s", table_name, e)
        finally:
            # The row this engine may already be serving from cache just changed its
            # index state, and `is_indexed` is read off the cached route. Dropped in a
            # `finally` because a failed UPDATE can still have altered the row.
            self.invalidate_route_cache(graph_id)

    def _record_route_recall(
        self, cursor, table_name: str, graph_id: str, recall: float, measured_at: str
    ) -> bool:
        """Write a measured recall and its timestamp onto a route's registry row (FR-021).

        The timestamp travels with the number because a recall is a statement about the
        rows that were there when it was taken. Without it, a number measured on three
        vectors keeps reading as current after a million more arrive.

        Returns whether the row was written, so a harness can report an unrecorded
        measurement rather than publishing it as recorded.
        """
        try:
            cursor.execute(
                f"UPDATE {self._registry_table()} SET recall_measured = ?, "
                "recall_measured_at = ? WHERE table_name = ? AND graph_id = ?",
                [float(recall), measured_at, table_name, graph_id],
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.warning(
                "Could not record recall %.4f for %s in graph %r: %s",
                recall, table_name, graph_id, e,
            )
            return False
        finally:
            self.invalidate_route_cache(graph_id)

    def _offered_embedding_identity(
        self,
        *,
        dimension: Optional[int] = None,
        dtype: Optional[str] = None,
        config: Optional[str] = None,
    ) -> EmbeddingIdentity:
        """The identity *this engine* declares, derived from how it is configured.

        The mechanism follows the configuration, not the call site: an engine configured
        with ``embedding_config`` offers ``iris-embedding-config`` from every seam it owns,
        including ``store_embedding``. Deriving it from the function called would make such
        an engine conflict with its own registry row on every direct vector write.

        An engine with no config and no nameable embedder offers the **undeclared** identity
        — no model is compared, the width still is.

        ``config`` names a model declared for one specific piece of work rather than by the
        engine: an ``enqueue_for_embedding`` call, or a queue entry's own
        ``^EmbedQueue(reqId, "config")``. It takes precedence over the engine's own
        configuration, because it is the model that work asked for. An empty or missing
        ``config`` is **not** an unknown model — it means "whatever this worker is", so the
        engine's own identity is what gets compared (spec 226, FR-011).
        """
        _dtype = (dtype or getattr(self, "vector_dtype", None) or "DOUBLE").upper()

        if config and str(config).strip():
            return identity_from_config(str(config), dimension=dimension, dtype=_dtype)

        config = getattr(self, "embedding_config", None)
        if config and str(config).strip():
            return identity_from_config(str(config), dimension=dimension, dtype=_dtype)

        model_name = _embedder_model_name(getattr(self, "embedder", None))
        if model_name:
            return identity_from_config(
                model_name,
                mechanism="sentence-transformers",
                dimension=dimension,
                dtype=_dtype,
            )

        return EmbeddingIdentity(
            mechanism=None, model_key=None, dimension=dimension, dtype=_dtype
        )

    def get_embedding_identity(
        self, table_name: str = "kg_NodeEmbeddings", *, graph_id: str = ""
    ) -> Optional[EmbeddingIdentity]:
        """The identity recorded for ``table_name``, or None when nothing is recorded.

        Reads only. Does not adopt, does not claim, does not write. Returns None on a
        pre-3.2.0 schema that has no registry table — there is nothing recorded to
        conflict with, and creating the table is ``initialize_schema``'s job.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT mechanism, model_key, declared_config, dimension, dtype "
                f"FROM {self._registry_table()} WHERE table_name = ? AND graph_id = ?",
                [table_name, graph_id],
            )
            row = cursor.fetchone()
            # Materialize before the cursor closes. The IRIS driver's DataRow is a live
            # view onto the cursor: reading it after close() raises
            # <COMMUNICATION LINK ERROR> Cursor closed, not a stale-value bug.
            values = None if row is None else tuple(row)
        except Exception as e:
            logger.debug("Embedding registry not readable (%s): %s", table_name, e)
            return None
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        # One policy for the whole read: anything this method cannot interpret means
        # "nothing recorded". A row that is not five values cannot come from the fixed
        # SELECT list above against a real registry table, so raising here would only ever
        # fire on a stand-in cursor — and it would take down `initialize_schema` with it,
        # which is a worse outcome than the read the `except` above already tolerates.
        if values is None or len(values) != 5:
            if values is not None:
                logger.debug(
                    "Embedding registry row for %s has %d values, expected 5",
                    table_name,
                    len(values),
                )
            return None

        mechanism, model_key, declared_config, dimension, dtype = values
        return EmbeddingIdentity(
            mechanism=mechanism or None,
            model_key=model_key or None,
            declared_config=declared_config or None,
            dimension=int(dimension) if dimension is not None else None,
            dtype=(dtype or "DOUBLE"),
        )

    def set_embedding_identity(
        self,
        identity: EmbeddingIdentity,
        table_name: str = "kg_NodeEmbeddings",
        *,
        graph_id: str = "",
        force: bool = False,
    ) -> EmbeddingIdentity:
        """Record ``identity`` for ``table_name``.

        Four cases: insert when nothing is recorded; claim an adopted row whose model is
        unknown; no-op when the same model is already recorded; raise
        :class:`EmbeddingIdentityConflict` when a different one is.

        The claim is atomic (FR-009). ``UPDATE ... WHERE model_key IS NULL`` is the
        arbitration: IRIS locks the row, so the second of two concurrent claimants
        re-evaluates the predicate after the first commits, matches nothing, re-reads, and
        is refused. Nothing here reads-then-writes.

        ``force=True`` overwrites unconditionally and records ``set_by='forced'``. That
        invalidates every vector already in the table, so it is an operator action and is
        logged at WARNING saying so.
        """
        if table_name not in VECTOR_TABLE_NAMES and not is_routed_embedding_table(table_name):
            raise ValueError(
                f"table_name {table_name!r} is not an embedding table. Expected one of "
                f"{VECTOR_TABLE_NAMES} or a routed table (kg_emb_<16 hex>)."
            )
        # Spec 227: `graph_id` is real now. 3.2.0 refused anything but '' because
        # nothing read the column; the table-name check above stays, because a typo
        # would otherwise record an identity against a table nothing ever resolves.
        if identity is None or identity.is_unknown:
            raise ValueError(
                "set_embedding_identity requires a declared model. Only "
                "adopt_embedding_identities records an unknown one (FR-007)."
            )
        if identity.mechanism not in MECHANISMS:
            raise ValueError(
                f"mechanism {identity.mechanism!r} is not one of {MECHANISMS}."
            )

        offered = identity.normalized()
        table = self._registry_table()
        cursor = self.conn.cursor()

        try:
            if force:
                cursor.execute(
                    f"UPDATE {table} SET mechanism = ?, model_key = ?, declared_config = ?, "
                    "dimension = ?, dtype = ?, set_at = CURRENT_TIMESTAMP, set_by = 'forced' "
                    "WHERE table_name = ? AND graph_id = ?",
                    [
                        offered.mechanism,
                        offered.model_key,
                        offered.declared_config,
                        offered.dimension,
                        offered.dtype,
                        table_name,
                        graph_id,
                    ],
                )
                self.conn.commit()
                if self.get_embedding_identity(table_name, graph_id=graph_id) is None:
                    self._insert_identity(cursor, table_name, graph_id, offered, "forced")
                logger.warning(
                    "Forced embedding identity on %s to %s. Every vector already stored "
                    "was produced by something else and is no longer comparable — re-embed "
                    "the table.",
                    table_name,
                    offered.describe(),
                )
                return offered

            recorded = self.get_embedding_identity(table_name, graph_id=graph_id)

            if recorded is None:
                try:
                    self._insert_identity(
                        cursor, table_name, graph_id, offered, "claimed"
                    )
                    return offered
                except Exception as e:
                    # A concurrent writer won the primary key. Re-read and compare.
                    logger.debug("Registry insert for %s lost the race: %s", table_name, e)
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                    recorded = self.get_embedding_identity(table_name, graph_id=graph_id)
                    if recorded is None:
                        raise

            if recorded.is_unknown:
                cursor.execute(
                    f"UPDATE {table} SET mechanism = ?, model_key = ?, declared_config = ?, "
                    "dimension = COALESCE(dimension, ?), set_at = CURRENT_TIMESTAMP, "
                    "set_by = 'claimed' WHERE table_name = ? AND graph_id = ? "
                    "AND model_key IS NULL AND (dimension IS NULL OR dimension = ?)",
                    [
                        offered.mechanism,
                        offered.model_key,
                        offered.declared_config,
                        offered.dimension,
                        table_name,
                        graph_id,
                        offered.dimension,
                    ],
                )
                self.conn.commit()
                # The re-read, not the rowcount, decides: drivers disagree about rowcount
                # on UPDATE, and the winner is whoever's model_key is in the row.
                recorded = self.get_embedding_identity(table_name, graph_id=graph_id)
                if (
                    recorded is not None
                    and recorded.model_key == offered.model_key
                    and recorded.mechanism == offered.mechanism
                ):
                    return recorded
                if recorded is None:
                    raise EmbeddingIdentityConflict(
                        table_name,
                        EmbeddingIdentity(mechanism=None, model_key=None),
                        offered,
                        "the registry row disappeared while it was being claimed",
                        graph_id=graph_id,
                    )

            reason = conflicts(recorded, offered)
            if reason:
                raise EmbeddingIdentityConflict(
                    table_name, recorded, offered, reason, graph_id=graph_id
                )
            return recorded
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    @staticmethod
    def _declared_vector_dtype(cursor, table: str) -> str:
        """The element type IRIS declares for ``table.emb``, defaulting to ``DOUBLE``.

        Read from the same ``%Dictionary.CompiledProperty`` blob
        :meth:`GraphSchema.get_embedding_dimension` parses the width out of
        (``...,DATATYPE,DOUBLE,...,LEN,128,...``), because adoption must describe the live
        column and not the engine's configuration.
        """
        from iris_vector_graph.schema import sanitize_identifier

        class_name = GraphSchema.resolve_table_class(
            cursor, table
        ) or GraphSchema.derive_class_name(table)
        try:
            safe_class = sanitize_identifier(class_name)
        except ValueError:
            return "DOUBLE"

        try:
            cursor.execute(
                "SELECT Parameters FROM %Dictionary.CompiledProperty "
                f"WHERE Name = 'emb' AND Parent = '{safe_class}'"
            )
            for row in cursor.fetchall():
                parts = str(row[0]).split(",")
                for i, part in enumerate(parts):
                    if part == "DATATYPE" and i + 1 < len(parts) and parts[i + 1]:
                        return parts[i + 1].strip().upper()
        except Exception as e:
            logger.debug("Could not read declared dtype for %s: %s", table, e)
        return "DOUBLE"

    def adopt_embedding_identities(self, cursor=None) -> Dict[str, str]:
        """Give every embedding table a registry row, with the model left unknown.

        This is what an installation that predates the registry gets, without operator
        action and without re-embedding (FR-006, FR-007, FR-020). The width and dtype come
        from the live column declaration in the data dictionary — never from
        ``constants.DEFAULT_EMBEDDING_DIMENSION``, never from a checked-in ``.cls``, and
        never from this engine's ``embedding_config``. A ``DdlAllowed`` class is rewritten
        by DDL, so the file on disk is not evidence about the live column.

        The model is recorded as explicitly unknown rather than assumed: nothing in the
        database knows what produced the existing vectors, and the first writer to declare
        a model at the recorded width claims the row (FR-008). Until then the width half of
        the contract is still enforced.

        Never overwrites an existing row, and never reads or writes vector data.

        Returns one outcome per table name:

        ``adopted``
            a row was created from the declared width and dtype.
        ``adopted_no_declared_width``
            the column has no declared length, so ``dimension`` is ``NULL``. This is the
            ``SQLCODE -260`` shape: -260 is raised off the column declaration, not the data.
        ``already_recorded``
            a row was already there; it was left exactly as it was.
        ``absent``
            no such table in this namespace.
        """
        owns_cursor = cursor is None
        cursor = cursor if cursor is not None else self.conn.cursor()
        outcomes: Dict[str, str] = {}
        try:
            for name in VECTOR_TABLE_NAMES:
                table = self._t(name)
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    cursor.fetchone()
                except Exception:
                    outcomes[name] = "absent"
                    continue

                if self.get_embedding_identity(name) is not None:
                    outcomes[name] = "already_recorded"
                    continue

                dimension = GraphSchema.get_embedding_dimension(cursor, table)
                dtype = self._declared_vector_dtype(cursor, table)
                adopted = EmbeddingIdentity(
                    mechanism=None,
                    model_key=None,
                    declared_config=None,
                    dimension=dimension,
                    dtype=dtype,
                )
                try:
                    self._insert_identity(cursor, name, "", adopted, "adopted")
                except Exception as e:
                    # A concurrent writer got there first. Its row stands.
                    logger.debug("Adoption of %s lost the race: %s", name, e)
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                    outcomes[name] = "already_recorded"
                    continue

                outcomes[name] = (
                    "adopted" if dimension is not None else "adopted_no_declared_width"
                )
        finally:
            if owns_cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
        return outcomes

    def enforce_embedding_identity(
        self,
        table_name: str = "kg_NodeEmbeddings",
        *,
        dimension: Optional[int] = None,
        dtype: Optional[str] = None,
        config: Optional[str] = None,
        graph_id: str = "",
    ) -> Optional[EmbeddingIdentity]:
        """Refuse a write whose identity disagrees with what ``table_name`` records.

        Called by every seam that writes vectors, before anything is written and before
        any width is inferred. ``dimension`` is the width of the vector actually being
        offered — not the engine's configured dimension — so a wrong-width write is
        refused whether or not a model is declared (the width half of the 2×2 in
        :func:`~iris_vector_graph.embedding_identity.conflicts`).

        ``config`` names a model declared for this one piece of work — a queue entry's
        ``config``, or the ``embedding_config`` passed to ``enqueue_for_embedding`` — and
        takes precedence over the engine's own. Empty means "this worker's own model".

        Returns the recorded identity, or ``None`` when nothing is recorded: a pre-3.2.0
        schema with no registry table has nothing to conflict with, and creating the table
        is ``initialize_schema``'s job. Enforcement is not opt-in (FR-014) — there is no
        argument, environment variable, or flag that turns this into a warning.

        Raises:
            EmbeddingIdentityConflict: the recorded and offered identities disagree.
        """
        recorded = self.get_embedding_identity(table_name, graph_id=graph_id)
        if recorded is None:
            return None

        offered = self._offered_embedding_identity(
            dimension=dimension, dtype=dtype, config=config
        )
        reason = conflicts(recorded, offered)
        if reason:
            # The graph goes in the message (spec 227): a routed table is named
            # after a hash, so the table name alone does not say what refused.
            raise EmbeddingIdentityConflict(
                table_name, recorded, offered, reason, graph_id=graph_id
            )
        return recorded

    def _sync_recorded_dimension(self, table_name: str, dimension: int) -> None:
        """Move a registry row's width to ``dimension`` after the column was altered.

        The column declaration is the truth about width (FR-006), so a successful
        ``ALTER TABLE ... ALTER COLUMN emb VECTOR(...)`` has to carry the recorded width
        with it — otherwise the engine that just migrated the column would be refused by
        its own registry row on the next write. The model is never touched here: the
        registry is the only thing that knows it, and an ALTER says nothing about it.

        Never inserts. A table with no row is adoption's business, not this method's.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"UPDATE {self._registry_table()} SET dimension = ?, "
                "set_at = CURRENT_TIMESTAMP WHERE table_name = ? AND dimension <> ?",
                [dimension, table_name, dimension],
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Could not sync recorded width for %s: %s", table_name, e)
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _insert_identity(
        self,
        cursor,
        table_name: str,
        graph_id: str,
        identity: EmbeddingIdentity,
        set_by: str,
        kind: str = ROUTE_KIND_NODE,
    ) -> None:
        """Write one registry row.

        ``kind`` is named in the INSERT only for an edge route (spec 230 FR-006). A node
        route leaves the column out and takes its ``DEFAULT 'node'``, which keeps this
        statement writable against a registry whose ``ADD COLUMN kind`` did not take —
        the same registry every pre-230 row lives in, where absent already means node.
        An *edge* route names it and fails loudly if the column is missing: a registry
        that cannot tell the two kinds apart must not be handed an edge route, because
        every reader would then answer a node lookup with a table of ``(s, p, o_id)``.
        """
        if kind not in ROUTE_KINDS:
            raise ValueError(f"kind must be one of {ROUTE_KINDS}, got {kind!r}")
        columns = [
            "table_name",
            "graph_id",
            "mechanism",
            "model_key",
            "declared_config",
            "dimension",
            "dtype",
            "set_by",
        ]
        values = [
            table_name,
            graph_id,
            identity.mechanism,
            identity.model_key,
            identity.declared_config,
            identity.dimension,
            identity.dtype,
            set_by,
        ]
        if kind != ROUTE_KIND_NODE:
            columns.append("kind")
            values.append(kind)
        placeholders = ", ".join(["?"] * len(values))
        cursor.execute(
            f"INSERT INTO {self._registry_table()} ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )
        self.conn.commit()

    def _record_configured_embedding_identity(self, cursor) -> Dict[str, str]:
        """Claim the engine's declared identity on every embedding table that exists.

        Runs from ``initialize_schema``. An engine that declares no model records nothing:
        there is no model to claim, and adoption has already recorded the width.

        The width offered here is the table's **live declared width**, not the engine's
        configured dimension. A column the migration could not alter (because it holds
        rows) keeps its width, and recording that width is what lets the write seams refuse
        the engine's wrong-width vectors with a message about the column declaration —
        rather than failing schema initialization for a database that was fine a moment ago.
        """
        outcomes: Dict[str, str] = {}
        offered = self._offered_embedding_identity()
        if offered.is_unknown:
            return outcomes

        for name in VECTOR_TABLE_NAMES:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {self._t(name)}")
                cursor.fetchone()
            except Exception:
                continue  # table absent in this namespace (DDL-only, or optimized table)

            dim = GraphSchema.get_embedding_dimension(cursor, self._t(name))
            from dataclasses import replace as _replace

            self.set_embedding_identity(_replace(offered, dimension=dim), name)
            outcomes[name] = "claimed"
        return outcomes

    def _migrate_vector_dimensions(self, cursor, dim: int) -> Dict[str, Any]:
        """Bring every vector column in ``VECTOR_TABLES`` to ``dim``.

        Each table is read on its own — a column already at ``dim`` is left
        alone, an untyped column is typed, and a column at the wrong width is
        altered only when its table is empty. ALTER on a populated vector column
        is not free, so a non-empty mismatch is reported and left for a human.

        Before 3.1.0 this logic read one dimension (the node table's) and
        compared it against the configured ``dim``, so a second writer sending a
        different width to ``kg_EdgeEmbeddings`` was invisible: the node column
        agreed with the engine, the check passed, and every edge insert was
        rejected row by row. A consumer lost 1,099 writes an hour for weeks that way.

        Returns a report — ``altered``, ``unchanged``, ``needs_manual_migration``,
        ``failed`` — so callers can see what happened instead of inferring it
        from the absence of an exception.
        """
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise ValueError(f"embedding dimension must be a positive int, got {dim!r}")

        report: Dict[str, Any] = {
            "altered": [],
            "unchanged": [],
            "needs_manual_migration": [],
            "failed": {},
        }
        altered_names: List[str] = []

        for name in VECTOR_TABLES:
            table = self._t(name)
            db_dim = GraphSchema.get_embedding_dimension(cursor, table)

            if db_dim == dim:
                report["unchanged"].append(table)
                continue

            if db_dim is not None:
                row_count = None
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    row = cursor.fetchone()
                    row_count = int(row[0]) if row else None
                except Exception as e:
                    logger.debug("Could not count %s: %s", table, e)
                if row_count != 0:
                    logger.error(
                        "CRITICAL: %s.emb is VECTOR(DOUBLE, %d) but the engine is configured "
                        "for %d, and the table is not empty (%s rows). Every write of the "
                        "configured width will be rejected. Drop and recreate the table, or "
                        "re-embed at %d.",
                        table, db_dim, dim, row_count, db_dim,
                    )
                    report["needs_manual_migration"].append(table)
                    continue
                logger.info(
                    "%s.emb is %d, configured %d, table EMPTY — altering to %d",
                    table, db_dim, dim, dim,
                )
            else:
                logger.info(
                    "%s.emb has no declared dimension — altering to VECTOR(DOUBLE, %d)",
                    table, dim,
                )

            try:
                cursor.execute(
                    f"ALTER TABLE {table} ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                )
                report["altered"].append(table)
                altered_names.append(name)
            except Exception as e:
                # Not swallowed: kg_NodeEmbeddings_optimized is absent in
                # DDL-only namespaces, but so is a genuinely failed migration,
                # and the caller has to be able to tell them apart.
                logger.warning("Could not ALTER %s to dim %d: %s", table, dim, e)
                report["failed"][table] = str(e)

        if report["altered"]:
            try:
                self.conn.commit()
            except Exception as e:
                logger.warning("Could not commit vector dimension migration: %s", e)

        # The registry's width follows the column it describes (spec 226), for every
        # table and not only the ones this call altered. `altered_names` is empty
        # whenever the columns already hold the configured width — which is exactly
        # the state a second writer finds after the first one migrated, and exactly
        # when its own stale row needs carrying forward (FR-031).
        self.reconcile_recorded_dimensions(cursor)

        return report

    def reconcile_recorded_dimensions(self, cursor) -> Dict[str, int]:
        """Make every embedding table's recorded width match its column declaration.

        The column is the truth about width (FR-006): it is what the next INSERT is
        checked against, and a registry row that disagrees refuses writes naming a
        width no column has. So this reads each column and writes the registry,
        unconditionally, rather than as a side effect of having altered something.

        A column with no declared width is left alone. ``None`` means the catalog has
        nothing to copy, and inventing a width would be worse than the ``SQLCODE -260``
        that already tells the caller the column is undeclared.

        Returns:
            ``{table_name: dimension}`` for the tables reconciled — short names, as
            the registry stores them.
        """
        reconciled: Dict[str, int] = {}
        for name in VECTOR_TABLES:
            declared = GraphSchema.get_embedding_dimension(cursor, self._t(name))
            if declared is None:
                continue
            self._sync_recorded_dimension(name, declared)
            reconciled[name] = declared
        return reconciled

    # ----------------------------------------------------------------- inventory

    def embedding_inventory(self) -> List["EmbeddingInventoryRow"]:
        """One row per embedding route, plus one per graph that has none (FR-020).

        The report an operator reads instead of writing SQL against the registry
        (FR-038). Three of its columns come from three different authorities, and
        keeping them apart is the point:

        * **the registry** — which model a route declares, and at what width. It is the
          authority on identity because it is what a write is checked against.
        * **the table** — how many rows the route actually holds, counted inside the
          route's graph. A legacy table holds every graph's rows, so an unscoped count
          would report the namespace's total against each graph, in the report about
          having stopped doing that.
        * **the class dictionary** — whether an ANN index exists, through
          :meth:`GraphSchema.hnsw_indexes`, the single owner of that read. Never from
          the registry's ``index_state``, which records what one ``CREATE INDEX``
          replied and not what the namespace holds now, and never from a row count,
          which is the synthesized index row spec 226 removed (SC-009).

        A graph with nodes and no route appears with ``table_name=None`` rather than
        being omitted (US4-3): "the vectors are in another route" and "there are no
        vectors" need opposite actions, and an absent row makes them the same answer.

        An unreadable registry yields ``[]``. That is a 3.2.0-or-earlier database,
        which has no routes to report, and a status command must not raise on one.
        """
        cursor = self.conn.cursor()
        try:
            registry_rows = self._registry_inventory_rows(cursor)
            if registry_rows is None:
                return []
            known_tables = self._graph_kg_tables(cursor)
            out: List[EmbeddingInventoryRow] = []
            routed_graphs = set()
            for row in registry_rows:
                (
                    table_name,
                    graph_id,
                    model_key,
                    dimension,
                    dtype,
                    recorded_state,
                    recorded_error,
                    recall,
                    recall_at,
                ) = row
                graph_id = graph_id or DEFAULT_GRAPH
                routed_graphs.add(graph_id)
                index_name, index_state, index_error = self._route_index_report(
                    cursor, table_name, recorded_state, recorded_error
                )
                out.append(
                    EmbeddingInventoryRow(
                        graph_id=graph_id,
                        model_key=model_key or None,
                        table_name=table_name,
                        dimension=int(dimension) if dimension is not None else None,
                        dtype=dtype or None,
                        row_count=self._route_row_count(
                            cursor, table_name, graph_id, known_tables
                        ),
                        index_name=index_name,
                        index_state=index_state,
                        index_error=index_error,
                        recall_measured=float(recall) if recall is not None else None,
                        recall_measured_at=str(recall_at) if recall_at else None,
                    )
                )
            for graph_id in self._graphs_with_nodes(cursor):
                if graph_id in routed_graphs:
                    continue
                out.append(
                    EmbeddingInventoryRow(
                        graph_id=graph_id,
                        model_key=None,
                        table_name=None,
                        dimension=None,
                        dtype=None,
                        row_count=0,
                        index_name=None,
                        index_state="absent",
                        index_error=None,
                        recall_measured=None,
                        recall_measured_at=None,
                    )
                )
            out.sort(
                key=lambda r: (r.graph_id or "", r.model_key or "", r.table_name or "")
            )
            return out
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _registry_inventory_rows(self, cursor) -> Optional[List[tuple]]:
        """Every registry row, or ``None`` when the registry cannot be read.

        ``None`` and ``[]`` are different databases: an empty registry is a routed
        install with no routes yet, while an unreadable one does not route at all, and
        only the second means "report nothing".
        """
        columns = ", ".join(_INVENTORY_REGISTRY_COLUMNS)
        try:
            cursor.execute(
                f"SELECT {columns} FROM {self._registry_table()} "
                "ORDER BY graph_id, table_name"
            )
            rows = [tuple(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.debug("Embedding registry not readable for the inventory: %s", e)
            return None
        # Nine columns were asked for. Fewer means the answer came from something other
        # than a 227 registry, and reading a width out of column four of it would report
        # a number with no relationship to any column declaration.
        return [r for r in rows if len(r) >= len(_INVENTORY_REGISTRY_COLUMNS)]

    def _graph_kg_tables(self, cursor) -> Optional[set]:
        """Bare table names the catalog holds in this engine's schema, or ``None``.

        Filtered on ``TABLE_SCHEMA``: IRIS projects an auto-generated view per class
        into ``SQLUser``, so a namespace-wide probe answers with the table and its view
        and anything counting the result doubles.

        ``None`` means the catalog could not be read, which is not the same as "the
        table is gone" — the row count then falls back to asking the table directly.
        """
        try:
            cursor.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?",
                [self._schema_prefix],
            )
            return {str(r[0]) for r in cursor.fetchall()}
        except Exception as e:
            logger.debug("Could not list tables in %s: %s", self._schema_prefix, e)
            return None

    def _graphs_with_nodes(self, cursor) -> List[str]:
        """Every graph that has at least one node. Empty when ``nodes`` cannot be read.

        ``nodes`` and not ``rdf_labels``: a node is what an embedding can be written
        for, and the FK on every routed table points here.
        """
        try:
            cursor.execute(f"SELECT DISTINCT graph_id FROM {self._t('nodes')}")
            return sorted({(r[0] or DEFAULT_GRAPH) for r in cursor.fetchall()})
        except Exception as e:
            logger.debug("Could not list graphs for the inventory: %s", e)
            return []

    def _route_index_report(self, cursor, table_name: str, recorded_state, recorded_error):
        """``(index_name, index_state, index_error)`` for one route's table.

        ``present`` comes from the class dictionary and nowhere else. A recorded
        ``refused`` still supplies the reason, because IRIS's own wording is the only
        thing that tells an operator why the build would not happen.
        """
        held = []
        try:
            held = GraphSchema.hnsw_indexes(cursor, self._t(table_name))
        except Exception as e:  # pragma: no cover - hnsw_indexes swallows its own
            logger.debug("Could not read the index state of %s: %s", table_name, e)
        if held:
            return held[0][0], "present", None
        if recorded_state == "refused" or recorded_error:
            return None, "refused", recorded_error or None
        return None, "absent", None

    def _route_row_count(
        self, cursor, table_name: str, graph_id: str, known_tables: Optional[set]
    ) -> int:
        """How many vectors this route holds *in this graph*.

        Zero for a registry row whose table is gone — the state a failed post-commit
        drop during an erase leaves behind. The row still has to appear in the report,
        because the operator who has to remove it cannot see it anywhere else.
        """
        if known_tables is not None and table_name not in known_tables:
            return 0
        try:
            table = self._t(sanitize_identifier(table_name))
        except ValueError:
            logger.warning("Registry names a table that is not an identifier: %r", table_name)
            return 0
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {graph_scope_predicate('graph_id')}",
                [graph_id],
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.debug("Could not count rows in %s: %s", table_name, e)
            return 0

    # --------------------------------------------------------------- quarantine

    def list_quarantine(self, *, reason: Optional[str] = None) -> List["QuarantinedVector"]:
        """Every vector the migration could not place, oldest first (FR-040, §5).

        The only read of ``embedding_quarantine`` in the package. No search path reaches
        it: a quarantined row has no graph, so a KNN that scored it would return a
        neighbour from an unknown space — spec 227's own leak, at the bottom of a table
        named for having caught it.

        ``reason`` must be one of :data:`QUARANTINE_REASONS`. A misspelled reason raises
        rather than answering ``[]``, because ``[]`` reads as "nothing is quarantined"
        and that is the answer an operator acts on by moving on.

        A database with no quarantine table yields ``[]`` — it is a 3.2.0 install, which
        has never quarantined anything, and a status command must not raise on one.
        """
        if reason is not None and reason not in QUARANTINE_REASONS:
            raise ValueError(
                f"{reason!r} is not a quarantine reason; expected one of "
                f"{', '.join(QUARANTINE_REASONS)}"
            )
        columns = ", ".join(_QUARANTINE_COLUMNS)
        sql = f"SELECT {columns} FROM {self._quarantine_table()}"
        params: List[Any] = []
        if reason is not None:
            sql += " WHERE reason = ?"
            params.append(reason)
        sql += " ORDER BY q_rowid"

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            rows = [tuple(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.debug("Quarantine not readable: %s", e)
            return []
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        out: List[QuarantinedVector] = []
        for row in rows:
            if len(row) < len(_QUARANTINE_COLUMNS):
                continue
            q_rowid, node_id, source_table, dimension, dtype, metadata, why, at = row[:8]
            out.append(
                QuarantinedVector(
                    q_rowid=int(q_rowid),
                    node_id=str(node_id),
                    source_table=str(source_table),
                    dimension=int(dimension) if dimension is not None else None,
                    dtype=str(dtype) if dtype else None,
                    metadata=metadata,
                    reason=str(why),
                    quarantined_at=str(at) if at else None,
                )
            )
        return out

    def place_quarantined(
        self, q_rowid: int, *, graph: str, model_key: Optional[str] = None
    ) -> bool:
        """Move one quarantined vector into ``(graph, model_key)``'s route (FR-040, §5).

        One row, named explicitly. There is no bulk drain and no ``force``: the operator
        who knows where a row belongs knows it one row at a time, and placing a whole
        table at once is the silent default-graph assignment FR-028 forbids under another
        name.

        Refuses — raising, leaving the row where it is — when the pair has no route, when
        the route's declared width or dtype disagrees with the row's, or when the node
        does not exist *in that graph*. The route is never created here: creating one
        would declare a width taken from a row whose provenance is the thing in doubt.

        The vector moves with an ``INSERT ... SELECT`` and the quarantine row is deleted
        after, in that order. A refused INSERT therefore leaves the only surviving copy
        where it was; a DELETE that went first would destroy it to satisfy a write IRIS
        had already rejected.
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        row = self._quarantined_row(q_rowid)
        if row is None:
            raise ValueError(
                f"no quarantined vector with q_rowid {q_rowid!r}; "
                "list_quarantine() reports the rows that exist"
            )
        node_id, row_dimension, row_dtype = row

        route = self.resolve_route(graph_id, model_key, create=False)
        if route is None:
            raise ValueError(
                f"({graph_id!r}, {model_key!r}) has no embedding route, so there is "
                "nowhere to place this vector. Store a vector for that pair first, or "
                "place the row in a graph that is already routed."
            )
        # The comparison a write would make, raised the way a write raises it: the route
        # is what is recorded, the quarantined row is what is being offered. Only the
        # width and dtype are compared — the row declares no model, which is why it is
        # in quarantine at all.
        recorded = EmbeddingIdentity(
            mechanism=None,
            model_key=route.model_key,
            dimension=route.dimension,
            dtype=route.dtype or "DOUBLE",
        )
        offered = EmbeddingIdentity(
            mechanism=None,
            model_key=route.model_key,
            dimension=int(row_dimension) if row_dimension is not None else None,
            dtype=str(row_dtype or "DOUBLE"),
        )
        if route.dimension is None or int(route.dimension) != int(row_dimension or 0):
            raise EmbeddingIdentityConflict(
                route.table_name,
                recorded,
                offered,
                f"quarantined vector {q_rowid} is {row_dimension}-wide and the route "
                f"declares {route.dimension}; IRIS refuses the INSERT with SQLCODE -104 "
                "rather than reshaping it (ADR-0005)",
                graph_id=graph_id,
            )
        if str(row_dtype or "").upper() != str(route.dtype or "").upper():
            raise EmbeddingIdentityConflict(
                route.table_name,
                recorded,
                offered,
                f"quarantined vector {q_rowid} is {row_dtype} and the route holds "
                f"{route.dtype}",
                graph_id=graph_id,
            )
        if not self.node_exists(node_id, graph=graph_id):
            raise ValueError(
                f"node {node_id!r} does not exist in graph {graph_id!r}; a routed table's "
                "foreign key points at (graph_id, node_id), so this placement names a "
                "node that is not there"
            )

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO {self._t(route.table_name)}"
                " (graph_id, node_id, emb, metadata)"
                " SELECT ?, node_id, emb, metadata"
                f" FROM {self._quarantine_table()} WHERE q_rowid = ?",
                [graph_id, q_rowid],
            )
            cursor.execute(
                f"DELETE FROM {self._quarantine_table()} WHERE q_rowid = ?", [q_rowid]
            )
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return True

    def _quarantined_row(self, q_rowid) -> Optional[tuple]:
        """``(node_id, dimension, dtype)`` for one quarantined row, or ``None``.

        ``emb`` is not selected: the vector is copied by IRIS and never needs to be here.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT node_id, dimension, dtype FROM "
                f"{self._quarantine_table()} WHERE q_rowid = ?",
                [q_rowid],
            )
            row = cursor.fetchone()
        except Exception as e:
            logger.debug("Quarantine row %r not readable: %s", q_rowid, e)
            return None
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        if row is None:
            return None
        return (str(row[0]), row[1], row[2])

    def initialize_schema(self, auto_deploy_objectscript: bool = True) -> dict:
        """
        Create the base schema tables in IRIS.

        Returns a status dict with keys:
          - 'tables_created': True/False
          - 'objectscript_deployed': True/False
          - 'kg_built': True/False  
          - 'embedding_dimension': int
          - 'warnings': list[str]

        Safe to call on existing databases — statements that fail with "already exists"
        are silently ignored.  Raises if ``embedding_dimension`` has not been set (either
        via the constructor or prior calls to :meth:`store_embedding`).

        Args:
            auto_deploy_objectscript: When True (default), attempt to load and compile
                the ObjectScript .cls files from iris_src/ into IRIS.  On failure a
                warning is logged and the engine falls back to Python/SQL paths.
                Set to False to skip .cls deployment entirely.
        """
        from iris_vector_graph.utils import _split_sql_statements

        dim = self.embedding_dimension
        if dim is None:
            raise ValueError(
                "embedding_dimension must be set before calling initialize_schema(). "
                "Pass it to IRISGraphEngine(conn, embedding_dimension=<N>) or call "
                "store_embedding() first so the dimension can be inferred."
            )

        cursor = self.conn.cursor()
        try:
            cursor.execute("CREATE SCHEMA Graph_KG")
        except Exception:
            pass  # already exists

        sql = GraphSchema.get_base_schema_sql(embedding_dimension=dim)
        # Asked once, before the script runs: `docs` gains `graph_id` in the 4.0.0
        # upgrade's `docs` step, so on a 3.2.0 install the index below cannot be created
        # yet. Probing after the failure would be probing a table this script may have
        # created in the meantime.
        docs_awaits_graph = self._docs_awaits_graph_column(cursor)
        for stmt in _split_sql_statements(sql):
            if not stmt.strip():
                continue
            if docs_awaits_graph and "idx_docs_graph" in stmt.lower():
                logger.warning(
                    "idx_docs_graph deferred: Graph_KG.docs has no graph_id column on "
                    "this install, so there is nothing to index yet. Run "
                    "iris_vector_graph.migrations.upgrade_to_4_0_0(conn) — its `docs` "
                    "step places every document in a graph and creates this index when "
                    "it is done. Nothing else is affected."
                )
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                _OPTIONAL_DDL_PATTERNS = (
                    "ifind",
                    "json_value",
                    "indextype",
                    "%find",
                    "kg_txt",
                    "kg_rrf",
                    "irisdev",
                    "iris_src",
                )
                if (
                    "already exists" not in err
                    and "already has a" not in err
                    and "already has index" not in err
                ):
                    import re as _re_ddl

                    _sqlcode = _re_ddl.search(
                        r"sqlcode.*?<(-?\d+)>", err
                    ) or _re_ddl.search(r"<(-\d+)>", err)
                    _sqlcode_val = _sqlcode.group(1) if _sqlcode else ""
                    is_index_on_rdf_edges = (
                        _sqlcode_val == "-400"
                        and "rdf_edges" in stmt.lower()
                        and "create index" in stmt.lower()
                    )
                    if (
                        any(
                            p in err or p in stmt.lower()
                            for p in _OPTIONAL_DDL_PATTERNS
                        )
                        or is_index_on_rdf_edges
                    ):
                        logger.debug(
                            "Optional DDL skipped (will retry via ALTER TABLE): %s",
                            stmt[:80],
                        )
                    else:
                        logger.warning(
                            "Schema setup warning: %s | Statement: %.100s", e, stmt
                        )

        # 3. Ensure indexes and run schema migrations (e.g. column size upgrades)
        GraphSchema.ensure_indexes(cursor)
        # Update the engine flag after migration — ensure_indexes runs
        # add_graph_id_to_nodes which adds the column if absent.
        self._nodes_has_graph_id = self._probe_nodes_graph_id()
        # Same round-trip for the child tables (spec 227 FR-034): a 3.2.0 install that
        # has not run the re-key has no graph_id on rdf_labels / rdf_props, and naming
        # it there would fail every create_node with SQLCODE -29.
        self._children_have_graph_id = self._probe_children_graph_id()

        # 3b. Embedding identity registry (spec 226). Created before anything alters a
        # vector column, so the recorded expectation exists before the width can move.
        self._ensure_embedding_registry(cursor)

        # 3b-ii. Quarantine (spec 227, FR-039). Created alongside the registry, not
        # lazily by the migration: a row whose graph cannot be recovered needs a
        # destination at the moment it is found.
        self._ensure_embedding_quarantine(cursor)

        # 3c. Adopt whatever the columns already declare (spec 226, FR-006). Runs before
        # the migration below so the recorded expectation exists before a width can move,
        # and so an installation that predates the registry needs no operator action.
        self.adopt_embedding_identities(cursor)

        # 4. Bring every vector column to the configured dimension.
        #
        # The handler is deliberately narrow (spec 226, FR-012). It used to be
        # `except Exception`, which turned every refusal and every bad argument into a log
        # line and let initialize_schema report success: an EmbeddingIdentityConflict and a
        # `embedding_dimension=0` both became warnings. Only the DB-API errors an ALTER
        # legitimately raises are tolerated here; ValueError and
        # EmbeddingIdentityConflict propagate.
        # The report is kept, not discarded. `needs_manual_migration` names tables the
        # engine deliberately refuses to fix — a populated column at another width,
        # which it cannot widen without inventing dimensions — and every write of the
        # configured width to one of them is rejected with SQLCODE -104. Reporting that
        # only through `logger.error` left the returned status reading as a clean setup.
        vector_migration: Dict[str, Any] = {}
        try:
            vector_migration = self._migrate_vector_dimensions(cursor, dim) or {}
        except _ALTER_TOLERATED_ERRORS as e:
            logger.warning("Could not verify embedding dimension: %s", e)
        needs_manual_migration = list(vector_migration.get("needs_manual_migration") or [])

        # 4b. Record what this engine says produces the vectors. An engine that declares
        # no model records nothing.
        self._record_configured_embedding_identity(cursor)

        # 5. Install stored procedures
        self._install_procedures(cursor)

        self.conn.commit()

        # 5b. Create SQLUser views so IVG's Python PPR fallback can use unqualified table names
        for view_sql in [
            "CREATE VIEW SQLUser.nodes AS SELECT node_id, created_at FROM Graph_KG.nodes",
            "CREATE VIEW SQLUser.rdf_edges AS SELECT * FROM Graph_KG.rdf_edges",
            "CREATE VIEW SQLUser.rdf_labels AS SELECT * FROM Graph_KG.rdf_labels",
            "CREATE VIEW SQLUser.rdf_props AS SELECT * FROM Graph_KG.rdf_props",
        ]:
            try:
                cursor.execute(view_sql)
            except Exception:
                pass

        # 6. Deploy ObjectScript .cls layer (best-effort)
        if auto_deploy_objectscript:
            try:
                pkg_dir = Path(__file__).parent.parent / "iris_src"
                if not pkg_dir.exists():
                    pkg_dir = Path(__file__).parent / ".." / "iris_src"
                self.capabilities = GraphSchema.deploy_objectscript_classes(
                    cursor, pkg_dir.resolve(), conn=self.conn
                )
            except RdfEdgesRescueError:
                # Deploying is best-effort; rescuing a class-owned `rdf_edges` is not.
                # Swallowing this one leaves the namespace with no edge table while
                # `initialize_schema` reports success.
                raise
            except Exception as exc:
                logger.debug(
                    "ObjectScript auto-deploy skipped (expected in Docker — use docker cp + LoadDir): %s",
                    exc,
                )
                self.capabilities = IRISCapabilities()
        else:
            self.capabilities = IRISCapabilities()

        # 6b. Always detect capabilities from %Dictionary (deployment may have failed
        # but classes could already be compiled from a prior docker cp + LoadDir)
        if not self.capabilities.objectscript_deployed:
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                    "WHERE Name='Graph.KG.PageRank'"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    self.capabilities.objectscript_deployed = True
                    logger.info("ObjectScript classes detected (pre-compiled)")
            except Exception:
                pass

        if not self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                routine_exists = int(iris_obj.classMethodValue("%Routine", "Exists", "Graph.KG.PageRank.1"))
                if routine_exists:
                    cursor.execute(
                        "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                        "WHERE Name='Graph.KG.PageRank'"
                    )
                    row = cursor.fetchone()
                    class_registered = int(row[0]) if row else 0
                    if class_registered:
                        self.capabilities.objectscript_deployed = True
                        logger.info("ObjectScript classes detected via %%Routine.Exists fallback")
                    else:
                        logger.warning(
                            "ObjectScript routines compiled but class dictionary missing "
                            "(irishealth ^oddDEF/^rOBJ mapping issue — classes not callable). "
                            "Use iris-community image or Atelier API for class deployment."
                        )
            except Exception:
                pass

        if self.capabilities.objectscript_deployed and not self.capabilities.kg_built:
            try:
                built = GraphSchema.bootstrap_kg_global(cursor, conn=self.conn)
                if built:
                    self.capabilities.kg_built = True
                    self.conn.commit()
            except Exception as exc:
                logger.warning("^KG bootstrap failed: %s", exc)

        # 6c. The deploy above compiles `Graph.KG`, and a package compile deletes the
        # generated class an iFind index is searched through without writing a new one
        # (spec 230, FR-030). The index definition survives, so only a query notices —
        # `kg_TXT` fails at Open with <CLASS DOES NOT EXIST>. Repair it here, where a
        # compile has just run, rather than leaving the text leg dead until someone
        # recompiles the owning class by hand.
        ifind = repair_ifind_helpers(cursor, conn=self.conn)

        # 6d. Tell the optimizer the shape of the tables just created. IVG tuned
        # nothing anywhere, and a `(node_id, graph_id)` equality — every graph-scoped
        # read, and both of `rdf_edges`' composite foreign keys on every edge insert —
        # planned as "Read master map Graph_KG.nodes.IDKEY, looping on ID", a scan of
        # the extent (spec 230, FR-033). Measured live: 5.3ms per lookup over 46,343
        # rows and 10.2ms per edge insert, against 0.23ms once tuned.
        tuned = GraphSchema.tune_tables(cursor)
        try:
            self.conn.commit()
        except Exception:
            pass

        status = {
            "tables_created": True,
            "ifind_indexes": ifind,
            "tuned": tuned,
            "objectscript_deployed": self.capabilities.objectscript_deployed,
            "kg_built": self.capabilities.kg_built,
            "embedding_dimension": dim,
            "needs_manual_migration": needs_manual_migration,
            "warnings": [],
        }
        for table in needs_manual_migration:
            status["warnings"].append(
                f"{table} holds rows at a vector width other than the configured "
                f"{dim}, and a populated column cannot be widened — every write of "
                "the configured width will be rejected (SQLCODE -104). Re-embed the "
                "table at the configured width, or clear it and re-run "
                "initialize_schema()."
            )
        if not self.capabilities.objectscript_deployed:
            status["warnings"].append(
                "ObjectScript classes not deployed — BFS, Subgraph, PageRank using Python fallbacks. "
                "Run docker cp iris_src/src <container>:/tmp/src && docker exec <container> iris session IRIS "
                "-U USER 'Do $system.OBJ.LoadDir(\"/tmp/src\",\"ck\",,1)' to deploy."
            )
        if not self.capabilities.kg_built:
            status["warnings"].append(
                "^KG adjacency index not built — multi-hop BFS unavailable. "
                "Call BuildKG() after loading data: from iris_vector_graph.schema import _call_classmethod; "
                "_call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')"
            )
        unsearchable = sorted(name for name, ok in ifind.items() if not ok)
        if unsearchable:
            status["warnings"].append(
                f"iFind index not searchable: {', '.join(unsearchable)} — text search "
                "through it fails with <CLASS DOES NOT EXIST>. Recompile the owning "
                "class: Do $SYSTEM.OBJ.Compile(\"<owner>\",\"ck\")"
            )

        if status["warnings"]:
            for w in status["warnings"]:
                logger.warning("IVG setup: %s", w[:120])

        logger.info(
            "initialize_schema() complete — objectscript=%s kg_built=%s dim=%d",
            status["objectscript_deployed"],
            status["kg_built"],
            dim,
        )
        return status


    def get_schema_visualization(self) -> dict:
        cursor = self.conn.cursor()

        cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
        labels = [r[0] for r in cursor.fetchall()]

        cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        rel_types = [r[0] for r in cursor.fetchall()]

        nodes = []
        for i, label in enumerate(labels):
            cursor.execute(
                "SELECT TOP 1 rl.s FROM Graph_KG.rdf_labels rl WHERE rl.label = ?",
                [label],
            )
            row = cursor.fetchone()
            sample_id = row[0] if row else None

            prop_names = []
            if sample_id:
                cursor.execute(
                    'SELECT DISTINCT TOP 20 "key" FROM Graph_KG.rdf_props WHERE s = ? '
                    'ORDER BY "key"',
                    [sample_id],
                )
                prop_names = [r[0] for r in cursor.fetchall()]

            nodes.append(
                {
                    "id": i,
                    "name": label,
                    "labels": [label],
                    "properties": [{"name": p, "type": "String"} for p in prop_names],
                }
            )

        label_to_id = {n["name"]: n["id"] for n in nodes}

        rels = []
        for i, rel_type in enumerate(rel_types):
            # TOP 1 because one row is what fetchone() takes and this cursor is
            # reused immediately below. Left uncapped, the rows still pending after
            # fetchone() put the next execute() out of sequence and IRIS closed the
            # connection under the caller — <COMMUNICATION ERROR> Message out of
            # order, then <COMMUNICATION LINK ERROR> on everything after it.
            cursor.execute(
                "SELECT TOP 1 s, o_id FROM Graph_KG.rdf_edges WHERE p = ?", [rel_type]
            )
            row = cursor.fetchone()
            start_label_id = 0
            end_label_id = 0
            if row:
                src_id, tgt_id = row
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [src_id]
                )
                src_row = cursor.fetchone()
                if src_row:
                    start_label_id = label_to_id.get(src_row[0], 0)
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [tgt_id]
                )
                tgt_row = cursor.fetchone()
                if tgt_row:
                    end_label_id = label_to_id.get(tgt_row[0], 0)

            rels.append(
                {
                    "id": i,
                    "name": rel_type,
                    "type": rel_type,
                    "properties": [],
                    "startNode": start_label_id,
                    "endNode": end_label_id,
                }
            )

        return {"nodes": nodes, "relationships": rels}


    def sync(self) -> bool:
        """Unified sync of adjacency and acceleration indexes (^KG + ^NKG).

        Idempotent. Chooses Rust accelerator for ^NKG when arno is available.
        Always clears the pending-sync flag after the attempt completes.

        Returns:
            True on success, False if a fatal error prevented completion.
        """
        kg_ok = self._sync_kg()
        nkg_ok = self._sync_nkg()
        self._nkg_dirty = False
        return kg_ok and nkg_ok


    def _sync_kg(self) -> bool:
        try:
            iris_obj = self._iris_obj()
            iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
            self.capabilities.kg_built = True
            self._nkg_dirty = True
            logger.info("^KG adjacency index rebuilt successfully")
            return True
        except Exception as e:
            logger.warning("_sync_kg failed: %s", e)
            return False


    def _sync_nkg(self) -> bool:
        try:
            iris_obj = self._iris_obj()
            rust_succeeded = False
            if self._detect_arno() and self._arno_capabilities.get("rust_callout"):
                try:
                    import json as _json
                    raw = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "BuildNKGRust"))
                    result = _json.loads(raw)
                    if "error" not in result:
                        logger.info("^NKG rebuilt via Rust: %s", result)
                        rust_succeeded = True
                    else:
                        logger.warning("BuildNKGRust returned error (%s), falling back to ObjectScript", result["error"])
                except Exception as rust_exc:
                    logger.warning("BuildNKGRust raised (%s), falling back to ObjectScript", rust_exc)
            if not rust_succeeded:
                iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildNKG")
            iris_obj.classMethodValue("Graph.KG.Traversal", "Build2HopStats")
            try:
                iris_obj.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
            except Exception:
                pass
            self._nkg_dirty = False
            return True
        except Exception as e:
            logger.warning("_sync_nkg failed: %s", e)
            return False


    def rebuild_kg(self) -> bool:
        """Deprecated: use ``engine.sync()`` instead."""
        import warnings
        warnings.warn(
            "rebuild_kg() is deprecated. Use engine.sync() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._sync_kg()


    def rebuild_nkg(self) -> bool:
        """Deprecated: use ``engine.sync()`` instead."""
        import warnings
        warnings.warn(
            "rebuild_nkg() is deprecated. Use engine.sync() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._sync_nkg()


    def backfill_degp(self) -> int:
        try:
            result = self._iris_obj().classMethodValue("Graph.KG.Traversal", "BackfillDegp")
            return int(result)
        except Exception as e:
            logger.warning("backfill_degp failed: %s", e)
            return 0


    def backfill_deg2p_exact(self) -> int:
        try:
            result = self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopExactStats")
            return int(result)
        except Exception as e:
            logger.warning("backfill_deg2p_exact failed: %s", e)
            return 0


    def materialize_inference(
        self, rules: str = "rdfs", graph: Optional[str] = None
    ) -> Dict[str, int]:
        _ledger_check(self, "materialize_inference")
        RDFS_SUBCLASSOF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
        RDFS_SUBPROPOF = "http://www.w3.org/2000/01/rdf-schema#subPropertyOf"
        RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
        RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"
        OWL_EQUIV_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"
        OWL_EQUIV_PROP = "http://www.w3.org/2002/07/owl#equivalentProperty"
        OWL_INVERSE = "http://www.w3.org/2002/07/owl#inverseOf"
        OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"
        OWL_TRANS_PROP = "http://www.w3.org/2002/07/owl#TransitiveProperty"
        OWL_SYM_PROP = "http://www.w3.org/2002/07/owl#SymmetricProperty"
        INFERRED_JSON = INFERRED_QUALIFIER_JSON

        cursor = self.conn.cursor()
        inferred_count = 0

        # The default graph is spelled '' by create_edge and NULL by any writer that
        # omitted the column before it was tightened, so both spellings have to
        # answer here — matching only one made default-graph inference read an empty
        # table and report success having inferred nothing.
        graph_filter_sql = " AND graph_id = ?" if graph else " AND COALESCE(graph_id, '') = ''"
        graph_filter_params = [graph] if graph else []

        def _fetch_edges(predicate):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ? "
                f"AND (qualifiers IS NULL OR qualifiers NOT LIKE '{INFERRED_QUALIFIER_LIKE}')"
                + graph_filter_sql,
                [predicate] + graph_filter_params,
            )
            return set((r[0], r[1]) for r in cursor.fetchall())

        def _exists(s, p, o):
            if graph:
                cursor.execute(
                    "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id=?",
                    [s, p, o, graph],
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? "
                    "AND COALESCE(graph_id, '') = ''",
                    [s, p, o],
                )
            row = cursor.fetchone()
            return row is not None and int(row[0]) > 0

        def _insert_inferred(triples):
            nonlocal inferred_count
            for s, p, o in triples:
                if not _exists(s, p, o):
                    try:
                        if graph:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON, graph],
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) "
                                "VALUES (?, ?, ?, ?, '')",
                                [s, p, o, INFERRED_JSON],
                            )
                        inferred_count += 1
                    except Exception:
                        pass
            try:
                self.conn.commit()
            except Exception:
                pass

        def _transitive_closure(direct_edges):
            closure = set(direct_edges)
            changed = True
            while changed:
                changed = False
                new = set()
                for a, b in closure:
                    for b2, c in closure:
                        if b == b2 and (a, c) not in closure and a != c:
                            new.add((a, c))
                if new:
                    closure |= new
                    changed = True
            return closure - direct_edges

        subclass_direct = _fetch_edges(RDFS_SUBCLASSOF)
        subprop_direct = _fetch_edges(RDFS_SUBPROPOF)

        inferred = set()
        inferred |= {
            (a, RDFS_SUBCLASSOF, c) for a, c in _transitive_closure(subclass_direct)
        }
        inferred |= {
            (a, RDFS_SUBPROPOF, c) for a, c in _transitive_closure(subprop_direct)
        }

        rdf_type_edges = _fetch_edges(RDF_TYPE)
        all_subclass = subclass_direct | {
            (a, c) for a, _, c in inferred if _ == RDFS_SUBCLASSOF
        }
        for x, cls_a in list(rdf_type_edges):
            for a, b in all_subclass:
                if a == cls_a:
                    inferred.add((x, RDF_TYPE, b))

        domain_edges = _fetch_edges(RDFS_DOMAIN)
        range_edges = _fetch_edges(RDFS_RANGE)
        all_predicate_edges = {}
        cursor.execute(
            "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE p NOT IN (?, ?, ?, ?, ?) LIMIT 50000",
            [RDFS_SUBCLASSOF, RDFS_SUBPROPOF, RDF_TYPE, RDFS_DOMAIN, RDFS_RANGE],
        )
        for s, p, o in cursor.fetchall():
            all_predicate_edges.setdefault(p, []).append((s, o))

        for p, domain in domain_edges:
            for s, _ in all_predicate_edges.get(p, []):
                inferred.add((s, RDF_TYPE, domain))

        for p, rng in range_edges:
            for _, o in all_predicate_edges.get(p, []):
                inferred.add((o, RDF_TYPE, rng))

        if rules == "owl":
            equiv_class = _fetch_edges(OWL_EQUIV_CLASS)
            for a, b in equiv_class:
                inferred.add((a, RDFS_SUBCLASSOF, b))
                inferred.add((b, RDFS_SUBCLASSOF, a))

            equiv_prop = _fetch_edges(OWL_EQUIV_PROP)
            for p, q in equiv_prop:
                inferred.add((p, RDFS_SUBPROPOF, q))
                inferred.add((q, RDFS_SUBPROPOF, p))

            inverse_edges = _fetch_edges(OWL_INVERSE)
            for p, q in inverse_edges:
                for x, y in all_predicate_edges.get(p, []):
                    inferred.add((y, q, x))
                for x, y in all_predicate_edges.get(q, []):
                    inferred.add((y, p, x))

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_TRANS_PROP],
            )
            trans_props = {r[0] for r in cursor.fetchall()}
            for tp in trans_props:
                tp_edges = _fetch_edges(tp)
                inferred |= {(a, tp, c) for a, c in _transitive_closure(tp_edges)}

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_SYM_PROP],
            )
            sym_props = {r[0] for r in cursor.fetchall()}
            for sp in sym_props:
                for x, y in all_predicate_edges.get(sp, []):
                    inferred.add((y, sp, x))

        _insert_inferred(inferred)
        return {"inferred": inferred_count}


    def retract_inference(self, graph: Optional[str] = None) -> int:
        _ledger_check(self, "retract_inference")
        cursor = self.conn.cursor()
        # `graph is None` and `graph == ""` both mean the default graph, never "every
        # graph" — the meaning spec 214 and 223 established and `cypher/translator.py`
        # already implements. Testing `if graph:` sent both forms down an unscoped
        # DELETE that removed every graph's inferred edges (spec 230, FR-001).
        # A cross-graph retraction is a different operation and has no spelling here.
        if graph is None or graph == "":
            cursor.execute(
                f"DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '{INFERRED_QUALIFIER_LIKE}' "
                "AND COALESCE(graph_id, '') = ''"
            )
        else:
            cursor.execute(
                f"DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '{INFERRED_QUALIFIER_LIKE}' "
                "AND graph_id = ?",
                [graph],
            )
        deleted = cursor.rowcount or 0
        try:
            self.conn.commit()
        except Exception:
            pass
        return deleted




    def reify_edge(
        self,
        edge_id: int,
        reifier_id: str = None,
        label: str = "Reification",
        props: Dict[str, str] = None,
    ) -> Optional[str]:
        _ledger_check(self, "reify_edge")
        if reifier_id is None:
            reifier_id = f"reif:{edge_id}"
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT edge_id FROM {self._t('rdf_edges')} WHERE edge_id = ?",
                [edge_id],
            )
            if not cursor.fetchone():
                logger.warning(f"reify_edge: edge_id={edge_id} not found")
                return None
            self.create_node(reifier_id)
            cursor.execute(
                f"INSERT INTO {self._t('rdf_labels')} (s, label) "
                f"SELECT ?, ? WHERE NOT EXISTS "
                f"(SELECT 1 FROM {self._t('rdf_labels')} WHERE s = ? AND label = ?)",
                [reifier_id, label, reifier_id, label],
            )
            cursor.execute(
                f"INSERT INTO {self._t('rdf_reifications')} (reifier_id, edge_id) VALUES (?, ?)",
                [reifier_id, edge_id],
            )
            if props:
                for key, val in props.items():
                    cursor.execute(
                        f'INSERT INTO {self._t("rdf_props")} (s, "key", val) '
                        f"SELECT ?, ?, ? WHERE NOT EXISTS "
                        f'(SELECT 1 FROM {self._t("rdf_props")} WHERE s = ? AND "key" = ?)',
                        [reifier_id, key, str(val), reifier_id, key],
                    )
            self.conn.commit()
            return reifier_id
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"reify_edge({edge_id}) failed: {e}")
            return None
        finally:
            cursor.close()


    def get_reifications(self, edge_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f'SELECT r.reifier_id, p."key", p.val '
                f"FROM {self._t('rdf_reifications')} r "
                f"LEFT JOIN {self._t('rdf_props')} p ON p.s = r.reifier_id "
                f"WHERE r.edge_id = ?",
                [edge_id],
            )
            rows = cursor.fetchall()
            result: Dict[str, dict] = {}
            for reifier_id, key, val in rows:
                if reifier_id not in result:
                    result[reifier_id] = {"reifier_id": reifier_id, "properties": {}}
                if key is not None:
                    result[reifier_id]["properties"][key] = val
            return list(result.values())
        except Exception as e:
            logger.warning(f"get_reifications({edge_id}) failed: {e}")
            return []
        finally:
            cursor.close()


    def delete_reification(self, reifier_id: str) -> bool:
        _ledger_check(self, "delete_reification")
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {self._t('rdf_reifications')} WHERE reifier_id = ?",
                [reifier_id],
            )
            cursor.execute(
                f"DELETE FROM {self._t('rdf_props')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {self._t('rdf_labels')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {self._t('nodes')} WHERE node_id = ?", [reifier_id]
            )
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"delete_reification({reifier_id}) failed: {e}")
            return False
        finally:
            cursor.close()
