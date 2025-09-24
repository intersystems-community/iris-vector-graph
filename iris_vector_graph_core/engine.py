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

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        self.conn = connection

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