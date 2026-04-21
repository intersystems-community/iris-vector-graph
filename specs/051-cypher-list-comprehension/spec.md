# Spec 051: Cypher List Comprehensions and Collection Functions

**Branch**: `051-cypher-list-comprehension`
**Created**: 2026-04-18
**Status**: Draft

## Overview

openCypher supports several collection-manipulation expressions that iris-vector-graph does not implement:

**List comprehensions**: `[x IN list WHERE condition | expression]`
**Predicate functions**: `ALL(x IN list WHERE cond)`, `ANY(x IN list WHERE cond)`, `NONE(x IN list WHERE cond)`, `SINGLE(x IN list WHERE cond)`
**Reduction**: `reduce(acc = init, x IN list | acc + x)`
**Extract/filter (legacy)**: `filter(x IN list WHERE cond)`, `extract(x IN list | expr)` (Cypher 9 compat)

These appear in production queries from LangChain, graph algorithms, and path filtering. The most critical is `ALL(x IN nodes(p) WHERE ...)` which is used for path constraint queries and was explicitly called out as out-of-scope for spec 047 — now addressing it here.

## Clarifications

### Session 2026-04-18
- Q: Are list comprehensions evaluated in-database or Python-side? → A: In-database via SQL JSON functions where possible; complex cases may fall back to Python post-processing.
- Q: Priority order? → A: `ALL/ANY/NONE/SINGLE` first (used in path queries), then list comprehension `[x IN list | expr]`, then `reduce()`.
- Q: Should `filter()` / `extract()` legacy forms be supported? → A: Yes as aliases — many Cypher 9 queries use them.

## User Scenarios & Testing

### User Story 1 — Path constraint with `ALL()` (P1)

```cypher
MATCH p = shortestPath((a {id: $from})-[*..8]-(b {id: $to}))
WHERE ALL(n IN nodes(p) WHERE n.approved = true)
RETURN p
```

Filters paths where every node meets a condition.

**Independent Test**: `RETURN ALL(x IN [1,2,3] WHERE x > 0)` returns `true`.

**Acceptance Scenarios**:
1. `ALL(x IN [1,2,3] WHERE x > 0)` → `true`
2. `ALL(x IN [1,-1,3] WHERE x > 0)` → `false`
3. `ANY(x IN [1,-1,3] WHERE x < 0)` → `true`
4. `NONE(x IN [1,2,3] WHERE x < 0)` → `true`
5. `SINGLE(x IN [1,2,3] WHERE x = 2)` → `true`

### User Story 2 — List comprehension (P1)

```cypher
RETURN [x IN range(1, 10) WHERE x % 2 = 0 | x * x]
-- returns [4, 16, 36, 64, 100]
```

**Independent Test**: `RETURN [x IN [1,2,3,4] WHERE x > 2 | x]` returns `[3, 4]`.

### User Story 3 — `reduce()` (P2)

```cypher
RETURN reduce(total = 0, x IN [1,2,3,4,5] | total + x) AS sum
-- returns 15
```

**Independent Test**: `RETURN reduce(s = '', x IN ['a','b','c'] | s + x)` returns `'abc'`.

### Edge Cases
- `ALL(x IN [] WHERE ...)` → `true` (vacuously true)
- `ANY(x IN [] WHERE ...)` → `false`
- `NONE(x IN [] WHERE ...)` → `true`
- `SINGLE(x IN [] WHERE ...)` → `false`
- Nested comprehensions: `[x IN [y IN [1,2,3] | y*2] WHERE x > 3]`

## Requirements

### Functional Requirements
- **FR-001**: `ALL(var IN list WHERE condition)` MUST return boolean true if all elements satisfy condition
- **FR-002**: `ANY(var IN list WHERE condition)` MUST return boolean true if any element satisfies condition
- **FR-003**: `NONE(var IN list WHERE condition)` MUST return boolean true if no elements satisfy condition
- **FR-004**: `SINGLE(var IN list WHERE condition)` MUST return boolean true if exactly one element satisfies condition
- **FR-005**: `[var IN list WHERE condition | expression]` list comprehension MUST return filtered+mapped list
- **FR-006**: `[var IN list | expression]` (map-only, no filter) MUST return mapped list
- **FR-007**: `reduce(acc = init, var IN list | accumulator_expr)` MUST return accumulated value
- **FR-008**: `filter(var IN list WHERE condition)` MUST work as alias for `[var IN list WHERE condition | var]`
- **FR-009**: `extract(var IN list | expression)` MUST work as alias for `[var IN list | expression]`
- **FR-010**: All functions MUST handle empty lists per openCypher spec (see edge cases)

## Success Criteria
- **SC-001**: `WHERE ALL(n IN nodes(p) WHERE ...)` works in conjunction with shortestPath
- **SC-002**: `[x IN range(1,5) | x*2]` returns `[2,4,6,8,10]`
- **SC-003**: Zero regressions on existing tests
