from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import json
import logging
import threading
import time

from iris_vector_graph.constants import DEFAULT_GRAPH, VECTOR_TABLE_NAMES
from iris_vector_graph.embedding_identity import (
    EmbeddingIdentity,
    identity_from_config,
    normalize_model_key,
)
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import (
    edge_route_table_name,
    graph_scope_predicate,
    route_table_name,
)
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph._engine.schema import (
    ROUTE_KIND_EDGE,
    ROUTE_KIND_NODE,
    ROUTE_KINDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingRoute:
    """Where one ``(graph, model)`` pair's vectors physically live (spec 227, FR-011).

    Produced only by :meth:`EmbeddingsMixin.resolve_route`, and only from a registry
    row — never from :func:`~iris_vector_graph.routing.route_table_name`. The hash says
    what a *new* route would be called; it says nothing about what an existing one is
    called, and ``kg_NodeEmbeddings`` is a route under a name no hash produces (FR-015).

    ``dimension`` is the width the route's column declares, which is the width IRIS
    enforces at INSERT. ``index_state`` is what was actually recorded about the ANN
    index — ``None`` means nobody has looked, not "no index".

    ``kind`` says what the route's rows are keyed on: ``node`` for ``(graph_id,
    node_id)``, ``edge`` for ``(graph_id, s, p, o_id)`` (spec 230, FR-006). It defaults
    to ``node`` because that is what every route written before 4.0.0 is, and because a
    caller that never asked about edges should never have to name it.
    """

    graph_id: str
    model_key: Optional[str]
    table_name: str
    dimension: Optional[int]
    dtype: str = "DOUBLE"
    index_state: Optional[str] = None
    index_error: Optional[str] = None
    kind: str = ROUTE_KIND_NODE

    @property
    def is_indexed(self) -> bool:
        """Does this route have an ANN index? A refused one reads as False (FR-019)."""
        return self.index_state == "present"


#: One lock per route, created on demand, keyed on
#: ``(registry table, graph, model, kind)``.
#:
#: Per route and not one global lock: a namespace at the measured ceiling of 100 routes
#: (FR-042) would otherwise serialise every first write in it behind every other. The
#: dict only ever grows, which is bounded by the number of routes an engine touches.
#:
#: This arbitrates *within* a process. Across processes the registry primary key
#: ``(table_name, graph_id)`` does it, and the loser's INSERT raising is the signal to
#: re-read rather than to fail.
_ROUTE_LOCKS: Dict[Tuple[str, str, Optional[str], str], threading.Lock] = {}
_ROUTE_LOCKS_GUARD = threading.Lock()


def _route_lock(key: Tuple[str, str, Optional[str], str]) -> threading.Lock:
    with _ROUTE_LOCKS_GUARD:
        lock = _ROUTE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ROUTE_LOCKS[key] = lock
        return lock


def _route_preference(table_name: str) -> Tuple[int, int]:
    """Sort key deciding which row wins when two answer one pair (FR-015).

    A 3.2.0 install has an adopted row for ``kg_NodeEmbeddings`` *and* one for
    ``kg_NodeEmbeddings_optimized``, both at ``(graph_id='', model_key=NULL)``. Both
    genuinely answer the default pair, so the choice has to be deterministic and it has
    to be the table the data is in — which is the first of ``VECTOR_TABLE_NAMES``.
    """
    try:
        return (0, VECTOR_TABLE_NAMES.index(table_name))
    except ValueError:
        return (1, 0)


class EmbeddingsMixin:
    """Embedding and vector storage mixin for IRISGraphEngine.
    
    Provides text embedding, node/edge embedding, vector search integration,
    and asynchronous embedding queue management."""

    def embed_text(self, text: str) -> List[float]:
        """
        Converts text to a vector embedding using the best available method.
        Order of preference:
        1. Native IRIS EMBEDDING() if embedding_config is set.
        2. Configured Python embedder.
        3. Default SentenceTransformer fallback.
        """
        # 1. Native IRIS embedding if available
        if self.embedding_config and self._probe_embedding_support():
            cursor = self.conn.cursor()
            try:
                # Call SQL EMBEDDING function
                cursor.execute("SELECT EMBEDDING(?, ?)", [text, self.embedding_config])
                result = cursor.fetchone()
                if result:
                    # IRIS returns vector as string or list depending on driver version
                    val = result[0]
                    if isinstance(val, str):
                        return [float(x) for x in val.strip("[]").split(",")]
                    return list(val)
            except Exception as e:
                logger.warning(
                    f"Native IRIS EMBEDDING failed for config '{self.embedding_config}': {e}. Falling back to Python."
                )
            finally:
                cursor.close()

        # 2. Python-side embedding
        if not self.embedder:
            try:
                import logging as _logging
                try:
                    import transformers as _tf
                    _tf.logging.set_verbosity_error()
                    _logging.getLogger("safetensors").setLevel(_logging.ERROR)
                except Exception:
                    pass
                # Local import avoids a circular import (engine.py imports this
                # mixin at module load); function-local matches the pattern used
                # elsewhere in this file (embed_selector, get_schema_prefix).
                from iris_vector_graph.engine import _load_sentence_transformer, _is_sentence_transformer
                self.embedder = _load_sentence_transformer("all-MiniLM-L6-v2")
                logger.info("Auto-initialized SentenceTransformer('all-MiniLM-L6-v2')")
            except ImportError:
                raise RuntimeError(
                    "No embedder or embedding_config configured, and 'sentence-transformers' not installed. "
                    "Pass an embedder/embedding_config to IRISGraphEngine or install sentence-transformers."
                )

        if hasattr(self.embedder, "encode"):
            return self.embedder.encode(text).tolist()
        if hasattr(self.embedder, "embed"):
            return self.embedder.embed(text)
        if callable(self.embedder):
            return self.embedder(text)

        raise TypeError(
            f"Configured embedder {type(self.embedder)} is not a supported type (must have encode/embed or be callable)"
        )


    def _get_embedding_dimension(self) -> int:
        """
        Get the vector embedding dimension, either from initialization or auto-detection.
        Prioritizes the already-known instance value to avoid a per-call class-dictionary
        query — querying %Dictionary.CompiledProperty on every write contends for the
        class's Class-Changed_Timestamp and can raise SQLCODE -150 (optimistic concurrency
        locking failure) under concurrent writers.
        """
        # 1. Fast path: caller already provided it, or a prior DB detection cached it here.
        if self.embedding_dimension is not None:
            return self.embedding_dimension

        # 2. Fall back to DB detection only when the dimension is truly unknown.
        cursor = self.conn.cursor()
        dim = GraphSchema.get_embedding_dimension(cursor)
        if dim:
            self.embedding_dimension = int(dim)
            return self.embedding_dimension

        raise ValueError(
            "Embedding dimension could not be determined. Please provide it during IRISGraphEngine initialization."
        )


    # ---------------------------------------------------------------- routing

    def resolve_route(
        self,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
        *,
        create: bool = False,
        dimension: Optional[int] = None,
        dtype: Optional[str] = None,
        kind: str = ROUTE_KIND_NODE,
    ) -> Optional[EmbeddingRoute]:
        """Where this ``(graph, model)`` pair's vectors live, or ``None`` (spec 227, FR-011).

        Reads the registry. A pair with no row resolves to nothing — not to the default
        graph's route, not to another model's, and not to an empty table. A substituted
        route returns real vectors from the wrong space, which scores and ranks and looks
        like an answer, so the miss is reported instead (FR-013).

        ``create=True`` makes the route: a ``CREATE TABLE`` at the declared width, an
        attempted ANN index, and a registry row naming the model. Two creators of one
        route produce one table and one row; the loser returns the winner's route,
        including the winner's declared width, because that is the width its next write
        will be measured against.

        ``model_key=None`` does not mean "any model". It is resolved through the engine's
        own configuration exactly as a write would be, so an engine configured with
        ``embedding_config`` resolves to the same route whether or not the caller repeats
        the name — and an engine that declares nothing resolves to spec 226's undeclared
        identity, which is its own route rather than a wildcard.

        ``dimension`` and ``dtype`` are beyond contracts/python-api.md §3's signature:
        creating a route has to declare a width, and the engine's configured dimension is
        the wrong answer when the caller is writing a second model's vectors. They are
        ignored unless a route is created.

        A registry that cannot be read yields ``None`` even under ``create=True``. The
        alternative — creating from the hash — is the failure FR-011 exists to prevent: a
        second table for a route that already exists, and one graph's vectors split
        across both.

        ``kind`` selects which of the pair's two routes is meant (spec 230, FR-006). One
        ``(graph, model)`` pair can have a node route and an edge route at once, and they
        are different tables with different keys, so a lookup that ignored ``kind`` would
        answer with whichever row the registry happened to return first.
        """
        if kind not in ROUTE_KINDS:
            raise ValueError(f"kind must be one of {ROUTE_KINDS}, got {kind!r}")
        graph_id = DEFAULT_GRAPH if graph is None else graph
        key = self._offered_embedding_identity(config=model_key).model_key

        readable, route = self._read_route(graph_id, key, kind=kind)
        if not readable:
            return None
        if route is not None or not create:
            return route

        lock = _route_lock((self._registry_table(), graph_id, key, kind))
        with lock:
            # Re-read inside the lock: another thread may have created it while we
            # waited, and its row is the authority on the name and the width.
            readable, route = self._read_route(graph_id, key, kind=kind)
            if not readable:
                return None
            if route is not None:
                return route
            return self._create_route(
                graph_id, key, dimension=dimension, dtype=dtype, kind=kind
            )

    #: How long a resolved route may be served without re-reading the registry.
    #:
    #: The cache exists for SC-010: measured on the live container, a per-search registry
    #: round trip put `kg_KNN_VEC` at 1.159× the 3.2.0 statement on the same 300 rows,
    #: against a 1.10× budget — a single-graph installation paying to re-learn a route
    #: that never changes. The TTL is what keeps it honest for the other case: this cache
    #: is per engine, and another process can drop a route, so a remembered route stops
    #: being the last word shortly after it stops being true. Set to 0 to disable.
    _ROUTE_CACHE_TTL_SECONDS = 30.0

    def _route_cache_key(
        self, graph_id: str, model_key: Optional[str], kind: str = ROUTE_KIND_NODE
    ) -> tuple:
        """Keyed on the registry table too: one engine can be re-pointed at another
        schema, and a route name means nothing outside the registry that issued it.

        ``kind`` is part of the key because one pair has two routes (spec 230, FR-006);
        without it, caching the node route would serve its table to an edge read."""
        return (self._registry_table(), graph_id or "", model_key, kind)

    def _cached_route(self, key: tuple) -> Optional[EmbeddingRoute]:
        ttl = getattr(self, "_route_cache_ttl_seconds", self._ROUTE_CACHE_TTL_SECONDS)
        if not ttl:
            return None
        entry = getattr(self, "_route_cache_store", {}).get(key)
        if entry is None:
            return None
        route, stored_at = entry
        if (time.monotonic() - stored_at) > ttl:
            self._route_cache_store.pop(key, None)
            return None
        return route

    def _cache_route(self, key: tuple, route: EmbeddingRoute) -> None:
        store = getattr(self, "_route_cache_store", None)
        if store is None:
            store = {}
            self._route_cache_store = store
        store[key] = (route, time.monotonic())

    def invalidate_route_cache(
        self,
        graph_id: Optional[str] = None,
        model_key: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> None:
        """Forget cached routes — one pair's, one graph's, or all of them.

        Anything that changes a registry row or drops a routed table has to call this:
        the alternative is an engine answering from a row that no longer exists, which
        reads as a missing table rather than as a stale cache.

        ``kind=None`` drops both of a pair's routes, which is the safe default: a caller
        that has just altered a registry row usually knows the graph and not whether the
        pair also has a route of the other kind (spec 230, FR-006).
        """
        store = getattr(self, "_route_cache_store", None)
        if not store:
            return
        if graph_id is None:
            store.clear()
            return
        graph = graph_id or ""
        for key in [
            k
            for k in list(store)
            if k[1] == graph
            and (model_key is None or k[2] == model_key)
            and (kind is None or (k[3] if len(k) > 3 else ROUTE_KIND_NODE) == kind)
        ]:
            store.pop(key, None)

    def _read_route(
        self,
        graph_id: str,
        model_key: Optional[str],
        kind: str = ROUTE_KIND_NODE,
    ) -> Tuple[bool, Optional[EmbeddingRoute]]:
        """``(readable, route)`` for one pair. ``readable=False`` means the read failed.

        The two are distinguished because they lead to opposite actions: no row means
        create one, while a failed read means do nothing at all.

        A row that was read recently is served from :meth:`_cached_route`. Only rows are
        cached: a miss is not, because the write that follows it creates the route, and
        an unreadable registry is not, because remembering one failed read would leave
        the whole session unrouted.
        """
        cache_key = self._route_cache_key(graph_id, model_key, kind)
        cached = self._cached_route(cache_key)
        if cached is not None:
            return True, cached

        table = self._registry_table()
        # `COALESCE(kind, 'node')` rather than `kind = ?`: every row written before 4.0.0
        # predates the column and holds NULL there, and all of them are node routes
        # (spec 230, FR-006). An equality test would hide 3.2.0's own adopted rows from
        # the node lookup that has to find them.
        sql = (
            "SELECT table_name, dimension, dtype, index_state, index_error "
            f"FROM {table} WHERE {graph_scope_predicate('graph_id')} "
            "AND COALESCE(kind, 'node') = ? AND "
        )
        params: List[Any] = [graph_id, kind]
        if model_key is None:
            # `model_key = NULL` is never true in SQL, and the undeclared identity is a
            # real route with a real row (spec 226, FR-007).
            sql += "model_key IS NULL"
        else:
            sql += "model_key = ?"
            params.append(model_key)

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            # Materialized before close: the driver's DataRow is a live view on the cursor.
            rows = [tuple(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.debug(
                "Embedding registry not readable for route (%r, %r): %s",
                graph_id,
                model_key,
                e,
            )
            return False, None
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        if not rows:
            return True, None

        # Five columns were asked for, so fewer than five means the answer came from
        # something other than a 227 registry — a table that predates `index_state`,
        # or a layer re-serving an earlier result set. Reading a table name out of it
        # would route writes at whatever sat in column one; not routing is the only
        # reading of a short answer that cannot be wrong.
        rows = [row for row in rows if len(row) >= 5]
        if not rows:
            logger.warning(
                "The embedding registry answered the route query for (%r, %r) with rows "
                "that are not route rows; treating this database as unrouted.",
                graph_id,
                model_key,
            )
            return False, None

        rows.sort(key=lambda row: _route_preference(row[0]))
        table_name, recorded_dim, recorded_dtype, index_state, index_error = rows[0][:5]
        route = EmbeddingRoute(
            graph_id=graph_id,
            model_key=model_key,
            table_name=table_name,
            dimension=int(recorded_dim) if recorded_dim is not None else None,
            dtype=(recorded_dtype or "DOUBLE"),
            index_state=index_state or None,
            index_error=index_error or None,
            kind=kind,
        )
        self._cache_route(cache_key, route)
        return True, route

    def _create_route(
        self,
        graph_id: str,
        model_key: Optional[str],
        *,
        dimension: Optional[int] = None,
        dtype: Optional[str] = None,
        kind: str = ROUTE_KIND_NODE,
    ) -> Optional[EmbeddingRoute]:
        """Create the table, attempt the index, record the row. Called under the lock.

        In that order, because each step is the evidence for the next: a row that names
        a table nothing created is a route that fails at its first write, and an index
        state recorded before the attempt is a claim nobody checked.

        ``kind`` decides both the name and the columns, together (spec 230, FR-006): an
        edge route is ``kg_eemb_…`` keyed ``(graph_id, s, p, o_id)``, a node route is
        ``kg_emb_…`` keyed ``(graph_id, node_id)``.
        """
        width = int(dimension) if dimension is not None else self._get_embedding_dimension()
        element = str(dtype or getattr(self, "vector_dtype", None) or "DOUBLE").upper()
        derive = edge_route_table_name if kind == ROUTE_KIND_EDGE else route_table_name
        table_name = derive(graph_id, model_key)

        cursor = self.conn.cursor()
        try:
            created = self._create_routed_table(
                cursor, table_name, dimension=width, dtype=element, kind=kind
            )
            if not created:
                width = self._reconcile_reused_route_width(
                    cursor, table_name, graph_id, model_key, width, element
                )
            index_state, index_error = self._attempt_route_index(cursor, table_name)
            identity = self._route_identity(model_key, width, element)
            try:
                self._insert_identity(
                    cursor, table_name, graph_id, identity, "routed", kind=kind
                )
            except Exception as e:
                # Another *process* got there first: the primary key refused the second
                # row. Its row stands, and its declared width is the one IRIS enforces.
                logger.debug("Route row for %s lost the race: %s", table_name, e)
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                readable, winner = self._read_route(graph_id, model_key, kind=kind)
                if winner is not None:
                    return winner
                raise
            self._record_route_index_state(
                cursor, table_name, graph_id, index_state, index_error
            )
            # Committed here, not left to the caller. `CREATE TABLE` above is DDL and is
            # durable the moment it runs, so a rollback after this point cannot take the
            # table with it — it takes only the row that names it, and the routed table
            # carries a FK on (graph_id, node_id) that the Eraser then cannot see. One
            # such orphan makes every erase in the namespace fail with SQLCODE -124 and
            # erase nothing. The row has to be as durable as its table.
            self.conn.commit()
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        route = EmbeddingRoute(
            graph_id=graph_id,
            model_key=model_key,
            table_name=table_name,
            dimension=width,
            dtype=element,
            index_state=index_state,
            index_error=index_error,
            kind=kind,
        )
        # Cached from what was just written rather than re-read: this is the one moment
        # the row's contents are known without asking. `_record_route_index_state` above
        # dropped the entry this replaces, so the order matters.
        self._cache_route(self._route_cache_key(graph_id, model_key, kind), route)
        return route

    def _route_identity(
        self, model_key: Optional[str], width: int, element: str
    ) -> EmbeddingIdentity:
        """The identity to record for a route on ``model_key``, at this width.

        The registry row is a claim about *how* the table's vectors were produced, and
        the only writer whose mechanism is knowable when a route is created is the one
        creating it. So when this engine's own configuration is what produced
        ``model_key``, the row records this engine's mechanism.

        Deriving the row from ``model_key`` alone reads every key as an IRIS
        ``embedding_config``, because that is what naming a config means. A worker with a
        local ``sentence-transformers`` embedder then created a route recorded as
        ``iris-embedding-config``, and the identity check on that same write refused it
        against the row it had just written — a local-embedder writer could not store a
        single vector into a new route.

        A ``model_key`` this engine did not produce is a model named for one piece of
        work (``enqueue_for_embedding(config=…)``, a queue entry's own ``"config"``
        subscript), and there no writer's mechanism is available to borrow: a named
        configuration is an IRIS embedding config, which is the fallback below.
        """
        own = self._offered_embedding_identity(dimension=width, dtype=element)
        if own.model_key == model_key:
            return own
        return self._offered_embedding_identity(
            dimension=width, dtype=element, config=model_key
        )

    def _reconcile_reused_route_width(
        self,
        cursor,
        table_name: str,
        graph_id: str,
        model_key: Optional[str],
        width: int,
        element: str,
    ) -> int:
        """The width to record for a routed table this call did **not** create.

        A routed table outliving its registry row is an ordinary state: ``CREATE TABLE``
        is DDL and durable the moment it runs, while the row that names it is an INSERT,
        so any rollback after the create — an interrupted writer, a fixture that clears
        the registry, the lost-race handler above — leaves the table behind. Creating the
        route then reuses it, which is right.

        What is not right is recording the width *this* caller asked for. IRIS enforces
        the declared VECTOR width at INSERT, so the surviving column is the width, and a
        row claiming another one hands the caller a route whose every write dies with a
        raw ``SQLCODE -104`` naming a hashed table. The column declaration is the truth
        about width (FR-006): a match is adopted, and a mismatch is refused here, before
        a row exists, with the identity conflict that says which widths and what to do.

        A column with no declared width proves nothing — that is the ``SQLCODE -260``
        shape, not evidence of a conflict — so the requested width stands.
        """
        live = GraphSchema.get_embedding_dimension(cursor, table_name)
        if not live or int(live) == int(width):
            return int(live) if live else int(width)

        recorded = self._route_identity(model_key, int(live), element)
        offered = self._route_identity(model_key, int(width), element)
        raise EmbeddingIdentityConflict(
            table_name,
            recorded,
            offered,
            f"the routed table already exists and its column declares "
            f"{int(live)}, but this writer declares {int(width)}. The column "
            f"declaration is what has to change: IRIS enforces a declared VECTOR "
            f"width at INSERT (SQLCODE -104), so recording the writer's width would "
            f"only move the failure to the first vector. Drop the table if it is "
            f"empty, or write this model at {int(live)}.",
            graph_id=graph_id,
        )

    #: The table every read and write used before routing existed, and the only honest
    #: answer when the registry cannot be read at all.
    _LEGACY_EMBEDDING_TABLE = "kg_NodeEmbeddings"

    #: The same, for edge vectors (spec 230, FR-006). `kg_EdgeEmbeddings` is where every
    #: edge vector written before 4.0.0 is, so an unrouted database's edge reads belong
    #: there and not in the node table — which has no ``(s, p, o_id)`` to answer with.
    _LEGACY_EDGE_EMBEDDING_TABLE = "kg_EdgeEmbeddings"

    def _legacy_table_for(self, kind: str) -> str:
        """The pre-routing table for one kind of vector."""
        return (
            self._LEGACY_EDGE_EMBEDDING_TABLE
            if kind == ROUTE_KIND_EDGE
            else self._LEGACY_EMBEDDING_TABLE
        )

    def _route_for_read(
        self,
        graph_id: str,
        model_key: Optional[str],
        kind: str = ROUTE_KIND_NODE,
    ) -> Tuple[Optional[str], Optional[EmbeddingRoute]]:
        """``(table to read, route)`` for one pair. ``(None, None)`` means read nothing.

        Three outcomes, and they are three because they need three different answers —
        which is why this consumes :meth:`_read_route`'s ``(readable, route)`` rather
        than ``resolve_route``'s single ``None``:

        - **a route**: read it.
        - **no row**: there is nowhere to read. Every substitute — the default graph's
          table, another model's, the legacy one — returns real vectors from the wrong
          space, and those score and rank and look like an answer (FR-013). The one
          exception is the default pair itself, below.
        - **an unreadable registry**: this database does not route, and is making no
          claim about routes. 3.2.0's table is still where its vectors are.
        """
        key = self._offered_embedding_identity(config=model_key).model_key
        legacy = self._legacy_table_for(kind)
        readable, route = self._read_route(graph_id, key, kind=kind)
        if not readable:
            return legacy, None
        if route is None:
            return (legacy if self._is_default_pair(graph_id, key) else None), None
        return route.table_name, route

    def _is_default_pair(self, graph_id: str, model_key: Optional[str]) -> bool:
        """Is this the pair 3.2.0 had — default graph, no model declared?

        That pair's vectors are in ``kg_NodeEmbeddings`` whether or not a registry row
        says so (FR-015), so a missing row for it is a missing row and not a missing
        table. A namespace built by running the DDL without ``initialize_schema``, or one
        whose row was deleted, would otherwise report its vectors as absent and write the
        next one into a hashed table beside them. No other pair gets this: a named graph
        or a declared model has no pre-227 home to inherit.
        """
        return (graph_id or "") == DEFAULT_GRAPH and model_key is None

    def _route_for_write(
        self,
        graph_id: str,
        model_key: Optional[str],
        *,
        dimension: int,
        dtype: str,
        kind: str = ROUTE_KIND_NODE,
    ) -> Tuple[str, Optional[EmbeddingRoute]]:
        """``(table to write, route)``. A write always has somewhere to go.

        Where a read of an unrouted pair returns nothing, a write to one makes the
        route: a table declared at ``dimension``, an attempted ANN index, and a registry
        row naming the model. The width comes from the vector in hand, not from the
        engine's configured dimension — the engine's is the wrong answer precisely when
        a second model is being written.

        The default pair is the exception, and keeps writing where 3.2.0 wrote: routing
        it elsewhere would put the next vector in a hashed table beside the ones already
        in ``kg_NodeEmbeddings`` (FR-015).
        """
        key = self._offered_embedding_identity(config=model_key).model_key
        legacy = self._legacy_table_for(kind)
        readable, route = self._read_route(graph_id, key, kind=kind)
        if readable and route is None and self._is_default_pair(graph_id, key):
            return legacy, None
        if readable and route is None:
            route = self.resolve_route(
                graph_id,
                model_key,
                create=True,
                dimension=dimension,
                dtype=dtype,
                kind=kind,
            )
        if route is None:
            return legacy, None
        return route.table_name, route

    def _route_width(self, route: Optional[EmbeddingRoute], offered: int) -> int:
        """The width this write is measured against.

        A route's declared width, because that is the width IRIS enforces at INSERT
        (SQLCODE -104). Only a write with no route falls back to the engine-wide
        dimension: one engine now writes several widths, so its own is a default for
        creating a route and not a statement about every route in the namespace.
        """
        if route is not None and route.dimension is not None:
            return route.dimension
        try:
            return self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from input if auto-detection fails
            self.embedding_dimension = offered
            logger.warning(
                f"Embedding dimension auto-detection failed. Inferred dimension {offered} from input."
            )
            return offered

    def _diagnose_vector_write(self, exc: Exception, table: str, offered: int) -> None:
        """Re-raise a refused vector INSERT as a statement about the width, if it is one.

        IRIS refuses a wrong-width vector with ``Field 'Graph_KG.kg_NodeEmbeddings.emb'
        (value '3D41...@$vector') failed validation`` — a column, a hash, and neither of
        the two widths that disagree. The pre-checks above cannot catch it on their own:
        :meth:`_get_embedding_dimension` answers from the engine's configured dimension
        whenever it has one, because reading ``%Dictionary.CompiledProperty`` on every
        write contends for the class's Class-Changed_Timestamp and raises SQLCODE -150
        under concurrent writers. So an engine built with ``embedding_dimension=128``
        against a 768-wide column passes its own check and is refused by the database.

        One column read *after* a write has already failed costs nothing and keeps the
        fast path. Silent about anything else: a failure whose widths agree was refused
        for some other reason, and calling that a width problem is a second wrong answer
        on top of the first.
        """
        declared = None
        try:
            cursor = self.conn.cursor()
            declared = GraphSchema.get_embedding_dimension(cursor, self._t(table))
        except Exception:
            return
        if declared is None or int(declared) == int(offered):
            return
        raise ValueError(
            f"Embedding dimension mismatch: {self._t(table)}.emb declares "
            f"VECTOR({self.vector_dtype}, {declared}) and this write offered {offered}. "
            "The column declaration is the width IRIS enforces (FR-006): call "
            "initialize_schema() while the table is empty to change it, or store under "
            "a model_key of its own so the write is routed to a table of its own width."
        ) from exc

    def _probe_embedding_support(self) -> bool:
        """Probe whether the IRIS EMBEDDING() SQL function is available (IRIS 2024.3+).

        Result is cached per engine instance. Probe strategy:
        - Execute ``SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')``
        - If the error message contains 'not found' / 'does not exist' → function absent → False
        - Any other error (e.g. config missing) → function present → True
        - No error → True
        """
        if self._embedding_function_available is not None:
            return self._embedding_function_available
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')"
            )
            self._embedding_function_available = True
        except Exception as e:
            err = str(e).lower()
            if "unknown function" in err or "not a recognized" in err:
                self._embedding_function_available = False
            else:
                self._embedding_function_available = True
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return bool(self._embedding_function_available)


    def _probe_native_vec(self) -> bool:
        if self._native_vec_available is not None:
            return self._native_vec_available
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT TOP 1 VECTOR_COSINE(emb, TO_VECTOR('[0]', DOUBLE)) "
                f"FROM {self._t('kg_NodeEmbeddings')} WHERE 1=0"
            )
            self._native_vec_available = True
        except Exception as e:
            err = str(e).lower()
            self._native_vec_available = not (
                "unknown function" in err
                or "not a recognized" in err
                or "not found" in err
                or "no such" in err
            )
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return bool(self._native_vec_available)


    def get_unembedded_nodes(
        self, *, graph: Optional[str] = None, model_key: Optional[str] = None
    ) -> List[str]:
        """Nodes in one graph with no vector in that graph's route (spec 227).

        Both halves of the join are scoped. Without it, a node embedded in graph B
        counts as embedded in graph A and graph A's backfill skips it — an omission
        with no error, which is the hardest kind to notice.

        A pair with no route has no vectors at all, so every node in the graph is
        unembedded and there is nothing to join to. Joining `kg_NodeEmbeddings` instead
        would report graph A's nodes as done on the strength of the default graph's rows.
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        cursor = self.conn.cursor()
        try:
            if table is None:
                cursor.execute(
                    f"SELECT node_id FROM {self._t('nodes')}"
                    f" WHERE {graph_scope_predicate('graph_id')}",
                    [graph_id],
                )
            else:
                cursor.execute(
                    f"SELECT n.node_id FROM {self._t('nodes')} n "
                    f"LEFT JOIN {self._t(table)} e "
                    f"ON e.node_id = n.node_id AND {graph_scope_predicate('e.graph_id')} "
                    f"WHERE {graph_scope_predicate('n.graph_id')} AND e.node_id IS NULL",
                    [graph_id, graph_id],
                )
            return [row[0] for row in cursor.fetchall()]
        except Exception:
            return []


    def store_embedding(
        self,
        node_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        dtype: Optional[str] = None,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> bool:
        """Store one vector in one graph (spec 227).

        `graph` and `model_key` are keyword-only so that a 3.2.0 positional call
        cannot land a graph in `metadata` or `dtype`. `graph=None` means the default
        graph, written out explicitly rather than left to the column default: on an
        upgraded install the column arrived by `ADD COLUMN` and is nullable, and
        rows that agree with each other are worth more than rows that agree with
        whichever DDL happened to create the table.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        _dtype = (dtype or self.vector_dtype).upper()
        graph_id = DEFAULT_GRAPH if graph is None else graph
        self._assert_node_exists(node_id, graph=graph_id)

        # Resolve before enforcing, enforce before writing. Enforcing first would check
        # `kg_NodeEmbeddings`' row for a write that is going somewhere else, so a
        # wrong-width write would pass and be refused by IRIS at INSERT with SQLCODE
        # -104 — an error about a column, for a mistake about a model.
        table, route = self._route_for_write(
            graph_id, model_key, dimension=len(embedding), dtype=_dtype
        )

        # spec 226: before anything is written, and before the fallback below can infer a
        # width from the input and mutate self.embedding_dimension to it. The width offered
        # is the vector's own length — that is what IRIS would reject at INSERT, and what a
        # writer declaring no model is still held to.
        self.enforce_embedding_identity(
            table,
            dimension=len(embedding),
            dtype=_dtype,
            config=model_key,
            graph_id=route.graph_id if route is not None else "",
        )

        dim = self._route_width(route, len(embedding))
        if len(embedding) != dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
            )

        cursor = self.conn.cursor()
        emb_str = ",".join(str(x) for x in embedding)
        meta_json = json.dumps(metadata) if metadata else None

        try:
            # Scoped: an unscoped delete-then-insert means storing graph B's vector
            # for `patient:1` erases graph A's, which is exactly the collision
            # UNIQUE (graph_id, node_id) exists to permit.
            cursor.execute(
                f"DELETE FROM {self._t(table)}"
                f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
                [node_id, graph_id],
            )
        except Exception:
            pass
        try:
            cursor.execute(
                f"INSERT INTO {self._t(table)} (graph_id, node_id, emb, metadata)"
                f" VALUES (?, ?, TO_VECTOR('{emb_str}', {_dtype}), ?)",
                [graph_id, node_id, meta_json],
            )
        except Exception as e:
            self._diagnose_vector_write(e, table, len(embedding))
            raise
        self.conn.commit()
        return True


    def store_embeddings(
        self, items: List[Dict[str, Any]], dtype: Optional[str] = None, *,
        graph: Optional[str] = None, model_key: Optional[str] = None,
    ) -> bool:
        """Store a batch of vectors in one graph (spec 227).

        One graph for the whole batch, not one per item. A per-item graph would let
        a batch span routes, and a route is one physical table with one declared
        width — so a mixed batch could only be honoured by splitting it across
        tables, which loses the single transaction that makes a batch worth using.
        A per-item `graph` key, if present, is ignored rather than silently
        honoured for some rows.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        _dtype = (dtype or self.vector_dtype).upper()
        graph_id = DEFAULT_GRAPH if graph is None else graph
        if not items:
            return True

        # One route, so one width. Refused here rather than at the first offending
        # INSERT, because a batch that is half-written is worse than one that is
        # refused: the caller has no way to tell which half.
        widths = sorted({len(item["embedding"]) for item in items})
        if len(widths) > 1:
            raise ValueError(
                f"A batch goes to one route and a route declares one width, but this "
                f"batch holds widths {widths}. Split it by width and store each part "
                f"under its own model_key."
            )

        # spec 226: one registry read for the whole batch, before the first INSERT and
        # before the inference fallback below. A refused batch writes nothing — not a
        # partial prefix — because the check runs outside the transaction entirely.
        table, route = self._route_for_write(
            graph_id, model_key, dimension=widths[0], dtype=_dtype
        )
        self.enforce_embedding_identity(
            table,
            dimension=widths[0],
            dtype=_dtype,
            config=model_key,
            graph_id=route.graph_id if route is not None else "",
        )

        dim = self._route_width(route, widths[0])

        for item in items:
            node_id = item["node_id"]
            embedding = item["embedding"]
            if len(embedding) != dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
                )
            self._assert_node_exists(node_id, graph=graph_id)

        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            for item in items:
                node_id = item["node_id"]
                embedding = item["embedding"]
                metadata = item.get("metadata")

                emb_str = ",".join(str(x) for x in embedding)
                meta_json = json.dumps(metadata) if metadata else None

                try:
                    cursor.execute(
                        f"DELETE FROM {self._t(table)}"
                        f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
                        [node_id, graph_id],
                    )
                except Exception:
                    pass
                cursor.execute(
                    f"INSERT INTO {self._t(table)}"
                    f" (graph_id, node_id, emb, metadata)"
                    f" VALUES (?, ?, TO_VECTOR('{emb_str}', {_dtype}), ?)",
                    [graph_id, node_id, meta_json],
                )
            cursor.execute("COMMIT")
            return True
        except Exception as e:
            cursor.execute("ROLLBACK")
            self._diagnose_vector_write(e, table, widths[0])
            raise


    def store_edge_embedding(
        self,
        s: str,
        p: str,
        o_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        dtype: Optional[str] = None,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> bool:
        """Store one edge vector in one graph (spec 230, FR-006).

        The node version of this, with the triple in place of the node ID. The same
        order applies and for the same reason: resolve the route, check the identity
        against *that* table's registry row, only then write. Enforcing first would
        check `kg_EdgeEmbeddings`' row for a write going somewhere else, so a
        wrong-width write would pass and be refused by IRIS at INSERT with SQLCODE
        -104 — an error about a column, for a mistake about a model.

        No check that the edge exists. A node vector's route has a foreign key to
        `nodes` and so `store_embedding` asserts the node first; an edge route has
        none, because `rdf_edges`' primary key is an IDENTITY `edge_id` rather than
        the triple, and requiring the edge row first would invert the order
        `embed_edges` works in.

        `graph` and `model_key` are keyword-only so a positional call cannot land a
        graph in `metadata` or `dtype`. A graph ID is collision avoidance, not an
        authorisation boundary: the caller supplies it, so it cannot decide what
        that caller may read (FR-032).
        """
        _dtype = (dtype or self.vector_dtype).upper()
        graph_id = DEFAULT_GRAPH if graph is None else graph

        table, route = self._route_for_write(
            graph_id,
            model_key,
            dimension=len(embedding),
            dtype=_dtype,
            kind=ROUTE_KIND_EDGE,
        )
        self.enforce_embedding_identity(
            table,
            dimension=len(embedding),
            dtype=_dtype,
            config=model_key,
            graph_id=route.graph_id if route is not None else "",
        )

        dim = self._route_width(route, len(embedding))
        if len(embedding) != dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
            )

        cursor = self.conn.cursor()
        emb_str = ",".join(str(x) for x in embedding)
        meta_json = json.dumps(metadata) if metadata else None

        try:
            # Scoped. An unscoped delete on the triple alone is the pre-4.0.0
            # overwrite wearing a new column name: storing graph B's vector for
            # `(s, p, o_id)` would erase graph A's, which is the collision
            # UNIQUE (graph_id, s, p, o_id) exists to permit.
            cursor.execute(
                f"DELETE FROM {self._t(table)}"
                " WHERE s = ? AND p = ? AND o_id = ?"
                f" AND {graph_scope_predicate('graph_id')}",
                [s, p, o_id, graph_id],
            )
        except Exception:
            pass
        try:
            cursor.execute(
                f"INSERT INTO {self._t(table)} (graph_id, s, p, o_id, emb, metadata)"
                f" VALUES (?, ?, ?, ?, TO_VECTOR('{emb_str}', {_dtype}), ?)",
                [graph_id, s, p, o_id, meta_json],
            )
        except Exception as e:
            self._diagnose_vector_write(e, table, len(embedding))
            raise
        self.conn.commit()
        return True


    def store_edge_embeddings(
        self,
        items: List[Dict[str, Any]],
        dtype: Optional[str] = None,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> bool:
        """Store a batch of edge vectors in one graph (spec 230, FR-006).

        One graph and one model for the whole batch, because a route is one physical
        table at one declared width: a batch spanning routes could only be honoured
        by splitting it across tables, which loses the single transaction that is
        the reason to batch. A per-item `"graph"` key is ignored rather than
        honoured for some rows.

        Each item is ``{"s", "p", "o_id", "embedding"}`` with an optional
        ``"metadata"``.
        """
        _dtype = (dtype or self.vector_dtype).upper()
        graph_id = DEFAULT_GRAPH if graph is None else graph
        if not items:
            return True

        # Refused here rather than at the first offending INSERT: a half-written
        # batch is worse than a refused one, because the caller cannot tell which
        # half landed.
        widths = sorted({len(item["embedding"]) for item in items})
        if len(widths) > 1:
            raise ValueError(
                f"A batch goes to one route and a route declares one width, but this "
                f"batch holds widths {widths}. Split it by width and store each part "
                f"under its own model_key."
            )

        table, route = self._route_for_write(
            graph_id, model_key, dimension=widths[0], dtype=_dtype, kind=ROUTE_KIND_EDGE
        )
        self.enforce_embedding_identity(
            table,
            dimension=widths[0],
            dtype=_dtype,
            config=model_key,
            graph_id=route.graph_id if route is not None else "",
        )

        dim = self._route_width(route, widths[0])
        for item in items:
            if len(item["embedding"]) != dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {dim}, "
                    f"got {len(item['embedding'])}"
                )

        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            for item in items:
                s, p, o_id = item["s"], item["p"], item["o_id"]
                emb_str = ",".join(str(x) for x in item["embedding"])
                metadata = item.get("metadata")
                meta_json = json.dumps(metadata) if metadata else None

                try:
                    cursor.execute(
                        f"DELETE FROM {self._t(table)}"
                        " WHERE s = ? AND p = ? AND o_id = ?"
                        f" AND {graph_scope_predicate('graph_id')}",
                        [s, p, o_id, graph_id],
                    )
                except Exception:
                    pass
                cursor.execute(
                    f"INSERT INTO {self._t(table)}"
                    " (graph_id, s, p, o_id, emb, metadata)"
                    f" VALUES (?, ?, ?, ?, TO_VECTOR('{emb_str}', {_dtype}), ?)",
                    [graph_id, s, p, o_id, meta_json],
                )
            cursor.execute("COMMIT")
            return True
        except Exception as e:
            cursor.execute("ROLLBACK")
            self._diagnose_vector_write(e, table, widths[0])
            raise


    def embed_nodes(
        self,
        model=None,
        text_fn=None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
        label: str = None,
        node_ids: List[str] = None,
        exclude_pattern: str = None,
        missing_only: bool = False,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> dict:
        """Embed a graph's nodes in bulk (spec 227).

        `graph` and `model_key` are keyword-only, so a 3.2.0 positional call cannot
        land a graph in `label`. Both the node selection and the writes are scoped:
        selecting namespace-wide would invent graph-A membership for graph B's nodes,
        by writing a graph-A vector for every node ID it found.

        The writes go through `store_embeddings`, which routes the pair, enforces
        spec 226 identity and binds the graph. This method used to issue its own
        `INSERT ... (id, emb)`, which 4.0.0 refuses with SQLCODE -108 ('node_id' is
        a required field) — inside an `except Exception` that counted an error, so a
        bulk embed reported errors instead of a broken statement (T071).

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        from iris_vector_graph.engine import _load_sentence_transformer, _is_sentence_transformer

        graph_id = DEFAULT_GRAPH if graph is None else graph
        sel = EmbedSelector(
            label=label,
            node_ids=node_ids,
            exclude_pattern=exclude_pattern,
            missing_only=missing_only,
        )
        where = build_node_where(
            sel,
            schema_prefix=self._schema_prefix,
            embeddings_table=self._t(
                self._route_for_read(graph_id, model_key)[0] or "kg_NodeEmbeddings"
            ),
            graph_id=graph_id,
        )

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                self.embedder = _load_sentence_transformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(f"SELECT node_id FROM {self._t('nodes')} {where_clause}")
            all_node_ids = [row[0] for row in cursor.fetchall()]
            n_total = len(all_node_ids)

            if not force and not missing_only:
                # Which of this graph's nodes already have a vector on this route.
                # Read `SELECT id` before 4.0.0, i.e. a set of RowIDs that matched no
                # node ID, so nothing was ever treated as already embedded.
                already_embedded: set = set()
                emb_table, _r = self._route_for_read(graph_id, model_key)
                if emb_table is not None:
                    cursor.execute(
                        f"SELECT node_id FROM {self._t(emb_table)}"
                        f" WHERE {graph_scope_predicate('graph_id')}",
                        [graph_id],
                    )
                    already_embedded = {row[0] for row in cursor.fetchall()}
                to_embed = [nid for nid in all_node_ids if nid not in already_embedded]
            else:
                to_embed = all_node_ids

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch_ids = to_embed[batch_start : batch_start + batch_size]

                placeholders = ", ".join("?" * len(batch_ids))
                # Scoped: the text a vector is built from must come from the graph
                # being embedded. A node ID living in two graphs would otherwise have
                # both graphs' properties concatenated into one text, and the vector
                # would describe neither graph.
                cursor.execute(
                    f'SELECT s, "key", val FROM {self._t("rdf_props")}'
                    f" WHERE s IN ({placeholders})"
                    f" AND {graph_scope_predicate('graph_id')}",
                    list(batch_ids) + [graph_id],
                )
                props_by_node: Dict[str, Dict[str, Any]] = {}
                for row in cursor.fetchall():
                    node_id, key, val = row[0], row[1], row[2]
                    props_by_node.setdefault(node_id, {})[key] = val

                texts: List[str] = []
                valid_ids: List[str] = []
                for node_id in batch_ids:
                    props = props_by_node.get(node_id, {})
                    if text_fn is not None:
                        try:
                            text = text_fn(node_id, props)
                        except Exception as ex:
                            logger.warning(
                                f"embed_nodes: text_fn raised for {node_id}: {ex}"
                            )
                            errors += 1
                            continue
                    else:
                        text = node_id

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_ids.append(node_id)

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch_ids)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            use_batch = _is_sentence_transformer(self.embedder)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(
                            texts, batch_size=min(64, len(texts)), show_progress_bar=False
                        )
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_nodes: batch encode failed, falling back per-node: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_nodes: embed_text failed: {ex2}")
                            embeddings.append(None)

                items = []
                for node_id, emb in zip(valid_ids, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    items.append({"node_id": node_id, "embedding": list(emb)})

                if items:
                    # One routed, identity-checked, graph-bound write per batch instead
                    # of a delete-then-insert per node. `store_embeddings` also refuses a
                    # batch of mixed widths outright, where the loop below it would have
                    # written the agreeing prefix and errored on the rest.
                    try:
                        self.store_embeddings(
                            items, graph=graph_id, model_key=model_key
                        )
                        embedded += len(items)
                    except Exception as ex2:
                        logger.warning(
                            "embed_nodes: batch store failed (%d nodes): %s",
                            len(items),
                            ex2,
                        )
                        errors += len(items)

                self.conn.commit()
                n_done = batch_start + len(batch_ids)
                logger.info(
                    f"embed_nodes: {n_done}/{n_to_embed} processed ({embedded} embedded)"
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder


    def embed_edges(
        self,
        model=None,
        text_fn=None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
        predicate: str = None,
        source_label: str = None,
        target_label: str = None,
        exclude_pattern: str = None,
        missing_only: bool = False,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> dict:
        """Embed a graph's edges in bulk (spec 230, FR-006).

        `graph` and `model_key` are keyword-only, so a positional 3.2.0 call cannot
        land a graph in `predicate`. Both halves are scoped: selecting edges
        namespace-wide would invent graph-A membership for graph B's edges by
        writing a graph-A vector for every triple it found, and asking
        `kg_EdgeEmbeddings` which edges are already done would report graph A's as
        finished on the strength of the default graph's rows.

        The writes go through `store_edge_embeddings`, which routes the pair,
        enforces spec 226 identity and binds the graph. This method used to issue
        its own `INSERT ... (s, p, o_id, emb)` against one namespace-wide table,
        inside an `except Exception` that counted an error — so a broken statement
        looked like an embedding failure.
        """
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        from iris_vector_graph.engine import _load_sentence_transformer, _is_sentence_transformer

        graph_id = DEFAULT_GRAPH if graph is None else graph
        sel = EmbedSelector(
            predicate=predicate,
            source_label=source_label,
            target_label=target_label,
            exclude_pattern=exclude_pattern,
            missing_only=missing_only,
        )
        where = build_edge_where(
            sel, schema_prefix=self._schema_prefix, graph_id=graph_id
        )

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                self.embedder = _load_sentence_transformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(
                f"SELECT s, p, o_id FROM {self._t('rdf_edges')} {where_clause}"
            )
            all_edges = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            n_total = len(all_edges)

            if not force:
                # The pair's own route, and nothing if it has none: a pair with no
                # route holds no edge vectors, so every selected edge is still to
                # do. Reading `kg_EdgeEmbeddings` instead would mark graph A's edges
                # done because the default graph had embedded the same triples.
                probe_table, _probe_route = self._route_for_read(
                    graph_id, model_key, kind=ROUTE_KIND_EDGE
                )
                already_embedded = set()
                if probe_table is not None:
                    cursor.execute(
                        f"SELECT s, p, o_id FROM {self._t(probe_table)}"
                        f" WHERE {graph_scope_predicate('graph_id')}",
                        [graph_id],
                    )
                    already_embedded = {
                        (row[0], row[1], row[2]) for row in cursor.fetchall()
                    }
                to_embed = [e for e in all_edges if e not in already_embedded]
            else:
                to_embed = all_edges

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch = to_embed[batch_start : batch_start + batch_size]

                texts: List[str] = []
                valid_edges: List[tuple] = []
                for s, p, o_id in batch:
                    if text_fn is not None:
                        try:
                            text = text_fn(s, p, o_id)
                        except Exception as ex:
                            logger.warning(
                                "embed_edges: text_fn raised for (%s, %s, %s): %s",
                                s, p, o_id, ex,
                            )
                            errors += 1
                            continue
                    else:
                        text = f"{s} {p} {o_id}"

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_edges.append((s, p, o_id))

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            use_batch = _is_sentence_transformer(self.embedder)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(texts, batch_size=min(64, len(texts)), show_progress_bar=False)
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_edges: batch encode failed, falling back per-edge: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_edges: embed_text failed: {ex2}")
                            embeddings.append(None)

                items = []
                for (s, p, o_id), emb in zip(valid_edges, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    items.append(
                        {"s": s, "p": p, "o_id": o_id, "embedding": list(emb)}
                    )

                if items:
                    # One routed, identity-checked, graph-bound write per batch
                    # instead of a delete-then-insert per edge. `store_edge_embeddings`
                    # also refuses a batch of mixed widths outright, where the loop
                    # this replaced wrote the agreeing prefix and errored on the rest.
                    try:
                        self.store_edge_embeddings(
                            items, graph=graph_id, model_key=model_key
                        )
                        embedded += len(items)
                    except Exception as ex2:
                        logger.warning(
                            "embed_edges: batch store failed (%d edges): %s",
                            len(items),
                            ex2,
                        )
                        errors += len(items)

                self.conn.commit()
                n_done = batch_start + len(batch)
                logger.info(
                    "embed_edges: %d/%d processed (%d embedded)",
                    n_done, n_to_embed, embedded,
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder


    def get_embedding(
        self,
        node_id: str,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve one node's embedding from one graph (spec 227).

        Args:
            node_id: The node ID to get embedding for
            graph: The graph to read from; ``None`` is the default graph
            model_key: The model whose route to read; ``None`` is the graph's
                unnamed-model route

        Returns:
            Dict with 'id', 'embedding' (as list of floats), and 'metadata' (if present)
            None if the node has no embedding in this graph, or the pair has no route

        The statement used to read `WHERE id = ?`, which compared a node ID against
        the table's RowID and matched nothing — so this returned None for every node
        on a 4.0.0 install, indistinguishable from "not embedded yet".

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            # An unrouted pair has no vectors. Reading `kg_NodeEmbeddings` instead
            # would answer this graph's question with the default graph's row.
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT node_id, emb, metadata FROM {self._t(table)}"
            f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
            [node_id, graph_id],
        )
        row = cursor.fetchone()
        if not row:
            return None

        node_id, emb_csv, metadata_json = row
        embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
        metadata = json.loads(metadata_json) if metadata_json else None

        result = {"id": node_id, "embedding": embedding}
        if metadata:
            result["metadata"] = metadata
        return result


    def get_embeddings(
        self,
        node_ids: List[str],
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve several nodes' embeddings from one graph (spec 227).

        Args:
            node_ids: List of node IDs
            graph: The graph to read from; ``None`` is the default graph
            model_key: The model whose route to read

        Returns:
            List of dicts with 'id', 'embedding', and 'metadata' for nodes that have
            an embedding *in this graph*. Nodes with none are absent from the list,
            as before — the list is not padded to the input length.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        if not node_ids:
            return []

        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            return []

        cursor = self.conn.cursor()
        placeholders = ",".join(["?"] * len(node_ids))
        cursor.execute(
            f"SELECT node_id, emb, metadata FROM {self._t(table)}"
            f" WHERE node_id IN ({placeholders})"
            f" AND {graph_scope_predicate('graph_id')}",
            list(node_ids) + [graph_id],
        )

        results = []
        for row in cursor.fetchall():
            node_id, emb_csv, metadata_json = row
            embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
            metadata = json.loads(metadata_json) if metadata_json else None

            result = {"id": node_id, "embedding": embedding}
            if metadata:
                result["metadata"] = metadata
            results.append(result)

        return results


    def enqueue_for_embedding(
        self,
        node_ids: Optional[List[str]] = None,
        embedding_config: str = "",
        texts: Optional[List[str]] = None,
        *,
        graph: Optional[str] = None,
    ) -> int:
        """Enqueue embedding work onto the async queue. Returns the number enqueued.

        Two modes (spec 199):
          - ``node_ids``: node-keyed entries (re-enqueue overwrites — one entry per
            node; backward compatible with the prior signature).
          - ``texts``: free-text entries (always-new; never deduplicated).
        Both may be supplied; an empty/None pair returns 0. Degrades gracefully
        (returns 0 + warns) when the queue backend is unavailable.

        Raises:
            EmbeddingIdentityConflict: ``embedding_config`` (or, when empty, this engine's
                own model) disagrees with what ``kg_NodeEmbeddings`` records. Queueing work
                that cannot be honoured only moves the failure onto a worker, away from
                whoever asked for it — so this is refused here, before anything is queued
                (spec 226, FR-011). Unlike the backend-unavailable case below, a conflict is
                never downgraded to a warning.
        """
        from iris_vector_graph.schema import _call_classmethod
        import json as _json

        # Deliberately outside the try/except blocks below, which exist to tolerate a
        # missing queue backend. A conflict is not a degraded backend.
        if node_ids or texts:
            self.enforce_embedding_identity(
                "kg_NodeEmbeddings", config=embedding_config
            )

        # Sent explicitly even when it is the default graph. The queue runs in another
        # process: what the entry carries is all the worker will ever know, so leaving
        # the graph to the ObjectScript parameter default means the worker's answer
        # depends on which version of the class is deployed rather than on this call.
        graph_id = DEFAULT_GRAPH if graph is None else graph

        total = 0
        if node_ids:
            try:
                ids_json = _json.dumps(node_ids)
                result = _call_classmethod(
                    self.conn, "Graph.KG.EmbedQueue", "BulkEnqueue",
                    ids_json, embedding_config, graph_id,
                )
                total += int(str(result))
            except Exception as e:
                logger.warning("enqueue_for_embedding (node_ids) failed: %s", e)
        if texts:
            try:
                texts_json = _json.dumps(texts)
                result = _call_classmethod(
                    self.conn, "Graph.KG.EmbedQueue", "BulkEnqueueText",
                    texts_json, embedding_config, graph_id,
                )
                total += int(str(result))
            except Exception as e:
                logger.warning("enqueue_for_embedding (texts) failed: %s", e)
        return total


    def process_embed_queue(self, batch_size: int = 100) -> dict:
        """Process up to ``batch_size`` PENDING queue entries (spec 199).

        Claims a batch of pending entries, embeds ALL of their texts in a SINGLE
        embedder call (the batched performance win), writes each result back to the
        queue entry, and — for node-keyed entries — upserts the vector into
        ``kg_NodeEmbeddings`` so semantic search finds it. A per-entry embedding
        failure marks only that entry ERROR; the rest of the batch still completes.
        Returns ``{"processed": int, "errors": int}``. Degrades gracefully when the
        backend is unavailable.
        """
        import json as _json
        from iris_vector_graph.schema import _call_classmethod
        try:
            claim_json = str(_call_classmethod(
                self.conn, "Graph.KG.EmbedQueue", "ClaimPendingBatch", batch_size,
            ))
            entries = _json.loads(claim_json) if claim_json else []
        except Exception as e:
            logger.warning("process_embed_queue claim failed: %s", e)
            return {"processed": 0, "errors": 0}

        if not entries:
            return {"processed": 0, "errors": 0}

        # spec 226 / FR-011: fail conflicting entries before the model runs. Each entry
        # carries the model it asked for (`config`); an empty config is this worker's own
        # identity, not an unknown. Filtering here rather than inside the loop below keeps
        # the batched encode aligned with the entries it is for, spends no model time on
        # work that could never be stored, and — the point of the batch — lets one bad
        # entry fail without stalling the rest.
        honoured = []
        refused = 0
        own = self._offered_embedding_identity()
        for entry in entries:
            try:
                self._assert_entry_is_this_workers_work(entry, own)
            except EmbeddingIdentityConflict as e:
                refused += 1
                self._fail_queue_entry(entry.get("reqId", ""), str(e))
                continue
            honoured.append(entry)
        entries = honoured
        if not entries:
            return {"processed": 0, "errors": refused}

        texts = [e.get("text", "") for e in entries]
        # Single batched embedder call (SC-002). If the whole call fails, fall back to
        # per-entry so one poison text does not fail the entire batch (FR-007).
        vectors = None
        try:
            vectors = self._encode_batch(texts)
        except Exception as e:
            logger.warning("batch encode failed, falling back per-entry: %s", e)

        processed = 0
        errors = refused
        for idx, entry in enumerate(entries):
            req_id = entry.get("reqId", "")
            node_id = entry.get("node_id", "") or ""
            try:
                if vectors is not None:
                    vec = vectors[idx]
                else:
                    vec = self._encode_batch([entry.get("text", "")])[0]
                vec_str = ",".join(str(float(x)) for x in vec)
                _call_classmethod(
                    self.conn, "Graph.KG.EmbedQueue", "SetResult",
                    req_id, "DONE", vec_str,
                )
                if node_id:
                    # `.get("graph")` is absent on a pre-upgrade entry and `None` would
                    # bind as SQL NULL — a vector belonging to no graph, which no scoped
                    # read can find. An absent subscript means the default graph.
                    #
                    # No `model_key`: the entry agrees with this worker (or named nothing),
                    # and the worker's own identity is both the honest record of what
                    # produced the vector and the key the route resolves from. Passing the
                    # entry's config instead would record a local embedder's vectors as an
                    # IRIS embedding config's, and then refuse the next write against it.
                    self._upsert_node_embedding(
                        node_id, vec, graph=entry.get("graph") or DEFAULT_GRAPH
                    )
                processed += 1
            except Exception as e:
                errors += 1
                self._fail_queue_entry(req_id, str(e))
        return {"processed": processed, "errors": errors}

    def _assert_entry_is_this_workers_work(
        self, entry: dict, own: EmbeddingIdentity
    ) -> None:
        """Refuse a queue entry that asks for a model this worker does not produce.

        FR-011's question is about the worker, not about a table: the vector will be
        computed by *this* embedder, so an entry naming a different model is work this
        worker cannot honour however the registry happens to look. 3.2.0 asked it by
        enforcing the entry's config against ``kg_NodeEmbeddings``' row, which under 227
        is the default pair's row and nobody else's — it refused entries on the strength
        of a table the write was not going to, and let entries past when that table had
        no row.

        An empty or absent ``config`` is not an unknown model. It means "whatever this
        worker is", which resolves to ``own`` and is honoured.
        """
        asked = (entry.get("config") or "").strip()
        if not asked:
            return
        asked_key = normalize_model_key(asked)
        if asked_key == own.model_key:
            return
        graph_id = entry.get("graph") or DEFAULT_GRAPH
        raise EmbeddingIdentityConflict(
            route_table_name(graph_id, asked_key),
            identity_from_config(asked),
            own,
            "the queue entry asks for a model this worker does not produce",
            graph_id=graph_id,
        )

    def _fail_queue_entry(self, req_id: str, message: str) -> None:
        """Mark one queue entry ERROR with the reason, leaving the rest of the batch alone.

        The reason is readable back via ``Graph.KG.EmbedQueue.GetEntry``, which is the only
        way a per-entry failure is visible at all: a claim returns PENDING entries only.
        """
        from iris_vector_graph.schema import _call_classmethod
        try:
            _call_classmethod(
                self.conn, "Graph.KG.EmbedQueue", "SetResult",
                req_id, "ERROR", message[:500],
            )
        except Exception as se:
            logger.warning("SetResult ERROR failed for %s: %s", req_id, se)

    def _encode_batch(self, texts: List[str]) -> list:
        """Embed a list of texts in one embedder call, resolving the engine embedder
        (same auto-init path used by embed_text). Returns a list of vectors."""
        if not self.embedder:
            # Reuse embed_text's auto-init by embedding the first item, then encode all.
            # embed_text resolves/sets self.embedder (or raises a clear RuntimeError).
            if texts:
                self.embed_text(texts[0])
        embedder = self.embedder
        if hasattr(embedder, "encode"):
            result = embedder.encode(texts)
            return [r.tolist() if hasattr(r, "tolist") else list(r) for r in result]
        if hasattr(embedder, "embed"):
            return [embedder.embed(t) for t in texts]
        if callable(embedder):
            return [embedder(t) for t in texts]
        raise TypeError(f"Embedder {type(embedder)} has no encode/embed and is not callable")

    def _upsert_node_embedding(
        self,
        node_id: str,
        vec,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> None:
        """Write a node's embedding where its ``(graph, model)`` routes (spec 227).

        This is the seam the embed queue stores vectors through, and it used to name
        ``kg_NodeEmbeddings`` in its own SQL: graph-scoped, but not model-routed. A worker
        declaring a model therefore wrote every queued vector into the default pair's
        table, where the routed search that the vector was queued for does not look, and
        where it collides on ``(graph_id, node_id)`` with the default pair's own row.

        Delegating to ``store_embedding`` rather than routing here on its own: routing,
        the identity contract (spec 226, FR-011), the width check and the scoped
        delete-then-insert are one sequence, and a second copy of it is a second place
        for the queue to drift out of agreement with every other writer.
        """
        self.store_embedding(
            node_id, [float(x) for x in vec], graph=graph, model_key=model_key
        )

    def clear_done(self) -> int:
        """Remove completed (DONE) queue entries; leave PENDING/ERROR intact (spec 199,
        FR-009). Returns the number cleared; 0 when the backend is unavailable."""
        from iris_vector_graph.schema import _call_classmethod
        try:
            return int(str(_call_classmethod(
                self.conn, "Graph.KG.EmbedQueue", "ClearDone",
            )))
        except Exception as e:
            logger.warning("clear_done failed: %s", e)
            return 0


    def embed_queue_pending(self) -> int:
        try:
            from iris_vector_graph.schema import _call_classmethod
            return int(str(_call_classmethod(self.conn, "Graph.KG.EmbedQueue", "PendingCount")))
        except Exception:
            return 0


    def start_background_embedding(self, batch_size: int = 100) -> str:
        try:
            from iris_vector_graph.schema import _call_classmethod
            return str(_call_classmethod(self.conn, "Graph.KG.EmbedQueue", "StartBackgroundTask", batch_size))
        except Exception as e:
            logger.warning("start_background_embedding failed: %s", e)
            return ""

    # ── BM25Index: pure ObjectScript lexical search ──


    def embedding_count(
        self, *, graph: Optional[str] = None, model_key: Optional[str] = None
    ) -> int:
        """How many embeddings exist.

        `graph=None` counts the legacy table across the whole namespace, which is what
        3.2.0 answered and still the right answer to "how much is in there". Naming a
        graph counts that pair's route instead, and an unrouted pair counts zero
        rather than falling back to the namespace total (spec 227).

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        cursor = self.conn.cursor()
        try:
            if graph is not None:
                table, _route = self._route_for_read(graph, model_key)
                if table is None:
                    return 0
                cursor.execute(
                    f"SELECT COUNT(*) FROM {self._t(table)}"
                    f" WHERE {graph_scope_predicate('graph_id')}",
                    [graph],
                )
            else:
                cursor.execute(f"SELECT COUNT(*) FROM {self._t('kg_NodeEmbeddings')}")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            cursor.close()
