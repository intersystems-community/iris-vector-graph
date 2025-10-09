# Contract: GET /api/bio/network/{protein_id}

**Endpoint**: `/api/bio/network/{protein_id}`
**Method**: GET
**Purpose**: Retrieve interaction network for a protein (nodes + edges for D3.js)

**Requirements**: FR-012 (render networks), FR-013 (expand/collapse nodes), FR-018 (handle 50-500 nodes)

---

## Request Parameters

**Path**:
- `protein_id` (string, required): Protein to center network on

**Query**:
- `expand_depth` (integer, optional): Neighborhood depth (1-3), default=1

**Example**: `/api/bio/network/ENSP00000269305?expand_depth=2`

---

## Response Schema (200 OK)

```json
{
  "result": {
    "nodes": [
      {"protein_id": "ENSP00000269305", "name": "TP53", ...},
      {"protein_id": "ENSP00000258149", "name": "MDM2", ...}
    ],
    "edges": [
      {
        "source_protein_id": "ENSP00000269305",
        "target_protein_id": "ENSP00000258149",
        "interaction_type": "inhibition",
        "confidence_score": 0.95,
        "evidence": "STRING DB experimental"
      }
    ],
    "layout_hints": {
      "force_strength": -200,
      "link_distance": 80
    }
  },
  "metrics": {
    "query_type": "network_expansion",
    "execution_time_ms": 120,
    "backend_used": "biomedical_api",
    "result_count": 15
  }
}
```

---

## Error Responses

**404 Not Found** (protein doesn't exist):
```json
{
  "detail": "Protein ENSP99999999 not found"
}
```

---

## Contract Tests

**File**: `tests/demo/contract/test_bio_network.py`

### test_network_response_schema()
Validates nodes and edges structure.

### test_network_node_expansion()
Tests expand_depth parameter changes node count.

### test_network_size_limits()
Asserts max 500 nodes returned (FR-018).

---

## Implementation Notes

- Returns data formatted for D3.js force-directed graph
- Lazy loading: Initially returns expand_depth=1, user clicks expand for more
- Hard limit 500 nodes enforced to prevent browser performance issues
