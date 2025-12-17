# Data Model: Bidirectional Personalized PageRank

**Feature**: 005-bidirectional-ppr
**Date**: 2025-12-15

## Entities

This feature operates on existing graph entities. No new tables are required.

### Existing Entities (Read-Only)

#### nodes

Central node registry with referential integrity.

| Field | Type | Description |
|-------|------|-------------|
| node_id | VARCHAR(256) | Primary key, unique node identifier |
| created_at | TIMESTAMP | Node creation timestamp |

#### rdf_edges

Directed edges between nodes.

| Field | Type | Description |
|-------|------|-------------|
| edge_id | BIGINT | Primary key |
| s | VARCHAR(256) | Source node (FK → nodes.node_id) |
| p | VARCHAR(128) | Predicate/relationship type |
| o_id | VARCHAR(256) | Object/target node (FK → nodes.node_id) |
| qualifiers | VARCHAR(MAX) | JSON metadata including confidence |

### Virtual Entities (Computed)

#### Reverse Edge

Not stored, computed at query time when `bidirectional=true`.

| Concept | Description |
|---------|-------------|
| Source | Original edge's object (o_id) |
| Target | Original edge's subject (s) |
| Weight | Original edge weight × reverse_edge_weight |

#### PageRank Score

Returned as query result, not persisted.

| Field | Type | Description |
|-------|------|-------------|
| entity_id | VARCHAR(256) | Node identifier |
| score | DOUBLE | PageRank score (0.0 - 1.0 range, sums to 1.0) |

## Data Flow

### Input

```json
{
  "seed_entities": ["ENTITY:Ewan_MacColl"],
  "bidirectional": true,
  "reverse_edge_weight": 1.0
}
```

### Graph Traversal

```
Forward edges:  s → o_id (weight: 1.0)
Reverse edges:  o_id → s (weight: reverse_edge_weight)
```

### Output

```json
{
  "ENTITY:Ewan_MacColl": 0.15,
  "ENTITY:Peggy_Seeger": 0.12,
  "ENTITY:Folk_Music": 0.08
}
```

## Adjacency Structure

### Forward Adjacency (Existing)

Built from `rdf_edges` table:

```python
forward_adj = defaultdict(list)
for edge in rdf_edges:
    forward_adj[edge.s].append(edge.o_id)
```

### Reverse Adjacency (New)

Built when `bidirectional=true`:

```python
reverse_adj = defaultdict(list)
for edge in rdf_edges:
    reverse_adj[edge.o_id].append((edge.s, reverse_edge_weight))
```

### Combined In-Edges

For PageRank computation, each node receives contributions from:

```python
in_edges[node] = forward_in_edges[node] + reverse_in_edges[node]
```

## Validation Rules

| Rule | Validation |
|------|------------|
| reverse_edge_weight >= 0 | Reject negative values with error |
| reverse_edge_weight <= 10.0 | Warn but allow (unusual but valid) |
| seed_entities not empty | Require at least one seed |
| seed_entities exist in nodes | Log warning for missing, continue with valid |

## State Transitions

PageRank is a stateless computation. No state transitions to model.

## Scale Assumptions

| Metric | Expected Range |
|--------|----------------|
| Graph size | 1K - 100K nodes |
| Edge density | 5-20 edges per node average |
| Seed entities | 1-100 per query |
| Return top K | 10-1000 results |
