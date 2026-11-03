<!-- markdownlint-disable MD013 -->

# Tasks: One IVG Schema per namespace (spec 262)

**Input**: `specs/262-ivg-owned-sql-schema/` (spec.md, plan.md, research.md, data-model.md, contracts/python-api.md, quickstart.md)
**Decisions**: ADR-0016; grill and clarify sessions 2026-11-03
**Tests**: mandatory and first in every phase (constitution III). A phase is done when its E2E gate passes. Each test
task records its red run (the failing assertion on the pre-task code) in the commit message.

**Environment for every live task**: `IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_ARNO_PORT=31972
IVG_NAMESPACE=IVGFIX IVG_SECONDARY_NAMESPACE=IVGSEC PYTHONPATH=<worktree>`, Python
`/Users/tdyar/ws/iris-vector-graph/.venv/bin/python`. Scratch namespaces for this spec: `IVG262ALT` (IVG Schema
`IVGALT`), `IVG262UPG` (upgrade fixtures), `IVG262BASE` (speed-gate baseline). Never use another project's container.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US6 from spec.md

---

## Phase 1: Setup

**Purpose**: scratch namespaces and the frozen 4.5.1 starting point every upgrade test needs.

- [ ] T001 Create scratch namespaces `IVG262ALT`, `IVG262UPG`, `IVG262BASE` on `ivg-iris-enterprise` with their own databases (recipe: memory `scratch_namespaces_enterprise`), via a script `scripts/dev/make_scratch_namespace.py <NS>` that is idempotent and refuses any name outside `IVG262*`
- [ ] T002 Write `scripts/fixtures/build_release_namespace.py <tag> <NS>`: checks out `<tag>` into `~/.cache/ivg-bench/releases/<tag>` (git worktree), and with `PYTHONPATH` on that worktree runs its `initialize_schema()` in `<NS>` and seeds the graph from `tests/e2e/fixtures/old_releases.py`'s seed; prints what it created. Used by every upgrade task (T040+)
- [ ] T003 [P] Write `scripts/dev/gen_legacy_manifest.py`: reads `iris_vector_graph/schema.py` and `iris_vector_graph/_engine/schema.py` at tags `v4.4.0`…`v4.5.1` (`git show <tag>:<path>`) and emits every `SQLUser` function, procedure and view those releases create, their `User.*` class names (IRIS mapping, research R2), plus `Graph.KG.Benchmark` and `Graph.KG.BenchFormat`, as the Python literal for `iris_vector_graph/legacy_manifest.py`

---

## Phase 2: Foundational (blocks every story)

**Purpose**: the name, the record, the error type, the two name helpers and the source rewrite.

### Tests first (phase 2)

- [ ] T004 [P] Unit tests in `tests/unit/test_schema_name.py` for `resolve_schema_name(arg, env)`: argument wins over `IVG_SCHEMA_PREFIX`; `IRIS_SCHEMA_PREFIX` ignored; default `Graph_KG`; `ValueError` naming the rule for `""`, `1abc`, `a-b`, `SQLUser`, `sqluser`, `%X`; accepts `IVGALT`, `My_App`; the package mapping `MY_APP` → `MY.APP`; refusal of packages equal to or under `Graph.KG`, `IVG`, `IVGC` except `Graph_KG` itself
- [ ] T005 [P] Unit tests in `tests/unit/test_fn_names.py`: `_fn("STR_SPLIT", "IVGALT") == "IVGALT.STR_SPLIT"`; `_fn` and `_table` agree on the schema; no two IVG function names compile to the same class name (`func` + name without `_`) and none equals an IVG table or view name
- [ ] T006 [P] Unit tests in `tests/unit/test_source_rewrite.py`: `rewrite_sources(sources, "IVGALT")` over every packaged `.cls` (from `class_deploy.read_class_sources`) leaves 0 allow-listed `Graph_KG.<ivg object>` and `Graph.KG.<table or function class>` references, changes 0 references to code classes (`Graph.KG.Eraser`, `Graph.KG.Adjacency`, …: every class in `iris_src/src`), is the identity for `Graph_KG`, and changes the fingerprint when the schema changes
- [ ] T007 [P] Unit tests in `tests/unit/test_schema_record.py` (mocked native object that records `get`/`set`, no `create=True`): `read_record` returns `None` when `^IVG.Deploy("schema")` is absent; `write_record` sets name and `("schema","recorded")`; `IVGSchemaError` is a `RuntimeError` exported from `iris_vector_graph`, and its two messages carry the strings in contracts/python-api.md

### Implementation (phase 2)

- [ ] T008 Create `iris_vector_graph/schema_name.py`: `resolve_schema_name`, `schema_package`, `validate_schema_name`, `read_record`, `write_record`, `IVGSchemaError` (re-exported in `iris_vector_graph/__init__.py`); make T004 and T007 pass
- [ ] T009 Add `_fn(name, schema=None)` beside `_table()` in `iris_vector_graph/cypher/translator.py`; make T005 pass
- [ ] T010 Add `rewrite_sources(sources, schema)` to `iris_vector_graph/_engine/class_deploy.py` with the allow-list from research R1 (IVG table names, function and procedure names, table-class and function-class names), and apply it in `deploy_packaged_classes` before `upload_and_compile`, with the fingerprint taken over the rewritten text; make T006 pass
- [ ] T011 `IRISGraphEngine.__init__` in `iris_vector_graph/engine.py`: `schema_prefix: Optional[str] = None` resolved by `resolve_schema_name`, validated before any database call; existing `schema_prefix="Graph_KG"` callers unchanged (unit test added to `tests/unit/test_schema_prefix_isolation.py`)

**Gate**: `tests/unit/test_schema_name.py test_fn_names.py test_source_rewrite.py test_schema_record.py test_schema_prefix_isolation.py` green; full unit suite green.

---

## Phase 3: User Story 1 — an operator finds all of IVG in one schema (P1) 🎯 MVP

**Goal**: a fresh namespace has every IVG function, procedure and table in the IVG Schema and nothing in `SQLUser` or `User`.
**Independent test**: initialize a fresh namespace and list its schemas, routines, views and classes.

### Tests first (phase 3)

- [ ] T012 [P] [US1] Unit test `tests/unit/test_no_sqluser_refs.py`: grep of `iris_vector_graph/` for `SQLUser.` and for unqualified `FROM rdf_edges|rdf_props|rdf_labels|nodes` finds nothing outside `legacy_manifest.py` and the legacy-removal code
- [ ] T013 [P] [US1] E2E `tests/e2e/test_262_fresh_namespace.py` in a freshly emptied `IVG262ALT` (schema `Graph_KG` for this file): after `initialize_schema()`, `INFORMATION_SCHEMA.ROUTINES` and `.VIEWS` show 0 IVG objects in `SQLUser`, `%Dictionary.ClassDefinition` shows 0 IVG classes in `User`; every IVG function is in `Graph_KG`; an application's own `SQLUser.RAND` and `User.funcRAND` created before setup are unchanged after it
- [ ] T014 [P] [US1] E2E in the same file: one Cypher query per helper family the translator emits (`CY_*`, `CypherFn_*`, JSON, list, string, regex, temporal, VLP) on a hand-built graph returns its hand-worked answer, and `IVGResult.sql` names no object outside `Graph_KG`
- [ ] T015 [P] [US1] E2E: text search (`text_search.py` paths) and the Python PPR fallback give their known answers on a namespace with no `SQLUser` views

### Implementation (phase 3)

- [ ] T016 [US1] `iris_vector_graph/schema.py` and `iris_vector_graph/_engine/schema.py`: every `CREATE [OR REPLACE] FUNCTION|PROCEDURE` names the IVG Schema (`self._schema`); remove the four `CREATE VIEW SQLUser.*` and `RDF_EDGES_COMPAT_VIEW_DDL` re-creation (the 3.2.0 rescue still drops a legacy view when present)
- [ ] T017 [US1] `iris_vector_graph/cypher/translator.py`: replace all 102 `SQLUser.` references with `_fn(...)` under the engine's schema; `iris_vector_graph/_engine/query.py`, `prop_values.py`, `schema.py` likewise
- [ ] T018 [US1] `iris_vector_graph/text_search.py` (lines 106, 161, 209, 210) and the PPR fallback: qualify through `_table()`
- [ ] T019 [US1] Native executor and compiled plans (`Graph.KG.Cypher.Gen`, spec 257): plan text names functions through the rewritten classes; check `IVGC.P<n>` plans compile in `Graph_KG` with no `SQLUser` reference

**Gate**: T012–T015 green; `tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match` green in `Graph_KG`.

---

## Phase 4: User Story 3 — a user chooses the schema name (P1)

**Goal**: with `schema_prefix="IVGALT"`, every IVG feature works and no `Graph_KG` schema exists.
**Independent test**: one E2E case per write, delete and read path in `IVG262ALT`.

### Tests first (phase 4)

- [ ] T020 [P] [US3] Unit tests `tests/unit/test_schema_env.py`: `IVG_SCHEMA_PREFIX=ENVNAME` with `schema_prefix="ARGNAME"` → `ARGNAME`; with `IRIS_SCHEMA_PREFIX=RAG` set and no IVG variable → `Graph_KG`
- [ ] T021 [US3] E2E `tests/e2e/test_262_alt_schema_paths.py`, namespace `IVG262ALT`, schema `IVGALT`: setup leaves no `Graph_KG` schema and every IVG table, function and procedure in `IVGALT`; the deployed classes contain 0 surviving `Graph_KG.`/table-class references (SC-004, read from `%Dictionary.CompiledMethod` implementations)
- [ ] T022 [US3] Same file: one case per path, each asserting database state and a known answer, enumerated from `tests/integration/test_236_token_write_paths.py` and the graph-scope tests: node and edge create (row and bulk), property and label set/remove, `MERGE`, `DETACH DELETE`, `delete_nodes`, `erase_graph`, bare-node retire, temporal edge write and window read, `drop_temporal_structural_rows`, ledger commit/verify/redact, embeddings store and KNN, per-graph routed embeddings, BM25 text search, PageRank/PPR/WCC/CDLP/betweenness, BFS and shortest path, snapshot save/restore, FHIR graph build
- [ ] T023 [P] [US3] Direction-symmetry gate in `IVGALT`: parametrize `tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match` over `Graph_KG` and `IVGALT`

### Implementation (phase 4)

- [ ] T024 [US3] Thread the engine's schema through every Python store and mixin that still reads the module global or the literal `Graph_KG` (`grep -rn '"Graph_KG' iris_vector_graph`), until T021–T023 pass
- [ ] T025 [US3] Package-clash refusal at `initialize_schema()` (research R2): a schema whose package holds a class IVG did not create raises `IVGSchemaError` naming the class (E2E case in T021's file)

**Gate**: T020–T023 green.

---

## Phase 5: User Story 2 — an existing namespace upgrades without breaking anyone's SQL (P1)

**Goal**: a 4.5.1 namespace upgrades; legacy objects and their grants keep working; `remove_legacy_objects` lists and removes exactly the manifest.
**Independent test**: build `IVG262UPG` with T002 at `v4.5.1`, upgrade, run legacy SQL, list, remove.

### Tests first (phase 5)

- [ ] T026 [P] [US2] Unit test `tests/unit/test_legacy_manifest.py`: `iris_vector_graph/legacy_manifest.py` equals `scripts/dev/gen_legacy_manifest.py`'s output from the tags; contains no pattern entries; includes both benchmark classes
- [ ] T027 [P] [US2] Unit tests `tests/unit/test_remove_legacy_objects.py` (mocked cursor and native): `dry_run=True` issues no DDL; only manifest names are listed; a present `SQLUser.CY_MINE` is never listed
- [ ] T028 [US2] E2E `tests/e2e/test_262_upgrade_451.py` on `IVG262UPG` built from `v4.5.1`: after 4.6.0 `initialize_schema()`, Cypher answers from the IVG Schema, `SELECT SQLUser.STR_SPLIT(...)` and the other legacy calls still answer as before, one log line names the leftovers
- [ ] T029 [US2] Same file: grants. Before upgrade, `CREATE ROLE ivg262_app`, `GRANT EXECUTE` on the 4.5.1 functions Cypher needs, and a user with only that role and `SELECT` on the tables; a Cypher query as that user works before and after the upgrade; `initialize_schema(dry_run=True)` lists each grant it would copy and the snapshot is unchanged
- [ ] T030 [US2] Same file: `remove_legacy_objects()` returns exactly the manifest objects present (and an application `SQLUser.CY_MINE` is absent from it); `dry_run=False` removes exactly those; whole-namespace snapshot (`tests/e2e/graph_snapshot.py` plus the routine/class lists) differs only by them
- [ ] T031 [US2] Same file, second fixture: a namespace built at `v3.2.0`, upgraded through `v4.5.1` (T002 twice), then to 4.6.0, ends in the same state as T028
- [ ] T032 [US2] Same file: a native plan compiled before the upgrade is gone after it (FR-014)

### Implementation (phase 5)

- [ ] T033 [US2] Generate `iris_vector_graph/legacy_manifest.py` with T003's script; make T026 pass
- [ ] T034 [US2] Grant copy in `iris_vector_graph/_engine/schema.py`: read `%Library.SQLCatalogPriv:SQLAllPrivileges` per legacy object, keep `GRANTED_VIA = Direct`, grant on the replacement (`%SYSTEM.SQL.Security.GrantPrivilege[WithGrant]`), report in `grants_copied`; under `dry_run` report only
- [ ] T035 [US2] `engine.remove_legacy_objects(dry_run=True)` in `iris_vector_graph/_engine/admin.py` per contracts/python-api.md; make T027 and T030 pass
- [ ] T036 [US2] `initialize_schema(dry_run=False)` signature and the new result keys (`schema`, `schema_recorded`, `legacy_objects`, `grants_copied`, `dry_run`); dry run proven read-only

**Gate**: T026–T032 green.

---

## Phase 6: User Story 4 — the namespace's schema is fixed once recorded (P2)

**Goal**: record, infer, refuse; a mismatched engine and a not-yet-upgraded namespace both get one clear error.
**Independent test**: record states on `IVG262UPG` and `IVG262ALT`.

### Tests first (phase 6)

- [ ] T037 [P] [US4] Unit tests `tests/unit/test_first_access.py`: the first query of an engine whose schema differs from the record raises `IVGSchemaError` before any SQL is sent (mock cursor sees nothing); with no record and no 4.6.0 objects, the FR-006a message; with a matching record, no extra round trip after the first
- [ ] T038 [US4] E2E `tests/e2e/test_262_record.py`: new namespace records `Graph_KG`; 4.5.1 namespace with all tables in `RAG` (T002 with `schema_prefix="RAG"`) is inferred as `RAG` and the ObjectScript features then read `RAG` (erase and temporal write give known answers); a namespace with tables in `Graph_KG` and `RAG` stops with both schemas and row counts and records nothing; `initialize_schema(schema_prefix="RAG")` then records `RAG` and reports the `Graph_KG` tables untouched; each dry run proven read-only
- [ ] T039 [US4] Same file: an engine with `schema_prefix="RAG"` on a namespace recorded `Graph_KG` raises on first access, and no row or global changed (snapshot)
- [ ] T040 [US4] E2E `tests/e2e/test_262_not_upgraded.py`: a `v4.5.1` namespace and the 4.6.0 package, no `initialize_schema()`: the first Cypher query raises the FR-006a error, not a missing-function error; `SELECT SQLUser.STR_SPLIT(...)` still answers
- [ ] T041 [US4] Same file: the manual consolidation route from ADMIN_GUIDE (record one schema, move or drop the other's IVG tables with the SQL ADMIN_GUIDE gives) run on a two-schema namespace ends with one schema and the graph's known answers

### Implementation (phase 6)

- [ ] T042 [US4] Record, infer and refuse in `initialize_schema()` (`iris_vector_graph/_engine/schema.py`) per data-model.md's transitions
- [ ] T043 [US4] First-access check in the engine's connection path (`iris_vector_graph/engine.py` / `stores/iris_sql_store.py`): read `^IVG.Deploy("schema")` once per connection; raise `IVGSchemaError` per contracts/python-api.md
- [ ] T044 [US4] `status()` gains `schema` and `legacy_objects`; `report()` prints both (`iris_vector_graph/status.py`, `_engine/admin.py`); unit test in `tests/unit/test_status_schema.py` written first

**Gate**: T037–T041 green.

---

## Phase 7: User Story 5 — production namespaces carry no benchmark code (P2)

### Tests first (phase 7)

- [ ] T045 [P] [US5] Unit test `tests/unit/test_wheel_contents.py`: the built wheel holds no `Graph/KG/Benchmark.cls` or `BenchFormat.cls`, and holds `Service.cls` and `FHIRGraphSyncTask.cls`
- [ ] T046 [US5] E2E `tests/e2e/test_262_fresh_namespace.py::test_no_benchmark_classes`: after setup neither benchmark class exists; `Graph.KG.Service` and `Graph.KG.FHIRGraphSyncTask` do

### Implementation (phase 7)

- [ ] T047 [US5] Move `Benchmark.cls` and `BenchFormat.cls` to `iris_src/dev/Graph/KG/`; `pyproject.toml` force-include stays `iris_src/src` only
- [ ] T048 [US5] A test helper `tests/helpers/dev_classes.py:deploy_dev_classes(conn)` that loads `iris_src/dev`; switch `tests/integration/test_bench_format.py`, `test_nkg_legacy_layout.py`, `test_236_token_write_paths.py`, `test_small_helper_classes.py` and `scripts/line_coverage/` to it; `docs/objectscript-coverage-floor.json` keeps covering them
- [ ] T049 [US5] `scripts/ci-parity.sh` wheel check: assert the two classes are absent

**Gate**: T045–T046 green; the four switched integration tests green.

---

## Phase 8: User Story 6 — a project that vendors IVG keeps working (P3)

### Tests first (phase 8)

- [ ] T050 [US6] E2E `tests/e2e/test_262_vendored.py`: `IVG262UPG` rebuilt at `v4.5.1`, plus a hand-made `User.funcSTRSPLIT` (opsreview's shape) and `SQLUser.CypherFn_IVGREGEXMATCH`; 4.6.0 deploy + `initialize_schema()`; IVG answers known Cypher; the hand-made class is byte-identical (`%Dictionary.ClassDefinition` text compared)

### Implementation (phase 8)

- [ ] T051 [US6] `docs/migration/v4.6.0.md` with a "For projects that vendor IVG's sources" section: every moved, renamed and retired object (from the manifest), `remove_legacy_objects`, grants, and "a namespace that stays on `Graph_KG` needs no SQL changes"
- [ ] T052 [US6] Release-time task (recorded in `PRE_RELEASE_CHECKLIST.md` §9): send opsreview an agent-bus message pointing to the notes; no edits in opsreview

**Gate**: T050 green.

---

## Phase 9: Inventory, docs and cross-cutting gates

- [ ] T053 [P] E2E `tests/e2e/test_262_inventory.py`, written before T054: a freshly initialized namespace's packages, tables, routines, views and globals compared with `docs/inventory.json` in both directions (0 differences, SC-002); fails today
- [ ] T054 Write the inventory table in `docs/ADMIN_GUIDE.md` (purpose, optional and how activated, in backup; `^IVG.AlgoCache`, `^ArnoAccel`, `^IVG.Deploy` included) and generate `docs/inventory.json` from it with `scripts/dev/gen_inventory_json.py`; a unit test checks the two agree
- [ ] T055 ADMIN_GUIDE: "One IVG Schema per namespace" (FR-020), choosing a name, the record, the two errors, the consolidation route T041 tests, `remove_legacy_objects`; README and USER_GUIDE mention `schema_prefix`/`IVG_SCHEMA_PREFIX` once and link there
- [ ] T056 CHANGELOG 4.6.0: spec 262 entries (Changed, with the two approved breaks called out; Added: `IVG_SCHEMA_PREFIX`, `remove_legacy_objects`, `initialize_schema(dry_run=)`, `status().schema`)
- [ ] T057 Full openCypher TCK: `scripts/tck/run_sharded.sh` then `scripts/tck/summarize.py diff` against the current `results.tsv`: 0 regressions, 0 missing (constitution VII); redeploy classes first (memory `tck_sharded_harness_traps`)
- [ ] T058 Speed gate `scripts/bench/schema_gate.py` (research R8): deploy `a42db224` to `IVG262BASE` (T002 with the commit), the 262 build to `IVGFIX` (`Graph_KG`) and `IVG262ALT` (`IVGALT`); interleaved, ≥5 runs, medians; FR-020a's paths; fails on any path > 5% slower; results under `~/.cache/ivg-bench/spec262/gate/` and summarized in the sign-off (SC-006a)
- [ ] T059 Full unit suite, full E2E suite, `scripts/ci-parity.sh`, `scripts/quickstart_e2e.py --isolated`, reader gate (`IVG_REQUIRE_ARNO=1 scripts/arno_reader_gate.py --check`, `tests/e2e/test_236_reader_gate.py`), `tests/e2e/test_upgrade_dry_run_2x.py`
- [ ] T060 Mark every task done here and run `/speckit.analyze`

---

## Dependencies & execution order

- Phase 1 → Phase 2 → everything else.
- US1 (Phase 3) before US3 (Phase 4): US3's path cases need the functions in the IVG Schema.
- US2 (Phase 5) needs T002/T003 and Phase 3. US4 (Phase 6) needs Phase 5's `initialize_schema(dry_run=)`.
- US5 (Phase 7) is independent after Phase 2. US6 (Phase 8) needs Phase 5.
- Phase 9: T053/T054 after Phases 3 and 7; T057 after Phases 3–4; T058 after all code phases; T059 last.

```text
P1 → P2 ─┬─ US1 → US3
         ├─ US1 → US2 → US4
         │          └─ US6
         └─ US5
all → T053–T060
```

## Parallel opportunities

- Phase 2 tests T004–T007 together; then T008–T010 (different files).
- US1 tests T012–T015 together. US3: T020 and T023 beside T021/T022.
- US5 (T045–T049) can run beside US2 once Phase 3 is in, since it touches different files.
- T003 beside T001/T002.

## Implementation strategy

MVP is Phase 1–3 (US1): fresh namespaces are clean, which is what an operator like Luca sees first. Then US3 and US2
(the other two P1 stories), US4, US5, US6, and Phase 9's gates. Nothing ships until T059 passes on the final commit,
as part of the 4.6.0 release.
