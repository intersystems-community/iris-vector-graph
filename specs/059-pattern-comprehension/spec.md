# Spec 059: Pattern Comprehension

**Created**: 2026-04-18 | **Branch**: 048-unified-edge-store

## Overview

Pattern comprehension `[(a)-[r]->(b) | r.weight]` is distinct from list comprehension `[x IN list | expr]`. It matches a graph pattern and collects a projection from each match. v1.53.1's list comprehension parser expects `IDENTIFIER IN` after `[`, so graph patterns fail with parse error. Pattern comprehension is commonly used to collect relationship properties or neighbor IDs inline.

## Requirements

- **FR-001**: `[(a)-[r]->(b) | expr]` MUST parse as a pattern comprehension returning a list
- **FR-002**: Pattern variables (`a`, `r`, `b`) MUST be scoped to the comprehension
- **FR-003**: Outer bound variables MUST be accessible inside the pattern
- **FR-004**: Optional WHERE filter: `[(a)-[r]->(b) WHERE r.weight > 0 | r.weight]`
- **FR-005**: Result is a list usable in RETURN, WITH, or further expressions

## User Scenarios

```cypher
MATCH (n:Drug)
RETURN n.id, [(n)-[r:TREATS]->(d) | d.id] AS diseases

MATCH (a)
WITH a, [(a)-[r]->(b) WHERE r.weight > 0.5 | {rel: type(r), target: b.id}] AS strong_rels
RETURN a.id, strong_rels
```

## Success Criteria
- `[(n)-[r:TREATS]->(d) | d.id]` returns list of target node IDs
- WHERE filter inside pattern comprehension works
- Outer bound variable accessible in pattern
- Zero regressions
