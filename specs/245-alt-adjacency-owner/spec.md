<!-- markdownlint-disable MD013 MD060 -->

# Feature Specification: One writer for the adjacency stores

**Feature Branch**: `alt/adjacency-owner`
**Created**: 2026-10-12
**Status**: Draft (exploratory, not adopted)
**Input**: Give `^KG("out")` and `^KG("in")` one writer module and route the writers that bypass it through it (RISKS R3, ADR-0008).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every out/in write goes through one module (Priority: P1)

A maintainer reading `^KG("out")` or `^KG("in")` must find exactly one class that sets or kills them, so the
per-edge invariant (both mirrors, the graph key, the counters) is stated once.

**Why this priority**: Two writers that disagree is the defect class that produced the `deg`/`degp` drift and the
hardcoded graph key 0 (see the ownership tests). Everything else in the alternative depends on this.

**Independent Test**: A source scan over `iris_src/src/**/*.cls` finds Set/Kill/Merge/`$Increment` of `^KG("out"` or
`^KG("in"` only in `Graph.KG.Adjacency` and an allow-listed set of files, each with a reason.

**Acceptance Scenarios**:

1. **Given** the tree after this change, **When** the scan runs, **Then** `Adjacency.cls` is the only non-allow-listed writer.
2. **Given** `EdgeScan.WriteAdjacency` and `DeleteAdjacency`, **When** they are called, **Then** they delegate to
   `Adjacency.Put` and `Adjacency.Remove` with the same arguments and produce the same stores.
3. **Given** `LedgerApply`, `Loader` and `BenchFormat`, **When** they write an edge, **Then** they call `Adjacency`
   and no longer write `out`/`in` (or the counters they duplicated) themselves.

---

### User Story 2 - Adjacency layout is unchanged for existing callers (Priority: P1)

Existing callers (Python `create_edge`/`delete_edge`, `TemporalIndex`, `Eraser`, `FHIRGraph`) keep calling
`EdgeScan` by name and get byte-identical `out`/`in`/`deg`/`degp` for the same input.

**Why this priority**: The layout of record (ADR-0001) is a storage contract; a silent change corrupts every reader.

**Independent Test**: `tests/e2e/test_adjacency_owner_live.py` writes the same edges through the ledger, the loader and
the EdgeScan path and compares the stores with the layout recorded before the change.

**Acceptance Scenarios**:

1. **Given** one edge written through `EdgeScan.WriteAdjacency`, **When** read back, **Then** `out`, `in`, `deg`, `degp`
   and `__version` match the pre-change values.
2. **Given** a ledger relationship that already has an `out` leaf, **When** its weight changes, **Then** only the weight
   changes and the degree counters do not move (no double count).
3. **Given** the Loader, **When** it loads a CSV, **Then** rows land under the default graph key `0` (the layout of
   record). This is the one intended change: the legacy no-shard layout was invisible to every reader.

### Edge Cases

- A ledger relationship whose `out` leaf exists but whose `in` mirror does not: `Reweight` sets only what exists.
- An explicit empty-string weight passed to `EdgeScan.WriteAdjacency`: previously stored 0; see Deviations in the plan.
- `TemporalIndex` structural shadow: stays in `out`/`in` via `PutShadow`; its own migration is out of scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `Graph.KG.Adjacency` MUST provide `Put`, `PutKeyed`, `Remove`, `RemoveKeyed` (each returning `%Status`),
  plus `PutShadow` and `Reweight`, and the private helpers the keyed variants need.
- **FR-002**: Only `Adjacency.cls` and the allow-listed files may Set, Kill, Merge or `$Increment` `^KG("out"` / `^KG("in"`.
- **FR-003**: `EdgeScan` adjacency primitives MUST be thin delegations with unchanged names and signatures.
- **FR-004**: `LedgerApply`, `Loader` and `BenchFormat` MUST route their adjacency writes through `Adjacency` and drop
  duplicate counter increments.
- **FR-005**: `Eraser.cls` MUST NOT be edited (another agent owns it).

### Key Entities

- **Adjacency**: the one module that writes the `out`/`in` mirror pair for one edge under a derived graph key.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The single-writer scan passes; it failed before the change.
- **SC-002**: Unit tests for the delegation and the counter ownership pass (or are updated with a stated reason).
- **SC-003**: The live test (run by the lead) shows identical layouts for ledger and EdgeScan paths, and the graph-0
  layout for the loader.
