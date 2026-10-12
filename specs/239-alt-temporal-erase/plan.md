<!-- markdownlint-disable MD013 -->

# Implementation Plan: Temporal globals have one writer

**Branch**: `alt/temporal-erase` | **Date**: 2026-10-12 | **Spec**: specs/239-alt-temporal-erase/spec.md

## Summary

Move every temporal-global kill out of `Graph.KG.Eraser` and into `Graph.KG.TemporalIndex`, which already owns the
layout (ADR-0002). Add three entry points: `EraseGraph`, `EraseAll`, and `EraseNodes` (per-node, both directions).
A source-scan guard makes the single-owner rule a test.

## Technical Context

**Language/Version**: Python 3.11 (`.venv`) for tests; ObjectScript on IRIS 2024.1+ for the classes
**Primary Dependencies**: pytest; `intersystems-irispython` for the live test only
**Storage**: `^KG` temporal subtrees (no SQL tables change)
**Testing**: pytest source-scan guards (unit, no IRIS); live pytest e2e under `tests/e2e/` marked `e2e` (not run by the package author)
**Target Platform**: IRIS namespace hosting `Graph.KG.*`
**Project Type**: single (ObjectScript classes plus Python tests)
**Performance Goals**: none; `EraseNodes` makes one walk of `^KG("tout", graph)` per erase batch
**Constraints**: Eraser transaction owns TSTART/TCOMMIT; TemporalIndex methods must not open their own transaction.
ObjectScript is uncompiled in this package.

## Constitution Check

Repo CLAUDE.md test-first rule: unit guard tests written before the classes change, and they were run to fail first.
The IRIS-backend gate in the plan template (named container, e2e phase, `SKIP_IRIS_TESTS`) is met by
`tests/e2e/test_temporal_erase_owner.py`, which follows `tests/e2e/test_temporal_store_e2e.py`. Its container
resolution is the existing `iris_connection` fixture; no port is hardcoded.

## Design

- `TemporalIndex.RemoveTemporalRow(gKey, ts, s, p, o)` (private): kills `tout`, `tin`, `edgeprop` for one row, decrements
  the source bucket counter, and rebuilds that bucket's aggregate. `DeleteTemporalEdge` calls it and then keeps its
  structural-adjacency step unchanged.
- `EraseNodes(pGraph, ByRef pNodeIds)`: walks `tout` once for the graph, collects rows whose source or target is in
  `pNodeIds`, then removes them with `RemoveTemporalRow`. Collect-then-delete, because the walk must not mutate the tree.
  It does not call `DeleteAdjacency`: the Eraser already removes structural adjacency per node, and a second call
  would decrement the degree counters twice.
- `Eraser.EraseIdSet` builds a local `tNodeIds` array from `^||IVG.EraseIds` and calls `EraseNodes` once, after the per-id
  loop, inside the same transaction.
- `Eraser.EraseGraph` and `Eraser.EraseAll` replace their kill lists with a call to `EraseGraph` and `EraseAll`,
  wrapped in `$$$ThrowOnError`, so a failure rolls back the whole erase.
- `TemporalIndex.Purge` calls `EraseAll` so the five-name kill list exists once.

## Project Structure

```text
iris_src/src/Graph/KG/TemporalIndex.cls      (owner; EraseGraph, EraseAll, EraseNodes, RemoveTemporalRow)
iris_src/src/Graph/KG/Eraser.cls             (no temporal kills; calls the owner)
tests/unit/test_temporal_single_owner.py     (source guard, runs without IRIS)
tests/unit/test_eraser_inventory_coverage.py (delegated stores are checked in TemporalIndex)
tests/e2e/test_temporal_erase_owner.py       (live; lead runs)
specs/239-alt-temporal-erase/                (this spec)
```
