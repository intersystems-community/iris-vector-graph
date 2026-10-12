# Feature Specification: Temporal edges own a structural row (opt-in)

**Feature Branch**: `alt/temporal-structural-row`
**Status**: Exploratory (alternative implementation, not adopted)
**Evidence**: ADR-0009, `docs/research/spikes/functional-index-adjacency/RISKS.md` R3 and R5

## Problem

`create_edge_temporal` without `graph=` writes the temporal globals and a structural
shadow in `^KG("out"/"in")`, but no `rdf_edges` row. SQL, `edge_count`, and Cypher
`MATCH` cannot see those edges, and a table-driven adjacency index cannot reproduce them.

## User Scenarios and Testing

### User Story 1: Opt-in structural row for temporal edges (Priority: P1)

An engine constructed with `temporal_structural_rows=True` makes every successful
`create_edge_temporal` also own one `rdf_edges` row for its `(source, predicate, target)`
triple, in the requested graph, marked as temporal in `qualifiers`.

**Acceptance Scenarios**:

1. **Given** the flag is on and no row exists, **When** `create_edge_temporal("a","R","b",
   timestamp=t)` succeeds, **Then** exactly one `rdf_edges` INSERT runs, with
   `qualifiers` JSON `{"temporal": true}` and the graph passed through.
2. **Given** the flag is on and an `rdf_edges` row already exists for the triple, **When**
   a temporal edge is written, **Then** no INSERT runs and the existing row is not marked.
3. **Given** the flag is on and the ledger is strict, **Then** no structural row is written
   (the ledger owns SQL writes in strict mode, as before).

### User Story 2: Default behaviour unchanged (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the default (flag off), **When** `create_edge_temporal` runs without `graph=`,
   **Then** no `rdf_edges` statement runs and `create_edge` is not called.
2. **Given** the default, **When** `graph=` is given, **Then** the existing unmarked mirror
   INSERT is the only `rdf_edges` write (unchanged).

### User Story 3: Delete removes only the row it created (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the flag is on and the row carries the temporal marker, **When** the last temporal
   edge of the triple is deleted, **Then** the row is deleted.
2. **Given** a surviving temporal edge for the triple, **Then** the row is kept.
3. **Given** the window read fails, **Then** the row is kept (conservative).
4. **Given** the row has no temporal marker (an explicit `create_edge`), **Then** it is not deleted.
5. **Given** the flag is off, **Then** delete issues no `rdf_edges` statement.

### User Story 4: Backfill existing temporal-only edges (Priority: P2)

`backfill_structural_rows(conn, ...)` inserts marked rows for temporal triples that have none.

**Acceptance Scenarios**:

1. Dry run performs zero writes; the `rdf_edges` row count is identical before and after.
2. A second run inserts nothing (idempotent).
3. Triples that already have a row are left unmarked and untouched.

### User Story 5: Live visibility (Priority: P2, e2e)

With the flag on, `edge_count` and Cypher `MATCH` see a temporal-only edge; with it off,
they do not. Run by the lead against IRIS (`tests/e2e/test_temporal_structural_row_live.py`).

## Requirements

- **FR-001**: The flag is an `IRISGraphEngine.__init__` keyword, default `False`.
- **FR-002**: The structural row is written through `create_edge` (no duplicated SQL).
- **FR-003**: The marker is `qualifiers["temporal"] = true`. No new columns.
- **FR-004**: Backfill is idempotent and its dry run is read-only.
- **FR-005**: No ObjectScript change (`iris_src` is owned by another agent).

## Out of scope

Clearing the marker on an explicit `create_edge` (ADR-0009 consequence) and reverting
marked rows when the flag is turned off. Both are listed as needs-from-the-lead.
