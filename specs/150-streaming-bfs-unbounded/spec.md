# Feature Specification: Streaming BFS for Unbounded Variable-Length Path Queries

**Feature Branch**: `150-streaming-bfs-unbounded`
**Created**: 2026-05-06
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Unbounded graph traversal never crashes (Priority: P1)

A developer runs `MATCH (n {node_id:$x})-[:KNOWS*1..3]->(m) RETURN m.node_id` against a high-degree
node in a large graph. Today this query silently crashes or returns empty results when the
result set exceeds ~93K edges. With this fix, the query completes and returns all results
regardless of result set size.

**Why this priority**: This is a correctness bug. Unbounded traversal is a core graph operation.
Silent data loss or crashes are unacceptable.

**Independent Test**: Seed a graph with a node that has 50K+ 2-hop neighbors. Run an unbounded
VL path query. Assert all results returned, no crash.

**Acceptance Scenarios**:

1. **Given** a node with 10K direct neighbors, **When** `MATCH (s)-[:R*1..2]->(n)` is run with no LIMIT, **Then** all reachable nodes are returned without error.
2. **Given** a bounded query `MATCH (s)-[:R*1..2]->(n) RETURN n LIMIT 1000`, **When** executed, **Then** results are returned via the fast single-call path (no paging overhead).
3. **Given** any unbounded VL path query, **When** the result set exceeds 50K results, **Then** the query completes and returns correct results (no crash, no silent truncation).

---

### User Story 2 — Bounded queries retain their current performance (Priority: P1)

A developer running `MATCH (s)-[:R*1..2]->(n) LIMIT 1000` should see no performance regression.
The streaming path has per-page round-trip overhead; bounded queries should not pay this cost.

**Why this priority**: Performance regression on the common case (LIMIT queries) is unacceptable.

**Independent Test**: Benchmark LIMIT 1000 before and after — p50 must not regress more than 20%.

**Acceptance Scenarios**:

1. **Given** a LIMIT query, **When** executed, **Then** latency is within 20% of the pre-fix baseline.
2. **Given** an unbounded query, **When** executed, **Then** it completes (even if slower than LIMIT variant).

---

### Edge Cases

- Unbounded query against a node with zero neighbors (empty result, no crash).
- Unbounded query against isolated node (no outgoing edges).
- Very deep traversal `[*1..10]` on a sparse graph.
- Cursor page boundary falls exactly at last result.
- `ReadBFSPage` called with `cursor_step=""` on first call (existing bug — must handle gracefully).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Unbounded VL path queries (no LIMIT clause, `max_results == 0`) MUST use the cursor-based streaming path — never call `ReadBFSResults` directly.
- **FR-002**: Bounded VL path queries (LIMIT clause present, `max_results > 0`) MUST continue using `ReadBFSResults` for the single-call fast path.
- **FR-003**: `ReadBFSPage` MUST return correct results when called with `cursorStep=""` on first invocation (cursor initialization must be robust to empty string input).
- **FR-004**: The streaming path MUST work correctly from the ObjectScript surface — `_execute_var_length_cypher` must not depend on Python-side accumulation that would break ObjectScript callers.
- **FR-005**: No existing tests may regress — all current VL path tests MUST continue to pass.
- **FR-006**: `_bfs_stream_pages` MUST be the canonical streaming implementation — no duplicate streaming logic.

### Key Entities

- **`_bfs_stream_pages(conn, tag, page_size)`**: Python generator that calls `ReadBFSPage` in a cursor loop. Already exists; becomes the mandatory path for unbounded queries.
- **`ReadBFSPage(tag, cursorStep, cursorO, pageSize)`**: ObjectScript cursor-based page reader. Bug fix: empty `cursorStep` must correctly initialize to first `$Order` position.
- **`ReadBFSResults(tag)`**: ObjectScript single-call reader. Retained for bounded (LIMIT) queries only.
- **`max_results`**: Integer passed from translator to engine. `0` means unbounded; `> 0` means LIMIT was specified.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A query returning 90K+ results completes without crash or error.
- **SC-002**: All results from an unbounded query are correct — count matches direct `^KG` scan.
- **SC-003**: LIMIT 1000 query latency within 20% of pre-fix baseline (no paging overhead for bounded queries).
- **SC-004**: All existing VL path e2e tests pass — zero regressions.
- **SC-005**: `ReadBFSPage` with empty cursor returns correct first page (no off-by-one, no missing results).

## Assumptions

- `BFSFastJsonSorted` is the primary BFS path; `BFSFastJsonChunked` is a fallback and is out of scope for this fix.
- LDBC SF10 data is available on the enterprise container (port 4972) for the 90K+ result test.
- `max_results == 0` reliably indicates "no LIMIT" — the translator always passes a non-zero value when LIMIT is present.
- The fix is in Python engine only — no ObjectScript changes required unless `ReadBFSPage` cursor bug is confirmed.
