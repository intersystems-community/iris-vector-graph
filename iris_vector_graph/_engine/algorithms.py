import json
import os
import logging
import re
from typing import Dict, Any, List, Optional, Tuple, Callable


logger = logging.getLogger(__name__)


def _exclude_entry(entry) -> Tuple[Optional[str], str]:
    """``"p"`` -> (None, p); ``"Type.p"`` -> (Type, p). Anything else raises."""
    if not isinstance(entry, str):
        raise ValueError(f"exclude_predicates entries must be strings, got {entry!r}")
    parts = entry.split(".")
    if len(parts) > 2 or any(not x for x in parts):
        raise ValueError(f"exclude_predicates entry must be 'p' or 'Type.p', got {entry!r}")
    return (None, parts[0]) if len(parts) == 1 else (parts[0], parts[1])


def _exclude_matches(entry: str, source_key: str, p: str) -> bool:
    """Whether the edge ``source_key -p->`` is excluded by ``entry`` (spec 235): a
    bare predicate matches every source, ``Type.p`` only a source key starting
    ``Type/``."""
    rtype, pred = _exclude_entry(entry)
    if pred != p:
        return False
    return rtype is None or source_key.startswith(rtype + "/")


def _check_exclusions(exclude_predicates) -> Optional[List[str]]:
    if exclude_predicates is None:
        return None
    if isinstance(exclude_predicates, str):
        raise ValueError("exclude_predicates must be a list of strings, not a string")
    out = list(exclude_predicates)
    for entry in out:
        _exclude_entry(entry)
    return out


def _raise_store_error(method: str, result) -> None:
    """Raise the error the store's path reported (4.6.0 R105). The community methods answered ``[]`` instead, so
    a failure read as a graph with no communities, triangles or cores."""
    if result.error:
        raise RuntimeError(f"{method} failed: {result.error}")


#: The global and first subscript each read writes as its work area, for `_refused_work_area`.
_WORK_AREAS = {
    "kg_PERSONALIZED_PAGERANK": ("^KG", "degc"),  # Graph.KG.Adj.Degree's degree cache, filled by RunJson's walk
    "kg_GRAPH_WALK": ("^ArnoKG", "bfs_r"),  # BFSFastJsonSorted stages the walk's rows there
}
#: The node a write probe sets and kills at once: outside every subscript IVG reads.
_WRITE_PROBE = "ivg_write_probe"


def _refused_work_area(engine, operation: str, error: str) -> Optional[str]:
    """The message for `operation`'s failure when it is a work-area write this login may not make, else None.

    The Native API reports a <PROTECT> inside a class method as a bare ``<THROW>`` (or ``<PROTECT>``), naming
    neither the global nor the privilege (4.6.0 R244: a reader at database ``R`` read just that). On such an error
    this asks whether the login may write the global at all: it sets a probe node and kills it at once, and a refusal
    of that is the cause. Any other error keeps its own message.
    """
    if "<THROW>" not in error and "<PROTECT>" not in error:
        return None
    gname, sub = _WORK_AREAS[operation]
    probe = (_WRITE_PROBE, os.getpid())
    try:
        iris_obj = engine._iris_obj()
    except Exception:  # noqa: BLE001 - no Native API here: nothing to ask, the error stays as it was
        return None
    try:
        iris_obj.set(1, gname, *probe)
    except Exception as exc:  # noqa: BLE001 - only a <PROTECT> names the cause; anything else is not ours to explain
        if "<PROTECT>" not in str(exc):
            return None
        return (
            f"{operation} failed: it writes a work area, {gname}(\"{sub}\"), and IRIS refused this login a write to "
            f"the namespace's globals database (<PROTECT>). It needs the namespace's database resource at RW (ADMIN_GUIDE "
            f"§3, the 'reading, fully' row); with it at R this read cannot run. ({error})"
        )
    iris_obj.kill(gname, *probe)
    return None


#: RunJson's pTol default, and kg_PERSONALIZED_PAGERANK's.
DEFAULT_PPR_TOLERANCE = 1e-6


def _runjson_tail(exclude: Optional[List[str]], limit: int, tolerance: float) -> tuple:
    """RunJson's arguments after ``pGraph``: none for the default request, so the call is the pre-235 call that
    classes from before 4.6.0 accept; else ``pExclude`` and ``pLimit`` (spec 235; RunJson's own defaults, nothing
    excluded and 1000 rows, when only a tolerance was asked for), then ``pTol`` when it is not RunJson's 1e-6
    (4.6.0 R270)."""
    custom_tol = float(tolerance) != DEFAULT_PPR_TOLERANCE
    if exclude is None and not custom_tol:
        return ()
    tail = ("", 1000) if exclude is None else (json.dumps(exclude), limit)
    if custom_tol:
        tail += (tolerance,)
    return tail


#: %SYS.DBSRV's errors for a call whose class or method is not compiled, naming Graph.KG.PageRank / RunJson as the
#: thing missing. The same error raised inside the walk names the class the walk needed, not these.
_RUNJSON_MISSING = re.compile(
    r"<CLASS DOES NOT EXIST>.*\*Graph\.KG\.PageRank(?![\w.])|<METHOD DOES NOT EXIST>.*\*RunJson,Graph\.KG\.PageRank(?![\w.])"
)


def _runjson_missing(exc: BaseException) -> bool:
    """``Graph.KG.PageRank.RunJson`` is not there to call: the one failure the PPR fast path falls back on."""
    return bool(_RUNJSON_MISSING.search(str(exc)))


def _runjson_failure(exc: BaseException) -> str:
    message = f"kg_PERSONALIZED_PAGERANK: Graph.KG.PageRank.RunJson failed: {exc}"
    if re.match(r"<PARAMETER>RunJson\^", str(exc)):
        message += (
            ". The RunJson compiled in this namespace does not take the arguments this IVG sends (an exclusion or a "
            "tolerance other than 1e-6): its classes predate this IVG. Run initialize_schema() to deploy them"
        )
    return message


class AlgorithmsMixin:
    def _named_graph_with_edges(self) -> Optional[str]:
        """A graph other than the default one with an edge in ``^KG("out")``, or None. The Cypher procedures that
        read ``^NKG`` raise when there is one, since ``^NKG`` then merges its edges with the default graph's
        (4.6.0 R184)."""
        from iris_vector_graph.stores.lazy_kg import named_graph_with_edges

        return named_graph_with_edges(self.conn)

    def _graphs_with_edges(self) -> List[str]:
        """Every graph with an edge in ``^KG("out")`` (``""``: the default graph). A Cypher procedure without
        ``USE GRAPH`` answers the union of these graphs' own answers (4.6.0 R192)."""
        from iris_vector_graph.stores.lazy_kg import graphs_with_edges

        return graphs_with_edges(self.conn)

    def kg_PERSONALIZED_PAGERANK(
        self,
        seed_entities: List[str],
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        return_top_k: Optional[int] = None,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
        *,
        graph: Optional[str] = None,
        exclude_predicates: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """
        Personalized PageRank from ``seed_entities``, as ``{node_id: score}``: networkx's
        ``pagerank(G, alpha=damping_factor, personalization={seed: 1, ...}, tol=tolerance)``, G the graph as a
        multigraph (an edge under each predicate counts, as does each keyed edge of a multigraph-mode triple). At
        each step the walk restarts at the seeds (evenly) with probability ``1 - damping_factor`` and otherwise
        follows an out-edge chosen evenly; a node with no out-edges hands its score to the seeds, as networkx does,
        so the scores sum to 1. It stops after ``max_iterations`` or once the scores' summed change in an iteration
        is below N * ``tolerance``, N the nodes holding a score (networkx's rule, whose N is every node of the
        graph). Every route gives this answer: the server walk (``Graph.KG.PageRank.RunJson``) and the Python
        fallback. Before 4.6.0 (R270) both passed a dangling node's score nowhere, so the scores summed to less than
        1, and the server walk stopped once no score moved by 1e-4 whatever ``tolerance`` said.

        Implements personalized PageRank biased toward seed entities, with optional
        reverse edge traversal for enhanced multi-hop reasoning in knowledge graphs.

        Architecture: Python API -> SQL Function -> ObjectScript Embedded Python
        Falls back to pure Python if SQL function is unavailable.

        Args:
            seed_entities: List of entity IDs to use as seeds (personalization)
            damping_factor: PageRank damping factor (default 0.85)
            max_iterations: Maximum iterations before stopping (default 100)
            tolerance: Convergence threshold (default 1e-6)
            return_top_k: Limit results to top K entities (None = all)
            bidirectional: Enable reverse edge traversal (default False)
            reverse_edge_weight: Weight multiplier for reverse edges (default 1.0)
            graph: The one graph to walk. ``None`` or ``""`` is the default graph, never
                every graph. A seed outside it scores nothing.
            exclude_predicates: Edges left out of both the walk and every divisor, as
                ``"p"`` (any source) or ``"Type.p"`` (source key starts ``Type/``).
                ``None`` is the pre-235 walk, unchanged; a list, even empty, also lifts
                the 1000-row cap of the ObjectScript path (spec 235).

        Returns:
            Dict mapping entity_id to PageRank score

        Raises:
            ValueError: If reverse_edge_weight is negative
            ValueError: If seed_entities is empty

        Note:
            Uses IRIS embedded Python for 10-50x performance (10-50ms for 10K nodes).
            Falls back to pure Python if SQL function unavailable.
        """
        # Input validation
        if reverse_edge_weight < 0:
            raise ValueError(
                f"reverse_edge_weight must be non-negative, got: {reverse_edge_weight}"
            )
        if not seed_entities:
            raise ValueError("seed_entities must contain at least one entity")
        from iris_vector_graph._validate import validate_graph_name

        graph_name = validate_graph_name(graph)
        exclude = _check_exclusions(exclude_predicates)
        # Sent only for a named graph, so a store predating `graph` keeps serving the
        # default graph; for a named graph its `TypeError` drops to the paths below.
        graph_kw = {"graph": graph_name} if graph_name else {}
        # Likewise the exclusion: None sends nothing, so the call is the pre-235 call.
        if exclude is not None:
            graph_kw.update(exclude=exclude, limit=return_top_k or 0)
        # And the tolerance, only when not the default every store shares (4.6.0 R270).
        if float(tolerance) != DEFAULT_PPR_TOLERANCE:
            graph_kw["tolerance"] = tolerance

        if self._store_capabilities.get("ppr", True):
            # `bidirectional` and `reverse_edge_weight` go to the store, not just to the
            # fallback below. Without them this call answered every bidirectional request
            # with forward-only scores: the store returned no error, so the two paths that
            # do honour reverse edges — `Graph.KG.PageRank.RunJson`'s `bidir`/`revWeight`
            # arguments and `_kg_PERSONALIZED_PAGERANK_python_fallback` — were unreachable.
            #
            # A store predating these keywords raises `TypeError`; that is the correct
            # outcome, because dropping to the fallback computes the reverse half rather
            # than quietly omitting it.
            try:
                result = self._store.execute_ppr(
                    seed_entities,
                    damping_factor,
                    max_iterations,
                    bidirectional=bidirectional,
                    reverse_edge_weight=reverse_edge_weight,
                    **graph_kw,
                )
            except TypeError as exc:
                logger.warning(
                    "store.execute_ppr does not accept bidirectional/reverse_edge_weight/"
                    "graph/tolerance (%s); computing elsewhere so nothing the caller asked for is "
                    "dropped",
                    exc,
                )
                result = None
            if result is not None:
                # The store ran the Owner (Graph.KG.PageRank.RunJson, after any Native Reader). Its failure is
                # raised (4.6.0 R127): running RunJson again below fails the same way, and the SQL fallback then
                # answered in its place, or {} where it read no node, so the failure never showed.
                if result.error:
                    refused = _refused_work_area(self, "kg_PERSONALIZED_PAGERANK", str(result.error))
                    if refused:
                        raise RuntimeError(refused)
                _raise_store_error("kg_PERSONALIZED_PAGERANK", result)
                top_k = return_top_k
                rows = result.rows
                if top_k:
                    rows = rows[:top_k]
                return {r[0]: float(r[1]) for r in rows if len(r) >= 2}

        # --- Fast path: Graph.KG.PageRank.RunJson() via .cls layer ---
        if self.capabilities.objectscript_deployed and self.capabilities.kg_built:
            try:
                seed_json = json.dumps(seed_entities)
                iris_obj = self._iris_obj()
                result_json = iris_obj.classMethodValue(
                    "Graph.KG.PageRank",
                    "RunJson",
                    seed_json,
                    damping_factor,
                    max_iterations,
                    1 if bidirectional else 0,
                    reverse_edge_weight,
                    graph_name,
                    *_runjson_tail(exclude, return_top_k or 0, tolerance),
                )
                if result_json:
                    items = json.loads(str(result_json))
                    scores = {
                        item["id"]: item["score"]
                        for item in items
                        if item.get("score", 0) > 0
                    }
                    if return_top_k is not None and return_top_k > 0:
                        scores = dict(
                            sorted(scores.items(), key=lambda x: x[1], reverse=True)[
                                :return_top_k
                            ]
                        )
                    logger.debug(
                        "PageRank via Graph.KG.PageRank.RunJson(): %d results",
                        len(scores),
                    )
                    return scores
            except Exception as exc:
                # Only RunJson not being there to call is a reason to compute in Python (4.6.0 R276). Every
                # other error was logged and answered from the fallback, which hid a <PARAMETER> from a RunJson
                # older than the arguments sent and any failure of the walk; the store's route raises on them.
                if not _runjson_missing(exc):
                    raise RuntimeError(_runjson_failure(exc)) from exc
                logger.warning(
                    "Graph.KG.PageRank.RunJson is not compiled in this namespace (%s); computing in Python", exc
                )

        return self._kg_PERSONALIZED_PAGERANK_python_fallback(
            seed_entities,
            damping_factor,
            max_iterations,
            tolerance,
            return_top_k,
            bidirectional,
            reverse_edge_weight,
            graph=graph_name,
            exclude_predicates=exclude,
        )

    def _kg_PERSONALIZED_PAGERANK_python_fallback(
        self,
        seed_entities: List[str],
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        return_top_k: Optional[int] = None,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
        *,
        graph: Optional[str] = None,
        exclude_predicates: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """
        Pure Python fallback for Personalized PageRank.

        Used when IRIS SQL function kg_PPR is unavailable.
        Performance: ~25ms for 1K nodes (vs 2-5ms with embedded Python).
        """
        _t = self._t

        from iris_vector_graph._validate import validate_graph_name

        # One graph, always. Until 4.0.1 these reads had no graph predicate, so the
        # fallback walked every graph's edges at once (spec 231, FR-016).
        graph_id = validate_graph_name(graph)
        exclude = _check_exclusions(exclude_predicates)
        cursor = self.conn.cursor()
        try:
            # Step 1: Get all nodes
            cursor.execute(f"SELECT node_id FROM {_t('nodes')} WHERE graph_id = ?", [graph_id])
            nodes = [row[0] for row in cursor.fetchall()]
            num_nodes = len(nodes)

            if num_nodes == 0:
                return {}

            node_set = set(nodes)
            valid_seeds = [s for s in seed_entities if s in node_set]
            if not valid_seeds:
                # No valid seeds found - return empty
                logger.warning(f"No valid seeds found in graph: {seed_entities}")
                return {}

            # Step 2: Build adjacency lists. With an exclusion the edges are read once
            # with `p`, filtered, and the survivors serve both directions; without one
            # the reads are the pre-235 reads.
            kept = None
            if exclude:
                cursor.execute(
                    f"SELECT s, p, o_id FROM {_t('rdf_edges')} WHERE graph_id = ?", [graph_id]
                )
                kept = [
                    (s, o)
                    for s, p, o in cursor.fetchall()
                    if not any(_exclude_matches(e, s, p) for e in exclude)
                ]
                forward = kept
            else:
                cursor.execute(
                    f"SELECT s, o_id FROM {_t('rdf_edges')} WHERE graph_id = ?", [graph_id]
                )
                forward = cursor.fetchall()

            in_edges = {}  # target -> [(source, weight)]
            out_degree = {}

            for src, dst in forward:
                # Forward edge: weight = 1.0
                if dst not in in_edges:
                    in_edges[dst] = []
                in_edges[dst].append((src, 1.0))
                out_degree[src] = out_degree.get(src, 0) + 1

            # Step 2b: Build reverse edges if bidirectional mode enabled
            if bidirectional and reverse_edge_weight > 0:
                if kept is not None:
                    reverse = [(o, s) for s, o in kept]
                else:
                    cursor.execute(
                        f"SELECT o_id, s FROM {_t('rdf_edges')} WHERE graph_id = ?", [graph_id]
                    )
                    reverse = cursor.fetchall()
                for o_id, s in reverse:
                    # Reverse edge: o_id -> s with weighted contribution
                    if s not in in_edges:
                        in_edges[s] = []
                    in_edges[s].append((o_id, reverse_edge_weight))
                    out_degree[o_id] = out_degree.get(o_id, 0) + 1

            # Initialize out_degree for nodes with no outgoing edges
            for node in nodes:
                if node not in out_degree:
                    out_degree[node] = 0

            # Step 3: Initialize PageRank scores (Personalized). A seed named twice is one
            # seed, as in networkx's personalization dict.
            seed_set = set(valid_seeds)
            seed_count = len(seed_set)
            ranks = {
                node: (1.0 / seed_count if node in seed_set else 0.0) for node in nodes
            }
            dangling_nodes = [node for node in nodes if out_degree[node] == 0]

            # Step 4: Iterative computation with personalization. networkx's
            # pagerank(personalization=...): a node with no out-edges hands damping * its
            # score to the seeds, as the restart does, so every iterate sums to 1 (4.6.0
            # R270: before, that share was passed nowhere). The walk stops on networkx's
            # rule, the summed change below N * tolerance, N the nodes holding a score.
            for iteration in range(max_iterations):
                new_ranks = {}
                change = 0.0
                held = 0
                dangling = sum(ranks[node] for node in dangling_nodes)
                restart = ((1.0 - damping_factor) + damping_factor * dangling) / seed_count

                for node in nodes:
                    rank = restart if node in seed_set else 0.0

                    # Add contributions from incoming edges (with weights)
                    if node in in_edges:
                        for src, weight in in_edges[node]:
                            if out_degree.get(src, 0) > 0:
                                rank += (
                                    damping_factor
                                    * weight
                                    * (ranks.get(src, 0) / out_degree[src])
                                )

                    new_ranks[node] = rank
                    change += abs(rank - ranks.get(node, 0))
                    held += rank != 0.0

                ranks = new_ranks

                # Check convergence
                if change < held * tolerance:
                    logger.debug(
                        f"PageRank converged after {iteration + 1} iterations (Python fallback)"
                    )
                    break

            # Filter out zero scores and apply top_k limit
            results = {node: score for node, score in ranks.items() if score > 0}

            if return_top_k is not None and return_top_k > 0:
                sorted_items = sorted(results.items(), key=lambda x: x[1], reverse=True)
                results = dict(sorted_items[:return_top_k])

            return results

        except Exception as e:
            logger.error(f"kg_PERSONALIZED_PAGERANK Python fallback failed: {e}")
            raise
        finally:
            cursor.close()
    def khop(self, seed: str, hops: int = 2, max_nodes: int = 500) -> dict:
        """The nodes within ``hops`` outbound hops of ``seed`` and the edges walked to reach them,
        as ``{"nodes", "edges"}``.

        ``nodes`` are IDs: the seed first, then the nodes in the order the breadth-first walk
        reached them, at most ``max_nodes``. ``edges`` are the edges walked between listed nodes,
        each ``{"s", "p", "o", "w", "step"}`` (``step``: the hop that walked it). A seed with no
        out-edge answers itself and no edges; a missing seed answers two empty lists, and so does
        a namespace without the ObjectScript classes. A walk that fails raises ``RuntimeError``
        with IRIS's error (before 4.6.0 it answered two empty lists too). Read-only; a user
        holding the database at ``R`` gets the same answer (4.6.0 R206).

        The walk is ``Graph.KG.Traversal.BFSFastJson`` whether or not the Arno library is
        loaded: the library's ``KHopNeighbors`` answers ``{"seed", "hops", "totalNodes",
        "nodes": [{"id", "dist"}]}`` and no edges, so before 4.6.0 the shape of the answer
        depended on whether it was loaded (R095).
        """
        return self._khop_fallback(seed, hops, max_nodes)
    def _khop_fallback(self, seed: str, hops: int, max_nodes: int) -> dict:
        if self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                from iris_vector_graph.schema import _call_classmethod_large

                result = _call_classmethod_large(
                    iris_obj, "Graph.KG.Traversal", "BFSFastJson", seed, "", hops, "", "out"
                )
                edges = json.loads(str(result)) if result else []
                if not edges:
                    return {"nodes": [seed] if self._khop_seed_exists(seed) else [], "edges": []}
                # Walk order: the edges come hop by hop, so first appearance is reach order.
                reached = {seed: None}
                for e in edges:
                    reached.setdefault(e["s"], None)
                    reached.setdefault(e["o"], None)
                nodes = list(reached)[: max(int(max_nodes), 0)]
                kept = set(nodes)
                return {"nodes": nodes, "edges": [e for e in edges if e["s"] in kept and e["o"] in kept]}
            except Exception as e:
                # R206: this answered two empty lists, the answer for a missing seed
                raise RuntimeError(f"khop({seed!r}) failed: {e}") from e
        return {"nodes": [], "edges": []}
    def _khop_seed_exists(self, seed: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {self._t('nodes')} WHERE node_id = ?", [seed])
            row = cursor.fetchone()
            return bool(row and int(row[0]))
        finally:
            cursor.close()
    def ppr(
        self, seed: str, alpha: float = 0.85, max_iter: int = 20, top_k: int = 20
    ) -> dict:
        """Personalized PageRank from one ``seed``: ``{"scores": [{"id", "score"}]}``, best first.

        ``kg_PERSONALIZED_PAGERANK`` on the default graph, cut to ``top_k`` nodes; ``alpha`` is the damping factor.
        Read-only. Before 4.6.0 (R270), with the Arno library loaded and ``^NKG`` built, the answer came from
        ``Graph.KG.NKGAccel.PPRNative`` instead: a walk of the seed's 2-hop neighbourhood that passed a dangling
        node's score nowhere and rounded to 6 places, so it was not networkx's PPR, nor this method's answer
        without the library.
        """
        scores = self.kg_PERSONALIZED_PAGERANK(
            [seed], damping_factor=alpha, max_iterations=max_iter, return_top_k=top_k
        )
        return {
            "scores": [
                {"id": k, "score": v}
                for k, v in sorted(scores.items(), key=lambda x: -x[1])
            ]
        }
    def random_walk(self, seed: str, length: int = 20, num_walks: int = 10) -> list:
        """Random walks of up to ``length`` steps from ``seed``, ``num_walks`` of them.

        Needs the Arno library: the walks come from its ``RandomWalkJson`` as a list. Without the
        library, or when the call fails, it returns ``[]`` (logged as a warning). Read-only.
        """
        if self._detect_arno() and "random_walk" in self._arno_capabilities.get(
            "algorithms", []
        ):
            try:
                result = self._arno_call(
                    "Graph.KG.NKGAccel", "RandomWalkJson", seed, str(length), str(num_walks)
                )
                parsed = json.loads(result)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict) and "error" in parsed:
                    logger.warning(f"Arno random_walk error: {parsed['error']}")
            except Exception as exc:
                logger.warning("random_walk arno call failed (%s), returning []", exc)
        return []
    def kg_GRAPH_PATH(self, src_id: str, pred1: str, pred2: str, max_hops: int = 2):
        """Two-hop paths ``src_id -pred1-> b -pred2-> c``, one row per step.

        Runs one Cypher query and returns ``(path_id, step, from_id, rel_type, to_id)`` tuples:
        step 1 is the first edge, step 2 the second. ``path_id`` is 1 on every row, so rows of
        different paths are told apart only by their node IDs. ``max_hops`` is not used: the
        pattern is always exactly two hops. Read-only.
        """
        result = self.execute_cypher(
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 1 AS step, a.node_id AS from_id, type(r1) AS rel_type, b.node_id AS to_id "
            "UNION ALL "
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 2 AS step, b.node_id AS from_id, type(r2) AS rel_type, c.node_id AS to_id",
            {"src": src_id, "p1": pred1, "p2": pred2},
        )
        return [(int(r[0]), int(r[1]), r[2], r[3], r[4]) for r in result.rows]
    def kg_GRAPH_WALK(self, start_entity: str, max_depth: int = 3,
                      edge_types: Optional[List[str]] = None,
                      max_results: int = 100):
        """Breadth-first walk outbound from ``start_entity``, up to ``max_depth`` hops.

        Follows only ``edge_types`` when given (all predicates otherwise) and returns at most
        ``max_results`` ``(source, predicate, target, step)`` tuples, ordered by step, where
        step is the hop at which the target was reached; ``[]`` when nothing is reachable.
        Runs ``Graph.KG.Traversal.BFSFastJsonSorted`` over ``^KG``.
        """
        preds_json = json.dumps(edge_types) if edge_types else "[]"
        from iris_vector_graph.schema import _call_classmethod, _call_classmethod_large
        try:
            raw = str(_call_classmethod(
                self.conn, "Graph.KG.Traversal", "BFSFastJsonSorted",
                start_entity, preds_json, max_depth, "", "out", max_results
            ))
        except Exception as exc:
            refused = _refused_work_area(self, "kg_GRAPH_WALK", str(exc))
            if refused:
                raise RuntimeError(refused) from exc
            raise
        if raw.startswith("SORTED:") and raw != "SORTED:0":
            tag = raw.split(":")[1]
            json_str = str(_call_classmethod_large(
                self._iris_obj(), "Graph.KG.Traversal", "ReadBFSResults", tag))
            rows = json.loads(json_str) if json_str else []
            return [(r.get("s", ""), r.get("p", ""), r.get("o", ""), r.get("step", 1))
                    for r in rows]
        return []
    def kg_GRAPH_WALK_TVF(self, start_entity: str, max_depth: int = 3,
                           edge_types: Optional[List[str]] = None,
                           max_results: int = 100):
        """The walk ``kg_GRAPH_WALK`` does, under its older table-valued-function name.

        Returns what ``kg_GRAPH_WALK`` returns for the same arguments.
        """
        return self.kg_GRAPH_WALK(start_entity, max_depth, edge_types, max_results)
    def kg_PAGERANK(self, seed_entities: Optional[List[str]] = None,
                    damping: float = 0.85, max_iterations: int = 20,
                    bidirectional: bool = False, reverse_weight: float = 1.0):
        """Global PageRank over the default graph as a list of ``(node_id, score)`` pairs, highest first, or, with
        ``seed_entities``, personalized PageRank from those nodes as a ``{node_id: score}`` dict. ``damping`` is the
        probability of following an edge rather than restarting. ``max_iterations`` (20) is a fixed count with no
        convergence test: 20 can leave scores a few hundredths from converged, so raise it (for example to 200) to
        compare with networkx's ``pagerank(G, weight=None)`` (4.6.0 R225). Only the nodes with at least one edge
        take part: their scores sum to 1, and a node with no edge has no pair, so on a graph with such nodes this is
        ``pagerank`` of G without them (4.6.0 R050). ``kg_PAGERANK(seed_entities=["a"])`` returns
        ``kg_PERSONALIZED_PAGERANK(["a"], damping_factor=damping, max_iterations=max_iterations)``: the same walk,
        stopped after 20 iterations by default where ``kg_PERSONALIZED_PAGERANK`` allows 100 and stops once
        converged, so call that one for personalized PageRank. ``bidirectional`` and ``reverse_weight`` are accepted
        and ignored, in both forms: edges are followed in their direction only.

        Runs through the store, which uses a proven native reader and the shared algorithm
        cache, falling back to ``Graph.KG.PageRank.PageRankGlobalJson``.

        Example:
            >>> engine.kg_PAGERANK()  # a -> b -> c -> a, and d with no edge
            [('a', 0.333...), ('b', 0.333...), ('c', 0.333...)]
            >>> engine.kg_PAGERANK(seed_entities=["a"]) == engine.kg_PERSONALIZED_PAGERANK(["a"], max_iterations=20)
            True
        """
        if seed_entities is not None:
            return self.kg_PERSONALIZED_PAGERANK(
                seed_entities, damping_factor=damping, max_iterations=max_iterations,
            )
        # Through the store, as kg_WCC and kg_CDLP are: the store routes to a Native
        # Reader only when one is proven, and serves a repeat from the shared cache.
        if self._store_capabilities.get("pagerank", True):
            result = self._store.execute_pagerank(damping, max_iterations)
            if not result.error:
                return [(r[0], float(r[1])) for r in result.rows]
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.PageRank", "PageRankGlobalJson",
                                    damping, max_iterations))
        parsed = json.loads(raw) if raw else []
        return [(item["id"], float(item["score"])) for item in parsed]
    def kg_WCC(self, max_iterations: Optional[int] = None) -> Dict[str, Any]:
        """Weakly connected components, as ``{node: component}``. Labels propagate until they
        settle; ``max_iterations`` is a safety limit on the rounds, and raises
        ``WCC did not converge in n rounds`` when it is reached first (before 4.5.2: 100 rounds,
        then whatever labels it had). With a limit the ObjectScript Owner answers, on the store
        path too (before 4.6.0 the store ignored the limit)."""
        if self._store_capabilities.get("wcc", True):
            result = (
                self._store.execute_wcc()
                if max_iterations is None
                else self._store.execute_wcc(max_iterations=max_iterations)
            )
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        limit = () if max_iterations is None else (max_iterations,)
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "WCCJson", *limit))
        return json.loads(raw) if raw else {}
    def kg_CDLP(self, max_iterations: int = 10) -> Dict[str, Any]:
        """Communities by label propagation (CDLP), as ``{node: community}``.

        Runs through the store (a proven native reader or the ObjectScript owner, with the
        shared algorithm cache), falling back to ``Graph.KG.Algorithms.CDLPJson``; at most
        ``max_iterations`` rounds. ``{}`` when the graph is empty.
        """
        if self._store_capabilities.get("cdlp", True):
            result = self._store.execute_cdlp(max_iterations)
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "CDLPJson", max_iterations))
        return json.loads(raw) if raw else {}
    def _subgraph_embeddings(self, nodes: List[str]) -> Dict[str, Any]:
        """The vectors of the nodes a subgraph traversal returned.

        `WHERE id IN (...)` compared node IDs against the table's RowID and matched
        nothing, so `include_embeddings=True` returned an empty map and looked like
        "no node is embedded". Reach stays namespace-wide because the traversal that
        produced `nodes` is namespace-wide too (`Graph.KG.Subgraph` takes no graph) —
        scoping only the vector read would answer a narrower question than the node
        list it decorates.
        """
        node_embeddings: Dict[str, Any] = {}
        if not nodes:
            return node_embeddings
        emb_table = self._t("kg_NodeEmbeddings")
        cursor = self.conn.cursor()
        phs = ",".join(["?"] * len(nodes))
        cursor.execute(
            f"SELECT node_id, emb FROM {emb_table} WHERE node_id IN ({phs})",
            nodes,
        )
        for nid, emb in cursor.fetchall():
            if emb is None:
                continue  # a row with no vector: the node is not embedded
            try:
                node_embeddings[nid] = [float(x) for x in str(emb).split(",")]
            except ValueError as e:
                # It was passed over (4.6.0 R276), so the node read as not embedded.
                raise ValueError(f"the stored vector of {nid!r} is not a list of numbers: {str(emb)[:80]!r}") from e
        cursor.close()
        return node_embeddings

    def kg_SUBGRAPH(self, seed_ids: List[str], k_hops: int = 2,
                    edge_types: Optional[List[str]] = None,
                    include_properties: bool = True,
                    include_embeddings: bool = False,
                    max_nodes: int = 10000):
        """The subgraph within ``k_hops`` of ``seed_ids``, as a ``SubgraphData``.

        Holds the reached nodes (at most ``max_nodes``), the edges among them as ``(s, p, o)``
        tuples (only ``edge_types`` when given), and, by default, each node's properties and
        labels; ``include_embeddings=True`` adds the nodes' vectors from ``kg_NodeEmbeddings``.
        Empty seeds give an empty ``SubgraphData``. Read-only.
        """
        from iris_vector_graph.models import SubgraphData
        if not seed_ids:
            return SubgraphData(seed_ids=list(seed_ids))
        if self._store_capabilities.get("subgraph", True):
            result = self._store.execute_subgraph(seed_ids, k_hops, edge_types or [], max_nodes)
            if result.error is None:
                import json as _j

                def _col(index, empty):
                    row = result.rows[0] if result.rows else []
                    if len(row) <= index or not row[index]:
                        return empty
                    return _j.loads(row[index])

                nodes = _col(0, [])
                # The envelope's edges are `{"s","p","o"}` objects; `SubgraphData.edges`
                # is `List[Tuple[str, str, str]]`, and the ObjectScript path below has
                # always converted them. Leaving dicts here meant
                # `for s, p, o in sg.edges` unpacked the *keys*.
                edges = [
                    (e["s"], e["p"], e["o"]) if isinstance(e, dict) else tuple(e)
                    for e in _col(1, [])
                ]
                node_properties = _col(2, {}) if include_properties else {}
                node_labels = _col(3, {}) if include_properties else {}
                node_embeddings = (
                    self._subgraph_embeddings(nodes) if include_embeddings else {}
                )
                return SubgraphData(
                    nodes=nodes,
                    edges=edges,
                    node_properties=node_properties,
                    node_labels=node_labels,
                    node_embeddings=node_embeddings,
                    seed_ids=list(seed_ids),
                )
        from iris_vector_graph.schema import _call_classmethod
        seed_json = json.dumps(seed_ids)
        edge_types_json = json.dumps(edge_types) if edge_types else ""
        raw = str(_call_classmethod(self.conn, "Graph.KG.Subgraph", "SubgraphJson",
                                    seed_json, k_hops, edge_types_json, max_nodes))
        if raw:
            parsed = json.loads(raw)
            nodes = parsed.get("nodes", [])
            edges = [(e["s"], e["p"], e["o"]) for e in parsed.get("edges", [])]
            node_properties = parsed.get("properties", {})
            node_labels = parsed.get("labels", {})
            node_embeddings = self._subgraph_embeddings(nodes) if include_embeddings else {}
            return SubgraphData(
                seed_ids=seed_ids, nodes=nodes, edges=edges,
                node_properties=node_properties, node_labels=node_labels,
                node_embeddings=node_embeddings,
            )
        return SubgraphData(seed_ids=seed_ids)
    def kg_PPR_GUIDED_SUBGRAPH(self, seed_ids: List[str], ppr_top_k: int = 50,
                                k_hops: int = 1, damping: float = 0.85,
                                max_iterations: int = 10,
                                edge_types: Optional[List[str]] = None,
                                max_nodes: int = 5000):
        """The subgraph around the nodes personalized PageRank ranks highest for ``seed_ids``.

        Runs ``kg_PERSONALIZED_PAGERANK`` from the seeds, takes the ``ppr_top_k`` best-scored
        nodes, and returns ``kg_SUBGRAPH`` of the seeds plus those nodes within ``k_hops``,
        capped at ``min(max_nodes, ppr_top_k)`` nodes, as a ``PprGuidedSubgraphData`` with the
        scores and with edges as ``{"src", "dst", "type"}`` dicts. Nothing is pruned: the
        before and after node counts are equal. Read-only.
        """
        from iris_vector_graph.models import PprGuidedSubgraphData
        if not seed_ids:
            return PprGuidedSubgraphData(seed_ids=[], nodes=[], edges=[], ppr_scores=[])
        ppr_scores = self.kg_PERSONALIZED_PAGERANK(seed_ids, damping_factor=damping,
                                                     max_iterations=max_iterations)
        if isinstance(ppr_scores, dict):
            sorted_scores = sorted(ppr_scores.items(), key=lambda x: -x[1])
            top_ids = [k for k, _ in sorted_scores[:ppr_top_k]]
        else:
            sorted_scores = sorted(ppr_scores, key=lambda x: -x[1])
            top_ids = [item[0] for item in sorted_scores[:ppr_top_k]]
        all_seeds = list(dict.fromkeys(seed_ids + top_ids))
        subgraph = self.kg_SUBGRAPH(all_seeds, k_hops=k_hops, edge_types=edge_types,
                                    max_nodes=min(max_nodes, ppr_top_k))
        return PprGuidedSubgraphData(
            seed_ids=seed_ids,
            nodes=subgraph.nodes,
            edges=[{"src": e[0], "dst": e[2], "type": e[1]} for e in subgraph.edges if isinstance(e, (list, tuple)) and len(e) == 3]
                  if subgraph.edges and isinstance(subgraph.edges[0], (list, tuple))
                  else subgraph.edges,
            ppr_scores=sorted_scores[:ppr_top_k],
            nodes_before_pruning=len(subgraph.nodes),
            nodes_after_pruning=len(subgraph.nodes),
        )
    def kg_NEIGHBORS(self, source_ids: List[str], predicate: Optional[str] = None,
                     direction: str = "out", distinct: bool = True,
                     chunk_size: int = 500) -> List[str]:
        """The node IDs one hop from ``source_ids``, along ``predicate`` edges if given.

        ``direction`` is ``"out"``, ``"in"`` or ``"both"`` (anything else raises
        ``ValueError``). Runs one Cypher query per source and direction and returns the targets
        in the order found, each once unless ``distinct=False``; ``[]`` for no sources.
        """
        if not source_ids:
            return []
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in', or 'both', got {direction!r}")
        all_targets: List[str] = []
        seen: set = set()
        for i in range(0, len(source_ids), chunk_size):
            chunk = source_ids[i:i + chunk_size]
            for src in chunk:
                dirs = ["out", "in"] if direction == "both" else [direction]
                for d in dirs:
                    if d == "out":
                        q = ("MATCH (s {node_id: $id})-[r]->(t) " +
                             ("WHERE type(r)=$p " if predicate else "") +
                             "RETURN t.node_id")
                    else:
                        q = ("MATCH (t)-[r]->(s {node_id: $id}) " +
                             ("WHERE type(r)=$p " if predicate else "") +
                             "RETURN t.node_id")
                    params: Dict[str, Any] = {"id": src}
                    if predicate:
                        params["p"] = predicate
                    r = self.execute_cypher(q, params)
                    for row in r.rows:
                        t = row[0]
                        if t and (not distinct or t not in seen):
                            all_targets.append(t)
                            seen.add(t)
        return all_targets
    def kg_MENTIONS(self, source_ids: List[str], predicate: str = "MENTIONS",
                    direction: str = "out") -> List[str]:
        """``kg_NEIGHBORS`` along ``MENTIONS`` edges (or ``predicate``): the IDs one hop away."""
        return self.kg_NEIGHBORS(source_ids, predicate=predicate, direction=direction)
    def kg_PPR(self, seed_entities: List[str], damping: float = 0.85,
               max_iterations: int = 20) -> List[Tuple[str, float]]:
        """Personalized PageRank from ``seed_entities``, as ``(node_id, score)`` pairs, best first.

        Runs ``kg_PERSONALIZED_PAGERANK`` on the default graph and sorts its scores; ``[]`` for
        no seeds. Read-only.
        """
        if not seed_entities:
            return []
        result = self.kg_PERSONALIZED_PAGERANK(seed_entities, damping_factor=damping,
                                                max_iterations=max_iterations)
        if isinstance(result, dict):
            return sorted(result.items(), key=lambda x: -x[1])
        return result
    def kg_RERANK(self, top_n: int, query_vector: str, query_text: str):
        """The ``top_n`` best nodes for a vector and a text query, fused by ``kg_RRF_FUSE``.

        Each list is searched ``2 * top_n`` deep and the two are fused with ``c=60``; returns
        what ``kg_RRF_FUSE`` returns. Read-only.
        """
        return self.kg_RRF_FUSE(k=top_n, k1=top_n * 2, k2=top_n * 2, c=60,
                                 query_vector=query_vector, query_text=query_text)
    def degree_centrality(
        self,
        direction: str = "out",
        predicate: Optional[str] = None,
        top_k: int = 10000,
        *,
        graph: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Each node's ``degree`` (its number of edges in ``direction``) and ``score``, its degree / (n - 1), n being
        the number of the graph's nodes with at least one edge (any type, either direction), highest first: on 6
        such nodes a node with 3 out-edges has degree 3 and score 0.6. A node with no edge has no row and is not
        in n, so n is ``node_count(graph)`` only when every node has an edge (4.6.0 R050).
        ``CALL ivg.degreeCentrality`` yields the same two, as ``degree`` and ``score``.

        Useful for identifying hubs and peripheral nodes.

        Args:
            direction: Edge direction — "out" (outbound), "in" (inbound), or "both" (undirected). Default "out".
            predicate: Optional relationship type to filter by (e.g., "DEPENDS_ON"). None = all predicates.
            top_k: Maximum results to return. 0 = all nodes (with warning if > 100K).
            graph: The one graph to count. ``None`` or ``""`` is the default graph, never
                every graph; n counts that graph's nodes with an edge.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Normalized degree (value / (n-1)).
                - degree (int): Raw edge count.

        Example:
            >>> scores = engine.degree_centrality(direction="out", top_k=20)
            >>> print(scores[0])  # {"id": "hub-node", "score": 0.847, "degree": 12}

        Note:
            Performance tier: ObjectScript parallel (8× workers) when `^NKG` built, 
            otherwise Python LazyKG. See docs/BENCHMARKS.md.
        """
        from iris_vector_graph._validate import DegreeCentralityInput, validate_graph_name
        validated = DegreeCentralityInput(
            direction=direction,
            predicate=predicate,
            top_k=top_k,
        )
        graph_name = validate_graph_name(graph)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("degree_centrality", False):
            raise NotImplementedError(
                f"Centrality.degree_centrality not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"degree_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        # It always counted the default graph (4.6.0 R144). The graph is sent only when it is named, so a store
        # whose execute_degree_centrality takes no graph still serves the default graph, and fails (TypeError)
        # rather than answering the default graph's degrees for a named one.
        result = self._store.execute_degree_centrality(
            validated.direction,
            validated.predicate or "",
            validated.top_k,
            **({"graph": graph_name} if graph_name else {}),
        )
        _raise_store_error("degree_centrality", result)
        return [
            {"id": row[0], "score": row[1], "degree": row[2]}
            for row in result.rows
        ]
    def betweenness_centrality(
        self,
        sample_size: int = 0,
        direction: str = "out",
        max_hops: int = 0,
        top_k: int = 10000,
        mem_budget_mb: int = 256,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Betweenness centrality via Brandes (2001) algorithm, one row per node that lies on some shortest path
        between two others: a node scoring 0 has no row, where ``closeness_centrality`` and ``degree_centrality``
        list every node with an edge, zeros included, so for the lowest scores count each node with an edge and no
        row as 0 (``CALL ivg.betweenness`` and ``gds.betweenness.stream`` answer the same rows).

        Measures how often a node appears on shortest paths between other nodes.
        Uses three-tier dispatch: Rust accelerator (fastest) → ObjectScript parallel 
        (8× workers) → Python LazyKG (always works).

        Args:
            sample_size: Number of source nodes for the Brandes-Pich
                approximation; scores are scaled by the node count over it.
                0 (the default) uses every source on a graph of up to 200
                nodes and 200 sources above that, an approximation. A value at
                or above the node count gives exact Brandes over every source:
                ``sample_size=engine.node_count()`` is networkx's
                ``betweenness_centrality(G, normalized=False)``. The node count
                minus one is not exact (4.6.0 R216). A sample is fixed and
                pseudo-random: the IDs first by the SHA-1 of their UTF-8 bytes
                (first 7 bytes, big-endian, then the ID), the same on every call
                and route; before 4.6.0 R233 it was the first IDs in ID order.
            direction: Edge direction — "out", "in", or "both". Default "out".
                "both" scores the undirected graph, each pair of endpoints
                counted once, as networkx and igraph do (before 4.6.0 R216,
                twice). "out" and "in" give the same scores.
            max_hops: Maximum BFS depth per source: only pairs of nodes at most
                this many hops apart are counted. 0 = unbounded. Before 4.6.0
                R232 the ObjectScript tier, and ``CALL ivg.betweenness``,
                ignored it.
            top_k: The highest scores to return. 0 = all nodes.
            mem_budget_mb: Memory budget in MB for predecessor accumulator.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, one per node on some shortest path (a node scoring 0 is not
            listed, whichever tier answered), each containing:
                - id (str): Node identifier.
                - score (float): Raw betweenness score (sum of dependency values
                  scaled by n/sample_size if sampled).

        Example:
            >>> scores = engine.betweenness_centrality(sample_size=200, top_k=20)
            >>> print(scores[0])  # {"id": "hub-node", "score": 4821.3}

        Note:
            Performance tiers require `^NKG` to be built (`engine.sync()`).
            Without the accelerator library, falls back to ObjectScript parallel
            (~500ms on ER(2000)). See docs/BENCHMARKS.md.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.betweenness``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import BetweennessInput
        validated = BetweennessInput(
            sample_size=sample_size,
            direction=direction,
            max_hops=max_hops,
            top_k=top_k,
            mem_budget_mb=mem_budget_mb,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("betweenness", False):
            raise NotImplementedError(
                f"Centrality.betweenness not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"betweenness_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_betweenness(
            validated.sample_size,
            validated.direction,
            validated.max_hops,
            validated.top_k,
            validated.mem_budget_mb,
            progress_callback,
        )
        _raise_store_error("betweenness_centrality", result)

        # Only the nodes on some shortest path. The ObjectScript tier lists those; the Python walk, which answers
        # where that tier cannot run (a reader at database R, a named graph with edges; direction="both" until R232), listed
        # every node with zeros too, so one call answered two shapes by role (4.6.0 R244).
        out: List[Dict[str, Any]] = []
        for row in result.rows:
            if len(row) == 2 and row[0] == "_meta" and isinstance(row[1], dict):
                out.append(row[1])
            elif row[1]:
                out.append({"id": row[0], "score": row[1]})
        return out
    def betweenness_centrality_neighborhood(
        self,
        seed: str,
        hops: int = 2,
        sample_size: int = 200,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """Betweenness centrality within a node's neighborhood: Brandes on the subgraph induced by the nodes ``seed``
        reaches by out-edges within ``hops`` hops (the seed included), directed, highest first and ties by id. With
        ``sample_size`` at or above the neighbourhood's size it is networkx's ``betweenness_centrality(G,
        normalized=False)`` on that subgraph; a smaller one takes that many sources and scales by the size over it.

        Performance depends on the neighbourhood's size, not the graph's. Before 4.6.0 it scored the neighbourhood as
        undirected and counted each pair from both ends, and ``top_k`` kept the first scoring nodes in index order
        rather than the highest (R050). A node scoring 0 is left out.

        Args:
            seed: Seed node ID (e.g., "MESH:D009101" for Multiple Myeloma).
            hops: Neighborhood radius in hops (default 2). Typical biomedical: 2–3 hops = 500–5K nodes.
            sample_size: Number of source nodes for Brandes approximation (default 200).
            top_k: Maximum results to return (default 20).

        Returns:
            List of dicts sorted by score descending, each with ``id`` (str) and ``score`` (float).

        Example:
            >>> scores = engine.betweenness_centrality_neighborhood(seed="MESH:D009101", hops=2, top_k=20)
            >>> print(scores[0])  # {"id": "TP53", "score": 1234.5}
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        result = self._store.execute_betweenness_neighborhood(seed, hops, sample_size, top_k)
        # It answered [] here, so a failure read as a seed with no neighbourhood (4.6.0 R145).
        _raise_store_error("betweenness_centrality_neighborhood", result)
        return [{"id": r[0], "score": r[1]} for r in result.rows]
    def bfs_vector_rerank(
        self,
        seed: str,
        query_vec: List[float],
        hops: int = 2,
        top_k: int = 10,
        max_buckets: int = 32,
    ) -> List[Dict[str, Any]]:
        """Not implemented on IVG's own store: raises ``NotImplementedError``. A hook for a store that has it.

        Meant as graph-filtered semantic search: the nodes reachable from ``seed`` within ``hops`` steps, ranked by
        cosine similarity to ``query_vec``. The engine passes the call to the store's ``execute_bfs_vector_rerank``,
        and no store IVG ships has one (``IRISGraphStore`` included), so on IVG this raises ``NotImplementedError``
        naming the store. A store that has it answers through this method; a store error raises ``RuntimeError``.

        Args:
            seed: Seed node ID to start the BFS from.
            query_vec: Query embedding vector (same dimension as node embeddings).
            hops: BFS radius (default 2).
            top_k: Maximum results to return (default 10).
            max_buckets: Passed to the store: how many nearest vector buckets it scans (default 32).

        Returns:
            From a store that implements it: dicts sorted by score descending, each with ``id`` (str),
            ``score`` (float, cosine similarity to ``query_vec``) and ``hops`` (int, BFS distance from ``seed``).

        Raises:
            NotImplementedError: no store, or a store without ``execute_bfs_vector_rerank`` (IVG's own).
            RuntimeError: the store reported an error.
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        rerank = getattr(self._store, "execute_bfs_vector_rerank", None)
        if rerank is None:
            # IVG's own store has never had it, so this was an AttributeError (4.6.0 R145).
            raise NotImplementedError(
                f"bfs_vector_rerank: store {type(self._store).__name__} has no execute_bfs_vector_rerank"
            )
        result = rerank(seed, query_vec, hops, top_k, max_buckets)
        # It answered [] here, so a failure read as no node in reach (4.6.0 R145).
        _raise_store_error("bfs_vector_rerank", result)
        return [{"id": r[0], "score": r[1], "hops": r[2]} for r in result.rows]
    def closeness_centrality(
        self,
        formula: str = "harmonic",
        direction: str = "out",
        max_hops: int = 0,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Closeness centrality over shortest-path distances from each node (``direction="out"``: the hops to the
        nodes it reaches). ``formula="harmonic"`` (the default) is the sum of 1/d over the nodes reached, not
        normalised (networkx's ``harmonic_centrality`` on the reversed graph for ``"out"``); ``"classical"`` is
        (n - 1) / (sum of d) for a node that reaches all n - 1 others, and 0 for one that does not. No other
        value is accepted (4.6.0 R225). n counts the nodes with at least one edge; a node with no edge has no row,
        is not in n and need not be reached, so n is ``node_count()`` only when every node has an edge (4.6.0 R050).

        Measures how quickly a node can reach other nodes via shortest paths.
        Uses either the classical formula (undefined for disconnected graphs) or 
        the harmonic formula (robust for disconnected graphs).

        Args:
            formula: "harmonic" (default, Beauchamp 1965, works on disconnected) 
                or "classical" (standard Bavelas-Freeman, undefined for disconnected).
            direction: Edge direction — "out", "in", or "both". Default "out".
            max_hops: Maximum BFS depth. 0 = unbounded (full graph).
            top_k: Maximum results to return. 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Closeness score (depends on formula choice).

        Example:
            >>> scores = engine.closeness_centrality(formula="harmonic", top_k=20)
            >>> print(scores[0])  # {"id": "central-node", "score": 0.823}

        Note:
            Harmonic score = the sum of 1/d over the nodes reached, not normalised (as
            networkx.harmonic_centrality), so it works on disconnected components; every
            path answers that scale (before 4.6.0 the Python walk divided it by n - 1).
            Classical formula is undefined when any node is unreachable.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.closeness``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import ClosenessInput
        validated = ClosenessInput(
            formula=formula,
            direction=direction,
            max_hops=max_hops,
            top_k=top_k,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("closeness", False):
            raise NotImplementedError(
                f"Centrality.closeness not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"closeness_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_closeness(
            validated.formula,
            validated.direction,
            validated.max_hops,
            validated.top_k,
            progress_callback,
        )
        _raise_store_error("closeness_centrality", result)
        return [{"id": row[0], "score": row[1]} for row in result.rows]
    def eigenvector_centrality(
        self,
        max_iter: int = 30,
        tol: float = 1e-6,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Eigenvector centrality by power iteration on the directed graph: a node's score is the sum of the scores
        of the nodes with an edge to it, L2-normalised each round, stopping after ``max_iter`` rounds (1 to 1000;
        default 30) or when no score moves by more than ``tol``. 30 rounds can stop short of convergence, a few
        hundredths off; ``max_iter=1000`` with a small ``tol`` matches networkx's ``eigenvector_centrality(G)`` on
        the directed graph (4.6.0 R225).

        Iterative power method over the raw adjacency matrix A (not the transition 
        matrix). Measures influence: a node is important if it's connected to other 
        important nodes. L2-normalized output. Matches `networkx.eigenvector_centrality_numpy`.

        Args:
            max_iter: Maximum power iterations (default 30, at most 1000); 30 can stop before ``tol`` is met.
            tol: Convergence tolerance for L2 norm change (default 1e-6).
            top_k: Maximum results to return. 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): L2-normalized eigenvector component (range 0–1).

        Example:
            >>> scores = engine.eigenvector_centrality(max_iter=30, top_k=20)
            >>> print(scores[0])  # {"id": "influential-node", "score": 0.894}

        Note:
            Convergence requires the largest eigenvalue to be unique (no symmetry).
            Falls back to Python LazyKG if ObjectScript or Rust path unavailable.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.eigenvector``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import EigenvectorInput
        validated = EigenvectorInput(
            max_iter=max_iter,
            tol=tol,
            top_k=top_k,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("eigenvector", False):
            raise NotImplementedError(
                f"Centrality.eigenvector not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"eigenvector_centrality(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_eigenvector(
            validated.max_iter,
            validated.tol,
            validated.top_k,
            progress_callback,
        )
        _raise_store_error("eigenvector_centrality", result)
        return [{"id": row[0], "score": row[1]} for row in result.rows]
    def leiden_communities(
        self,
        max_levels: int = 10,
        gamma: float = 1.0,
        tol: float = 1e-4,
        top_k: int = 10000,
        mem_budget_mb: int = 256,
        random_seed: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Leiden community detection (Traag et al. 2019).

        Partitions the graph into densely-connected communities using the Leiden
        algorithm, which fixes the "badly connected community" problem in Louvain.
        Supports modularity (gamma=1.0) and CPM (resolution parameter) quality functions.

        Args:
            max_levels: Maximum number of aggregation levels (default 10).
            gamma: 1.0 (the default) optimizes modularity. Any other value
                is the Constant Potts Model with resolution gamma (leidenalg's
                CPMVertexPartition): a lower gamma gives fewer, larger
                communities, and above 1.0 every node is its own. Every route
                means this (4.6.0 R231); where the CPM cannot be computed
                (leidenalg missing past ^NKG) the call raises.
            tol: Convergence tolerance (default 1e-4).
            top_k: Maximum results to return. 0 = all nodes.
            mem_budget_mb: Memory budget in MB for community tracking.
            random_seed: Seed for reproducibility. None = stochastic.
            progress_callback: Optional callable(completed, total).

        Returns:
            List of dicts sorted by community ascending, each containing:
                - id (str): Node identifier.
                - community (int): Community index (0 = largest, 1 = second-largest, ...).
                - size (int): Number of nodes in this community.

        Example:
            >>> communities = engine.leiden_communities(gamma=1.0, top_k=100)
            >>> print(communities[0])  # {"id": "node-a", "community": 0, "size": 23}

        Note:
            The first route that answers is used: ``Graph.KG.Communities.LeidenJsonAuto`` in IRIS (leidenalg in
            IRIS's embedded Python when igraph and leidenalg are installed there, else Louvain in ObjectScript);
            Arno's Leiden when it is loaded; leidenalg in this process, else networkx's Louvain. Every route reads
            the graph as undirected and simple and ignores edge weights, so compare with networkx's
            ``modularity(..., weight=None)`` (4.6.0 R220; docs/COMMUNITIES.md).

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            through Arno when it is loaded, otherwise by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.leiden``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import LeidenInput
        validated = LeidenInput(
            max_levels=max_levels, gamma=gamma, tol=tol, top_k=top_k,
            mem_budget_mb=mem_budget_mb, random_seed=random_seed,
        )

        if not getattr(self, "_store", None) or not self._store.capabilities().get("leiden", False):
            raise NotImplementedError(
                f"Communities.leiden not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        if validated.top_k == 0:
            try:
                count_result = self._store.get_node_count()
                node_count = int(count_result.rows[0][0]) if count_result.rows else 0
                if node_count > 100_000:
                    import warnings
                    warnings.warn(
                        f"leiden_communities(top_k=0) on {node_count}-node graph may produce large JSON",
                        RuntimeWarning, stacklevel=2,
                    )
            except Exception:
                pass

        result = self._store.execute_leiden(
            validated.max_levels, validated.gamma, validated.tol,
            validated.top_k, validated.mem_budget_mb,
            validated.random_seed, progress_callback,
        )
        _raise_store_error("leiden_communities", result)

        out: List[Dict[str, Any]] = []
        for row in result.rows:
            if len(row) >= 1 and row[0] == "_meta":
                import json as _json
                meta = _json.loads(row[1]) if isinstance(row[1], str) else row[1]
                out.append(meta if isinstance(meta, dict) else {"_meta": row[1]})
            else:
                out.append({"id": row[0], "community": row[1], "size": row[2]})
        return out
    def triangle_count(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Triangle count and local clustering coefficient.

        Counts triangles incident to each node and computes the local clustering 
        coefficient (LCC) — fraction of a node's neighbors that are also connected 
        to each other. High LCC indicates tightly-knit local neighborhoods.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by triangle count descending, each containing:
                - id (str): Node identifier.
                - triangles (int): Number of triangles involving this node.
                - lcc (float): Local clustering coefficient (0–1).

        Example:
            >>> results = engine.triangle_count(top_k=50)
            >>> print(results[0])  # {"id": "hub-node", "triangles": 45, "lcc": 0.73}

        Note:
            Uses symmetrized adjacency (treats graph as undirected for deduplication).
            LCC = 2 * triangles / (k * (k-1)) where k is node degree.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            through Arno when it is loaded, otherwise by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.triangleCount``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import TriangleCountInput
        validated = TriangleCountInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("triangle_count", False):
            raise NotImplementedError(
                f"Communities.triangle_count not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_triangle_count(validated.top_k, progress_callback)
        _raise_store_error("triangle_count", result)
        return [{"id": row[0], "triangles": row[1], "lcc": row[2]} for row in result.rows]
    def strongly_connected_components(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Strongly connected components (Tarjan 1972, iterative).

        Partitions directed graph into SCCs — maximal sets of nodes where every node 
        is reachable from every other node. Detects feedback loops and cycles in workflows.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by component ascending, each containing:
                - id (str): Node identifier.
                - component (int): Component index (0 = first SCC, etc.).
                - size (int): Number of nodes in this SCC.

        Example:
            >>> sccs = engine.strongly_connected_components(top_k=100)
            >>> print(sccs[0])  # {"id": "node-a", "component": 0, "size": 8}

        Note:
            Iterative Tarjan (1972) with explicit DFS stack to avoid Python recursion limits.
            Matches `networkx.strongly_connected_components` exactly.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            through Arno when it is loaded, otherwise by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.scc``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import SCCInput
        validated = SCCInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("scc", False):
            raise NotImplementedError(
                f"Communities.scc not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_scc(validated.top_k, progress_callback)
        _raise_store_error("strongly_connected_components", result)
        return [{"id": row[0], "component": row[1], "size": row[2]} for row in result.rows]
    def k_core(
        self,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """K-core decomposition (Batagelj-Zaversnik 2003, O(V+E)).

        Recursively removes nodes with degree < k, iteratively increasing k. The k-core 
        is the maximal subgraph where all nodes have degree ≥ k. High coreness nodes form 
        the network's structural core; low coreness nodes are peripheral.

        Args:
            top_k: Maximum results to return (default 10000). 0 = all nodes.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by coreness descending, each containing:
                - id (str): Node identifier.
                - coreness (int): K-core index (higher = more central/core).

        Example:
            >>> cores = engine.k_core(top_k=100)
            >>> print(cores[0])  # {"id": "core-hub", "coreness": 5}

        Note:
            Uses bucket-sort O(V+E) algorithm (Batagelj-Zaversnik 2003) over symmetrized 
            adjacency. Matches `networkx.core_number` per-node values exactly.

        Graph scope (4.6.0 R184):
            The default graph's nodes and edges; there is no ``graph=`` argument. While only the default graph
            has edges this reads ``^NKG``, which then holds exactly them. Once a named graph has an edge, ``^NKG``
            holds it too, merged with the default graph's, and this reads the default graph's ``^KG`` instead,
            through Arno when it is loaded, otherwise by a walk from Python that is much slower on a large graph. Its Cypher form, ``CALL ivg.kcore``, answers the union of each graph's own
            answer, as a query without ``USE GRAPH`` does (4.6.0 R192): this answer while only the default graph has
            edges, and ``ValueError`` once a named graph has one, since ``^NKG`` cannot answer per graph (and
            under ``USE GRAPH``). For a named graph's
            answer, keep that graph in a namespace of its own, or read its edges with a ``USE GRAPH`` query and run
            the algorithm on them yourself (docs/USER_GUIDE.md, Components and centrality).
        """
        from iris_vector_graph._validate import KCoreInput
        validated = KCoreInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("k_core", False):
            raise NotImplementedError(
                f"Communities.k_core not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_k_core(validated.top_k, progress_callback)
        _raise_store_error("k_core", result)
        return [{"id": row[0], "coreness": row[1]} for row in result.rows]
