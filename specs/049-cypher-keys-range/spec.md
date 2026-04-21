# Spec 049: keys(n), range(), and Missing Cypher Functions

**Branch**: `049-cypher-keys-range`
**Created**: 2026-04-18
**Status**: Draft

## Overview

Several standard openCypher functions currently return NULL or throw errors in iris-vector-graph:
- `keys(n)` — should return list of property keys for a node or relationship
- `range(start, end)` / `range(start, end, step)` — should return a list of integers
- `size(list)` — should return integer count of list elements
- `head(list)` / `tail(list)` — first element / all-but-first of a list
- `last(list)` — last element of a list
- `isEmpty(x)` — true if string/list/map is empty
- `reverse(list)` — reverse order of a list

These are used in production Cypher queries from LangChain, py2neo, and hand-written queries. Their absence silently returns wrong results or throws parser errors.

## Clarifications

### Session 2026-04-18
- Q: Which functions are highest priority? → A: `keys(n)` and `range()` — both appear in LangChain schema introspection and standard graph exploration queries. Others are secondary.
- Q: Should `keys(n)` return relationship properties too? → A: Yes — `keys(r)` for relationships should also work, returning edge qualifier keys.
- Q: Should `range()` support step parameter? → A: Yes — `range(0, 10, 2)` returning `[0,2,4,6,8,10]`.

## User Scenarios & Testing

### User Story 1 — `keys(n)` returns property keys (P1)

A user exploring schema writes:
```cypher
MATCH (n:Drug) RETURN keys(n) LIMIT 5
```
Gets back `["id", "name", "smiles"]` per node — the actual property keys stored.

**Independent Test**: Create node with 3 properties; `RETURN keys(n)` returns list containing all 3 key names.

**Acceptance Scenarios**:
1. **Given** node with props `{id: 'x', name: 'y'}`, **When** `RETURN keys(n)`, **Then** returns `["id", "name"]` (order unspecified)
2. **Given** node with no properties, **When** `RETURN keys(n)`, **Then** returns `[]`
3. **Given** relationship `r` with qualifiers `{confidence: 0.9}`, **When** `RETURN keys(r)`, **Then** returns `["confidence"]`

### User Story 2 — `range(start, end)` generates integer lists (P1)

```cypher
UNWIND range(1, 5) AS i RETURN i
```
Returns rows 1, 2, 3, 4, 5.

**Independent Test**: `RETURN range(0, 3)` returns `[0, 1, 2, 3]`.

**Acceptance Scenarios**:
1. `range(1, 5)` → `[1, 2, 3, 4, 5]`
2. `range(0, 10, 3)` → `[0, 3, 6, 9]`
3. `range(5, 1, -1)` → `[5, 4, 3, 2, 1]`
4. `UNWIND range(1,3) AS i RETURN i` → rows 1, 2, 3

### User Story 3 — List utility functions (P2)

```cypher
RETURN size([1,2,3])          -- 3
RETURN head([1,2,3])          -- 1
RETURN tail([1,2,3])          -- [2,3]
RETURN last([1,2,3])          -- 3
RETURN isEmpty([])            -- true
RETURN reverse([1,2,3])       -- [3,2,1]
```

**Independent Test**: Each returns correct value for a known list literal.

### Edge Cases
- `keys(null)` → returns `[]` not error
- `range(5, 5)` → `[5]` (single element)
- `range(5, 1)` (no step, start > end) → `[]`
- `size(null)` → `0`
- `head([])` → `null`
- `tail([])` → `[]`

## Requirements

### Functional Requirements
- **FR-001**: `keys(n)` MUST return list of property key strings for any node variable
- **FR-002**: `keys(r)` MUST return list of qualifier key strings for any relationship variable
- **FR-003**: `range(start, end)` MUST return inclusive integer list
- **FR-004**: `range(start, end, step)` MUST return stepped integer list, supporting negative step
- **FR-005**: `size(list)` MUST return integer count
- **FR-006**: `head(list)`, `tail(list)`, `last(list)` MUST return correct elements
- **FR-007**: `isEmpty(x)` MUST return boolean for strings, lists, and maps
- **FR-008**: All functions MUST handle null input gracefully (return null or [] not error)

## Success Criteria
- **SC-001**: LangChain `Neo4jGraph.refresh_schema()` completes without error (it calls `keys()`)
- **SC-002**: `UNWIND range(1, 100) AS i RETURN i` returns 100 rows
- **SC-003**: Zero regressions on existing 513 unit tests
