# Implementation Plan: Bidirectional Personalized PageRank

**Branch**: `005-bidirectional-ppr` | **Date**: 2025-12-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-bidirectional-ppr/spec.md`

## Summary

Add bidirectional edge traversal support to Personalized PageRank for improved multi-hop reasoning in knowledge graphs with asymmetric relationships. The implementation extends the existing `PageRankEmbedded.cls` ObjectScript class, exposes it via SQL stored procedure, and provides a Python API wrapper in `IRISGraphEngine`.

## Technical Context

**Language/Version**: Python 3.11, ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: iris-vector-graph-core, IRIS embedded Python
**Storage**: IRIS SQL tables (nodes, rdf_edges, rdf_labels)
**Testing**: pytest with live IRIS database (@pytest.mark.requires_database)
**Target Platform**: IRIS 2025.1+ with embedded Python support
**Project Type**: Single project (library extension)
**Performance Goals**: <15ms for 10K nodes with idx_edges_oid index, <300ms table scan fallback
**Constraints**: Backward compatible (bidirectional=False by default)
**Scale/Scope**: 10K-100K node graphs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. IRIS-Native Development | PASS | Uses embedded Python in ObjectScript, SQL procedures |
| II. Test-First with Live Database | PASS | Tests use @pytest.mark.requires_database |
| III. Performance as a Feature | PASS | Performance targets defined (<15ms/<300ms) |
| IV. Hybrid Search by Default | N/A | PageRank is graph-only algorithm |
| V. Observability and Debuggability | PASS | FR-008 requires logging new parameters |
| VI. Modular Core Library | PASS | Python wrapper in IRISGraphEngine, core in ObjectScript |
| VII. Explicit Error Handling | PASS | FR-005 requires validation with clear error messages |
| VIII. Standardized Database Interfaces | PASS | Uses SQL stored procedure pattern |

## Architecture Decision

**Selected Approach**: SQL Stored Procedure wrapping ObjectScript embedded Python

```
HippoRAG2 (external Python)
    ↓
IRISGraphEngine.kg_PERSONALIZED_PAGERANK()
    ↓
cursor.execute("CALL kg_PERSONALIZED_PAGERANK(...)")
    ↓
SQL Stored Procedure (operators.sql)
    ↓
PageRankEmbedded.cls (embedded Python, 10-50ms)
```

**Rationale**:
- Provides Python API surface HippoRAG2 expects
- Gets embedded Python performance (10-50ms vs 200ms)
- Follows IRIS-native development principle
- Consistent with existing kg_KNN_VEC, kg_RRF_FUSE patterns

## Project Structure

### Documentation (this feature)

```text
specs/005-bidirectional-ppr/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Architecture decision record
├── data-model.md        # Entity definitions
├── quickstart.md        # Usage examples
├── contracts/           # API contracts
│   └── kg_personalized_pagerank.md
└── tasks.md             # Implementation tasks (via /speckit.tasks)
```

### Source Code (repository root)

```text
iris/src/
└── PageRankEmbedded.cls     # MODIFY: Add bidirectional parameters

sql/
└── operators.sql            # MODIFY: Add kg_PERSONALIZED_PAGERANK procedure

iris_vector_graph/
└── engine.py                # MODIFY: Add kg_PERSONALIZED_PAGERANK method

tests/
├── integration/
│   ├── test_bidirectional_ppr.py        # NEW: Bidirectional tests
│   └── test_nodepk_graph_analytics.py   # MODIFY: Add bidirectional cases
└── contract/
    └── test_ppr_api.py                  # NEW: API contract tests
```

**Structure Decision**: Extends existing single-project structure. Core algorithm in ObjectScript embedded Python, exposed via SQL procedure, wrapped in Python API.

## Implementation Layers

### Layer 1: ObjectScript (PageRankEmbedded.cls)

Extend `ComputePageRank` and `ComputePageRankWithMetrics` methods:

```objectscript
ClassMethod ComputePageRank(
    nodeFilter As %String = "%",
    maxIterations As %Integer = 10,
    dampingFactor As %Numeric = 0.85,
    seedEntities As %String = "",           // NEW: JSON array of seed node IDs
    bidirectional As %Boolean = 0,          // NEW: Enable reverse edge traversal
    reverseEdgeWeight As %Numeric = 1.0     // NEW: Weight for reverse edges
) As %DynamicArray [ Language = python ]
```

### Layer 2: SQL Stored Procedure (operators.sql)

```sql
CREATE OR REPLACE PROCEDURE kg_PERSONALIZED_PAGERANK(
  IN seedEntities VARCHAR(32000),      -- JSON array of seed entity IDs
  IN dampingFactor DOUBLE DEFAULT 0.85,
  IN maxIterations INT DEFAULT 100,
  IN tolerance DOUBLE DEFAULT 0.000001,
  IN returnTopK INT DEFAULT NULL,
  IN bidirectional BOOLEAN DEFAULT FALSE,
  IN reverseEdgeWeight DOUBLE DEFAULT 1.0
)
RETURNS TABLE (entity_id VARCHAR(256), score DOUBLE)
LANGUAGE OBJECTSCRIPT
BEGIN
  -- Call PageRankEmbedded.ComputePageRank and return results
END;
```

### Layer 3: Python Wrapper (IRISGraphEngine)

```python
def kg_PERSONALIZED_PAGERANK(
    self,
    seed_entities: List[str],
    damping_factor: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
    return_top_k: Optional[int] = None,
    # Note: idx_edges_oid index auto-detected by query planner
    bidirectional: bool = False,
    reverse_edge_weight: float = 1.0,
) -> Dict[str, float]:
    """Personalized PageRank with optional bidirectional edge traversal."""
```

## Complexity Tracking

> No constitution violations requiring justification.

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Three-layer architecture | Necessary | Provides Python API while using IRIS-native performance |
| New SQL procedure | Consistent | Follows existing kg_* operator pattern |
