# Spec 058: EXISTS Subquery with Edge Patterns

**Created**: 2026-04-18 | **Branch**: 048-unified-edge-store

## Overview

`EXISTS { (n)-[r]->(m) }` crashes with `Expected (, got -` in v1.53.1. The parser's `parse_graph_pattern()` inside EXISTS braces fails because it expects `(` but finds `-` when parsing the relationship. Node-only `EXISTS { (n) }` works. Edge patterns are the primary use case for EXISTS in WHERE clauses.

## Requirements

- **FR-001**: `EXISTS { (a)-[r]->(b) }` MUST parse and translate to a SQL EXISTS subquery
- **FR-002**: `EXISTS { (a)-[r:TYPE]->(b) }` MUST support relationship type filter
- **FR-003**: `EXISTS { (a {id: $x})-[r]->(b) }` MUST support property filters on pattern nodes
- **FR-004**: `NOT EXISTS { (a)-[r]->(b) }` MUST negate the check
- **FR-005**: Bound variables from outer MATCH MUST be accessible inside EXISTS pattern

## User Scenarios

```cypher
MATCH (n:Drug)
WHERE EXISTS { (n)-[:TREATS]->(d:Disease) }
RETURN n.id

MATCH (a)-[r]->(b)
WHERE NOT EXISTS { (b)-[:DEPRECATED]->(x) }
RETURN a.id, b.id
```

## Success Criteria
- `WHERE EXISTS { (a)-[:TREATS]->(b) }` returns correct filtered results
- `NOT EXISTS` correctly excludes matching nodes
- Outer bound variable `n` resolves correctly inside EXISTS pattern
- Zero regressions
