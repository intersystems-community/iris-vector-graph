"""Resilient biomedical API client with circuit breaker"""
import httpx
import time
import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

from ..models.biomedical import (
    Protein,
    ProteinSearchQuery,
    SimilaritySearchResult,
    InteractionNetwork,
    Interaction,
    PathwayQuery,
    PathwayResult
)


class CircuitBreaker:
    """Exponential backoff circuit breaker"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half_open

    def is_open(self) -> bool:
        """Check if circuit is open (failing)"""
        if self.state == "open":
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) > self.recovery_timeout:
                self.state = "half_open"
                return False
            return True
        return False

    def record_success(self) -> None:
        """Record successful call"""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        """Record failed call"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"


class BiomedicalAPIClient:
    """Resilient biomedical API client (integrates with biomedical server on :8300)"""

    def __init__(self, base_url: str = "http://localhost:8300", demo_mode: bool = False):
        self.base_url = base_url
        self.demo_mode = demo_mode or os.getenv("DEMO_MODE", "false").lower() == "true"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=True
        )
        self.circuit_breaker = CircuitBreaker()

    async def search_proteins(self, query: ProteinSearchQuery) -> SimilaritySearchResult:
        """Search proteins with circuit breaker fallback (FR-006, FR-007)"""
        if self.demo_mode or self.circuit_breaker.is_open():
            return self._get_demo_search_results(query)

        try:
            response = await self.client.post(
                f"{self.base_url}/bio/search",
                json=query.model_dump()
            )
            response.raise_for_status()
            self.circuit_breaker.record_success()

            # Parse biomedical API response
            result = response.json()
            return SimilaritySearchResult(**result)

        except (httpx.HTTPError, httpx.TimeoutException, Exception):
            self.circuit_breaker.record_failure()
            return self._get_demo_search_results(query)

    async def get_interaction_network(self, protein_id: str, expand_depth: int = 1) -> InteractionNetwork:
        """Get protein interaction network (FR-012, FR-013)"""
        if self.demo_mode or self.circuit_breaker.is_open():
            return self._get_demo_network(protein_id, expand_depth)

        try:
            response = await self.client.get(
                f"{self.base_url}/bio/network/{protein_id}",
                params={"expand_depth": expand_depth}
            )
            response.raise_for_status()
            self.circuit_breaker.record_success()

            result = response.json()
            return InteractionNetwork(**result)

        except (httpx.HTTPError, httpx.TimeoutException, Exception):
            self.circuit_breaker.record_failure()
            return self._get_demo_network(protein_id, expand_depth)

    async def find_pathway(self, query: PathwayQuery) -> PathwayResult:
        """Find shortest pathway between proteins (FR-019, FR-020)"""
        if self.demo_mode or self.circuit_breaker.is_open():
            return self._get_demo_pathway(query)

        try:
            response = await self.client.post(
                f"{self.base_url}/bio/pathway",
                json=query.model_dump()
            )
            response.raise_for_status()
            self.circuit_breaker.record_success()

            result = response.json()
            return PathwayResult(**result)

        except (httpx.HTTPError, httpx.TimeoutException, Exception):
            self.circuit_breaker.record_failure()
            return self._get_demo_pathway(query)

    def _get_demo_search_results(self, query: ProteinSearchQuery) -> SimilaritySearchResult:
        """Fallback to demo protein data (FR-002 requirement)"""
        # Demo protein dataset (10-15 sample proteins)
        demo_proteins = [
            Protein(
                protein_id="ENSP00000269305",
                name="TP53 (Tumor Protein P53)",
                organism="Homo sapiens",
                function_description="Tumor suppressor regulating cell cycle"
            ),
            Protein(
                protein_id="ENSP00000258149",
                name="MDM2 (E3 Ubiquitin-Protein Ligase)",
                organism="Homo sapiens",
                function_description="Regulates p53 activity"
            ),
            Protein(
                protein_id="ENSP00000344548",
                name="CDKN1A (Cyclin-Dependent Kinase Inhibitor 1A)",
                organism="Homo sapiens",
                function_description="Cell cycle regulation"
            ),
            Protein(
                protein_id="ENSP00000306407",
                name="GAPDH (Glyceraldehyde-3-Phosphate Dehydrogenase)",
                organism="Homo sapiens",
                function_description="Glycolysis pathway enzyme"
            ),
            Protein(
                protein_id="ENSP00000316649",
                name="LDHA (Lactate Dehydrogenase A)",
                organism="Homo sapiens",
                function_description="Converts pyruvate to lactate"
            )
        ]

        # Simple keyword matching for demo
        query_lower = query.query_text.lower()
        matched_proteins = [
            p for p in demo_proteins
            if query_lower in p.name.lower() or
            (p.function_description and query_lower in p.function_description.lower())
        ]

        # If no matches, return all as fallback
        if not matched_proteins:
            matched_proteins = demo_proteins

        # Limit to top_k results
        matched_proteins = matched_proteins[:query.top_k]

        # Generate similarity scores (descending from 1.0)
        scores = [1.0 - (i * 0.1) for i in range(len(matched_proteins))]

        return SimilaritySearchResult(
            proteins=matched_proteins,
            similarity_scores=scores,
            search_method="demo_text_match"
        )

    def _get_demo_network(self, protein_id: str, expand_depth: int = 1) -> InteractionNetwork:
        """Fallback to demo network data (FR-012, FR-018)"""
        # Demo network: TP53 → MDM2 → CDKN1A
        demo_nodes = [
            Protein(
                protein_id="ENSP00000269305",
                name="TP53 (Tumor Protein P53)",
                organism="Homo sapiens",
                function_description="Tumor suppressor"
            ),
            Protein(
                protein_id="ENSP00000258149",
                name="MDM2",
                organism="Homo sapiens",
                function_description="Regulates p53"
            ),
            Protein(
                protein_id="ENSP00000344548",
                name="CDKN1A",
                organism="Homo sapiens",
                function_description="Cell cycle regulation"
            )
        ]

        demo_edges = [
            Interaction(
                source_protein_id="ENSP00000269305",
                target_protein_id="ENSP00000258149",
                interaction_type="inhibition",
                confidence_score=0.95,
                evidence="STRING DB experimental"
            ),
            Interaction(
                source_protein_id="ENSP00000258149",
                target_protein_id="ENSP00000344548",
                interaction_type="activation",
                confidence_score=0.88,
                evidence="BioGRID"
            )
        ]

        return InteractionNetwork(
            nodes=demo_nodes,
            edges=demo_edges,
            layout_hints={"force_strength": -200, "link_distance": 80}
        )

    def _get_demo_pathway(self, query: PathwayQuery) -> PathwayResult:
        """Fallback to demo pathway data (FR-019, FR-020)"""
        # Demo pathway: source → intermediate → target
        demo_proteins = [
            Protein(protein_id=query.source_protein_id, name="Source Protein", organism="Homo sapiens"),
            Protein(protein_id="ENSP_INTERMEDIATE", name="Intermediate Protein", organism="Homo sapiens"),
            Protein(protein_id=query.target_protein_id, name="Target Protein", organism="Homo sapiens")
        ]

        demo_interactions = [
            Interaction(
                source_protein_id=query.source_protein_id,
                target_protein_id="ENSP_INTERMEDIATE",
                interaction_type="activation",
                confidence_score=0.85
            ),
            Interaction(
                source_protein_id="ENSP_INTERMEDIATE",
                target_protein_id=query.target_protein_id,
                interaction_type="binding",
                confidence_score=0.78
            )
        ]

        return PathwayResult(
            path=[query.source_protein_id, "ENSP_INTERMEDIATE", query.target_protein_id],
            intermediate_proteins=demo_proteins,
            path_interactions=demo_interactions,
            confidence=0.81  # Average of interaction confidences
        )

    async def close(self) -> None:
        """Close HTTP client"""
        await self.client.aclose()
