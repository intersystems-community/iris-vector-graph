<!-- markdownlint-disable MD013 -->

# Tasks: temporal edges that purge cleanly, collide visibly and rebuild identically

Tests come first in every phase and must fail before the code that makes them pass. E2E runs in `IVGFIX` only
(`IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`).

## Phase 0: baseline and support

- [x] T001 Baseline on the unchanged tree: bulk ingest rate, journal bytes per edge, purge cost (20,000 one-node-per-event
      edges, purge the oldest third). Recorded in the sign-off.
- [x] T002 [test] `tests/e2e/graph_snapshot.py`: canonical, sorted, hashed snapshot of every graph-scoped store in
      `GraphStores.Inventory()` for one graph; self-test that a one-entry change moves the hash.

## Phase 1: collisions (US2, FR-003)

- [x] T010 [test] unit: `TemporalWriteCount` is an int equal to written with `.collisions`; the store parses
      `"w:c:s"` and still accepts a bare integer; the mirror is skipped with the index on.
- [x] T011 [test] E2E `test_254_collisions_e2e.py`: same coordinates twice with different weights and attributes, in one
      batch and in two calls: raw, attributes, aggregates and HLL hold the first write; counts 1 written, 1 collision;
      `"update"` unchanged; `"skip"` counted as skipped; ms precision too.
- [x] T012 `WriteStores` return codes, `BulkInsertCounts`, `BulkInsert`; Python counts and mirror skip.
- [x] T013 Update `test_upsert_false_allows_duplicates` (pinned the double count).

## Phase 2: purge (US1, FR-001, FR-002)

- [x] T020 [test] E2E `test_254_purge_e2e.py`: the 48 h purge of a 72 h load equals a reference graph that only held the
      last 48 h (every store except aggregates, nodes and degree generations), degree 0, nodes stay.
- [x] T021 [test] E2E path matrix (plan table): one case per write and delete path, including `suppress_reverse_index`,
      ms precision, two graphs, `PurgeBefore`, `DeleteTemporalEdge`, `EraseNodes`, `EraseGraph`, `delete_edge`; Cypher
      `CREATE`/`MERGE` after a temporal edge as strict xfail.
- [x] T022 [test] the purge's read-only paths: a purge of an empty range changes no store (before/after snapshot).
- [x] T023 `RetireExpired`, `Adjacency.RetractTemporalRows`, both purges; `bulk_create_edges` adoption.
- [x] T024 ADR-0009 amendment; docstrings of `purge_raw_before`, `create_edge_temporal`, `bulk_create_edges_temporal`.

## Phase 3: rebuild (US3, FR-004)

- [x] T030 [test] E2E `test_254_rebuild_e2e.py`: 100,000 one-node-per-event edges plus identical colliding duplicates in
      batches of 1,000, snapshot; erase the graph, replay in 777s, snapshot; erase, replay shuffled, snapshot; all equal,
      aggregates included.

## Phase 4: existing callers (US4) and gates

- [x] T040 Unit suite; temporal, adjacency and eraser E2E/integration suites.
- [x] T041 SC-003/SC-004 measured on the new tree against T001.

## Sign-off (2026-10-22, `ivg-iris-enterprise`, namespace `IVGFIX`)

Measured with `BulkInsert` called directly (the same call on both trees), 20,000 one-node-per-event edges with two
attributes each, default graph, batches of 1,000, then `purge_raw_before` of the oldest 24 of 72 hours (6,667 edges);
four runs each, baseline classes from `alt-parity` deployed into the same namespace. Journal bytes are the change in
`%SYS.Journal.System.GetCurrentFileOffset()` across the ingest, so other work on the container counts too.

| Measure                   | Baseline (`alt-parity`) | Spec 254                | Criterion                      |
| ------------------------- | ----------------------- | ----------------------- | ------------------------------ |
| Ingest, events/s          | 7,160–7,461             | 6,970–7,182 (about −3%) | SC-003 within 15%: met         |
| Journal bytes per edge    | 1,861                   | 1,861–1,874             | SC-003 within 15%: met         |
| Purge, us per purged edge | 2.5–3.1                 | 44–48                   | SC-004 at most 2x: **not met** |
| Rows left after purge     | 20,000 (all)            | 13,333 (the survivors)  | SC-001: met                    |

The purge's check alone (every purged triple still held by a later timestamp, so nothing retired) costs 7.7 us an
edge, about 3x, most of it the walk of the surviving timestamps. The rest is one SQL `DELETE` per retired triple (index
maintenance, the adjacency functional index, journaling), which FR-001 requires under one node per event; a
`SELECT` then `DELETE` cost 66 us, the single prefix-filtered `DELETE` 49. SC-004 assumed the check was the new cost.

Tests: `tests/unit` 13,967 passed; the failures left are environment or pre-existing (`TestBFSArnoE2E`,
`test_rrf_fuse_e2e`, `test_structural_guard_e2e` connect to port 31971 and pass with `IVG_ARNO_PORT=31972`;
`test_sql_table_bridge` x3 fails on the baseline classes too). Temporal, adjacency and eraser E2E and integration (35
files): 576 passed, 2 xfailed (Cypher `CREATE`/`MERGE`, open), 1 failed:
`test_bulk_ingest_edges_file_reads_json_from_the_servers_disk`, which fails on the baseline classes too.

Open:

- Cypher `CREATE`/`MERGE` of a triple that already has a marked row leaves the marker, so a purge retires an edge a
  Cypher write asserted (strict xfail in `test_254_purge_e2e.py`).
- SC-004 (above). A follow-up could retire rows in bulk.
- A temporal write racing a purge on the same triple can leave a new timestamp without its row (the writer trusts
  `^KG("out")` and skips the assert; the purge then deletes the row). `DeleteTemporalEdge` has the same window.
- `create_edge_temporal` still returns `True` for a collision; counts are on the bulk path.
- Event nodes stay after a purge (US1 scenario 3), so `Graph_KG.nodes` still grows with one node per event; deleting
  them is the caller's.
- A triple whose timestamps carry different weights gets its first-written weight on the row, so a shuffled replay
  lands the same row weight only when the weights agree (pinned in US3).
