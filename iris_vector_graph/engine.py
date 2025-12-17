#!/usr/bin/env python3
"""
IRIS Graph Core Engine - Domain-Agnostic Graph Operations

High-performance graph operations extracted from the biomedical implementation.
Provides vector search, text search, graph traversal, and hybrid fusion capabilities
that can be used across any domain.
"""

import json
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class IRISGraphEngine:
    """
    Domain-agnostic IRIS graph engine providing:
    - HNSW-optimized vector search (50ms performance)
    - Native IRIS iFind text search
    - Graph traversal with confidence filtering
    - Reciprocal Rank Fusion for hybrid ranking
    """

    # Class-level cache for SQL function availability (avoid repeated failed attempts)
    _ppr_sql_function_available = None

    # SQL function name - MUST NOT contain '_JSON' or 'JSON_' due to IRIS naming bug
    _PPR_SQL_FUNCTION_NAME = "kg_PPR"

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        self.conn = connection

    @classmethod
    def reset_sql_function_cache(cls):
        """Reset the SQL function availability cache (useful for testing)."""
        cls._ppr_sql_function_available = None

    # Vector Search Operations
    def kg_KNN_VEC(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        K-Nearest Neighbors vector search using HNSW optimization

        Args:
            query_vector: JSON array string like "[0.1,0.2,0.3,...]"
            k: Number of top results to return
            label_filter: Optional label to filter by (e.g., 'protein', 'gene', 'person', 'company')

        Returns:
            List of (entity_id, similarity_score) tuples
        """
        # Try optimized HNSW vector search first (50ms performance)
        try:
            return self._kg_KNN_VEC_hnsw_optimized(query_vector, k, label_filter)
        except Exception as e:
            logger.warning(f"HNSW optimized search failed: {e}")
            # Fallback to Python CSV implementation
            logger.warning("Falling back to Python CSV vector computation")
            return self._kg_KNN_VEC_python_optimized(query_vector, k, label_filter)

    def _kg_KNN_VEC_hnsw_optimized(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        HNSW-optimized vector search using native IRIS VECTOR functions

        Uses kg_NodeEmbeddings_optimized table with VECTOR(FLOAT, 768) and HNSW index.
        Performance: ~50ms for 10K vectors
        """
        cursor = self.conn.cursor()
        try:
            # Build query with optional label filter
            if label_filter is None:
                sql = f"""
                    SELECT TOP {k}
                        n.id,
                        VECTOR_COSINE(n.emb, TO_VECTOR(?)) as similarity
                    FROM kg_NodeEmbeddings_optimized n
                    ORDER BY similarity DESC
                """
                cursor.execute(sql, [query_vector])
            else:
                sql = f"""
                    SELECT TOP {k}
                        n.id,
                        VECTOR_COSINE(n.emb, TO_VECTOR(?)) as similarity
                    FROM kg_NodeEmbeddings_optimized n
                    LEFT JOIN rdf_labels L ON L.s = n.id
                    WHERE L.label = ?
                    ORDER BY similarity DESC
                """
                cursor.execute(sql, [query_vector, label_filter])

            results = cursor.fetchall()
            return [(entity_id, float(similarity)) for entity_id, similarity in results]

        except Exception as e:
            logger.error(f"HNSW optimized kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()

    def _kg_KNN_VEC_python_optimized(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        Fallback Python implementation using CSV parsing
        Performance: ~5.8s for 20K vectors (when HNSW not available)
        """
        cursor = self.conn.cursor()
        try:
            # Parse query vector from JSON string
            query_array = np.array(json.loads(query_vector))

            # Get embeddings with optional label filter (optimized query)
            if label_filter is None:
                sql = """
                    SELECT n.id, n.emb
                    FROM kg_NodeEmbeddings n
                    WHERE n.emb IS NOT NULL
                """
                cursor.execute(sql)
            else:
                sql = """
                    SELECT n.id, n.emb
                    FROM kg_NodeEmbeddings n
                    LEFT JOIN rdf_labels L ON L.s = n.id
                    WHERE n.emb IS NOT NULL
                      AND L.label = ?
                """
                cursor.execute(sql, [label_filter])

            # Compute similarities efficiently
            similarities = []
            batch_size = 1000  # Process in batches for memory efficiency

            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break

                for entity_id, emb_csv in batch:
                    try:
                        # Fast CSV parsing to numpy array
                        emb_array = np.fromstring(emb_csv, dtype=float, sep=',')

                        # Compute cosine similarity efficiently
                        dot_product = np.dot(query_array, emb_array)
                        query_norm = np.linalg.norm(query_array)
                        emb_norm = np.linalg.norm(emb_array)

                        if query_norm > 0 and emb_norm > 0:
                            cos_sim = dot_product / (query_norm * emb_norm)
                            similarities.append((entity_id, float(cos_sim)))

                    except Exception as emb_error:
                        # Skip problematic embeddings
                        continue

            # Sort by similarity and return top k
            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:k]

        except Exception as e:
            logger.error(f"Python optimized kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()

    # Text Search Operations
    def kg_TXT(self, query_text: str, k: int = 50, min_confidence: int = 0) -> List[Tuple[str, float]]:
        """
        Enhanced text search using JSON_TABLE for structured qualifier filtering

        Args:
            query_text: Text query string
            k: Number of results to return
            min_confidence: Minimum confidence score (0-1000 scale)

        Returns:
            List of (entity_id, relevance_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            sql = f"""
                SELECT TOP {k}
                    e.s AS entity_id,
                    (CAST(jt.confidence AS FLOAT) / 1000.0 +
                     CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END) AS relevance_score
                FROM rdf_edges e,
                     JSON_TABLE(
                        e.qualifiers, '$'
                        COLUMNS(confidence INTEGER PATH '$.confidence')
                     ) jt
                WHERE jt.confidence >= ? OR e.o_id LIKE ?
                ORDER BY relevance_score DESC
            """

            # Use text query for LIKE matching
            like_pattern = f'%{query_text}%'
            cursor.execute(sql, [like_pattern, min_confidence, like_pattern])

            results = cursor.fetchall()
            return [(entity_id, float(score)) for entity_id, score in results]

        except Exception as e:
            logger.error(f"kg_TXT failed: {e}")
            raise
        finally:
            cursor.close()

    # Graph Traversal Operations
    def kg_NEIGHBORHOOD_EXPANSION(self, entity_list: List[str], expansion_depth: int = 1, confidence_threshold: int = 500) -> List[Dict[str, Any]]:
        """
        Efficient neighborhood expansion for multiple entities using JSON_TABLE filtering

        Args:
            entity_list: List of seed entity IDs
            expansion_depth: Number of hops to expand (1-3 recommended)
            confidence_threshold: Minimum confidence for edges (0-1000 scale)

        Returns:
            List of expanded entities with metadata
        """
        if not entity_list:
            return []

        cursor = self.conn.cursor()
        try:
            # Build parameterized query for multiple entities
            entity_placeholders = ','.join(['?' for _ in entity_list])

            sql = f"""
                SELECT DISTINCT e.s, e.p, e.o_id, jt.confidence
                FROM rdf_edges e,
                     JSON_TABLE(e.qualifiers, '$' COLUMNS(confidence INTEGER PATH '$.confidence')) jt
                WHERE e.s IN ({entity_placeholders}) AND jt.confidence >= ?
                ORDER BY confidence DESC, e.s, e.p
            """

            params = entity_list + [confidence_threshold]
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'source': row[0],
                    'predicate': row[1],
                    'target': row[2],
                    'confidence': row[3]
                })

            return results

        except Exception as e:
            logger.error(f"kg_NEIGHBORHOOD_EXPANSION failed: {e}")
            raise
        finally:
            cursor.close()

    # Hybrid Fusion Operations
    def kg_RRF_FUSE(self, k: int, k1: int, k2: int, c: int, query_vector: str, query_text: str) -> List[Tuple[str, float, float, float]]:
        """
        Reciprocal Rank Fusion combining vector and text search results

        Implements the RRF algorithm from Cormack & Clarke (SIGIR 2009)

        Args:
            k: Final number of results to return
            k1: Number of vector search results to retrieve
            k2: Number of text search results to retrieve
            c: RRF parameter (typically 60)
            query_vector: Vector query as JSON string
            query_text: Text query string

        Returns:
            List of (entity_id, rrf_score, vector_score, text_score) tuples
        """
        try:
            # Get vector search results
            vector_results = self.kg_KNN_VEC(query_vector, k=k1)
            vector_dict = {entity_id: (rank + 1, score) for rank, (entity_id, score) in enumerate(vector_results)}

            # Get text search results
            text_results = self.kg_TXT(query_text, k=k2)
            text_dict = {entity_id: (rank + 1, score) for rank, (entity_id, score) in enumerate(text_results)}

            # Calculate RRF scores
            all_entities = set(vector_dict.keys()) | set(text_dict.keys())
            rrf_scores = []

            for entity_id in all_entities:
                rrf_score = 0.0

                # Vector contribution
                if entity_id in vector_dict:
                    vector_rank, vector_score = vector_dict[entity_id]
                    rrf_score += 1.0 / (c + vector_rank)
                else:
                    vector_score = 0.0

                # Text contribution
                if entity_id in text_dict:
                    text_rank, text_score = text_dict[entity_id]
                    rrf_score += 1.0 / (c + text_rank)
                else:
                    text_score = 0.0

                rrf_scores.append((entity_id, rrf_score, vector_score, text_score))

            # Sort by RRF score and return top k
            rrf_scores.sort(key=lambda x: x[1], reverse=True)
            return rrf_scores[:k]

        except Exception as e:
            logger.error(f"kg_RRF_FUSE failed: {e}")
            raise

    def kg_VECTOR_GRAPH_SEARCH(self, query_vector: str, query_text: str = None, k: int = 15,
                             expansion_depth: int = 1, min_confidence: float = 0.5) -> List[Dict[str, Any]]:
        """
        Multi-modal search combining vector similarity, graph expansion, and text relevance

        Args:
            query_vector: Vector query as JSON string
            query_text: Optional text query
            k: Number of final results
            expansion_depth: Graph expansion depth
            min_confidence: Minimum confidence threshold

        Returns:
            List of ranked entities with combined scores
        """
        try:
            # Step 1: Vector search for semantic similarity
            k_vector = min(k * 2, 50)  # Get more candidates for fusion
            vector_results = self.kg_KNN_VEC(query_vector, k=k_vector)
            vector_entities = [entity_id for entity_id, _ in vector_results]

            # Step 2: Graph expansion around vector results
            if vector_entities:
                graph_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
                    vector_entities,
                    expansion_depth,
                    int(min_confidence * 1000)
                )
                expanded_entities = list(set([item['target'] for item in graph_expansion]))
            else:
                expanded_entities = []

            # Step 3: Combine with text search if provided
            if query_text:
                text_results = self.kg_TXT(query_text, k=k_vector * 2, min_confidence=int(min_confidence * 1000))
                text_entities = [entity_id for entity_id, _ in text_results]
                all_entities = list(set(vector_entities + expanded_entities + text_entities))
            else:
                all_entities = list(set(vector_entities + expanded_entities))

            # Step 4: Score combination (simplified)
            combined_results = []
            for entity_id in all_entities[:k]:
                # Get scores from different sources
                vector_sim = next((score for eid, score in vector_results if eid == entity_id), 0.0)

                # Simple weighted combination
                combined_score = vector_sim  # Can be enhanced with graph centrality, text relevance

                combined_results.append({
                    'entity_id': entity_id,
                    'combined_score': combined_score,
                    'vector_similarity': vector_sim,
                    'in_graph_expansion': entity_id in expanded_entities
                })

            # Sort by combined score
            combined_results.sort(key=lambda x: x['combined_score'], reverse=True)
            return combined_results[:k]

        except Exception as e:
            logger.error(f"kg_VECTOR_GRAPH_SEARCH failed: {e}")
            raise

    # Personalized PageRank Operations
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
            raise ValueError(f"reverse_edge_weight must be non-negative, got: {reverse_edge_weight}")
        if not seed_entities:
            raise ValueError("seed_entities must contain at least one entity")

        # Skip SQL function if we already know it's not available (cached failure)
        if IRISGraphEngine._ppr_sql_function_available is False:
            return self._kg_PERSONALIZED_PAGERANK_python_fallback(
                seed_entities, damping_factor, max_iterations, tolerance,
                return_top_k, bidirectional, reverse_edge_weight
            )

        # Auto-deploy SQL function if not yet attempted
        if IRISGraphEngine._ppr_sql_function_available is None:
            self._auto_deploy_ppr_sql_function()

        cursor = self.conn.cursor()
        try:
            # Convert seed_entities list to JSON string for SQL function
            seed_json = json.dumps(seed_entities)

            # Call IRIS embedded Python via SQL function for 10-50x speedup
            cursor.execute(f"""
                SELECT {self._PPR_SQL_FUNCTION_NAME}(?, ?, ?, ?, ?)
            """, [
                seed_json,
                damping_factor,
                max_iterations,
                1 if bidirectional else 0,  # INT in SQL
                reverse_edge_weight
            ])

            result = cursor.fetchone()
            if result and result[0]:
                # Mark SQL function as available
                IRISGraphEngine._ppr_sql_function_available = True

                # Parse JSON array: [{"nodeId": "X", "pagerank": 0.15}, ...]
                json_results = json.loads(result[0])
                scores = {item['nodeId']: item['pagerank'] for item in json_results}

                # Filter out zero scores
                scores = {k: v for k, v in scores.items() if v > 0}

                # Apply return_top_k if specified
                if return_top_k is not None and return_top_k > 0:
                    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    scores = dict(sorted_items[:return_top_k])

                logger.debug(f"PageRank via IRIS embedded Python: {len(scores)} results")
                return scores

            return {}

        except Exception as e:
            # Cache that SQL function is not available to avoid repeated failed attempts
            error_str = str(e).lower()
            if "not found" in error_str or "does not exist" in error_str:
                IRISGraphEngine._ppr_sql_function_available = False
                logger.info(f"IRIS SQL function {self._PPR_SQL_FUNCTION_NAME} not available, using Python fallback")
            elif "parameter" in error_str or "invalid argument" in error_str:
                # Embedded Python call failed - likely schema mismatch or missing tables
                # This is NOT a Community Edition issue - all IRIS versions have embedded Python
                IRISGraphEngine._ppr_sql_function_available = False
                logger.warning(f"IRIS embedded PageRank failed (check SQLUSER schema has nodes/rdf_edges views): {e}")
            else:
                logger.warning(f"IRIS embedded PageRank failed: {e}, falling back to pure Python")
            cursor.close()
            return self._kg_PERSONALIZED_PAGERANK_python_fallback(
                seed_entities, damping_factor, max_iterations, tolerance,
                return_top_k, bidirectional, reverse_edge_weight
            )
        finally:
            try:
                cursor.close()
            except Exception:
                pass  # Already closed in fallback path

    def _auto_deploy_ppr_sql_function(self) -> None:
        """Auto-deploy the PPR SQL function using IRIS embedded Python.

        This enables 10-50x performance improvement by executing PageRank
        directly in the IRIS server process using LANGUAGE PYTHON SQL functions.
        """
        cursor = self.conn.cursor()
        try:
            # Drop existing function to ensure clean state
            try:
                cursor.execute(f"DROP FUNCTION IF EXISTS {self._PPR_SQL_FUNCTION_NAME}")
                self.conn.commit()
            except Exception:
                pass  # Function might not exist

            # Create SQL function using LANGUAGE PYTHON directly
            # This is more portable than calling ObjectScript class methods
            # NOTE: Use VARCHAR(65535) instead of VARCHAR(MAX) - MAX doesn't work
            # correctly with LANGUAGE PYTHON return types in some IRIS versions
            # NOTE: Use explicit SQLUSER schema prefix for table access
            # NOTE: Function name MUST NOT contain '_JSON' or 'JSON_' due to IRIS bug
            # NOTE: Cannot use f-string here because Python code in LANGUAGE PYTHON has {}
            ppr_function_sql = '''
                CREATE OR REPLACE FUNCTION ''' + self._PPR_SQL_FUNCTION_NAME + '''(
                  seedEntities VARCHAR(32000),
                  dampingFactor DOUBLE DEFAULT 0.85,
                  maxIterations INT DEFAULT 100,
                  bidirectional INT DEFAULT 0,
                  reverseEdgeWeight DOUBLE DEFAULT 1.0
                )
                RETURNS VARCHAR(65535)
                LANGUAGE PYTHON
                {
                    import iris
                    import json

                    # Parse seed entities
                    seeds = set()
                    if seedEntities and seedEntities.strip():
                        try:
                            seeds = set(json.loads(seedEntities))
                        except:
                            pass

                    # Get nodes - use SQLUSER schema explicitly
                    nodes = [r[0] for r in iris.sql.exec("SELECT node_id FROM SQLUSER.nodes")]
                    num_nodes = len(nodes)
                    if num_nodes == 0:
                        return "[]"

                    # Build adjacency
                    in_edges = {}
                    out_degree = {}
                    for src, dst in iris.sql.exec("SELECT s, o_id FROM SQLUSER.rdf_edges"):
                        in_edges.setdefault(dst, []).append((src, 1.0))
                        out_degree[src] = out_degree.get(src, 0) + 1

                    # Reverse edges if bidirectional
                    if bidirectional and reverseEdgeWeight > 0:
                        for dst, src in iris.sql.exec("SELECT o_id, s FROM SQLUSER.rdf_edges"):
                            in_edges.setdefault(src, []).append((dst, reverseEdgeWeight))
                            out_degree[dst] = out_degree.get(dst, 0) + 1

                    for n in nodes:
                        if n not in out_degree:
                            out_degree[n] = 0

                    # Initialize ranks (personalized if seeds provided)
                    valid_seeds = [s for s in seeds if s in set(nodes)]
                    if valid_seeds:
                        seed_set = set(valid_seeds)
                        seed_count = len(valid_seeds)
                        ranks = {n: (1.0/seed_count if n in seed_set else 0) for n in nodes}
                        teleport = (1.0 - dampingFactor) / seed_count
                    else:
                        seed_set = set(nodes)
                        ranks = {n: 1.0/num_nodes for n in nodes}
                        teleport = (1.0 - dampingFactor) / num_nodes

                    # PageRank iterations
                    for _ in range(int(maxIterations)):
                        new_ranks = {}
                        for node in nodes:
                            rank = teleport if node in seed_set else 0
                            for src, w in in_edges.get(node, []):
                                if out_degree.get(src, 0) > 0:
                                    rank += dampingFactor * w * ranks.get(src, 0) / out_degree[src]
                            new_ranks[node] = rank
                        ranks = new_ranks

                    # Build result as JSON array
                    results = [{"nodeId": n, "pagerank": r} for n, r in sorted(ranks.items(), key=lambda x: -x[1]) if r > 0]
                    return json.dumps(results)
                }
            '''

            cursor.execute(ppr_function_sql)
            self.conn.commit()
            IRISGraphEngine._ppr_sql_function_available = True
            logger.info("IVG PPR SQL function auto-deployed (LANGUAGE PYTHON)")

        except Exception as e:
            logger.debug(f"Could not auto-deploy PPR SQL function: {e}")
            IRISGraphEngine._ppr_sql_function_available = False
        finally:
            cursor.close()

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
        cursor = self.conn.cursor()
        try:
            # Step 1: Get all nodes
            cursor.execute("SELECT node_id FROM nodes")
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
            cursor.execute("SELECT s, o_id FROM rdf_edges")

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
                cursor.execute("SELECT o_id, s FROM rdf_edges")
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
            ranks = {node: (1.0 / seed_count if node in seed_set else 0.0) for node in nodes}

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
                                rank += damping_factor * weight * (ranks.get(src, 0) / out_degree[src])

                    new_ranks[node] = rank
                    max_diff = max(max_diff, abs(rank - ranks.get(node, 0)))

                ranks = new_ranks

                # Check convergence
                if max_diff < tolerance:
                    logger.debug(f"PageRank converged after {iteration + 1} iterations (Python fallback)")
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