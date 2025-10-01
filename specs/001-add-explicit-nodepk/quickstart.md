# NodePK Feature Quickstart

## Prerequisites
- IRIS database running (docker-compose up -d)
- Python environment activated (source .venv/bin/activate)

## Step 1: Run Migration

```bash
uv run python scripts/migrations/migrate_to_nodepk.py --validate-only
# Expected: Report of nodes discovered, duplicates, orphans

uv run python scripts/migrations/migrate_to_nodepk.py --execute
# Expected: Nodes table populated, FKs added, validation passed
```

## Step 2: Verify Constraints

```bash
uv run pytest tests/integration/test_nodepk_constraints.py -v
# Expected: All tests pass (FK violations correctly raised)
```

## Step 3: Test Data Insertion

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

## Step 4: Performance Validation

```bash
uv run python scripts/migrations/benchmark_fk_overhead.py
# Expected: <10% degradation on edge insertion
```

## Success Criteria
- ✅ Migration completes without data loss
- ✅ FK constraints enforce node existence
- ✅ Performance overhead within acceptable range (<10%)
- ✅ All integration tests pass
