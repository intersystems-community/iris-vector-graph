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

- [x] T001 Create scratch namespaces `IVG262ALT`, `IVG262UPG`, `IVG262BASE` on `ivg-iris-enterprise` with their own databases (in `%SYS`: `Config.Databases.Create` and `SYS.Database.CreateDatabase` for `/usr/irissys/mgr/<ns>/`, `Config.Namespaces.Create` with that database for globals and routines, then `IVG_NAMESPACE=<NS> IVG_PORT=31972 scripts/enterprise-container.sh tcp-deploy`), via a script `scripts/dev/make_scratch_namespace.py <NS>` that is idempotent and refuses any name outside `IVG262*`
- [x] T002 Write `scripts/fixtures/build_release_namespace.py <tag> <NS>`: checks out `<tag>` into `~/.cache/ivg-bench/releases/<tag>` (git worktree), and with `PYTHONPATH` on that worktree runs its `initialize_schema()` in `<NS>` and seeds the graph from `tests/e2e/fixtures/old_releases.py`'s seed; prints what it created. Used by every upgrade fixture (US2, US4, US6 and the speed gate)
- [x] T003 [P] Write `scripts/dev/gen_legacy_manifest.py`: reads `iris_vector_graph/schema.py` and `iris_vector_graph/_engine/schema.py` at tags `v4.4.0`…`v4.5.1` (`git show <tag>:<path>`) and emits every `SQLUser` function, procedure and view those releases create, their `User.*` class names (IRIS mapping, research R2), plus `Graph.KG.Benchmark` and `Graph.KG.BenchFormat`, and cross-checks the result against a live namespace built at each tag with T002 (every function, procedure and view in `SQLUser`, every class in `User` and the top-level package that IVG created, including the shipped `User.CypherFn`, `User.funcSTRSPLIT`, `User.funcJSONKEYS`, `User.SQL`, `User.Exec`, `PageRankEmbedded`; research R6), failing on any difference, then emits it as the Python literal for `iris_vector_graph/legacy_manifest.py`

---

## Phase 2: Foundational (blocks every story)

**Purpose**: the name, the record, the error type, the two name helpers and the source rewrite.

### Tests first (phase 2)

- [x] T004 [P] Unit tests in `tests/unit/test_schema_name.py` for `resolve_schema_name(arg, env)`: argument wins over `IVG_SCHEMA_PREFIX`; `IRIS_SCHEMA_PREFIX` ignored; default `Graph_KG`; `ValueError` naming the rule for `""`, `1abc`, `a-b`, `SQLUser`, `sqluser`, `%X`; accepts `IVGALT`, `My_App`; the package mapping `MY_APP` → `MY.APP`; refusal of packages equal to or under `Graph.KG`, `IVG`, `IVGC` except `Graph_KG` itself
- [x] T005 [P] Unit tests in `tests/unit/test_fn_names.py`: `_fn("STR_SPLIT", "IVGALT") == "IVGALT.STR_SPLIT"`; `_fn` and `_table` agree on the schema; no two IVG function names compile to the same class name (`func` + name without `_`) and none equals an IVG table or view name
- [x] T006 [P] Unit tests in `tests/unit/test_source_rewrite.py`: `rewrite_sources(sources, "IVGALT")` over every packaged `.cls` (from `class_deploy.read_class_sources`) leaves 0 allow-listed `Graph_KG.<ivg object>` and `Graph.KG.<table or function class>` references, changes 0 references to code classes (`Graph.KG.Eraser`, `Graph.KG.Adjacency`, …: every class in `iris_src/src`), is the identity for `Graph_KG`, and changes the fingerprint when the schema changes
- [x] T007 [P] Unit tests in `tests/unit/test_schema_record.py` (mocked native object that records `get`/`set`, no `create=True`): `read_record` returns `None` when `^IVG.Deploy("schema")` is absent; `write_record` sets name and `("schema","recorded")`; `IVGSchemaError` is a `RuntimeError` exported from `iris_vector_graph`, and its two messages carry the strings in contracts/python-api.md

### Implementation (phase 2)

- [x] T008 Create `iris_vector_graph/schema_name.py`: `resolve_schema_name`, `schema_package`, `validate_schema_name`, `read_record`, `write_record`, `IVGSchemaError` (re-exported in `iris_vector_graph/__init__.py`); make T004 and T007 pass
- [x] T009 Add `_fn(name, schema=None)` beside `_table()` in `iris_vector_graph/cypher/translator.py`; make T005 pass
- [x] T010 Add `rewrite_sources(sources, schema)` to `iris_vector_graph/_engine/class_deploy.py` with the allow-list from research R1 (IVG table names, function and procedure names, table-class and function-class names), and apply it in `deploy_packaged_classes` before `upload_and_compile`, with the fingerprint taken over the rewritten text; make T006 pass
- [x] T011 `IRISGraphEngine.__init__` in `iris_vector_graph/engine.py`: `schema_prefix: Optional[str] = None` resolved by `resolve_schema_name`, validated before any database call; existing `schema_prefix="Graph_KG"` callers unchanged (unit test added to `tests/unit/test_schema_prefix_isolation.py`)

**Gate**: `tests/unit/test_schema_name.py test_fn_names.py test_source_rewrite.py test_schema_record.py test_schema_prefix_isolation.py` green; full unit suite green.

---

## Phase 3: User Story 1 — an operator finds all of IVG in one schema (P1) 🎯 MVP

**Goal**: a fresh namespace has every IVG function, procedure and table in the IVG Schema and nothing in `SQLUser` or `User`.
**Independent test**: initialize a fresh namespace and list its schemas, routines, views and classes.

### Tests first (phase 3)

- [x] T012 [P] [US1] Unit test `tests/unit/test_no_sqluser_refs.py`: grep of `iris_vector_graph/` for `SQLUser.` and for unqualified `FROM rdf_edges|rdf_props|rdf_labels|nodes` finds nothing outside `legacy_manifest.py` and the legacy-removal code
- [x] T013 [P] [US1] E2E `tests/e2e/test_262_fresh_namespace.py` in a freshly emptied `IVG262ALT` (schema `Graph_KG` for this file): after `initialize_schema()`, `INFORMATION_SCHEMA.ROUTINES` and `.VIEWS` show 0 IVG objects in `SQLUser`, `%Dictionary.ClassDefinition` shows 0 IVG classes in `User`; every IVG function is in `Graph_KG`; an application's own `SQLUser.RAND` and `User.funcRAND` created before setup are unchanged after it; no IVG class exists without a package
- [x] T014 [P] [US1] E2E in the same file: one Cypher query per helper family the translator emits (`CY_*`, `CypherFn_*`, JSON, list, string, regex, temporal, VLP) on a hand-built graph returns its hand-worked answer, and `IVGResult.sql` names no object outside `Graph_KG`
- [x] T015 [P] [US1] E2E: text search (`text_search.py` paths) and the Python PPR fallback give their known answers on a namespace with no `SQLUser` views
- [x] T016 [P] [US1] Integration test `tests/integration/test_function_placement.py`: for every function the translator can emit (from `_fn` call sites), `INFORMATION_SCHEMA.ROUTINES` shows it in the IVG Schema, and a direct `SELECT Graph_KG.<fn>(…)` returns a hand-worked value
- [x] T017 [P] [US1] Unit test `tests/unit/test_class_packages.py`: every packaged `.cls` (`class_deploy.read_class_sources`) is in `Graph.KG` or `IVG`; none in `User`, none without a package; fails today on 6 classes (research R10)

### Implementation (phase 3)

- [x] T018 [US1] `iris_vector_graph/schema.py` and `iris_vector_graph/_engine/schema.py`: every `CREATE [OR REPLACE] FUNCTION|PROCEDURE` names the IVG Schema (`self._schema`); remove the four `CREATE VIEW SQLUser.*` and `RDF_EDGES_COMPAT_VIEW_DDL` re-creation (the 3.2.0 rescue still drops a legacy view when present)
- [x] T019 [US1] `iris_vector_graph/cypher/translator.py`: replace all 102 `SQLUser.` references with `_fn(...)` under the engine's schema; `iris_vector_graph/_engine/query.py`, `prop_values.py`, `schema.py` likewise
- [x] T020 [US1] `iris_vector_graph/text_search.py` (lines 106, 161, 209, 210) and the PPR fallback: qualify through `_table()`
- [x] T021 [US1] Native executor and compiled plans (`Graph.KG.Cypher.Gen`, spec 257): plan text names functions through the rewritten classes; check `IVGC.P<n>` plans compile in `Graph_KG` with no `SQLUser` reference
- [x] T022 [US1] Move `iris_src/src/User.CypherFn.cls`, `User.funcSTRSPLIT.cls`, `User.funcJSONKEYS.cls` to `iris_src/src/Graph/KG/CypherFn.cls`, `StrSplit.cls`, `JsonKeys.cls` (class names `Graph.KG.CypherFn`, `Graph.KG.StrSplit`, `Graph.KG.JsonKeys`), keeping each `SqlName`; add them to the R1 rewrite allow-list as function classes; point every translator `SQLUser.CypherFn_*` reference at `_fn("CypherFn_…")`
- [x] T023 [US1] Move `iris_src/src/User.SQL.cls` and `iris_src/src/PageRankEmbedded.cls` to `iris_src/src/Graph/KG/SQL.cls` and `Graph/KG/PageRankEmbedded.cls`; update every caller found by `grep -rn "User.SQL\|PageRankEmbedded" iris_vector_graph iris_src tests scripts`

**Gate**: T012–T017 green; `tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match` green in `Graph_KG`.

---

## Phase 4: User Story 3 — a user chooses the schema name (P1)

**Goal**: with `schema_prefix="IVGALT"`, every IVG feature works and no `Graph_KG` schema exists.
**Independent test**: one E2E case per write, delete and read path in `IVG262ALT`.

### Tests first (phase 4)

- [x] T024 [P] [US3] Unit tests `tests/unit/test_schema_env.py`: `IVG_SCHEMA_PREFIX=ENVNAME` with `schema_prefix="ARGNAME"` → `ARGNAME`; with `IRIS_SCHEMA_PREFIX=RAG` set and no IVG variable → `Graph_KG`
- [x] T025 [US3] E2E `tests/e2e/test_262_alt_schema_paths.py`, namespace `IVG262ALT`, schema `IVGALT`: setup leaves no `Graph_KG` schema and every IVG table, function and procedure in `IVGALT`; the deployed classes contain 0 surviving `Graph_KG.`/table-class references (SC-004, read from `%Dictionary.CompiledMethod` implementations)
- [x] T026 [US3] Same file: one case per path, each asserting database state and a known answer, enumerated from `tests/integration/test_236_token_write_paths.py` and the graph-scope tests: node and edge create (row and bulk), property and label set/remove, `MERGE`, `DETACH DELETE`, `delete_nodes`, `erase_graph`, bare-node retire, temporal edge write and window read, `drop_temporal_structural_rows`, ledger commit/verify/redact, embeddings store and KNN, per-graph routed embeddings, BM25 text search, PageRank/PPR/WCC/CDLP/betweenness, BFS and shortest path, snapshot save/restore, FHIR graph build
- [x] T027 [P] [US3] Direction-symmetry gate in `IVGALT`: parametrize `tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match` over `Graph_KG` and `IVGALT`
- [x] T028 [US3] E2E in `tests/e2e/test_262_alt_schema_paths.py`: `initialize_schema()` run by a user without the privilege to create a schema raises one error naming the privilege, and the namespace snapshot is unchanged
  Red at `79b2dd58`: setup logged a warning per statement, left an empty `IVGALT` schema (CREATE SCHEMA needs no privilege) and raised an unrelated procedure RuntimeError. Green: one `IVGSchemaError` naming user, schema and `%CREATE_TABLE`; snapshot unchanged.

### Implementation (phase 4)

- [x] T029 [US3] Thread the engine's schema through every Python store and mixin that still reads the module global or the literal `Graph_KG` (`grep -rn '"Graph_KG' iris_vector_graph`), until T025–T027 pass
- [x] T030 [US3] Package-clash refusal at `initialize_schema()` (research R2): a schema whose package holds a class IVG did not create raises `IVGSchemaError` naming the class (E2E case in T025's file)

**Gate**: T024–T028 green.

---

## Phase 5: User Story 2 — an existing namespace upgrades without breaking anyone's SQL (P1)

**Goal**: a 4.5.1 namespace upgrades; legacy objects and their grants keep working; `remove_legacy_objects` lists and removes exactly the manifest.
**Independent test**: build `IVG262UPG` with T002 at `v4.5.1`, upgrade, run legacy SQL, list, remove.

### Tests first (phase 5)

- [x] T031 [P] [US2] Unit test `tests/unit/test_legacy_manifest.py`: `iris_vector_graph/legacy_manifest.py` equals `scripts/dev/gen_legacy_manifest.py`'s output from the tags; contains no pattern entries; includes both benchmark classes
- [x] T032 [P] [US2] Unit tests `tests/unit/test_remove_legacy_objects.py` (mocked cursor and native): `dry_run=True` issues no DDL; only manifest names are listed; a present `SQLUser.CY_MINE` is never listed
- [x] T033 [US2] E2E `tests/e2e/test_262_upgrade_451.py` on `IVG262UPG` built from `v4.5.1`: after 4.6.0 `initialize_schema()`, Cypher answers from the IVG Schema, `SELECT SQLUser.STR_SPLIT(...)` and the other legacy calls still answer as before (including on the same connection that just ran the engine's Cypher), one log line names the leftovers
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): fixture error, `initialize_schema() got an unexpected keyword argument 'dry_run'`.
- [x] T034 [US2] Same file: grants. Before upgrade, `CREATE ROLE ivg262_app`, `GRANT EXECUTE` on the 4.5.1 functions Cypher needs, and a user with only that role and `SELECT` on the tables; a Cypher query as that user works before and after the upgrade; `initialize_schema(dry_run=True)` lists each grant it would copy and the snapshot is unchanged
  Before: v4.5.1's own engine (`build_release_namespace.run_cypher`) as `ivg262_appuser` (`SQLUser.CY_CMP`); after: 4.6.0's (`Graph_KG.CY_CMP`, the copied grant). Control user with SELECT alone: a function-free query answers, the CY_CMP query is refused (-99).
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): fixture error, `initialize_schema() got an unexpected keyword argument 'dry_run'`.
- [x] T035 [US2] Same file: `remove_legacy_objects()` returns exactly the manifest objects present (and an application `SQLUser.CY_MINE` is absent from it); `dry_run=False` removes exactly those; whole-namespace snapshot (`tests/e2e/graph_snapshot.py` plus the routine/class lists) differs only by them
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): fixture error, `initialize_schema() got an unexpected keyword argument 'dry_run'`.
- [ ] T036 [US2] Same file, second fixture: a namespace built at `v3.2.0`, upgraded through `v4.5.1` (T002 twice), then to 4.6.0, ends in the same state as T033
  OPEN, not spec 262: `xfail(strict, raises=RuntimeError)`. v4.5.1's `upgrade_to_4_0_0` (and 4.6.0's) fails on the 3.2.0 seed: SQLCODE -127 `fk_edges_source` (GRAPH_ID=acme, S=n5), an acme edge whose endpoints 3.2.0 left in the default graph only. Without the migration the 3.2.0 rows stay graph-less and the comparison query answers Dov/Eve as well.
- [x] T037 [US2] Same file: a native plan compiled before the upgrade is gone after it (FR-014)
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): fixture error, `initialize_schema() got an unexpected keyword argument 'dry_run'`.
- [x] T038 [US2] Same file: an upgrade interrupted after the IVG Schema's functions are created and before the record is written (a raise injected through a test hook `iris_vector_graph._engine.schema._UPGRADE_HOOK`, added in T043) resumes on the next `initialize_schema()` and ends in the same snapshot as an uninterrupted upgrade
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): `DID NOT RAISE RuntimeError` (no `_UPGRADE_HOOK`).
- [x] T039 [P] [US2] Integration test `tests/integration/test_grant_copy.py`: at the SQL layer, after `GRANT EXECUTE` on a legacy function to a role, the copy routine grants exactly the `Direct` rows on the replacement and never the `SuperUser` or `Owner Privilege` rows, read through `%Library.SQLCatalogPriv:SQLAllPrivileges` before and after
  Red at `fd3789a9` (release-4.6.0 before spec 262, `~/.cache/ivg-bench/262b-pre`, 2026-11-03): collection error, `No module named 'iris_vector_graph._engine.legacy'`.

### Implementation (phase 5)

- [x] T040 [US2] Generate `iris_vector_graph/legacy_manifest.py` with T003's script; make T031 pass
- [x] T041 [US2] Grant copy in `iris_vector_graph/_engine/schema.py`: read `%Library.SQLCatalogPriv:SQLAllPrivileges` per legacy object, keep `GRANTED_VIA = Direct`, grant on the replacement (`%SYSTEM.SQL.Security.GrantPrivilege[WithGrant]`), report in `grants_copied`; under `dry_run` report only
- [x] T042 [US2] `engine.remove_legacy_objects(dry_run=True)` in `iris_vector_graph/_engine/admin.py` per contracts/python-api.md; make T032 and T035 pass
- [x] T043 [US2] `initialize_schema(dry_run=False)` signature, the `_UPGRADE_HOOK` test seam, and the new result keys (`schema`, `schema_recorded`, `legacy_objects`, `grants_copied`, `dry_run`); dry run proven read-only

**Gate**: T031–T039 green.

---

## Phase 6: User Story 4 — the namespace's schema is fixed once recorded (P2)

**Goal**: record, infer, refuse; a mismatched engine and a not-yet-upgraded namespace both get one clear error.
**Independent test**: record states on `IVG262UPG` and `IVG262ALT`.

### Tests first (phase 6)

- [ ] T044 [P] [US4] Unit tests `tests/unit/test_first_access.py`: the first query of an engine whose schema differs from the record raises `IVGSchemaError` before any SQL is sent (mock cursor sees nothing); with no record and no 4.6.0 objects, the FR-006a message; with a matching record, no extra round trip after the first
- [ ] T045 [US4] E2E `tests/e2e/test_262_record.py`: new namespace records `Graph_KG`; 4.5.1 namespace with all tables in `RAG` (T002 with `schema_prefix="RAG"`) is inferred as `RAG` and the ObjectScript features then read `RAG` (erase and temporal write give known answers); a namespace with tables in `Graph_KG` and `RAG` stops with both schemas and row counts and records nothing; `initialize_schema(schema_prefix="RAG")` then records `RAG` and reports the `Graph_KG` tables untouched; a namespace recorded as `ivgalt` accepts an engine built with `IVGALT` and refuses one built with `IVGALT2`; each dry run proven read-only
- [ ] T046 [US4] Same file: an engine with `schema_prefix="RAG"` on a namespace recorded `Graph_KG` raises on first access, and no row or global changed (snapshot)
- [ ] T047 [US4] E2E `tests/e2e/test_262_not_upgraded.py`: a `v4.5.1` namespace and the 4.6.0 package, no `initialize_schema()`: the first Cypher query raises the FR-006a error, not a missing-function error; `SELECT SQLUser.STR_SPLIT(...)` still answers
- [ ] T048 [US4] Same file: the manual consolidation route from ADMIN_GUIDE (record one schema, move or drop the other's IVG tables with the SQL ADMIN_GUIDE gives) run on a two-schema namespace ends with one schema and the graph's known answers
- [ ] T049 [P] [US4] Integration test `tests/integration/test_schema_record.py`: record, read and compare names regardless of case, and refuse a package clash, against a live namespace using direct SQL and globals only (no engine)

### Implementation (phase 6)

- [ ] T050 [US4] Record, infer and refuse in `initialize_schema()` (`iris_vector_graph/_engine/schema.py`) per data-model.md's transitions
- [ ] T051 [US4] First-access check in the engine's connection path (`iris_vector_graph/engine.py` / `stores/iris_sql_store.py`): read `^IVG.Deploy("schema")` once per connection; raise `IVGSchemaError` per contracts/python-api.md
- [ ] T052 [US4] `status()` gains `schema` and `legacy_objects`; `report()` prints both (`iris_vector_graph/status.py`, `_engine/admin.py`); unit test in `tests/unit/test_status_schema.py` written first

**Gate**: T044–T049 green.

---

## Phase 7: User Story 5 — production namespaces carry no benchmark code (P2)

### Tests first (phase 7)

- [ ] T053 [P] [US5] Unit test `tests/unit/test_wheel_contents.py`: the built wheel holds no `Graph/KG/Benchmark.cls`, `BenchFormat.cls` or `User.Exec.cls`, and holds `Service.cls` and `FHIRGraphSyncTask.cls`
- [ ] T054 [US5] E2E `tests/e2e/test_262_fresh_namespace.py::test_no_benchmark_classes`: after setup neither benchmark class exists; `Graph.KG.Service` and `Graph.KG.FHIRGraphSyncTask` do

### Implementation (phase 7)

- [ ] T055 [US5] Move `Benchmark.cls` and `BenchFormat.cls` to `iris_src/dev/Graph/KG/` and `User.Exec.cls` to `iris_src/dev/`; `pyproject.toml` force-include stays `iris_src/src` only
- [ ] T056 [US5] A test helper `tests/helpers/dev_classes.py:deploy_dev_classes(conn)` that loads `iris_src/dev`; switch `tests/integration/test_bench_format.py`, `test_nkg_legacy_layout.py`, `test_236_token_write_paths.py`, `test_small_helper_classes.py`, `tests/e2e/test_253_ordered_trim_e2e.py` and `scripts/line_coverage/` to it; `docs/objectscript-coverage-floor.json` keeps covering them
- [ ] T057 [US5] `scripts/ci-parity.sh` wheel check: assert the three classes (`Benchmark`, `BenchFormat`, `User.Exec`) are absent

**Gate**: T053–T054 green; every test switched to the dev-classes helper green.

---

## Phase 8: User Story 6 — a project that vendors IVG keeps working (P3)

### Tests first (phase 8)

- [ ] T058 [US6] E2E `tests/e2e/test_262_vendored.py`: `IVG262UPG` rebuilt at `v4.5.1`, plus a hand-made `User.funcSTRSPLIT` (opsreview's shape) and `SQLUser.CypherFn_IVGREGEXMATCH`; 4.6.0 deploy + `initialize_schema()`; IVG answers known Cypher; the hand-made class is byte-identical (`%Dictionary.ClassDefinition` text compared)

### Implementation (phase 8)

- [ ] T059 [US6] `docs/migration/v4.6.0.md` with a "For projects that vendor IVG's sources" section: every moved, renamed and retired object (from the manifest), `remove_legacy_objects`, grants, and "a namespace that stays on `Graph_KG` needs no SQL changes"
- [ ] T060 [US6] Release-time task (recorded in `PRE_RELEASE_CHECKLIST.md` §9): send opsreview an agent-bus message pointing to the notes; no edits in opsreview

**Gate**: T058 green.

---

## Phase 9: Inventory, docs and cross-cutting gates

- [ ] T061 [P] E2E `tests/e2e/test_262_inventory.py`, written before T062: a freshly initialized namespace's packages, tables, routines, views and globals compared with `docs/inventory.json` in both directions (0 differences, SC-002); fails today
- [ ] T062 Write the inventory table in `docs/ADMIN_GUIDE.md` (purpose, optional and how activated, in backup; `^IVG.AlgoCache`, `^ArnoAccel`, `^IVG.Deploy` included; classes skipped on servers without embedded Python or `%AI` listed with the reason) and generate `docs/inventory.json` from it with `scripts/dev/gen_inventory_json.py`; a unit test checks the two agree
- [ ] T063 ADMIN_GUIDE: "One IVG Schema per namespace" (FR-020), choosing a name, the record, the two errors, the consolidation route T048 tests, `remove_legacy_objects`; README and USER_GUIDE mention `schema_prefix`/`IVG_SCHEMA_PREFIX` once and link there
- [ ] T064 CHANGELOG 4.6.0: spec 262 entries (Changed, with the two approved breaks called out; Added: `IVG_SCHEMA_PREFIX`, `remove_legacy_objects`, `initialize_schema(dry_run=)`, `status().schema`)
- [ ] T065 Full openCypher TCK: `scripts/tck/run_sharded.sh` then `scripts/tck/summarize.py diff` against the current `results.tsv`: 0 regressions, 0 missing (constitution VII); redeploy classes first (memory `tck_sharded_harness_traps`)
- [ ] T066 Speed gate `scripts/bench/schema_gate.py` (research R8): deploy `a42db224` to `IVG262BASE` (T002 with the commit), the 262 build to `IVGFIX` (`Graph_KG`) and `IVG262ALT` (`IVGALT`); interleaved, ≥5 runs, medians; FR-020a's paths; fails on any path > 5% slower; results under `~/.cache/ivg-bench/spec262/gate/` and summarized in the sign-off (SC-006a)
- [ ] T067 Full unit suite, full E2E suite, `scripts/ci-parity.sh`, `scripts/quickstart_e2e.py --isolated`, reader gate (`IVG_REQUIRE_ARNO=1 scripts/arno_reader_gate.py --check`, `tests/e2e/test_236_reader_gate.py`), `tests/e2e/test_upgrade_dry_run_2x.py`
- [ ] T068 Mark every task done here and run `/speckit.analyze`

---

## Dependencies & execution order

- Phase 1 → Phase 2 → everything else.
- US1 (Phase 3) before US3 (Phase 4): US3's path cases need the functions in the IVG Schema.
- US2 (Phase 5) needs T002/T003 and Phase 3. US4 (Phase 6) needs Phase 5's `initialize_schema(dry_run=)`.
- US5 (Phase 7) is independent after Phase 2. US6 (Phase 8) needs Phase 5.
- Phase 9: T061/T062 after Phases 3 and 7; T065 after Phases 3–4; T066 after all code phases; T067 last.

```text
P1 → P2 ─┬─ US1 → US3
         ├─ US1 → US2 → US4
         │          └─ US6
         └─ US5
all → T061–T068
```

## Parallel opportunities

- Phase 2 tests T004–T007 together; then T008–T010 (different files).
- US1 tests T012–T017 together. US3: T024 and T027 beside T025/T026/T028.
- US5 (T053–T057) can run beside US2 once Phase 3 is in, since it touches different files.
- T003 beside T001/T002.

## Implementation strategy

MVP is Phase 1–3 (US1): fresh namespaces are clean, which is what an operator like Luca sees first. Then US3 and US2
(the other two P1 stories), US4, US5, US6, and Phase 9's gates. Nothing ships until T067 passes on the final commit,
as part of the 4.6.0 release.
