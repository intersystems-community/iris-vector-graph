<!-- markdownlint-disable MD013 -->

# ADR-0012 — Degree is served from a generation-validated cache

**Status**: Accepted (design decision; spike done, implementation in progress on `alt/parity-os`)
**Date**: 2026-10-12
**Evidence**: `docs/research/spikes/degree-cache/README.md`, `docs/research/alt-impl/PERF.md`
**Refines**: ADR-0010 (degree is counted, never stored). **Depends on**: ADR-0011.

## Decision

ADR-0010 stands: no counter is maintained incrementally, and a degree is always equal to a count of the `out` entries. What
changes is how the count is served. A degree is answered from a cache entry `^KG("degc", graph, src, key)` holding
`$ListBuild(generation, count)` when its generation equals `^KG("gen", graph, src)`, and is recounted otherwise.
Writers (through `Adjacency`, ADR-0011) bump the source's generation with `$Increment` and hold a shared lock
`^IVG.AdjW(graph)`; a reader stores a recount only if an exclusive no-wait probe of that lock succeeded and the generation had not
moved while it counted. `BuildKG` leaves the cache warm. The cache is derived: `Kill` it and every answer is still right.

## Context

Counting every degree on every read made the readers slower than the baseline: `KHopCount` 270x to 18,000x, degree centrality
2.7x, `PageRankGlobalJson` +16 %, `Build2HopStats` +35 % (PERF.md). The spike measured the cache at 0.06 ms against 49.9 ms for the
`KHopCount` pattern, 28 ms against 93.6 ms for the all-sources loop, with writes level with today's cost. A rolled-back write
over-bumps a generation, which only invalidates; the one unsafe case, a reader caching an uncommitted edge that is then rolled
back, is what the shared lock and probe exist for (shown failing without them and passing with them). Shared locks do not
conflict with one another, so no writer waits for another: four writers in opposite orders finished in 0.1 s, where the earlier
exclusive-lock counter never finished.

## Consequences

- Every adjacency write must bump the generation and hold the shared lock; `Adjacency` is the only place that does, and the
  guard test makes any other writer an explicit, justified exception.
- A reader writes the cache, so a read-only replica counts instead of caching. Journal volume of cache fills is not yet measured.
- The first read of a source after a write recounts it; a rebuild leaves the cache warm.
- ADR-0010's consequence "counting costs 27 microseconds ... against 0.06" describes the cold path; the warm path is a cache lookup.
