<!-- markdownlint-disable MD013 MD024 -->

# Tasks: Arno Callout Adoption

**Input**: `specs/236-arno-callout-adoption/` (spec, plan, research, data-model,
contracts, quickstart). Evidence: `research-capabilities.md`,
`research-token-audit.md`.
**Container**: `ivg-iris-enterprise` (31972) for every live test. Never
`ivg-iris`. Export `IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`.
**Tests are mandatory and come first.** Each phase ends in an e2e phase gate
that must pass before the next phase starts. A new test must be seen to fail
before its implementation task starts.

## Format: `- [ ] T### [P?] [US?] Description with file path`

`[P]` = parallelizable (different files, no dependency on an open task).

**Namespace safety (research R3)**: `BuildKG`/`sync()` can leave the Native API
process in `USER`. Every live test uses a fresh scratch namespace, recreates
the connection after any `BuildKG`/`sync`, and asserts a sentinel global
before any write or erase. Never run erase paths without that assertion.

---

## Phase 1: Setup

- [X] T001 Confirm branch `236-arno-callout-adoption` and that `ivg-iris-enterprise` is up (`scripts/enterprise-container.sh status`); record the loaded library sha256 (expected `35ea20ae…`, not 1.1.0) in `specs/236-arno-callout-adoption/research.md` under "Baseline"; record the current `USER` contents (row counts in `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, and the top-level `^KG` keys) (2026-10-03 baseline: all tables 0 rows, `^KG` holds only `__version`=54958; Tom confirmed `USER` is test-only and the erased data is not to be restored)
- [X] T002 [P] Add `IVG_REQUIRE_ARNO` and `IVG_DISABLE_ALGO_CACHE` to the environment table in `docs/OPERATIONS.md`
- [X] T003 [P] Create empty test modules with module docstrings naming the spec: `tests/unit/test_236_reader_routing.py`, `tests/unit/test_236_proof_file.py`, `tests/unit/test_236_cache_keys.py`, `tests/integration/test_236_capabilities.py`, `tests/integration/test_236_token_write_paths.py`, `tests/integration/test_236_algo_cache.py`, `tests/integration/test_236_bfs_size_gate.py`, `tests/e2e/test_236_reader_gate.py`

## Phase 2: Foundational (blocks all stories)

- [X] T004 Write a failing test in `tests/integration/test_236_capabilities.py` that a scratch-namespace helper fixture asserts a sentinel global before writes and recreates the Native API connection after `BuildKG`/`sync` (reproduces the `USER` flip)
- [X] T005 Implement the scratch-namespace fixture `ivg_236_scratch` in `tests/conftest.py` (creates/drops namespace `IVG236`, sentinel check, reconnect-after-sync helper) so T004 passes; recipe in memory note `scratch_namespaces_enterprise`
- [X] T006 [P] Add fake capability-report helpers (`fake_report(readers, version)`) in `tests/unit/_236_helpers.py`
- [X] T007 [P] Add the JSON schema check helper for `contracts/arno-reader-proof.schema.json` in `tests/unit/_236_helpers.py`

**Gate**: T004 passes with the fixture; no test touches `USER`.

## Phase 3: User Story 1 - Old and new library builds both work (P1)

**Goal**: per-algorithm routing on reader revision; old/absent library uses Owners.
**Independent test**: feed `route()` each report shape with no container.

### Tests first

- [X] T008 [P] [US1] Unit tests in `tests/unit/test_236_reader_routing.py`: no entry, empty readers, reader below min, at min, above min, unknown reader name, betweenness never native, malformed/non-JSON report, version-string change with equal revisions gives equal routing (Story 1 scenarios 1-5); named-graph `ppr` and `subgraph` with a sufficient revision still give Owner (FR-014a)
- [X] T009 [P] [US1] Unit tests in `tests/unit/test_236_proof_file.py`: missing file, unparseable file, schema-invalid file all give an empty proof and a logged diagnostic; valid file loads once per process
- [X] T010 [P] [US1] Unit tests in `tests/unit/test_236_reader_routing.py`: dispatch sites fall back to the Owner when a Native Reader raises (mock the call the dispatch actually makes; make the raise explicit, not MagicMock-implicit)
- [X] T011 [US1] Integration test in `tests/integration/test_236_capabilities.py`: against the currently loaded (old) library `ReaderRevisions()` returns `"{}"`, never throws, and a second call still works

### Implementation

- [X] T012 [US1] Add `ReaderRevisions()` to `iris_src/src/Graph/KG/ArnoAccel.cls` (`$ZF(-4,3,dllid,"capabilities")` + `$ZF(-5,dllid,funcid)` in `Try/Catch`, `"{}"` on any failure; reuse `^ArnoAccel("dllid")`); push via `iris_doc(mode="put", compile=true)`; follow objectscript-guardrails
- [X] T013 [US1] Create `iris_vector_graph/arno_readers.py` with `read_reader_report`, `load_proof`, `route` per `contracts/reader-routing.md`
- [X] T014 [US1] Wire routing into the five dispatch sites: `iris_vector_graph/stores/iris_sql_store.py` (pagerank ~1601, wcc ~1633, cdlp ~1666), `iris_vector_graph/_engine/algorithms.py` (ppr ~376, subgraph ~457); keep the Owner fallback and `IVG_DISABLE_ARNO`; remove the dead `If fid = 0` reliance noted in R1 wherever touched; pass the graph to `route()` so a named graph gives Owner (FR-014a)
- [X] T015 [US1] Create a starter `docs/arno-reader-proof.json` with `library` set to version `arno-callout 0.0.0+none` and an all-zero sha256, `proven_at` 2026-10-03, and an empty `algorithms` map (valid against the schema), so routing ships inert until Phase 4 proves readers

**Phase gate (US1)**: T008-T011 pass; with an old library every algorithm still returns the Owner answer (run `tests/unit/test_bfs_arno.py` and the existing Arno integration tests; SC-008 no behaviour change).

## Phase 4: User Story 2 - A Native Reader is used only after it is proven (P1)

**Goal**: evidence gate, pinned library, committed proof file.
**Independent test**: run the gate on the enterprise container; tamper the proof file and see it fail.

### Tests first

- [X] T016 [P] [US2] Unit tests in `tests/unit/test_236_proof_file.py`: equivalence helpers (pagerank/ppr node set, 1e-6 relative, tie-aware ranking; wcc partition ignoring names; subgraph exact sets; cdlp similarity and 5-call identity) including negative cases that must fail
- [X] T017 [P] [US2] Unit test: proof writer records min revision only for algorithms that passed, never for a failing one, and keeps the others
- [X] T018 [US2] E2E in `tests/e2e/test_236_reader_gate.py`: fails when `docs/arno-reader-proof.json` claims a revision above the loaded library's report; with `IVG_REQUIRE_ARNO=1` it fails (not skips) when the library is absent; without the switch on a non-enterprise machine it skips like the existing Arno tests
- [X] T019 [US2] E2E: for each in-scope algorithm, five native calls per fixture are identical and equivalent to the Owner on fixtures path, star, dense cluster, multi-component, and one tie-heavy graph (fixtures defined in T020)

### Implementation

- [X] T020 [P] [US2] Create fixtures in `tests/fixtures/arno_reader_graphs.py` (five graphs, deterministic node ids, documented expected shapes)
- [X] T021 [US2] Install the pinned 1.1.0 build: extend `scripts/enterprise-container.sh` with `install-arno <path>` that verifies sha256 `da28ca71…ab03` (arm64) before copying over `docker/enterprise/libarno_callout.so`, then reloads via `tcp-load-arno`; run it (file is gitignored); record the result in `research.md`
- [X] T021a [US2] Run the existing Arno tests on the 1.1.0 build (`tests/unit/test_bfs_arno.py`, `tests/unit/test_230_arno_*.py`, `tests/unit/test_411_arno_kg_readers.py`, the Arno integration tests); record in `research.md` any failure that did not occur on the old build
- [X] T022 [US2] Implement the equivalence helpers in `iris_vector_graph/arno_readers.py` (or `scripts/arno_reader_gate.py` if not needed at runtime) to satisfy T016
- [X] T023 [US2] Implement `scripts/arno_reader_gate.py --write-proof`: loads the library report, runs each algorithm on each fixture natively x5 and via the Owner, writes `docs/arno-reader-proof.json` (library version + sha256, min revision, fixtures, repeats, cdlp similarity threshold taken from observed data and recorded); `--check` reads the proof file, compares it with the loaded report, exits non-zero on any mismatch and writes nothing; a dropped CDLP fixture is listed under `dropped`, never silently removed
- [X] T024 [US2] Run the gate on `ivg-iris-enterprise` with `IVG_REQUIRE_ARNO=1`; commit nothing; review the generated proof file; for any algorithm that fails keep it absent and record the failure in `research.md`
- [X] T025 [US2] Add the gate step to `PRE_RELEASE_CHECKLIST.md` (`IVG_REQUIRE_ARNO=1 python scripts/arno_reader_gate.py --check` and T018) and to `scripts/ci-parity.sh` guidance for the enterprise tier; CI stays library-free

**Phase gate (US2)**: T016-T019 pass; T018 proves the tamper case fails; routing tests from US1 still pass with the generated proof file.

## Phase 5: User Story 3 - BFS never overruns the argument limit (P2)

**Goal**: size gate before the library call.
**Independent test**: graphs with adjacency just under, at, over 34,791 and past 65,535.

### Tests first

- [X] T026 [P] [US3] Unit tests in `tests/unit/test_236_reader_routing.py` (or `tests/unit/test_bfs_arno.py`): gate function returns owner for length > 34,791, native-eligible for <= 34,791; named-graph and inbound still select the ObjectScript adapter (unchanged routing)
- [X] T027 [US3] Integration tests in `tests/integration/test_236_bfs_size_gate.py`: adjacency lengths 34,790/34,791/34,792/65,535/65,536+ each return the correct BFS result and raise no `OUT OF $ZF HEAP SPACE` / `MAX $ZF STRING`; compare to the ObjectScript BFS on the same graph

### Implementation

- [X] T028 [US3] Locate the point in `iris_src/src/Graph/KG/NKGAccelTraversal.cls` (near :341-353) and `NKGAccelLoader.cls` where the serialized adjacency string exists; record the exact spot in `research.md` R4 (closes the UNVERIFIED item)
- [X] T029 [US3] Add the length check before `$ZF` and return to the ObjectScript BFS above 34,791 characters; add a named constant and a comment citing `docs/research/zf-heap-and-exstr.md`; push and compile
- [X] T030 [US3] Leave `kg_bfs_compute` unadopted: add a comment at `_ArnoBfsAdapter` and the Open Questions note that adoption needs arno's `test_neg_int_keys` result (FR-015)

**Phase gate (US3)**: T026-T027 pass on `ivg-iris-enterprise`; existing `tests/unit/test_bfs_arno.py` and BFS integration tests unchanged.

## Phase 6: User Story 4 - Repeat calls come from a shared cache (P2)

**Goal**: hardened `^KG("__version")` token, then the shared cache.

### 6a. Token hardening (tests first, must fail first)

- [X] T031 [US4] Write the FR-019 write-path tests in `tests/integration/test_236_token_write_paths.py` from `research-token-audit.md` section 7 (29 cases, oracle: `after == truth`, `after != cached` where `^KG` changes, repeat is a hit); parametrize by pagerank/wcc/cdlp/ppr; expect cases 14, 15, 16, 22, 23, 24, 25 to fail
- [X] T032 [P] [US4] Concurrency test: two concurrent `BumpVersion` calls yield two distinct values; rolled-back erase leaves the counter advanced (in `tests/integration/test_236_token_write_paths.py`)
- [X] T033 [US4] Fix 1: `ArnoAccel.BumpVersion` uses `$Increment` in `iris_src/src/Graph/KG/ArnoAccel.cls` (also used by `Eraser.InvalidateDerived`)
- [X] T034 [P] [US4] Fix 2: bump after import and on `merge=True` in `iris_vector_graph/_engine/snapshot.py` (`finally:` block ~1251-1270)
- [X] T035 [P] [US4] Fix 3: `BuildKG` multigraph branch bumps after `TCommit`, and the `Catch` bumps after `TRollback`, in `iris_src/src/Graph/KG/TraversalBuild.cls` (~81-87, :116)
- [X] T036 [P] [US4] Fix 4: keyed adjacency primitives bump before `Quit` in `iris_src/src/Graph/KG/EdgeScan.cls` (~240-333)
- [X] T037 [P] [US4] Fix 5: `Loader.Load` and `BenchFormat` read, kill, then set the counter to old+1 in `iris_src/src/Graph/KG/Loader.cls`
- [X] T038 [US4] Push and compile the changed classes (`iris_doc put compile=true`), then run T031-T032 until green

### 6b. Cache (tests first)

- [X] T039 [P] [US4] Unit tests in `tests/unit/test_236_cache_keys.py`: `paramHash` canonicalization (key order, numeric forms), Graph Key form (`0` for default, never the ledger form), subgraph and oversize payloads never cached, `IVG_DISABLE_ALGO_CACHE=1` disables
- [X] T040 [US4] Integration tests in `tests/integration/test_236_algo_cache.py`: repeat call is a hit; second connection hits; different params separate; two graphs isolated; erasure leaves no erased node id in any payload (FR-020); token mismatch recomputes and overwrites
- [X] T041 [US4] Create `iris_src/src/Graph/KG/AlgoCache.cls` (`Get`, `Put`, `InvalidateGraph` per `contracts/algo-cache.md`; single round trip compare; follow guardrails)
- [X] T042 [US4] Kill the graph's `^IVG.AlgoCache` subtree in erase paths in `iris_src/src/Graph/KG/Eraser.cls` (`InvalidateDerived` neighbourhood)
- [X] T043 [US4] Wire the cache into pagerank, wcc, cdlp and ppr in `iris_vector_graph/_engine/algorithms.py` and `iris_vector_graph/stores/iris_sql_store.py`: read token, `Get`, compute (Native or Owner), `Put` with the step-1 token; subgraph bypasses
- [X] T044 [US4] Benchmark (FR-021): time shared-cache hit vs a PPG front layer for a small and a large result on `ivg-iris-enterprise`; record numbers in `research.md`; add the PPG layer only if it is >= 2x faster with the shared token re-check kept, otherwise record the decision not to

**Phase gate (US4)**: T031-T032 and T039-T040 all green (29 write-path cases), including the former failures; the differential tests for Cypher DML and raw SQL pass.

## Phase 7: User Story 5 - Known issues describe the current build (P3)

- [X] T045 [US5] Test in `tests/unit/test_spec_hygiene_gates.py` (or a new `tests/unit/test_236_known_issues.py`): every Arno entry in `docs/KNOWN_ISSUES.md` names the build it was verified against and a date
- [X] T046 [US5] Rewrite the Arno section of `docs/KNOWN_ISSUES.md` for `1.1.0+3b2123b8e7bb`: BFS size limit (measured), betweenness undirected by construction, x86_64 never run on IRIS, `kg_bfs_compute` empty result and `ns.keys()` hypothesis, `set_edge_weight` changes PageRank, `Build2HopExactStats` namespace flip; move fixed items to the ruled-out section with evidence
- [X] T047 [US5] Run `markdownlint-cli2 --fix` and `prettier --write` on every markdown file touched

**Phase gate (US5)**: T045 passes; SC-009 reviewed against the recorded evidence.

## Phase 8: Polish and cross-cutting

- [X] T048 Fix or narrowly guard the `Build2HopExactStats` namespace flip in `iris_src/src/Graph/KG/Traversal*.cls` so it restores the caller's namespace (separate commit); add a regression test in `tests/integration/test_236_capabilities.py` (skip only if the fix is deferred and record why)
- [X] T049 [P] Add a `CHANGELOG.md` entry and update `CONTEXT.md` if terms changed
- [X] T050 Run `scripts/ci-parity.sh`, then the full unit suite and the enterprise-tier Arno tests; confirm no new skips are hidden (`SKIP_IRIS_TESTS` default `false`)
- [X] T051 Run the quickstart steps in `specs/236-arno-callout-adoption/quickstart.md` end to end and correct it where it differs
- [X] T052 Send the arno maintainers the open asks as a drafted message only (no send): `test_neg_int_keys` result, directed-betweenness flag, resident-BFS symbol, EXSTR sibling, namespace handling, x86_64 run; file under `specs/236-arno-callout-adoption/arno-asks.md`; add that the x86_64 build needs a manual enterprise-tier run on x86_64 hardware because CI does not fetch the library

## Dependencies and order

- Phase 1 -> 2 -> 3 (US1) -> 4 (US2). US2 needs US1 routing and the proof schema.
- Phase 5 (US3) and Phase 6 (US4) depend only on Phase 2; they may run in
  parallel after Phase 4, or before it if the library swap is delayed.
- 6b depends on 6a: no cache code until the token tests are green.
- Phase 7 after the behaviour it documents settles; Phase 8 last.
- T048 is independent of the rest and can be a separate PR.

## Parallel examples

- Phase 3 tests: T008, T009, T010 together.
- Phase 6a fixes: T034, T035, T036, T037 together after T033.

## Implementation strategy

MVP = Phases 1-3: routing ships inert and safe with no behaviour change on old
builds. Then Phase 4 to turn Native Readers on with evidence. US3 and US4 are
independent increments. Nothing is pushed or filed without explicit
instruction.
