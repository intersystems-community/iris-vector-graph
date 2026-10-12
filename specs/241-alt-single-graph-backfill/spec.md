<!-- markdownlint-disable MD013 -->

# Feature Specification: Single-graph backfill shortcut for the graph_id re-key

**Feature Branch**: `alt/single-graph-backfill` (exploratory, not adopted)
**Status**: Draft
**Input**: The 4.0.0 re-key places each rdf_labels / rdf_props row in its node's graph with one
UPDATE per table whose SET and WHERE each carry a correlated subquery over nodes. When nodes holds
one graph, a constant UPDATE places the same rows.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-graph install is backfilled with one statement per table (Priority: P1)

An install whose nodes all sit in one graph has every child row's graph decided. The re-key sends
one constant `UPDATE Graph_KG.<table> SET graph_id = '<graph>' WHERE graph_id IS NULL` per child
table instead of the correlated form.

**Why this priority**: it is the whole performance point; the correlated form is a scan of nodes
per child row.

**Independent Test**: unit test counts the UPDATE statements sent during rekey on a one-graph fake.

**Acceptance Scenarios**:

1. **Given** nodes holds exactly one non-NULL graph_id and every child row names a node in nodes,
   **When** the re-key backfills, **Then** exactly one UPDATE per child table is sent, it has no
   subquery, and every child row ends in that graph.
2. **Given** the one graph is the default graph `''`, **When** backfilled, **Then** the rows get `''`
   (not NULL) and the re-key completes.
3. **Given** the graph id contains a quote, **When** backfilled, **Then** the literal is escaped.

### User Story 2 - Anything the shortcut cannot prove falls back (Priority: P1)

**Acceptance Scenarios**:

1. **Given** nodes holds two graphs, **When** backfilled, **Then** the original correlated statement runs.
2. **Given** a child row names a node missing from nodes, **When** backfilled, **Then** the original
   correlated statement runs (the orphan stays NULL there, as before).
3. **Given** nodes is empty, or holds a NULL graph_id, **When** backfilled, **Then** the original
   statement runs.

### User Story 3 - The two paths leave the same install (Priority: P1)

**Acceptance Scenarios**:

1. **Given** each scenario in the unit fakes (named graph, default graph,
   resumed, duplicates, two graphs, orphan), **When** rekey runs with the
   shortcut and with it forced off, **Then** the rows, columns, constraints and
   node rows are identical.
2. **Given** the same legacy data on a live 3.2.0-shaped IRIS, **When** both
   paths run, **Then** the child table contents match (live test, run by the
   lead).

## Requirements *(mandatory)*

- **FR-001**: The shortcut applies only when nodes has exactly one distinct
  graph_id, which is not NULL, and no child row's s is missing from nodes.
- **FR-002**: The shortcut is applied inside `_rekey_children` in
  `iris_vector_graph/migrations/graph_scoped_embeddings.py`. The existing
  blockers check runs before it, unchanged.
- **FR-003**: Any other case runs the statement list's correlated UPDATE
  unchanged.

## Out of scope

- Editing `schema.py`. The statement list stays as it is; the shortcut
  recognises it.
- The rdf_edges and embedding backfills.
