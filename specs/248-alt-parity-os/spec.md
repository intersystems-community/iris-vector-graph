<!-- markdownlint-disable MD013 MD060 -->

# Feature Specification: Generation-validated degree cache (ObjectScript side)

**Feature Branch**: `alt/parity-os`
**Created**: 2026-10-12
**Status**: Draft (exploratory, not adopted)
**Input**: `docs/research/alt-impl/BRIEF-PARITY.md` (the contract), `docs/research/spikes/degree-cache/README.md`
(the evidence), `docs/research/alt-impl/PERF.md` (the slowdown to remove), and the lead's added scope: finish the
single-owner design of `Graph.KG.Adjacency`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A degree read is O(1) once warm and never wrong (Priority: P1)

A reader (`KHopCount`, PageRank, degree centrality, `Build2HopStats`, `BuildNKG`) asks for the out-degree of a node. It
gets the cached count when the node's generation has not moved, recounts when it has, and never gets a count that a
rolled-back write left behind.

**Why this priority**: PERF.md measured `KHopCount` 270x to 18,000x slower, degree centrality 2.7x, PageRank +16 %,
`Build2HopStats` +35 %, all from counting degree on every read.

**Independent Test**: `tests/unit/test_degree_cache_protocol.py` (reference model, no container) and
`tests/e2e/test_degree_cache_live.py` (two connections, a real rollback).

**Acceptance Scenarios**:

1. **Given** a node whose cache entry has the current generation, **When** `Adj.Degree` runs, **Then** it returns the
   cached count and walks nothing.
2. **Given** a writer that holds an open transaction containing a keyed adjacency write, **When** another connection
   reads `Adj.Degree` and the writer then rolls back, **Then** the next read equals a recount, and the entry was not
   stored from the dirty read.
3. **Given** a parallel edge (a triple with `"#"` multiplicity 3), **When** `Adj.Degree` runs, **Then** it counts 3.
4. **Given** `BuildKG` has run, **When** the first reader asks for any source, **Then** the cache entry is already there.

---

### User Story 2 - Every writer of out/in keeps the cache honest, through one module (Priority: P1)

`Graph.KG.Adjacency` is the only code that sets or kills `^KG("out")` and `^KG("in")`. Every method that adds or removes
an `out` entry takes the shared lock, bumps `^KG("gen", g, src)` and releases. The stored counters `^KG("deg")` and
`^KG("degp")` are no longer written anywhere in `iris_src`.

**Why this priority**: the cache is sound only if no writer bypasses the generation and the lock.

**Independent Test**: source scans in `tests/unit/test_adjacency_single_writer.py` (allow-list empty) and
`tests/unit/test_degree_cache_writers.py`.

**Acceptance Scenarios**:

1. **Given** the tree after this change, **When** the scan runs, **Then** nothing in `iris_src` writes `^KG("deg"` or
   `^KG("degp"`, and `Adjacency.cls` is the only writer of `out`/`in` apart from the documented bulk refill path, which
   itself calls `Adjacency`.
2. **Given** an erase of a graph, a source or all graphs, **When** it runs, **Then** `Adjacency.RemoveGraph`,
   `RemoveSource`/`RemoveNode` or `RemoveAll` kill the `out`/`in` entries and the `gen`/`degc` subtrees under the shared
   lock.
3. **Given** `Remove` of an edge in a named graph whose triple also exists in the default graph, **When** `^NKG` is
   built, **Then** the merged `^NKG` entry survives (it was killed by the old code).

---

### User Story 3 - The reconciler checks the cache, not a counter (Priority: P2)

`GraphVerify` compares each cache entry with a recount and `out`/`in` with the rows. `GraphStores` lists `gen` and
`degc` instead of `deg` and `degp`.

### Edge Cases

- A rolled-back write over-bumps the generation: harmless, the entry looks stale and is recounted.
- A reader inside the writer's own open transaction: the lock does not conflict with the same process (verified live), so
  its probe succeeds and it stores its own dirty count. The store is a Set inside that transaction and the rollback takes
  it back with the edge, so no `$TLevel` guard is needed (a guard would also keep every reader inside a transaction cold).
- A lock that cannot be taken in 5 seconds: the writer returns an error status and writes nothing.
- A mid-flight BuildKG failure: out/in and the cache roll back together; past the chunked commit the cache is cold
  (killed), never wrong.
- Erase kills `gen`, so a counter restarts at 0. A reader that stored after the kill is detected by the post-store
  generation check and its entry is killed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `Adj.Degree(pGraph, pSrc, pPred="")` follows the four reader steps of BRIEF-PARITY.md exactly, plus
  one post-store re-read of the generation (see the edge cases).
- **FR-002**: Every `Adjacency` method that adds or removes an `out` entry takes `Lock +^IVG.AdjW(g)#"S":5`, bumps
  `^KG("gen", g, src)`, and releases. `^KG("__version")` behaviour is unchanged (one bump per write).
- **FR-003**: Nothing in `iris_src` writes `^KG("deg"` or `^KG("degp"`. Only `Adjacency.RemoveSource`, `RemoveGraph` and
  `RemoveAll` kill them, as residue a database upgraded in place may still hold (an erase must not leave it).
- **FR-004**: `Adjacency` gains `RemoveSource`, `RemoveNode`, `RemoveGraph`, `RemoveAll`, `BeginRebuild`, `FillEdge`,
  `FillTriple`, `EndRebuild`, `WarmCache`. `Eraser`, `EdgeScan.BulkDeleteAdjacency` and `TraversalBuild` call them and no
  longer set or kill `out`/`in`.
- **FR-005**: `BuildKG` leaves the cache warm. `KHopCount` does not walk edges when the entry is current.
- **FR-006**: `Build2HopStats` and `BuildNKG` read degree from the warm cache.
- **FR-007**: `Adjacency.Remove` does not drop a merged `^NKG` entry that another graph still holds.
- **FR-008**: `GraphVerify` reconciles the cache; `GraphStores` lists `gen` and `degc`.
- **FR-009**: `EdgeScan.WriteAdjacencyShadow` is deleted (its only caller, `TemporalIndex`, calls `Adjacency.PutShadow`).
  The other `EdgeScan` wrappers stay because Python and tests call them by name.

### Key Entities

- **Generation** `^KG("gen", g, src)`: integer, bumped by `$Increment`, absent means 0.
- **Degree cache entry** `^KG("degc", g, src, key)`: `$ListBuild(gen, count)`; key is the predicate or `$Char(1)`.
- **Writer lock** `^IVG.AdjW(g)`: no data; shared for writers, exclusive no-wait probe for readers.

## Success Criteria *(mandatory)*

- **SC-001**: The reference-model test shows the protocol sound, and shows that dropping the probe or the
  unchanged-generation check fails a rollback scenario.
- **SC-002**: The source-scan tests pass; the single-writer allow-list is empty.
- **SC-003**: The live tests pass in `IVGPAROS` on `ivg-iris-enterprise` (rollback race, multiplicity, erase, warm
  rebuild, randomized sequence with rollbacks), and the classes compile clean there.
- **SC-004**: No timing is claimed. The lead runs the timed benchmarks.
