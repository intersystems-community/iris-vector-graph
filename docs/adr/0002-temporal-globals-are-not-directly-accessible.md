# ADR-0002 — Temporal globals are accessed only through TemporalIndex.cls

**Status**: Accepted  
**Date**: 2026-09-11  
**Specs**: 223

## Decision

No ObjectScript class other than `Graph.KG.TemporalIndex` (and its subclass
`Graph.KG.TemporalIndexMS`) may read or write `^KG("tout")`, `^KG("tin")`,
`^KG("bucket")`, `^KG("tagg")`, or `^KG("edgeprop")` directly.

`TemporalIndex` is the seam. All callers go through its classmethods.

## Context

The global subscript layout for temporal data changed in spec-223 (graph key added as
first subscript). When code outside `TemporalIndex` accessed these globals directly, it
had to be updated every time the layout changed — and sometimes wasn't, causing silent
bugs (e.g. `BenchSeeder.cls` was still writing the pre-spec-214 structural layout after
spec-214 shipped).

Concentrating all temporal global access inside `TemporalIndex` gives:

- **Locality**: layout changes touch one file.
- **Testability**: tests go through the same classmethod interface as production code.
- **Auditability**: grepping for `^KG("tout"` outside `TemporalIndex.cls` is a lint rule.

## Consequences

- `BenchSeeder.cls` was deleted (it bypassed the seam).
- `TemporalIndexMS.GetBucketCount` was updated to call `TemporalIndex` rather than
  reading `^KG("bucket")` directly.
- Future callers that need temporal access must use `TemporalIndex` classmethods.
- Cross-graph admin operations (e.g. enumerate all graphs with temporal data) must be
  added as new classmethods on `TemporalIndex`, not as ad-hoc global scans.
