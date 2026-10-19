<!-- markdownlint-disable MD013 -->

# Tasks: The Adjacency Index kept current by the edge table

Tests come first in every phase and must fail before the code that makes them pass. E2E runs in `TCKJ`
(`IVG_NAMESPACE=TCKJ IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`).

## Phase 0: Shared test support

- [x] T001 `tests/e2e/adjacency_recount.py`: recount `out` / `in` / `label` (and keyed children, `"#"`) from the tables for a set of
      node ids, and walk the index for entries with no row behind them; returns the differences.

## Phase 1: Index and callbacks (US1)

- [x] T010 [test] `tests/e2e/test_252_cypher_read_after_write_e2e.py`: CREATE, MERGE, DELETE, DETACH DELETE, `SET r.weight`,
      `SET`/`REMOVE` label, each read back through one-hop MATCH from a bound node, `shortestPath`, weighted shortest path, BFS,
      k-hop counts; plus the recount. Fails today.
- [x] T011 [test] `tests/unit/test_252_index_declaration.py`: `ensure_indexes` issues both `CREATE INDEX ... AS` statements only
      when the classes are deployed, after the ekey migration, and announces the build; never on a DDL-only namespace.
- [x] T012 [test] rollback E2E: a Cypher write inside a rolled-back transaction leaves the index and the recount equal.
- [x] T013 `Graph.KG.AdjIndex`, `Graph.KG.LabelIndex`; `Adjacency.RowPut` / `RowRemove` / `PutLabel` / `RemoveLabel`.
- [x] T014 `"#"` by recount in `PutKeyed` / `RemoveKeyed`; `Remove` touches `^NKG` only when the entry existed.
- [x] T015 `ensure_indexes`: the two declarations.
- [x] T016 Gate: unit suite, TCK 3896/3896, T010-T012 green.

## Phase 2: Writers (US2)

- [ ] T020 [test] one E2E per writer path in FR-005 with the recount (raw SQL INSERT/DELETE/UPDATE, `create_edge`, `delete_edge`,
      `bulk_ingest_edges`, `bulk_create_edges` +/- `auto_sync`, ledger commit and rollback, `erase_graph`, `erase_all`,
      `import_rdf`, inference materialize/retract, FHIR sync, snapshot restore).
- [ ] T021 [test] unit: the Python writers no longer call `WriteAdjacency` / `DeleteAdjacency` after a row write.
- [ ] T022 Drop the double writes (plan, Writers table); FHIRGraph `ResyncKey` / `DropNode`.
- [ ] T023 `BuildKG` / `BuildKGKeyed`: edge and label passes become `%BuildIndices`.
- [ ] T024 `prop`: `Subgraph` reads SQL; remove the `^KG("prop")` writers.
- [ ] T025 Gate: unit, TCK, T020 green, `test_adjacency_single_writer.py` green.

## Phase 3: No adjacency without a row (US2)

- [ ] T030 [test] temporal: a temporal edge has a marked row; the last timestamp's delete removes a marked row and keeps an
      explicit one; upgrade backfill from `^KG("tout")`.
- [ ] T031 [test] weight: `create_edge(weight=)` and `set_edge_weight` survive a rebuild.
- [ ] T032 [test] `bulk_delete_adjacency` warns and deletes rows; `Loader` gone; `BenchFormat` seeds rows; restore skips archived adjacency.
- [ ] T033 Temporal-origin column and migration; temporal writers assert the row; `PutShadow` calls removed.
- [ ] T034 Weight into `qualifiers`; the row-less APIs.
- [ ] T035 Gate: unit, TCK, T030-T032 green.

## Phase 4: Bulk (US3)

- [ ] T040 [test] `bulk_load_session` leaves the index equal to the recount, duplicates not double-counted, also on a mid-load failure.
- [ ] T041 `%NOINDEX` in the session's server loader plus a ranged `%BuildIndices` at the end.
- [ ] T042 SNB SF0.1 load time against today's (SC-004); write-cost probe (SC-003).

## Phase 5: Existing installs and docs (US4)

- [ ] T050 [test] a drifted install (entry with no row, row with no entry): `ensure_indexes` rebuilds once and announces it; a
      second call does nothing; `sync()` still succeeds.
- [ ] T051 `CONTEXT.md` (Adjacency Index, Drift), ADR-0008/0009 status, CHANGELOG (major: temporal triples visible).
- [ ] T052 SNB SF0.1, 40 queries, same answers; TCK; unit; then the AWS SF1/SF10 run.
