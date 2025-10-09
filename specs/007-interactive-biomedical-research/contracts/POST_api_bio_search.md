# Contract: POST /api/bio/search

**Endpoint**: `/api/bio/search`
**Method**: POST
**Purpose**: Protein similarity search using vector embeddings, text matching, or hybrid

**Requirements**: FR-006 (search by name/ID/function), FR-007 (ranked similarity scores), FR-008 (top K results)

---

## Request Schema

```json
{
  "query_text": "TP53",
  "query_type": "name",
  "top_k": 10,
  "filters": {
    "organism": "Homo sapiens"
  }
}
```

**Fields**:
- `query_text` (string, required): Protein name, ID, or functional keyword
- `query_type` (enum, optional): One of ["name", "sequence", "function"], default="name"
- `top_k` (integer, optional): Number of results (1-50), default=10
- `filters` (object, optional): Additional filters (organism, confidence threshold)

---

## Response Schema (200 OK)

```json
{
  "result": {
    "proteins": [
      {
        "protein_id": "ENSP00000269305",
        "name": "TP53 (Tumor Protein P53)",
        "organism": "Homo sapiens",
        "function_description": "Tumor suppressor regulating cell cycle",
        "sequence": "MEEPQ...",
        "vector_embedding": null
      }
    ],
    "similarity_scores": [1.0, 0.89, 0.78],
    "search_method": "hybrid"
  },
  "metrics": {
    "query_type": "protein_search",
    "execution_time_ms": 850,
    "backend_used": "biomedical_api",
    "result_count": 10
  }
}
```

---

## Error Responses

**400 Bad Request** (validation failure):
```json
{
  "detail": "query_text must be non-empty"
}
```

**500 Internal Server Error** (backend failure):
```json
{
  "detail": "Biomedical backend unavailable - using demo mode"
}
```

---

## Contract Tests

**File**: `tests/demo/contract/test_bio_search.py`

### test_protein_search_request_schema()
Validates request model accepts valid protein search queries.

### test_protein_search_response_schema()
Validates response contains proteins, scores, and metrics.

### test_protein_search_validation_errors()
Tests 400 error for missing query_text.

### test_protein_search_performance()
Asserts response time <2 seconds (FR-002).

### test_protein_search_similarity_scores()
Validates all similarity_scores are 0.0-1.0.

---

## Implementation Notes

- Uses `ProteinSearchQuery` Pydantic model for validation
- Calls `BiomedicalAPIClient.search_proteins()` with circuit breaker
- Falls back to demo mode if backend unavailable
- Returns FastHTML components (HTMX swap target)
