<!-- markdownlint-disable MD013 -->

# Feature Specification: Derived-store oracle (walks the stores, not the rows)

**Feature Branch**: `alt/derived-oracle-harness`
**Status**: Exploratory (alternative implementation, not adopted)
**Input**: RISKS.md R9 in `docs/research/spikes/functional-index-adjacency/`. A verification that recounts from the
rows and compares per source never visits a source whose rows are all gone, so a ghost counter reads as "0 mismatches".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find store keys that have no row behind them (Priority: P1)

A maintainer runs the oracle over one graph and gets a typed list of disagreements. Keys in `^KG("out")`, `^KG("in")`,
`^KG("deg")` and `^KG("degp")` that have no backing row are reported, not skipped.

**Acceptance Scenarios**:

1. **Given** a consistent state, **When** the oracle runs, **Then** it returns no violations.
2. **Given** a `deg` counter for a source with zero remaining rows, **When** the oracle runs, **Then** it reports
   `ghost_deg` for that source.
3. **Given** an `out` or `in` entry with no row, **When** the oracle runs, **Then** it reports `ghost_out` or `ghost_in`.
4. **Given** a row with no `out` or `in` entry, **When** the oracle runs, **Then** it reports `missing_out` or `missing_in`.
5. **Given** an `out` entry with no `in` mirror, **When** the oracle runs, **Then** it reports `out_in_mismatch`.
6. **Given** a `deg` or `degp` value that differs from the row count, **When** the oracle runs, **Then** it reports
   `deg_mismatch` or `degp_mismatch`.

### User Story 2 - Random write sequences keep the adjacency exact (Priority: P2)

Under a seeded random sequence of inserts, deletes, rollbacks after insert, and failed statements in the middle of a
batch, the adjacency stores (`out`, `in`) never disagree with the rows. Advisory counters may drift, and only the
documented kinds are allowed.

**Acceptance Scenarios**:

1. **Given** any seeded sequence, **When** the oracle runs after every step, **Then** adjacency kinds are empty.
2. **Given** a strict run, **When** it runs, **Then** any `deg`/`degp` drift is reported (this is how today's ghost counters show).
3. **Given** a lenient run, **When** it runs, **Then** only `ghost_deg`, `deg_mismatch` and `degp_mismatch` are tolerated.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The oracle MUST read `^KG` by walking the stores with `next_subscript`/`get`, not by recounting rows.
- **FR-002**: The oracle MUST map a NULL, empty, or `$Char(0)` `graph_id` to graph key `0`.
- **FR-003**: `violations()` MUST return typed `Violation(kind, store, key)` objects with one of the eight kinds.
- **FR-004**: The host and connection MUST be injected; the oracle MUST NOT open a connection.
- **FR-005**: The e2e property test MUST take the allowed kind set as a parameter.

### Key Entities

- **Violation**: `kind`, `store` (`out`/`in`/`deg`/`degp`), `key` (the subscript tuple in that store's layout).
- **Walk**: the stores as read: sets of `(g, s, p, o)`, `(g, o, p, s)`, and counter maps.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The unit suite proves each of the eight kinds is detected, including the R9 ghost-counter case, and that a consistent state reports none.
- **SC-002**: The e2e property test runs strict (shows today's ghost counters) and lenient (passes) on a fixed seed set. The lead runs it against IRIS.
