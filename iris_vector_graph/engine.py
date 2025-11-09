#!/usr/bin/env python3
"""
IRIS Graph Core Engine - Domain-Agnostic Graph Operations

High-performance graph operations extracted from the biomedical implementation.
Provides vector search, text search, graph traversal, and hybrid fusion capabilities
that can be used across any domain.
"""

import json
import os
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

    def __init__(self, connection):
        """
        Initialize with IRIS database connection or ConnectionManager.

        Args:
            connection: Either a direct IRIS connection (iris.connect()) or
                       a ConnectionManager object with .get_connection() method
        """
        # Handle both direct connections and ConnectionManager
        if hasattr(connection, 'get_connection'):
            # ConnectionManager from iris-vector-rag
            self.connection_manager = connection
            self.conn = connection.get_connection()
            self._is_managed_connection = True
        else:
            # Direct IRIS connection
            self.connection_manager = None
            self.conn = connection
            self._is_managed_connection = False

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
        use_functional_index: Optional[bool] = None
    ) -> Dict[str, float]:
        """
        Compute Personalized PageRank scores for knowledge graph entities.

        Implements random walk with restart to seed entities using power iteration.

        Performance implementations (automatic selection):
        1. Functional Index (fastest): <10ms for 10K nodes using ^PPR Globals
        2. Pure Python (fallback): ~200ms for 10K nodes using SQL extraction

        Args:
            seed_entities: List of entity IDs to use as personalization seeds
            damping_factor: Probability of following an edge vs teleporting back to seed (default: 0.85)
            max_iterations: Maximum iterations for convergence (default: 100)
            tolerance: Convergence threshold - stop when max score change < tolerance (default: 1e-6)
            return_top_k: Optional limit to return only top K scored entities
            use_functional_index: Force Functional Index (True) or Pure Python (False).
                If None (default), auto-selects based on USE_PPR_FUNCTIONAL_INDEX env var.

        Returns:
            Dictionary mapping entity_id -> PPR score (probability distribution summing to 1.0)

        Environment Variables:
            USE_PPR_FUNCTIONAL_INDEX: Set to "1" or "true" to enable Functional Index (default: auto-detect)

        Raises:
            ValueError: If seed_entities is empty, damping_factor invalid, or seeds don't exist

        Examples:
            >>> scores = engine.kg_PERSONALIZED_PAGERANK(["PROTEIN:TP53"], top_k=20)
            >>> print(scores)
            {'PROTEIN:TP53': 0.152, 'PROTEIN:MDM2': 0.087, ...}
        """
        # T021: Implementation selection - try Functional Index first
        if use_functional_index is None:
            # Auto-detect based on environment variable
            use_functional_index_env = os.getenv('USE_PPR_FUNCTIONAL_INDEX', '').lower()
            use_functional_index = use_functional_index_env in ('1', 'true', 'yes')

        if use_functional_index:
            try:
                from iris_vector_graph.ppr_functional_index import compute_ppr_functional_index
                logger.info(f"Using Functional Index PPR for {len(seed_entities)} seed entities")
                scores = compute_ppr_functional_index(
                    self.conn,
                    seed_entities=seed_entities,
                    damping_factor=damping_factor,
                    max_iterations=max_iterations,
                    tolerance=tolerance
                )
                # Apply top_k filtering if requested
                if return_top_k is not None:
                    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    scores = dict(sorted_scores[:return_top_k])
                return scores
            except Exception as e:
                logger.warning(f"Functional Index PPR failed ({e}), falling back to Pure Python")

        # Fallback: Pure Python implementation
        from iris_vector_graph.ppr import (
            validate_ppr_inputs,
            get_all_graph_nodes,
            get_outdegrees,
            get_incoming_edges
        )

        # T017: Validate inputs
        validate_ppr_inputs(
            seed_entities, damping_factor, max_iterations, tolerance, self.conn
        )

        logger.info(f"Computing PPR (Pure Python) for {len(seed_entities)} seed entities")

        # Get all nodes in graph
        all_nodes = get_all_graph_nodes(self.conn)

        if len(all_nodes) == 0:
            logger.warning("Graph has no nodes")
            return {}

        # Initialize scores: uniform distribution over seeds, 0 for others
        scores = {}
        seed_set = set(seed_entities)
        uniform_seed_score = 1.0 / len(seed_entities)

        for node_id in all_nodes:
            scores[node_id] = uniform_seed_score if node_id in seed_set else 0.0

        # Get graph structure (outdegrees and incoming edges)
        outdegrees = get_outdegrees(self.conn, all_nodes)
        incoming = get_incoming_edges(self.conn, all_nodes)

        # Personalization vector (probability of teleporting to each node)
        personalization = {
            node_id: uniform_seed_score if node_id in seed_set else 0.0
            for node_id in all_nodes
        }

        # Power iteration
        iteration = 0
        converged = False

        for iteration in range(1, max_iterations + 1):
            prev_scores = scores.copy()
            new_scores = {}

            # Update each node's score
            for node_id in all_nodes:
                # Random walk component: sum of scores from incoming neighbors
                walk_score = 0.0
                if node_id in incoming:
                    for source_id in incoming[node_id]:
                        source_score = prev_scores[source_id]
                        source_outdegree = outdegrees[source_id]

                        # Nodes with outdegree 0 are sinks (distribute evenly to all)
                        if source_outdegree == 0:
                            walk_score += source_score / len(all_nodes)
                        else:
                            walk_score += source_score / source_outdegree

                # PPR formula: (1 - alpha) * personalization + alpha * walk
                new_scores[node_id] = (
                    (1 - damping_factor) * personalization[node_id] +
                    damping_factor * walk_score
                )

            # Check convergence
            max_change = max(
                abs(new_scores[node_id] - prev_scores[node_id])
                for node_id in all_nodes
            )

            scores = new_scores

            if max_change < tolerance:
                converged = True
                logger.debug(f"PPR converged in {iteration} iterations (max_change={max_change:.2e})")
                break

        # T019: Log convergence status
        if not converged:
            logger.warning(
                f"PPR did not converge after {max_iterations} iterations "
                f"(max_change={max_change:.2e}, tolerance={tolerance:.2e})"
            )

        logger.info(
            f"PPR computed for {len(seed_entities)} seeds, "
            f"returned {len(scores)} entities"
        )

        # Normalize to ensure scores sum to exactly 1.0 (fix floating point errors)
        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}

        # Filter to non-zero scores only (after normalization)
        scores = {k: v for k, v in scores.items() if v > 1e-10}

        # Return top-k if requested
        if return_top_k is not None:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            scores = dict(sorted_scores[:return_top_k])

        return scores

    def kg_PPR_RANK_DOCUMENTS(
        self,
        seed_entities: List[str],
        document_ids: Optional[List[str]] = None,
        top_k: int = 10,
        **ppr_kwargs
    ) -> List[Dict[str, Any]]:
        """
        Rank documents using Personalized PageRank scores.

        Computes PPR scores for entities, then scores documents by aggregating
        the scores of entities they contain.

        Args:
            seed_entities: Query entities for PPR computation
            document_ids: Optional list of document IDs to score (None = all documents)
            top_k: Number of top documents to return
            **ppr_kwargs: Additional arguments passed to kg_PERSONALIZED_PAGERANK
                (damping_factor, max_iterations, tolerance)

        Returns:
            List of document ranking dictionaries, each containing:
            - document_id: Document identifier
            - score: Aggregated PPR score
            - top_entities: List of top contributing entities with their scores
            - entity_count: Total number of entities in document

        Examples:
            >>> results = engine.kg_PPR_RANK_DOCUMENTS(
            ...     seed_entities=["PROTEIN:TP53"],
            ...     top_k=10
            ... )
            >>> print(results[0])
            {
                'document_id': 'doc_001',
                'score': 0.8734,
                'top_entities': [
                    {'entity_id': 'PROTEIN:TP53', 'score': 0.15},
                    {'entity_id': 'PROTEIN:MDM2', 'score': 0.12}
                ],
                'entity_count': 5
            }
        """
        # Compute PPR scores
        ppr_scores = self.kg_PERSONALIZED_PAGERANK(
            seed_entities=seed_entities,
            **ppr_kwargs
        )

        # Get document-entity relationships from rdf_props
        # Assuming documents are linked to entities via rdf_props or rdf_edges
        cursor = self.conn.cursor()
        try:
            # Query for document-entity relationships
            # This is a simplified implementation - adjust based on actual schema
            if document_ids is None:
                query = """
                    SELECT DISTINCT s as document_id, val as entity_id
                    FROM rdf_props
                    WHERE key = 'contains_entity'
                """
                cursor.execute(query)
            else:
                placeholders = ",".join(["?" for _ in document_ids])
                query = f"""
                    SELECT DISTINCT s as document_id, val as entity_id
                    FROM rdf_props
                    WHERE key = 'contains_entity'
                    AND s IN ({placeholders})
                """
                cursor.execute(query, document_ids)

            doc_entities = cursor.fetchall()

            # Aggregate scores by document
            doc_scores = {}
            for doc_id, entity_id in doc_entities:
                if doc_id not in doc_scores:
                    doc_scores[doc_id] = []

                entity_score = ppr_scores.get(entity_id, 0.0)
                if entity_score > 0:
                    doc_scores[doc_id].append({
                        'entity_id': entity_id,
                        'score': entity_score
                    })

            # Build result list
            results = []
            for doc_id, entities in doc_scores.items():
                # Aggregate score (sum of entity scores)
                total_score = sum(e['score'] for e in entities)

                # Sort entities by score descending
                entities_sorted = sorted(
                    entities,
                    key=lambda x: x['score'],
                    reverse=True
                )

                results.append({
                    'document_id': doc_id,
                    'score': total_score,
                    'top_entities': entities_sorted[:5],  # Top 5 contributing entities
                    'entity_count': len(entities)
                })

            # Sort documents by score descending
            results.sort(key=lambda x: x['score'], reverse=True)

            # Return top-k
            return results[:top_k]

        finally:
            cursor.close()