# Quickstart: Bidirectional Personalized PageRank

## Overview

Bidirectional PageRank enables discovering entities connected via incoming edges in knowledge graphs with asymmetric relationships.

**Problem**: Graph has edge `Peggy Seeger → [married to] → Ewan MacColl`. Query "Who was Ewan MacColl married to?" cannot find Peggy because PageRank only follows forward edges.

**Solution**: Enable `bidirectional=True` to traverse edges in both directions.

## Prerequisites

- IRIS 2025.1+ with embedded Python
- iris-vector-graph package installed
- Database with nodes and rdf_edges tables populated

## Quick Example

```python
from iris_vector_graph import IRISGraphEngine
import iris

# Connect to IRIS
conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)

# Find entities related to Ewan MacColl (including via incoming edges)
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["Ewan MacColl"],
    bidirectional=True,
    reverse_edge_weight=1.0,
    return_top_k=10,
)

# Print results
for entity, score in sorted(scores.items(), key=lambda x: -x[1]):
    print(f"{entity}: {score:.4f}")

# Output:
# Ewan MacColl: 0.1500
# Peggy Seeger: 0.1200  # Now reachable via reverse edge!
# Folk Music: 0.0800
```

## Usage Patterns

### Pattern 1: Forward-Only (Default, Backward Compatible)

```python
# Existing behavior - only follows subject → object edges
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["PROTEIN:TP53"]
)
```

### Pattern 2: Bidirectional with Equal Weight

```python
# Reverse edges contribute equally to forward edges
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["PROTEIN:TP53"],
    bidirectional=True,
    reverse_edge_weight=1.0,
)
```

### Pattern 3: Bidirectional with Reduced Reverse Weight

```python
# Reverse edges contribute 50% of forward edges
# Useful when edge direction has semantic meaning
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["DRUG:aspirin"],
    bidirectional=True,
    reverse_edge_weight=0.5,
)
```

### Pattern 4: Multi-Seed Personalization

```python
# Start from multiple entities
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["GENE:BRCA1", "GENE:BRCA2", "GENE:TP53"],
    bidirectional=True,
    return_top_k=50,
)
```

## SQL Usage

```sql
-- Bidirectional PageRank via SQL (returns JSON, parse with JSON_TABLE)
SELECT entity_id, score
FROM JSON_TABLE(
    kg_PPR(
        '["PROTEIN:TP53"]',  -- seed entities (JSON array)
        0.85,                -- damping factor
        100,                 -- max iterations
        1,                   -- bidirectional (1=true, 0=false)
        1.0                  -- reverse edge weight
    ),
    '$[*]' COLUMNS(
        entity_id VARCHAR(256) PATH '$.nodeId',
        score DOUBLE PATH '$.pagerank'
    )
)
ORDER BY score DESC
FETCH FIRST 50 ROWS ONLY;
```

## Common Use Cases

### 1. Question Answering over Knowledge Graphs

```python
# Query: "What drugs target proteins associated with cancer?"
# Graph direction: Drug → [targets] → Protein → [associated_with] → Disease

# Without bidirectional: Starting from "cancer" can't reach drugs
# With bidirectional: Can traverse backward through protein to find drugs

scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["DISEASE:cancer"],
    bidirectional=True,
)
drugs = [e for e in scores if e.startswith("DRUG:")]
```

### 2. Finding Related Entities Regardless of Edge Direction

```python
# Find all entities related to a person, whether they:
# - Point to the person (e.g., "wrote" → author)
# - Are pointed to by the person (e.g., person → "works_at")

scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["PERSON:Albert_Einstein"],
    bidirectional=True,
    return_top_k=100,
)
```

### 3. Weighted Relationship Importance

```python
# In some domains, reverse relationships are less meaningful
# e.g., "child_of" reversed is "parent_of" - equally valid
# but "employee_of" reversed is less natural

scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["COMPANY:Google"],
    bidirectional=True,
    reverse_edge_weight=0.3,  # Reduce noise from reverse edges
)
```

## Performance Tips

1. **Start with bidirectional=False** to establish baseline
2. **Use return_top_k** to limit result size for large graphs
3. **Reduce max_iterations** for exploratory queries (10-20 is often sufficient)
4. **Monitor with logging** - new parameters are logged per FR-008

## Troubleshooting

### No results returned

```python
# Check if seed entities exist
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", ["ENTITY:test"])
count = cursor.fetchone()[0]
print(f"Seed exists: {count > 0}")
```

### Performance slower than expected

```python
# Check graph size
cursor.execute("SELECT COUNT(*) FROM nodes")
node_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM rdf_edges")
edge_count = cursor.fetchone()[0]
print(f"Graph: {node_count} nodes, {edge_count} edges")

# Expected: <15ms for 10K nodes with bidirectional=True
```

### ValueError on reverse_edge_weight

```python
# reverse_edge_weight must be >= 0
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["ENTITY:test"],
    bidirectional=True,
    reverse_edge_weight=-0.5,  # ERROR: negative not allowed
)
```

## Next Steps

- See [API Contract](./contracts/kg_personalized_pagerank.md) for full parameter documentation
- See [Data Model](./data-model.md) for entity relationships
- See [Research](./research.md) for architecture decisions
