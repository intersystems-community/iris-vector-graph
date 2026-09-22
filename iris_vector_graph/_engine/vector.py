import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Sequence, Tuple, Union

from iris_vector_graph.constants import DEFAULT_GRAPH, ROUTE_TABLE_PREFIX
from iris_vector_graph.index_protocol import Index
from iris_vector_graph.result import IVGResult
from iris_vector_graph.routing import graph_scope_predicate
from iris_vector_graph.security import validate_table_name
from iris_vector_graph._validate import (
    IVFBuildInput, VectorSearchInput, BM25BuildInput, BM25SearchInput, VecSearchInput,
)

logger = logging.getLogger(__name__)


@dataclass
class RouteRecall:
    """One route's measured recall against an exact scan of the same rows (FR-021).

    ``recall`` is recall@k: of the ``k`` nearest neighbours an exhaustive scan of the
    route finds for a probe, the fraction the route's own search returned, averaged over
    the probes. 1.0 does not mean the index is exact in general — it means it was exact
    on this data, at this ``k``, for these probes, at ``measured_at``. That is the only
    kind of recall claim spec 227 accepts.
    """

    table_name: str
    graph_id: str
    model_key: Optional[str]
    k: int
    probes: int
    rows_scanned: int
    recall: float
    measured_at: str
    index_state: Optional[str] = None
    #: Did the registry row take the number? A write that failed on, say, a value IRIS
    #: would not bind to TIMESTAMP is otherwise indistinguishable from a route nobody
    #: measured, and the caller holding the number has no way to know it was lost.
    recorded: bool = False


class VectorMixin:
    def _detect_stored_vector_dtype(self) -> str:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                f"SELECT TOP 1 emb FROM {self._t('kg_NodeEmbeddings')} WHERE emb IS NOT NULL"
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return "DOUBLE"
            emb_csv = str(row[0])
            sample = ",".join(emb_csv.split(",")[:2])
            for dtype in ("FLOAT", "DOUBLE"):
                try:
                    c2 = self.conn.cursor()
                    c2.execute(
                        f"SELECT VECTOR_COSINE(emb, TO_VECTOR(?, {dtype})) FROM {self._t('kg_NodeEmbeddings')} WHERE emb IS NOT NULL LIMIT 1",
                        [sample],
                    )
                    c2.fetchone()
                    c2.close()
                    logger.info("Auto-detected stored vector dtype: %s", dtype)
                    return dtype
                except Exception:
                    pass
        except Exception:
            pass
        return "DOUBLE"
    def _build_index_registry(self) -> Dict[str, str]:
        registry: Dict[str, str] = {}
        try:
            import iris as _iris_pkg
            if not callable(getattr(_iris_pkg, "gref", None)):
                raise AttributeError("iris.gref not available")
            for global_name, type_str in (
                ("^IVF",      "ivf"),
                ("^VecIdx",   "vec"),
                ("^BM25Idx",  "bm25"),
                ("^PLAID",    "plaid"),
            ):
                gref = _iris_pkg.gref(global_name)
                name = ""
                for _ in range(10000):
                    name = gref.order([name])
                    if not isinstance(name, str) or name == "":
                        break
                    registry[name] = type_str
        except Exception:
            pass
        if not registry:
            try:
                from iris_vector_graph.schema import _call_classmethod
                for cls_name, type_str in (
                    ("Graph.KG.IVFIndex",   "ivf"),
                    ("Graph.KG.BM25Index",  "bm25"),
                    ("Graph.KG.PLAIDSearch", "plaid"),
                ):
                    raw = str(_call_classmethod(self.conn, cls_name, "List"))
                    for name in (n.strip() for n in raw.split(",") if n.strip()):
                        registry[name] = type_str
            except Exception:
                pass
        if not registry:
            # SQL fallback for an install whose ObjectScript index classes are not
            # deployed. Before 4.0.0 this loop read a `cur` nobody opened, and it sat
            # inside the handler above as a second `except` clause: every query raised
            # NameError into its own `except Exception: pass`, so the fallback reported
            # no indexes rather than failing.
            cur = None
            try:
                cur = self.conn.cursor()
                for sql_query, type_str in (
                    ("SELECT DISTINCT name FROM Graph_KG.ivf_indexes", "ivf"),
                    ("SELECT DISTINCT name FROM Graph_KG.bm25_indexes", "bm25"),
                    ("SELECT DISTINCT name FROM Graph_KG.plaid_indexes", "plaid"),
                ):
                    try:
                        cur.execute(sql_query)
                        for row in cur.fetchall():
                            registry[str(row[0])] = type_str
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:
                        pass
        if self._probe_native_vec():
            registry["hnsw"] = "hnsw"
        return registry
    @property
    def _index_graph_scopes(self) -> Dict[str, str]:
        """Which graph each index covers, as far as this engine can tell (FR-009).

        Populated by `bm25_build` and `ivf_build`. Deliberately not filled in by
        `_build_index_registry`: `^IVF` records no graph, so an index this process
        did not build has an unknown scope, and reporting it as the default graph
        would be the same guess that made the BM25 leg answer every graph.
        """
        scopes = getattr(self, "_index_graph_scopes_cache", None)
        if scopes is None:
            scopes = {}
            self._index_graph_scopes_cache = scopes
        return scopes

    def _index_graph(self, name: str, index_type: str) -> Optional[str]:
        """The graph `name` was built for, or `None` when that is not knowable.

        A BM25 index records its graph in `^BM25Idx(name, "cfg", "graph")`, so its
        scope survives the process that built it and is read back here once. An IVF
        index has nowhere to record one, so an index built by another process stays
        unknown rather than being assumed.
        """
        scopes = self._index_graph_scopes
        if name in scopes:
            return scopes[name]
        if index_type != "bm25":
            return None
        try:
            recorded = self.bm25_info(name).get("graph")
        except Exception as exc:  # noqa: BLE001 - an unreadable index is just unknown
            logger.debug("bm25_info(%s) did not answer a graph: %s", name, exc)
            return None
        if recorded is None:
            return None
        scopes[name] = recorded
        return recorded

    def _fusion_leg_index(
        self, index_types: Sequence[str], graph_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Pick the index one fusion leg should read, preferring `graph_id`'s own.

        Returns `(name, type)`, or `(None, None)` when no index of these types
        exists. An `hnsw` entry is always eligible: it names the server-side route,
        which takes the graph as a query parameter.
        """
        fallback: Tuple[Optional[str], Optional[str]] = (None, None)
        for name in self._index_registry:
            index_type = self._index_registry[name]
            if index_type not in index_types:
                continue
            if index_type == "hnsw":
                return name, index_type
            recorded = self._index_graph(name, index_type)
            if recorded == graph_id:
                return name, index_type
            if recorded is None and fallback[0] is None:
                fallback = (name, index_type)
        if fallback[0] is not None:
            logger.warning(
                "kg_RRF_FUSE: using index %r (%s) for graph %r — the graph it was "
                "built for is not recorded, so its results may come from another "
                "graph. Rebuild it with graph=%r to scope this leg.",
                fallback[0],
                fallback[1],
                graph_id,
                graph_id,
            )
        return fallback

    def index(self, name: str) -> "Index":
        from iris_vector_graph.index_protocol import Index
        from iris_vector_graph.errors import IndexNotFoundError
        if name not in self._index_registry:
            raise IndexNotFoundError(name, known=list(self._index_registry))
        concept = self._LEGACY_TO_CONCEPT.get(
            self._index_registry[name], self._index_registry[name]
        )
        return Index(name=name, type=concept, engine=self)
    def create_index(self, config, replace: bool = False) -> "Index":
        from iris_vector_graph.index_protocol import Index
        if config.name in self._index_registry:
            if not replace:
                raise ValueError(
                    f"Index '{config.name}' already exists; pass replace=True to recreate."
                )
            self.index(config.name).drop()
        self._pending_index_config[config.name] = config
        self._index_registry[config.name] = config.type
        return Index(name=config.name, type=config.type, engine=self)
    def list_indexes(self) -> "List[Index]":
        return [self.index(n) for n in sorted(self._index_registry)]
    def _index_config(self, name: str):
        return self._pending_index_config.get(name)
    def _build_vector_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_create_index(name, dim=kw.get("dim") or cfg.dim, metric=cfg.metric)
            return self.vec_build(name)
        nlist = kw.get("nlist", getattr(cfg, "nlist", 256))
        metric = kw.get("metric", getattr(cfg, "metric", "cosine"))
        return self.ivf_build(name, nlist=nlist, metric=metric, node_ids=kw.get("node_ids"))
    def _search_vector_index(self, name: str, q, k: int = 10, **kw) -> list:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_search(name, q, k, **kw)
        return self.ivf_search(name, q, k, **kw)
    def _vector_index_insert(self, name: str, id_: str, vec) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_insert(name, id_, vec)
        else:
            self.ivf_insert(name, id_, vec)
    def _vector_index_drop(self, name: str) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_drop(name)
        else:
            self.ivf_drop(name)
    def _vector_index_info(self, name: str) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_info(name)
        return self.ivf_info(name)
    def _build_fulltext_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        props = kw.get("properties") or (cfg.properties if cfg else ["name"])
        k1 = kw.get("k1", getattr(cfg, "k1", 1.5))
        b = kw.get("b", getattr(cfg, "b", 0.75))
        graph = kw.get("graph", getattr(cfg, "graph", None))
        info = self.bm25_build(name, props, k1=k1, b=b, graph=graph)
        from iris_vector_graph.index_protocol import _rows_of
        from iris_vector_graph.errors import IndexNotBuiltError
        if _rows_of(info or {}) == 0:
            raise IndexNotBuiltError(name, rows=0)
        return info
    def _build_multivector_index(self, name: str, **kw) -> dict:
        docs = kw.get("docs")
        if not docs:
            from iris_vector_graph.errors import IndexNotBuiltError
            raise IndexNotBuiltError(name, rows=0)
        cfg = self._index_config(name)
        return self.plaid_build(
            name, docs,
            n_clusters=kw.get("n_clusters", getattr(cfg, "n_clusters", None)),
            dim=kw.get("dim", getattr(cfg, "dim", 128)),
        )
    def _build_neighborhood_index(self, name: str, **kw) -> dict:
        raise NotImplementedError(
            "neighborhood_vector index build lands in spec 181; "
            "config registered but build not yet wired."
        )
    def _search_neighborhood_index(self, name: str, q, k: int = 10, **kw) -> list:
        raise NotImplementedError("neighborhood_vector search lands in spec 181.")
    def _neighborhood_index_drop(self, name: str) -> None:
        self._iris_obj().kill("^NKG", "q")
    def _neighborhood_index_info(self, name: str) -> dict:
        return {"type": "neighborhood_vector", "rows": 0}
    def edge_vector_search(
        self,
        query_embedding,
        top_k: int = 10,
        score_threshold: float = None,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> List[dict]:
        """Nearest edges in one graph, from that graph's own edge route (spec 230).

        `graph` and `model_key` are keyword-only, so a positional 3.2.0 call cannot
        land a graph in `score_threshold`. A pair with no edge route answers nothing:
        not the default table and not another model's, because a substituted route
        returns real vectors from the wrong space — which score and rank exactly like
        an answer (FR-006, following 227's FR-013).
        """
        from iris_vector_graph._engine.schema import ROUTE_KIND_EDGE

        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key, kind=ROUTE_KIND_EDGE)
        if table is None:
            return []

        if isinstance(query_embedding, list):
            import json as _json
            query_vec_str = _json.dumps(query_embedding)
            dim = len(query_embedding)
        else:
            query_vec_str = query_embedding
            dim = str(query_embedding).count(",") + 1

        query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        sql = (
            f"SELECT TOP {int(top_k)} s, p, o_id, "
            f"VECTOR_COSINE(emb, {query_cast}) AS score "
            f"FROM {self._t(table)} "
            f"WHERE {graph_scope_predicate('graph_id')} "
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [query_vec_str, graph_id])
        except Exception as e:
            if "-30" in str(e) or "not found" in str(e).lower() or "empty" in str(e).lower():
                return []
            raise
        rows = cursor.fetchall()
        if not rows:
            return []
        return [
            {"s": row[0], "p": row[1], "o_id": row[2], "score": float(row[3])}
            for row in rows
        ]
    def _validate_k(self, k: Any) -> int:
        """
        Validates and caps the 'k' parameter (TOP clause limit)
        1 <= k <= 1000, defaults to 50.
        Handles non-numeric strings by failing safe to 50.
        """
        try:
            k = int(k or 50)
        except (ValueError, TypeError):
            return 50
        return min(max(1, k), 1000)
    def _assert_query_width(
        self, query_vector: Any, route: Any, *, graph_id: str, table: str
    ) -> None:
        """Refuse a query vector whose width disagrees with the route's declared one.

        IRIS answers the mismatch with `SQLCODE -257 Cannot perform vector operation
        on vectors of different lengths`, which reaches the caller of `kg_KNN_VEC` as
        an ordinary exception — the same shape as "this build has no such procedure".
        The fallback chain below reads it that way and re-asks the question twice more,
        swallowing each refusal, until the client-side path scores every row it may see
        in Python, where numpy declines per row and the `continue` drops it. The caller
        is handed `[]`: the answer that means this graph holds no neighbours for them.

        A caller error must not be reported as a server-unavailable condition, so the
        width is checked here, above the chain, before any statement runs (ADR-0005).

        Silent where there is nothing to compare against:

        - no route, or a route that declares no dimension — a legacy read on a database
          whose registry predates 227 makes no claim about width;
        - a seed node ID rather than a `[...]` literal — its vector comes out of the
          routed table, so its width is right by construction;
        - a literal this cannot parse — IRIS is a better judge of that than a guess
          here, and refusing it would be refusing on the strength of not understanding it.
        """
        dimension = getattr(route, "dimension", None) if route is not None else None
        if not dimension:
            return
        if not isinstance(query_vector, str):
            return
        text = query_vector.strip()
        if not text.startswith("["):
            return
        try:
            width = len(json.loads(text))
        except (ValueError, TypeError):
            return
        if width == int(dimension):
            return
        raise ValueError(
            f"query vector has {width} dimensions, but graph {graph_id!r} is routed to "
            f"{table}, which stores {int(dimension)}-dimensional vectors. A vector from "
            "one model cannot be scored against another model's space: pass the graph's "
            "own model_key and a vector of its width, or query the graph the vector "
            "belongs to."
        )

    def kg_KNN_VEC(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None,
        dtype: Optional[str] = None, *,
        graph: Optional[str] = None, model_key: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Nearest neighbours inside one graph (spec 227).

        ``graph`` and ``model_key`` are keyword-only, so a 3.2.0 positional call
        cannot bind a graph to ``dtype``. ``graph=None`` means the default graph
        ``''`` — never every graph. There is no value that means every graph
        (FR-004); a cross-graph search is a different question and needs a
        different method.

        Every statement this builds carries the graph predicate, including the
        lookup that expands a seed node ID into its stored vector: unscoped, that
        lookup reads whichever graph's row IRIS returns first and then searches
        graph A with graph B's vector — a wrong answer with no error anywhere.

        The table searched is the one this `(graph, model)` pair is routed to (spec
        227, FR-013). A pair with no route returns `[]`: there is no table holding
        its vectors, and every substitute returns real vectors from another space,
        which score and rank and look exactly like an answer.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        _dtype = (dtype or self.vector_dtype).upper()
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            return []
        self._assert_query_width(query_vector, _route, graph_id=graph_id, table=table)
        scope_n = graph_scope_predicate("n.graph_id")
        cursor = self.conn.cursor()
        try:
            emb_table = self._t(table)
            labels_table = self._t("rdf_labels")
            label_join = (
                f" LEFT JOIN {labels_table} L"
                f" ON L.s = n.node_id AND {graph_scope_predicate('L.graph_id')}"
            )

            qv = query_vector.strip() if isinstance(query_vector, str) else query_vector
            exclude_id: Optional[str] = None
            if isinstance(qv, str) and not qv.startswith("["):
                exclude_id = qv
                cursor.execute(
                    f"SELECT emb FROM {emb_table}"
                    f" WHERE node_id = ? AND {graph_scope_predicate('graph_id')}",
                    [exclude_id, graph_id],
                )
                row = cursor.fetchone()
                if not row:
                    return []
                query_vector = f"[{str(row[0])}]"

            if label_filter and exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f"{label_join}"
                    f" WHERE {scope_n} AND L.label = ? AND n.node_id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, graph_id, graph_id, label_filter, exclude_id],
                )
            elif label_filter:
                cursor.execute(
                    f"SELECT TOP ? n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f"{label_join}"
                    f" WHERE {scope_n} AND L.label = ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, graph_id, graph_id, label_filter],
                )
            elif exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" WHERE {scope_n} AND n.node_id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, graph_id, exclude_id],
                )
            else:
                cursor.execute(
                    f"SELECT TOP ? n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" WHERE {scope_n}"
                    f" ORDER BY score DESC",
                    [k, query_vector, graph_id],
                )
            results = cursor.fetchall()
            return [(entity_id, float(similarity)) for entity_id, similarity in results]
        except Exception as e:
            logger.warning(
                f"Server-side kg_KNN_VEC failed: {e}. Falling back to client-side logic."
            )
            # The fallback is scoped to the same graph. That is the whole point of
            # threading it: the failure mode of a partially-upgraded install must be a
            # scoped answer from a different code path, not an unscoped one.
            return self._kg_KNN_VEC_python_optimized(
                query_vector, k, label_filter, graph=graph_id, model_key=model_key
            )
    #: Two scores this close are one score. A neighbour that ties the k-th exact score is
    #: a correct answer for that slot, and fixture vectors tie constantly.
    _RECALL_TIE_EPSILON = 1e-9

    def measure_route_recall(
        self,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
        k: int = 10,
        probes: int = 5,
        record: bool = True,
    ) -> Optional[RouteRecall]:
        """Measure one route's search against an exhaustive scan of the same rows.

        Spec 227 routes one graph per table so that a scoped search needs no predicate
        over the ANN index, and therefore claims recall is not degraded by
        post-filtering. FR-021 does not accept that claim without a number: this
        produces the number and, unless ``record=False``, writes it and its timestamp
        onto the route's registry row.

        The exact side is not ``SELECT TOP k ... ORDER BY VECTOR_COSINE`` — that is the
        shape IRIS answers *from the ANN index*, so it would compare the index to
        itself. It scores every row in the route in one statement with no ``TOP`` and no
        ``ORDER BY``, and ranks in Python. Scores cross into Python; vectors never do.
        The seed vector reaches the scan as a subquery for the same reason (ADR-0005).

        Returns ``None`` — never a number — when there is nothing to measure: a pair
        with no route, or a route with no rows. Recall over zero rows is not 1.0, and a
        recorded 1.0 is a published claim.
        """
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, route = self._route_for_read(graph_id, model_key)
        if table is None:
            return None

        k = max(1, int(k))
        emb_table = self._t(table)
        scope = graph_scope_predicate("graph_id")
        cursor = self.conn.cursor()

        cursor.execute(
            f"SELECT TOP ? node_id FROM {emb_table} WHERE {scope} ORDER BY node_id",
            [max(1, int(probes)), graph_id],
        )
        seeds = [str(r[0]) for r in (cursor.fetchall() or [])]
        if not seeds:
            return None

        _dtype = (route.dtype if route is not None else self.vector_dtype) or "DOUBLE"
        hits = 0.0
        rows_scanned = 0
        for seed in seeds:
            cursor.execute(
                f"SELECT node_id, VECTOR_COSINE(emb, (SELECT emb FROM {emb_table}"
                f" WHERE node_id = ? AND {scope})) AS score"
                f" FROM {emb_table} WHERE {scope} AND node_id <> ?",
                [seed, graph_id, graph_id, seed],
            )
            scored = [
                (str(nid), float(score))
                for nid, score in (cursor.fetchall() or [])
                if score is not None
            ]
            rows_scanned += len(scored)
            if not scored:
                continue
            exact = sorted(scored, key=lambda r: -r[1])[:k]
            cutoff = exact[-1][1] - self._RECALL_TIE_EPSILON
            answered = self.kg_KNN_VEC(seed, k=k, graph=graph_id, model_key=model_key)
            by_id = dict(scored)
            found = sum(
                1
                for nid, _score in answered
                if nid in by_id and by_id[nid] >= cutoff
            )
            hits += min(found, len(exact)) / len(exact)

        if not rows_scanned:
            return None

        measured = RouteRecall(
            table_name=table,
            graph_id=graph_id,
            model_key=route.model_key if route is not None else model_key,
            k=k,
            probes=len(seeds),
            rows_scanned=rows_scanned,
            recall=hits / len(seeds),
            # `YYYY-MM-DD HH:MM:SS`, not ISO-8601: an offset-bearing string is not a
            # value IRIS binds to TIMESTAMP, and the UPDATE fails for a format reason
            # while looking like an unmeasured route. The clock is UTC.
            measured_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            index_state=route.index_state if route is not None else None,
        )
        if record:
            measured.recorded = bool(
                self._record_route_recall(
                    cursor,
                    measured.table_name,
                    measured.graph_id,
                    measured.recall,
                    measured.measured_at,
                )
            )
        return measured

    def measure_recall_all_routes(
        self, *, k: int = 10, probes: int = 5, record: bool = True
    ) -> List[RouteRecall]:
        """Measure every route the inventory reports, skipping the ones with no rows.

        One call, because the thing an operator publishes is the whole table (FR-021,
        SC-007) and asking per route means knowing the routes first.
        """
        measured: List[RouteRecall] = []
        for row in self.embedding_inventory():
            if not row.table_name:
                continue
            one = self.measure_route_recall(
                graph=row.graph_id, model_key=row.model_key, k=k, probes=probes,
                record=record,
            )
            if one is not None:
                measured.append(one)
        return measured

    def search_nodes_by_vector(
        self,
        query: "Union[List[float], str]",
        k: int = 10,
        label_filter: Optional[str] = None,
        ivf_name: Optional[str] = None,
        nprobe: int = 8,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Nearest neighbours within one graph, from that graph's routed table.

        `graph` and `model_key` are keyword-only for the reason `store_embedding`'s
        are: this method's fourth and fifth positional parameters are already taken
        by `ivf_name` and `nprobe`, and a 3.2.0 caller who passed a graph
        positionally would have named an IVF index instead (FR-013).

        The IVF fallback is graph-blind — `Graph.KG.IVFIndex` is keyed by index name
        and holds no graph — so a lookup that named a graph refuses rather than
        answering from it. A scoped lookup never widens (FR-013): an answer drawn
        from every graph is worse than no answer, because the caller cannot tell.
        """
        if not isinstance(query, str):
            VecSearchInput(query=list(query), k=k, nprobe=nprobe)
        if self._probe_native_vec():
            query_json = json.dumps([float(v) for v in query]) if not isinstance(query, str) else query
            return self.kg_KNN_VEC(
                query_json, k=k, label_filter=label_filter,
                graph=graph, model_key=model_key,
            )
        named_graph = graph is not None and graph != DEFAULT_GRAPH
        if named_graph or model_key is not None:
            raise ValueError(
                "search_nodes_by_vector cannot serve a graph-scoped lookup from an "
                f"IVF index: Graph.KG.IVFIndex holds no graph, so index "
                f"'{ivf_name or 'default'}' would answer from every graph. Native "
                "VECTOR_COSINE support is required for graph-scoped search."
            )
        if ivf_name is not None:
            query_list = json.loads(query) if isinstance(query, str) else query
            return self.ivf_search(ivf_name, query_list, k=k, nprobe=nprobe)
        query_list = json.loads(query) if isinstance(query, str) else query
        return self.ivf_search("default", query_list, k=k, nprobe=nprobe)
    def _kg_KNN_VEC_python_optimized(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None,
        *, graph: Optional[str] = None, model_key: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """The fallback path, scoped and routed like the procedure path.

        A fallback that widens is worse than a fallback that fails: the caller sees
        an answer, and the only symptom of the missing scope is extra neighbours
        they have no reason to distrust. Resolving the route again here rather than
        being handed a table name keeps that true of direct callers too.
        """
        _dtype = getattr(self, 'vector_dtype', 'DOUBLE')
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            return []
        self._assert_query_width(query_vector, _route, graph_id=graph_id, table=table)
        emb_table = self._t(table)
        labels_table = self._t("rdf_labels")
        scope_n = graph_scope_predicate("n.graph_id")

        if label_filter:
            sql = (
                f"SELECT TOP {int(k)} n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" LEFT JOIN {labels_table} L"
                f" ON L.s = n.node_id AND {graph_scope_predicate('L.graph_id')}"
                f" WHERE {scope_n} AND L.label = ?"
                f" ORDER BY score DESC"
            )
            params = [query_vector, graph_id, graph_id, label_filter]
        else:
            sql = (
                f"SELECT TOP {int(k)} n.node_id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" WHERE {scope_n}"
                f" ORDER BY score DESC"
            )
            params = [query_vector, graph_id]

        try:
            from iris_vector_graph.embedded import _sql_statement_execute, _is_ddtab_error
            rs = _sql_statement_execute(sql, params)
            results = [(row[0], float(row[1])) for row in rs if row[0] is not None]
            return results
        except Exception:
            pass

        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            results = [(row[0], float(row[1])) for row in cursor.fetchall()]
            cursor.close()
            return results
        except Exception:
            pass

        return self._kg_KNN_VEC_client_side(
            query_vector, k, label_filter, graph=graph_id, model_key=model_key
        )
    def _kg_KNN_VEC_client_side(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None,
        *, graph: Optional[str] = None, model_key: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """The last fallback. Scoped and routed for the same reason the others are:
        this one reads *every* row it is allowed to see and scores them in Python, so
        an unscoped or unrouted SELECT here is the widest leak of the three."""
        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            return []
        self._assert_query_width(query_vector, _route, graph_id=graph_id, table=table)
        cursor = self.conn.cursor()
        try:
            import numpy as np

            query_array = np.array(json.loads(query_vector))

            emb_table = self._t(table)
            labels_table = self._t("rdf_labels")
            scope_n = graph_scope_predicate("n.graph_id")
            if label_filter is None:
                cursor.execute(
                    f"SELECT n.node_id, n.emb FROM {emb_table} n"
                    f" WHERE n.emb IS NOT NULL AND {scope_n}",
                    [graph_id],
                )
            else:
                cursor.execute(
                    f"SELECT n.node_id, n.emb FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L"
                    f" ON L.s = n.node_id AND {graph_scope_predicate('L.graph_id')}"
                    f" WHERE n.emb IS NOT NULL AND {scope_n} AND L.label = ?",
                    [graph_id, graph_id, label_filter],
                )

            similarities = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for entity_id, emb_csv in batch:
                    try:
                        emb_array = np.fromstring(str(emb_csv), dtype=float, sep=",")
                        dot_product = np.dot(query_array, emb_array)
                        query_norm = np.linalg.norm(query_array)
                        emb_norm = np.linalg.norm(emb_array)
                        if query_norm > 0 and emb_norm > 0:
                            cos_sim = dot_product / (query_norm * emb_norm)
                            similarities.append((entity_id, float(cos_sim)))
                    except Exception:
                        continue

            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:k]

        except Exception as e:
            logger.error(f"Client-side kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()
    def kg_TXT(
        self,
        query_text: str,
        k: int = 50,
        min_confidence: int = 0,
        *,
        graph: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Full-text search over the docs table.

        Uses IRIS iFind through the `idx_docs_text_ifind` index, and scores a match by
        how many times the search string occurs in the document. That is a term
        frequency, not BM25: `%FIND.Rank` does not exist, and `%iFind.Rank` fails at
        runtime with <CLASS DOES NOT EXIST> because the IRIS AI image carries no
        `%iFind.Ranker.*` class for $$$IFDEFAULTRANKER to resolve.

        The predicate used to be `%FIND(d.text, ?) > 0` with `%FIND.Rank(d.text, ?)` as
        the score — neither is IRIS syntax (iFind is reached through the index, so a bare
        `%FIND` resolves as the user function `SQLUSER.%FIND`, SQLCODE -359). Every call
        therefore fell into the LIKE fallback below: substring matching, every score 1.0,
        logged at DEBUG. The fallback is still here for a namespace with no iFind index,
        but it now says so at WARNING, because its answers are weaker in a way the caller
        cannot see in the results.

        Note: the kg_TXT stored procedure is NOT used — IRIS registers it as a scalar
        FUNCTION (SQLCODE -51 on CALL, SQLCODE -30 on SELECT … FROM).  Inline SQL is
        the only reliable calling convention from DBAPI.

        Args:
            query_text: Full-text query string.
            k: Maximum number of results.
            min_confidence: Minimum number of occurrences of `query_text` in the document
                (iFind path only). Formerly documented as an iFind rank on a 0-1000 scale
                that nothing ever produced.
            graph: The graph whose documents may answer. `None` means the default graph,
                never every graph — `docs.id` is a node ID since spec 230 (FR-007), and a
                node ID is unique per graph, so an unscoped read returns another graph's
                document under an ID this graph also uses.

        Returns:
            List of (entity_id, score) tuples ordered by descending relevance.
        """
        docs_table = self._t("docs")
        graph_id = DEFAULT_GRAPH if graph is None else graph
        scope = graph_scope_predicate("d.graph_id")
        cursor = self.conn.cursor()
        # Occurrence count of the search string, computed twice because IRIS will not
        # accept a SELECT alias in the same statement's WHERE clause.
        tf = (
            "(CHAR_LENGTH(UPPER(d.text)) - CHAR_LENGTH(REPLACE(UPPER(d.text), UPPER(?), '')))"
            " / CHAR_LENGTH(?)"
        )
        try:
            # iFind path — available when idx_docs_text_ifind exists
            try:
                if min_confidence > 0:
                    cursor.execute(
                        f"SELECT TOP ? d.id, {tf} AS score "
                        f"FROM {docs_table} d "
                        f"WHERE %ID %FIND search_index(idx_docs_text_ifind, ?) "
                        f"AND {scope} "
                        f"AND {tf} >= ? "
                        f"ORDER BY score DESC",
                        [
                            k,
                            query_text,
                            query_text,
                            query_text,
                            graph_id,
                            query_text,
                            query_text,
                            min_confidence,
                        ],
                    )
                else:
                    cursor.execute(
                        f"SELECT TOP ? d.id, {tf} AS score "
                        f"FROM {docs_table} d "
                        f"WHERE %ID %FIND search_index(idx_docs_text_ifind, ?) "
                        f"AND {scope} "
                        f"ORDER BY score DESC",
                        [k, query_text, query_text, query_text, graph_id],
                    )
                results = cursor.fetchall()
                return [(row[0], float(row[1])) for row in results]
            except Exception as ifind_err:
                err = str(ifind_err)
                # -359: no %FIND function. -151: the index named in search_index is not on
                # a table in this statement, i.e. the iFind index was never created.
                if "-359" not in err and "-151" not in err and "FIND" not in err.upper():
                    raise
                # -149 with <CLASS DOES NOT EXIST> is a *different* failure: the index
                # exists and the generated class it is searched through does not,
                # because a package compile deleted it without writing a replacement
                # (spec 230, FR-030). Reporting that as "unavailable" sends the
                # operator off to re-create an index that is already there.
                if "-149" in err or "CLASS DOES NOT EXIST" in err.upper():
                    logger.warning(
                        "kg_TXT: idx_docs_text_ifind is defined but the generated class "
                        "it is searched through is missing (%s) — a package compile "
                        "deleted it. Repair it with initialize_schema(), or "
                        "Do $SYSTEM.OBJ.Compile(\"<owner>\",\"ck\") on the class the "
                        "error names. Falling back to LIKE meanwhile, which matches "
                        "substrings and scores every hit 1.0",
                        ifind_err,
                    )
                else:
                    logger.warning(
                        "kg_TXT: iFind index unavailable (%s) — falling back to LIKE, "
                        "which matches substrings and scores every hit 1.0",
                        ifind_err,
                    )

            # LIKE fallback — no BM25 ranking, score = 1.0 for all matches
            terms = query_text.split()
            if not terms:
                return []
            conditions = " AND ".join(["d.text LIKE ?" for _ in terms])
            like_params = [f"%{t}%" for t in terms]
            cursor.execute(
                f"SELECT TOP {k} d.id, 1.0 AS score "
                f"FROM {docs_table} d "
                f"WHERE {conditions} AND {scope}",
                like_params + [graph_id],
            )
            results = cursor.fetchall()
            return [(row[0], float(row[1])) for row in results]

        except Exception as e:
            logger.error("kg_TXT failed: %s", e)
            raise
        finally:
            cursor.close()
    def kg_NEIGHBORHOOD_EXPANSION(
        self,
        entity_list: List[str],
        expansion_depth: int = 1,
        confidence_threshold: int = 500,
        *,
        graph: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Efficient neighborhood expansion for multiple entities using JSON_TABLE filtering

        Args:
            entity_list: List of seed entity IDs
            expansion_depth: Number of hops to expand (1-3 recommended)
            confidence_threshold: Minimum confidence for edges (0-1000 scale)
            graph: The graph to expand within. `None` means the default graph. A seed ID
                can name a node in several graphs, so an unscoped expansion returns
                neighbours the caller's graph has no edge to (FR-009).

        Returns:
            List of expanded entities with metadata
        """
        if not entity_list:
            return []

        edges_table = self._t("rdf_edges")
        graph_id = DEFAULT_GRAPH if graph is None else graph
        cursor = self.conn.cursor()
        try:
            # Build parameterized query for multiple entities
            entity_placeholders = ",".join(["?" for _ in entity_list])

            # The table was named bare, without the schema prefix, so this only ever
            # resolved for a caller whose default schema happened to be Graph_KG.
            sql = f"""
                SELECT DISTINCT e.s, e.p, e.o_id, jt.confidence
                FROM {edges_table} e,
                     JSON_TABLE(e.qualifiers, '$' COLUMNS(confidence INTEGER PATH '$.confidence')) jt
                WHERE e.s IN ({entity_placeholders}) AND jt.confidence >= ?
                  AND {graph_scope_predicate("e.graph_id")}
                ORDER BY confidence DESC, e.s, e.p
            """

            params = entity_list + [confidence_threshold, graph_id]
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "source": row[0],
                        "predicate": row[1],
                        "target": row[2],
                        "confidence": row[3],
                    }
                )

            return results

        except Exception as e:
            logger.error(f"kg_NEIGHBORHOOD_EXPANSION failed: {e}")
            raise
        finally:
            cursor.close()
    def validate_vector_table(self, table: str, vector_col: str) -> dict:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        schema, tbl = (table.split(".", 1) + [""])[:2] if "." in table else ("", table)
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                [schema or "USER", tbl or table, vector_col],
            )
            row = cursor.fetchone()
            if not row or int(row[0]) == 0:
                raise ValueError(f"Column '{vector_col}' not found in table '{table}'")
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT TOP 1 {vector_col} FROM {table}")
            sample = cursor.fetchone()
            dimension = None
            if sample and sample[0]:
                try:
                    import json

                    v = (
                        json.loads(sample[0])
                        if isinstance(sample[0], str)
                        else sample[0]
                    )
                    dimension = len(v)
                except Exception:
                    pass
            return {
                "table": table,
                "vector_col": vector_col,
                "dimension": dimension,
                "row_count": row_count,
            }
        finally:
            cursor.close()
    def vector_search(
        self,
        table: str,
        vector_col: str,
        query_embedding,
        top_k: int = 10,
        id_col: str = "id",
        return_cols: List[str] = None,
        score_threshold: float = None,
        *,
        graph: Optional[str] = None,
    ) -> List[dict]:
        """Generic single-table vector search, with one rule added by spec 227.

        A routed embedding table (`kg_emb_<hash>`) holds one graph's vectors for one
        model, so searching one without saying which graph is not a question this
        method can answer: defaulting to `''` would return the default graph's rows
        from a table that does not hold them, and dropping the predicate would scan
        the route. It raises instead (FR-041).

        The test is the table *name*, not a registry lookup: the refusal must not
        depend on a database read, or the refusal itself becomes a query that can
        fail open. Every other table keeps its 3.2.0 behaviour — this is also the
        generic any-table helper.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        sanitize_identifier(id_col)
        if return_cols:
            for col in return_cols:
                sanitize_identifier(col)

        bare_table = table.split(".")[-1]
        is_route = bare_table.startswith(ROUTE_TABLE_PREFIX)
        if is_route and graph is None:
            raise ValueError(
                f"{bare_table} is a routed embedding table: it holds one graph's "
                f"vectors for one model, so a search of it needs an explicit "
                f"graph. Pass graph=... — this call is refused rather than "
                f"defaulted, because the default graph's vectors are not in this "
                f"table and an unscoped scan would cross graphs."
            )

        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        extra = ", ".join(
            sanitize_identifier(c) for c in (return_cols or []) if c != id_col
        )

        dim = None
        if isinstance(query_embedding, list):
            dim = len(query_embedding)
        elif isinstance(query_embedding, str):
            dim = query_embedding.count(",") + 1

        if dim:
            query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"
        else:
            query_cast = f"TO_VECTOR(?, {self.vector_dtype})"

        select_cols = (
            f"t.{id_col}, VECTOR_COSINE(t.{vector_col}, {query_cast}) AS score"
        )
        if extra:
            select_cols += f", {extra}"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        # The graph predicate is emitted only when a graph was given. A table with no
        # graph_id column — docs, fhir_bridges, anything a caller points this at —
        # would fail to compile with one, and 227 does not widen its own scope to
        # every table in the namespace.
        params = [query_vec_str]
        where = ""
        if graph is not None:
            where = f"WHERE {graph_scope_predicate('t.graph_id')} "
            params.append(graph)
        sql = (
            f"SELECT TOP {int(top_k)} {select_cols} "
            f"FROM {table} t "
            f"{where}"
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            cols = [d[0].lower() for d in cursor.description]
            results = []
            for row in cursor.fetchall():
                r = dict(zip(cols, row))
                r["id"] = r.pop(id_col.lower(), r.get("id"))
                results.append(r)
            return results
        except Exception as ex:
            raise ValueError(
                f"vector_search failed on {table}.{vector_col}: {ex}. "
                f"Ensure the column is a VECTOR type and query_embedding has the correct dimension."
            ) from ex
        finally:
            cursor.close()
    def multi_vector_search(
        self,
        sources: List[dict],
        query_embedding,
        top_k: int = 10,
        fusion: str = "rrf",
        rrf_k: int = 60,
        *,
        graph: Optional[str] = None,
    ) -> List[dict]:
        """Fuse several vector columns, all within one graph.

        `graph` reaches every source, and a per-source `graph` key overrides it for
        that source alone. Without it a routed table (`kg_emb_*`) is unreachable:
        `vector_search` refuses one by name when no graph is given (FR-041), the
        refusal is caught by the per-source `except` below, and the source
        contributes nothing to the fusion. Passing the graph is what makes routed
        vectors fusable at all (FR-013).
        """
        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        per_source_k = top_k * 2

        all_results: List[dict] = []
        for source in sources:
            tbl = source["table"]
            col = source.get("col") or source.get("vector_col", "emb")
            id_c = source.get("id_col", "id")
            weight = float(source.get("weight", 1.0))
            return_c = source.get("return_cols")
            source_graph = source.get("graph", graph)
            try:
                rows = self.vector_search(
                    table=tbl,
                    vector_col=col,
                    query_embedding=query_vec_str,
                    top_k=per_source_k,
                    id_col=id_c,
                    return_cols=return_c,
                    graph=source_graph,
                )
                for i, r in enumerate(rows):
                    r["source_table"] = tbl
                    r["_rank"] = i + 1
                    r["_weight"] = weight
                all_results.extend(rows)
            except Exception as ex:
                logger.warning(f"multi_vector_search: skipping {tbl}: {ex}")

        if not all_results:
            return []

        if fusion == "rrf":
            scores: Dict[str, float] = {}
            meta: Dict[str, dict] = {}
            for r in all_results:
                node_id = str(r["id"])
                weight = r["_weight"]
                rank = r["_rank"]
                rrf_score = weight * (1.0 / (rrf_k + rank))
                scores[node_id] = scores.get(node_id, 0.0) + rrf_score
                if node_id not in meta:
                    meta[node_id] = {
                        k: v for k, v in r.items() if not k.startswith("_")
                    }
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            results = []
            for rank_i, (node_id, score) in enumerate(ranked, 1):
                row = meta[node_id].copy()
                row["score"] = round(score, 6)
                row["rank"] = rank_i
                results.append(row)
            return results
        else:
            seen: set = set()
            merged = []
            for r in sorted(all_results, key=lambda x: x.get("score", 0), reverse=True):
                nid = str(r["id"])
                if nid not in seen:
                    seen.add(nid)
                    clean = {k: v for k, v in r.items() if not k.startswith("_")}
                    merged.append(clean)
                    if len(merged) >= top_k:
                        break
            return merged
    def kg_RRF_FUSE(
        self,
        k: int,
        k1: int,
        k2: int,
        c: int,
        query_vector: str,
        query_text: str,
        graph: Optional[str] = None,
    ) -> List[Tuple[str, float, float, float]]:
        """Client-side RRF over a vector leg and a BM25 leg.

        `graph` scopes every leg, matching the generated `kg_RRF_FUSE` procedure,
        which takes `graphId` in 4.0.0 (FR-023). `None` means the default graph,
        never every graph.

        An IVF or BM25 index covers the one graph it was built for since 4.0.0
        (FR-009), so this picks the index built for `graph` rather than the first one
        of its type in the registry. An index whose graph cannot be determined — one
        built by another process, or before 4.0.0 — is used only when nothing built
        for `graph` exists, and that is logged at WARNING, because its answers may
        come from a graph the caller did not ask about.

        A graph ID is collision avoidance, not an authorisation boundary: it is
        supplied by the caller, so it cannot decide what that caller may read
        (FR-032).
        """
        vec_results: List[Tuple[str, float]] = []
        txt_results: List[Tuple[str, float]] = []

        import json as _json
        vec_list = _json.loads(query_vector) if isinstance(query_vector, str) else query_vector
        graph_id = DEFAULT_GRAPH if graph is None else graph

        try:
            vec_name, vec_type = self._fusion_leg_index(("ivf", "hnsw"), graph_id)
            if vec_type == "ivf":
                raw = self.ivf_search(vec_name, vec_list, k=k1)
                vec_results = [(r["id"], float(r.get("score", 0))) for r in raw]
            elif vec_type == "hnsw":
                # The HNSW leg is scoped by the query, not by the index, so it needs
                # no recorded graph of its own.
                vec_results = self.kg_KNN_VEC(query_vector, k=k1, graph=graph)
            if query_text:  # skip BM25 leg when no text query provided
                txt_name, _txt_type = self._fusion_leg_index(("bm25",), graph_id)
                if txt_name is not None:
                    txt_results = self.bm25_search(txt_name, query_text, k=k2)
        except Exception as e:
            logger.error(f"kg_RRF_FUSE index search failed: {e}")

        vec_rank = {nid: i + 1 for i, (nid, _) in enumerate(vec_results)}
        txt_rank = {nid: i + 1 for i, (nid, _) in enumerate(txt_results)}
        all_ids = set(vec_rank) | set(txt_rank)

        fused = []
        for nid in all_ids:
            v_r = vec_rank.get(nid, len(vec_results) + c)
            t_r = txt_rank.get(nid, len(txt_results) + c)
            rrf = 1.0 / (c + v_r) + 1.0 / (c + t_r)
            v_score = dict(vec_results).get(nid, 0.0)
            t_score = dict(txt_results).get(nid, 0.0)
            fused.append((nid, rrf, v_score, t_score))

        fused.sort(key=lambda x: -x[1])
        return fused[:k]
    def kg_VECTOR_GRAPH_SEARCH(
        self,
        query_vector: str,
        query_text: str = None,
        k: int = 15,
        expansion_depth: int = 1,
        min_confidence: float = 0.5,
        *,
        graph: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Multi-modal search combining vector similarity, graph expansion, and text relevance

        Args:
            query_vector: Vector query as JSON string
            query_text: Optional text query
            k: Number of final results
            expansion_depth: Graph expansion depth
            min_confidence: Minimum confidence threshold
            graph: Named graph all three steps answer from. `None` is the default
                graph, never every graph (FR-009).

        Returns:
            List of ranked entities with combined scores
        """
        try:
            # Step 1: Vector search for semantic similarity
            k_vector = min(k * 2, 50)  # Get more candidates for fusion
            vector_results = self.kg_KNN_VEC(query_vector, k=k_vector, graph=graph)
            vector_entities = [entity_id for entity_id, _ in vector_results]

            # Step 2: Graph expansion around vector results
            if vector_entities:
                graph_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
                    vector_entities,
                    expansion_depth,
                    int(min_confidence * 1000),
                    graph=graph,
                )
                expanded_entities = list(
                    set([item["target"] for item in graph_expansion])
                )
            else:
                expanded_entities = []

            # Step 3: Combine with text search if provided
            if query_text:
                # No occurrence floor: `min_confidence` here is an edge-confidence
                # fraction for the graph-expansion step above, and scaling it by 1000 into
                # kg_TXT's threshold asked for 500 occurrences of the search string —
                # which since kg_TXT's iFind path started working would return nothing.
                text_results = self.kg_TXT(query_text, k=k_vector * 2, graph=graph)
                text_entities = [entity_id for entity_id, _ in text_results]
                all_entities = list(
                    set(vector_entities + expanded_entities + text_entities)
                )
            else:
                all_entities = list(set(vector_entities + expanded_entities))

            # Step 4: Score combination (simplified)
            combined_results = []
            for entity_id in all_entities[:k]:
                # Get scores from different sources
                vector_sim = next(
                    (score for eid, score in vector_results if eid == entity_id), 0.0
                )

                # Simple weighted combination
                combined_score = (
                    vector_sim  # Can be enhanced with graph centrality, text relevance
                )

                combined_results.append(
                    {
                        "entity_id": entity_id,
                        "combined_score": combined_score,
                        "vector_similarity": vector_sim,
                        "in_graph_expansion": entity_id in expanded_entities,
                    }
                )

            # Sort by combined score
            combined_results.sort(key=lambda x: x["combined_score"], reverse=True)
            return combined_results[:k]

        except Exception as e:
            logger.error(f"kg_VECTOR_GRAPH_SEARCH failed: {e}")
            raise
    def vec_create_index(
        self,
        name: str,
        dim: int,
        metric: str = "cosine",
        num_trees: int = 4,
        leaf_size: int = 50,
    ) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex",
            "Create",
            name,
            str(dim),
            metric,
            str(num_trees),
            str(leaf_size),
        )
        info = json.loads(str(result))
        self._index_registry[name] = "vec"
        return info
    def vec_insert(self, index_name: str, doc_id: str, embedding) -> None:
        vec_json = json.dumps([float(v) for v in embedding])
        self._iris_obj().classMethodVoid(
            "Graph.KG.VecIndex", "InsertJSON", index_name, doc_id, vec_json
        )
    def vec_bulk_insert(self, index_name: str, items: list) -> int:
        batch = [
            {"id": item["id"], "vec": [float(v) for v in item["embedding"]]}
            for item in items
        ]
        batch_json = json.dumps(batch)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "InsertBatchJSON", index_name, batch_json
        )
        return json.loads(str(result)).get("inserted", 0)
    def vec_build(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Build", index_name
        )
        return json.loads(str(result))
    def vec_search(
        self, index_name: str, query_embedding, k: int = 10, nprobe: int = 8
    ) -> list:
        vec_json = json.dumps([float(v) for v in query_embedding])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchJSON", index_name, vec_json, k, nprobe
        )
        return json.loads(str(result))
    def vec_search_multi(
        self, index_name: str, query_embeddings: list, k: int = 10, nprobe: int = 8
    ) -> list:
        queries_json = json.dumps([[float(v) for v in q] for q in query_embeddings])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchMultiJSON", index_name, queries_json, k, nprobe
        )
        return json.loads(str(result))
    def vec_info(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Info", index_name
        )
        info = json.loads(str(result))
        info.setdefault("type", "vec")
        return info
    def vec_drop(self, index_name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.VecIndex", "Drop", index_name)
    def vec_expand(self, index_name: str, seed_id: str, k: int = 5) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SeededVectorExpand", seed_id, index_name, k
        )
        return json.loads(str(result))
    def plaid_build(
        self, name: str, docs: list, n_clusters: int = None, dim: int = 128
    ) -> dict:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError:
            raise ImportError(
                "plaid_build requires numpy and sklearn: pip install numpy scikit-learn"
            )

        all_tokens = []
        doc_token_map = []
        for doc in docs:
            tokens = doc["tokens"]
            for tok_pos, tok in enumerate(tokens):
                all_tokens.append(tok)
                doc_token_map.append(
                    {"docId": doc["id"], "tokPos": tok_pos, "centroid": 0}
                )

        all_tokens_np = np.array(all_tokens, dtype=np.float64)
        K = n_clusters or max(1, int(np.sqrt(len(all_tokens_np))))
        K = min(K, len(all_tokens_np))

        kmeans = KMeans(n_clusters=K, n_init=1, max_iter=20, random_state=42).fit(
            all_tokens_np
        )
        labels = kmeans.labels_.tolist()

        for i, label in enumerate(labels):
            doc_token_map[i]["centroid"] = int(label)

        centroids_json = json.dumps(kmeans.cluster_centers_.tolist())
        docs_json = json.dumps([
            {
                "id": doc["id"],
                "tokens": [[float(v) for v in tok] for tok in doc["tokens"]],
            }
            for doc in docs
        ])
        assignments_json = json.dumps(doc_token_map)

        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Build", name,
            centroids_json, docs_json, assignments_json
        )
        info = json.loads(str(result))
        self._index_registry[name] = "plaid"
        return info
    def plaid_search(
        self, name: str, query_tokens: list, k: int = 10, nprobe: int = 4
    ) -> list:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in query_tokens])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Search", name, tokens_json, k, nprobe
        )
        return json.loads(str(result))
    def plaid_insert(self, name: str, doc_id: str, token_embeddings: list) -> None:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in token_embeddings])
        self._iris_obj().classMethodVoid(
            "Graph.KG.PLAIDSearch", "Insert", name, doc_id, tokens_json
        )
    def plaid_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.PLAIDSearch", "Info", name)
        return json.loads(str(result))
    def plaid_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.PLAIDSearch", "Drop", name)
    def bm25_build(
        self,
        name: str,
        text_props: list,
        k1: float = 1.5,
        b: float = 0.75,
        *,
        graph: Optional[str] = None,
    ) -> dict:
        """Build a BM25 index over one graph's nodes (FR-009).

        ``graph`` is keyword-only so a 3.2.0 positional call cannot bind a graph to
        ``k1``. ``graph=None`` is the default graph ``''``, never every graph: before
        4.0.0 ``Graph.KG.BM25Index.Build`` read ``Graph_KG.nodes`` with no graph
        predicate, so one index held every graph's nodes and the same node ID in two
        graphs was accumulated into a single document.
        """
        BM25BuildInput(name=name, text_props=text_props, k1=k1, b=b)
        props_csv = ",".join(text_props)
        graph_id = DEFAULT_GRAPH if graph is None else graph
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Build", name, props_csv, k1, b, graph_id
        )
        info = json.loads(str(result))
        self._index_registry[name] = "bm25"
        self._index_graph_scopes[name] = graph_id
        return info
    def bm25_search(self, name: str, query: str, k: int = 10) -> list:
        BM25SearchInput(name=name, query=query, k=k)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Search", name, query, k
        )
        import re as _re
        raw = str(result)
        raw = _re.sub(r'(?<=[:\[,])(\.\d)', r'0\1', raw)
        rows = json.loads(raw)
        return [(r["id"], float(r["score"])) for r in rows]
    def bm25_insert(self, name: str, doc_id: str, text: str) -> bool:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Insert", name, doc_id, text
        )
        return bool(int(str(result)))
    def bm25_delete(self, name: str, doc_id: str) -> bool:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Delete", name, doc_id
        )
        return bool(int(str(result)))

    def bm25_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.BM25Index", "Drop", name)
    def bm25_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.BM25Index", "Info", name)
        info = json.loads(str(result))
        info.setdefault("type", "bm25")
        return info
    def ivf_build(
        self,
        name: str,
        nlist: int = 256,
        metric: str = "cosine",
        batch_size: int = 10000,
        build_batch_size: int = 500,
        node_ids: Optional[List[str]] = None,
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> dict:
        """Build an IVF index over one graph's vectors in one routed table (spec 227).

        ``graph`` and ``model_key`` are keyword-only so a 3.2.0 positional call
        cannot bind a graph to ``node_ids``. ``graph=None`` is the default graph
        ``''``, never every graph: an index built from two graphs' vectors answers
        every search with the other graph's neighbours mixed in, and nothing in the
        result says so.

        Before 4.0.0 this read ``SELECT id, emb`` from a hardcoded
        ``kg_NodeEmbeddings``. The embedding tables are keyed ``(graph_id,
        node_id)`` now and have no ``id`` column — but IRIS still resolves a bare
        ``id`` on a DDL-created table as its implicit row ID, so the statement
        prepared, compared row IDs to node IDs, matched nothing, and reported "no
        vectors found" about a table full of vectors.
        """
        IVFBuildInput(name=name, nlist=nlist, metric=metric, batch_size=batch_size, build_batch_size=build_batch_size)
        import sys as _sys
        try:
            import numpy as np
            from sklearn.cluster import MiniBatchKMeans
        except ImportError as _ie:
            _err = str(_ie)
            if "more than once" in _err or "cannot load" in _err.lower():
                np = _sys.modules.get("numpy")
                _sklearn_cluster = _sys.modules.get("sklearn.cluster")
                MiniBatchKMeans = getattr(_sklearn_cluster, "MiniBatchKMeans", None) if _sklearn_cluster else None
                if np is None or MiniBatchKMeans is None:
                    raise ImportError(
                        "ivf_build requires numpy and sklearn: pip install numpy scikit-learn"
                    )
            else:
                raise ImportError(
                    "ivf_build requires numpy and sklearn: pip install numpy scikit-learn"
                )

        import base64
        import json as _json
        import struct

        graph_id = DEFAULT_GRAPH if graph is None else graph
        table, _route = self._route_for_read(graph_id, model_key)
        if table is None:
            raise ValueError(
                f"ivf_build: no embedding table is routed for graph {graph_id!r}"
                f" and model {model_key!r}, so there is no route to build from."
                " Store an embedding for that pair first; building from another"
                " model's table would index vectors from a different space."
            )

        cursor = self.conn.cursor()
        emb_table = self._t(table)
        scope = graph_scope_predicate("graph_id")
        if node_ids is not None:
            if not node_ids:
                raise ValueError("ivf_build: node_ids list is empty")
            placeholders = ",".join(["?"] * len(node_ids))
            cursor.execute(
                f"SELECT node_id, emb FROM {emb_table}"
                f" WHERE {scope} AND node_id IN ({placeholders})",
                [graph_id] + list(node_ids),
            )
        else:
            cursor.execute(
                f"SELECT node_id, emb FROM {emb_table} WHERE {scope}", [graph_id]
            )
        rows = cursor.fetchall()
        if not rows:
            raise ValueError(
                f"ivf_build: no vectors found in {emb_table} for graph {graph_id!r}"
            )

        node_ids = []
        vecs = []
        for row in rows:
            nid, emb_val = row[0], row[1]
            if emb_val is None:
                continue
            emb_str = str(emb_val)
            if "," in emb_str:
                vec = [float(v) for v in emb_str.split(",")]
            else:
                raw = base64.b64decode(emb_str)
                dim = len(raw) // 4
                vec = list(struct.unpack(f"{dim}f", raw))
            node_ids.append(nid)
            vecs.append(vec)

        X = np.array(vecs, dtype=np.float32)
        n_nodes, dim = X.shape
        effective_nlist = min(nlist, n_nodes)

        km = MiniBatchKMeans(
            n_clusters=effective_nlist,
            batch_size=batch_size,
            random_state=42,
            n_init=3,
        ).fit(X)

        centroids = km.cluster_centers_.tolist()
        labels = km.labels_.tolist()

        iris_obj = self._iris_obj()

        result = iris_obj.classMethodValue(
            "Graph.KG.IVFIndex",
            "Build",
            name,
            _json.dumps(effective_nlist),
            _json.dumps(metric),
            _json.dumps(centroids),
            "[]",
        )

        for batch_start in range(0, n_nodes, build_batch_size):
            batch = []
            for i in range(batch_start, min(batch_start + build_batch_size, n_nodes)):
                batch.append(
                    {"nodeId": node_ids[i], "cellIdx": int(labels[i]), "vec": vecs[i]}
                )
            iris_obj.classMethodValue(
                "Graph.KG.IVFIndex", "AddBatch", name, _json.dumps(batch)
            )

        iris_obj.classMethodValue("Graph.KG.IVFIndex", "FinalizeIndex", name)
        info = iris_obj.classMethodValue("Graph.KG.IVFIndex", "Info", name)
        result = _json.loads(str(info))
        self._index_registry[name] = "ivf"
        self._index_graph_scopes[name] = graph_id
        return result
    def ivf_search(self, name: str, query: list, k: int = 10, nprobe: int = 8) -> list:
        VectorSearchInput(name=name, query=query, k=k, nprobe=nprobe)
        query_json = json.dumps([float(v) for v in query])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Search", name, query_json, k, nprobe
        )
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]
    def ivf_insert(self, name: str, node_id: str, vector: list) -> int:
        vec_json = json.dumps([float(v) for v in vector])
        cell = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Insert", name, node_id, vec_json
        ))
        if cell < 0:
            raise ValueError(f"ivf_insert: index '{name}' not found — call ivf_build first")
        return cell
    def ivf_delete(self, name: str, node_id: str) -> bool:
        removed = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Delete", name, node_id
        ))
        return bool(removed)
    def ivf_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.IVFIndex", "Drop", name)
    def ivf_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.IVFIndex", "Info", name)
        info = json.loads(str(result))
        if info:
            info.setdefault("type", "ivf")
        return info
