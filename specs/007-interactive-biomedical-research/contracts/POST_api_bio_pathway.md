# Contract: POST /api/bio/pathway

**Endpoint**: `/api/bio/pathway`
**Method**: POST
**Purpose**: Find shortest pathway between two proteins via graph traversal

**Requirements**: FR-019 (compute shortest paths), FR-020 (multi-hop pathways), FR-021 (pathway confidence)

---

## Request Schema

```json
{
  "source_protein_id": "ENSP00000269305",
  "target_protein_id": "ENSP00000344548",
  "max_hops": 3
}
```

**Fields**:
- `source_protein_id` (string, required): Starting protein ID
- `target_protein_id` (string, required): Ending protein ID
- `max_hops` (integer, optional): Maximum path length (1-5), default=3

---

## Response Schema (200 OK)

```json
{
  "result": {
    "path": ["ENSP00000269305", "ENSP00000258149", "ENSP00000344548"],
    "intermediate_proteins": [
      {"protein_id": "ENSP00000269305", "name": "TP53", ...},
      {"protein_id": "ENSP00000258149", "name": "MDM2", ...},
      {"protein_id": "ENSP00000344548", "name": "CDKN1A", ...}
    ],
    "path_interactions": [
      {"source": "ENSP00000269305", "target": "ENSP00000258149", "type": "inhibition", "confidence": 0.95},
      {"source": "ENSP00000258149", "target": "ENSP00000344548", "type": "activation", "confidence": 0.88}
    ],
    "confidence": 0.91
  },
  "metrics": {
    "query_type": "pathway_search",
    "execution_time_ms": 450,
    "backend_used": "biomedical_api",
    "result_count": 1
  }
}
```

---

## Error Responses

**404 Not Found** (no path exists):
```json
{
  "detail": "No pathway found between proteins within 3 hops"
}
```

**400 Bad Request** (invalid protein IDs):
```json
{
  "detail": "source_protein_id must be non-empty"
}
```

---

## Contract Tests

**File**: `tests/demo/contract/test_bio_pathway.py`

### test_pathway_request_schema()
Validates pathway query with source/target/max_hops.

### test_pathway_response_schema()
Validates path structure with proteins and interactions.

### test_pathway_no_path_found()
Tests 404 error when proteins disconnected.

### test_pathway_confidence_scores()
Validates confidence 0.0-1.0.

---

## Implementation Notes

- Uses `PathwayQuery` Pydantic model for validation
- Calls biomedical backend's graph traversal algorithm
- Returns `PathwayResult` with highlighted path for D3.js visualization
