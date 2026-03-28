# Research: NICHE Knowledge Graph Integer Index

**Feature**: 028-nkg-integer-index | **Date**: 2026-03-28

## R1: Where to Place InternNode/InternLabel

**Decision**: Add to `Graph.KG.GraphIndex` — the existing functional index class.

**Rationale**: `InsertIndex` already lives there and is the primary consumer. No new class means no new CPF mapping, no new compile dependency. The arno integration request suggested either `GraphIndex` or a new `NKGIndex` class — `GraphIndex` is simpler.

**Alternatives**: New `Graph.KG.NKGIndex` class — rejected because it adds a compile dependency and requires all callers to know about a second class.

## R2: Structural Label Pre-Population

**Decision**: Pre-populate labels 0=out, 1=in, 2=deg in `InternLabel` when the `^NKG("$LI")` subtree is empty (first call).

**Rationale**: These three labels are always needed. Lazy initialization on first `InternLabel("out")` call works, but pre-populating ensures the encoding is deterministic: out=-1, in=-2, deg=-3 regardless of call order.

**Implementation**: At the start of `InternLabel`, check `$Data(^NKG("$LS", 0))`. If empty, initialize all three.

## R3: BuildKG ^NKG Pass — Kill-and-Rebuild vs Incremental

**Decision**: Kill `^NKG` and rebuild from `^KG` in a second pass after the existing `BuildKG()` logic.

**Rationale**: `BuildKG()` already does `Kill ^KG` at the top. The `^NKG` pass should follow the same pattern — clean state, no stale data. This also provides the recovery path: if `^NKG` is corrupted, re-run `BuildKG()`.

## R4: Locking Strategy

**Decision**: Fine-grained `Lock +^NKG("$NI", id)` with immediate timeout (`Lock ... :0`) for fast-path, 5-second retry for contention.

**Rationale**: Matches the arno integration request's proposed pattern. Lock is held for microseconds (two global SETs). At 10K concurrent inserts, contention is negligible because different node IDs lock different subscripts.

## R5: PurgeIndex Update

**Decision**: `PurgeIndex` kills both `^KG` and `^NKG`.

**Rationale**: The functional index owns both globals. If one is purged, the other must be too for consistency.
