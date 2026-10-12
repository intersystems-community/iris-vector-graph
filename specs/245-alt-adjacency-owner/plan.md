<!-- markdownlint-disable MD013 MD060 -->

# Implementation Plan: One writer for the adjacency stores

**Spec**: `specs/245-alt-adjacency-owner/spec.md` | **Branch**: `alt/adjacency-owner`

## Summary

Move the bodies of the EdgeScan adjacency primitives into a new `Graph.KG.Adjacency` class (the single writer of
`^KG("out")`/`("in")`), make the EdgeScan names thin delegations, and route LedgerApply, Loader and BenchFormat
through it. Direction chosen: the code moves into Adjacency (not the reverse). The single-writer rule is about where
`Set`/`Kill` appear; a delegation from Adjacency back into EdgeScan would leave the writes in EdgeScan.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2024.1+), Python 3.10+ for the unit tests.
**Testing**: pytest source-scan tests (unit, no container); live test in `tests/e2e/` (lead runs it).
**Constraints**: ObjectScript is uncompiled in this package (no container). Graph key is the first subscript
(ADR-0001); default graph key is `0`; `$$$OK` first and `Quit tSC` last; parenthesised compound conditions.

## Constitution Check

- Test first: the scan and delegation tests are written and seen failing before any `.cls` edit.
- No container: nothing here connects to IRIS; the live test is written and not run.
- Eraser.cls is not touched; its direct `Kill ^KG("out"/"in")` lines are allow-listed with a reason.

## Design

| Method                                                   | Source of body                                | Writes                                                          |
| -------------------------------------------------------- | --------------------------------------------- | --------------------------------------------------------------- |
| `Adjacency.Put(pGraph,pSrc,pPred,pDst,pWeight="")`       | `EdgeScan.WriteAdjacency`                     | out, in, deg, degp, `__version`, `^NKG` (as today)              |
| `Adjacency.PutShadow(pGraph,pSrc,pPred,pDst,pWeight="")` | `EdgeScan.WriteAdjacencyShadow`               | out, in; deg, degp, `__version` only when the edge is new       |
| `Adjacency.PutKeyed(pGraph,pSrc,pPred,pDst,pEkey,pWeight="")` | `EdgeScan.WriteAdjacencyKeyed`           | as Put, plus ekey children                                      |
| `Adjacency.Remove(pGraph,pSrc,pPred,pDst)`               | `EdgeScan.DeleteAdjacency`                    | kills out/in, decrements deg/degp, `__version`, `GraphIndex.DeleteIndex` |
| `Adjacency.RemoveKeyed(pGraph,pSrc,pPred,pDst,pEkey)`    | `EdgeScan.DeleteAdjacencyKeyed`               | as Remove, plus ekey children                                   |
| `Adjacency.Reweight(pGraph,pSrc,pPred,pDst,pWeight)`     | new, from `LedgerApply.PersistQuals`          | sets out/in weight where present, `__version`; no counters      |
| private `SetLeafFromLowest`, `OtherEdgeKey`              | moved from EdgeScan                           | as today                                                        |

`EdgeScan` keeps `WriteAdjacency`, `WriteAdjacencyShadow`, `WriteAdjacencyKeyed`, `DeleteAdjacency`,
`DeleteAdjacencyKeyed` with their existing signatures, each a one-line `Do ##class(Graph.KG.Adjacency).X(...)`.
Python callers, TemporalIndex, Eraser and FHIRGraph are untouched.

Scan allow-list (each entry gets a reason in the test): `Eraser.cls` (another agent's file; deletes adjacency directly),
`TraversalBuild.cls` (rebuild from SQL; kills and refills the whole store). `TemporalIndex.cls` is allow-listed only if it
literally writes out/in; its shadow goes through `EdgeScan.WriteAdjacencyShadow`, so the expected answer is that it does not.

Routing:

- `LedgerApply.OpCreateRel`: existing-triple branch calls `Reweight` (was: direct Set of out/in plus a version bump);
  new-triple branch calls `Put` (was `EdgeScan.WriteAdjacency`). `PersistQuals`: `Reweight`. `DeleteRel`: `Remove`.
- `Loader.Load`: `Put(0, s, p, o, 1.0)` replaces the two Sets and the deg/degp increments (Put does them). Header
  comment updated: it now writes the layout of record.
- `BenchFormat.Seed`: `Put(0, s, "REL", d, 1)` replaces the out/in Sets and the explicit `deg` Set. The walkers in
  `BenchFormat` read graph key `0` too, so the bench reads what it writes.

## Project Structure

```text
iris_src/src/Graph/KG/Adjacency.cls                  new, the single writer
iris_src/src/Graph/KG/EdgeScan.cls                   primitives become delegations
iris_src/src/Graph/KG/LedgerApply.cls                routed writes
iris_src/src/Graph/KG/Loader.cls                     routed writes
iris_src/src/Graph/KG/BenchFormat.cls                routed writes, graph key 0
tests/unit/test_adjacency_single_writer.py           new
tests/unit/test_adjacency_counter_ownership.py       repointed to Adjacency bodies
tests/e2e/test_adjacency_owner_live.py               new, needs IRIS
```

## Deviations and risks

- `pWeight=""` maps to `"1.0"` in `Put`/`PutShadow`/`PutKeyed`/`Reweight`. Before, an explicit `""` to
  `WriteAdjacency` stored `0`. Grep finds no caller in `iris_src` or Python that passes `""`.
- `Reweight` checks each store with `$Data` (PersistQuals' form). The old ledger branch set `in` unconditionally when
  `out` existed; the two differ only for an `out` without an `in`, which is already inconsistent.
- The Loader changes layout (no-shard to graph 0). Intended; the live test asserts it.
- `Put`/`Remove` return `$$$OK` and do NOT catch exceptions (`PutKeyed`/`RemoveKeyed` keep their existing Try/Catch).
  Catching in `Put` would turn a failure into an ignored status.
- ADR-0001 allows direct `^KG` access only in TemporalIndex and EdgeScan; Adjacency is now a third. Needs an ADR
  amendment from the lead.
- The scan cannot see indirect writes (`^KG(pStore, ...)` in the TemporalIndex migration) or whole-tree `Kill ^KG`
  (Loader, Seed). Both are documented gaps.
