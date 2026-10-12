<!-- markdownlint-disable MD013 -->

# Feature Specification: Traversal readers stop reading the stored degree counters

**Feature Branch**: `alt/adj-traversal-readers` (exploratory, not adopted)
**Created**: 2026-10-12
**Status**: Draft
**Input**: Package adj-traversal-readers of the functional-index-adjacency alternative (docs/research/spikes/functional-index-adjacency/DEG-AUDIT.md, CONTEXT.md "Degree").

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Predicate pruning in BFS never drops a real path (Priority: P1)

A BFS asked for a predicate sequence must not skip a node that has an outgoing edge of the next predicate, even when a stored predicate counter has drifted low.

**Why this priority**: Results go missing silently; this is the dangerous direction of counter drift (DEG-AUDIT, section P).

**Independent Test**: Remove the only edge of predicate P from a node, then run a BFS with a predicate sequence where P is the next hop. The node must be pruned exactly when it has no P edge, computed from SQL rows.

**Acceptance Scenarios**:

1. **Given** node B has one outgoing P edge and node C has none, **When** BFS runs with predicates [Q, P] from A, **Then** the path through B is returned and C is not expanded on P.
2. **Given** the P edge of B was removed, **When** BFS runs again, **Then** B is pruned and the result equals the SQL-computed result.

### User Story 2 - Counts are computed from the out entries (Priority: P1)

`KHopCount`, the two-hop statistic `deg2p`, and the merged degree in the NKG snapshot (`^NKG(-3, s)`) return values equal to the number of outgoing edges counted from `^KG("out")`, not from stored counters.

**Why this priority**: Stored counters are not exact inside a transaction; no reader needs them stored (CONTEXT.md, Degree).

**Independent Test**: Build a small graph with several predicates, run each reader, compare with counts computed from SQL rows.

**Acceptance Scenarios**:

1. **Given** a source with 3 edges over 2 predicates, **When** `KHopCount(src)` and `KHopCount(src, P)` run, **Then** they return 3 and the P-only count from SQL.
2. **Given** a rebuild, **When** `^NKG(-3, s)` and `deg2p` are read, **Then** they equal the out-edge counts computed from SQL rows.

### User Story 3 - Sources are enumerated from out, and the dead ^KG fallback is gone (Priority: P2)

ArnoAccel iterates sources from `^KG("out")`. `Graph.KG.NKGAccelAdjacency` no longer has the `^KG` fallback of `ExportAdjacency`; the NKG path is kept.

**Why this priority**: Removes a reader of the counter; the fallback is dead code (DEG-AUDIT, section E).

**Independent Test**: Source scan plus the live Arno snapshot test (lead runs it).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `TraversalBFS` predicate pruning MUST use `'$Data(^KG("out", g, o, nextP))`.
- **FR-002**: `TraversalKHop.KHopCount` MUST drop the stored-counter shortcut and keep the counting branch.
- **FR-003**: `TraversalBuild` MUST compute `^NKG(-3, s)` and `deg2p` from `^KG("out")` during the rebuild, not from `deg`/`degp`.
- **FR-004**: `ArnoAccel` MUST enumerate sources through `Graph.KG.Adj.NextSource`.
- **FR-005**: `NKGAccelAdjacency.ExportAdjacency` MUST NOT read `^KG("deg")`; the fallback path is deleted.
- **FR-006**: Degree counts MUST add the `"#"` multiplicity leaf where present (multigraph), per the Adj.Degree contract.
- **FR-007**: No reader in the five classes may read `^KG("deg")` or `^KG("degp")`.

### Key Entities

- **Adjacency entry**: `^KG("out", graph, s, p, o)`; a multigraph triple also has `^KG("out", graph, s, p, o, "#")` holding its edge count.
- **Degree**: outgoing edges of a node, or of one predicate on it (CONTEXT.md).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The source scan test passes: no reader file reads `^KG("deg")` or `^KG("degp")`.
- **SC-002**: The live e2e test returns, for each reader, values equal to counts computed from SQL rows (lead runs against IRIS).
- **SC-003**: Unit tests in the worktree pass; no ObjectScript is compiled by this package.

## Out of scope

- Writers of `^KG("deg")` and `^KG("degp")` (EdgeScan, Loader, GraphIndex, BackfillDegp writes). They stay; the writer package removes them.
- Algorithms, Centrality, PageRank, Subgraph, lazy_kg.py, GraphVerify, snapshot.py (other packages).
- Class `Graph.KG.Adj` (another agent writes it).
- Multigraph semantics of `KHopCount`'s counting branch (see plan.md Risks).
