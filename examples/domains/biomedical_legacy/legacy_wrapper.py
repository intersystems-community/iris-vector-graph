#!/usr/bin/env python3
"""
Legacy Wrapper for Backward Compatibility

Maintains compatibility with existing iris_graph_operators.py interface
while using the new modular architecture under the hood.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from biomedical.biomedical_engine import BiomedicalGraphEngine

logger = logging.getLogger(__name__)


class IRISGraphOperators(BiomedicalGraphEngine):
    """
    Legacy wrapper maintaining backward compatibility with existing code

    This class preserves the original iris_graph_operators.py interface
    while using the new modular architecture internally.
    """

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        super().__init__(connection)
        logger.info("Using new modular architecture with legacy compatibility wrapper")

    # Legacy method signatures for backward compatibility
    def kg_KNN_VEC(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        Legacy vector search method - maintains original signature
        Uses new HNSW-optimized implementation under the hood
        """
        return super().kg_KNN_VEC(query_vector, k, label_filter)

    def kg_TXT(
        self, query_text: str, k: int = 50, min_confidence: int = 0
    ) -> List[Tuple[str, float]]:
        """
        Legacy text search method - maintains original signature
        Uses new JSON_TABLE implementation under the hood
        """
        return super().kg_TXT(query_text, k, min_confidence)

    def kg_RRF_FUSE(
        self, k: int, k1: int, k2: int, c: int, query_vector: str, query_text: str
    ) -> List[Tuple[str, float, float, float]]:
        """
        Legacy RRF fusion method - maintains original signature
        Uses new RRF implementation under the hood
        """
        return super().kg_RRF_FUSE(k, k1, k2, c, query_vector, query_text)

    def kg_NEIGHBORHOOD_EXPANSION(
        self, entity_list: List[str], expansion_depth: int = 1, confidence_threshold: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Legacy neighborhood expansion method - maintains original signature
        Uses new JSON_TABLE implementation under the hood
        """
        return super().kg_NEIGHBORHOOD_EXPANSION(entity_list, expansion_depth, confidence_threshold)

    # Additional legacy methods that may exist in the original implementation
    def kg_GRAPH_PATH(
        self, src_id: str, pred1: str, pred2: str, max_hops: int = 2
    ) -> List[Tuple[int, int, str, str, str]]:
        """
        Legacy graph path method - basic implementation
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

    def kg_GRAPH_WALK(
        self,
        start_entity: str,
        max_depth: int = 3,
        traversal_mode: str = "BFS",
        predicate_filter: Optional[str] = None,
        max_degree: int = 100,
    ) -> List[Tuple[str, str, str, int, str]]:
        """
        Legacy graph walk method - simplified implementation
        """
        # For backward compatibility, use neighborhood expansion
        expansion_results = self.kg_NEIGHBORHOOD_EXPANSION(
            [start_entity],
            expansion_depth=min(max_depth, 2),  # Limit depth for performance
            confidence_threshold=500,
        )

        # Convert to legacy format
        walk_results = []
        for i, result in enumerate(expansion_results):
            walk_results.append(
                (
                    result["source"],
                    result["predicate"],
                    result["target"],
                    1,  # Simplified depth
                    f"path_{i}",
                )
            )

        return walk_results[:max_degree]  # Respect max_degree limit

    def kg_RERANK(
        self, entity_ids: List[str], query_vector: str, rerank_weights: List[float] = None
    ) -> List[Tuple[str, float]]:
        """
        Legacy rerank method - uses vector similarity for reranking
        """
        if not entity_ids:
            return []

        # Get vector similarities for reranking
        vector_results = self.kg_KNN_VEC(query_vector, k=len(entity_ids) * 2)
        vector_scores = {entity_id: score for entity_id, score in vector_results}

        # Rerank input entities by vector similarity
        reranked = []
        for entity_id in entity_ids:
            score = vector_scores.get(entity_id, 0.0)
            reranked.append((entity_id, score))

        # Sort by score
        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked
