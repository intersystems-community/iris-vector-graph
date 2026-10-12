<!-- markdownlint-disable MD013 -->

# ADR-0011 — The Adjacency Index has one writer module

**Status**: Accepted (design decision; implemented on the alternative path, `alt/adjacency-owner`, live-validated)
**Date**: 2026-10-12
**Amends**: ADR-0001 (the clause "direct global access outside `TemporalIndex.cls` and `EdgeScan.cls` is forbidden")
**Relates to**: ADR-0008, ADR-0010, ADR-0012

## Decision

`Graph.KG.Adjacency` is the only class that sets or kills `^KG("out")` and `^KG("in")`, the Adjacency Index.
`Graph.KG.EdgeScan` no longer touches them: it keeps its non-adjacency primitives and calls `Adjacency` for the rest.
`TemporalIndex` keeps sole ownership of the temporal globals (ADR-0002) and reaches the structural shadow through
`Adjacency` too. Every writer that adds or removes an adjacency entry goes through one of its methods
(`Put`, `PutShadow`, `PutKeyed`, `Remove`, `RemoveKeyed`, `RemoveSource`, `RemoveGraph`, `RemoveAll`, `Reweight`, and the bulk
rebuild entry used by `BuildKG`). A source-scan guard test (`test_adjacency_single_writer.py`) fails on any other writer.

## Context

Ten classes wrote the derived `^KG` stores (RISKS R3 in the functional-index spike), each with its own copy of the layout,
the counters and the version bump. They drifted: `LedgerApply` wrote `out`/`in` itself after a SQL write and could double
count, `Loader` wrote the legacy no-graph layout, `Eraser` removed adjacency by hand. Any rule that has to hold on every
write (the version token, the degree generation of ADR-0012) was a rule ten places had to remember.

## Consequences

- A rule that must hold on every adjacency write is written once. ADR-0012 depends on this.
- ADR-0001's list of classes that may touch `^KG` directly gains `Adjacency` and loses `EdgeScan` for adjacency stores.
- `EdgeScan`'s adjacency methods become delegations or are removed; callers that named them keep working while any remain.
- Net code shrinks: the lifted bodies exist once, the duplicate write blocks in `LedgerApply`, `Loader` and `BenchFormat` go.
- The guard test is the enforcement; a new writer is an explicit edit to its allow-list with a reason.
