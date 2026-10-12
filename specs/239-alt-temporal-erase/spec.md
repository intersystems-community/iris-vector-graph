<!-- markdownlint-disable MD013 -->

# Feature Specification: Temporal globals have one writer

**Feature Branch**: `alt/temporal-erase` (exploratory, not for main)
**Created**: 2026-10-12
**Status**: Draft
**Input**: Exploratory alternative implementation, package temporal-erase. Make `Graph.KG.TemporalIndex` the only
writer of the temporal globals by removing their direct kills from `Graph.KG.Eraser`. Governed by ADR-0002 and
ADR-0004.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Erasing a graph removes its temporal edges and nothing else (Priority: P1)

An operator erases one named graph. Its temporal edges, temporal buckets and aggregates, and edge properties must go.
Another graph's temporal data must stay exactly as it was.

**Why this priority**: The Eraser reaches into `^KG("tout")` and friends itself, so the temporal layout is maintained
in two places. That is the defect this package removes.

**Independent Test**: Live test `tests/e2e/test_temporal_erase_owner.py::test_erase_graph_removes_only_its_temporal_edges`.

**Acceptance Scenarios**:

1. **Given** graph A and graph B each hold a temporal edge, **When** `erase_graph(A)` runs, **Then** A's temporal edge
   is no longer returned by `get_edges_in_window` and B's edge still is.
2. **Given** graph A has temporal aggregates, **When** `erase_graph(A)` runs, **Then** no `^KG("tagg", keyA)` or
   `^KG("bucket", keyA)` subtree remains.

### User Story 2 - Erase-all empties every temporal global through the owner (Priority: P1)

`erase_all` must leave no temporal data in any graph.

**Why this priority**: Same single-owner rule, unscoped form.

**Independent Test**: Live test `test_erase_all_empties_the_temporal_index`.

**Acceptance Scenarios**:

1. **Given** temporal edges in two graphs, **When** `erase_all()` runs, **Then** no temporal edge is returned for
   either graph and `^KG("tout")` holds no data.

### User Story 3 - Erasing a node removes its temporal edges at both ends (Priority: P2)

Erasing a node removes every temporal edge in that graph that starts or ends at the node, and nothing else.

**Why this priority**: The Eraser's per-node path never touched temporal data, so a deleted node's temporal edges
outlived it. ADR-0004 says erasure removes content from the temporal index.

**Independent Test**: Live test `test_erase_node_removes_its_temporal_edges_both_directions`.

**Acceptance Scenarios**:

1. **Given** node X is the source of one temporal edge and the target of another in graph A, and graph B has an edge
   between the same two nodes, **When** X is erased from A, **Then** both A edges are gone and B's edge remains.

### Edge Cases

- A temporal edge written with a suppressed reverse index has no `^KG("tin")` row; node erase must still remove its
  `tout` row.
- Removing a temporal edge must not decrement the structural degree counters a second time: the Eraser removes
  structural adjacency per node, and `TemporalIndex` must not repeat that.
- `^KG("labelset")` is content-addressed and append-only; it is not in ADR-0002's list and stays with the Eraser.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: No class other than `Graph.KG.TemporalIndex` may Set, Kill, Merge or `$Increment` a `^KG` subtree named
  `tout`, `tin`, `bucket`, `tagg` or `edgeprop` (ADR-0002). Enforced by `tests/unit/test_temporal_single_owner.py`.
- **FR-002**: `TemporalIndex.EraseGraph(pGraph) As %Status` removes the five temporal subtrees for one graph key.
- **FR-003**: `TemporalIndex.EraseAll() As %Status` removes the five temporal subtrees for every graph.
- **FR-004**: `TemporalIndex.EraseNodes(pGraph, ByRef pNodeIds) As %Status` removes each temporal edge in the graph
  whose source or target is in `pNodeIds`, keeping bucket counts and aggregates consistent. It does not touch
  structural adjacency.
- **FR-005**: `Graph.KG.Eraser` calls those three methods inside its own transaction and kills no temporal subtree
  itself. `^KG("labelset")` stays with the Eraser.
- **FR-006**: `TemporalIndex.DeleteTemporalEdge` keeps its behaviour; its row removal is shared with `EraseNodes`.

### Key Entities

- **Temporal subtrees**: `^KG("tout"|"tin"|"bucket"|"tagg"|"edgeprop", graphKey, ...)`, owned by TemporalIndex.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The source-scan guard reports zero writers outside TemporalIndex.cls, and its allow-list is empty.
- **SC-002**: The unit tests in `test_temporal_single_owner.py` pass. The live tests in
  `tests/e2e/test_temporal_erase_owner.py` are run by the lead, since the package author has no IRIS.
