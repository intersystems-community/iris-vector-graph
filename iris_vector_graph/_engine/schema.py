import logging
import re
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
from iris_vector_graph._validate import validate_graph_name
# The record of created routines (4.6.0 R133), shared with the Arno bridge's wrappers since R153.
from iris_vector_graph.routine_record import (  # noqa: F401 - re-exported for callers of 4.6.0's names
    ROUTINES_MARKER,
    RoutineRecord,
    declared_routine_name,
    routine_fingerprint,
    routine_name,
)
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
from iris_vector_graph.prop_values import prop_text
from iris_vector_graph._engine.ledger import ledger_check as _ledger_check
from iris_vector_graph._validate import graph_index_key

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

    The second driver is asked only when the first has no ``Error``: ``intersystems_iris``
    re-exports ``iris``, and importing it asks ``iris`` for names (``IRISNative``,
    ``context``...) that the embedded-Python wrapper answers outside IRIS with a MagicMock
    and a WARNING each (4.6.0, NR18).
    """
    for module_name in ("iris.dbapi", "intersystems_iris.dbapi"):
        try:
            module = __import__(module_name, fromlist=["Error"])
        except Exception:
            continue
        base = getattr(module, "Error", None)
        if isinstance(base, type) and issubclass(base, Exception):
            return (base,)
    return ()


def _one_line(error, limit: int = 300) -> str:
    """A server error on one line (IRIS's span several: SQLCODE, Location, %msg), at most ``limit`` characters."""
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _setup_summary(namespace: str, schema: str, deploy, status: Dict[str, Any], dim: int,
                   arno: Optional[bool] = None) -> str:
    """The one INFO line ``initialize_schema()`` ends with: where, what was deployed, what was skipped and why, and
    whether the optional Arno library answers (``arno``; None when it was not asked, R207)."""
    if not status["objectscript_deployed"]:
        classes = "ObjectScript classes not deployed"
    elif deploy is None:
        classes = "ObjectScript classes already compiled"
    else:
        classes = f"{len(deploy.compiled)} classes deployed" + (" (unchanged)" if deploy.unchanged else "")
    skipped = status["objectscript_skipped"]
    if skipped:
        classes += f"; {len(skipped)} skipped: " + ", ".join(f"{n} ({r})" for n, r in sorted(skipped.items()))
    # 4.6.0 R200: a function or procedure setup could not create (kg_RRF_FUSE on 2026.1 was one) is named here, with
    # the server's error, and in status().missing_routines; it was a debug line.
    failed = (status.get("routines") or {}).get("failed") or {}
    if failed:
        noun = "routine" if len(failed) == 1 else "routines"
        classes += f"; {len(failed)} {noun} not created: " + ", ".join(
            f"{n} ({e})" for n, e in sorted(failed.items())
        )
    line = f"IVG ready in namespace {namespace}, IVG Schema {schema}: {classes}; embedding dimension {dim}"
    if arno is not None:
        line += ("; Arno library loaded" if arno else
                 "; Arno library not loaded (optional): algorithms run in ObjectScript")
    return line


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


#: Spec 262 T038: called after setup and before the record is written; tests set it to interrupt an upgrade there.
_UPGRADE_HOOK = None


class SchemaMixin:
    """Schema management and graph initialization mixin for IRISGraphEngine.
    
    Provides schema creation, status checking, graph building, and inference."""

    def is_ready(self) -> bool:
        """True when the IVG Schema's ``nodes`` table can be queried, False on any error.

        Reads only; a missing table, a missing schema and a dead connection all return False.
        """
        try:
            cur = self.conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {self._ivg_schema}.nodes")
            cur.fetchone()
            return True
        except Exception:
            return False


    def get_labels(self) -> List[str]:
        """Every distinct node label in ``rdf_labels``, sorted, across all graphs."""
        cur = self.conn.cursor()
        cur.execute(f"SELECT DISTINCT label FROM {self._ivg_schema}.rdf_labels ORDER BY label")
        return [r[0] for r in cur.fetchall()]


    def get_relationship_types(self) -> List[str]:
        """Every distinct edge predicate in ``rdf_edges``, sorted, across all graphs."""
        cur = self.conn.cursor()
        cur.execute(f"SELECT DISTINCT p FROM {self._ivg_schema}.rdf_edges ORDER BY p")
        return [r[0] for r in cur.fetchall()]


    def get_node_count(self, label: str = None, graph: Optional[str] = None) -> int:
        """The number of nodes in ``graph``, or of those with ``label``; omitted (or None, ``""``), the default graph.

        Before 4.6.0 this counted every graph (R249, as R219 did for ``node_count``). Raises ``ValueError`` for a
        graph name that cannot be one.
        """
        gid = validate_graph_name(graph)
        cur = self.conn.cursor()
        if label:
            cur.execute(
                f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_labels WHERE label = ? AND COALESCE(graph_id, '') = ?",
                [label, gid],
            )
        else:
            cur.execute(f"SELECT COUNT(*) FROM {self._ivg_schema}.nodes WHERE COALESCE(graph_id, '') = ?", [gid])
        return int(cur.fetchone()[0])


    def get_edge_count(self, predicate: str = None, graph: Optional[str] = None) -> int:
        """The number of edges in ``graph``, or of those with ``predicate``; omitted, the default graph (4.6.0 R249;
        before, every graph). A multigraph's parallel edges each count."""
        gid = validate_graph_name(graph)
        cur = self.conn.cursor()
        if predicate:
            cur.execute(
                f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE p = ? AND COALESCE(graph_id, '') = ?",
                [predicate, gid],
            )
        else:
            cur.execute(f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE COALESCE(graph_id, '') = ?", [gid])
        return int(cur.fetchone()[0])


    def get_label_distribution(self, graph: Optional[str] = None) -> Dict[str, int]:
        """Label to node count in ``graph``, largest first; omitted, the default graph (4.6.0 R249; before, every
        graph)."""
        gid = validate_graph_name(graph)
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT label, COUNT(*) AS cnt FROM {self._ivg_schema}.rdf_labels WHERE COALESCE(graph_id, '') = ? "
            "GROUP BY label ORDER BY cnt DESC",
            [gid],
        )
        return {r[0]: int(r[1]) for r in cur.fetchall()}


    def get_property_keys(self, label: str = None) -> List[str]:
        """Every distinct property key in ``rdf_props``, sorted; with ``label``, of nodes with it.

        Not graph-scoped.
        """
        cur = self.conn.cursor()
        if label:
            cur.execute(
                f'SELECT DISTINCT rp."key" FROM {self._ivg_schema}.rdf_props rp'
                f" JOIN {self._ivg_schema}.rdf_labels rl ON rl.s = rp.s"
                ' WHERE rl.label = ? ORDER BY rp."key"',
                [label],
            )
        else:
            cur.execute(f'SELECT DISTINCT "key" FROM {self._ivg_schema}.rdf_props ORDER BY "key"')
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
            f"SELECT COUNT(*) FROM {self._ivg_schema}.nodes"
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

    def _ensure_fhir_graph_columns(self, cursor) -> bool:
        """Add ``fhir_graphs.interp_version`` (spec 235) where a pre-235 schema lacks it.
        No backfill: NULL is stale, so the next sync re-derives the graph once.

        True when the column exists afterwards; False when there is no
        ``fhir_graphs`` table or the ALTER fails, which is logged, not raised.
        ``Graph.KG.FHIRGraph`` adds the same column itself when it is missing."""
        table = f"{self._ivg_schema}.fhir_graphs"
        try:
            cursor.execute(f"SELECT interp_version FROM {table} WHERE 1 = 0")
            return True
        except Exception:
            pass
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN interp_version INTEGER")
            self.conn.commit()
            return True
        except Exception as e:
            err = str(e).lower()
            if "already" in err or "duplicate" in err:
                return True
            if "not found" not in err:
                logger.warning("Could not add %s.interp_version: %s", table, e)
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

    def _raise_on_missing_privilege(self, cursor, exc: BaseException, nothing_created: bool = False) -> None:
        """Raise :class:`IVGSchemaError` naming the privilege when a setup statement met a privilege violation
        (SQLCODE -99); return for any other error, which the caller handles as before.

        Setup stops at the first one: a user without %CREATE_TABLE meets it at the first table, before anything
        exists, where carrying on would log one warning per statement and report a setup that created nothing.
        """
        from iris_vector_graph.schema_name import IVGSchemaError, missing_privilege

        privilege = missing_privilege(exc)
        if privilege is None:
            return
        user = "(unknown)"
        try:
            cursor.execute("SELECT $USERNAME")
            row = cursor.fetchone()
            user = str(row[0]) if row else user
        except Exception as probe:  # the error raised below names the privilege either way
            logger.debug("SELECT $USERNAME failed after a privilege violation: %s", probe)
        raise IVGSchemaError.no_privilege(user, self._ivg_schema, privilege, nothing_created) from exc

    def _install_procedures(self, cursor, force: bool = False) -> Dict[str, Any]:
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

        4.6.0 R133: a routine whose fingerprint (:func:`routine_fingerprint`, its DDL text and the IVG Schema) is the
        one recorded in :data:`ROUTINES_MARKER` and that ``INFORMATION_SCHEMA.ROUTINES`` still lists is not created
        again: ``CREATE OR REPLACE`` recompiles the routine's class and discards the cached queries that call it.
        ``force`` (the migration, and ``IVG_FORCE_CLASS_DEPLOY=1``) creates every one. Without the Native API there is
        no record, and every routine is created, as before. Returns ``{"created": [names], "unchanged": count,
        "failed": {name: error}}``: ``failed`` holds each optional routine whose CREATE failed, as the statement names
        it, with the server's error (4.6.0 R200; each is also a WARNING, the setup summary's last clause, and an entry
        of ``status().missing_routines``).
        """
        import os

        schema = getattr(self, "_schema_prefix", "Graph_KG")
        force = force or os.environ.get("IVG_FORCE_CLASS_DEPLOY", "") == "1"
        deferred = self._embeddings_await_migration(cursor)
        procedure_errors: List[Any] = []
        # No Native API: no globals to keep the record in, so every routine is created and nothing recorded.
        routines = RoutineRecord(self._native_api_or_none("routine fingerprints not kept"), cursor, schema, force=force)
        created: List[str] = []
        failed: Dict[str, str] = {}  # routine (as declared) -> the server's error, one line
        unchanged = 0
        # No width is passed: `get_procedures_sql_list` ignores it and, since 3.2.0,
        # deprecates it (ADR-0005 — the query vector is converted unlengthed on purpose).
        # Passing a width here would fire that warning on every initialize_schema, on a
        # path no caller can fix, which teaches people to filter the warning out.
        for stmt in GraphSchema.get_procedures_sql_list(table_schema=schema):
            if not stmt.strip():
                continue
            if routines.unchanged(stmt):
                unchanged += 1
                continue
            routines.forget(stmt)
            is_core = f"procedure {schema.lower()}.kg_knn_vec" in stmt.lower()
            if is_core and deferred:
                logger.warning(
                    f"kg_KNN_VEC deferred: {self._ivg_schema}.kg_NodeEmbeddings is still keyed "
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
                self._raise_on_missing_privilege(cursor, e)
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
                        failed[declared_routine_name(stmt) or stmt[:80]] = _one_line(e)
                        logger.warning(
                            "kg_KNN_VEC was not created in %s (non-fatal): %s",
                            schema,
                            _one_line(e),
                        )
                    else:
                        procedure_errors.append((stmt[:80], e))
                        logger.error("Procedure DDL failed: %s | Error: %s", stmt[:80], e)
                else:
                    # 4.6.0 R200: this was logger.debug, and kg_RRF_FUSE was absent on 2026.1 with nothing said
                    name = declared_routine_name(stmt) or stmt[:80]
                    failed[name] = _one_line(e)
                    logger.warning(
                        "%s was not created in %s; setup goes on without it: %s", name, schema, failed[name]
                    )
                continue
            name = routines.created(stmt)
            if name:
                created.append(name)

        routines.save()

        if procedure_errors:
            raise RuntimeError(
                f"initialize_schema() failed to install {len(procedure_errors)} "
                f"stored procedure(s). Server-side vector search will be unavailable. "
                f"First error: {procedure_errors[0][1]}"
            )
        return {"created": sorted(created), "unchanged": unchanged, "failed": failed}

    def _ledger_index_builder(self):
        """A call that builds ``idx_ledger_correlation`` if the ledger has rows and the index none (4.6.0 R150), on the
        schema's own ledger class (``IVGALT.LedgerRevision`` for ``IVGALT``); None without the Native API."""
        from iris_vector_graph.schema_name import schema_package

        iris_obj = self._native_api_or_none("idx_ledger_correlation build not checked")
        if iris_obj is None:
            return None
        cls = f"{schema_package(getattr(self, '_schema_prefix', 'Graph_KG'))}.LedgerRevision"

        def build():
            status = iris_obj.classMethodValue(cls, "BuildCorrelationIndex")
            self._raise_unless_ok(iris_obj, status, f"{cls}.BuildCorrelationIndex")

        return build

    def _native_api_or_none(self, skipped: str):
        """The connection's Native API object, or None when it has none; ``skipped`` names, in the debug log, what is
        then not done."""
        try:
            return self._iris_obj()
        except TypeError as exc:  # the driver refuses a connection that is not its own
            logger.debug("%s: no Native API on this connection (%s)", skipped, exc)
            return None

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
            dimension=int(dimension) if dimension not in (None, "") else None,
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

    @staticmethod
    def _vector_column_action(cursor, table: str, dim: int):
        """``(action, db_dim, rows)`` for one vector column at ``dim``: ``"unchanged"``, ``"alter"`` (an untyped column,
        or an empty one at another width) or ``"manual"`` (a column at another width whose table has rows, or whose
        rows could not be counted). ``rows`` is None where it was not counted. Read-only; shared by setup and its dry
        run (4.6.0 R207)."""
        db_dim = GraphSchema.get_embedding_dimension(cursor, table)
        if db_dim == dim:
            return "unchanged", db_dim, None
        if db_dim is None:
            return "alter", None, None
        rows = None
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            rows = int(row[0]) if row else None
        except Exception as e:
            logger.debug("Could not count %s: %s", table, e)
        return ("alter" if rows == 0 else "manual"), db_dim, rows

    def _plan_vector_dimensions(self, cursor, dim: int) -> Dict[str, Any]:
        """What ``_migrate_vector_dimensions`` would do at ``dim``, read-only, for ``initialize_schema(dry_run=True)``
        (4.6.0 R207, P6): ``configured``, ``alter``, ``unchanged`` and ``needs_manual_migration`` (a populated column at
        another width, which setup leaves alone). A table that does not exist yet is not listed: setup creates it at
        ``dim``."""
        from iris_vector_graph.migrations.kg_node_stores import _table_exists

        plan: Dict[str, Any] = {"configured": dim, "alter": [], "unchanged": [], "needs_manual_migration": []}
        bucket = {"unchanged": "unchanged", "alter": "alter", "manual": "needs_manual_migration"}
        for name in VECTOR_TABLES:
            if not _table_exists(cursor, self._schema_prefix, name):
                continue  # setup creates it
            table = self._t(name)
            action, _, _ = self._vector_column_action(cursor, table, dim)
            plan[bucket[action]].append(table)
        return plan

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
            action, db_dim, row_count = self._vector_column_action(cursor, table, dim)

            if action == "unchanged":
                report["unchanged"].append(table)
                continue
            if action == "manual":
                logger.error(
                    "CRITICAL: %s.emb is VECTOR(DOUBLE, %d) but the engine is configured "
                    "for %d, and the table is not empty (%s rows). Every write of the "
                    "configured width will be rejected. Drop and recreate the table, or "
                    "re-embed at %d.",
                    table, db_dim, dim, row_count, db_dim,
                )
                report["needs_manual_migration"].append(table)
                continue
            if db_dim is not None:
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
                        dimension=int(dimension) if dimension not in (None, "") else None,
                        dtype=dtype or None,
                        row_count=self._route_row_count(
                            cursor, table_name, graph_id, known_tables
                        ),
                        index_name=index_name,
                        index_state=index_state,
                        index_error=index_error,
                        recall_measured=float(recall) if recall not in (None, "") else None,
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
                    dimension=int(dimension) if dimension not in (None, "") else None,
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
            dimension=int(row_dimension) if row_dimension not in (None, "") else None,
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

    def initialize_schema(self, auto_deploy_objectscript: bool = True, dry_run: bool = False) -> dict:
        """Create IVG's tables, functions and classes in the IVG Schema and return a status dict.

        Safe to rerun on an installed namespace. Raises ``ValueError`` when the engine has no
        ``embedding_dimension``; ``auto_deploy_objectscript=False`` skips deploying the classes.

        Spec 262: setup's DDL and catalog probes are written for `Graph_KG`; on another IVG Schema they run through a
        connection that rewrites them for it (`schema_name.in_schema`), the way the class sources are. After setup the
        direct grants on the 4.5.x objects in SQLUser are copied to their replacements in the IVG Schema (FR-010a), the
        schema is recorded in `^IVG.Deploy("schema")`, and the 4.5.x objects left in place are logged once (FR-010).

        Which schema is recorded follows data-model.md: a record that names another schema than this engine's
        raises `IVGSchemaError` (FR-006); with no record, a namespace whose IVG tables are in this engine's schema
        alone is recorded as it ("inferred"), one with none as "new", and one whose IVG tables are in another schema,
        or several, raises listing them and their row counts unless the schema was named (``schema_prefix=`` or
        `IVG_SCHEMA_PREFIX`), when it is recorded ("explicit") and the others are reported, untouched (FR-009).

        Every setup then gives each grantee holding EXECUTE on one routine of the Cypher read path, or SELECT on
        ``rdf_props``, EXECUTE on all of them (``legacy.read_path_routines``: the functions whose bodies read nothing
        outside the caller's SQL privileges, ``CY_VLP_*`` included since R280), ``read_path_grants`` listing each grant
        made and why (4.6.0 R170, R278): 4.6.0's translation calls routines 4.5.x had not, and a routine a later
        release adds reaches the same grantees.

        ``dry_run=True`` writes nothing (no DDL, row, global or grant) and reports what a run would do: the keys
        ``schema``, ``schema_recorded``, ``legacy_objects``, ``grants_copied``,
        ``read_path_grants``, ``other_schemas``,
        ``ledger_history_copied``, ``dry_run``, and ``embedding_dimension``: the engine's width and the vector tables
        a run would alter to it, leave unchanged, or cannot change because they hold rows at another width
        (``needs_manual_migration``; 4.6.0 R207). On a schema other than ``Graph_KG`` whose ledger tables are empty,
        setup copies the 4.5.x ledger rows from ``Graph_KG.ledger_revisions`` and ``ledger_stats`` into them
        (``ledger_history_copied``: rows per table; 4.6.0 R114).
        """
        with self._schema_check_suspended():
            status = self._record_and_initialize(auto_deploy_objectscript, dry_run)
        if not dry_run:
            self._schema_check_passed()
        return status

    def _schema_check_suspended(self):
        """The engine's first-access check (engine.py) does not apply to setup, which is how a namespace that refuses
        an engine is put right. A mixin user without the engine's check gets a no-op."""
        import contextlib

        return contextlib.nullcontext()

    def _schema_check_passed(self) -> None:
        return None

    def _ivg_table_schemas(self) -> Dict[str, Dict[str, int]]:
        """Every schema in the namespace holding IVG's tables (base tables `nodes` and `rdf_edges` both, so the
        4.5.x `SQLUser` views do not count), with each table's row count (FR-009)."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME IN ('nodes', 'rdf_edges')"
            )
            names: Dict[str, set] = {}
            for s, t in cur.fetchall():
                names.setdefault(str(s), set()).add(str(t))
            out: Dict[str, Dict[str, int]] = {}
            for schema in sorted(names):
                if names[schema] != {"nodes", "rdf_edges"} or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", schema):
                    continue
                counts = {}
                for table in ("nodes", "rdf_edges"):
                    cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                    counts[table] = int(cur.fetchone()[0])
                out[schema] = counts
            return out
        finally:
            cur.close()

    def _record_and_initialize(self, auto_deploy_objectscript: bool, dry_run: bool) -> dict:
        """The record transitions of data-model.md, then setup (``_initialize_schema``) unless ``dry_run``."""
        from iris_vector_graph import __version__
        from iris_vector_graph._engine import legacy
        from iris_vector_graph.schema_name import (
            IVGSchemaError,
            in_schema,
            native_namespace,
            read_record,
            refuse_package_clash,
            same_schema,
            write_record,
        )

        schema = getattr(self, "_schema_prefix", "Graph_KG")
        try:
            iris_obj = self._iris_obj()
        except TypeError as exc:
            # The driver refuses anything but its own connection (a stand-in object, a foreign DB-API wrapper): there
            # is no Native API, so no record to read or write and no grants to read. Said in the status, not hidden.
            iris_obj = None
            native_missing = f"schema record and 4.5.x grants skipped: no Native API on this connection ({exc})"
        else:
            native_missing = None
        recorded = read_record(iris_obj) if iris_obj is not None else None
        # recorded(S), engine T != S: refused, nothing written (FR-006). First, before anything else is asked or run:
        # an engine with no dimension on such a namespace was told about the dimension instead (4.6.0 R173).
        if recorded is not None and not same_schema(recorded, schema):
            raise IVGSchemaError.mismatch(recorded, schema, native_namespace(iris_obj))
        if not dry_run and getattr(self, "embedding_dimension", None) is None:
            return self._initialize_schema(auto_deploy_objectscript)  # raises, naming the missing dimension
        # No Native API: no record to read or write, so nothing to infer either (the status says so, below)
        held = {} if recorded is not None or iris_obj is None else self._ivg_table_schemas()
        others = {s: c for s, c in held.items() if not same_schema(s, schema)}
        if recorded is not None:
            how = "existing"
        elif not held:
            how = "new"
        elif not others:
            how = "inferred"  # IVG's tables in exactly this one schema
        elif getattr(self, "_schema_explicit", False):
            how = "explicit"  # named by the caller; the other schemas' tables are reported and left alone
        else:
            # IVG's tables in another schema (or several), and nobody named one: record nothing (FR-009)
            raise IVGSchemaError.unrecorded_schemas(held, schema, native_namespace(iris_obj))
        refuse_package_clash(self.conn, schema)
        found = legacy.present_legacy_objects(self.conn, iris_obj) if iris_obj is not None else []
        if dry_run:
            dim = getattr(self, "embedding_dimension", None)
            widths = None
            if dim:
                cursor = self.conn.cursor()
                try:
                    widths = self._plan_vector_dimensions(cursor, dim)
                finally:
                    cursor.close()
            return {
                "schema": recorded or schema, "schema_recorded": how, "legacy_objects": len(found),
                "grants_copied": legacy.copy_grants(self.conn, found, schema, dry_run=True), "dry_run": True,
                "read_path_grants": (legacy.grant_read_path(self.conn, iris_obj, schema, dry_run=True, pending=found)
                                     if iris_obj is not None else []),
                "other_schemas": others,
                "ledger_history_copied": legacy.copy_ledger_history(self.conn, schema, dry_run=True),
                "embedding_dimension": widths,
            }

        conn = self.conn
        self.conn = in_schema(conn, schema)
        try:
            status = self._initialize_schema(auto_deploy_objectscript)
        finally:
            self.conn = conn
        # Test seam (T038): an upgrade interrupted here, with the IVG Schema's functions created and nothing recorded,
        # must end where an uninterrupted one does on the next run.
        if _UPGRADE_HOOK is not None:
            _UPGRADE_HOOK()
        status["grants_copied"] = legacy.copy_grants(conn, found, schema, dry_run=False)
        # 4.6.0 R170: and every routine of the read path to a grantee holding one, on every setup (a routine added by a
        # later release included), so a user who could run Cypher before the upgrade can run it after
        status["read_path_grants"] = (legacy.grant_read_path(conn, iris_obj, schema, dry_run=False)
                                      if iris_obj is not None else [])
        status["ledger_history_copied"] = legacy.copy_ledger_history(conn, schema, dry_run=False)
        if native_missing:
            status["warnings"].append(native_missing)
        elif not recorded:
            write_record(iris_obj, schema, __version__)
        conn.commit()
        left = [f"IVG tables in schema {s} ({c['nodes']} nodes, {c['rdf_edges']} edges) are not this namespace's IVG "
                f"Schema ({schema}) and were left untouched" for s, c in others.items()]
        for line in left:
            logger.warning("IVG setup: %s", line)
        status["warnings"].extend(left)
        status.update({"schema": recorded or schema, "schema_recorded": how, "legacy_objects": len(found),
                       "dry_run": False, "other_schemas": others})
        if found:
            legacy.log_leftovers(len(found), native_namespace(iris_obj))
        return status

    def _initialize_schema(self, auto_deploy_objectscript: bool = True) -> dict:
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

        # No CREATE SCHEMA: the first CREATE TABLE creates the IVG Schema, and CREATE SCHEMA needs no privilege, so run
        # first it left an empty schema behind for a user who could create nothing in it (spec 262, T028). A
        # privilege violation stops setup at the statement that met it, naming the privilege.
        cursor = self.conn.cursor()

        sql = GraphSchema.get_base_schema_sql(embedding_dimension=dim)
        # Asked once, before the script runs: `docs` gains `graph_id` in the 4.0.0
        # upgrade's `docs` step, so on a 3.2.0 install the index below cannot be created
        # yet. Probing after the failure would be probing a table this script may have
        # created in the meantime.
        docs_awaits_graph = self._docs_awaits_graph_column(cursor)
        ran_one = False  # a statement succeeded: past this point a stop leaves objects behind
        for stmt in _split_sql_statements(sql):
            if not stmt.strip():
                continue
            if docs_awaits_graph and "idx_docs_graph" in stmt.lower():
                logger.warning(
                    f"idx_docs_graph deferred: {self._ivg_schema}.docs has no graph_id column on "
                    "this install, so there is nothing to index yet. Run "
                    "iris_vector_graph.migrations.upgrade_to_4_0_0(conn) — its `docs` "
                    "step places every document in a graph and creates this index when "
                    "it is done. Nothing else is affected."
                )
                continue
            try:
                cursor.execute(stmt)
                ran_one = True
            except Exception as e:
                self._raise_on_missing_privilege(cursor, e, nothing_created=not ran_one)
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
        # ensure_indexes may just have created the rdf_props value-key column and index (spec: lookup by value).
        self._props_value_key_cached = None
        self._adjacency_indexed_cached = None
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

        # 3b-iii. spec 235: the FHIR graph interpretation marker on pre-235 registries.
        self._ensure_fhir_graph_columns(cursor)

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

        # 5. Install stored procedures (an unchanged one is not created again: R133)
        routines = self._install_procedures(cursor)

        self.conn.commit()

        # 5b. Spec 262 (FR-008): no views in SQLUser; 4.5.x created four there for unqualified reads; every IVG query
        # now names its table in the IVG Schema.

        # 6. Deploy ObjectScript .cls layer (best-effort)
        if auto_deploy_objectscript:
            try:
                pkg_dir = Path(__file__).parent.parent / "iris_src"
                if not pkg_dir.exists():
                    pkg_dir = Path(__file__).parent / ".." / "iris_src"
                self.capabilities = GraphSchema.deploy_objectscript_classes(
                    cursor, pkg_dir.resolve(), conn=self.conn, schema=getattr(self, "_schema_prefix", "Graph_KG")
                )
            except RdfEdgesRescueError:
                # Deploying is best-effort; rescuing a class-owned `rdf_edges` is not.
                # Swallowing this one leaves the namespace with no edge table while
                # `initialize_schema` reports success.
                raise
            except Exception as exc:
                logger.warning(
                    "ObjectScript auto-deploy failed: %s",
                    exc,
                )
                self.capabilities = IRISCapabilities()
        else:
            self.capabilities = IRISCapabilities()
            # The deploy is what normally restores a rescued `rdf_edges`, and with
            # `staged=None` it resumes one an earlier run left unfinished. Skipping the
            # deploy must not skip that: a rescue table left behind is the only copy of
            # the edges, next to an `rdf_edges` the base DDL above just created empty.
            # Raises RdfEdgesRescueError, like the deploy path.
            GraphSchema.restore_rescued_rdf_edges(cursor, None)

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

        # 6b-ii. Spec 252: ensure_indexes (step 3) ran before the deploy compiled Graph.KG.AdjIndex /
        # LabelIndex, so on a fresh namespace it skipped the adjacency index. Declare it now; "present"
        # on every later run.
        adjacency_index: Dict[str, Any] = {}
        arno_loaded: Optional[bool] = None
        if self.capabilities.objectscript_deployed:
            adjacency_index = GraphSchema.ensure_adjacency_index(cursor)
            self._adjacency_indexed_cached = None
            self._native_executor_cached = None  # the deploy may just have brought Exec to PLAN_VERSION
            self._ekey_guard_cached = None  # spec 234's migration may just have added rdf_edges.ekey
            # Arno was asked about at construction, before the deploy compiled its classes: ask again (NR6)
            self._arno_available = None
            store = getattr(self, "_store", None)
            if store is not None:
                if hasattr(store, "_arno_available"):
                    store._arno_available = None
                self._store_capabilities = store.capabilities()
                arno_loaded = getattr(store, "_arno_available", None)

        # 6b-iii. R137: the ledger table is a class's, compiled by the deploy above; its index is asked for here, after it.
        ledger_warning = None
        try:
            ledger_index = GraphSchema.ensure_ledger_index(cursor, build=self._ledger_index_builder())
        except Exception as exc:
            self._raise_on_missing_privilege(cursor, exc)
            ledger_index = f"not created: {exc}"
            ledger_warning = (
                f"idx_ledger_correlation not created on ledger_revisions ({exc}); ledger lookups by "
                "correlation id scan the table until a later initialize_schema() creates it"
            )

        if self.capabilities.objectscript_deployed and not self.capabilities.kg_built:
            try:
                built = GraphSchema.bootstrap_kg_global(cursor, conn=self.conn, schema=self._ivg_schema)
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
            "adjacency_index": adjacency_index,
            "ledger_index": ledger_index,
            "routines": routines,
            "embedding_dimension": dim,
            "needs_manual_migration": needs_manual_migration,
            "warnings": [],
        }
        if ledger_warning:
            status["warnings"].append(ledger_warning)
        for table in needs_manual_migration:
            status["warnings"].append(
                f"{table} holds rows at a vector width other than the configured "
                f"{dim}, and a populated column cannot be widened — every write of "
                "the configured width will be rejected (SQLCODE -104). Re-embed the "
                "table at the configured width, or clear it and re-run "
                "initialize_schema()."
            )
        deploy = GraphSchema.last_deploy if auto_deploy_objectscript else None
        # A class the server cannot compile (no %AI, no embedded Python) is not a fault: the summary below names it.
        status["objectscript_skipped"] = dict(deploy.skipped) if deploy else {}
        if deploy is not None:
            for err in deploy.errors:
                status["warnings"].append(f"ObjectScript deploy error: {err}")
        if not self.capabilities.objectscript_deployed:
            status["warnings"].append(
                "ObjectScript classes not deployed — BFS, Subgraph, PageRank using Python "
                "fallbacks. initialize_schema() sends the classes packaged with IVG over this "
                "connection; see the log for why that failed. Set IVG_FORCE_CLASS_DEPLOY=1 "
                "to reinstall an unchanged set."
            )
        # The declared adjacency index keeps ^KG current from the rows (spec 252): an empty namespace has none yet
        adjacency_declared = adjacency_index.get("idx_edges_adj") in ("created", "present")
        if not self.capabilities.kg_built and not adjacency_declared:
            status["warnings"].append(
                "^KG adjacency index not built — multi-hop BFS unavailable. "
                "Call engine.rebuild_kg() after loading data."
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

        logger.info("%s", _setup_summary(self.namespace, self._schema_prefix, deploy, status, dim,
                                         arno=arno_loaded))
        return status


    def get_schema_visualization(self) -> dict:
        """A schema diagram as ``{"nodes": [...], "relationships": [...]}``: one node per label, one
        relationship per predicate, read from ``rdf_labels``, ``rdf_props`` and ``rdf_edges``.

        Sampled, not exact: a label's properties are the first 20 keys of one node carrying it,
        and a predicate's start and end labels are those of one edge's endpoints (0 if none).
        Across all graphs; reads only.
        """
        cursor = self.conn.cursor()

        cursor.execute(f"SELECT DISTINCT label FROM {self._ivg_schema}.rdf_labels ORDER BY label")
        labels = [r[0] for r in cursor.fetchall()]

        cursor.execute(f"SELECT DISTINCT p FROM {self._ivg_schema}.rdf_edges ORDER BY p")
        rel_types = [r[0] for r in cursor.fetchall()]

        nodes = []
        for i, label in enumerate(labels):
            cursor.execute(
                f"SELECT TOP 1 rl.s FROM {self._ivg_schema}.rdf_labels rl WHERE rl.label = ?",
                [label],
            )
            row = cursor.fetchone()
            sample_id = row[0] if row else None

            prop_names = []
            if sample_id:
                cursor.execute(
                    f'SELECT DISTINCT TOP 20 "key" FROM {self._ivg_schema}.rdf_props WHERE s = ? '
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
                f"SELECT TOP 1 s, o_id FROM {self._ivg_schema}.rdf_edges WHERE p = ?", [rel_type]
            )
            row = cursor.fetchone()
            start_label_id = 0
            end_label_id = 0
            if row:
                src_id, tgt_id = row
                cursor.execute(
                    f"SELECT TOP 1 label FROM {self._ivg_schema}.rdf_labels WHERE s = ?", [src_id]
                )
                src_row = cursor.fetchone()
                if src_row:
                    start_label_id = label_to_id.get(src_row[0], 0)
                cursor.execute(
                    f"SELECT TOP 1 label FROM {self._ivg_schema}.rdf_labels WHERE s = ?", [tgt_id]
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
        """Rebuild ``^KG`` from the tables, then ``^NKG`` and its two-hop statistics, from ``^KG``.

        Idempotent. ^NKG is rewritten in place by the ObjectScript builder. Clears the pending-sync flag when both
        rebuilds succeed.

        Returns:
            True when both rebuilds succeeded.

        Raises:
            RuntimeError: a rebuild failed, naming the server method and its error (4.6.0 R135: it used to answer
                ``False`` and log a warning, and a rebuild whose ``%Status`` reported an error answered ``True``).
        """
        self._sync_kg()
        self._sync_nkg()
        self._nkg_dirty = False
        return True


    @staticmethod
    def _raise_unless_ok(iris_obj, status, method: str) -> None:
        """Raise ``RuntimeError`` naming ``method`` and the server's error text when ``status`` is not ``$$$OK``."""
        if str(status).strip() == "1":
            return
        text = iris_obj.classMethodValue("%SYSTEM.Status", "GetErrorText", status)
        raise RuntimeError(f"{method} failed: {text}")


    def _sync_kg(self) -> bool:
        """Rebuild ``^KG`` from the tables (``Graph.KG.Traversal.BuildKG``); True, or raises (R135)."""
        iris_obj = self._iris_obj()
        status = iris_obj.classMethodValue("Graph.KG.Traversal", "BuildKG")
        self._raise_unless_ok(iris_obj, status, "Graph.KG.Traversal.BuildKG")
        self.capabilities.kg_built = True
        self._nkg_dirty = True
        logger.info("^KG adjacency index rebuilt successfully")
        return True


    def _sync_nkg(self) -> bool:
        """Rebuild ``^NKG`` and its two-hop statistics from ``^KG``; True, or raises (R135)."""
        # ^NKG has one writer, Graph.KG.TraversalBuild:BuildNKG in ObjectScript (spec 260
        # D-NKG-WRITER). It rewrites ^NKG in place, so readers never see it empty, and
        # invalidates the cached adjacency itself. The Rust branch that used to come first
        # (the arno callout's builder, through NKGAccel) could never succeed, and had it
        # run it would have built an ^NKG without named graphs or sketches.
        iris_obj = self._iris_obj()
        status = iris_obj.classMethodValue("Graph.KG.Traversal", "BuildNKG")
        self._raise_unless_ok(iris_obj, status, "Graph.KG.Traversal.BuildNKG")
        iris_obj.classMethodValue("Graph.KG.Traversal", "Build2HopStats")
        self._nkg_dirty = False
        return True


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
        """Deprecated no-op: out-degree is no longer stored, so there is no per-predicate counter to backfill.

        ``Graph.KG.Adj.Degree`` counts the ``^KG("out")`` entries behind a generation-validated cache, so
        a graph loaded by ``BulkIngestEdges`` already answers exactly. Returns 0 and calls no server method.
        """
        import warnings

        warnings.warn(
            "backfill_degp() does nothing: degp is no longer stored; degree comes from the adjacency "
            "(Graph.KG.Adj.Degree). Remove the call.",
            DeprecationWarning,
            stacklevel=2,
        )
        return 0


    def backfill_deg2p_exact(self) -> int:
        """Recompute the exact two-hop reach counts in ``^KG("out")``, per graph, node, predicate.

        Calls ``Graph.KG.Traversal.Build2HopExactStats``, which rewrites ``^KG("deg2p_exact")``, and
        returns the number of entries it wrote; 0 when the call fails (logged as a warning).
        """
        try:
            result = self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopExactStats")
            return int(result)
        except Exception as e:
            logger.warning("backfill_deg2p_exact failed: %s", e)
            return 0


    def materialize_inference(
        self, rules: str = "rdfs", graph: Optional[str] = None
    ) -> Dict[str, int]:
        """Derive RDFS (``rules="owl"``: also OWL) entailments in one graph; write them as edges.

        Reads ``rdf_edges`` in ``graph`` (None or ``""`` is the default graph) and adds the missing
        ``subClassOf`` and ``subPropertyOf`` closures, ``rdf:type`` from subclass, domain and range,
        and for ``"owl"`` equivalence, inverse, transitive and symmetric properties. Each new edge
        carries the qualifier ``{"inferred":true}`` and is indexed in ``^KG``. Returns
        ``{"inferred": n}``, the number of edges written; ``retract_inference`` removes them.
        """
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
                f"SELECT s, o_id FROM {self._ivg_schema}.rdf_edges WHERE p = ? "
                f"AND (qualifiers IS NULL OR qualifiers NOT LIKE '{INFERRED_QUALIFIER_LIKE}')"
                + graph_filter_sql,
                [predicate] + graph_filter_params,
            )
            return set((r[0], r[1]) for r in cursor.fetchall())

        def _exists(s, p, o):
            if graph:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id=?",
                    [s, p, o, graph],
                )
            else:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE s=? AND p=? AND o_id=? "
                    "AND COALESCE(graph_id, '') = ''",
                    [s, p, o],
                )
            row = cursor.fetchone()
            return row is not None and int(row[0]) > 0

        # import_rdf runs BuildKG before inference, so an inferred row that only
        # reaches rdf_edges is invisible to every ^KG walker (concept expansion,
        # BFS, PPR). Each one is indexed as create_edge indexes it (spec 233 FR-015).
        graph_key = graph_index_key(graph)

        def _insert_inferred(triples):
            nonlocal inferred_count
            # One transaction: an INSERT IRIS refuses (a reader's SQLCODE -99, a lock timeout) rolls back the edges
            # this call wrote and raises. It was `except Exception: continue`, and a refused call answered
            # {"inferred": 0}, the answer for nothing to infer (4.6.0 R245). A duplicate (-119) is still skipped.
            from iris_vector_graph._write_txn import is_duplicate, rollback
            from iris_vector_graph.errors import GraphWriteError

            cursor.execute("START TRANSACTION")
            adjacency_written = 0
            for s, p, o in sorted(triples):
                if not _exists(s, p, o):
                    try:
                        if graph:
                            cursor.execute(
                                f"INSERT INTO {self._ivg_schema}.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON, graph],
                            )
                        else:
                            cursor.execute(
                                f"INSERT INTO {self._ivg_schema}.rdf_edges (s, p, o_id, qualifiers, graph_id) "
                                "VALUES (?, ?, ?, ?, '')",
                                [s, p, o, INFERRED_JSON],
                            )
                        inferred_count += 1
                    except Exception as e:
                        if is_duplicate(e):
                            continue
                        rollback(cursor)
                        inferred_count = 0
                        left = (
                            f"the {adjacency_written} ^KG entries written for the rolled-back edges remain "
                            "(rebuild_kg() removes them)" if adjacency_written else ""
                        )
                        raise GraphWriteError("materialize_inference", e, left) from e
                    if self._adjacency_indexed:
                        continue  # the row wrote ^KG through the adjacency index (spec 252)
                    try:
                        self._iris_obj().classMethodVoid(
                            "Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", graph_key
                        )
                        adjacency_written += 1
                    except Exception as e:
                        logger.warning(f"materialize_inference ^KG write failed (BuildKG can recover): {e}")
            cursor.execute("COMMIT")

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
        # Scoped to the target graph and unbounded: a LIMIT with no graph predicate
        # ran one graph's domain/range rules over the first 50,000 edges of any graph
        # (spec 233 FR-015).
        cursor.execute(
            f"SELECT s, p, o_id FROM {self._ivg_schema}.rdf_edges WHERE p NOT IN (?, ?, ?, ?, ?)" + graph_filter_sql,
            [RDFS_SUBCLASSOF, RDFS_SUBPROPOF, RDF_TYPE, RDFS_DOMAIN, RDFS_RANGE] + graph_filter_params,
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
                f"SELECT s FROM {self._ivg_schema}.rdf_edges WHERE p=? AND o_id=?" + graph_filter_sql,
                [RDF_TYPE, OWL_TRANS_PROP] + graph_filter_params,
            )
            trans_props = {r[0] for r in cursor.fetchall()}
            for tp in trans_props:
                tp_edges = _fetch_edges(tp)
                inferred |= {(a, tp, c) for a, c in _transitive_closure(tp_edges)}

            cursor.execute(
                f"SELECT s FROM {self._ivg_schema}.rdf_edges WHERE p=? AND o_id=?" + graph_filter_sql,
                [RDF_TYPE, OWL_SYM_PROP] + graph_filter_params,
            )
            sym_props = {r[0] for r in cursor.fetchall()}
            for sp in sym_props:
                for x, y in all_predicate_edges.get(sp, []):
                    inferred.add((y, sp, x))

        _insert_inferred(inferred)
        return {"inferred": inferred_count}


    def retract_inference(self, graph: Optional[str] = None) -> int:
        """Delete the edges ``materialize_inference`` wrote in one graph, and their ``^KG`` entries.

        ``graph`` None or ``""`` is the default graph, never every graph. An asserted edge for the
        same triple keeps its adjacency entry. Returns the number of ``rdf_edges`` rows deleted.
        """
        _ledger_check(self, "retract_inference")
        cursor = self.conn.cursor()
        # `graph is None` and `graph == ""` both mean the default graph, never "every
        # graph" — the meaning spec 214 and 223 established and `cypher/translator.py`
        # already implements. Testing `if graph:` sent both forms down an unscoped
        # DELETE that removed every graph's inferred edges (spec 230, FR-001).
        # A cross-graph retraction is a different operation and has no spelling here.
        if graph is None or graph == "":
            scope_sql, scope_params = "COALESCE(graph_id, '') = ''", []
        else:
            scope_sql, scope_params = "graph_id = ?", [graph]
        # materialize_inference indexes what it writes, so retraction takes the same
        # edges back out of ^KG (spec 233 FR-015).
        cursor.execute(
            f"SELECT s, p, o_id FROM {self._ivg_schema}.rdf_edges WHERE qualifiers LIKE '{INFERRED_QUALIFIER_LIKE}' "
            f"AND {scope_sql}",
            scope_params,
        )
        retracted = [tuple(r) for r in cursor.fetchall()]
        cursor.execute(
            f"DELETE FROM {self._ivg_schema}.rdf_edges WHERE qualifiers LIKE '{INFERRED_QUALIFIER_LIKE}' "
            f"AND {scope_sql}",
            scope_params,
        )
        deleted = cursor.rowcount or 0
        try:
            self.conn.commit()
        except Exception as e:
            # It was passed over (4.6.0 R276), and the count returned for rows still there.
            from iris_vector_graph.errors import GraphWriteError

            raise GraphWriteError("retract_inference", e) from e
        graph_key = graph_index_key(graph)
        if self._adjacency_indexed:
            return deleted  # the row deletes removed ^KG through the adjacency index (spec 252)
        for s, p, o in retracted:
            # An asserted row for the same triple keeps its adjacency entry.
            cursor.execute(
                f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE s=? AND p=? AND o_id=? AND {scope_sql}",
                [s, p, o] + scope_params,
            )
            row = cursor.fetchone()
            if row is not None and int(row[0]) > 0:
                continue
            try:
                self._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", s, p, o, graph_key)
            except Exception as e:
                logger.warning(f"retract_inference ^KG kill failed (BuildKG can recover): {e}")
        return deleted




    def reify_edge(
        self,
        edge_id: int,
        reifier_id: str = None,
        label: str = "Reification",
        props: Dict[str, str] = None,
    ) -> Optional[str]:
        """Attach a reifier node to the ``rdf_edges`` row ``edge_id``, to hold edge properties.

        Creates the node ``reifier_id`` (default ``reif:<edge_id>``) in the default graph, labels it
        ``label``, records the link in ``rdf_reifications`` and sets each of ``props`` the node does
        not already have. Returns the reifier ID, or None when the edge does not exist.

        A write IRIS refuses raises ``GraphWriteError`` and leaves nothing behind: the reifier node,
        which ``create_node`` commits on its own, is deleted again when this call created it (before
        4.6.0 a refusal answered None, like a missing edge).
        """
        from iris_vector_graph.errors import GraphWriteError

        _ledger_check(self, "reify_edge")
        if reifier_id is None:
            reifier_id = f"reif:{edge_id}"
        cursor = self.conn.cursor()
        created = False
        try:
            cursor.execute(
                f"SELECT edge_id FROM {self._t('rdf_edges')} WHERE edge_id = ?",
                [edge_id],
            )
            if not cursor.fetchone():
                logger.warning(f"reify_edge: edge_id={edge_id} not found")
                return None
            created = self.create_node(reifier_id)
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
                        [reifier_id, key, prop_text(val), reifier_id, key],
                    )
            self.conn.commit()
            return reifier_id
        except GraphWriteError as e:
            # create_node's, which rolled its node back: this call wrote nothing
            raise GraphWriteError("reify_edge", e.__cause__ or e) from e
        except Exception as e:
            self.conn.rollback()
            if created:
                # Not swallowed: a reifier node left behind is reported in the error this raises.
                try:
                    self.delete_node(reifier_id, graph=None)
                except Exception as cleanup:
                    raise GraphWriteError(
                        "reify_edge", e,
                        left=f"its node {reifier_id!r} remains (deleting it again failed: {cleanup})") from e
            raise GraphWriteError("reify_edge", e) from e
        finally:
            cursor.close()


    def get_reifications(self, edge_id: int) -> List[Dict[str, Any]]:
        """The reifiers of ``rdf_edges`` row ``edge_id``, as ``reifier_id``/``properties`` dicts.

        Reads only. Returns an empty list when there are none, or when the query fails (logged).
        """
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
        """Delete a reifier: its ``rdf_reifications`` link, properties, labels and node row.

        The reified edge stays. Not graph-scoped: every row for ``reifier_id`` goes. Returns True,
        also when there was none. A delete IRIS refuses raises ``GraphWriteError`` after the
        transaction is rolled back, so nothing is deleted (before 4.6.0 it answered False).
        """
        from iris_vector_graph.errors import GraphWriteError

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
            raise GraphWriteError("delete_reification", e) from e
        finally:
            cursor.close()
