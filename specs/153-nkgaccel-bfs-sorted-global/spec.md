# Feature Specification: NKGAccel BFS Unified Output via Sorted Global

**Feature Branch**: `153-nkgaccel-bfs-sorted-global`
**Created**: 2026-05-07
**Status**: Draft (revised after council review)

## Background

IVG has two BFS paths:

1. **Rust-accelerated** (`NKGAccel.BFSJson`): Uses arno's `kg_bfs_compute`, assembles all result chunks from `^ArnoKG("bfs_result", N)` into one JSON string, and returns it directly. The engine receives and parses this JSON string in Python — no `<MAXSTRING>` risk here since Python handles multi-MB strings fine.

2. **ObjectScript fallback** (`BFSFastJsonSorted`): Writes to `^ArnoKG("bfs_r", tag, step, o)` sorted by depth, returns `"SORTED:tag"`. The engine reads via `ReadBFSResults` (bounded) or streams via `ReadBFSPage` (unbounded, spec 150).

**The actual problem is not `<MAXSTRING>`.** It is **code path divergence**:

- The Rust path returns raw assembled JSON → Python parses it → no streaming possible
- The ObjectScript path returns `"SORTED:tag"` → engine reads via `ReadBFSResults`/`_bfs_stream_pages` → streaming possible for unbounded queries

When the Rust path produces a 50K+ result BFS, the assembled JSON can be several MB. Python handles this, but there is no way to stream it. Meanwhile the ObjectScript fallback supports streaming for the same query via `ReadBFSPage`. The Rust path is also slower for large result sets because Python must deserialize the entire result at once.

Unifying the output format eliminates the divergence: both paths return `"SORTED:tag"`, the engine handles streaming/non-streaming identically, and the `"CHUNKED:"` cleanup branch (in the `BFSFastJsonChunked` legacy fallback) can be removed.

**Note on `BFSFastJsonChunked`**: The `"CHUNKED:"` branch at `engine.py:1575` is NOT from the Rust path — it is from `BFSFastJsonChunked`, the old ObjectScript chunked fallback (superseded by `BFSFastJsonSorted`). FR-006 removes this stale branch.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Unbounded Rust-accelerated BFS supports streaming (Priority: P1)

A developer runs a large graph traversal with the Rust BFS path active. Today, unbounded queries via the Rust path cannot stream — all results must be assembled and parsed at once. After this fix, the Rust path outputs `"SORTED:tag"` and the engine's existing `_bfs_stream_pages` handles it identically to the ObjectScript path.

**Independent Test**: With arno Rust BFS active, run an unbounded VL path query. Assert result count matches ObjectScript fallback. Measure that large results (>10K) are not fully assembled in Python before processing.

**Acceptance Scenarios**:

1. **Given** arno Rust BFS returns results, **When** `execute_cypher('MATCH (s)-[:KNOWS*1..2]->(n) RETURN n.node_id')` runs, **Then** result count matches the ObjectScript fallback exactly.
2. **Given** a bounded query (`LIMIT 1000`), **When** Rust BFS path is active, **Then** results are returned via `ReadBFSResults` single-call path and latency is within 20% of the pre-fix baseline.
3. **Given** an unbounded query via Rust path, **When** result count exceeds 10K, **Then** the engine uses `_bfs_stream_pages` (same as ObjectScript path) rather than full-JSON assembly.

---

### User Story 2 — Single code path in the engine (Priority: P2)

Both BFS paths return `"SORTED:tag"` so the engine has one handling path. The stale `"CHUNKED:"` branch from `BFSFastJsonChunked` (superseded by `BFSFastJsonSorted`) is removed.

**Acceptance Scenarios**:

1. **Given** either Rust or ObjectScript BFS runs, **When** the engine receives the response, **Then** identical `ReadBFSResults`/`_bfs_stream_pages` logic handles it.
2. **Given** the old `"CHUNKED:"` branch at `engine.py:1575` (from `BFSFastJsonChunked`), **Then** it is removed — dead code.

---

### Edge Cases

- Rust BFS returns 0 results: returns `"SORTED:tag"` with empty global; `ReadBFSResults` returns `[]`.
- Rust BFS unavailable: falls back to `BFSFastJsonSorted` as before.
- Tag collision between concurrent requests: tag includes `$Job` to scope per IRIS process.
- ObjectScript conversion overhead: 15K results × `%DynamicObject.%Get()` + `$Set` — must benchmark before accepting (SC-002).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `NKGAccel.BFSJson` MUST convert assembled chunk JSON to `^ArnoKG("bfs_r", tag, step, o) = $LB(s, p, w)` and return `"SORTED:tag"`.
- **FR-002**: The engine MUST detect `"SORTED:"` prefix from the Rust path (line 1539 receives the return of `NKGAccel.BFSJson`) and route to `ReadBFSResults`/`_bfs_stream_pages` instead of `_json.loads()` directly.
- **FR-003**: The tag MUST be scoped per IRIS process (`$Job _ "_bfs"`) to avoid concurrent collisions.
- **FR-004**: `^ArnoKG("bfs_result", N)` global MUST be killed after chunk assembly, before sorted write.
- **FR-005**: The stale `"CHUNKED:"` branch at `engine.py:1575` (`BFSFastJsonChunked` path) MUST be removed.
- **FR-006**: `NKGAccel.BFSJson` with Rust unavailable MUST still fall back to `BFSFastJson` as before.
- **FR-007**: All existing VL path e2e tests MUST pass with no regressions.
- **FR-008**: Bounded Rust BFS (`LIMIT N`) MUST complete within 20% of pre-fix latency baseline.

### Key Entities

- **`^ArnoKG("bfs_r", tag, step, o)`**: Sorted BFS result global. Used by both paths after this change.
- **`ReadBFSResults(tag)`**: Single-call reader for bounded results. Unchanged.
- **`ReadBFSPage(tag, cursor, pageSize)`**: Streaming reader for unbounded results. Unchanged.
- **Tag**: `$Job _ "_bfs"` — unique per IRIS process, sequential calls overwrite (synchronous).

## Success Criteria *(mandatory)*

- **SC-001**: Unbounded Rust BFS result count matches ObjectScript fallback for the same seed/hops.
- **SC-002**: Bounded Rust BFS (`LIMIT 1000`) latency within 20% of pre-fix baseline — benchmark BEFORE and AFTER T010 to catch ObjectScript conversion overhead.
- **SC-003**: `"CHUNKED:"` branch removed from engine — verified by grep.
- **SC-004**: All existing VL path e2e tests pass — zero regressions.
- **SC-005**: Engine routes Rust BFS response through `ReadBFSResults`/`_bfs_stream_pages` (not direct `_json.loads`).

## Assumptions

- The sorted global write uses `^ArnoKG("bfs_r", tag, step, o) = $LB(s, p, w)` — `step` = BFS depth (integer), `o` = destination node string.
- The assembled chunk JSON from `kg_bfs_read_chunk` is a flat JSON array of `{s, p, o, step, w}` objects — suitable for `%DynamicArray.%FromJSON()`.
- ObjectScript conversion (FR-001) for 15K results: ~15K `%DynamicObject.%Get()` + 15K `$Set` ≈ several seconds. **This must be benchmarked in T010a before proceeding to engine changes.** If unacceptably slow, an alternative (e.g. return sorted JSON directly from a new Rust function) will be scoped separately.
- No arno Rust changes required for the ObjectScript conversion path. If benchmarking shows it is too slow, a Rust alternative will be a separate spec.
- `BFSFastJsonChunked` at `engine.py:1575` is dead code post-`BFSFastJsonSorted` (spec 102) — safe to remove.


**Feature Branch**: `153-nkgaccel-bfs-sorted-global`
**Created**: 2026-05-07
**Status**: Draft

## Background

IVG has two BFS paths:

1. **Rust-accelerated** (`NKGAccel.BFSJson`): Uses arno's `kg_bfs_compute`, writes chunks to `^ArnoKG("bfs_result", N)`, returns `"CHUNKED:N"`. The engine assembles all N chunks into one string, then parses — no streaming possible.

2. **ObjectScript fallback** (`BFSFastJsonSorted`): Writes to `^ArnoKG("bfs_r", tag, step, o)` sorted by depth, returns `"SORTED:tag"`. The engine can read all results at once (`ReadBFSResults`) or stream page-by-page (`ReadBFSPage`) for unbounded queries (spec 150 fix).

The `"CHUNKED:N"` path from the Rust BFS cannot stream. For large result sets via the Rust path, the engine must assemble the full JSON string in memory — the same `<MAXSTRING>` risk that motivated spec 150 for the ObjectScript path.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Unbounded Rust-accelerated BFS never crashes (Priority: P1)

A developer runs a large graph traversal against a LDBC-scale node with the Rust BFS path active. With a 15K-result BFS, the current chunk assembly may produce a multi-MB string. With a 90K+ result BFS, it risks `<MAXSTRING>`. After this fix, the Rust path returns `"SORTED:tag"` and the engine's streaming path handles it identically to the ObjectScript path — no memory limit.

**Independent Test**: With arno Rust BFS active, run an unbounded VL path query on a high-degree node. Assert all results returned, no crash, same count as ObjectScript fallback.

**Acceptance Scenarios**:

1. **Given** arno Rust BFS returns results, **When** `execute_cypher('MATCH (s)-[:KNOWS*1..2]->(n) RETURN n.node_id')` runs on a high-degree seed, **Then** result count matches the ObjectScript fallback exactly.
2. **Given** the Rust BFS path, **When** result count exceeds 50K, **Then** no `<MAXSTRING>` error occurs and all results are returned.
3. **Given** a bounded query (`LIMIT 1000`), **When** Rust BFS path is active, **Then** results are returned via `ReadBFSResults` single-call path (no paging overhead).

---

### User Story 2 — Code path consistency (Priority: P2)

Both BFS paths (Rust and ObjectScript) return the same format — `"SORTED:tag"` — so the engine has one code path to maintain, not two. Currently the engine has separate handling for `"CHUNKED:N"` (Rust) and `"SORTED:tag"` (ObjectScript). After this change, the `"CHUNKED:N"` handling in `_execute_var_length_cypher` can be removed.

**Acceptance Scenarios**:

1. **Given** the engine receives a BFS response, **When** it starts with `"SORTED:"`, **Then** the same `ReadBFSResults`/`_bfs_stream_pages` logic handles it regardless of which path produced it.
2. **Given** the old `"CHUNKED:N"` handling path in the engine, **Then** it is removed or dead-code (only `"SORTED:tag"` handled).

---

### Edge Cases

- Rust BFS returns 0 results: `NKGAccel.BFSJson` should still return `"SORTED:tag"` (empty sorted global); `ReadBFSResults` returns `[]`.
- Rust BFS unavailable (DLL not loaded): falls back to `BFSFastJsonSorted` as before — no change.
- Tag collision between concurrent requests: tag includes process ID or timestamp to ensure uniqueness.
- `ReadBFSPage` cursor reaching end: handled by existing `ReadBFSPage` implementation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `NKGAccel.BFSJson` MUST write BFS results to `^ArnoKG("bfs_r", tag, step, o) = $LB(s, p, w)` — same format as `BFSFastJsonSorted`.
- **FR-002**: `NKGAccel.BFSJson` MUST return `"SORTED:tag"` instead of `"CHUNKED:N"`.
- **FR-003**: The tag MUST be unique per-call (include PID or timestamp to avoid collisions).
- **FR-004**: The old `^ArnoKG("bfs_result", N)` global MUST be killed before the new write (cleanup).
- **FR-005**: `_execute_var_length_cypher` in `engine.py` MUST handle `"SORTED:tag"` from the Rust path via the same `ReadBFSResults`/`_bfs_stream_pages` logic used for the ObjectScript path.
- **FR-006**: The `"CHUNKED:N"` handling branch in `_execute_var_length_cypher` MUST be removed once FR-002 is in place.
- **FR-007**: All existing VL path e2e tests MUST pass with no regressions — both Rust and ObjectScript paths.
- **FR-008**: `NKGAccel.BFSJson` with Rust unavailable MUST still fall back to `BFSFastJson`/`BFSFastJsonSorted` as before.

### Key Entities

- **`^ArnoKG("bfs_r", tag, step, o)`**: Sorted BFS result global. Same structure used by both paths after this change.
- **`ReadBFSResults(tag)`**: Single-call reader for bounded result sets. Unchanged.
- **`ReadBFSPage(tag, cursor, pageSize)`**: Streaming reader for unbounded result sets. Unchanged.
- **Tag**: Unique identifier per BFS call — currently `$Job` + counter suffix to avoid collision.

## Success Criteria *(mandatory)*

- **SC-001**: Unbounded Rust BFS on SF10 high-degree seed completes without `<MAXSTRING>` and returns correct count.
- **SC-002**: Bounded Rust BFS (`LIMIT 1000`) latency within 20% of pre-fix baseline.
- **SC-003**: `"CHUNKED:N"` branch removed from engine — single code path for both BFS outputs.
- **SC-004**: All existing VL path e2e tests pass — zero regressions on Rust and ObjectScript paths.
- **SC-005**: Rust BFS result count matches ObjectScript BFS result count for same seed/hops.

## Assumptions

- The sorted global write in `NKGAccel.BFSJson` uses the same `^ArnoKG("bfs_r", tag, step, o) = $LB(s,p,w)` structure — `step` = BFS depth level (integer), `o` = destination node string, value = `$ListBuild(source, predicate, weight)`.
- The arno Rust `kg_bfs_compute` returns chunks in `^ArnoKG("bfs_result", N)` as JSON. The conversion to the sorted global format happens in ObjectScript by parsing the assembled JSON and writing to `^ArnoKG("bfs_r", ...)`.
- Tag uniqueness: `$Job _ "_bfs"` is sufficient since BFS calls are synchronous per job.
- No arno Rust changes required — the change is entirely in `NKGAccel.cls` ObjectScript.
