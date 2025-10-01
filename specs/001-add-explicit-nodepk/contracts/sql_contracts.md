# SQL Contracts: Node Identity and Foreign Keys

## Contract 1: Create Node

**Operation**: `INSERT INTO nodes (node_id) VALUES (?)`

**Preconditions**:
- None (creating new node)

**Postconditions**:
- Node with `node_id` exists in `nodes` table
- OR UNIQUE constraint violation if `node_id` already exists

**Error Conditions**:
- `UNIQUE constraint violation` - Node ID already exists (duplicate)
- `NOT NULL constraint violation` - Node ID is NULL or empty

**Example**:
```sql
INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
-- Success: Node created
-- created_at automatically set to CURRENT_TIMESTAMP

INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
-- Error: UNIQUE constraint violation
```

---

## Contract 2: Create Edge with Node Validation

**Operation**: `INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)`

**Preconditions**:
- Source node (`s`) MUST exist in `nodes` table
- Destination node (`o_id`) MUST exist in `nodes` table

**Postconditions**:
- Edge created with `edge_id` assigned automatically
- OR FOREIGN KEY constraint violation if source or destination node missing

**Error Conditions**:
- `FOREIGN KEY constraint violation (s)` - Source node does not exist
- `FOREIGN KEY constraint violation (o_id)` - Destination node does not exist

**Example**:
```sql
-- Successful edge creation (both nodes exist)
INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
INSERT INTO nodes (node_id) VALUES ('DISEASE:cancer');
INSERT INTO rdf_edges (s, p, o_id)
VALUES ('PROTEIN:TP53', 'associated_with', 'DISEASE:cancer');
-- Success: Edge created with auto-generated edge_id

-- Failed edge creation (destination node missing)
INSERT INTO rdf_edges (s, p, o_id)
VALUES ('PROTEIN:TP53', 'targets', 'NONEXISTENT:node');
-- Error: FOREIGN KEY constraint violation on o_id
```

---

## Contract 3: Assign Label to Node

**Operation**: `INSERT INTO rdf_labels (s, label) VALUES (?, ?)`

**Preconditions**:
- Subject node (`s`) MUST exist in `nodes` table

**Postconditions**:
- Label assigned to node
- OR FOREIGN KEY constraint violation if node missing

**Error Conditions**:
- `FOREIGN KEY constraint violation (s)` - Node does not exist

**Example**:
```sql
INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
INSERT INTO rdf_labels (s, label) VALUES ('PROTEIN:TP53', 'tumor_suppressor');
-- Success: Label assigned

INSERT INTO rdf_labels (s, label) VALUES ('NONEXISTENT:node', 'some_label');
-- Error: FOREIGN KEY constraint violation
```

---

## Contract 4: Assign Property to Node

**Operation**: `INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)`

**Preconditions**:
- Subject node (`s`) MUST exist in `nodes` table

**Postconditions**:
- Property assigned to node
- OR FOREIGN KEY constraint violation if node missing

**Error Conditions**:
- `FOREIGN KEY constraint violation (s)` - Node does not exist

**Example**:
```sql
INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
INSERT INTO rdf_props (s, key, val) VALUES ('PROTEIN:TP53', 'chromosome', '17');
-- Success: Property assigned

INSERT INTO rdf_props (s, key, val) VALUES ('NONEXISTENT:node', 'prop', 'value');
-- Error: FOREIGN KEY constraint violation
```

---

## Contract 5: Create Embedding for Node

**Operation**: `INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, ?)`

**Preconditions**:
- Node (`id`) MUST exist in `nodes` table
- Embedding vector MUST be 768-dimensional

**Postconditions**:
- Embedding created for node
- OR FOREIGN KEY constraint violation if node missing
- OR dimension mismatch error if embedding wrong size

**Error Conditions**:
- `FOREIGN KEY constraint violation (id)` - Node does not exist
- `Dimension mismatch` - Embedding not 768-dimensional

**Example**:
```sql
INSERT INTO nodes (node_id) VALUES ('PROTEIN:TP53');
INSERT INTO kg_NodeEmbeddings (id, emb)
VALUES ('PROTEIN:TP53', TO_VECTOR('[0.1, 0.2, ..., 0.768]'));
-- Success: Embedding created

INSERT INTO kg_NodeEmbeddings (id, emb)
VALUES ('NONEXISTENT:node', TO_VECTOR('[...]'));
-- Error: FOREIGN KEY constraint violation
```

---

## Contract 6: Delete Node (Cascade Behavior)

**Operation**: `DELETE FROM nodes WHERE node_id = ?`

**Preconditions**:
- Node exists in `nodes` table

**Postconditions**:
- Node deleted
- OR FOREIGN KEY constraint violation if edges/labels/props/embeddings reference it

**Error Conditions**:
- `FOREIGN KEY constraint violation` - Node referenced by:
  - `rdf_edges.s` or `rdf_edges.o_id`
  - `rdf_labels.s`
  - `rdf_props.s`
  - `kg_NodeEmbeddings.id`

**Resolution Strategy**:
- Delete all dependent entities first (edges, labels, props, embeddings)
- Then delete node
- OR use migration utility for safe cascade delete

**Example**:
```sql
-- Attempt to delete node with edges
INSERT INTO nodes (node_id) VALUES ('NODE:A'), ('NODE:B');
INSERT INTO rdf_edges (s, p, o_id) VALUES ('NODE:A', 'relates', 'NODE:B');

DELETE FROM nodes WHERE node_id = 'NODE:A';
-- Error: FOREIGN KEY constraint violation (referenced by rdf_edges.s)

-- Correct deletion order
DELETE FROM rdf_edges WHERE s = 'NODE:A' OR o_id = 'NODE:A';
DELETE FROM nodes WHERE node_id = 'NODE:A';
-- Success: Node deleted
```

---

## Contract 7: Bulk Node Insertion (Migration Pattern)

**Operation**: Migration script discovers and inserts all nodes

**Preconditions**:
- Existing graph data in `rdf_*` and `kg_NodeEmbeddings` tables

**Postconditions**:
- All unique node IDs inserted into `nodes` table
- Duplicates handled automatically via `ON DUPLICATE KEY IGNORE`

**Process**:
```sql
INSERT INTO nodes (node_id)
SELECT DISTINCT s FROM (
  SELECT s FROM rdf_labels
  UNION SELECT s FROM rdf_props
  UNION SELECT s FROM rdf_edges
  UNION SELECT o_id FROM rdf_edges
  UNION SELECT id FROM kg_NodeEmbeddings
) all_nodes
ON DUPLICATE KEY IGNORE;
```

**Validation**:
```sql
-- Verify all nodes discovered
SELECT COUNT(*) FROM nodes;
-- Should equal: COUNT(DISTINCT node IDs across all tables)

-- Detect orphaned references (should return 0 rows)
SELECT e.s, e.o_id
FROM rdf_edges e
LEFT JOIN nodes n1 ON e.s = n1.node_id
LEFT JOIN nodes n2 ON e.o_id = n2.node_id
WHERE n1.node_id IS NULL OR n2.node_id IS NULL;
```

---

## Contract Testing Requirements

All contracts MUST be validated via integration tests:

1. **Positive Tests**: Verify successful operations when preconditions met
2. **Negative Tests**: Verify FK violations when preconditions violated
3. **Concurrent Tests**: Verify UNIQUE constraint handling under concurrent inserts
4. **Migration Tests**: Verify bulk discovery and deduplication
5. **Performance Tests**: Verify FK overhead <10% on edge insertion

Test files:
- `tests/integration/test_nodepk_constraints.py` - FK validation tests
- `tests/integration/test_nodepk_migration.py` - Migration validation tests
- `scripts/migrations/benchmark_fk_overhead.py` - Performance validation
