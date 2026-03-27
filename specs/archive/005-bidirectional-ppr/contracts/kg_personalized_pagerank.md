# API Contract: kg_PERSONALIZED_PAGERANK

**Version**: 1.2.0
**Feature**: 005-bidirectional-ppr

## Python API (IRISGraphEngine)

### Method Signature

```python
def kg_PERSONALIZED_PAGERANK(
    self,
    seed_entities: List[str],
    damping_factor: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
    return_top_k: Optional[int] = None,
    bidirectional: bool = False,
    reverse_edge_weight: float = 1.0,
) -> Dict[str, float]:
    """
    Personalized PageRank with optional bidirectional edge traversal.

    Args:
        seed_entities: List of entity IDs to use as seeds (personalization)
        damping_factor: PageRank damping factor (default 0.85)
        max_iterations: Maximum iterations before stopping (default 100)
        tolerance: Convergence threshold (default 1e-6)
        return_top_k: Limit results to top K entities (None = all)
        bidirectional: Enable reverse edge traversal (default False)
        reverse_edge_weight: Weight multiplier for reverse edges (default 1.0)

    Returns:
        Dict mapping entity_id to PageRank score

    Raises:
        ValueError: If reverse_edge_weight is negative
        ValueError: If seed_entities is empty

    Note:
        Performance depends on idx_edges_oid index presence (auto-detected by query planner).
        With index: <15ms for 10K nodes. Without index (table scan): <300ms.
    """
```

### Usage Examples

```python
from iris_vector_graph import IRISGraphEngine

engine = IRISGraphEngine(connection)

# Basic usage (forward edges only, backward compatible)
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["PROTEIN:TP53"]
)

# Bidirectional with equal weight
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["Ewan MacColl"],
    bidirectional=True,
    reverse_edge_weight=1.0,
)

# Bidirectional with reduced reverse weight
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["GENE:BRCA1", "GENE:BRCA2"],
    bidirectional=True,
    reverse_edge_weight=0.5,
    return_top_k=100,
)
```

## SQL Stored Procedure

### Procedure Signature

```sql
CREATE OR REPLACE PROCEDURE kg_PERSONALIZED_PAGERANK(
  IN seedEntities VARCHAR(32000),      -- JSON array: '["entity1", "entity2"]'
  IN dampingFactor DOUBLE DEFAULT 0.85,
  IN maxIterations INT DEFAULT 100,
  IN tolerance DOUBLE DEFAULT 0.000001,
  IN returnTopK INT DEFAULT NULL,
  IN bidirectional BOOLEAN DEFAULT FALSE,
  IN reverseEdgeWeight DOUBLE DEFAULT 1.0
)
RETURNS TABLE (entity_id VARCHAR(256), score DOUBLE)
```

### Usage Examples

```sql
-- Basic usage
CALL kg_PERSONALIZED_PAGERANK('["PROTEIN:TP53"]')

-- Bidirectional
CALL kg_PERSONALIZED_PAGERANK(
    '["Ewan MacColl"]',
    0.85,    -- dampingFactor
    100,     -- maxIterations
    0.000001, -- tolerance
    50,      -- returnTopK
    TRUE,    -- bidirectional
    1.0      -- reverseEdgeWeight
)

-- Using table function syntax
SELECT * FROM TABLE(kg_PERSONALIZED_PAGERANK('["GENE:TP53"]', 0.85, 100, 0.000001, 100, TRUE, 0.5))
ORDER BY score DESC
```

## ObjectScript Class Method

### Method Signature

```objectscript
ClassMethod ComputePageRank(
    nodeFilter As %String = "%",
    maxIterations As %Integer = 10,
    dampingFactor As %Numeric = 0.85,
    seedEntities As %String = "",
    bidirectional As %Boolean = 0,
    reverseEdgeWeight As %Numeric = 1.0
) As %DynamicArray [ Language = python ]
```

### Usage Example

```objectscript
Set seeds = "[""PROTEIN:TP53""]"
Set results = ##class(PageRankEmbedded).ComputePageRank("%", 100, 0.85, seeds, 1, 1.0)
Do results.%ToJSON()
```

## Response Format

### Success Response

```json
{
  "ENTITY:seed1": 0.15,
  "ENTITY:neighbor1": 0.12,
  "ENTITY:neighbor2": 0.08,
  "ENTITY:distant1": 0.03
}
```

### Error Responses

| Error | Condition | Message |
|-------|-----------|---------|
| ValueError | reverse_edge_weight < 0 | "reverse_edge_weight must be non-negative, got: {value}" |
| ValueError | empty seed_entities | "seed_entities must contain at least one entity" |

## Backward Compatibility

| Parameter | Default | Behavior |
|-----------|---------|----------|
| bidirectional | False | Forward-only (v1.1.6 behavior) |
| reverse_edge_weight | 1.0 | Ignored when bidirectional=False |

Existing code using `kg_PERSONALIZED_PAGERANK` without new parameters will work unchanged.

## Performance Contract

| Scenario | Target |
|----------|--------|
| 10K nodes, bidirectional=False | <10ms |
| 10K nodes, bidirectional=True | <15ms |
| 100K nodes, bidirectional=True | <250ms |
| No regression when bidirectional=False | 0% overhead |
