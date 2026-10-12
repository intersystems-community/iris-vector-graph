<!-- markdownlint-disable MD013 MD060 -->

# Implementation Plan: Generation-validated degree cache (ObjectScript side)

**Spec**: `specs/248-alt-parity-os/spec.md` | **Branch**: `alt/parity-os` (from `alt-integration`)

## Summary

Implement the contract in `docs/research/alt-impl/BRIEF-PARITY.md` for everything under `iris_src/`: `Adj.Degree` becomes
the generation-validated cache, `Adjacency` becomes the only writer of `out`/`in` and bumps the generation under the
shared lock, the stored counters stop being written, and the readers (`KHopCount`, `Build2HopStats`, `BuildNKG`,
PageRank, centrality) read the cache. The lead added: finish the single owner (erase and rebuild go through
`Adjacency`), and fix `Remove`'s `^NKG` handling.

## Technical Context

**Language**: ObjectScript (IRIS 2026.3 AI build in `ivg-iris-enterprise`), Python 3.10+ for tests.
**Container**: `ivg-iris-enterprise`, namespace `IVGPAROS` only (own database `/usr/irissys/mgr/ivgparos/`).
**Constraints**: parenthesise compound conditions; `$$$OK` first and `Quit tSC` last in status methods; graph key first
subscript; no timing runs; Python source belongs to parity-py.

## Constitution Check

- Test first: reference-model test, source-scan tests and the live test are written and seen failing before `.cls` edits.
- The IRIS container is used only in `IVGPAROS`; no database files deleted.

## Design

### Cache (Adj.cls)

`Degree`: read `gen`; entry current -> return count. Else probe `Lock +^IVG.AdjW(g)#"E":0`, release at once if got; count
(`"#"` multiplicity); store `$ListBuild(gen, n)` only when the probe was granted AND gen is unchanged; after the store
re-read gen and kill the entry if it moved (closes the window against an erase that killed `gen`, which restarts the
counter).

A lock never conflicts with its own process (checked live: a process holding `S` in a transaction gets `E` on the same
name), so a reader inside the writer's own open transaction stores its own dirty count. That store is a Set inside the
same transaction and rolls back with it, which the live test shows. A `$TLevel = 0` guard was considered and dropped: it
is unnecessary and would keep every reader inside a transaction cold.

`WarmFill(g, src)`: one walk of a source, stores the all-predicate entry and one per predicate, same protocol per source.
`WarmAll(g)` (the lead's request): `WarmFill` for every source of one graph, returns the sources filled; `WarmCache()`: every
graph. `Adjacency.EndRebuild`, `BackfillDegp` and snapshot restore call the same code.

### Writers (Adjacency.cls)

| Method | Lock + gen | Notes |
| --- | --- | --- |
| `Put` | yes | gen bump always (an overwrite over-bumps, harmless) |
| `PutShadow` | only when the edge is new | |
| `PutKeyed` | yes where the count changes | the first edge goes through `Put` |
| `Remove` | when the `out` entry existed | `^NKG` entry dropped only if no other graph holds the triple |
| `RemoveKeyed` | yes | |
| `Reweight` | no | degree unchanged |
| `RemoveSource(g, src)` | one lock, one gen kill | out entries of a source and their in mirrors; kills `gen`/`degc` of the source |
| `RemoveNode(g, node)` | | `RemoveSource` plus the inbound edges |
| `RemoveGraph(g)` | lock on the graph | kills out/in/gen/degc of the graph |
| `RemoveAll()` | lock on `^IVG.AdjW` (parent, covers every graph: checked live) | kills the four stores |
| `BeginRebuild` / `FillEdge` / `FillTriple` / `EndRebuild(pWarm)` | shared lock on the parent across the rebuild | `EndRebuild` unlocks, then `WarmCache` |

Every lock is a `Lock +` with a 5 second timeout; on timeout the method returns an error status and writes nothing.
`^KG("__version")` bumps stay as they are.

### Callers

- `Eraser`: `EraseGraph` -> `RemoveGraph`; `EraseAll` -> `RemoveAll`; `EraseIdSet`/`EraseEdgeRow`/`EraseNodes` -> `Remove`,
  `RemoveNode`. The `deg`/`degp` kills go.
- `EdgeScan`: `BulkDeleteAdjacency` -> `RemoveNode` per graph; `WriteAdjacencyShadow` deleted; the wrappers Python and tests
  call (`WriteAdjacency`, `DeleteAdjacency`, `DeleteAdjacencyAllGraphs`, `WriteAdjacencyKeyed`, `DeleteAdjacencyKeyed`,
  `BulkDeleteAdjacency`) stay. In-tree callers (`Eraser`, `FHIRGraph`, `TemporalIndex`) call `Adjacency` directly.
- `TraversalBuild.BuildKG`: `BeginRebuild`, `RemoveAll`, `FillEdge`/`FillTriple`, `EndRebuild`; `WriteKeyedTriple` moves into
  `Adjacency.FillTriple`. `Build2HopStats` reads the warm cache inline (a class call per edge would add 0.3 us to 500k
  edges); `BuildNKG` calls `Adj.Degree` once per source.
- `TraversalKHop.KHopCount` returns `Adj.Degree(0, src, pred)`.
- `GraphVerify` reconciles the cache; `GraphStores` lists `gen` and `degc`.

### Tests changed on purpose

See the commit messages; each test that pinned a stored counter says why it changed.

## Risks

- Everything runs only in `IVGPAROS`; the full integration suite is not run by this package.
- The cache and `gen` are not journaled differently from `^KG`; journal volume is not measured.

## Deviations from the brief

- No `$TLevel` guard (considered, dropped; see Design). One addition instead: the reader re-reads `gen` after its store and
  kills the entry if it moved.
- The warm fill lives in `Graph.KG.Adj` (`WarmFill`, `WarmAll`, `WarmCache`), not in `Adjacency`, so that `degc` has one
  setter; `Adjacency.EndRebuild` calls it. The lead asked for `WarmAll` by name; it is there.
- `EdgeScan` keeps `WriteAdjacency`, `DeleteAdjacency`, `DeleteAdjacencyAllGraphs`, `BulkDeleteAdjacency` (Python calls them)
  and `WriteAdjacencyKeyed`/`DeleteAdjacencyKeyed` (about a dozen tests call them by name). `WriteAdjacencyShadow` is deleted.
- `Adjacency.Remove` and `RemoveSource` call `GraphIndex.DeleteIndex` only when no other graph holds the triple. The first
  argument of `DeleteIndex` is unused (`^NKG` has no graph dimension), so the defect was not a hardcoded graph key but a merged
  entry dropped while another graph still held the triple.
