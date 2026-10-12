<!-- markdownlint-disable MD013 MD060 -->

# Tasks: Generation-validated degree cache (ObjectScript side)

**Input**: `specs/248-alt-parity-os/plan.md`, `spec.md`. Test tasks come first in every phase.

## Phase 1: Tests first (must FAIL before Phase 2)

- [x] T001 `tests/unit/test_degree_cache_protocol.py`: dict-backed reference model (simulated lock state, rollback that
      restores Set/Kill but not `$Increment`), the four reader steps, and ablations (no probe, no unchanged check, probe
      before gen, no post-store check) each failing a rollback scenario; a same-process read inside a transaction rolls
      back with it; random interleavings (76 tests, green from the start: the model is the specification).
- [x] T002 `tests/unit/test_degree_cache_writers.py`: nothing in `iris_src` writes `^KG("deg"`/`("degp"`; only `Adj.cls` sets
      `degc` and only `Adj.cls`/`Adjacency.cls` kill it; only `Adjacency.cls` touches `gen`; every `Adjacency` method that
      sets or kills `out` takes the `S` lock and bumps or kills `gen` (explicit exceptions with a reason); lock/unlock
      balance per method; `__version` bump counts per method as before; `Adj.Degree` has the four steps in order.
      Failed first: 29 of 60.
- [x] T003 Tighten `tests/unit/test_adjacency_single_writer.py` to an empty allow-list; add the new `Adjacency` methods to
      its signature table; drop `WriteAdjacencyShadow` from the delegation list. Failed first: 9.
- [x] T004 Update, on purpose and with a stated reason, the tests that pin the old counters:
      `test_adjacency_counter_ownership.py`, `test_traversal_readers_no_stored_counter.py`, `test_graph_stores_inventory.py`,
      `test_eraser_inventory_coverage.py`, `test_engine_critical_fixes.py`, `test_adj_contract.py`, `test_234_multigraph_sql.py`
      (re-pinned hashes), and the live tests that assert `deg`/`degp` (`test_adjacency_owner_live`,
      `test_234_multigraph_storage_e2e`, and under `tests/integration`: delete_adjacency, erase_graph, buildkg_atomicity,
      delete_nodes_graph_scoped, delete_temporal_edge, ledger_adjacency_graph_key, verify_graph, 236_token_write_paths).
- [x] T005 `tests/e2e/test_degree_cache_live.py` (marked e2e): rollback race over two connections, a removal in flight,
      parallel-edge multiplicity, Eraser clears `gen`/`degc`, `BuildKG` leaves the cache warm and releases its lock,
      a failed `BuildKG` keeps the adjacency, `WarmAll`, `GraphVerify` flags a wrong current entry only, `Remove` keeps a
      merged `^NKG` entry another graph holds, randomized sequence with rollbacks against a recount.
- [x] T006 Run T001-T004: red before the `.cls` edits (29 + 9 failures), green after.

## Phase 2: Implementation

- [x] T007 `Adj.cls`: `Degree` (cache), `Count`, `WarmFill`, `WarmAll`, `WarmCache`.
- [x] T008 `Adjacency.cls`: lock + gen in every method; stop writing `deg`/`degp`; `RemoveSource`, `RemoveNode`,
      `RemoveGraph`, `RemoveAll`, `BeginRebuild`, `FillEdge`, `FillTriple`, `EndRebuild`; `Remove` keeps a merged `^NKG` entry
      another graph holds.
- [x] T009 `Eraser`, `EdgeScan`, `TemporalIndex`, `FHIRGraph`: call `Adjacency`; delete `WriteAdjacencyShadow`.
- [x] T010 `TraversalBuild`: `BuildKG` through the rebuild entry points and leaves the cache warm; `Build2HopStats` and
      `BuildNKG` read the cache; `BackfillDegp` warms the cache; `TraversalKHop.KHopCount` goes through `Adj.Degree`.
- [x] T011 `GraphVerify` reconciles the cache; `GraphStores` lists `gen` and `degc`.

## Phase 3: Live

- [x] T012 Deploy `iris_src/src` into `IVGPAROS` (clean compile except the known `MCPToolSet` error), run T005 and the
      integration tests that touch adjacency.

## Phase 4: Commit

- [x] T013 Commit specs (`git add -f specs/248-alt-parity-os`) and code in logical commits. No attribution lines. No push.
