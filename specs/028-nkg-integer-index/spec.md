# Feature Specification: NICHE Knowledge Graph Integer Index (^NKG)

**Feature Branch**: `028-nkg-integer-index`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: arno integration request: `arno/specs/034-niche-knowledge-graph/ivg-integration-request.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Populate ^NKG on Edge Insert (Priority: P1)

A developer inserts an edge via the standard IVG SQL path (`INSERT INTO Graph_KG.rdf_edges`). The functional index fires and populates both `^KG` (string-subscripted, backward compatible) and `^NKG` (integer-encoded, for arno acceleration). The integer encoding uses a master label set and node dictionary stored within `^NKG`.

**Why this priority**: This is the write path — the foundation that makes all arno graph algorithms work. Without it, `^NKG` never gets populated and arno has nothing to read.

**Independent Test**: Insert an edge via SQL, then verify `^NKG` contains the correct integer-subscripted entries with proper node dictionary and label set entries.

**Acceptance Scenarios**:

1. **Given** an empty graph, **When** a developer inserts edge (A, "binds", B), **Then** `^NKG` contains: out-edge entry `^NKG(-1, sIdx, -(pIdx+1), oIdx)`, in-edge entry `^NKG(-2, oIdx, -(pIdx+1), sIdx)`, degree entry `^NKG(-3, sIdx)`, and the node dictionary maps A→sIdx and B→oIdx.
2. **Given** an existing edge, **When** a second edge (A, "binds", C) is inserted, **Then** node A reuses the same integer index (no duplicate allocation), and the label "binds" reuses the same label index.
3. **Given** concurrent edge inserts for the same new node, **When** two inserts race, **Then** each node gets exactly one integer index (no duplicates, no gaps from lost races).

---

### User Story 2 - Batch Rebuild ^NKG from ^KG (Priority: P1)

A developer calls `BuildKG()` to rebuild the adjacency index from SQL tables. After the existing `^KG` population, a second pass encodes the entire graph into `^NKG` with fresh node dictionary and label set.

**Why this priority**: Existing deployments already have data in `^KG`. The batch rebuild is the migration path — it populates `^NKG` from the existing `^KG` data without requiring re-ingest of all edges.

**Independent Test**: Load a graph via SQL, call `BuildKG()`, then verify `^NKG` has correct node count, edge count, and that `ExportAdjacency()` (arno) produces matching results.

**Acceptance Scenarios**:

1. **Given** a graph with 10K nodes and 50K edges in `^KG`, **When** `BuildKG()` completes, **Then** `^NKG("$meta", "nodeCount")` equals the number of unique node IDs, and `^NKG("$meta", "edgeCount")` equals the edge count.
2. **Given** a freshly rebuilt `^NKG`, **When** queried with `$Order`, **Then** all integer subscripts follow the encoding rule: label index N stored as -(N+1), node indices as positive integers.
3. **Given** a `BuildKG()` invocation on a graph that already has `^NKG` data, **When** it runs, **Then** `^NKG` is cleanly replaced (killed then rebuilt), not appended to.

---

### User Story 3 - Delete and Update Index Entries (Priority: P2)

A developer deletes or updates an edge via IVG's DML path. The functional index removes or updates the corresponding `^NKG` entries using the existing node dictionary lookups, without orphaning integer indices.

**Why this priority**: Write operations beyond INSERT are less frequent but necessary for correctness. The arno version counter (`^NKG("$meta", "version")`) must increment on every mutation so arno's cache invalidation works.

**Independent Test**: Insert an edge, delete it, verify `^NKG` entries are removed and version counter incremented.

**Acceptance Scenarios**:

1. **Given** an edge (A, "binds", B) exists in `^NKG`, **When** the edge is deleted, **Then** both the out-edge and in-edge entries are killed, degree is decremented, and `^NKG("$meta", "version")` is incremented.
2. **Given** an edge with weight 1.0, **When** the edge qualifiers are updated to weight 0.5, **Then** the `^NKG` entry value changes to 0.5 and version is incremented.
3. **Given** a node that had all its edges deleted, **When** queried, **Then** the node's integer index still exists in the dictionary (no index reclamation — indices are monotonic and never reused).

---

### Edge Cases

- What happens when `InternNode` is called with an empty string? The system rejects it — empty node IDs are invalid in IVG.
- What happens when `^NKG("$meta", "nodeCount")` overflows? At 2^62 nodes (IRIS integer limit), this is not a practical concern. Document the theoretical limit.
- What happens when `^NKG` is corrupted but `^KG` is intact? `BuildKG()` kills and rebuilds `^NKG` from `^KG`, providing a recovery path.
- What happens when arno reads `^NKG` during a `BuildKG()` rebuild? The version counter increment at the end signals arno to invalidate its cache. Reads during rebuild may see partial data — arno must check version before and after.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an `InternNode(id)` classmethod that assigns a monotonic integer index per unique node string ID, stored in `^NKG("$ND", idx)` and `^NKG("$NI", id)`.
- **FR-002**: System MUST provide an `InternLabel(label)` classmethod that assigns a monotonic integer index per unique predicate/type label, stored in `^NKG("$LS", idx)` and `^NKG("$LI", label)`.
- **FR-003**: Both `InternNode` and `InternLabel` MUST use fine-grained locking to prevent duplicate index assignment under concurrent INSERT.
- **FR-004**: The functional index `InsertIndex` MUST write both `^KG` (backward compatible) and `^NKG` (integer-encoded) on every edge insert.
- **FR-005**: Integer encoding MUST follow the rule: label index N stored as subscript -(N+1), node indices as positive integers.
- **FR-006**: `BuildKG()` MUST include a batch pass that populates `^NKG` from `^KG` with fresh dictionaries.
- **FR-007**: `DeleteIndex` MUST remove `^NKG` entries and decrement degree counts.
- **FR-008**: `UpdateIndex` MUST update `^NKG` entry values (weights).
- **FR-009**: Every mutation (insert, delete, update) MUST increment `^NKG("$meta", "version")`.
- **FR-010**: The first three label set entries (0=out, 1=in, 2=deg) MUST be pre-populated as structural labels on first use.

### Key Entities

- **^NKG**: The integer-encoded adjacency global. Subscript structure: `^NKG(-(labelIdx+1), nodeIdx, -(predIdx+1), targetIdx) = weight`. Metadata in `^NKG("$meta", ...)`, dictionaries in `^NKG("$ND", ...)`, `^NKG("$NI", ...)`, `^NKG("$LS", ...)`, `^NKG("$LI", ...)`.
- **Node Dictionary**: Bidirectional mapping between string node IDs and monotonic integer indices.
- **Master Label Set**: Bidirectional mapping between string labels (predicates, structural names) and monotonic integer indices.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After `BuildKG()`, `^NKG("$meta", "nodeCount")` matches the count of unique nodes in the graph.
- **SC-002**: `InternNode` assigns the same index when called twice with the same node ID (idempotent).
- **SC-003**: Under concurrent edge inserts (10 simultaneous threads), no duplicate integer indices are assigned.
- **SC-004**: 100% of existing tests continue to pass — `^KG` backward compatibility is maintained.
- **SC-005**: `^NKG` population is covered by at least 6 unit tests and 3 e2e tests.

## Assumptions

- `^NKG` is a process-global (not process-private) that persists across IRIS restarts.
- The encoding rule (negative integers for labels, positive for nodes) has been validated on IRIS 2025.1+ by the arno team.
- Node integer indices are monotonic and never reclaimed (delete does not free indices).
- `^KG` continues to be written during the migration period. Removing `^KG` writes is a future decision after arno validation.
- The functional index class is `Graph.KG.GraphIndex` (existing) or a new `Graph.KG.NKGIndex` class. Decision: update the existing `GraphIndex` to write both globals.
- Structural labels (out=0, in=1, deg=2) are pre-populated on first `BuildKG()` or first `InternLabel` call.

## Scope Boundaries

**In scope**:
- `InternNode` and `InternLabel` classmethods in ObjectScript
- `InsertIndex` update to write `^NKG` alongside `^KG`
- `BuildKG()` batch pass to populate `^NKG`
- `DeleteIndex` and `UpdateIndex` for `^NKG`
- Version counter increment on all mutations
- Unit and e2e tests

**Out of scope**:
- arno's `ExportAdjacency()` (arno team owns the read path)
- Removing `^KG` writes (future decision)
- Multi-graph support (future — if needed, add graph name prefix to `^NKG`)
- ASQ structural pruning (arno team)
- Python API changes (no Python-side changes needed — functional index is ObjectScript)

## Clarifications

### Session 2026-03-28

- Q: New class vs update existing? → A: Update existing `Graph.KG.GraphIndex` (or `Traversal.cls`) to write both globals. Simpler than a new class, no additional CPF mapping needed.
- Q: Structural labels pre-populated or lazy? → A: Pre-populated on first `BuildKG()` or `InternLabel("out")` call. The first three entries (0=out, 1=in, 2=deg) are always present.
- Q: BuildKG or InsertIndex for batch encoding? → A: Separate batch pass in `BuildKG()` (faster — avoids per-row interning overhead during bulk SQL scan). `InsertIndex` handles the incremental path.
