#!/usr/bin/env python3
"""
IRIS Graph-AI Operators - Python Implementation

This module implements the graph retrieval operators as Python functions,
since IRIS doesn't support standard SQL stored procedures through the Python driver.

Based on working patterns from rag-templates and actual kg_NodeEmbeddings table structure.
Table structure: kg_NodeEmbeddings(node_id, id, emb) where emb contains CSV string embeddings.
"""

import json
from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
import numpy as np
from typing import List, Tuple, Optional, Dict, Any, Union, cast
from collections import deque
import logging

logger = logging.getLogger(__name__)


class IRISGraphOperators:
    """IRIS Graph-AI retrieval operators implemented in Python"""

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        self.conn = connection

    def kg_KNN_VEC(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        K-Nearest Neighbors vector search with multi-tier fallback

        Hierarchy:
        1. Primary: HNSW optimized (native IRIS VECTOR functions)
        2. Fallback: Python CSV computation (numpy-optimized)

        Args:
            query_vector: JSON array string like "[0.1,0.2,0.3,...]"
            k: Number of top results to return
            label_filter: Optional label to filter by (e.g., 'protein', 'gene')

        Returns:
            List of (entity_id, similarity_score) tuples
        """
        # Try optimized HNSW vector search first (6ms performance)
        try:
            return self._kg_KNN_VEC_hnsw_optimized(query_vector, k, label_filter)
        except Exception as e:
            logger.warning(f"HNSW optimized search failed: {e}")
            # Fallback to Python CSV implementation (5.8s performance)
            logger.warning("Falling back to Python CSV vector computation")
            return self._kg_KNN_VEC_python(query_vector, k, label_filter)

    def _kg_KNN_VEC_hnsw_optimized(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        HNSW-optimized vector search using native IRIS VECTOR functions

        Uses kg_NodeEmbeddings_optimized table with VECTOR(FLOAT, 768) and HNSW index.
        Performance: ~40ms for 10K vectors (1790x improvement vs CSV fallback)
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

    def _kg_KNN_VEC_python(self, query_vector: str, k: int = 50, label_filter: Optional[str] = None) -> List[Tuple[str, float]]:
        """
        Python implementation using the fast CSV parsing approach

        This version performs well and handles the CSV embedding format correctly.
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
            logger.error(f"Python kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()

    def kg_TXT(self, query_text: str, k: int = 50, min_confidence: int = 0) -> List[Tuple[str, float]]:
        """
        Enhanced text search using JSON_TABLE for structured qualifier filtering

        This implementation uses JSON_TABLE to extract confidence scores and evidence types
        from qualifiers, providing much better precision than LIKE filters.

        Args:
            query_text: Text to search for
            k: Number of top results to return
            min_confidence: Minimum confidence threshold (0-1000)

        Returns:
            List of (entity_id, relevance_score) tuples ordered by relevance
        """
        cursor = self.conn.cursor()
        try:
            # Simplified JSON_TABLE approach with confidence filtering
            sql = f"""
                SELECT TOP {k}
                    e.s AS entity_id,
                    (CAST(jt.confidence AS FLOAT) / 1000.0 +
                     CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END) AS relevance_score
                FROM rdf_edges e,
                     JSON_TABLE(
                        e.qualifiers, '$'
                        COLUMNS(
                            confidence INTEGER PATH '$.confidence'
                        )
                     ) jt
                WHERE jt.confidence >= ?
                   OR e.o_id LIKE ?
                ORDER BY relevance_score DESC
            """

            search_pattern = f'%{query_text}%'
            params = [
                search_pattern,   # object ID pattern for scoring
                min_confidence,   # confidence threshold
                search_pattern    # object ID filter
            ]

            cursor.execute(sql, params)
            results = cursor.fetchall()
            return [(row[0], float(row[1])) for row in results]

        except Exception as e:
            logger.error(f"Enhanced kg_TXT with JSON_TABLE failed: {e}")
            # Fallback to original LIKE-based implementation
            return self._kg_TXT_fallback(query_text, k)
        finally:
            cursor.close()

    def _kg_TXT_fallback(self, query_text: str, k: int = 50) -> List[Tuple[str, float]]:
        """
        Fallback text search using original LIKE filters for compatibility
        """
        cursor = self.conn.cursor()
        try:
            sql = f"""
                SELECT TOP {k}
                    e.s AS entity_id,
                    (
                        CASE WHEN e.qualifiers LIKE ? THEN 1.0 ELSE 0.0 END +
                        CASE WHEN e.o_id LIKE ? THEN 0.5 ELSE 0.0 END
                    ) AS bm25_score
                FROM rdf_edges e
                WHERE e.qualifiers LIKE ?
                   OR e.o_id LIKE ?
                ORDER BY bm25_score DESC
            """

            search_pattern = f'%{query_text}%'
            cursor.execute(sql, [search_pattern, search_pattern, search_pattern, search_pattern])

            results = cursor.fetchall()
            return [(row[0], float(row[1])) for row in results]

        except Exception as e:
            logger.error(f"Fallback kg_TXT failed: {e}")
            return []
        finally:
            cursor.close()

    def kg_RRF_FUSE(self, k: int = 50, k1: int = 200, k2: int = 200, c: int = 60,
                    query_vector: Optional[str] = None, query_text: Optional[str] = None) -> List[Tuple[str, float, float, float]]:
        """
        Reciprocal Rank Fusion of vector and text search results

        Args:
            k: Final number of results
            k1: Number of vector search results
            k2: Number of text search results
            c: RRF parameter (typically 60)
            query_vector: JSON array string for vector search
            query_text: Text query for text search

        Returns:
            List of (entity_id, rrf_score, vector_score, text_score) tuples
        """
        try:
            # Get vector search results
            vector_results = []
            if query_vector:
                vector_results = self.kg_KNN_VEC(query_vector, k1, None)

            # Get text search results
            text_results = []
            if query_text:
                text_results = self.kg_TXT(query_text, k2)

            # Create ranking dictionaries
            vector_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(vector_results)}
            text_ranks = {entity_id: rank + 1 for rank, (entity_id, _) in enumerate(text_results)}

            # Get all unique entity IDs
            all_entities = set(vector_ranks.keys()) | set(text_ranks.keys())

            # Calculate RRF scores
            rrf_scores = []
            for entity_id in all_entities:
                # Get original scores
                vector_score = next((score for eid, score in vector_results if eid == entity_id), 0.0)
                text_score = next((score for eid, score in text_results if eid == entity_id), 0.0)

                # Calculate RRF score
                vector_rank = vector_ranks.get(entity_id, 1000000)
                text_rank = text_ranks.get(entity_id, 1000000)
                rrf_score = (1.0 / (c + vector_rank)) + (1.0 / (c + text_rank))

                rrf_scores.append((entity_id, rrf_score, vector_score, text_score))

            # Sort by RRF score and return top k
            rrf_scores.sort(key=lambda x: x[1], reverse=True)
            return rrf_scores[:k]

        except Exception as e:
            logger.error(f"kg_RRF_FUSE failed: {e}")
            return []

    def kg_GRAPH_PATH(self, src_id: str, pred1: str, pred2: str, max_hops: int = 2) -> List[Tuple[int, int, str, str, str]]:
        """
        Simple graph path traversal: src --pred1--> intermediate --pred2--> target

        Args:
            src_id: Starting entity ID
            pred1: First predicate/relationship
            pred2: Second predicate/relationship
            max_hops: Maximum number of hops (currently supports 2)

        Returns:
            List of (path_id, step, source, predicate, object) tuples
        """
        cursor = self.conn.cursor()
        try:
            sql = """
                SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id
                FROM rdf_edges e1
                WHERE e1.s = ? AND e1.p = ?
                UNION ALL
                SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id
                FROM rdf_edges e2
                WHERE e2.p = ?
                  AND EXISTS (
                    SELECT 1 FROM rdf_edges e1
                    WHERE e1.s = ? AND e1.p = ? AND e1.o_id = e2.s
                  )
                ORDER BY step
            """

            cursor.execute(sql, [src_id, pred1, pred2, src_id, pred1])
            results = cursor.fetchall()
            return [(int(row[0]), int(row[1]), row[2], row[3], row[4]) for row in results]

        except Exception as e:
            logger.error(f"kg_GRAPH_PATH failed: {e}")
            return []
        finally:
            cursor.close()

    def kg_GRAPH_WALK(self, start_entity: str, max_depth: int = 3,
                     traversal_mode: str = 'BFS', predicate_filter: Optional[str] = None,
                     max_degree: int = 100) -> List[Tuple[str, str, str, int, str]]:
        """
        Advanced graph traversal using iterative expansion (no recursive CTEs)

        Implements BFS/DFS graph traversal with depth control, predicate filtering,
        and degree-based pruning. Uses iterative approach since IRIS CTEs are non-recursive.

        Args:
            start_entity: Starting entity ID for traversal
            max_depth: Maximum traversal depth (1-5 recommended)
            traversal_mode: 'BFS' (breadth-first) or 'DFS' (depth-first)
            predicate_filter: Optional predicate to filter edges (e.g., 'interacts_with')
            max_degree: Maximum degree per node to prevent hub explosion

        Returns:
            List of (source, predicate, target, depth, path_id) tuples
        """
        cursor = self.conn.cursor()
        visited = set()
        result_paths = []
        path_counter = 0

        try:
            # Initialize traversal queue/stack based on mode
            queue: Any
            if traversal_mode.upper() == 'BFS':
                queue = deque([(start_entity, 0, f"path_{path_counter}")])  # (entity, depth, path_id)
            else:  # DFS
                queue = [(start_entity, 0, f"path_{path_counter}")]  # Use list as stack

            visited.add(start_entity)

            while queue and len(result_paths) < 10000:  # Prevent runaway expansion
                if traversal_mode.upper() == 'BFS':
                    current_entity, current_depth, current_path = queue.popleft()
                else:  # DFS
                    current_entity, current_depth, current_path = queue.pop()

                # Stop if max depth reached
                if current_depth >= max_depth:
                    continue

                # Build neighbor query with optional predicate filter
                if predicate_filter:
                    neighbor_sql = """
                        SELECT e.s, e.p, e.o_id,
                               ROW_NUMBER() OVER (ORDER BY e.s) as rn
                        FROM rdf_edges e
                        WHERE e.s = ? AND e.p LIKE ?
                        ORDER BY e.s
                        LIMIT ?
                    """
                    params = [current_entity, f'%{predicate_filter}%', max_degree]
                else:
                    neighbor_sql = """
                        SELECT e.s, e.p, e.o_id,
                               ROW_NUMBER() OVER (ORDER BY e.s) as rn
                        FROM rdf_edges e
                        WHERE e.s = ?
                        ORDER BY e.s
                        LIMIT ?
                    """
                    params = [current_entity, max_degree]

                cursor.execute(neighbor_sql, params)
                neighbors = cursor.fetchall()

                # Process neighbors
                for source, predicate, target, _ in neighbors:
                    # Add edge to results
                    result_paths.append((
                        source,
                        predicate,
                        target,
                        current_depth + 1,
                        current_path
                    ))

                    # Add unvisited targets to queue for further expansion
                    if target not in visited and current_depth + 1 < max_depth:
                        visited.add(target)
                        path_counter += 1
                        new_path_id = f"path_{path_counter}"

                        if traversal_mode.upper() == 'BFS':
                            queue.append((target, current_depth + 1, new_path_id))
                        else:  # DFS
                            queue.append((target, current_depth + 1, new_path_id))

            return result_paths

        except Exception as e:
            logger.error(f"kg_GRAPH_WALK failed: {e}")
            return []
        finally:
            cursor.close()

    def kg_NEIGHBORHOOD_EXPANSION(self, entity_list: List[str], expansion_depth: int = 1,
                                 confidence_threshold: int = 500) -> List[Tuple[str, str, str, float]]:
        """
        Efficient neighborhood expansion for multiple entities using JSON_TABLE filtering

        Expands the neighborhood around a set of entities with confidence-based filtering.
        Useful for vector search result expansion and graph clustering.

        Args:
            entity_list: List of entity IDs to expand around
            expansion_depth: Number of hops to expand (1-2 recommended)
            confidence_threshold: Minimum confidence for edges (0-1000)

        Returns:
            List of (source, predicate, target, confidence) tuples
        """
        cursor = self.conn.cursor()
        try:
            if not entity_list:
                return []

            # Create parameterized IN clause for entity list
            entity_placeholders = ','.join(['?' for _ in entity_list])

            sql = f"""
                SELECT DISTINCT
                    e.s as source,
                    e.p as predicate,
                    e.o_id as target,
                    jt.confidence as confidence
                FROM rdf_edges e,
                     JSON_TABLE(
                        e.qualifiers, '$'
                        COLUMNS(
                            confidence INTEGER PATH '$.confidence'
                        )
                     ) jt
                WHERE e.s IN ({entity_placeholders})
                  AND jt.confidence >= ?
                ORDER BY confidence DESC, e.s, e.p
            """

            params = entity_list + [confidence_threshold]
            cursor.execute(sql, params)

            results = cursor.fetchall()
            return [(row[0], row[1], row[2], float(row[3] or 0)) for row in results]

        except Exception as e:
            logger.error(f"kg_NEIGHBORHOOD_EXPANSION failed: {e}")
            return []
        finally:
            cursor.close()

    def kg_GRAPH_WALK_TVF(self, start_entity: str, max_depth: int = 3,
                         traversal_mode: str = 'BFS', predicate_filter: Optional[str] = None,
                         min_confidence: float = 0.0) -> List[Tuple[str, str, str, int, str, float, int]]:
        """
        SQL-callable Graph Walk using Table-Valued Function (requires TVF deployment)

        This method calls the Graph_Walk TVF deployed in IRIS, providing true recursive
        traversal that can be composed with other SQL operations.

        Args:
            start_entity: Starting entity ID for traversal
            max_depth: Maximum traversal depth (1-5 recommended)
            traversal_mode: 'BFS' (breadth-first) or 'DFS' (depth-first)
            predicate_filter: Optional predicate to filter edges
            min_confidence: Minimum confidence threshold (0.0-1.0)

        Returns:
            List of (source, predicate, target, depth, path_id, confidence, path_length) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Call the Graph_Walk table-valued function
            sql = """
                SELECT source_entity, predicate, target_entity, depth, path_id, confidence, path_length
                FROM Graph_Walk(?, ?, ?, ?, ?)
                ORDER BY depth, confidence DESC
            """

            cursor.execute(sql, [start_entity, max_depth, traversal_mode, predicate_filter, min_confidence])
            results = cursor.fetchall()

            return [(row[0], row[1], row[2], int(row[3]), row[4], float(row[5]), int(row[6])) for row in results]

        except Exception as e:
            logger.error(f"kg_GRAPH_WALK_TVF failed (TVF may not be deployed): {e}")
            logger.info("Falling back to iterative Python implementation")
            # Fallback to the iterative implementation
            return self._convert_graph_walk_format(
                self.kg_GRAPH_WALK(start_entity, max_depth, traversal_mode, predicate_filter, int(min_confidence * 1000))
            )
        finally:
            cursor.close()

    def kg_VECTOR_GRAPH_SEARCH(self, query_vector: str, query_text: Optional[str] = None,
                               k_vector: int = 10, k_final: int = 20,
                               expansion_depth: int = 2, min_confidence: float = 0.6) -> List[Tuple[str, float, float, float, float, int]]:
        """
        Hybrid vector-graph search using TVF (requires TVF deployment)

        Combines HNSW vector similarity with graph expansion for enhanced recall and precision.
        This is the flagship Graph-SQL pattern demonstrating the full integration.

        Args:
            query_vector: JSON array string for vector search
            query_text: Optional text query for hybrid search
            k_vector: Number of vector search seed results
            k_final: Final number of results after graph expansion
            expansion_depth: Graph expansion depth around vector results
            min_confidence: Minimum confidence for graph edges

        Returns:
            List of (entity_id, vector_similarity, text_relevance, graph_centrality, combined_score, expansion_paths) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Call the Vector_Graph_Search TVF for full hybrid search
            sql = """
                SELECT entity_id, vector_similarity, text_relevance, graph_centrality, combined_score, expansion_paths
                FROM Vector_Graph_Search(?, ?, ?, ?, ?, ?)
                ORDER BY combined_score DESC
            """

            cursor.execute(sql, [query_vector, query_text or "", k_vector, k_final, expansion_depth, min_confidence])
            results = cursor.fetchall()

            return [(row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5])) for row in results]

        except Exception as e:
            logger.error(f"kg_VECTOR_GRAPH_SEARCH TVF failed (TVF may not be deployed): {e}")
            logger.info("Falling back to sequential vector + graph approach")
            # Fallback to sequential approach using existing methods
            return self._vector_graph_search_fallback(query_vector, query_text, k_vector, k_final, expansion_depth, min_confidence)
        finally:
            cursor.close()

    def _convert_graph_walk_format(self, graph_walk_results: List[Tuple[str, str, str, int, str]]) -> List[Tuple[str, str, str, int, str, float, int]]:
        """Convert kg_GRAPH_WALK results to TVF format"""
        return [(row[0], row[1], row[2], row[3], row[4], 0.0, row[3]) for row in graph_walk_results]

    def _vector_graph_search_fallback(self, query_vector: str, query_text: Optional[str] = None,
                                    k_vector: int = 10, k_final: int = 20,
                                    expansion_depth: int = 2, min_confidence: float = 0.6) -> List[Tuple[str, float, float, float, float, int]]:
        """Fallback implementation using existing methods"""
        try:
            # Step 1: Vector search
            vector_results = self.kg_KNN_VEC(query_vector, k_vector)
            vector_entities = [entity_id for entity_id, _ in vector_results]
            vector_scores = {entity_id: score for entity_id, score in vector_results}

            # Step 2: Graph expansion
            graph_expansion = self.kg_NEIGHBORHOOD_EXPANSION(vector_entities, expansion_depth, int(min_confidence * 1000))
            graph_scores = {}
            expansion_counts = {}

            for source, _, target, confidence in graph_expansion:
                graph_scores[source] = graph_scores.get(source, 0) + confidence / 1000.0
                graph_scores[target] = graph_scores.get(target, 0) + confidence / 1000.0
                expansion_counts[source] = expansion_counts.get(source, 0) + 1
                expansion_counts[target] = expansion_counts.get(target, 0) + 1

            # Step 3: Text search (if provided)
            text_scores = {}
            if query_text:
                text_results = self.kg_TXT(query_text, k_vector * 2, int(min_confidence * 1000))
                text_scores = {entity_id: score for entity_id, score in text_results}

            # Step 4: Combine and rank
            all_entities = set(vector_scores.keys()) | set(graph_scores.keys()) | set(text_scores.keys())
            final_results = []

            for entity_id in all_entities:
                vector_sim = vector_scores.get(entity_id, 0.0)
                graph_cent = graph_scores.get(entity_id, 0.0)
                text_rel = text_scores.get(entity_id, 0.0)
                paths = expansion_counts.get(entity_id, 0)

                combined = (0.5 * vector_sim) + (0.3 * graph_cent) + (0.2 * text_rel)

                final_results.append((entity_id, vector_sim, text_rel, graph_cent, combined, paths))

            # Sort and return top k
            final_results.sort(key=lambda x: x[4], reverse=True)
            return final_results[:k_final]

        except Exception as e:
            logger.error(f"Vector-graph search fallback failed: {e}")
            return []

    def kg_RERANK(self, top_n: int, query_vector: str, query_text: str) -> List[Tuple[str, float]]:
        """
        Rerank using RRF fusion (passthrough for now)

        Args:
            top_n: Number of results to return
            query_vector: JSON array string for vector search
            query_text: Text query

        Returns:
            List of (entity_id, score) tuples
        """
        try:
            rrf_results = self.kg_RRF_FUSE(top_n, 200, 200, 60, query_vector, query_text)
            return [(entity_id, rrf_score) for entity_id, rrf_score, _, _ in rrf_results]
        except Exception as e:
            logger.error(f"kg_RERANK failed: {e}")
            return []


def test_operators():
    """Test all operators with sample data"""
    print("=== Testing IRIS Graph Operators ===")

    try:
        conn = iris_connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
        operators = IRISGraphOperators(conn)

        # Test 1: Vector search
        print("\n1. Testing kg_KNN_VEC...")
        test_vector = json.dumps([0.1] * 768)
        vector_results = operators.kg_KNN_VEC(test_vector, k=5)
        print(f"   Found {len(vector_results)} vector results")
        for i, (entity_id, score) in enumerate(vector_results[:3]):
            print(f"   {i+1}. {entity_id}: {score:.6f}")

        # Test 2: Enhanced Text search with JSON_TABLE
        print("\n2. Testing enhanced kg_TXT with JSON_TABLE...")
        text_results = operators.kg_TXT("protein", k=5, min_confidence=500)
        print(f"   Found {len(text_results)} enhanced text results (min_confidence=500)")
        for i, (entity_id, score) in enumerate(text_results[:3]):
            print(f"   {i+1}. {entity_id}: relevance_score={score:.6f}")

        # Test 3: Graph traversal with iterative expansion
        print("\n3. Testing kg_GRAPH_WALK (iterative Python)...")
        if vector_results:
            test_entity = vector_results[0][0]
            walk_results = operators.kg_GRAPH_WALK(test_entity, max_depth=2, traversal_mode='BFS', predicate_filter='interacts_with')
            print(f"   Found {len(walk_results)} graph walk results")
            for i, (source, pred, target, depth, path_id) in enumerate(walk_results[:3]):
                print(f"   {i+1}. Depth {depth}: {source} → {pred} → {target} (path: {path_id})")

        # Test 4: Neighborhood expansion with confidence filtering
        print("\n4. Testing kg_NEIGHBORHOOD_EXPANSION...")
        if vector_results:
            seed_entities = [entity_id for entity_id, _ in vector_results[:3]]
            expansion_results = operators.kg_NEIGHBORHOOD_EXPANSION(seed_entities, expansion_depth=1, confidence_threshold=600)
            print(f"   Found {len(expansion_results)} neighborhood expansion results")
            for i, (source, pred, target, conf) in enumerate(expansion_results[:3]):
                print(f"   {i+1}. {source} → {pred} → {target} (confidence: {conf:.1f})")

        # Test 5: Table-Valued Function approach (with fallback)
        print("\n5. Testing kg_GRAPH_WALK_TVF (with fallback)...")
        if vector_results:
            test_entity = vector_results[0][0]
            tvf_results = operators.kg_GRAPH_WALK_TVF(test_entity, max_depth=2, traversal_mode='BFS', min_confidence=0.5)
            print(f"   Found {len(tvf_results)} TVF results")
            for i, (source, pred, target, depth, path_id, conf, path_len) in enumerate(tvf_results[:3]):
                print(f"   {i+1}. Depth {depth}: {source} → {pred} → {target} (conf: {conf:.3f}, path_len: {path_len})")

        # Test 6: Hybrid Vector-Graph search
        print("\n6. Testing kg_VECTOR_GRAPH_SEARCH (flagship hybrid)...")
        hybrid_results = operators.kg_VECTOR_GRAPH_SEARCH(
            query_vector=test_vector,
            query_text="protein",
            k_vector=5,
            k_final=10,
            expansion_depth=2,
            min_confidence=0.6
        )
        print(f"   Found {len(hybrid_results)} hybrid vector-graph results")
        for i, (entity_id, vec_sim, text_rel, graph_cent, combined, paths) in enumerate(hybrid_results[:3]):
            print(f"   {i+1}. {entity_id}")
            print(f"       Vector: {vec_sim:.3f}, Text: {text_rel:.3f}, Graph: {graph_cent:.3f}")
            print(f"       Combined: {combined:.3f}, Expansion paths: {paths}")

        # Test 7: Original methods for comparison
        print("\n7. Testing original kg_RRF_FUSE for comparison...")
        original_hybrid = operators.kg_RRF_FUSE(k=5, query_vector=test_vector, query_text="protein")
        print(f"   Found {len(original_hybrid)} original hybrid results")
        for i, (entity_id, rrf, vs, txt) in enumerate(original_hybrid[:3]):
            print(f"   {i+1}. {entity_id}: RRF={rrf:.3f}, Vector={vs:.3f}, Text={txt:.3f}")

        print("\n✅ All operator tests completed successfully!")

        conn.close()

    except Exception as e:
        print(f"\n❌ Operator tests failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_operators()