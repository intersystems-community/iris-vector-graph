import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Callable

from iris_vector_graph.cypher.translator import _table
from iris_vector_graph.result import IVGResult

logger = logging.getLogger(__name__)


class AlgorithmsMixin:
    def kg_PERSONALIZED_PAGERANK(
        self,
        seed_entities: List[str],
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        return_top_k: Optional[int] = None,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
    ) -> Dict[str, float]:
        """
        Personalized PageRank with optional bidirectional edge traversal.

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

        if self._store_capabilities.get("ppr", True):
            result = self._store.execute_ppr(seed_entities, damping_factor, max_iterations)
            if not result.error:
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
                logger.warning(
                    "Graph.KG.PageRank.RunJson() failed, falling back: %s", exc
                )

        return self._kg_PERSONALIZED_PAGERANK_python_fallback(
            seed_entities,
            damping_factor,
            max_iterations,
            tolerance,
            return_top_k,
            bidirectional,
            reverse_edge_weight,
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
    ) -> Dict[str, float]:
        """
        Pure Python fallback for Personalized PageRank.

        Used when IRIS SQL function kg_PPR is unavailable.
        Performance: ~25ms for 1K nodes (vs 2-5ms with embedded Python).
        """
        from iris_vector_graph.cypher.translator import _table as _t

        cursor = self.conn.cursor()
        try:
            # Step 1: Get all nodes
            cursor.execute(f"SELECT node_id FROM {_t('nodes')}")
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

            # Step 2: Build adjacency lists
            cursor.execute(f"SELECT s, o_id FROM {_t('rdf_edges')}")

            in_edges = {}  # target -> [(source, weight)]
            out_degree = {}

            for src, dst in cursor.fetchall():
                # Forward edge: weight = 1.0
                if dst not in in_edges:
                    in_edges[dst] = []
                in_edges[dst].append((src, 1.0))
                out_degree[src] = out_degree.get(src, 0) + 1

            # Step 2b: Build reverse edges if bidirectional mode enabled
            if bidirectional and reverse_edge_weight > 0:
                cursor.execute(f"SELECT o_id, s FROM {_t('rdf_edges')}")
                for o_id, s in cursor.fetchall():
                    # Reverse edge: o_id -> s with weighted contribution
                    if s not in in_edges:
                        in_edges[s] = []
                    in_edges[s].append((o_id, reverse_edge_weight))
                    out_degree[o_id] = out_degree.get(o_id, 0) + 1

            # Initialize out_degree for nodes with no outgoing edges
            for node in nodes:
                if node not in out_degree:
                    out_degree[node] = 0

            # Step 3: Initialize PageRank scores (Personalized)
            seed_count = len(valid_seeds)
            seed_set = set(valid_seeds)
            ranks = {
                node: (1.0 / seed_count if node in seed_set else 0.0) for node in nodes
            }

            # Step 4: Iterative computation with personalization
            teleport_prob = (1.0 - damping_factor) / seed_count

            for iteration in range(max_iterations):
                new_ranks = {}
                max_diff = 0.0

                for node in nodes:
                    # Teleport: jump to seed nodes (personalized)
                    if node in seed_set:
                        rank = teleport_prob
                    else:
                        rank = 0.0

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
                    max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

                ranks = new_ranks

                # Check convergence
                if max_diff < tolerance:
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
        if hops > 1 and self._detect_arno() and "khop" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel", "KHopNeighbors", seed, str(hops), str(max_nodes)
            )
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno khop error: {parsed['error']}")
        return self._khop_fallback(seed, hops, max_nodes)
    def _khop_fallback(self, seed: str, hops: int, max_nodes: int) -> dict:
        if self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                result = iris_obj.classMethodValue(
                    "Graph.KG.Traversal", "BFSFastJson", seed, "", hops, "", "out"
                )
                if result:
                    edges = json.loads(str(result))
                    nodes = set()
                    for e in edges:
                        nodes.add(e["s"])
                        nodes.add(e["o"])
                    return {"nodes": list(nodes)[:max_nodes], "edges": edges}
            except Exception as e:
                logger.debug(f"BFSFastJson fallback failed: {e}")
        return {"nodes": [], "edges": []}
    def ppr(
        self, seed: str, alpha: float = 0.85, max_iter: int = 20, top_k: int = 20
    ) -> dict:
        if self._detect_arno() and "ppr" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel",
                "PPRNative",
                seed,
                str(alpha),
                str(max_iter),
                str(top_k),
            )
            parsed = json.loads(result)
            if "error" not in parsed:
                return parsed
            logger.warning(f"Arno ppr error: {parsed['error']}")
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
        if self._detect_arno() and "random_walk" in self._arno_capabilities.get(
            "algorithms", []
        ):
            result = self._arno_call(
                "Graph.KG.NKGAccel", "RandomWalkJson", seed, str(length), str(num_walks)
            )
            parsed = json.loads(result)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "error" in parsed:
                logger.warning(f"Arno random_walk error: {parsed['error']}")
        return []
    def kg_GRAPH_PATH(self, src_id: str, pred1: str, pred2: str, max_hops: int = 2):
        result = self.execute_cypher(
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 1 AS step, a.node_id, type(r1), b.node_id "
            "UNION ALL "
            "MATCH (a {node_id: $src})-[r1]->(b)-[r2]->(c) "
            "WHERE type(r1) = $p1 AND type(r2) = $p2 "
            "RETURN 1 AS path_id, 2 AS step, b.node_id, type(r2), c.node_id",
            {"src": src_id, "p1": pred1, "p2": pred2},
        )
        return [(int(r[0]), int(r[1]), r[2], r[3], r[4]) for r in (result.get("rows") or [])]
    def kg_GRAPH_WALK(self, start_entity: str, max_depth: int = 3,
                      edge_types: Optional[List[str]] = None,
                      max_results: int = 100):
        preds_json = json.dumps(edge_types) if edge_types else "[]"
        from iris_vector_graph.schema import _call_classmethod, _call_classmethod_large
        raw = str(_call_classmethod(
            self.conn, "Graph.KG.Traversal", "BFSFastJsonSorted",
            start_entity, preds_json, max_depth, "", "out", max_results
        ))
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
        return self.kg_GRAPH_WALK(start_entity, max_depth, edge_types, max_results)
    def kg_PAGERANK(self, seed_entities: Optional[List[str]] = None,
                    damping: float = 0.85, max_iterations: int = 20,
                    bidirectional: bool = False, reverse_weight: float = 1.0):
        if seed_entities is not None:
            return self.kg_PERSONALIZED_PAGERANK(
                seed_entities, damping_factor=damping, max_iterations=max_iterations,
            )
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.PageRank", "PageRankGlobalJson",
                                    damping, max_iterations))
        parsed = json.loads(raw) if raw else []
        return [(item["id"], float(item["score"])) for item in parsed]
    def kg_WCC(self, max_iterations: int = 100) -> Dict[str, Any]:
        if self._store_capabilities.get("wcc", True):
            result = self._store.execute_wcc()
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "WCCJson", max_iterations))
        return json.loads(raw) if raw else {}
    def kg_CDLP(self, max_iterations: int = 10) -> Dict[str, Any]:
        if self._store_capabilities.get("cdlp", True):
            result = self._store.execute_cdlp(max_iterations)
            if not result.error:
                return {r[0]: r[1] for r in result.rows if len(r) >= 2}
        from iris_vector_graph.schema import _call_classmethod
        raw = str(_call_classmethod(self.conn, "Graph.KG.Algorithms", "CDLPJson", max_iterations))
        return json.loads(raw) if raw else {}
    def kg_SUBGRAPH(self, seed_ids: List[str], k_hops: int = 2,
                    edge_types: Optional[List[str]] = None,
                    include_properties: bool = True,
                    include_embeddings: bool = False,
                    max_nodes: int = 10000):
        from iris_vector_graph.models import SubgraphData
        if not seed_ids:
            return SubgraphData(seed_ids=list(seed_ids))
        if self._store_capabilities.get("subgraph", True):
            result = self._store.execute_subgraph(seed_ids, k_hops, edge_types or [], max_nodes)
            if result.error is None:
                import json as _j
                if result.rows:
                    nodes = _j.loads(result.rows[0][0]) if result.rows[0][0] else []
                    edges = _j.loads(result.rows[0][1]) if result.rows[0][1] else []
                else:
                    nodes, edges = [], []
                return SubgraphData(nodes=nodes, edges=edges, seed_ids=list(seed_ids))
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
            node_embeddings: Dict[str, Any] = {}
            if include_embeddings and nodes:
                emb_table = _table("kg_NodeEmbeddings")
                cursor = self.conn.cursor()
                phs = ",".join(["?"] * len(nodes))
                cursor.execute(
                    f"SELECT id, emb FROM {emb_table} WHERE id IN ({phs})", nodes
                )
                for row in cursor.fetchall():
                    nid, emb_csv = row[0], str(row[1])
                    try:
                        import numpy as _np
                        node_embeddings[nid] = list(_np.fromstring(emb_csv, dtype=float, sep=","))
                    except Exception:
                        pass
                cursor.close()
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
                    for row in (r.get("rows") or []):
                        t = row[0]
                        if t and (not distinct or t not in seen):
                            all_targets.append(t)
                            seen.add(t)
        return all_targets
    def kg_MENTIONS(self, source_ids: List[str], predicate: str = "MENTIONS",
                    direction: str = "out") -> List[str]:
        return self.kg_NEIGHBORS(source_ids, predicate=predicate, direction=direction)
    def kg_PPR(self, seed_entities: List[str], damping: float = 0.85,
               max_iterations: int = 20) -> List[Tuple[str, float]]:
        if not seed_entities:
            return []
        result = self.kg_PERSONALIZED_PAGERANK(seed_entities, damping_factor=damping,
                                                max_iterations=max_iterations)
        if isinstance(result, dict):
            return sorted(result.items(), key=lambda x: -x[1])
        return result
    def kg_RERANK(self, top_n: int, query_vector: str, query_text: str):
        return self.kg_RRF_FUSE(k=top_n, k1=top_n * 2, k2=top_n * 2, c=60,
                                 query_vector=query_vector, query_text=query_text)
    def degree_centrality(
        self,
        direction: str = "out",
        predicate: Optional[str] = None,
        top_k: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Degree centrality — node connectivity.

        Measures how many edges connect to each node (in/out/bidirectional). Useful for 
        identifying hubs and peripheral nodes. Normalized to (n-1).

        Args:
            direction: Edge direction — "out" (outbound), "in" (inbound), or "both" (undirected). Default "out".
            predicate: Optional relationship type to filter by (e.g., "DEPENDS_ON"). None = all predicates.
            top_k: Maximum results to return. 0 = all nodes (with warning if > 100K).

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
            otherwise Python LazyKG. See docs/performance/GRAPH_ALGORITHMS.md.
        """
        from iris_vector_graph._validate import DegreeCentralityInput
        validated = DegreeCentralityInput(
            direction=direction,
            predicate=predicate,
            top_k=top_k,
        )

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

        result = self._store.execute_degree_centrality(
            validated.direction,
            validated.predicate or "",
            validated.top_k,
        )
        if result.error:
            return []
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
        """Betweenness centrality via Brandes (2001) algorithm.

        Measures how often a node appears on shortest paths between other nodes.
        Uses three-tier dispatch: Rust accelerator (fastest) → ObjectScript parallel 
        (8× workers) → Python LazyKG (always works).

        Args:
            sample_size: Number of source nodes for Brandes-Pich approximation.
                0 uses the maxSources cap (default 200). Set equal to node count
                for exact full Brandes.
            direction: Edge direction — "out", "in", or "both". Default "out".
            max_hops: Maximum BFS depth per source. 0 = unbounded.
            top_k: Maximum results to return. 0 = all nodes.
            mem_budget_mb: Memory budget in MB for predecessor accumulator.
            progress_callback: Optional callable(completed, total) for progress reporting.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Raw betweenness score (sum of dependency values
                  scaled by n/sample_size if sampled).

        Example:
            >>> scores = engine.betweenness_centrality(sample_size=200, top_k=20)
            >>> print(scores[0])  # {"id": "hub-node", "score": 4821.3}

        Note:
            Performance tiers require `^NKG` to be built (`engine.rebuild_nkg()`).
            Without the accelerator library, falls back to ObjectScript parallel
            (~500ms on ER(2000)). See docs/performance/GRAPH_ALGORITHMS.md.
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
        if result.error:
            return []

        out: List[Dict[str, Any]] = []
        for row in result.rows:
            if len(row) == 2 and row[0] == "_meta" and isinstance(row[1], dict):
                out.append(row[1])
            else:
                out.append({"id": row[0], "score": row[1]})
        return out
    def betweenness_centrality_neighborhood(
        self,
        seed: str,
        hops: int = 2,
        sample_size: int = 200,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """Betweenness centrality within a node's neighborhood.

        Extracts a k-hop neighborhood around a seed node and computes Brandes 
        betweenness only on that subgraph. Scales to biomedical KGs: performance depends 
        on neighborhood size, not total graph size.

        Args:
            seed: Seed node ID (e.g., "MESH:D009101" for Multiple Myeloma).
            hops: Neighborhood radius in hops (default 2). Typical biomedical: 2–3 hops = 500–5K nodes.
            sample_size: Number of source nodes for Brandes approximation (default 200).
            top_k: Maximum results to return (default 20).

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Betweenness within the neighborhood subgraph.
                - hops (int): Distance from seed node.

        Example:
            >>> scores = engine.betweenness_centrality_neighborhood(
            ...     seed="MESH:D009101", hops=2, top_k=20
            ... )
            >>> print(scores[0])  # {"id": "TP53", "score": 1234.5, "hops": 1}

        Note:
            Use this for disease-gene bottleneck analysis. Rust accelerator extracts 
            subgraph via process-static adjacency cache (~microseconds), then runs 
            rayon parallel Brandes on the subgraph only.
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        result = self._store.execute_betweenness_neighborhood(seed, hops, sample_size, top_k)
        if result.error:
            return []
        return [{"id": r[0], "score": r[1]} for r in result.rows]
    def bfs_vector_rerank(
        self,
        seed: str,
        query_vec: List[float],
        hops: int = 2,
        top_k: int = 10,
        max_buckets: int = 32,
    ) -> List[Dict[str, Any]]:
        """Graph-filtered semantic search: fused BFS + vector reranking.

        Finds nodes that are BOTH reachable from a seed within `hops` BFS steps
        AND semantically similar to `query_vec`. Graph topology defines the
        candidate scope; vector similarity defines relevance. This is the
        biomedical drug-discovery pattern — "which genes are connected to this
        disease AND similar to my target gene?"

        Uses the NICHE quantized bucket index (`^NKG("q",...)`): the BFS frontier
        is pruned to nodes in the query's nearest IVF buckets before full-precision
        cosine reranking.

        Args:
            seed: Seed node ID to start the BFS from (e.g., "Gene::7157" for TP53).
            query_vec: Query embedding vector (same dimension as node embeddings).
            hops: BFS radius (default 2). Larger neighborhoods cost more.
            top_k: Maximum results to return (default 10).
            max_buckets: Number of nearest IVF buckets to scan (default 32).
                Higher = better recall, slower. 32 gives recall@10 ≈ 0.90 on
                400-dim TransE embeddings.

        Returns:
            List of dicts sorted by score descending, each containing:
                - id (str): Node identifier.
                - score (float): Cosine similarity to query_vec.
                - hops (int): BFS distance from seed.

        Example:
            >>> tp53_vec = engine.get_node_embedding("Gene::7157")
            >>> hits = engine.bfs_vector_rerank(
            ...     seed="Gene::7157", query_vec=tp53_vec, hops=1, top_k=10
            ... )
            >>> print(hits[0])  # {"id": "Gene::8626", "score": 0.63, "hops": 1}

        Note:
            Requires the NICHE bucket index to be built (see scripts/niche/).
            Returns [] if the bucket index is absent or the seed is not found.
            Performance: ObjectScript path ~28ms for hops=1 on a 18K-node graph.
            The sub-millisecond Rust accelerator path is planned for v2.1.x.
        """
        if not getattr(self, "_store", None):
            raise NotImplementedError("No store configured")
        result = self._store.execute_bfs_vector_rerank(seed, query_vec, hops, top_k, max_buckets)
        if result.error:
            return []
        return [{"id": r[0], "score": r[1], "hops": r[2]} for r in result.rows]
    def closeness_centrality(
        self,
        formula: str = "harmonic",
        direction: str = "out",
        max_hops: int = 0,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Closeness centrality — how close a node is to all other nodes.

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
            Harmonic formula = 1 / (average shortest-path distance), so it works 
            on disconnected components. Classical formula is undefined when any node 
            is unreachable.
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
        if result.error:
            return []
        return [{"id": row[0], "score": row[1]} for row in result.rows]
    def eigenvector_centrality(
        self,
        max_iter: int = 30,
        tol: float = 1e-6,
        top_k: int = 10000,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Eigenvector centrality — influence by neighbor influence.

        Iterative power method over the raw adjacency matrix A (not the transition 
        matrix). Measures influence: a node is important if it's connected to other 
        important nodes. L2-normalized output. Matches `networkx.eigenvector_centrality_numpy`.

        Args:
            max_iter: Maximum power iterations (default 30). Typical convergence: 5–15 iters.
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
        if result.error:
            return []
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
            gamma: Resolution parameter. 1.0 = ModularityVertexPartition (default,
                canonical Leiden). Values < 1.0 produce fewer, larger communities;
                values > 1.0 produce more, smaller communities.
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
            Uses Rust accelerator (leiden-rs) when libarno_callout.so is deployed.
            Falls back to Python leidenalg or networkx Louvain. Quality matches 
            leidenalg reference (ARI=1.0 on karate club).
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
        if result.error:
            return []

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
        """
        from iris_vector_graph._validate import TriangleCountInput
        validated = TriangleCountInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("triangle_count", False):
            raise NotImplementedError(
                f"Communities.triangle_count not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_triangle_count(validated.top_k, progress_callback)
        if result.error:
            return []
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
        """
        from iris_vector_graph._validate import SCCInput
        validated = SCCInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("scc", False):
            raise NotImplementedError(
                f"Communities.scc not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_scc(validated.top_k, progress_callback)
        if result.error:
            return []
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
        """
        from iris_vector_graph._validate import KCoreInput
        validated = KCoreInput(top_k=top_k)

        if not getattr(self, "_store", None) or not self._store.capabilities().get("k_core", False):
            raise NotImplementedError(
                f"Communities.k_core not supported by store "
                f"{type(self._store).__name__ if getattr(self, '_store', None) else 'None'}"
            )

        result = self._store.execute_k_core(validated.top_k, progress_callback)
        if result.error:
            return []
        return [{"id": row[0], "coreness": row[1]} for row in result.rows]
