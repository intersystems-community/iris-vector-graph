#!/usr/bin/env python3
"""
Biomedical Graph Engine - Domain-Specific Wrapper

Provides biomedical-specific functionality on top of the generic iris_vector_graph.
Includes specialized entity types, predicates, and search methods for biomedical knowledge graphs.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.fusion import HybridSearchFusion

logger = logging.getLogger(__name__)


class BiomedicalGraphEngine(IRISGraphEngine):
    """
    Biomedical-specific graph engine with domain knowledge
    """

    # Biomedical entity types
    BIOMEDICAL_ENTITY_TYPES = [
        "protein",
        "gene",
        "disease",
        "drug",
        "compound",
        "pathway",
        "tissue",
        "cell_type",
        "organism",
        "phenotype",
        "variant",
    ]

    # Biomedical predicates/relationships
    BIOMEDICAL_PREDICATES = [
        "interacts_with",
        "regulates",
        "causes",
        "treats",
        "associates_with",
        "expressed_in",
        "located_in",
        "part_of",
        "similar_to",
        "binds_to",
    ]

    def __init__(self, connection):
        """Initialize biomedical engine with IRIS connection"""
        super().__init__(connection)
        self.fusion_engine = HybridSearchFusion(self)

    def search_proteins(
        self, query_vector: str = None, query_text: str = None, k: int = 15
    ) -> List[Dict[str, Any]]:
        """
        Search for proteins using hybrid search

        Args:
            query_vector: Optional vector query
            query_text: Optional text query
            k: Number of results

        Returns:
            List of protein entities with scores
        """
        return self.fusion_engine.multi_modal_search(
            query_vector=query_vector,
            query_text=query_text,
            entity_types=["protein"],
            k=k,
            fusion_method="rrf",
        )

    def search_genes(
        self, query_vector: str = None, query_text: str = None, k: int = 15
    ) -> List[Dict[str, Any]]:
        """
        Search for genes using hybrid search

        Args:
            query_vector: Optional vector query
            query_text: Optional text query
            k: Number of results

        Returns:
            List of gene entities with scores
        """
        return self.fusion_engine.multi_modal_search(
            query_vector=query_vector,
            query_text=query_text,
            entity_types=["gene"],
            k=k,
            fusion_method="rrf",
        )

    def search_diseases(
        self, query_vector: str = None, query_text: str = None, k: int = 15
    ) -> List[Dict[str, Any]]:
        """
        Search for diseases using hybrid search

        Args:
            query_vector: Optional vector query
            query_text: Optional text query
            k: Number of results

        Returns:
            List of disease entities with scores
        """
        return self.fusion_engine.multi_modal_search(
            query_vector=query_vector,
            query_text=query_text,
            entity_types=["disease"],
            k=k,
            fusion_method="rrf",
        )

    def drug_target_interactions(self, drug_entity: str, k: int = 20) -> List[Dict[str, Any]]:
        """
        Find drug-target interactions for a given drug

        Args:
            drug_entity: Drug entity ID
            k: Number of results

        Returns:
            List of target interactions with confidence scores
        """
        expansion_results = self.kg_NEIGHBORHOOD_EXPANSION(
            [drug_entity], expansion_depth=1, confidence_threshold=600
        )

        # Filter for interaction predicates
        interactions = []
        for item in expansion_results:
            if any(pred in item["predicate"] for pred in ["binds_to", "targets", "interacts_with"]):
                interactions.append(
                    {
                        "drug": drug_entity,
                        "target": item["target"],
                        "interaction_type": item["predicate"],
                        "confidence": item["confidence"],
                    }
                )

        # Sort by confidence and return top k
        interactions.sort(key=lambda x: x["confidence"], reverse=True)
        return interactions[:k]

    def protein_protein_interactions(
        self, protein_entity: str, k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Find protein-protein interactions for a given protein

        Args:
            protein_entity: Protein entity ID
            k: Number of results

        Returns:
            List of protein interactions with confidence scores
        """
        expansion_results = self.kg_NEIGHBORHOOD_EXPANSION(
            [protein_entity], expansion_depth=1, confidence_threshold=500
        )

        # Filter for protein interaction predicates
        interactions = []
        for item in expansion_results:
            if "interacts_with" in item["predicate"] or "binds_to" in item["predicate"]:
                interactions.append(
                    {
                        "protein_a": protein_entity,
                        "protein_b": item["target"],
                        "interaction_type": item["predicate"],
                        "confidence": item["confidence"],
                    }
                )

        interactions.sort(key=lambda x: x["confidence"], reverse=True)
        return interactions[:k]

    def disease_gene_associations(self, disease_entity: str, k: int = 20) -> List[Dict[str, Any]]:
        """
        Find gene associations for a given disease

        Args:
            disease_entity: Disease entity ID
            k: Number of results

        Returns:
            List of gene associations with confidence scores
        """
        expansion_results = self.kg_NEIGHBORHOOD_EXPANSION(
            [disease_entity], expansion_depth=1, confidence_threshold=600
        )

        # Filter for disease-gene associations
        associations = []
        for item in expansion_results:
            if any(
                pred in item["predicate"] for pred in ["associates_with", "causes", "linked_to"]
            ):
                associations.append(
                    {
                        "disease": disease_entity,
                        "gene": item["target"],
                        "association_type": item["predicate"],
                        "confidence": item["confidence"],
                    }
                )

        associations.sort(key=lambda x: x["confidence"], reverse=True)
        return associations[:k]

    def pathway_analysis(self, entity_list: List[str], k: int = 15) -> List[Dict[str, Any]]:
        """
        Analyze pathways for a list of entities (genes, proteins)

        Args:
            entity_list: List of entity IDs
            k: Number of pathway results

        Returns:
            List of pathway enrichment results
        """
        # Expand around all entities to find pathway connections
        expansion_results = self.kg_NEIGHBORHOOD_EXPANSION(
            entity_list, expansion_depth=2, confidence_threshold=500
        )

        # Group by pathway entities
        pathway_connections = {}
        for item in expansion_results:
            if "pathway" in item.get("predicate", "").lower():
                pathway_id = item["target"]
                if pathway_id not in pathway_connections:
                    pathway_connections[pathway_id] = []
                pathway_connections[pathway_id].append(item)

        # Calculate pathway enrichment scores
        pathway_scores = []
        for pathway_id, connections in pathway_connections.items():
            enrichment_score = len(connections) / len(entity_list)  # Simple enrichment
            avg_confidence = sum(conn["confidence"] for conn in connections) / len(connections)

            pathway_scores.append(
                {
                    "pathway_id": pathway_id,
                    "connected_entities": len(connections),
                    "enrichment_score": enrichment_score,
                    "avg_confidence": avg_confidence,
                    "input_coverage": len(set(conn["source"] for conn in connections))
                    / len(entity_list),
                }
            )

        # Sort by enrichment score
        pathway_scores.sort(key=lambda x: x["enrichment_score"], reverse=True)
        return pathway_scores[:k]

    def semantic_drug_discovery(self, disease_query: str, k: int = 20) -> List[Dict[str, Any]]:
        """
        Semantic drug discovery using hybrid search

        Args:
            disease_query: Disease description or entity
            k: Number of drug candidates

        Returns:
            List of potential drug candidates with reasoning
        """
        # First, find related diseases/targets
        disease_results = self.fusion_engine.multi_modal_search(
            query_text=disease_query, entity_types=["disease"], k=10, fusion_method="rrf"
        )

        if not disease_results:
            return []

        # Get disease entities and expand to find targets
        disease_entities = [result["entity_id"] for result in disease_results[:5]]
        target_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
            disease_entities, expansion_depth=2, confidence_threshold=600
        )

        # Find drugs that target these entities
        drug_candidates = []
        target_entities = list(set([item["target"] for item in target_expansion]))

        if target_entities:
            drug_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
                target_entities[:20],  # Limit to prevent explosion
                expansion_depth=1,
                confidence_threshold=500,
            )

            # Group by potential drug entities
            drug_connections = {}
            for item in drug_expansion:
                if any(pred in item["predicate"] for pred in ["treats", "targets", "inhibits"]):
                    drug_id = item["source"]  # Drug is the source targeting the target
                    if drug_id not in drug_connections:
                        drug_connections[drug_id] = []
                    drug_connections[drug_id].append(item)

            # Score drug candidates
            for drug_id, connections in drug_connections.items():
                avg_confidence = sum(conn["confidence"] for conn in connections) / len(connections)
                target_coverage = len(set(conn["target"] for conn in connections))

                drug_candidates.append(
                    {
                        "drug_candidate": drug_id,
                        "target_count": target_coverage,
                        "avg_confidence": avg_confidence,
                        "mechanism_score": avg_confidence * target_coverage,
                        "connected_targets": [conn["target"] for conn in connections],
                    }
                )

        # Sort by mechanism score
        drug_candidates.sort(key=lambda x: x["mechanism_score"], reverse=True)
        return drug_candidates[:k]
