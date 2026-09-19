import json
import logging
from typing import Dict, Any, Optional, List

from iris_vector_graph.schema import GraphSchema, _call_classmethod
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.constants import VECTOR_TABLE_NAMES
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
    mechanism       VARCHAR(32),
    model_key       VARCHAR(256),
    declared_config VARCHAR(512),
    dimension       INTEGER,
    dtype           VARCHAR(16)   NOT NULL DEFAULT 'DOUBLE',
    set_at          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    set_by          VARCHAR(128),
    CONSTRAINT pk_embedding_registry PRIMARY KEY (table_name, graph_id)
)"""


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


    def node_exists(self, node_id: str) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?",
            [node_id],
        )
        row = cur.fetchone()
        return row is not None and int(row[0]) > 0

    # ----------------------------------------------------------------- embedding identity

    def _registry_table(self) -> str:
        return self._t(EMBEDDING_REGISTRY_TABLE)

    def _ensure_embedding_registry(self, cursor) -> bool:
        """Create ``embedding_registry`` if absent. True when it exists afterwards."""
        try:
            cursor.execute(_EMBEDDING_REGISTRY_DDL.format(table=self._registry_table()))
            self.conn.commit()
            return True
        except Exception as e:
            err = str(e).lower()
            if "already exists" in err or "already has a" in err:
                return True
            logger.warning(
                "Could not create %s: %s — embedding identity will not be enforced",
                self._registry_table(),
                e,
            )
            return False

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
        if table_name not in VECTOR_TABLE_NAMES:
            raise ValueError(
                f"table_name {table_name!r} is not an embedding table. Expected one of "
                f"{VECTOR_TABLE_NAMES}."
            )
        if graph_id != "":
            raise ValueError(
                f"graph_id must be '' in 3.2.0, got {graph_id!r}. The column exists so "
                f"per-graph identity can be added without a migration; nothing reads it "
                f"as anything other than 'all graphs' yet (FR-003)."
            )
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
                    )

            reason = conflicts(recorded, offered)
            if reason:
                raise EmbeddingIdentityConflict(table_name, recorded, offered, reason)
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
            raise EmbeddingIdentityConflict(table_name, recorded, offered, reason)
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
    ) -> None:
        cursor.execute(
            f"INSERT INTO {self._registry_table()} (table_name, graph_id, mechanism, "
            "model_key, declared_config, dimension, dtype, set_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                table_name,
                graph_id,
                identity.mechanism,
                identity.model_key,
                identity.declared_config,
                identity.dimension,
                identity.dtype,
                set_by,
            ],
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
            # The registry's width follows the column it describes (spec 226). Without
            # this, the engine that just altered the column would be refused by its own
            # recorded width on the very next write.
            for name in altered_names:
                self._sync_recorded_dimension(name, dim)

        return report

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
        for stmt in _split_sql_statements(sql):
            if not stmt.strip():
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

        # 3b. Embedding identity registry (spec 226). Created before anything alters a
        # vector column, so the recorded expectation exists before the width can move.
        self._ensure_embedding_registry(cursor)

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
        try:
            self._migrate_vector_dimensions(cursor, dim)
        except _ALTER_TOLERATED_ERRORS as e:
            logger.warning("Could not verify embedding dimension: %s", e)

        # 4b. Record what this engine says produces the vectors. An engine that declares
        # no model records nothing.
        self._record_configured_embedding_identity(cursor)

        # 5. Install stored procedures
        procedure_errors = []
        # No width is passed: `get_procedures_sql_list` ignores it and, since 3.2.0,
        # deprecates it (ADR-0005 — the query vector is converted unlengthed on purpose).
        # Passing `dim` here would fire that warning on every initialize_schema, on a path
        # no caller can fix, which teaches people to filter the warning out.
        for stmt in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
            if not stmt.strip():
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already has" in err:
                    continue  # idempotent re-run — schema or procedure already installed
                # Only kg_KNN_VEC is required for server-side vector search;
                # kg_TXT and kg_RRF_FUSE are optional (depend on full-text search feature)
                is_core = "procedure graph_kg.kg_knn_vec" in stmt.lower()
                if is_core:
                    _sqlcode = ""
                    import re as _re_proc
                    m = _re_proc.search(r"sqlcode.*?<(-?\d+)>", err)
                    if m:
                        _sqlcode = m.group(1)
                    if _sqlcode == "-260":
                        logger.debug(
                            "kg_KNN_VEC skipped: vector dimension mismatch in kg_NodeEmbeddings "
                            "(table has mixed-dim vectors from tests). Non-fatal. | Error: %s", e
                        )
                    else:
                        procedure_errors.append((stmt[:80], e))
                        logger.error(
                            "Procedure DDL failed: %s | Error: %s", stmt[:80], e
                        )
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

        status = {
            "tables_created": True,
            "objectscript_deployed": self.capabilities.objectscript_deployed,
            "kg_built": self.capabilities.kg_built,
            "embedding_dimension": dim,
            "warnings": [],
        }
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
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ?", [rel_type]
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
        INFERRED_JSON = '{"inferred":true}'

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
                "AND (qualifiers IS NULL OR qualifiers NOT LIKE '%\"inferred\"%')"
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
        if graph:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '%\"inferred\":\"true\"%' AND graph_id = ?",
                [graph],
            )
        else:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '%\"inferred\":\"true\"%'"
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
