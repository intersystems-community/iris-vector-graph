<!-- markdownlint-disable MD013 -->

# Tasks: IVG 4.6.0 (workstreams A, C, D, E, F)

**Input**: `specs/release-4.6.0/plan.md`; the newcomer report `~/.cache/ivg-bench/novice/REPORT.md` (its friction-log
numbers are cited as `NR#`). Workstream B is `specs/262-ivg-owned-sql-schema/tasks.md`.
**Tests**: first in every task group; each fix records its red run in the commit message. Environment as in spec 262's
tasks (enterprise container, port 31972, `IVGFIX`/`IVGSEC`; doc tests use `IVG460DOC`).

## Format: `[ID] [P?] [Stream] Description`

Streams: **FIX** (A), **DOC** (C), **DT** (D), **NW** (E), **REL** (F). IDs are `R###` so they never collide with spec
262's `T###`.

---

## Setup

- [x] R001 [REL] Create branch `release-4.6.0` from `262-ivg-owned-sql-schema`; `pyproject.toml` version `4.6.0`; retitle the CHANGELOG section `### v4.5.2 (2026-11-03)` to `### v4.6.0 (unreleased)` and note in its intro that 4.5.2 was never published
- [x] R002 [P] [DT] Create scratch namespace `IVG460DOC` with spec 262's `scripts/dev/make_scratch_namespace.py` (extend its allow-list to `IVG460*`)

---

## Workstream A: the six walkthrough fixes (FIX)

### A1. `shortestPath` keeps aliases and every column (NR8)

- [x] R003 [FIX] E2E `tests/e2e/test_shortest_path_projection_e2e.py`, written first, on a hand-built 12-node graph (the walkthrough's A–L): `MATCH p = shortestPath((a {name:'A'})-[*]->(b {name:'I'})) RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names` returns columns `['len','names']` and `[[5, ['A','F','E','G','H','I']]]`; also `RETURN [n IN nodes(p) | n.name] AS names`, `RETURN p`, `RETURN length(p)`, `RETURN relationships(p)`, and `RETURN nodes(p) AS ns, 1 AS one`, each with its hand-worked columns and rows. Red on the current code
- [x] R004 [FIX] Unit tests in `tests/unit/test_shape_shortest_path.py` for `_engine/query.py:_shape_shortest_path`: a RETURN with anything beyond bare `length/nodes/relationships(p)` goes to the general projection; aliases are kept
- [x] R005 [FIX] Fix `_shape_shortest_path` (`iris_vector_graph/_engine/query.py:2713`): project from the path's node and edge lists through the general RETURN evaluator; make R003–R004 pass; full TCK `clauses/match` and `expressions/path` areas green

### A2. `NKGAccelLoader.Load` returns 0 instead of throwing (NR2)

- [x] R006 [P] [FIX] Integration test `tests/integration/test_nkg_loader_missing_library.py`, written first: `Graph.KG.NKGAccelLoader.Load("/usr/irissys/mgr/no-such.so")` returns `0`, `IsLoaded()` returns `0`, and the process can still run a following classmethod (red today: uncaught `<DYNAMIC LIBRARY LOAD>`)
- [x] R007 [FIX] Wrap `$ZF(-4, 1, libPath)` in `Try/Catch` in `iris_src/src/Graph/KG/NKGAccelLoader.cls:15`, returning 0 and recording the error text in `^||NKGAccel("loadError")`; redeploy with `tcp-deploy`; R006 green

### A3. The "Acceleration" label tells the truth (NR7)

- [x] R008 [P] [FIX] Unit tests `tests/unit/test_status_acceleration_label.py`, written first: with `arno.loaded=False` the report line is `Acceleration:    ObjectScript (Arno not loaded)`; with Arno loaded, `Acceleration:    Arno <version>`
- [x] R009 [FIX] `iris_vector_graph/status.py:197` (`accel_label`); R008 green

### A4. `kg_edge_count` counts edges (NR7)

- [x] R010 [P] [FIX] E2E `tests/e2e/test_kg_edge_count_e2e.py`, written first: 14 edges from 13 sources (two edges out of one node, one parallel pair) gives `status().adjacency.kg_edge_count == 14`, equal to `SELECT COUNT(*) FROM Graph_KG.rdf_edges` (red today: 13)
- [x] R011 [FIX] `Graph.KG.TraversalBuild.KGEdgeCount` (`iris_src/src/Graph/KG/TraversalBuild.cls:969`): count leaves (parallel edges by multiplicity), keeping `maxCount`; R010 green; ADMIN_GUIDE §7 row unchanged

### A5. Setup is quiet and the namespace comes from the connection (NR6, NR11, NR18)

- [x] R012 [P] [FIX] Unit tests `tests/unit/test_setup_logging.py`, written first, capturing `caplog` over a mocked `initialize_schema()` on Community-like capabilities (no `%AI`, no Arno): exactly one INFO summary line (namespace, IVG Schema, classes deployed, classes skipped with reasons); 0 WARNING lines; no line names `_call_classmethod`; no "Namespace mismatch"
- [x] R013 [P] [FIX] E2E `tests/e2e/test_setup_logging_e2e.py`: a real `initialize_schema()` in `IVG460DOC` logs 0 WARNINGs and no `_iris_ep` "Returning a mock object" line; a second run logs the same summary
- [x] R014 [FIX] `iris_vector_graph/stores/iris_sql_store.py` (~line 471): take the namespace from the connection (`%SYSTEM.SYS.NameSpace()`), drop the mismatch warning, and stop caching `_arno_available` before the deploy; `_engine/schema.py` (~2127): drop the BuildKG warning when the adjacency index is declared; MCP and Arno-absent messages to INFO, once; silence the embedded-python wrapper's mock warnings outside embedded mode (`iris_vector_graph/_iris_compat` or wherever the wrapper is imported); R012–R013 green

### A6. No IPM/ZPM claim (NR13)

- [x] R015 [P] [FIX] Unit test `tests/unit/test_no_ipm_claim.py`, written first: no `module.xml` or `module-core.xml` at the repo root; README has no "IPM"/"ZPM" row
- [x] R016 [FIX] Delete `module.xml` and `module-core.xml`; remove the README feature-table row; CHANGELOG "Removed" entry; R015 green

### A7. Embedded mode outside IRIS fails loudly (found by the doc-test baseline)

- [x] R056 [FIX] Test first: outside IRIS, `IRISGraphEngine(EmbeddedConnection())` and its `initialize_schema()` raise a clear error naming irispython; no MagicMock from the embedded wrapper reaches IVG code. Then the fix (overlaps R014's wrapper-warning work)

### A8. Silent wrong answers and a cross-graph delete (KNOWN_ISSUES triage; Tom, 2026-11-03: fix in 4.6.0)

- [x] R057 [FIX] N1, test first: on the chain `uz0-NEXT->uz1-NEXT->uz2-NEXT->uz3`, `MATCH (a {node_id:'uz0'})-[:NEXT*1..3]-(b) RETURN count(DISTINCT b)` answers 3, `RETURN DISTINCT b.node_id` answers `uz1, uz2, uz3`, and `*2..2` answers only `uz2` (today 4 / includes `uz0`). The BFS routes (`_vlp_bfs_answers`) must not walk the arriving relationship back (openCypher relationship uniqueness per path); add cases with a real cycle so a node reached by a different relationship still counts
- [x] R058 [FIX] #80, test first: on `vl0->vl1->vl2` plus `vl0->vl2`, `MATCH (s {node_id:'vl0'})-[*1..2]->(m) RETURN m.id` answers column `m.id` with one row per path (`vl1`, `vl2`, `vl2`), not `id, hops, pred`; the same for `m.node_id` and `RETURN DISTINCT m.node_id` (`vl1`, `vl2`)
- [x] R059 [FIX] #112, test first: `delete_node(id)` / `bulk_delete_nodes(ids)` without `graph=` raise `ValueError` naming the graphs when an id is held by more than one graph, and delete nothing; with one holding graph they delete there as today; `graph=None` and `graph='<g>'` unchanged. CHANGELOG notes it under Changed
- [x] R060 [FIX] `tests/integration/test_list_error_decode_live.py:39,45` opens its second connection in `IVG_NAMESPACE`, not `USER`; `tests/integration/test_nodes_edges_deep.py:188` asserts the message the code emits now (triage N2)

- [x] R061 [FIX] `shortestPath` with an incoming pattern (`(a)<-[*]-(b)`) returns no rows (found while fixing R005; also on 4.5.x). Test first on the walkthrough's 12-node graph: the incoming form returns the mirror of the outgoing path with its hand-worked length and nodes

- [x] R062 [FIX] A relationship property read is `NULL` on the SQL route: `MATCH (a:X)-[r:R]->(b) RETURN r.w AS w` translates to `SELECT NULL AS w` (also unaliased, untyped `[r]`, and right after `CREATE ... [r:R {w: 5}] ... RETURN r.w`), while `properties(r)` answers `{"w":5}` and `rdf_edges.qualifiers` holds `{"w": "5"}`. Found by spec 262's agent, confirmed by the lead on `release-4.6.0` (also v4.5.1). Test first (E2E with hand-worked values, integer and string properties, typed and untyped relationships), then fix; find why the TCK does not catch it and say so
- [x] R063 [FIX] `MATCH ...-[r]->... SET r.z = 7 RETURN r.z` returns two rows for one relationship (spec 262 agent, v4.5.1). Test first, then fix
- [ ] R064 [FIX] The `LIST_HEAD` and `LIST_LAST` SQL function bodies fail when called (the translator never calls them): test each function directly, then fix or remove them (and from the legacy manifest's replacement map if removed)
- [x] R065 [FIX] `engine.algorithms.execute_wcc` forwards to a method the engine does not have: test first (it answers the hand-worked components of a two-component graph), then fix

- [x] R067 [FIX] `engine.cypher.execute_bfs` and `execute_knn_vec` forward to methods the engine does not have (fixes2's R065 follow-up): test first (each answers a hand-worked BFS / KNN result), then fix
- [x] R068 [FIX] The `[:R*2]` fast paths (`KHop2*`) name their column `node_id` and return distinct nodes instead of the RETURN's column and one row per path (same class as #80): test first, then fix
- [x] R069 [FIX] `WHERE r.ts = 100` on a labelled pattern fails with "Incorrect number of parameters" (temporal route; also 4.5.x): test first, then fix
- [x] R070 [FIX] Check `approx_count_distinct`'s HLL route on an undirected variable-length walk against relationship uniqueness (N1's rule): a test with the hand-worked count; fix if it differs
- [ ] R071 [DOC] KNOWN_ISSUES: an unaliased, non-DISTINCT variable-length read now lists every path (correct, per openCypher); on a hub it can reach IRIS's string limit where 4.5.x returned fewer, wrong rows. Document the limit and the `DISTINCT`/`count()` alternatives, unless the 262 speed gate (T066) shows it needs fixing

- [x] R072 [DT] The doc-test reset kills every global in its scratch namespace, `^NKG` included; use `Graph.KG.Traversal.DropNKG` (or the engine's own drop) for `^NKG`, never a raw kill (memory: Arno callout defects), test first (fixes3, open question 3)
- [x] R073 [FIX] The store's `execute_knn_vec` (and so `CALL ivg.vector.knn`) reads `kg_NodeEmbeddings` and ignores per-graph/model embedding routes, which `kg_KNN_VEC` follows: test first with a routed graph (hand-worked nearest neighbours), then fix (fixes3, Q1)
- [x] R074 [FIX] The 1-hop ids shortcut returns one row per target instead of one per relationship in a multigraph default graph: test first (two parallel edges give two rows), then fix (fixes3, Q2)
- [x] R075 [FIX] `tests/unit/test_edge_embeddings.py::test_embed_edges_all_default` failed once in a full run with `<ILLEGAL VALUE>%SQLInsert+27^Graph.KG.nodes.1` and passed alone: find the order dependence and fix it (test or code)

- [ ] R076 [FIX] The 4.0.0 migration (in 4.5.1 and 4.6.0) fails on a 3.2.0 install whose named-graph edge has endpoints only in the default graph: SQLCODE -127 on `fk_edges_source` (the `acme` edge n5->n6 of the 3.2.0 fixture). Blocks spec 262 T036. Test first (the 3.2.0 fixture upgrades; the edge's endpoints exist in its graph afterwards, as `create_edge` has registered them since 4.0.0), then fix the migration
- [ ] R077 [FIX] In `IVGALT`, `test_262_alt_schema_paths.py::test_pagerank_ppr_and_betweenness` and `::test_bfs_and_shortest_path` fail in-process and pass from a fresh process (likely the in-process Arno/^NKG cache keyed without the schema). Also failing before spec 262's last commits. Find the cache, key it correctly, both tests green in one process
- [ ] R078 [FIX] `Graph.KG.PyOps` calls `CALL kg_KNN_VEC` and `CALL kg_RRF_FUSE` unqualified, which resolve in `SQLUser` where no such procedure exists: test first (each PyOps entry answers a hand-worked result), then qualify through the IVG Schema
- [ ] R079 [FIX] `api/gql/engine.py` and `api/gql/resolvers.py` name `Graph_KG` directly; GraphQL on a non-default IVG Schema reads the wrong tables: test first in `IVGALT`, then route through the engine's schema
- [x] R080 [FIX] Retire `iris.vector.graph.GraphOperators` (nothing calls its SQL procedures `iris_vector_graph.kg_TXT/kg_KNN_VEC/kg_RRF_FUSE`; only tests call its methods): remove it from the wheel like the benchmark classes, add it to the legacy manifest's retired classes, move its tests to the dev-classes helper or delete them with the reason in the commit
  Moved to `iris_src/dev/iris/vector/graph/GraphOperators.cls` with the benchmark classes and `User.Exec` (spec 262 T055); on the legacy manifest (`RETIRED_CLASSES`). Tests: `test_graph_operators_sql.py` deploys it from iris_src/dev (module-scoped, removed afterwards); `TestGraphOperatorsClass` in test_objectscript_classes.py deleted (it duplicated test_graph_operators_sql and skipped when the class was absent); `test_227_objectscript_surface::test_the_legacy_knn_procedure_cannot_cross_graphs` deleted (it guarded a procedure that no longer ships); `test_ppr_cls_fast_path::test_graphoperators_deployed` now asserts it is not deployed; test_cls_layer no longer skips on it. Red/green: test_wheel_contents and test_262_fresh_namespace::test_no_benchmark_classes (T053, T054).
- [ ] R081 [FIX] `_native_privileges` always answers True (`%CHECKPRIV` reports SQLCODE 100 without raising and DB-API cannot see it): test first with a user lacking the privilege, then fix or remove the probe

- [ ] R082 [FIX] `CALL ivg.vector.search` with a text or seed-node query goes through the translator, which still reads only `kg_NodeEmbeddings` (the R073 defect on the translator route): test first with a routed graph, then route it (fixes4, Q1)
- [ ] R083 [FIX] The store's KNN label filter joins `rdf_labels` without a graph scope: a same-id node labelled in another graph passes the filter. Test first, then scope the join (fixes4, Q2)

- [ ] R084 [FIX] Spec 262 design defect (s262c): the deploy maps the `Graph.KG`/`IVG` packages' SQL name to the IVG Schema (`map_sql_packages`) and rewrites six code classes' `[ SqlSchemaName = Graph_KG ]` (Meta, PageRank, Traversal, Centrality, Communities, TraversalPaths). With a non-default schema, `$$GetPackage^%apiDDL("RAG")` becomes `Graph.KG` and `Graph_KG`'s becomes `Graph.K1`, so in a namespace that still holds `Graph_KG` table classes, setup's DDL compiles into `Graph.KG` and collides (-400 on `prockgKNNVEC`, -324 on `idx_edges_adj`). Fix: no package remapping; the `[ SqlProc ]` projections move into classes generated at deploy in the IVG Schema's own package (e.g. `<pkg>.SqlProcs`, forwarding to the `Graph.KG` code), so DDL in schema S always compiles into S's package. Test first: (1) a fresh `IVGALT` namespace's table, function and procedure classes are in package `IVGALT`; (2) US4 scenario 4 (`initialize_schema(schema_prefix="RAG")` on a `Graph_KG`+`RAG` namespace) records `RAG` and leaves the `Graph_KG` classes untouched; (3) every SqlProc IVG calls answers in both schemas. Update research R1/R10 and the ADR's consequences

**Gate A**: R003–R016 and R056–R084 tests green; unit suite green; TCK areas from R005 green.

---

## Workstream C: docs restructure (DOC)

### C1. Moves and deletions (tests first)

- [x] R017 [DOC] Unit test `tests/unit/test_docs_layout.py`, written first: the four scratch files and `docs/python/PYTHON_SDK.md` are gone; every file listed in R018 is under `docs/dev/` and not at its old path; `docs/` top level holds only reader-facing docs; `CONTRIBUTING.md` exists
- [x] R018 [DOC] `git mv` to `docs/dev/`: `TESTING_POLICY.md`, `TCK.md`, `coverage.md`, `objectscript-coverage-floor.json`, `arno-reader-proof.json`, `setup/UV_SETUP.md`; to `docs/dev/notes/`: `performance/ALT_VS_BASELINE.md`, `performance/alt-bench/`, `enhancements/`, `research/`, `rdf-landscape-spike-2026.md`, `cypher-gap-recommendations.md`, `graph-analytics-detection-roadmap.md`, `index-maintenance-architecture-review.md`, `ldbc_snb_query_analysis.md`, `for-opsreview/`
- [x] R019 [DOC] Update every reference to a moved path (`grep -rn` over `tests/ scripts/ iris_vector_graph/ .github/ pyproject.toml *.md docs/`): at least `tests/unit/test_alt_bench_catalog.py`, `test_aws_run_pushes_results.py`, `scripts/bench/aws/poll.sh`, `scripts/bench/alt/build_report.py`, `tests/unit/test_objectscript_coverage_gate.py`, `scripts/line_coverage/linecov.py`, `check_gate.py`, `tests/unit/_236_helpers.py`, `tests/e2e/test_236_reader_gate.py`, `scripts/arno_reader_gate.py`, `tests/unit/test_230_collection_integrity.py`, `tests/e2e/test_254_purge_e2e.py`, CLAUDE.md/AGENTS.md pointers; full unit suite and the reader gate green
- [x] R020 [DOC] Delete `docs/dc_contest_raw.md`, `docs/bulk-loader-pr.md`, `docs/rdf2sql-brief.txt`, `docs/flowchart LR.mmd`, `docs/python/PYTHON_SDK.md`; R017 green
- [x] R021 [DOC] Write `CONTRIBUTING.md`: dev setup (`uv`, the enterprise container, `scripts/enterprise-container.sh`), test layout, ci-parity, TCK, coverage gates, the release checklist, and links into `docs/dev/`; move ADMIN_GUIDE §2's test-container material and the test-only env vars (`IVG_PORT`, `IVG_ARNO_PORT`, `IVG_KEEP_CONTAINER`, …) here (NR4, NR19)

### C2. One container recipe

- [x] R022 [P] [DOC] Unit test `tests/unit/test_compose_recipe.py`, written first: `docker-compose.yml` has no `container_name`, publishes `${IVG_IRIS_PORT:-1972}:1972` and `${IVG_WEB_PORT:-52773}:52773`; every doc that names a container or a port for the user agrees (checked by R043's consistency test once it exists)
- [x] R023 [DOC] Edit `docker-compose.yml` accordingly; README and QUICKSTART say how to change the ports and how to find the container (`docker compose ps`) (NR4, NR5)

### C3. API reference

- [ ] R024 [P] [DOC] Unit test `tests/unit/test_api_reference_fresh.py`, written first: `scripts/dev/gen_api_reference.py --check` exits 0 (the committed `docs/API_REFERENCE.md` equals a fresh generation); every public method of `IRISGraphEngine` and `IRISGraphOperators` appears with its signature; none has an empty summary
- [ ] R025 [DOC] Write `scripts/dev/gen_api_reference.py` (inspect signatures and the first docstring paragraph; group by mixin; stable order) and generate `docs/API_REFERENCE.md`; fill every empty or misleading docstring summary it reports (expected: some in `_engine/*.py`); R024 green

### C4. KNOWN_ISSUES split

- [x] R026 [DOC] For each of KNOWN_ISSUES' 119 sections, record in `specs/release-4.6.0/known-issues-triage.md`: fixed (with the CHANGELOG or commit that fixed it), open (with a reproduction run on the 4.6.0 build: command and output), or "cannot confirm" (and why)
- [x] R027 [DOC] New `docs/KNOWN_ISSUES.md`: only the confirmed-open entries, one short entry each (symptom, versions affected, workaround, what to check), grouped "For application developers" / "For IRIS operators"; everything else verbatim in `docs/dev/notes/known-issues-history.md`, "cannot confirm" entries marked so; update the tests that read the file (`tests/unit/test_236_known_issues.py` and the others `grep -rl KNOWN_ISSUES tests` lists) to read the half that holds what they check
- [x] R028 [DOC] Add as open issues for 4.6.0 anything A did not fix that the walkthrough found and R026 confirms (e.g. NR16's default-graph key `0` if not documented elsewhere)

### C5. Reader-facing docs, top to bottom (each written with its doc-test markers from D)

- [ ] R029 [DOC] `README.md`: what IVG is, install, first query (with `# →` answers), "Where IVG lives in a namespace" (the Luca answer: one namespace, the IVG Schema, classes compiled there by `initialize_schema()`, nothing in `%SYS`; Arno optional and not distributed), feature table without IPM, two reading paths (application developer → QUICKSTART, USER_GUIDE, API_REFERENCE; IRIS operator → ADMIN_GUIDE), links checked
- [ ] R030 [DOC] `docs/setup/QUICKSTART.md`: the compose recipe, install, load, query, one algorithm, each with its answer; no link to `PYTHON_SDK.md` (NR10); `docs/setup/DATA_FORMATS.md` reviewed against the loaders
- [ ] R031 [DOC] `docs/USER_GUIDE.md`: task-oriented; adds global PageRank, shortest path (with the R005 behaviour) and BFS (NR10); the default graph's key explained once (NR16); every example with its answer; links to API_REFERENCE for signatures
- [ ] R032 [DOC] `docs/ADMIN_GUIDE.md` as the single operator document, with OPERATIONS folded in and `docs/OPERATIONS.md` removed (links redirected), organised by an operator's questions: what IVG installs and where (spec 262 inventory, T061/T062); privileges needed for setup and for use (NR's "privileges never stated"); optional components (Arno: per-process, not distributed, no `%ZSTART`; `Graph.KG.Service`; `FHIRGraphSyncTask`; the run-time compiled `IVGC.*` plans and `^IVG.CypherGen("mode")`); backup (globals incl. `^IVG.AlgoCache`, `^ArnoAccel`, `^IVG.Deploy`); upgrade and `remove_legacy_objects`; inspecting with Portal and `iris session` (the `%ExistsId` check in the right namespace); env vars for users only; troubleshooting with stale entries removed (#5 `Graph.KG.Edge`; #4 reworded) (NR1, NR2, NR3, NR12, NR14, NR15)
- [ ] R033 [P] [DOC] `docs/ADMIN_API.md`, `docs/ledger-prometheus-hook.md`, `docs/setup/IRIS_PASSWORD_RESET.md`, `docs/iris-sql-quirks.md`: reviewed against the code; operator or developer audience stated at the top; examples marked for D
- [ ] R034 [P] [DOC] Feature docs `docs/FHIR_GRAPH.md`, `docs/SEMANTIC_LAYER.md`, `docs/COMMUNITIES.md`: reviewed against the code and the 4.6.0 behaviour (WCC/CDLP/PageRank changes); examples with answers
- [ ] R035 [P] [DOC] Benchmarks: merge `docs/BENCHMARKS.md` and `docs/performance/BENCHMARKS.md` into one `docs/BENCHMARKS.md` (dated numbers, the run that produced each); `docs/performance/GRAPH_ALGORITHMS.md`, `DRKG_SCALE.md` reviewed or moved to `docs/dev/notes/`
- [ ] R036 [P] [DOC] `docs/architecture/`: `ARCHITECTURE.md` corrected (embedded-Python claim, NR17; the IVG Schema), `ACTUAL_SCHEMA.md` regenerated or checked against a fresh namespace, `rust-accelerator.md` and `embedded_python_architecture.md` reviewed; audience "for contributors and curious operators" stated
- [ ] R037 [P] [DOC] `docs/demos/*.md` and `docs/notebooks/README.md`: each demo runs on 4.6.0 (or is marked with a reason) and links current docs
- [ ] R038 [P] [DOC] `docs/migration/`: `upgrading-from-older-releases.md` updated through 4.6.0; new `v4.6.0.md` (spec 262 T059's consumer section included); `docs/releases/` gains nothing (release notes stay in CHANGELOG)
- [ ] R039 [DOC] `CONTEXT.md` and `docs/adr/`: glossary terms used by the rewritten docs exist (IVG Schema, Graph, Graph Key, Native Reader…); ADR index line in ADMIN_GUIDE's "further reading"

- [x] R066 [DT] The doc run leaves IVG state behind in its namespace (USER_GUIDE turns ledger strict mode on, which failed 84 unit tests run in the same namespace afterwards): the harness's reset must restore every IVG global the docs can change (`^IVG.*` modes included); unit test first, then fix (found by docs1)

**Gate C**: R017, R022, R024 green; every changed `.md` passes `markdownlint-cli2` and `prettier --check`.

---

## Workstream D: docs tests, layers 1 and 2 (DT)

### D1. Layer 1: the docs' code runs and its answers match

- [x] R040 [DT] Unit tests `tests/unit/test_doctest_harness.py`, written first, for the harness's parser: fenced-block extraction by language; `<!-- doc-test: skip reason="…" -->`, `output`, `run` markers; `# →` expectations; a block with no marker that the harness cannot classify is an error
- [x] R041 [DT] Write the harness `tests/docs/harness.py` per plan.md's conventions (python in one interpreter per doc; sql through the cursor; `objectscript`/terminal through `iris session` with prompts stripped; bash checked, run only when marked); R040 green
- [x] R042 [DT] E2E `tests/docs/test_docs_run.py`: parametrized over README, QUICKSTART, DATA_FORMATS, USER_GUIDE, ADMIN_GUIDE, ADMIN_API, FHIR_GRAPH, SEMANTIC_LAYER, COMMUNITIES and each demo; each doc in a freshly emptied `IVG460DOC`; fails on any unmarked failing block or mismatched answer; reports skips by reason. First run against today's docs recorded in `specs/release-4.6.0/doctest-baseline.md` (how much runs before C)

### D2. Layer 2: the docs agree with the code

- [x] R043 [P] [DT] Unit test `tests/docs/test_docs_consistent.py`: every relative link and file path in `README.md`, `CONTRIBUTING.md`, `docs/**/*.md` resolves; every `Graph.KG.*`/`IVG.*` class, `engine.<method>`, `^Global` root and `IVG_*` env var a doc names exists (classes from `iris_src/src`, methods from `IRISGraphEngine`, globals from a fresh namespace snapshot list, env vars from `grep os.environ`); every port and container name a doc gives for the user matches `docker-compose.yml`; every `scripts/…` command a reader doc gives exists and is not a dev script
- [x] R044 [DT] Wire R042 and R043 into `scripts/ci-parity.sh` (R043 always; R042 when a container is up) and into the release checklist
- [ ] R045 [DT] Make every doc pass R042 and R043 (fix the doc, or the code where the doc states the intended behaviour and the code is wrong: then a test first, as in A)
- [x] R046 [P] [DT] Unit test that the API reference, inventory and doc-test harness are all run by ci-parity (guard against a gate silently dropped)

**Gate D**: R042 and R043 green on every reader doc with skips only for stated reasons (expected: Arno-only blocks).

---

## Workstream E: docs tests, layer 3 (NW)

- [ ] R047 [NW] Build the 4.6.0 wheel and sdist from a clean worktree of the branch head; `twine check`
- [ ] R048 [P] [NW] Newcomer walkthrough 1, IRIS expert (Luca's profile): fresh agent, fresh clone of the branch at the head commit, the built wheel in a fresh venv, its own container from the compose recipe on free ports; tasks as in the first walkthrough (install, schema, a small graph with hand-worked answers, Portal and `iris session` inspection, `%ExistsId` in three namespaces, a second namespace, re-run setup, Arno status); report to `~/.cache/ivg-bench/novice-460/expert/REPORT.md` with the same friction-log format
- [ ] R049 [P] [NW] Newcomer walkthrough 2, Python developer: same isolation; quickstart, load, Cypher, every algorithm the USER_GUIDE shows, shortest path, text search, a named graph, one temporal query, all checked against hand-worked answers; report to `~/.cache/ivg-bench/novice-460/python/REPORT.md`
- [ ] R050 [NW] Triage both reports: every "blocker" and "wrong" item fixed (test first) and the affected walkthrough re-run until it reports none; papercuts fixed or listed in the sign-off

**Gate E**: both reports with 0 "blocker" and 0 "wrong" items, on the final commit.

---

## Workstream F: release (REL)

- [ ] R051 [REL] CHANGELOG 4.6.0 complete (Security, Fixed, Changed with the approved breaks called out, Added, Removed, Docs); lint
- [ ] R052 [REL] Run every gate on the final commit (plan.md "Gates"); record in `PRE_RELEASE_CHECKLIST.md` "Sign-off" a 4.6.0 entry in the 4.5.1 entry's format, with commit, counts and spec 262's speed-gate table
- [ ] R053 [REL] On Tom's explicit word only: push `release-4.6.0`, watch CI, fast-forward `main`, push, tag `v4.6.0` (annotated), push the tag, build from a clean worktree at the tag, `twine check`, `twine upload`, `gh release create` from the CHANGELOG section, clean-venv install check with `-I -W error`, publish record committed to the checklist
- [ ] R054 [REL] Draft for Tom (he sends it): the reply to ArnolfJp with the 4.6.0 version and date and the credit line they chose
- [ ] R055 [REL] After publishing: agent-bus message to opsreview (spec 262 T060: the migration notes; `idx_edges_graph_oid`; re-vendor guidance); tell Tom; delete scratch namespaces `IVG262*`, `IVG460DOC` and clean `IVGSWAP` (`plan_*` graphs, `R262*` leftovers)

---

## Dependencies

- R001 first. A (R003–R016) and spec 262 can run in either order; both before C5's inventory and footprint sections (R029, R032).
- D1's harness (R040–R041) can be built during B; R042's baseline run then measures today's docs.
- C before R045; R045 before E; E before F. R053 waits for Tom.

## Parallel opportunities

- A's test tasks R003, R006, R008, R010, R012, R015 together; fixes in different files.
- C5's per-doc reviews R033–R038 in parallel once R029/R032's structure is settled.
- R043 beside R041; R048 beside R049.
