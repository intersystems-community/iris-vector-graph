<!-- markdownlint-disable MD013 -->

# ADR-0010 — Degree is counted from the Adjacency Index, never stored

**Status**: Accepted (design decision, implementation pending)
**Date**: 2026-10-12
**Evidence**: `docs/research/spikes/functional-index-adjacency/RISKS.md` (R1, R2) and `DEG-AUDIT.md`
**Relates to**: ADR-0008

## Decision

`^KG("deg")` and `^KG("degp")` are removed. A node's degree, or its degree for one predicate, is counted from its
`out` entries when asked, through two helpers (`Sources(graph)` and `Degree(graph, node[, predicate])`) that every
reader uses. In a multigraph a parallel edge counts through its leaf multiplicity. The two-hop statistics
(`deg2p`, `deg2p_exact`) stay, as Rebuilt stores computed during the rebuild.

## Context

A counter kept with `$Increment` inside a transaction is not undone by a rollback: a rolled-back insert left
`deg=1, degp=1` behind, and a rolled-back delete leaves it too low, so a counter that reaches zero has its key killed
while edges remain. Readers that enumerate `deg` or test `$Data(degp)` then miss real nodes and paths silently. Keeping
the counter exact with a lock held to commit deadlocked under overlapping writers. The audit of every reader (28
sites) found none that needs a stored value: ten enumerate sources, two prune by predicate, fourteen want the number,
six check or bookkeep.

## Consequences

- About 25 edit sites in 11 ObjectScript classes, the Python lazy-KG module, and about 20 test files.
- Counting costs 27 microseconds for a 200-entry node and 12.9 ms for a 100,000-entry hub, against 0.06 microseconds
  for a stored read. The loop readers pay it once over all edges; the public `degree()` of a single hub is O(degree).
- `GraphVerify` no longer reconciles a counter; the snapshot stops exporting the stores.
