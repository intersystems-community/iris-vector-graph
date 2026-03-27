# NodePK Feature Quickstart

**Status**: Implementation complete (88% - T001-T029)
**Branch**: `001-add-explicit-nodepk`

## Overview

The NodePK feature adds explicit node identity with foreign key constraints to ensure referential integrity across all graph entities. This guide covers:
- Migration from implicit to explicit node identity
- Validation and execution workflows
- Performance benchmarking
- Graph analytics with embedded Python

## Prerequisites
- IRIS database running (docker-compose up -d or docker-compose -f docker-compose.acorn.yml up -d)
- Python environment activated (uv sync && source .venv/bin/activate)
- Existing graph data loaded (or will create test data)

## Step 1: Validate Migration (Dry Run)

First, validate your existing data without making changes:

```bash
uv run python scripts/migrations/migrate_to_nodepk.py --validate-only
```

**Expected output:**
```
=== NodePK Migration Validation ===
Discovered 1000 unique nodes across graph tables
  - From rdf_edges (source): 500 nodes
  - From rdf_edges (destination): 500 nodes
  - From rdf_labels: 800 nodes
  - From rdf_props: 850 nodes
  - From kg_NodeEmbeddings: 300 nodes

Checking for orphaned references...
  ✓ No orphans found in rdf_edges
  ✓ No orphans found in rdf_labels
  ✓ No orphans found in rdf_props
  ✓ No orphans found in kg_NodeEmbeddings

=== Validation Result: READY FOR MIGRATION ===
```

If orphans are found, the report will list them:
```
Orphaned references detected:
  rdf_edges: 5 orphaned source nodes, 3 orphaned destination nodes
  rdf_labels: 2 orphaned subject nodes

=== Validation Result: NOT READY (fix orphans first) ===
```

## Step 2: Execute Migration

Once validation passes, execute the migration:

```bash
uv run python scripts/migrations/migrate_to_nodepk.py --execute
```

**Expected output:**
```
=== NodePK Migration Execution ===
Step 1: Discovering nodes...
  Discovered 1000 unique nodes

Step 2: Creating nodes table...
  Executing sql/migrations/001_add_nodepk_table.sql
  ✓ nodes table created

Step 3: Inserting nodes...
  Inserted 1000 nodes (6496 nodes/sec)

Step 4: Adding foreign key constraints...
  Executing sql/migrations/002_add_fk_constraints.sql
  ✓ Foreign keys added:
    - rdf_edges.s -> nodes.node_id
    - rdf_edges.o_id -> nodes.node_id
    - rdf_labels.s -> nodes.node_id
    - rdf_props.s -> nodes.node_id
    - kg_NodeEmbeddings.id -> nodes.node_id

Step 5: Final validation...
  ✓ No orphans detected

=== Migration Complete: SUCCESS ===
```

**Verbose mode** (for debugging):
```bash
uv run python scripts/migrations/migrate_to_nodepk.py --execute --verbose
```

## Step 3: Verify Constraints

Run the constraint validation tests:

```bash
uv run pytest tests/integration/test_nodepk_migration.py -v
```

**Expected output:**
```
tests/integration/test_nodepk_migration.py::test_discover_nodes PASSED
tests/integration/test_nodepk_migration.py::test_bulk_insert_nodes PASSED
tests/integration/test_nodepk_migration.py::test_detect_orphans PASSED
tests/integration/test_nodepk_migration.py::test_validate_migration PASSED
tests/integration/test_nodepk_migration.py::test_execute_migration PASSED

====== 5 passed in 2.34s ======
```

## Step 4: Test Data Insertion with FK Validation

```python
import iris
conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
cursor = conn.cursor()

# Insert node first
cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST:node1')")

# Insert edge (should succeed)
cursor.execute(
    "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
    ['TEST:node1', 'relates_to', 'TEST:node1']
)

# Try inserting edge with invalid node (should fail)
try:
    cursor.execute(
        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        ['INVALID:node', 'relates_to', 'TEST:node1']
    )
except Exception as e:
    print(f"Expected FK violation: {e}")
```

## Step 5: Performance Validation

Run comprehensive performance benchmarks:

```bash
# Basic performance gates (node lookup, bulk insert, FK overhead)
uv run pytest tests/integration/test_nodepk_performance.py -v -s

# Advanced benchmarks (vector + complex queries)
uv run pytest tests/integration/test_nodepk_advanced_benchmarks.py -v -s

# Graph analytics (PageRank, BFS, centrality)
uv run pytest tests/integration/test_nodepk_graph_analytics.py -v -s
```

**Expected results:**
- ✅ Node lookup: <1ms (actual: 0.292ms)
- ✅ Bulk insertion: ≥1000 nodes/sec (actual: 6,496 nodes/sec)
- ✅ FK overhead: <10% degradation (actual: -64% = IMPROVED!)
- ✅ Graph traversal: 0.09ms per hop
- ✅ PageRank (1K nodes): <500ms (actual: 5.31ms)
- ✅ Concurrent: ≥100 qps (actual: 702 qps)

## Step 6: Graph Analytics with Embedded Python

Test PageRank computation using IRIS embedded Python:

**IRIS Terminal:**
```objectscript
// Connect to IRIS terminal
docker exec -it iris-acorn-1 iris session iris

// Compute PageRank on graph subset
set results = ##class(PageRankEmbedded).ComputePageRank("PROTEIN:%", 10, 0.85)
do results.%ToJSON()

// With convergence tracking
set metrics = ##class(PageRankEmbedded).ComputePageRankWithMetrics("PROTEIN:%", 10, 0.85, 0.0001)
do metrics.%ToJSON()
```

**Python Client:**
```python
import iris, json

conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
cursor = conn.cursor()

# Call embedded Python PageRank
cursor.execute("SELECT ##class(PageRankEmbedded).ComputePageRank('PROTEIN:%', 10, 0.85)")
pagerank_json = cursor.fetchone()[0]

results = json.loads(pagerank_json)
print(f"PageRank computed for {len(results)} nodes:")
for node in results[:10]:  # Top 10
    print(f"  {node['nodeId']}: {node['pagerank']:.6f}")
```

**Expected:** PageRank results in 5-10ms for 1K nodes (vs 500ms client-side baseline)

## Success Criteria

- ✅ Migration completes without data loss
- ✅ FK constraints enforce node existence (reject invalid inserts)
- ✅ Performance exceeds targets:
  - Node lookup: <1ms ✓ (0.292ms)
  - Bulk insert: ≥1000/s ✓ (6,496/s)
  - FK overhead: <10% ✓ (-64% improvement!)
  - PageRank: <500ms ✓ (5.31ms)
  - Concurrent: ≥100 qps ✓ (702 qps)
- ✅ All integration tests pass
- ✅ Embedded Python PageRank functional and performant

## Troubleshooting

### Migration Fails with Orphans

**Problem:** Validation reports orphaned references

**Solution:**
```python
# Option 1: Fix orphans by creating missing nodes
cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [orphaned_node_id])

# Option 2: Delete orphaned references
cursor.execute("DELETE FROM rdf_edges WHERE s = ?", [orphaned_node_id])

# Re-run migration
uv run python scripts/migrations/migrate_to_nodepk.py --execute
```

### FK Constraint Violations After Migration

**Problem:** Cannot insert edges/labels/properties

**Solution:** Ensure node exists first:
```python
# Always insert node before inserting dependent entities
cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ['NEW:node'])
cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
               ['NEW:node', 'relates_to', 'OTHER:node'])
```

### Performance Degradation

**Problem:** Queries slower after FK constraints

**Solution:** FK constraints should IMPROVE performance. If degraded:
1. Check if indexes are built: `SHOW INDEXES FROM nodes`
2. Run `ANALYZE TABLE nodes` to update statistics
3. Verify IRIS query optimizer is using FK metadata

## Next Steps

- Read [`docs/architecture/embedded_python_architecture.md`](../../docs/architecture/embedded_python_architecture.md) for hybrid query patterns
- Review [`docs/performance/nodepk_benchmark_results.md`](../../docs/performance/nodepk_benchmark_results.md) for detailed performance analysis
- See [`docs/performance/graph_analytics_roadmap.md`](../../docs/performance/graph_analytics_roadmap.md) for optimization phases
- Explore [`specs/001-add-explicit-nodepk/plan.md`](./plan.md) for complete implementation retrospective
