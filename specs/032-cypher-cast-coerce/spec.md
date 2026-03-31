# Feature Specification: Cypher CAST Functions + COUNT(DISTINCT)

**Feature Branch**: `032-cypher-cast-coerce`  
**Created**: 2026-03-31  
**Status**: Draft  
**Source**: docs/cypher-gap-recommendations.md Gap #8 (COUNT DISTINCT) + Gap #10 (Type coercion)

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Type Coercion Functions Work Correctly (Priority: P1)

A developer writing biomedical Cypher queries wants to use type coercion functions like `toInteger()`, `toFloat()`, `toString()`, `toBoolean()` to normalize property values. Currently these are mapped in the translator but emit broken SQL (CAST without target type).

**Why this priority**: P0 quick win — 10-line fix. Unblocks chromosome filtering, confidence scoring, and boolean property normalization.

**Acceptance Scenarios**:

1. **Given** a gene node with `chromosome` property "7", **When** `WHERE toInteger(g.chromosome) = 7`, **Then** only genes on chromosome 7 are returned.
2. **Given** nodes with float-valued properties, **When** `toFloat(n.score) > 0.8`, **Then** the comparison works correctly.
3. **Given** integer properties, **When** `toString(n.count)` is used in RETURN, **Then** the output is a string.
4. **Given** a string property "true" or "1", **When** `toBoolean(n.active)` is used, **Then** it returns a truthy value.

---

### User Story 2 — COUNT(DISTINCT) Returns Deduplicated Counts (Priority: P1)

A developer wants `COUNT(DISTINCT g.name) AS unique_genes` to return deduplicated counts.

**Acceptance Scenarios**:

1. **Given** a gene connected to multiple pathways with duplicate relationships, **When** `RETURN COUNT(DISTINCT p.name) AS unique_pathways`, **Then** the result is the correct deduplicated count.
2. **Given** any aggregation query, **When** `COUNT(DISTINCT expr)` is used, **Then** the generated SQL contains `COUNT(DISTINCT ...)`.

---

### Edge Cases

- `toInteger()` on non-numeric string: IRIS CAST raises error — document as expected behavior, not a bug.
- `toBoolean()` on unexpected values: defaults to 0 (false). Comparison is case-insensitive.

## Clarifications

### Session 2026-03-31

- Q: Should toBoolean() comparison be case-insensitive? → A: Yes (Option A). Use LOWER(expr) IN ('true','1','yes','y') to match Neo4j behavior and handle inconsistent property casing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `toInteger(expr)` MUST emit `CAST(expr AS INTEGER)`.
- **FR-002**: `toFloat(expr)` MUST emit `CAST(expr AS DOUBLE)`.
- **FR-003**: `toString(expr)` MUST emit `CAST(expr AS VARCHAR(4096))`.
- **FR-004**: `toBoolean(expr)` MUST emit `CASE WHEN LOWER(expr) IN ('true','1','yes','y') THEN 1 ELSE 0 END` (case-insensitive, matches Neo4j behavior).
- **FR-005**: `COUNT(DISTINCT expr)` MUST emit `COUNT(DISTINCT ...)` in SQL.

## Success Criteria *(mandatory)*

- **SC-001**: All 4 coercion functions return correct results against live IRIS.
- **SC-002**: `COUNT(DISTINCT)` returns deduplicated counts matching direct SQL verification.
- **SC-003**: Zero regressions on existing Cypher tests (currently 230 unit tests passing).
- **SC-004**: At least 6 unit tests + 2 e2e tests.

## Scope Boundaries

**In scope**: `toInteger`, `toFloat`, `toString`, `toBoolean` CAST fixes; `COUNT(DISTINCT)` verification + tests.
**Out of scope**: `split()` → TVF mapping (deferred), new functions not in `_CYPHER_FN_MAP`.
