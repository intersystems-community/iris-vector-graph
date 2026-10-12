<!-- markdownlint-disable MD013 -->

# Tasks: Adjacency degree helpers (Graph.KG.Adj)

Test tasks come first. Each task is ticked when done.

## Phase 1: Tests first (no container)

- [x] T001 Source scan: `tests/unit/test_adj_degree_readers_no_stored_counter.py` (fails until T010-T013).
- [x] T002 Contract model and `Adj.cls` shape: `tests/unit/test_adj_contract.py` (fails until T004).
- [x] T003 Live E2E: `tests/e2e/test_adj_degree_live.py` (lead runs it; not run here).
- [x] T003a Run T001 and T002 once: 3 failed (reader scan; Adj.cls missing x2), 171 model tests passed.

## Phase 2: Adjacency helper

- [x] T004 Create `iris_src/src/Graph/KG/Adj.cls` with `Degree`, `NextSource`, `HasEdges` (uncompiled).

## Phase 3: Migrate readers

- [x] T010 Algorithms.cls: WCCJson and CDLPJson enumerate with `NextSource`.
- [x] T011 Centrality.cls: DegreeCentralityJson enumerates with `NextSource`; counts with `Degree`.
- [x] T012 PageRank.cls: Run (memoised `Degree`), PageRankGlobalJson (one pass before the loop).
- [x] T013 Subgraph.cls: SubgraphJson existence via `HasEdges`; PPRGuidedJson memoised `Degree`.
- [x] T014 Re-run T001 and T002 green (174 passed); `tests/unit/test_host.py` run separately.

## Phase 4: Commit

- [ ] T015 Commit on `alt/adj-degree-helpers` (no push, no attribution).
- [ ] T016 Gate for the lead: `tests/e2e/test_adj_degree_live.py` on IRIS (not run here).
