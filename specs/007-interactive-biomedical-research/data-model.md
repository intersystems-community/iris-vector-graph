# Data Model: Interactive Biomedical Research Demo

**Feature**: 007-interactive-biomedical-research
**Date**: 2025-01-08

This document defines the Pydantic models used for protein search, pathway analysis, and network visualization in the biomedical demo.

---

## Core Entities

### Protein
Represents a biological protein with metadata and vector embedding.

**Fields**:
- `protein_id` (str, required): Unique identifier (e.g., "ENSP00000269305")
- `name` (str, required): Protein name (e.g., "TP53 (Tumor Protein P53)")
- `organism` (str, required): Source organism (e.g., "Homo sapiens")
- `sequence` (str, optional): Amino acid sequence
- `function_description` (str, optional): Functional annotation
- `vector_embedding` (List[float], optional): 768-dimensional embedding

**Validation**:
- protein_id must be non-empty
- vector_embedding (if present) must be 768-dimensional
- name must be non-empty

**Usage**: Response payload in search results and network nodes

---

### ProteinSearchQuery
Request model for protein similarity search.

**Fields**:
- `query_text` (str, required): Search term (protein name, ID, or keyword)
- `query_type` (enum, default="name"): One of ["name", "sequence", "function"]
- `top_k` (int, default=10): Number of results to return
- `filters` (dict, optional): Additional filters (organism, confidence)

**Validation**:
- query_text must be non-empty
- top_k must be 1-50
- query_type must be in enum

**Usage**: Request body for POST /api/bio/search

---

### SimilaritySearchResult
Response model for protein search with performance metrics.

**Fields**:
- `proteins` (List[Protein], required): Matching proteins
- `similarity_scores` (List[float], required): Scores for each protein (0.0-1.0)
- `search_method` (str, required): Method used ("vector"|"text"|"hybrid")
- `performance_metrics` (QueryPerformanceMetrics, required): Execution metrics

**Validation**:
- len(proteins) == len(similarity_scores)
- All scores must be 0.0-1.0
- proteins must not be empty

**Usage**: Response for POST /api/bio/search

---

### Interaction
Represents protein-protein interaction with confidence score.

**Fields**:
- `source_protein_id` (str, required): Source protein ID
- `target_protein_id` (str, required): Target protein ID
- `interaction_type` (str, required): Type ("binding"|"phosphorylation"|"inhibition"|"activation")
- `confidence_score` (float, required): Confidence 0.0-1.0
- `evidence` (str, optional): Supporting evidence source

**Validation**:
- protein_ids must be non-empty
- confidence_score must be 0.0-1.0
- interaction_type must be non-empty

**Usage**: Edges in network graph, pathway results

---

### InteractionNetwork
Response model for protein interaction network.

**Fields**:
- `nodes` (List[Protein], required): Proteins in network
- `edges` (List[Interaction], required): Interactions between proteins
- `layout_hints` (dict, optional): D3.js layout parameters

**Validation**:
- All edge protein_ids must exist in nodes
- nodes must not be empty

**Usage**: Response for GET /api/bio/network/{protein_id}

---

### PathwayQuery
Request model for pathway analysis between two proteins.

**Fields**:
- `source_protein_id` (str, required): Starting protein
- `target_protein_id` (str, required): Ending protein
- `max_hops` (int, default=3): Maximum path length

**Validation**:
- protein_ids must be non-empty
- max_hops must be 1-5

**Usage**: Request body for POST /api/bio/pathway

---

### PathwayResult
Response model for pathway search result.

**Fields**:
- `path` (List[str], required): Ordered list of protein IDs in path
- `intermediate_proteins` (List[Protein], required): Protein details for path
- `path_interactions` (List[Interaction], required): Edges along path
- `confidence` (float, required): Overall pathway confidence 0.0-1.0

**Validation**:
- path length >= 2
- len(intermediate_proteins) == len(path)
- confidence must be 0.0-1.0

**Usage**: Response for POST /api/bio/pathway

---

### QueryPerformanceMetrics
Performance tracking embedded in all API responses.

**Fields**:
- `query_type` (str, required): Type ("protein_search"|"pathway"|"network")
- `execution_time_ms` (int, required): Execution time in milliseconds
- `backend_used` (enum, required): One of ["biomedical_api", "cached_demo"]
- `result_count` (int, required): Number of results returned

**Validation**:
- execution_time_ms >= 0
- backend_used must be in enum
- result_count >= 0

**Usage**: Embedded in all API response models

---

## Pydantic Implementation Reference

```python
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict
from enum import Enum

class QueryType(str, Enum):
    NAME = "name"
    SEQUENCE = "sequence"
    FUNCTION = "function"

class BackendStatus(str, Enum):
    BIOMEDICAL_API = "biomedical_api"
    CACHED_DEMO = "cached_demo"

class Protein(BaseModel):
    protein_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    organism: str = Field(..., min_length=1)
    sequence: Optional[str] = None
    function_description: Optional[str] = None
    vector_embedding: Optional[List[float]] = None

    @validator('vector_embedding')
    def validate_embedding_dim(cls, v):
        if v is not None and len(v) != 768:
            raise ValueError('vector_embedding must be 768-dimensional')
        return v

class ProteinSearchQuery(BaseModel):
    query_text: str = Field(..., min_length=1)
    query_type: QueryType = QueryType.NAME
    top_k: int = Field(10, ge=1, le=50)
    filters: Optional[Dict[str, str]] = None

# ... (additional models following same pattern)
```

---

## Model Relationships

```
ProteinSearchQuery (POST request)
    ↓
SimilaritySearchResult (response)
    ├── List[Protein]
    ├── List[float] (similarity_scores)
    └── QueryPerformanceMetrics

PathwayQuery (POST request)
    ↓
PathwayResult (response)
    ├── List[str] (path IDs)
    ├── List[Protein] (intermediate_proteins)
    ├── List[Interaction] (path_interactions)
    └── confidence score

GET /api/bio/network/{protein_id}
    ↓
InteractionNetwork (response)
    ├── List[Protein] (nodes)
    └── List[Interaction] (edges)
```

---

## Validation Rules Summary

| Model | Required Fields | Constraints |
|-------|----------------|-------------|
| Protein | protein_id, name, organism | ID non-empty, embedding 768-dim |
| ProteinSearchQuery | query_text | top_k 1-50, query_type in enum |
| SimilaritySearchResult | proteins, scores, method | len match, scores 0.0-1.0 |
| Interaction | source, target, type, confidence | IDs non-empty, confidence 0.0-1.0 |
| InteractionNetwork | nodes, edges | Edge IDs exist in nodes |
| PathwayQuery | source, target | IDs non-empty, max_hops 1-5 |
| PathwayResult | path, proteins, interactions | Path length >=2, confidence 0.0-1.0 |
| QueryPerformanceMetrics | all fields | time_ms >=0, backend in enum |

---

**Implementation Location**: `src/iris_demo_server/models/biomedical.py` (new file)
