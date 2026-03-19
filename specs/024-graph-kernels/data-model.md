# Data Model: Graph Analytics Kernels

**Phase 1 Output** | **Date**: 2026-03-19

## Return Types

### Global PageRank
`List[Tuple[str, float]]` — sorted descending by score

| Field | Type | Description |
|-------|------|-------------|
| node_id | str | Node identifier |
| score | float | PageRank importance score (all scores sum to ~1.0) |

JSON wire format: `[{"id":"node","score":0.12}, ...]`

### WCC (Weakly Connected Components)
`Dict[str, str]` — node ID → component label

| Field | Type | Description |
|-------|------|-------------|
| node_id (key) | str | Node identifier |
| component (value) | str | Component label = minimum node ID in the component |

JSON wire format: `{"node1":"comp_A","node2":"comp_A","node3":"comp_B"}`

### CDLP (Community Detection)
`Dict[str, str]` — node ID → community label

| Field | Type | Description |
|-------|------|-------------|
| node_id (key) | str | Node identifier |
| community (value) | str | Community label = dominant label after propagation |

JSON wire format: `{"node1":"comm_X","node2":"comm_X","node3":"comm_Y"}`

## Invariants

- PageRank: all scores > 0, sum ≈ 1.0, every node in ^KG("deg") has a score
- WCC: every node has exactly one component label; nodes in same component share the label
- CDLP: every node has exactly one community label; the algorithm is deterministic (smallest label wins ties)
