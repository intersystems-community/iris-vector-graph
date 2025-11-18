#!/usr/bin/env python3
"""
Reciprocal Rank Fusion (RRF) and Hybrid Search

Implements advanced ranking fusion algorithms for combining multiple search modalities:
- Vector similarity search
- Text relevance search
- Graph structural search
- Confidence-based filtering

Based on Cormack & Clarke (SIGIR 2009) RRF algorithm.
"""

import logging
from typing import List, Tuple, Dict, Any, Optional, Set
import numpy as np

logger = logging.getLogger(__name__)


class RRFFusion:
    """
    Reciprocal Rank Fusion for combining multiple search result lists
    """

    @staticmethod
    def fuse_results(result_lists: List[List[Tuple[str, float]]], c: int = 60) -> List[Tuple[str, float]]:
        """
        Fuse multiple ranked result lists using RRF algorithm

        Args:
            result_lists: List of result lists, each containing (entity_id, score) tuples
            c: RRF constant parameter (typically 60)

        Returns:
            Fused list of (entity_id, rrf_score) tuples sorted by RRF score
        """
        # Collect all unique entities
        all_entities: Set[str] = set()
        for result_list in result_lists:
            all_entities.update(entity_id for entity_id, _ in result_list)

        # Calculate RRF scores
        rrf_scores = {}
        for entity_id in all_entities:
            rrf_score = 0.0

            for result_list in result_lists:
                # Find rank of entity in this list (1-indexed)
                rank = None
                for i, (eid, _) in enumerate(result_list):
                    if eid == entity_id:
                        rank = i + 1
                        break

                # Add RRF contribution if entity found in this list
                if rank is not None:
                    rrf_score += 1.0 / (c + rank)

            rrf_scores[entity_id] = rrf_score

        # Sort by RRF score
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    @staticmethod
    def weighted_fusion(result_lists: List[List[Tuple[str, float]]], weights: List[float]) -> List[Tuple[str, float]]:
        """
        Weighted fusion of multiple result lists

        Args:
            result_lists: List of result lists
            weights: Weights for each result list (should sum to 1.0)

        Returns:
            Weighted fusion results
        """
        if len(result_lists) != len(weights):
            raise ValueError("Number of result lists must match number of weights")

        # Normalize weights
        weight_sum = sum(weights)
        if weight_sum > 0:
            weights = [w / weight_sum for w in weights]

        # Collect all entities and their scores
        entity_scores = {}
        for i, result_list in enumerate(result_lists):
            weight = weights[i]
            for entity_id, score in result_list:
                if entity_id not in entity_scores:
                    entity_scores[entity_id] = 0.0
                entity_scores[entity_id] += weight * score

        # Sort by weighted score
        sorted_results = sorted(entity_scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results


class HybridSearchFusion:
    """
    Advanced hybrid search combining vector, text, and graph modalities
    """

    def __init__(self, graph_engine):
        """Initialize with graph engine instance"""
        self.graph_engine = graph_engine

    def multi_modal_search(self,
                         query_vector: Optional[str] = None,
                         query_text: Optional[str] = None,
                         entity_types: Optional[List[str]] = None,
                         k: int = 15,
                         fusion_method: str = "rrf",
                         weights: Optional[List[float]] = None) -> List[Dict[str, Any]]:
        """
        Multi-modal search combining vector, text, and graph signals

        Args:
            query_vector: Optional vector query (JSON string)
            query_text: Optional text query
            entity_types: Optional entity types to filter by
            k: Number of final results
            fusion_method: "rrf" or "weighted"
            weights: Weights for weighted fusion [vector, text, graph]

        Returns:
            List of ranked entities with detailed scores
        """
        if not query_vector and not query_text:
            raise ValueError("At least one of query_vector or query_text must be provided")

        result_lists = []
        search_modes = []

        # Vector search
        if query_vector:
            try:
                k_vector = min(k * 3, 100)  # Get more candidates for fusion
                vector_results = self.graph_engine.kg_KNN_VEC(query_vector, k=k_vector)
                result_lists.append(vector_results)
                search_modes.append("vector")
                logger.info(f"Vector search returned {len(vector_results)} results")
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # Text search
        if query_text:
            try:
                k_text = min(k * 3, 100)
                text_results = self.graph_engine.kg_TXT(query_text, k=k_text)
                result_lists.append(text_results)
                search_modes.append("text")
                logger.info(f"Text search returned {len(text_results)} results")
            except Exception as e:
                logger.warning(f"Text search failed: {e}")

        # Graph expansion (if we have initial results)
        if result_lists:
            try:
                # Get top entities from combined initial results
                initial_entities = set()
                for result_list in result_lists:
                    initial_entities.update(entity_id for entity_id, _ in result_list[:k])

                if initial_entities:
                    graph_expansion = self.graph_engine.kg_NEIGHBORHOOD_EXPANSION(
                        list(initial_entities)[:20],  # Limit seed entities
                        expansion_depth=1,
                        confidence_threshold=600
                    )

                    # Convert graph results to scored list
                    graph_scores = {}
                    for item in graph_expansion:
                        target_id = item['target']
                        confidence = item['confidence'] / 1000.0  # Normalize to 0-1
                        if target_id not in graph_scores:
                            graph_scores[target_id] = 0.0
                        graph_scores[target_id] = max(graph_scores[target_id], confidence)

                    graph_results = list(graph_scores.items())
                    graph_results.sort(key=lambda x: x[1], reverse=True)

                    result_lists.append(graph_results[:k*2])
                    search_modes.append("graph")
                    logger.info(f"Graph expansion returned {len(graph_results)} results")

            except Exception as e:
                logger.warning(f"Graph expansion failed: {e}")

        # Fusion
        if not result_lists:
            logger.error("No search results available for fusion")
            return []

        if fusion_method == "rrf":
            fused_results = RRFFusion.fuse_results(result_lists)
        elif fusion_method == "weighted":
            if not weights:
                # Default weights: vector=0.5, text=0.3, graph=0.2
                default_weights = [0.5, 0.3, 0.2]
                weights = default_weights[:len(result_lists)]
            fused_results = RRFFusion.weighted_fusion(result_lists, weights)
        else:
            raise ValueError(f"Unknown fusion method: {fusion_method}")

        # Build detailed results
        detailed_results = []
        for i, (entity_id, fusion_score) in enumerate(fused_results[:k]):
            result = {
                'entity_id': entity_id,
                'fusion_score': fusion_score,
                'rank': i + 1,
                'search_modes': []
            }

            # Add scores from individual search modes
            for j, (result_list, mode) in enumerate(zip(result_lists, search_modes)):
                mode_score = 0.0
                mode_rank = None

                for rank, (eid, score) in enumerate(result_list):
                    if eid == entity_id:
                        mode_score = score
                        mode_rank = rank + 1
                        break

                result['search_modes'].append({
                    'mode': mode,
                    'score': mode_score,
                    'rank': mode_rank
                })

            detailed_results.append(result)

        return detailed_results

    def adaptive_search(self, query: str, k: int = 15) -> List[Dict[str, Any]]:
        """
        Adaptive search that automatically determines the best search strategy

        Args:
            query: Natural language query
            k: Number of results

        Returns:
            Adaptive search results
        """
        # Simple heuristics for search strategy selection
        query_lower = query.lower()

        # Check for entity-like patterns
        has_entity_indicators = any(keyword in query_lower for keyword in [
            'what is', 'who is', 'where is', 'find', 'search for', 'lookup'
        ])

        # Check for relationship patterns
        has_relationship_indicators = any(keyword in query_lower for keyword in [
            'related to', 'connected to', 'associated with', 'interacts with',
            'similar to', 'linked to'
        ])

        # Determine search strategy
        use_text = True  # Always useful for entity matching
        use_vector = len(query.split()) > 2  # Use vector for longer, semantic queries
        use_graph = has_relationship_indicators  # Use graph for relationship queries

        logger.info(f"Adaptive search strategy - Text: {use_text}, Vector: {use_vector}, Graph: {use_graph}")

        # Execute multi-modal search
        try:
            results = self.multi_modal_search(
                query_vector=None,  # Would need to generate embedding
                query_text=query if use_text else None,
                k=k,
                fusion_method="rrf"
            )
            return results
        except Exception as e:
            logger.error(f"Adaptive search failed: {e}")
            # Fallback to simple text search
            try:
                text_results = self.graph_engine.kg_TXT(query, k=k)
                return [{'entity_id': eid, 'fusion_score': score, 'rank': i+1, 'search_modes': [{'mode': 'text_fallback', 'score': score, 'rank': i+1}]}
                       for i, (eid, score) in enumerate(text_results)]
            except Exception as fallback_error:
                logger.error(f"Fallback search also failed: {fallback_error}")
                return []