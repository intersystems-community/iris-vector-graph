# Data Model: Explicit Node Identity

## Graph Primitives Context

This data model implements the **NodePK (Node Primary Key)** primitive identified as a critical baseline gap in [GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md](../../docs/GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md).

**Baseline Indexing Palette Position**:
- **Layer**: Identity (Level 0)
- **Dependencies**: None (foundational primitive)
- **Enables**: Topology indexes, property indexes, query statistics
- **Status**: ⚠️ → ✅ (Implicit → Explicit implementation)

**Assessment References**:
- Line 43: Current gap identified - "No explicit nodes table with enforced uniqueness constraints"
- Line 287: Immediate priority recommendation - "Add NodePK Table"
- Line 303: Baseline coverage impact - Moves from 90% to 95% baseline alignment

---

## Core Entity

### Node
**Purpose**: Central registry of all node identifiers in the graph

**Fields**:
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| node_id | VARCHAR(256) | PRIMARY KEY, NOT NULL | Unique node identifier |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Node creation timestamp (audit trail) |

**Indexes**:
- Primary key B-tree on `node_id` (automatic with PK constraint)

**Relationships**:
- Referenced by `rdf_labels.s` (1:N) - A node can have multiple labels
- Referenced by `rdf_props.s` (1:N) - A node can have multiple properties
- Referenced by `rdf_edges.s` (1:N) - A node can be the source of multiple edges
- Referenced by `rdf_edges.o_id` (1:N) - A node can be the destination of multiple edges
- Referenced by `kg_NodeEmbeddings.id` (1:1) - A node can have zero or one embedding

**Validation Rules**:
- `node_id` must be unique across entire graph
- `node_id` must not be NULL or empty string
- Node can exist without any labels, properties, edges, or embeddings (bare node)

**State Transitions**: None (nodes are immutable identity records)

---

## Modified Entities

### Edge (rdf_edges)
**Changes**:
- **New Constraint**: `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`
- **New Constraint**: `FOREIGN KEY (o_id) REFERENCES nodes(node_id) ON DELETE RESTRICT`

**Impact**:
- Edge insertion requires both source and destination nodes to exist
- Node deletion blocked if any edges reference it (must delete edges first)
- Constraint violations raise SQLEXCEPTION with FK constraint name

**Validation Rules**:
- Source node (`s`) MUST exist in `nodes` table before edge creation
- Destination node (`o_id`) MUST exist in `nodes` table before edge creation
- Cannot delete node while edges reference it (referential integrity enforcement)

---

### Label (rdf_labels)
**Changes**:
- **New Constraint**: `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`

**Impact**:
- Label assignment requires node existence
- Cannot assign label to non-existent node
- Node deletion blocked if labels reference it

**Validation Rules**:
- Subject node (`s`) MUST exist in `nodes` table before label assignment

---

### Property (rdf_props)
**Changes**:
- **New Constraint**: `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`

**Impact**:
- Property assignment requires node existence
- Cannot assign property to non-existent node
- Node deletion blocked if properties reference it

**Validation Rules**:
- Subject node (`s`) MUST exist in `nodes` table before property assignment

---

### Embedding (kg_NodeEmbeddings)
**Changes**:
- **New Constraint**: `FOREIGN KEY (id) REFERENCES nodes(node_id) ON DELETE RESTRICT`

**Impact**:
- Embedding creation requires node existence
- Cannot create embedding for non-existent node
- Node deletion blocked if embedding references it

**Validation Rules**:
- Node ID (`id`) MUST exist in `nodes` table before embedding creation

---

## Referential Integrity Diagram

```
                    ┌─────────────────┐
                    │     nodes       │
                    │                 │
                    │ node_id (PK)    │
                    │ created_at      │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
    ┌──────────┐      ┌──────────┐      ┌──────────┐
    │rdf_labels│      │rdf_props │      │rdf_edges │
    │          │      │          │      │          │
    │s (FK) ───┤      │s (FK) ───┤      │s (FK)────┤
    │label     │      │key       │      │p         │
    └──────────┘      │val       │      │o_id (FK)─┤
                      └──────────┘      │qualifiers│
                                        └──────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │kg_NodeEmbeddings │
                    │                  │
                    │id (FK) ──────────┤
                    │emb               │
                    └──────────────────┘

Legend:
  PK = Primary Key
  FK = Foreign Key (references nodes.node_id)
  ── = Relationship (FK constraint)
```

---

## Migration Considerations

### Data Discovery
- **Process**: UNION query across all tables to discover unique node IDs
- **Deduplication**: `DISTINCT` or `ON DUPLICATE KEY IGNORE` handles duplicates automatically
- **Sources**:
  - `rdf_labels.s`
  - `rdf_props.s`
  - `rdf_edges.s` and `rdf_edges.o_id`
  - `kg_NodeEmbeddings.id`

### Orphan Detection
- **Definition**: Edge/label/property/embedding referencing non-existent node
- **Detection**: LEFT JOIN query to find NULL references
- **Resolution**: Either create missing nodes or reject orphaned records (migration decision)

### Duplicate Handling
- **Scenario**: Same `node_id` appears in multiple tables
- **Resolution**: Single entry in `nodes` table (UNIQUE constraint enforces)
- **No Action Required**: INSERT with `ON DUPLICATE KEY IGNORE` handles automatically

---

## Performance Characteristics

### Foreign Key Overhead
- **Lookup Cost**: O(log n) using B-tree PK index on `nodes.node_id`
- **Expected Impact**: <10% overhead on INSERT operations
- **Mitigation**: Batch inserts with deferred constraint checking (if IRIS supports)

### Index Usage
- **Primary Benefit**: FK validation uses existing PK index (no additional index required)
- **Query Optimization**: Enables optimizer to use FK statistics for join planning

### Scale Considerations
- **Current Scale**: 27K nodes → <0.1ms FK lookup overhead
- **Target Scale**: 1M nodes → ~0.5ms FK lookup overhead (still within <1ms requirement)
- **Bottleneck**: None expected; PK index scales logarithmically
