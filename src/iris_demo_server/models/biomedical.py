"""Biomedical research models for Life Sciences demo"""
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class QueryType(str, Enum):
    """Protein search query types"""
    NAME = "name"
    SEQUENCE = "sequence"
    FUNCTION = "function"


class SimilarityLevel(str, Enum):
    """Human-readable similarity levels"""
    VERY_HIGH = "very_high"  # >0.9
    HIGH = "high"             # 0.7-0.9
    MODERATE = "moderate"     # 0.5-0.7
    LOW = "low"               # <0.5


class Protein(BaseModel):
    """Biological protein with metadata and optional vector embedding"""
    protein_id: str = Field(..., min_length=1, description="Unique protein identifier")
    name: str = Field(..., min_length=1, description="Protein name")
    organism: str = Field(..., min_length=1, description="Source organism")
    sequence: Optional[str] = Field(None, description="Amino acid sequence")
    function_description: Optional[str] = Field(None, description="Functional annotation")
    vector_embedding: Optional[List[float]] = Field(None, description="768-dim embedding")

    @field_validator('vector_embedding')
    @classmethod
    def validate_embedding_dimension(cls, v):
        if v is not None and len(v) != 768:
            raise ValueError('vector_embedding must be 768-dimensional')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "protein_id": "ENSP00000269305",
                "name": "TP53 (Tumor Protein P53)",
                "organism": "Homo sapiens",
                "function_description": "Tumor suppressor regulating cell cycle"
            }
        }


class ProteinSearchQuery(BaseModel):
    """User-submitted protein search request (FR-006)"""
    query_text: str = Field(..., min_length=1, description="Search term")
    query_type: QueryType = Field(default=QueryType.NAME, description="Search method")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
    filters: Optional[Dict[str, str]] = Field(None, description="Additional filters")

    class Config:
        json_schema_extra = {
            "example": {
                "query_text": "TP53",
                "query_type": "name",
                "top_k": 10,
                "filters": {"organism": "Homo sapiens"}
            }
        }


class SimilaritySearchResult(BaseModel):
    """Protein search results with similarity scores (FR-007)"""
    proteins: List[Protein] = Field(..., description="Matching proteins")
    similarity_scores: List[float] = Field(..., description="Scores 0.0-1.0")
    search_method: str = Field(..., description="vector|text|hybrid")

    @field_validator('similarity_scores')
    @classmethod
    def validate_scores(cls, v, info):
        # Validate all scores are 0.0-1.0
        for score in v:
            if not 0.0 <= score <= 1.0:
                raise ValueError(f'Similarity score {score} must be 0.0-1.0')

        # Validate length matches proteins (if proteins field exists)
        proteins = info.data.get('proteins', [])
        if proteins and len(v) != len(proteins):
            raise ValueError('similarity_scores length must match proteins length')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "proteins": [{"protein_id": "ENSP00000269305", "name": "TP53", "organism": "Homo sapiens"}],
                "similarity_scores": [1.0, 0.89, 0.78],
                "search_method": "hybrid"
            }
        }


class Interaction(BaseModel):
    """Protein-protein interaction with confidence score"""
    source_protein_id: str = Field(..., min_length=1, description="Source protein")
    target_protein_id: str = Field(..., min_length=1, description="Target protein")
    interaction_type: str = Field(..., min_length=1, description="binding|phosphorylation|inhibition|activation")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence 0.0-1.0")
    evidence: Optional[str] = Field(None, description="Supporting evidence source")

    class Config:
        json_schema_extra = {
            "example": {
                "source_protein_id": "ENSP00000269305",
                "target_protein_id": "ENSP00000258149",
                "interaction_type": "inhibition",
                "confidence_score": 0.95,
                "evidence": "STRING DB experimental"
            }
        }


class InteractionNetwork(BaseModel):
    """Protein interaction network for visualization (FR-012)"""
    nodes: List[Protein] = Field(..., description="Proteins in network")
    edges: List[Interaction] = Field(..., description="Interactions between proteins")
    layout_hints: Optional[Dict[str, Any]] = Field(None, description="D3.js layout parameters")

    @field_validator('edges')
    @classmethod
    def validate_edge_protein_ids(cls, v, info):
        """Ensure all edge protein IDs exist in nodes"""
        nodes = info.data.get('nodes', [])
        if not nodes:
            return v

        node_ids = {node.protein_id for node in nodes}
        for edge in v:
            if edge.source_protein_id not in node_ids:
                raise ValueError(f'Edge source {edge.source_protein_id} not found in nodes')
            if edge.target_protein_id not in node_ids:
                raise ValueError(f'Edge target {edge.target_protein_id} not found in nodes')
        return v

    @field_validator('nodes')
    @classmethod
    def validate_non_empty_nodes(cls, v):
        if not v:
            raise ValueError('nodes must not be empty')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "nodes": [
                    {"protein_id": "ENSP00000269305", "name": "TP53", "organism": "Homo sapiens"},
                    {"protein_id": "ENSP00000258149", "name": "MDM2", "organism": "Homo sapiens"}
                ],
                "edges": [
                    {
                        "source_protein_id": "ENSP00000269305",
                        "target_protein_id": "ENSP00000258149",
                        "interaction_type": "inhibition",
                        "confidence_score": 0.95
                    }
                ],
                "layout_hints": {"force_strength": -200, "link_distance": 80}
            }
        }


class PathwayQuery(BaseModel):
    """Request for shortest pathway between proteins (FR-019)"""
    source_protein_id: str = Field(..., min_length=1, description="Starting protein")
    target_protein_id: str = Field(..., min_length=1, description="Ending protein")
    max_hops: int = Field(default=3, ge=1, le=5, description="Maximum path length")

    class Config:
        json_schema_extra = {
            "example": {
                "source_protein_id": "ENSP00000269305",
                "target_protein_id": "ENSP00000344548",
                "max_hops": 3
            }
        }


class PathwayResult(BaseModel):
    """Shortest pathway search result (FR-020)"""
    path: List[str] = Field(..., min_length=2, description="Ordered protein IDs")
    intermediate_proteins: List[Protein] = Field(..., description="Protein details")
    path_interactions: List[Interaction] = Field(..., description="Edges along path")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Overall pathway confidence")

    @field_validator('path')
    @classmethod
    def validate_path_length(cls, v):
        if len(v) < 2:
            raise ValueError('path must contain at least 2 proteins')
        return v

    @field_validator('intermediate_proteins')
    @classmethod
    def validate_proteins_match_path(cls, v, info):
        path = info.data.get('path', [])
        if path and len(v) != len(path):
            raise ValueError('intermediate_proteins length must match path length')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "path": ["ENSP00000269305", "ENSP00000258149", "ENSP00000344548"],
                "intermediate_proteins": [
                    {"protein_id": "ENSP00000269305", "name": "TP53", "organism": "Homo sapiens"},
                    {"protein_id": "ENSP00000258149", "name": "MDM2", "organism": "Homo sapiens"},
                    {"protein_id": "ENSP00000344548", "name": "CDKN1A", "organism": "Homo sapiens"}
                ],
                "path_interactions": [
                    {"source_protein_id": "ENSP00000269305", "target_protein_id": "ENSP00000258149",
                     "interaction_type": "inhibition", "confidence_score": 0.95},
                    {"source_protein_id": "ENSP00000258149", "target_protein_id": "ENSP00000344548",
                     "interaction_type": "activation", "confidence_score": 0.88}
                ],
                "confidence": 0.91
            }
        }
