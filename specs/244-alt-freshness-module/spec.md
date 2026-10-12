<!-- markdownlint-disable MD013 -->

# Feature Specification: Freshness module for Rebuilt stores

**Feature Branch**: `alt/freshness-module` (exploratory; not adopted)
**Created**: 2026-10-12
**Status**: Draft
**Input**: Alternative-implementation work package `freshness-module` (docs/research/alt-impl/BRIEF.md). Governing
decisions: ADR-0008, ADR-0010; CONTEXT.md "Rebuilt store", "Graph-Version Token", "Drift".

## Context

Derived stores that are rebuilt rather than maintained on every write (`^NKG`, the two-hop statistics `deg2p`, the Arno
snapshot, cached algorithm results) decide whether they are current today with a per-process `_nkg_dirty` flag. The
flag is in-memory, is lost on restart, is not shared between processes, and is set and cleared by about a dozen sites.
The Graph-Version Token `^KG("__version")` already moves on every write that can change an answer. A Rebuilt store
that records the token it was built from can answer "is this current?" from persisted state alone.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask whether a stored Rebuilt store is current (Priority: P1)

A caller asks whether a named store was built from the live token. It is current only when it was stamped and the
stamp equals the live token.

**Why this priority**: It is the whole answer. Everything else builds on it.

**Independent Test**: Unit tests against an in-memory host, no IRIS.

**Acceptance Scenarios**:

1. **Given** a store that was never stamped, **When** `is_current` is asked, **Then** the answer is False.
2. **Given** a stamp taken at token T, **When** `is_current` is asked with the token still T, **Then** True.
3. **Given** a stamp at T, **When** the token moves to T+1, **Then** `is_current` is False and the stamp is still T.
4. **Given** a stale stamp, **When** the store is stamped again at T+1, **Then** `is_current` is True.
5. **Given** the token was never set, **When** asked, **Then** the token reads 0.

### User Story 2 - A rolled-back bump is safe (Priority: P1)

`$Increment` is not undone by a rollback, so the token can move with no content change. The store must read as stale,
never as current, until a rebuild stamps it.

**Independent Test**: Unit test that bumps the token past a stamp and further, with no re-stamp.

**Acceptance Scenarios**:

1. **Given** a stamp at T and a bump with no rebuild, **When** the bump is later rolled back, **Then** the store is not
   current, and stays not current under more bumps.

### User Story 3 - Find the stale stores in a list (Priority: P2)

A caller passes several store names and gets back the ones that need a rebuild, in the order given.

**Acceptance Scenarios**:

1. **Given** NKG current and deg2p stale, **When** `stale_stores(["deg2p", "NKG"])`, **Then** `["deg2p"]`.
2. **Given** an empty list, **Then** an empty list.

### User Story 4 - The same answer on a live IRIS host (Priority: P2)

On the enterprise container, through `host_for(conn)`, the token and stamps are read and written through the host
operations and persist across host objects.

**Independent Test**: `tests/e2e/test_freshness_live.py`, run by the lead against IRIS; not run in the worktree.

## Requirements

- **FR-001** The module MUST read and write only through the host operations `get`, `set`, `kill`, `is_defined`,
  `next_subscript`. It MUST NOT touch a native handle.
- **FR-002** `token()` MUST return `^KG("__version")` as an int, and 0 when unset.
- **FR-003** `stamp(store)` MUST write the token current at call time to `^IVG.BuiltFrom(store)`.
- **FR-004** `built_from(store)` MUST return None when no stamp exists, else the stamped int.
- **FR-005** `is_current(store)` MUST be False when unstamped, and True only when the stamp equals the token.
- **FR-006** `stale_stores(stores)` MUST return the non-current stores in input order.
- **FR-007** Module constants `NKG = "NKG"` and `DEG2P = "deg2p"`.
- **FR-008** No existing file changes. The engine wiring is a later decision and is listed in the report.

## Out of scope

Wiring into `engine.py`, the `_nkg_dirty` replacement, changing which write paths bump the token, and changing the
existing tests that set `_nkg_dirty`.

## Success Criteria

- The 16 unit tests in `tests/unit/test_freshness.py` pass.
- The e2e test in `tests/e2e/test_freshness_live.py` passes on the enterprise container (lead run).
