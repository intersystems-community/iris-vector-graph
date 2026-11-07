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
- [x] R064 [FIX] The `LIST_HEAD` and `LIST_LAST` SQL function bodies fail when called (the translator never calls them): test each function directly, then fix or remove them (and from the legacy manifest's replacement map if removed)
- [x] R065 [FIX] `engine.algorithms.execute_wcc` forwards to a method the engine does not have: test first (it answers the hand-worked components of a two-component graph), then fix

- [x] R067 [FIX] `engine.cypher.execute_bfs` and `execute_knn_vec` forward to methods the engine does not have (fixes2's R065 follow-up): test first (each answers a hand-worked BFS / KNN result), then fix
- [x] R068 [FIX] The `[:R*2]` fast paths (`KHop2*`) name their column `node_id` and return distinct nodes instead of the RETURN's column and one row per path (same class as #80): test first, then fix
- [x] R069 [FIX] `WHERE r.ts = 100` on a labelled pattern fails with "Incorrect number of parameters" (temporal route; also 4.5.x): test first, then fix
- [x] R070 [FIX] Check `approx_count_distinct`'s HLL route on an undirected variable-length walk against relationship uniqueness (N1's rule): a test with the hand-worked count; fix if it differs
- [x] R071 [DOC] KNOWN_ISSUES: an unaliased, non-DISTINCT variable-length read now lists every path (correct, per openCypher); on a hub it can reach IRIS's string limit where 4.5.x returned fewer, wrong rows. Document the limit and the `DISTINCT`/`count()` alternatives, unless the 262 speed gate (T066) shows it needs fixing

- [x] R072 [DT] The doc-test reset kills every global in its scratch namespace, `^NKG` included; use `Graph.KG.Traversal.DropNKG` (or the engine's own drop) for `^NKG`, never a raw kill (memory: Arno callout defects), test first (fixes3, open question 3)
- [x] R073 [FIX] The store's `execute_knn_vec` (and so `CALL ivg.vector.knn`) reads `kg_NodeEmbeddings` and ignores per-graph/model embedding routes, which `kg_KNN_VEC` follows: test first with a routed graph (hand-worked nearest neighbours), then fix (fixes3, Q1)
- [x] R074 [FIX] The 1-hop ids shortcut returns one row per target instead of one per relationship in a multigraph default graph: test first (two parallel edges give two rows), then fix (fixes3, Q2)
- [x] R075 [FIX] `tests/unit/test_edge_embeddings.py::test_embed_edges_all_default` failed once in a full run with `<ILLEGAL VALUE>%SQLInsert+27^Graph.KG.nodes.1` and passed alone: find the order dependence and fix it (test or code)

- [x] R076 [FIX] The 4.0.0 migration (in 4.5.1 and 4.6.0) fails on a 3.2.0 install whose named-graph edge has endpoints only in the default graph: SQLCODE -127 on `fk_edges_source` (the `acme` edge n5->n6 of the 3.2.0 fixture). Blocks spec 262 T036. Test first (the 3.2.0 fixture upgrades; the edge's endpoints exist in its graph afterwards, as `create_edge` has registered them since 4.0.0), then fix the migration
- [x] R077 [FIX] In `IVGALT`, `test_262_alt_schema_paths.py::test_pagerank_ppr_and_betweenness` and `::test_bfs_and_shortest_path` fail in-process and pass from a fresh process (likely the in-process Arno/^NKG cache keyed without the schema). Also failing before spec 262's last commits. Find the cache, key it correctly, both tests green in one process
  Cause: `Graph.KG.NKGAccelLoader.UseNKG()` cached its first answer in `^||NKGAccel("useNKG")` for the life of the IRIS process (and process-private globals span namespaces). A process that asked before `sync()` built `^NKG` kept reading the legacy no-shard `^KG("out", node, pred, dst)` layout, so PPR and k-hop answered the seed alone; a fresh connection (new process) answered. Not schema-specific (same on Graph_KG). Fix: asked every call. Red (`tests/e2e/test_262_nkg_process_cache.py`): `UseNKG` 0 after sync in the same process; ppr `{c:1: 0.15}`. Green: that file 2 passed, `test_262_alt_schema_paths.py` 25 passed in the same process.
- [x] R078 [FIX] `Graph.KG.PyOps` calls `CALL kg_KNN_VEC` and `CALL kg_RRF_FUSE` unqualified, which resolve in `SQLUser` where no such procedure exists: test first (each PyOps entry answers a hand-worked result), then qualify through the IVG Schema
- [x] R079 [FIX] `api/gql/engine.py` and `api/gql/resolvers.py` name `Graph_KG` directly; GraphQL on a non-default IVG Schema reads the wrong tables: test first in `IVGALT`, then route through the engine's schema
  The files are `iris_vector_graph/gql/engine.py` and `iris_vector_graph/gql/resolvers.py` (the package GraphQL API; `api/gql` is the separate demo server and was not changed). Introspection and the stats/nodes/outgoing/incoming resolvers read `engine._ivg_schema`. Red (`tests/e2e/test_262_gql_alt_schema.py`, IVG262ALT/IVGALT): `Table GRAPH_KG.NODES not found` on stats; `Unknown type GqlPerson` (introspection found no labels). Green: 3 passed.
- [x] R080 [FIX] Retire `iris.vector.graph.GraphOperators` (nothing calls its SQL procedures `iris_vector_graph.kg_TXT/kg_KNN_VEC/kg_RRF_FUSE`; only tests call its methods): remove it from the wheel like the benchmark classes, add it to the legacy manifest's retired classes, move its tests to the dev-classes helper or delete them with the reason in the commit
  Moved to `iris_src/dev/iris/vector/graph/GraphOperators.cls` with the benchmark classes and `User.Exec` (spec 262 T055); on the legacy manifest (`RETIRED_CLASSES`). Tests: `test_graph_operators_sql.py` deploys it from iris_src/dev (module-scoped, removed afterwards); `TestGraphOperatorsClass` in test_objectscript_classes.py deleted (it duplicated test_graph_operators_sql and skipped when the class was absent); `test_227_objectscript_surface::test_the_legacy_knn_procedure_cannot_cross_graphs` deleted (it guarded a procedure that no longer ships); `test_ppr_cls_fast_path::test_graphoperators_deployed` now asserts it is not deployed; test_cls_layer no longer skips on it. Red/green: test_wheel_contents and test_262_fresh_namespace::test_no_benchmark_classes (T053, T054).
- [x] R081 [FIX] `_native_privileges` always answers True (`%CHECKPRIV` reports SQLCODE 100 without raising and DB-API cannot see it): test first with a user lacking the privilege, then fix or remove the probe
  `_native_privileges` now prepares `SELECT TOP 1 1 FROM <schema>.<table> WHERE 1 = 0` per table: the Prepare checks the privilege and raises SQLCODE -99. Red (`tests/integration/test_native_privileges.py`, IVG262REC, a user with SELECT on nodes/rdf_labels/rdf_props only): `assert True is False`. Green: 3 passed (that user False, a user with all four True, _SYSTEM True).

- [x] R082 [FIX] `CALL ivg.vector.search` with a text or seed-node query goes through the translator, which still reads only `kg_NodeEmbeddings` (the R073 defect on the translator route): test first with a routed graph, then route it (fixes4, Q1)
- [x] R083 [FIX] The store's KNN label filter joins `rdf_labels` without a graph scope: a same-id node labelled in another graph passes the filter. Test first, then scope the join (fixes4, Q2)

- [x] R084 [FIX] Spec 262 design defect (s262c): the deploy maps the `Graph.KG`/`IVG` packages' SQL name to the IVG Schema (`map_sql_packages`) and rewrites six code classes' `[ SqlSchemaName = Graph_KG ]` (Meta, PageRank, Traversal, Centrality, Communities, TraversalPaths). With a non-default schema, `$$GetPackage^%apiDDL("RAG")` becomes `Graph.KG` and `Graph_KG`'s becomes `Graph.K1`, so in a namespace that still holds `Graph_KG` table classes, setup's DDL compiles into `Graph.KG` and collides (-400 on `prockgKNNVEC`, -324 on `idx_edges_adj`). Fix: no package remapping; the `[ SqlProc ]` projections move into classes generated at deploy in the IVG Schema's own package (e.g. `<pkg>.SqlProcs`, forwarding to the `Graph.KG` code), so DDL in schema S always compiles into S's package. Test first: (1) a fresh `IVGALT` namespace's table, function and procedure classes are in package `IVGALT`; (2) US4 scenario 4 (`initialize_schema(schema_prefix="RAG")` on a `Graph_KG`+`RAG` namespace) records `RAG` and leaves the `Graph_KG` classes untouched; (3) every SqlProc IVG calls answers in both schemas. Update research R1/R10 and the ADR's consequences

- [x] R085 [FIX] `iris_vector_graph.__all__` lists `ApiKeyMiddleware`, `ReadOnlyMiddleware` and `is_mutation_cypher`, which the package never imports, so `from iris_vector_graph import *` raises: test first, then fix (apiref)
- [x] R086 [FIX] `kg_RRF_FUSE` reads `r["id"]` from the tuples `ivf_search` returns, so with an IVF index the fusion is empty: test first with an IVF index and hand-worked fused ranks, then fix (apiref)
- [x] R087 [FIX] `start_background_embedding` schedules `Graph.KG.EmbedQueueTask`, which does not exist: test first, then fix or remove the method (apiref)
- [x] R088 [FIX] `export_graph_ndjson` writes no edges: test first (a 3-edge graph exports 3 edge records and re-imports to the same graph), then fix (apiref)
- [x] R089 [FIX] `plaid_build` ignores `dim`; `bulk_create_nodes` behaves differently on the server and SQL paths: a test per difference, then make them agree (apiref)
- [x] R090 [FIX] `scripts/fixtures/build_release_namespace.py` `_drop_schema_tables` swallows exceptions in its retry loop (`except Exception: pass`): keep the retry, raise the last error (simplify pass, spec 262)

- [x] R091 [FIX] `tests/unit/test_cypher_probe_throttle.py::test_execute_cypher_uses_the_throttled_probe` failed once in the live full unit run on `release-4.6.0` (e712f859) and passes alone and in ci-parity: find the order dependence (bisect as in R075) and fix the cause

- [x] R092 [FIX] `load_obo` stores every OBO `relationship:` line (`part_of`, `regulates`) as `is_a`: obonet keeps the relationship type in the multigraph edge key and `load_networkx` iterates `G.edges(data=True)`, which never yields the key. Test first with a small OBO file (hand-worked predicates per edge), then read the key (`G.edges(keys=True, data=True)` for a multigraph) (docs2)

- [x] R093 [FIX] A relationship property between -1 and 1 reads back as a string: `create_edge(qualifiers={"confidence": 0.95})` then `RETURN r.confidence` answers `'.95'`. `prop_values._CANONICAL_FLOAT_RE` (`^-?(0|[1-9]\d*)\.\d+$`) rejects ObjectScript's leading-dot form (`.95`, `-.5`), so `parse_rel_prop_text` leaves it text. Test first (0.95, -0.5, 0.5, 2.5, 0, -0, 1e-7 round-trip as numbers; '.95' stored as a string stays a string), then fix; check node properties for the same (docs2, DATA_FORMATS live run)

- [x] R094 [FIX] `ledger.verify()` right after `ledger.enable()` reports `diverges` (classification `unrecorded_writes`, one DiffEntry for `a-PING->b`) when the graph holds a temporal edge: the marked `rdf_edges` row (`{"temporal":true}`, ADR-0009/0015) is in verify's view but not in the genesis. Genesis and verify must agree on whether marked rows are ledger-governed. Test first (create_node a, b; create_edge_temporal(a, PING, b, ts); enable; verify is `consistent`), then fix (docs2, USER_GUIDE live run)
- [x] R095 [FIX] `khop`'s return shape depends on whether the Arno library is loaded: ObjectScript answers `{"nodes": [ids], "edges": [...]}`, arno-callout 1.2.1 answers `{"seed","hops","totalNodes","nodes": [{"id","dist"}]}` with no edges. Test first (the same hand-worked answer and shape with and without Arno, on the reader-gate container), then normalise to one documented shape (docs2)
- [x] R096 [FIX] `embedding_count(model_key=...)` without `graph=` ignores `model_key` and counts `kg_NodeEmbeddings` (answered 4, not 1): it routes only when `graph` is not None. Test first, then route the default graph too (docs2)

- [x] R097 [FIX] **Security.** With `IVG_API_KEY` set, `/admin/*`, `/schema` and `/db/*` are served without a key; only `/api/`, `/fhir-event` and `/graphql` check it. Test first (every route except an explicit public allow-list, e.g. `/health`, refuses a request without the key and accepts one with it), then fix the middleware's route matching. CHANGELOG Security entry (docs3)
- [x] R098 [FIX] `IVGClient` and the `ivg` CLI send `Authorization: Bearer <key>` while the server reads `X-Api-Key`: an authenticated client cannot authenticate. Test first (client against a server with `IVG_API_KEY` set), then make them agree (accept both on the server, or send `X-Api-Key`) and document it (docs3)
- [x] R099 [FIX] `export_rdf(label_filter=...)` exports every node's properties, not only the filtered nodes': test first, then fix (docs3)
- [x] R100 [FIX] N-Quads export writes only edges into their named graph; a named graph's labels and properties land in the default graph: test first (round-trip a named graph), then fix (docs3)
- [x] R101 [FIX] `kg_WCC(max_iterations=...)` is ignored on the default store path: test first (a chain that needs more than the cap), then fix (docs3)
- [x] R102 [FIX] `/admin/schema/init` ignores `embedding_dimension`: test first, then pass it through (docs3)
- [x] R103 [FIX] `SHOW INDEXES` reads the BM25, IVF and PLAID index lists from tables nothing creates, so those indexes never appear: test first (build one of each, `SHOW INDEXES` lists them), then read where they are recorded (docs3)
- [x] R104 [FIX] `docs/dev/arno-reader-proof.json` is not in the wheel, so an installed IVG never routes PageRank to Arno: test first (wheel contents, and `arno_readers` resolves the proof from an installed wheel), then package it (docs3)
- [x] R105 [FIX] After a community procedure (`CALL ivg.kcore(...) YIELD node ...`), `ORDER BY node` fails with SQLCODE -29, and `node` yields id, labels and properties; and the community methods answer `[]` when the path that ran reports an error. Test first, then fix both (no silent empty answer) (docs3)
- [x] R106 [FIX] `ledger.enable()` passes no schema name (seen in code, not tested): test first in `IVGALT`, then pass the engine's IVG Schema (docs3)

- [x] R107 [FIX] **Security.** `cypher_api` ignores `IVG_READ_ONLY`: `/admin/load` and the other `/admin` writes still run in read-only mode. Test first (every write route refuses under `IVG_READ_ONLY=true`, enumerated from the app's routes), then apply the read-only guard there (sec2)
- [x] R108 [FIX] `/health` is public and returns the node count and raw exception text: test first (it answers status only; no counts, no exception text, without the key), then trim it (sec2)
- [x] R109 [FIX] `src/iris_fraud_server` (demo server) has no API-key check: apply the same default-deny middleware, or state in its README that it is a local demo with no auth; test first (sec2)
- [x] R110 [FIX] The Bolt server prints query text to stdout: route it through logging at DEBUG; test first (no query text on stdout at default level) (sec2)

- [x] R111 [FIX] **Security (Bolt).** A Bolt 5 session that logged on and then sends a second LOGON with a wrong key stays authenticated (and keeps working after RESET): a failed LOGON must end the session's authentication. Also: on Bolt 5 the key is checked only in LOGON, so a 5.0 client that sends it in HELLO is refused on its first RUN; accept the key in either. Test first (both cases) (simplify review of R097)

- [x] R112 [FIX] **Security.** `ReadOnlyMiddleware` (and `ApiKeyMiddleware`'s public allow-list) match `request.url.path`, which still carries the `root_path` prefix on Starlette 0.48, while routing strips it: behind `uvicorn --root-path /ivg` (or a proxy, possibly IRIS WSGI via a2wsgi), `/ivg/admin/load` routes to `/admin/load` and passes the read-only check. Test first (an app with `root_path='/ivg'`: every write route refuses under `IVG_READ_ONLY`, every non-public route needs the key, `/ivg/health` is public), then match on `starlette.routing.get_route_path(request.scope)` (simplify review of R107)

- [x] R113 [FIX] REST, Bolt and GraphQL decide read-only with `is_mutation_cypher` (a keyword match), while `execute_cypher(read_only=True)` uses the parser (`CypherQuery.writes`, with every `;` statement checked). One rule for all four: route the servers' check through the parser's (a query that does not parse is refused under read-only), so a future writing `CALL` procedure or a keyword inside a string cannot diverge. Test first: the R0-series bypass cases (UNION branch, `CALL { }` body, `;` statements, keywords inside strings) give the same answer on every route (simplify review of R112)

- [x] R114 [FIX] Upgrading a 4.5.x namespace to a non-default IVG Schema leaves its ledger history in the `Graph_KG` ledger tables (and `^IVG.Ledger`) while the new schema's ledger tables start empty (R084: the ledger classes follow the IVG Schema's package). Preserve the history: test first (a 4.5.x namespace with ledger revisions upgraded to `RAG` keeps every revision and `verify()` is `consistent`), then copy or re-point the history in the upgrade step. Also: the accidental SQL projection `TemporalIndexMS_BackfillStructuralRows` is gone; confirm no caller (code, docs, legacy manifest) names it, or add it to the manifest (r084)

- [x] R115 [FIX] **Security hardening.** `writes_cypher` reads the cached parse tree; R113 fixed one mutation of it (`subsequent_queries`), but if `_execute_parsed`, `plan_row_merge` or `plan_count_create` change the cached tree another way, a later `writes_cypher` call on the same text reads the changed tree. Make the read-only verdict independent of execution: parse fresh for the check, or cache an immutable verdict per text. Test first: each write shape from the R113 table, executed (read_only=False) through each execution path, then re-classified, is still a write (simplify review of R113)

- [x] R116 [FIX] The background embedding worker (`Graph.KG.EmbedQueueTask`, R087) writes a named graph's vectors to `kg_NodeEmbeddings`, where reads for that graph (routed since 227) never find them: test first (queue embeddings for a named graph; `kg_KNN_VEC` in that graph finds them), then write through the route (fixes6)
- [x] R117 [FIX] `CALL ivg.retrieve` reads only `kg_NodeEmbeddings` (the R082 defect on another procedure): test first with a routed graph, then route it (fixes6)
- [x] R118 [FIX] A leading `CALL ivg.vector.search(... vector ...) YIELD ... MATCH ...` may be answered by the handler alone, dropping the following clauses (unverified): test first; fix if the clauses are dropped (fixes6)
- [x] R119 [FIX] 3.2.0's `Graph.KG.Loader` survives every upgrade and is not on the legacy manifest: add it (and check the manifest generator's tags reach 3.2.0 for classes it should list), test first via `remove_legacy_objects(dry_run=True)` on the 3.2.0 fixture; also note in KNOWN_ISSUES or fix that RRF fusion prefers the built-in `hnsw` entry over a graph's own IVF index (fixes6)

- [x] R120 [FIX] `UNWIND range(1,500) AS i WITH i LIMIT 3 CREATE (:X {i: i})` creates one node for all rows and fails with SQLCODE -119 (unchanged before and after R115): test first (three nodes with i = 1, 2, 3), then fix (sec6)

- [x] R121 [FIX] Relationship-property defects found while fixing R093 (fixes7): a user property named `s`, `p` or `o_id` returns the edge's column instead; `{c: 0.95}` or `{c: $v}` as an inline relationship filter matches nothing; `properties(r)` turns the string ".95" into 0.95; `WITH r.c AS c` still answers `.95`; inline relationship properties `{f: true}` were stored as NULL. A test per defect with hand-worked answers, then fix each
- [x] R122 [FIX] RDF export of a graph whose id is not an absolute IRI (e.g. `trials`) writes `<trials>`, which rdflib refuses to parse: mint an IRI for such ids on export (e.g. `urn:ivg:graph:trials`) and map it back on `import_rdf`, so the round trip restores the graph id; test first (fixes7)
- [x] R123 [FIX] Investigate two unit tests reported failing on the release branch before fixes7: `tests/unit/test_store_write_paths.py::test_execute_cypher_with_nkg_dirty_raises` and `tests/unit/test_nodes_edges_deep.py::TestSetEdgeWeightException` (likely the R058 route change and the R059 delete change): decide test-vs-code with evidence, fix
- [x] R124 [DEV] `uv.lock` is stale and `uv lock` fails to resolve (`iris-agentic-dev`); the `dev` extra gained `obonet` and `rdflib`. Regenerate the lock (or document why it is not used) so CI's install matches (fixes7)

- [x] R125 [FIX] `CALL ivg.degreeCentrality(...)` reports degree 0 for every node while `engine.degree_centrality()` is right (likely the `''` argument reaching ObjectScript as `$c(0)`): test first with a hand-worked degree table, then fix (fixes8)
- [x] R126 [FIX] On a non-default IVG Schema upgrade (`RAG`), setup warns that `RAG.BACKFILL_TEMPORAL_ROWS` does not exist and so does not declare the adjacency functional index: `^KG` then is not maintained on writes. Test first (after the upgrade, a new edge appears in `^KG` and a variable-length read finds it), then fix the procedure's placement or the lookup (fixes8)
- [x] R127 [FIX] Degree, betweenness, closeness, eigenvector centrality and PPR still answer `[]` when the path that ran fails (R105 fixed the community methods): raise instead; test first by forcing each path's failure (fixes8)
- [x] R128 [DEV] About 20 unit tests read source with `inspect.getsource`, which can fail when a file changes during the run (R091): switch them to checking behaviour or the compiled code (fixes8)
- [x] R129 [DEV] `tests/e2e/test_236_reader_gate.py` runs in USER and erases it, and `tests/integration/test_ledger_lock_and_chunks.py` takes its lock in USER whatever `IVG_NAMESPACE` is: both must use the test namespace (or a dedicated one) (fixes8)

- [x] R130 [FIX] Engines built with `from_connect` run a `FETCH FIRST` probe (`engine.py:719`) that creates unqualified tables `ivg_ff_probe_a`/`ivg_ff_probe_b` in `SQLUser` and never drops them (breaks spec 262 FR-002): test first (after `from_connect`, `INFORMATION_SCHEMA.TABLES` lists no IVG table in `SQLUser`), then put the probe tables in the IVG Schema and drop them (admin)
- [x] R131 [SECURITY] `Graph.KG.Service.CreateWebApp` sets `MatchRoles ":%All"`, so every authenticated user of `/kg` runs with `%All`: test first (the created app's `MatchRoles` grants no `%All`), then grant only what `/kg` needs and document it in ADMIN_GUIDE (admin)
- [x] R132 [FIX] Arno library default paths disagree: `ArnoAccel.Load` uses `/tmp/libarno_callout.so`, `NKGAccel.Load` and `IVG_ARNO_LIB` use `/usr/irissys/mgr/...`, and `IVG_ARNO_LIB` never reaches the BFS loader: test first (one resolved path for both loaders, `IVG_ARNO_LIB` honoured by each), then one default (admin)
- [x] R133 [PERF] Setup runs `CREATE OR REPLACE` on every function and procedure on each run (NR15): test first (a second `initialize_schema()` rewrites no unchanged routine, measured by T061), then skip unchanged ones (admin)
- [x] R134 [FIX] `kg_BM25Meta`, `kg_IVFMeta`, `kg_PlaidMeta` are read (`admin.py:153-159`, `489-491`) but nothing creates them, and the read errors are swallowed: test first, then read from what does exist or report "not built" explicitly, with no silent `except` (admin)
- [x] R135 [FIX] `engine.sync()` returns `False` on any exception (`_sync_kg`), on a data-changing path: raise instead; test first by forcing the failure (admin)
- [x] R136 [FIX] Betweenness work areas `^bcParScore`, `^bcParSrc`, `^bcBitAdj` are persistent globals that outlive a killed run: test first (after a run, and after an interrupted run's next run, none is left), then process-private globals or a cleanup on entry (admin)

- [x] R137 [FIX] First `initialize_schema()` does not create `idx_ledger_correlation` (asked for before the ledger table class compiles); the second run adds it. Test first: after one setup on a fresh namespace the index exists; then un-xfail T061's case and update ADMIN_GUIDE "Running setup again" (admin)
- [x] R138 [PERF] Every setup run repeats `ALTER TABLE rdf_edges ALTER COLUMN graph_id NOT NULL` / `SET DEFAULT ''` on a column already both, recompiling `Graph.KG.rdfedges` (NR15): test first (a second run leaves the class's compile time unchanged), then skip when already set (admin)

- [x] R139 [FIX] Same family as R121, left open: `WHERE r.c = $v` with a fraction matches nothing; `properties(n)` on nodes turns ".95" into 0.95; after `WITH r.c AS c` a filter not pushed into the MATCH compares an API-written `.95` as `0.95`, so `= 0.95` misses. Test first with each shape and its hand-worked answer (fixes9)
- [x] R140 [FIX] `vector_distance()` still reads `kg_NodeEmbeddings` rather than the graph's route; `ivg.shortestPath.weighted` probably drops later clauses like R118 did. Test first for each (fixes9)
- [x] R141 [DEV] The v4.0.0 legacy fixture build fails, so `remove_legacy_objects` is unchecked against a 4.0.0 namespace: fix the fixture and run R119's check there (fixes9)

- [x] R142 [FIX] `Graph.KG.EmbedQueue` writes every route with `TO_VECTOR(?, DOUBLE)`: a route whose element type is not DOUBLE fails each entry (now recorded as its error, R116). Test first with a FLOAT route, then use the route's element type, as `_translate_retrieve` does (simplify review of fixes9)

- [x] R143 [FIX] `kg_KNN_VEC`, `kg_TXT` and `kg_RRF_FUSE` answer no rows, with no error, when the caller lacks SELECT on a table they read: a quiet wrong answer for any user without full privileges. Test first with a user lacking one grant: the call raises a privilege error naming the table (fixes10)
- [x] R144 [FIX] `DegreeCentralityJson` always reads the default graph (`pGraph = 0`): test first with two graphs of different degree, then pass the graph through (fixes10)
- [x] R145 [FIX] `betweenness_centrality_neighborhood` and `bfs_vector_rerank` still answer `[]` on a store error (outside R127's five): raise; test first with an explicit trigger (fixes10)
- [x] R146 [FIX] PyOps swallows a failed `embedding_registry` read and falls back to the default table, so a named graph's width check can be wrong silently: raise unless the table is absent (fixes10)
- [x] R147 [SECURITY] `CreateWebApp` only adds SQL grants to `IVG_KG_<namespace>`, so a privilege a later release drops stays on an existing role: reset the role's SQL privileges to exactly the current list; test first by granting an extra privilege, re-running, and reading it back gone. Also: `CreateWebApp` on an existing application fails at `Security.Applications.Create`, after creating the role; make it update the existing application's `MatchRoles` (ADMIN_GUIDE then says re-running fixes a pre-4.6.0 `%All` app). And: `EnsureWebAppRole` asks `GetGlobalDest(ns, "IVG")`, which is `^IVG` only; `^IVG.Ledger`, `^IVG.AlgoCache` mapped elsewhere get no read grant: check whether a route reads them and, if so, resolve each (fixes10, simplify review)
- [x] R148 [DEV] `scripts/dev/make_scratch_namespace.py` CLI always installs default IVG from the main checkout, and `--drop` refuses IVG460*names other than IVG460INV*: add `--no-install`, install from the caller's checkout, and allow dropping IVG460FX*/IVG460DC* scratch names (fixes10)

- [x] R149 [FIX] `NKGAccelLoader.Load` answers 1 when this process already holds a handle opened from a different path: test first (a Load naming another path reports the mismatch or reloads), then fix (fixes11)
- [x] R150 [FIX] A class reload on its own (`tcp-deploy`, any `$System.OBJ.Load` of `LedgerRevision`) drops `idx_ledger_correlation` until the next `initialize_schema()`: declare it in the class (or re-add it in the deploy), test first (fixes11)
- [x] R151 [DEV] `tests/e2e/test_cy_sort_key_e2e.py` still calls `SQLUser.CY_SORT_KEY` (stale since spec 262): call it in the IVG Schema (fixes11)
- [x] R152 [FIX] `BuildKG` calls `BuildNKG` inside a Try/Catch that only writes to the console, and `BetweennessNeighborhoodOS` ignores the status `queue.Sync()` returns: return/raise both; test first with a forced failure (fixes11)
- [x] R153 [PERF] `arno_bridge` runs `CREATE OR REPLACE` on its SQL wrappers on first use in each connection (outside R133): skip unchanged ones with the same fingerprint record, test first (fixes11)

- [x] R154 [FIX] `ivg.shortestPath.weighted` searches only the default graph (`DijkstraJson` hard-codes `pGraph = 0`; `USE GRAPH` ignored), and `weightProp`/`maxHops` look ignored (weights come from the `^KG` value): test first with two graphs and a weight property, hand-worked costs (fixes12)
- [x] R155 [FIX] The weighted-path handler's `DijkstraJson` failure and `execute_weighted_shortest_path` answer no rows on any exception, and the store passes `max_hops` where `maxCost` belongs: raise; remove or fix the now-dead store path (fixes12)
- [x] R156 [FIX] `CALL ivg.ppr(...) YIELD node, score MATCH (node)-...` joins `PPR.node_id`, a column the CTE lacks (the R118 defect on another procedure): test first, then fix; check every `CALL ivg.* YIELD` followed by MATCH/WITH for the same shape (fixes12)
- [x] R157 [FIX] `CY_PROPS_MAP` reads `rdf_props WHERE s = ?` with no graph filter, so `properties(n)` merges same-id nodes' properties across graphs: test first with two graphs sharing a node id (fixes12)
- [x] R158 [FIX] Without `USE GRAPH`, `vector_distance` reads the default route for every row, including named-graph nodes in an all-graphs read: score each row with its own graph's route; test first (fixes12)
- [x] R159 [DOC] The string ".95" compares equal to the number 0.95 in relationship-property filters (R121, R139), because the stored `JSON_VALUE` text cannot tell them apart: a KNOWN_ISSUES entry with the shape and the workaround (fixes12)
- [x] R160 [FIX] `status()`: `hnsw_built` is a row count of `kg_NodeEmbeddings_optimized`, not an index check; other probes still swallow errors; `security.py` still lists the `kg_*Meta` names R134 retired: test first, then fix (fixes12)

- [x] R161 [FIX] `_engine/embeddings.py` `_read_route` catches every exception, logs at debug and treats the registry as unreadable, so reads fall back to the legacy table (R146's pattern in Python): fall back only when `embedding_registry` is absent, raise otherwise; test first (fixes13)
- [x] R162 [DOC] `bfs_vector_rerank` is implemented by no store (R145 made it raise `NotImplementedError`), yet its docstring, and so `docs/API_REFERENCE.md`, describe it with ~28 ms timings: say it is not implemented on IVG's store, or remove it from the public API (fixes13)
- [x] R163 [FIX] `CALL ivg.degreeCentrality` and the other centrality CALL procedures take no graph option and read the default graph under `USE GRAPH`: pass the query's graph; test first with two graphs (fixes13)

- [x] R164 [FIX] `Graph.KG.PyOps` `VectorSearch`/`HybridSearch` set `rows` only inside the `try`: an exception whose `sqlcode` is 0 or more makes `ReadFailure` answer `""` and the next `for r in rows` raise `NameError`. Test first, set `rows = []` up front, and fold the four copies of the exec/read-SQLCODE/raise block into one helper (simplify review of fixes13)

- [x] R165 [FIX] After any BM25 build `status()` and SHOW INDEXES list `__stop__` as a BM25 index: `Graph.KG.BM25Index.List()` returns the stopword node `^BM25Idx("__stop__")` as an index name. Test first (fixes14)
- [x] R166 [FIX] A centrality/community CALL (kcore, SCC, betweenness, closeness) answers one row of NULLs when `^NKG` is not built: the SQL function returns `ERROR:^NKG not built` and `JSON_TABLE` turns it into a null row. Raise instead; test first (fixes14)
- [x] R167 [FIX] `YIELD node AS x` fails on every procedure (`CALL ivg.ppr(['a']) YIELD node AS n RETURN n` → `SyntaxError: Undefined variable: n`): test first per procedure family, then fix (fixes14)
- [x] R168 [FIX] Node property filters: `n.score = 0.95` misses a node whose score is the number 0.95 (written by `create_node`) and matches the string ".95"; `RETURN n.score` answers the string `'0.95'`. Same family as R121/R139 on nodes: test first with API- and Cypher-written nodes, hand-worked rows (fixes15)
- [x] R169 [FIX] A relationship property holding the string "0.95" reads back as the number 0.95 in `RETURN r.p` and `properties(r)`: number-shaped text is retyped. Keep a string a string; test first (fixes15)
- [x] R170 [DEV] Legacy fixtures up to 4.4 loaded classes from the container's shared `/tmp/src/` first (fixed in R141), so earlier legacy upgrade results were built from mixed classes: rebuild every legacy fixture namespace and re-run the legacy upgrade E2E files in the full gate (fixes15)
- [x] R171 [DEV] Test infra: the Arno integration helpers fall back to port 31971; the five Arno integration modules route class calls to USER so they skip outside USER; `test_execution_contexts_new` needs a container that does not exist and never runs; IVG236 needs `initialize_schema()` re-run. Fix or delete each, so nothing silently skips (fixes15)

- [x] R172 [DOC] `/no-ai-slop` pass over every reader doc (README, QUICKSTART, USER_GUIDE, ADMIN_GUIDE, ADMIN_API, DATA_FORMATS, KNOWN_ISSUES, the feature docs, `docs/migration/v4.6.0.md`), edit mode: prose only; code blocks, `# →` answers, doc-test markers, names, links and numbers unchanged; consistency and doc-run tests still pass. Before R048/R049 (Tom, 2026-11-04)

- [x] R173 [FIX] Spec 262 refusal order: an engine with the default `Graph_KG` schema set up in a namespace recorded as `RAG` loaded unrewritten `Graph_KG` classes (`Graph.KG.SqlProcs`, `LedgerRevision`, `LedgerStats`) before or instead of refusing (fixes17 saw it via `test_460_pyops_search_e2e.py` in IVG460FXD). Test first: on a namespace recorded `RAG`, a `Graph_KG` engine's `initialize_schema()` raises `IVGSchemaError` and the namespace's class list and `^oddDEF` are unchanged (before/after snapshot) (fixes17)
- [x] R174 [FIX] `CALL gds.pageRank.stream('g') YIELD nodeId, score` and `CALL gds.betweenness.stream(...)` answer no rows and no error on a 3-node cycle: test first with hand-worked scores (fixes17)
- [x] R175 [FIX] `arno_bridge._build_kg_adjacency_serverside` always fails with `<SUBSCRIPT> ^KG("out","")` (walks the pre-214 layout) and `build_kg_adjacency_chunked` swallows it, so the fast path is dead: fix the walk, stop swallowing; test first (fixes17)
- [x] R176 [FIX] `get_unembedded_nodes` has `except Exception: return []`, so any SQL failure makes the `embed_nodes` backfill embed nothing silently: raise; test first with an explicit trigger (fixes17)
- [x] R177 [DOC] Betweenness, closeness, eigenvector and the community procedures read `^NKG`, which has no graph key, so without `USE GRAPH` they answer every graph merged: say so in USER_GUIDE and KNOWN_ISSUES (and in each method's docstring), with what to do for a per-graph answer (fixes17)
- [x] R178 [DOC] `docs/migration/v4.6.0.md` consolidation, step 2: the drop block drops `OTHER.ledger_revisions`/`ledger_stats` before step 3's `initialize_schema()` copies `Graph_KG`'s ledger rows, so when `OTHER` is `Graph_KG` the history is lost; the prose says drop them after. Measure what happens with the ledger tables left in place during step 3 (ambiguity refusal or copy), then fix the steps and the `tests/e2e/test_262_not_upgraded.py` run of them; a test asserts `history()` survives the consolidation (slop review)
- [x] R179 [DOC] Unmeasured claims: FHIR_GRAPH "a sync of 500 changed resources takes well under the 10 s budget" (measure and state the number), FRAUD_DEMO "exponential cost" and "Real-time community detection" (source or cut) (slop review)

- [x] R180 [FIX] `MATCH ()-[r:T]->() SET r += {p: 2}` fails with SQLCODE -29 (`E3.NODE_ID` not found): the map is written into `rdf_props` instead of the relationship. Test first, then write it into the edge's qualifiers like `SET r.p` (fixes16)
- [x] R181 [FIX] `store_node('x', properties={...})` writes no `id` property, so `MATCH (n {id: 'x'})` finds nothing and `RETURN n.id` is null (`create_node`, `bulk_create_nodes` write it): test first, then write it (fixes16)
- [x] R182 [FIX] `toString()` of a stored number answers `.95` (number from `create_edge`) or `.94999999999999995559` (fraction from a Cypher CREATE on a node); openCypher answers `'0.95'`. Test first with each stored form; canonical number text in `toString()` (fixes16)

- [x] R183 [FIX] `CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) YIELD totalCost` answers no rows and no error though a→b→c exists with cost 2: the shim passes GDS's arguments to `ivg.shortestPath.weighted` by position. Test first with the hand-worked cost (fixes18)
- [x] R184 [FIX] On a namespace with named graphs a Python method and its Cypher form disagree: `engine.strongly_connected_components()` (and `triangle_count`, `k_core`, `eigenvector_centrality`) answer the default graph while `CALL ivg.scc()` (and the others) answer every graph merged. Make each pair answer the same graph (the default, as the Python methods and the openCypher reading of an unscoped query do), or raise where it cannot; test first per pair; update R177's docs (fixes18)
- [x] R185 [DEV] `scripts/enterprise-container.sh` `compile-all` and `deploy` load raw `iris_src/src` into USER without reading the IVG Schema record (R173 covered `tcp-deploy` and the session load): route them through the same refusal, or remove them if `tcp-deploy` replaces them (fixes18)

- [x] R186 [FIX] Lost write: `MATCH (n:X {k: 1}) SET n.k = 2, n.q = 3` never writes `q` (answers `[[2, None]]`): each SET item re-runs the MATCH, which no longer matches after `k` changed (R180 fixed it for `+=`). Pin the matched nodes once for the whole SET clause, and check SET items across REMOVE and multiple SET clauses; test first (fixes19)
- [x] R187 [FIX] `MATCH (n:X {id:'a'}), (m:X {id:'b'}) SET n.p = m.x` (and `SET n += {p: m.x}`) raises SQLCODE -23: test first, then fix (fixes19)
- [x] R188 [FIX] `MATCH (n:X) WITH n.id AS id RETURN id` raises SQLCODE -23 ("Label 'STAGE1' is not listed"); another alias name works: test first, then fix (fixes19)
- [x] R189 [FIX] `WITH $v AS v CREATE (n:X {id: v, name: 'nm'})` raises IntegrityError SQLCODE -121 (foreign key): test first, then fix (fixes19)
- [x] R190 [FIX] `RETURN toString(0.95), toString(1.0)` answers `['.95', '1']` for literals; openCypher answers `'0.95'`, `'1.0'`: test first (fixes19)

- [x] R191 [DEV] E2E files that drop and rebuild a hard-coded scratch namespace (`ns262.recreate("IVG262CONA")` in `test_460_r174_gds_shims_e2e.py` and others): a regression run by another agent wiped a namespace it did not own. Each such test takes its namespace from an env var with the hard-coded name as default, and the namespace is documented in the file header (fixes20)

- [x] R192 [FIX] All-graphs rule (CONTEXT.md "All-graphs read", decided 2026-10-20): a Cypher read with no `USE GRAPH` answers the union of each graph's own answer. Check every `CALL ivg.*` / `gds.*` procedure without `USE GRAPH` on a namespace with a default and a named graph (degreeCentrality, ppr, pageRank, wcc, cdlp, kcore…, R163/R184 made several read the default graph only): each answers the per-graph union, or raises (as R184's seven do) when it cannot. Fix USER_GUIDE/KNOWN_ISSUES/docstring wording R184 added ("without USE GRAPH … answer the default graph") to state the rule; Python methods' `graph=None` meaning stated per CONTEXT. Test first per procedure (fixes20)
- [x] R193 [FIX] `head([1,2,3])` and `last([1,2,3])` answer the text `'1'`/`'3'` while `[1,2,3][0]` answers the integer 1, on both execution paths (strict xfail in R064's e2e): test first, keep the element's type (fixes20)
- [x] R194 [FIX] On nodes created by `bulk_create_edges` (no `id` property), `MATCH (h {id:'x'})` answers inconsistently: `count(h)` 1 and the path read 20,000 rows, but `count(DISTINCT x)` 0 and `DISTINCT x.id` no rows. Test first; one meaning for `{id:}` on every route (fixes20)
- [x] R195 [PERF] `EigenvectorGlobal` writes scores without a leading zero (`.5773…`), Python's JSON parser rejects it, and `eigenvector_centrality` falls back to the slow Python walk every time: emit valid JSON numbers; test first (fixes20)
- [x] R196 [FIX] The `^NKG` SQL functions called directly (`SELECT kg_Betweenness(...)` and the others) still merge every graph; R184 guards only the Cypher form: same refusal on a namespace with named graphs, or document it in ADMIN_GUIDE/USER_GUIDE where those functions are named (fixes20)
- [x] R197 [DEV] `scripts/test-container.sh compile` and `compile-all` load raw sources into USER without the IVG Schema record check (R185 covered enterprise-container.sh): same refusal (fixes20)

- [x] R198 [FIX] `[n IN nodes(p) | n.node_id]` answers `'[]'` with no error on both the native and the SQL path (`MATCH p = shortestPath((x {node_id:'a'})-[*]->(y {node_id:'f'})) RETURN [n IN nodes(p) | n.node_id]` → `[['[]']]`; also plain `MATCH p=(x)-[:ROAD]->(y)`), while `n.name`/`n.id` in the same comprehension and `RETURN p` are right. Test first, both paths (walkthrough 1, preliminary)
- [x] R199 [FIX] On a fresh Community 2026.1 namespace `status().report()` shows `Probe errors (1) ! adjacency predicate probe failed: <SYNTAX>`: find the probe's statement, test first on 2026.1 (compose image), fix (walkthrough 1, preliminary)
- [x] R200 [FIX] On Community 2026.1 `kg_RRF_FUSE` is missing from `Graph_KG` after setup though ADMIN_GUIDE's inventory lists it (and R131's `/kg` role grants EXECUTE on it): find why its CREATE fails there (logged? swallowed?), make setup report it, and make the inventory say which builds have it; test on the compose image (walkthrough 1, preliminary)

- [x] R201 [FIX] An engine on a never-set-up namespace raises `IVGSchemaError` saying it "was set up by an earlier IVG release", which is false: the not-upgraded message only when legacy objects exist, a never-set-up message otherwise; test first (walkthrough 1 W4, `~/.cache/ivg-bench/novice-460/expert/REPORT-prelim-d2f00abe.md`)
- [x] R202 [DOC] ADMIN_GUIDE's reading role lacks `%Native_GlobalAccess:U`, so every engine warns the schema-record check is skipped and `status()` shows 4 errors: add it (or make the check work without it), and check the role end to end; also the `%DB_USER` vs `%DB_%DEFAULT` resource assumption (P5) (walkthrough 1 W5, P5)
- [x] R203 [FIX] `create_node` and `delete_edge` return `False` on SQLCODE -99 instead of raising `GraphWriteError` as USER_GUIDE §2 says; check every write method for the same: test first with a reader that lacks the privilege (walkthrough 1 W6)
- [x] R204 [FIX] In the default `job` mode ~20 hop reads over 5 shapes compiled no `IVGC.*` plan and logged nothing (`sync` compiled one): find why the job never compiles, test first that a repeated shape compiles in `job` mode (walkthrough 1 W7)
- [x] R205 [DOC] ADMIN_GUIDE's globals inventory vs a set-up namespace: ledger storage globals absent until a row is written, no `degrees` in `^KG` but an undocumented `^KG("gen")`, `^NKG` built by a setup re-run (undocumented); and the skipped-class reasons differ from the "Reason setup gives" column (W9). Correct the doc and `inventory.json` from what T061 measures, and make T061 assert both (walkthrough 1 W8, W9)
- [x] R206 [FIX] `khop` answers `[]` for a reader holding database `R` (a silent wrong answer, reported as papercut P8): raise a privilege error naming what is missing, or work under `R`; test first (walkthrough 1 P8)
- [x] R207 [FIX] Walkthrough 1 papercuts P1–P4, P6, P7, P9–P12 (`~/.cache/ivg-bench/novice-460/expert/REPORT-prelim-d2f00abe.md`): fix each (setup's INFO lines vs "one summary", the ~138 severity-1 "Error loading dll" lines when Arno is absent, `IVG_DISABLE_ARNO=1` indistinguishable from absent, `Gen.Entries()` JSON string, dry run silent on a different `embedding_dimension`, snapshot scope wording, operator path starting in Python, the `[communities]` extra, `bulk_create_nodes`'s `id` property, compose healthcheck after `iris stop`), or list why not in the sign-off (walkthrough 1)

- [x] R208 [FIX] `MATCH (n:X {k:1}) WITH n SET n.q = 3` raises -29 (the SET reads a `node_id` column the WITH stage lacks), so `SET n.k = 2 WITH n SET n.q = 3` writes nothing: test first (fixes21)
- [x] R209 [FIX] `toString()` of a non-literal float keeps IRIS's spelling: `UNWIND [0.5, 1.0] AS x RETURN toString(x)` → `'.5'`, `'1'`; `toString(0.1 + 0.2)` → `'.30000000000000004441'`; openCypher answers `'0.5'`, `'1.0'`, `'0.30000000000000004'`: route every float through `CY_FLOAT_TEXT`; test first (fixes21)
- [x] R210 [FIX] `SET r.p = r.q`, `SET r.p = n.x`, `SET n.p = r.w` (a SET reading a relationship's property) are refused with SyntaxError since R187 (failed with -23 before): support them; test first (fixes21)
- [x] R211 [FIX] TCK Set5 [4] "Explicit null values in a map remove old values" fails (before and after R186–R190): test first, fix (fixes21)
- [x] R212 [FIX] MERGE ON MATCH after a MATCH that returns exactly one row writes the node the MERGE just created (kept by R186 for the multi-row TCK cases): find the rule the TCK implies for one row and many, test first, fix (fixes21)

- [x] R213 [FIX] BLOCKER: `bulk_load_session().add_edges` with the `source_id`/`predicate`/`target_id` dicts every other loader takes writes 0 edges, returns 0, raises nothing: accept that shape (and refuse an unknown shape loudly); API reference states the shape; test first (walkthrough 2 B1, `~/.cache/ivg-bench/novice-460/python/REPORT-prelim-d2f00abe.md`)
- [x] R214 [FIX] (folded into R212, strict-xfail test on release) `MERGE … ON MATCH SET` silently ignored on both paths (walkthrough ran d2f00abe, before R186): re-check on HEAD, test first, fix with R212 if still wrong (walkthrough 2 W1)
- [x] R215 [FIX] `create_edge(weight=1.0)` (or `weight=1`) reads back `r.weight = None` and `properties(r) = None`, so `sum(r.weight)` and `WHERE r.weight = 1.0` are wrong; `weight=2.0` reads back as int 2. Test first, hand-worked sums (walkthrough 2 W2)
- [x] R216 [DOC] USER_GUIDE's exact-betweenness recipe `sample_size = N-1` scales every score by N/(N−1) (14.44 vs networkx 13.0); `sample_size=0` is exact: fix the recipe (and the memory `ivg_betweenness_sample_cap` premise), check the code's meaning of each value, test the documented recipe against networkx (walkthrough 2 W4)
- [x] R217 [FIX] `get_temporal_aggregate` counts whole 5-minute buckets, not `[ts_start, ts_end]` (window [T+100,T+300]: count 4 vs 3, sum 16 vs 14): exact window, or document bucket granularity in the signature and USER_GUIDE; test first (walkthrough 2 W5)
- [x] R218 [FIX] Native and SQL paths return different types for one query (`0.5`/`29` vs `'.5'`/`'29'`), against USER_GUIDE "both give the same answer"; and Cypher-written floats read back as IRIS text (`'.94999999999999995559'` from `get_node`, `'.5'`, `2.5*2` → `'5'`), contradicting R168's reader table and the KNOWN_ISSUES example (which fails in a fresh namespace). Measure on HEAD, reconcile code, table and docs; test first (walkthrough 2 W6, W7)
- [x] R219 [FIX] `node_count()`/`edge_count()` count every graph (14/21 vs 10/18), against USER_GUIDE "omitted means the default graph, never every graph"; `node_count` has no `graph=`: one rule per CONTEXT.md for the Python API, add `graph=`, test first (walkthrough 2 W8)
- [x] R220 [FIX] `leiden_communities` / `ivg.leiden` return modularity −0.012 (worse than one community) where networkx Louvain gets 0.204 on the same 10-node graph: test first with the report's graph, find the defect (weights? directed?), fix (walkthrough 2 W9)
- [x] R221 [FIX] A refused statement (DELETE of a node with relationships) raises a bare `Exception`, not `result.error`/`RuntimeError` as USER_GUIDE "Errors" says: one documented error type; test first (walkthrough 2 W10)
- [x] R222 [FIX] `IVGClient(url)` ignores `IVG_API_KEY` in the environment (401) though ADMIN_API says it reads it; `GET /server` reports `iris_version: unknown`: test first, fix both (walkthrough 2 W11, W12)
- [x] R223 [FIX] A vector query of the wrong width logs a server-side failure and answers `[]` (reads as "no neighbours"): raise naming the widths; test first (walkthrough 2 W14)
- [x] R224 [DOC] COMMUNITIES' intro says the `^NKG` procedures "see every graph"; the page, USER_GUIDE and KNOWN_ISSUES say they raise once a named graph has edges (check after R192 lands) (walkthrough 2 W15)
- [x] R225 [FIX] Walkthrough 2 papercuts (`~/.cache/ivg-bench/novice-460/python/REPORT-prelim-d2f00abe.md`, 21 items, e.g. BM25 stopwords undocumented, eigenvector default iterations): fix each or list why not in the sign-off (walkthrough 2)

- [x] R226 [FIX] A `WITH` after a CALL loses the row's graph: `CALL ivg.degreeCentrality({}) YIELD node, degree WITH node, degree WHERE degree > 0 RETURN node.name, degree` answers `[a_def,2],[a_g,2],[a_def,1],[a_g,1],[x_g,1]` (by hand `[a_def,2],[a_g,1],[x_g,1]`): the Stage pipeline projects no `graph_id` (the same limit MATCH…WITH has). Carry the graph through every stage; test first (fixes22)
- [x] R227 [FIX] Lists from a row lose their element types: `WITH [1,'a',2.5,true] AS x RETURN head(x), x[0], last(x)` answers `['1','1','1']` (by hand `[1, 1, True]`); R193 fixed constant lists only: test first (fixes22)

- [x] R228 [FIX] Path and list element reads, all silent or crashing (fixes23): on a fixed-length path `[r IN relationships(p) | r.km]` answers `[]` (elements hold only the type); `UNWIND nodes(p) AS n RETURN n.name` (and `n.node_id`) answers null every row; `head(nodes(p)).name`/`last(nodes(p)).name` fail -25, `[n IN nodes(p) | n {.name}]` -23, `[n IN nodes(p) | id(n)]` -29; a comprehension drops null elements (`[n IN nodes(p) | n.missing]` → `[]`, not `[null, …]`); two comprehensions over one collected list in a RETURN fail `-400 <SUBSCRIPT>` on 2026.3. Test first per shape, hand-worked answers, both builds
- [x] R229 [FIX] `GrantWebAppSql` fails `CreateWebApp` when a granted routine (`kg_TXT`, `kg_RRF_FUSE`) is absent on some build: grant what exists and report the missing one in the status the call returns; test first (fixes23)

- [x] R230 [DEV] Driver drift: the repo `.venv` has intersystems-irispython 5.4.0, a fresh `pip install` gets 5.4.1, and they decode a `$DOUBLE` in a VARCHAR column differently (5.4.0 the item's own type, 5.4.1 the column's: `'.94999999999999995559'`), so `.venv` runs hide what users see. Add a gate: the E2E subset that reads properties runs under 5.4.1 as well (and CI installs the version a fresh install gets); record the measured difference in `docs/dev/` (fixes26)

- [x] R231 [FIX] Leiden's `gamma ≠ 1.0` means different things per route (CPM on leidenalg, modularity resolution on ObjectScript and the networkx fallback, unchecked on Arno): one meaning, documented in COMMUNITIES; test each route at gamma 0.5 and 2.0 against the chosen definition (fixes27)
- [x] R232 [FIX] `CALL ivg.betweenness` ignores `direction` and `maxHops` (its SQL function never passes them; `direction:'both'` silently answers directed scores): pass them or refuse; test first against networkx (fixes27)
- [x] R233 [FIX] `betweenness_centrality(sample_size=0)` above 200 nodes takes the first 200 node ids, not a random sample: sample uniformly (seeded, documented) or say "first 200 by id" in the docstring and USER_GUIDE; test first (fixes27)
- [x] R234 [GATE] Full E2E on a freshly made enterprise container (`ivg-iris-e2e`, at aaef3122's ancestor d2f00abe): 229 failed, 84 errors (the long-lived container: 9 and 3). Groups: IVGFHIR absent → fixture `Failed:` instead of skip (46); `Table 'SQLUSER.NODES' not found` (20, tests that assume 4.5.x views); `No module named 'numpy'` in embedded Python (13, container setup); traversals/subgraph/shortest-length/temporal answering empty on IVGFIX (the bulk — possibly ^KG not maintained in a namespace made by make_scratch_namespace + initialize_schema: a fresh-install defect until shown otherwise). Triage every group to a product or test/environment cause, fix test-first, rerun to green on a fresh container. Log: `~/.cache/ivg-bench/rel-460/e2e-full-4.log`

- [x] R235 [FIX] Class-deploy order on an upgraded namespace: `tests/conftest.py` `_deploy_objectscript`/`classes_to_deploy` load in name order and never check the load status, so `Graph.KG.SqlProcs` fails `#5083` (procedure name projected by an old `Traversal`/`TemporalIndex`) silently and that session runs without it (fhirfix). Fix the test deploy (SqlProcs last, status checked), and prove the product's `initialize_schema()` / `deploy_packaged_classes` upgrade path from a 4.5.x namespace cannot hit the same collision (a test from the frozen 4.5.1 classes, asserting `SqlProcs` compiled and every projected procedure answers) (fhirfix)
- [x] R236 [FIX] Native plan compile error in messages.log: `IVGC.P3393737283` `#1054 Invalid expression '}'` (seen 10:59 UTC 2026-11-04 on ivg-iris-enterprise): find the Cypher shape that generated it, test first that the plan compiles or the executor declines it (fhirfix)
- [x] R237 [FIX] `test_230_retrieval_scope` (2 tests, failing since before fixes14) and `test_230_bulk_ingest_upsert`'s timing test: triage, fix the code or the test (fhirfix, fixes14)

- [x] R238 [FIX] RETURN after SET answers nodes the MATCH never bound: with `{name:'A'}` and `{name:'B'}`, `MATCH (n:QX {name:'A'}) SET n.name = 'C' RETURN n.id` answers both (the RETURN drops the filter on a key the query sets; R211 fixed it for removed keys only). Capture as R211 does; test first (fixes25)
- [x] R239 [FIX] A parameter inside a `toString()` argument loses its binding: `MATCH (n) RETURN toString(n.f * $k)` → "Incorrect number of parameters": test first (fixes25)
- [x] R240 [FIX] A whole float from arithmetic over a stored value prints without `.0`: `toString(n.g * 2)` with `g = 2.0` answers `'4'` (openCypher `'4.0'`): keep the stored value's type through the arithmetic; test first (fixes25)
- [x] R241 [FIX] MERGE or CREATE after a stale WITH (its WHERE reads a key an earlier SET changed) still writes nothing silently (SET/REMOVE/DELETE there are refused since R208): refuse them too, or support; test first (fixes25)
- [x] R242 [FIX] `WITH n.k AS k, n SET n.r = k` raises UndefinedVariable: a SET reads a value the WITH projected; test first (fixes25)

- [x] R243 [FIX] Spec 262: setup's `^KG`/`^NKG` bootstrap counts the hard-coded `Graph_KG.rdf_edges` (`schema.py` ~2999), so under another IVG Schema it never bootstraps and it names an object outside the schema: use the engine's schema; test first on a `RAG` namespace with edges (fixes24)
- [x] R244 [FIX] PPR and `kg_GRAPH_WALK` for a reader at database `R` raise a bare `<THROW>` that does not say what was refused: name the missing privilege; and `betweenness_centrality` answers a different shape by role (zero scores listed at `R`, not at `RW`): one shape; test first (fixes24)
- [x] R245 [FIX] `materialize_inference` swallows a refused insert (`except Exception: continue`) on a data-changing path: raise `GraphWriteError`; test first (fixes24)
- [x] R246 [PERF] The native planner declines unlabelled `{node_id}` anchors and `RETURN x.node_id`, so common reads stay on SQL and never compile to `IVGC.*`: support them in the planner (documented as a limit since R204); test first that such a read runs natively with the same answer (fixes24)
- [x] R247 [FIX] `status()` for the documented reading role lists "class lookup failed" for `kg_NodeEmbeddings` and `_optimized` (HNSW index state needs the dictionary in SQL): a server-side probe the role can run; test first (fixes24)

- [x] R248 [FIX] **Fresh-install TCK:** under intersystems-irispython 5.4.1 (what a fresh `pip install` gets) the TCK fails 138 scenarios (same before and after fixes26; every TCK figure so far was measured under the `.venv`'s 5.4.0). Whole-node values (`RETURN n`, CALL-yielded nodes) carry Cypher-written numbers as IRIS text, which R218 does not cover. Extend R218's typed read to every node/map value the engine returns; run the full TCK under both drivers as a release gate (fixes26)
- [x] R249 [FIX] Unscoped counters still count every graph: `count_nodes`, `get_node_count`, `get_edge_count`, `get_label_distribution`, `IVGClient.node_count`; and `get_bucket_groups` sums whole buckets. Same rule as R219/R217; test first (fixes26)
- [x] R250 [FIX] Value spellings: a whole-number `sum(r.weight)` answers 5 not 5.0; `properties(r)` spells a stored "2.0" as 2 and answers `None` for an edge with no properties (openCypher `{}`); test first (fixes26)
- [x] R251 [FIX] The native executor fails `WHERE n.id IN [$a, $b]` with `<ARGUMENT ERROR>` (`IN $list` works): test first (fixes26)
- [x] R252 [FIX] `bulk_create_edges_temporal` turns weight `0` into `1.0` (`e.get("w") or …`): for data written from 4.6.0, a given 0 is stored as 0 (a new write, not a reinterpretation); test first (fixes26)

- [x] R253 [FIX] List values lose their types: `[x IN [r.km] | x]` answers `['1']` (r.km answers 1); `collect(n {.name})` lists JSON strings, not maps. Test first (fixes29)
- [x] R254 [FIX] On a variable-length named path `-[:ROAD*1..2]->`, `[r IN relationships(p) | r.km]` answers node ids (`'r228:b'`, `'r228:c'`): test first (fixes29)
- [x] R255 [FIX] `UNWIND [[1,2,3]] AS list WITH [x IN list WHERE x = 2 | x] AS l, count(*) AS c RETURN l, c` answers two rows (`["2"]`,1) and (`[]`,2); one row `[2]`,1 by hand: test first (fixes29)
- [x] R256 [FIX] A pattern comprehension with a map projection `[(a)-->(c) | c {.name}]` fails -23; and on 2026.3 two comprehensions with non-property projections over one collected list longer than 511 characters still fail `<SUBSCRIPT>`: test first (fixes29)
- [x] R257 [FIX] Silent fallbacks: betweenness's fast tier failing at R is swallowed into the slow Python walk without a word; `rebuild_indexes` reports failures in a status dict nobody reads (what hid R243's lost indexes): log/raise; test first (fixes29)
- [x] R258 [DOC] `CALL ivg.ppr` answers at database R but ADMIN_GUIDE says `kg_PPR` raises: check the SQL function, make the doc state each (fixes29)

- [x] R259 [FIX] (relationship arithmetic in RETURN fixed by R248; the rest open) Arithmetic over a relationship property rounds (`RETURN r.w * 3`, w=1.5 → 4; `toString(r.v * 2)`, v=2.0 → `'4'`); `coalesce()` of a stored number returns its text (`'1.25'`; strict xfail in R239's e2e); a CREATE stores a WITH column as text after a SET in the same query (`'5'`, but integer 5 from a committed value); `RETURN n.g` of a stored whole float is int in some shapes and float in others; static integer division FLOORs (-7/2 = -4) where the stored-value path truncates (-3, openCypher). Test first each, both drivers (fixes28; R248 owner takes the 5.4.1 side)

- [x] R260 [FIX] SQL path, silent wrong answer: with no `USE GRAPH`, a WITH-stage aggregate joins rows from every graph that share a node id: `MATCH (x {node_id: $id})-[:K]->(y) WITH y.name AS name, count(*) AS c RETURN name, c` counts B 8 times instead of once when `a`/`b` exist in two graphs (without the WITH, and with `USE GRAPH`, it is right). Strict xfail `test_an_unscoped_two_stage_read_over_shared_ids` in R246's e2e; same family as R226 (fixes31)

- [x] R261 [FIX] `UNWIND [1,2] AS i CREATE (:X {f: i * 0.5})` (or `{s: toString(i)}`) raises `<LIST ERROR>` on 2026.1 / `<ARGUMENT ERROR>` on 2026.3 and writes nothing (`{k: i}` works): test first (fixes32)
- [x] R262 [FIX] Remaining list-element typing: a computed list whose only element is a list (`collect()` of one list, a parameter) is still flattened by JSON_TABLE (R255 fixed literals); a boolean relationship property in a list reads as `1`; `[x IN ms | x]` over maps/lists lists strings; `[x IN [true] | x]` → `["1"]`; `1.0` inside a list → `1`. Test first each, both drivers (fixes32)

- [x] R263 [FIX] fixes30 finds (all on baseline too): `CREATE (:L {d: 2.5*2})` fails `<ARGUMENT ERROR>` (the arithmetic's AST bound as a value; same family as R261); `CALL { MATCH (n) RETURN n } RETURN n` fails -29; `OPTIONAL MATCH (n:L {id:'nope'}) RETURN n` answers no row instead of `[null]` when the label has nodes but none match; `CALL db.labels() YIELD label RETURN label LIMIT 1` ignores LIMIT; native `RETURN n.g` of a stored float 5.0 answers int 5 (SQL 5.0); `WHERE x IN [1, null]` fails -14 and `RETURN x IN [1, null]` a parameter count; a sum carried through WITH turns a whole float into an int. Test first each (fixes30)
- [x] R264 [FIX] `embed_nodes` builds embedding text from values read through the driver, so the text (and the vector) depends on the driver version: read typed (as R218) for text built from 4.6.0 on; say in CHANGELOG that a re-embed under 5.4.1 may change vectors; test first (fixes30)
- [x] R265 [DEV] R234 leftovers: `translate_to_sql()` without an engine names tables unqualified while `_fn()` defaults to `Graph_KG` (since spec 262 that SQL fails; `test_schema_prefix_isolation` pins it); `NKGAccelTraversal.BFSJson` outside USER may read a chunked answer from `^ArnoKG` in the caller's namespace while the library wrote it in USER (verify); `kg_RRF_FUSE` reads `kg_NodeEmbeddings` while `ivg.retrieve` reads the graph's route (one sees a named graph's vectors, the other not); the dead `self._store.query_nodes()` call in the VLP projection (`_engine/query.py` ~2103). Test first each (freshtriage, fixes30)
- [x] R266 [DEV] `test_230_ifind_helper_repair_e2e::test_the_docs_helper_class_disappears` fails only in the full run (asserts an IRIS compile quirk); `test_betweenness_neighborhood::test_performance_under_5ms` 14.2 ms vs 10 ms under host load, and its "with arno" message is stale outside USER: fix the order dependence and the timing test's premise (freshtriage)

- [x] R267 [FIX] fixes33 finds: `collect()` of an UNWIND boolean stores 1 (`UNWIND [1, true] AS v WITH collect(v) AS vs RETURN last(vs)` → 1; strict xfail in R227's e2e) and a stored `true` returns `"true"`; `x[-1]` on a row list in a WITH or arithmetic fails `<RANGE>` (-149); `CALL { WITH y MATCH (y)-[:K]->(z) RETURN z } RETURN z.name` fails -29 (with R263's CALL subquery case). Test first each (fixes33)

- [x] R268 [FIX] After `erase_graph` of a named graph, `^NKG` still holds that graph's nodes until `sync()`, so the `^NKG` Leiden routes partition nodes that no longer exist: rebuild or invalidate `^NKG` on erase (or make the readers check the generation); test first (fixes34)
- [x] R269 [FIX] `MatchEdges(src, pred, 0, '')` from SQL answers `[]` (probably `''` arriving as `$c(0)`): treat `$c(0)` as empty, as R125 did; test first (fixes34)

- [x] R270 [FIX] ObjectScript PPR (`Graph.KG.PageRank.Run`) keeps (1 − damping) of a dangling node's score and drops the rest, so scores do not sum to 1, and its stop rule is a hard-coded 1e-4 (the `tolerance` argument ignored on that path): redistribute dangling mass as the other routes do, honour `tolerance`; test first against networkx `pagerank(personalization=…)` (fixes37)
- [x] R271 [FIX] `save_snapshot` records a table it cannot read as 0 rows instead of raising: a swallowed error on a backup path. Raise (naming the table and error); test first with a table the snapshotting user cannot read (fixes37)
- [x] R272 [DEV] `scripts/dev/make_scratch_namespace.py` exits non-zero on Community because `tcp-deploy` counts the three MCP classes (need `%AI`) as compile errors: count them as the documented skip setup gives; and a 4.5.1 legacy fixture tries to load `/usr/irissys/mgr/<ns>/None` (a stored "None" library path from the frozen classes: confirm it is only the fixture) (fixes37)

- [x] R273 [FIX] fixes35 leftovers: an UNWIND row binds `true` as 1 (`UNWIND [1, true, 'a'] AS v RETURN v`; R227's strict xfail stays); `collect()` of a list-valued WITH column holds a string element; a row list element read through WITH is text, and under 5.4.1 `x[0] + 1` answers `'4'`; with `USE GRAPH`, `MATCH (n {k:1}) SET n.k = 5 WITH n, n.k AS k CREATE (...)` writes nothing; `UNWIND [-7, 7] AS x RETURN x / 2` answers `'-3.5'`; `size($p)` answers the JSON text's length; `OPTIONAL MATCH (a:L)-[r]->()` answers one row per `a`; `WHERE x[2] = 3` on a collected list answers `size(x) = 0`; native grouping by a whole float in parallel/long-key runs; `toString(coalesce(n.f, $k))` of a stored 3.0 → `'3'`. Test first each (fixes35)
- [x] R274 [FIX] fixes38 leftovers: Cypher `split('', ',')` → `["\u0000"]` and `split('a,b', '')` → `["a,b"]` (translator's `STR_SPLIT`); `'' =~ 'a*'` false, `'' =~ '.+'` true (translator regex path); `percentileCont` over zero rows answers 0.0 not null (`IVG.Percentile.PCONT`); an empty-string relationship property is stored as `"\u0000"` so `properties(r)` and `qualifiers` readers see `"\x00"` (read side: decode); a named graph's interned nodes left after its edges are deleted appear in default-graph SCC/closeness (R184's check looks only for graphs with edges); `NKGPopulated` answers 1 after `DropNKG`. Test first each (fixes38)
- [ ] R275 [PERF] Checks for the speed gate: `collect()` of any node property now runs `LIST()` of per-row JSON with a `CY_JSON_NUM` call per row (R262), and the `^NKG` readers may rebuild under the build lock after an erase (R268, `EnsureNKG`; up to `BuildNKG`'s cost on the first read after an erase). Time both at scale; document R268's first-read cost in ADMIN_GUIDE (Maintenance) and USER_GUIDE where erase is described (fixes35, fixes38)

- [x] R276 [FIX] fixes39 leftovers: `NKGAccelTraversal.PPRNative` is still the 2-hop approximation and its non-NKG branch reads `^KG("out", nodeId, …)` without the graph subscript (unreached by the engine, callable directly: remove it or make it correct); `restore_snapshot` wraps `self.conn.commit()` in `except Exception: pass` after each table (a swallow on a data-changing path); the `kg_PERSONALIZED_PAGERANK` fast path's `except Exception` logs and falls back to Python, hiding a `<PARAMETER>` error (narrow it). Test first each (fixes39)
- [x] R277 [DOC] For Arno (other repo, do not edit): `ppr_on_adj` in `arno/iris-integration/arno-callout/src/kg_ffi.rs` drops dangling mass and has a fixed 1e-6 stop; add to the unfiled Arno findings draft (memory `arno_callout_defects`) so the PPR reader gate fails it on the `path` fixture (fixes39)

- [x] R278 [FIX] A 4.5.x user holding SELECT only (no EXECUTE) could run Cypher that returns a property; since R218 every such RETURN calls `CY_TYPED`, so after the upgrade it fails -99 (`test_262_upgrade_451` line 171, strict xfail). Tom decided (2026-11-04): every setup grants EXECUTE on the reshaping read-path routines (not `CY_VLP_*`) to any grantee holding SELECT on `rdf_props`; un-xfail line 171; document in docs/migration/v4.6.0.md (fixes36)
- [x] R279 [FIX] `CY_PROPS_MAP` answers `'{}'` when the caller may not read `rdf_props` (the refusal is swallowed): raise the privilege error naming the table, as R143 does; test first (fixes36)
- [x] R280 [SECURITY] `CY_VLP_ENDS` with the indexed adjacency short-circuits to `Graph.KG.VarLength.Ends`, which walks `^KG` checking only the database resource, so a caller with EXECUTE but no SELECT learns the edges. More broadly, every `^KG`/`^NKG` reader (native executor, VLP ends, Arno readers, algorithms) is gated by the database resource, not SQL privileges. Decide and state the model: make the SQL-callable `^KG` readers check SELECT on the table they stand for (`$SYSTEM.SQL.Security.CheckPrivilege`), and say in ADMIN_GUIDE §3 that the database resource, not SQL privileges, is the boundary for IVG's global readers; test first (fixes36)

- [x] R281 [FIX] Ledger post-commit properties: the DELETE and INSERT on `rdf_props` in `ledger/client.py` carry no `graph_id`, so a ledger commit replaces the property in every graph and writes to the default one: scope by the revision's graph; test first with two graphs sharing a node id (fixes41)
- [x] R282 [FIX] Var-length path executors swallow: `_execute_var_length_labeled_path_funcs` and `_bfs_with_paths` (and both callers) catch SQL errors, log at debug, and answer truncated or missing paths and relationship properties; the 2240 lookup is not scoped by graph; also the VLP path-function node fetch does not filter on `graph_id`. Raise, scope; test first (fixes41, fixes36)
- [x] R283 [FIX] `translator.py` ~3852 drops a WITH … ORDER BY item that fails to translate, so rows come back in the wrong order; ~6817 is the same kind: raise instead; TCK before/after (fixes41)
- [x] R284 [FIX] `khop2_count_fast` reads `^KG("deg2p")` without a freshness check (stale counts after any write); `NKGAccelLoader.Capabilities` still lists `"ppr"`, which `capabilities()["ppr_arno"]` reads; `CALL ivg.retrieve` returned `rrf_score` as text once under 5.4.0 (probe; reproduce). Test first each (fixes41, fixes36)
- [x] R285 [FIX] `test_460_r242_set_reads_with_value_e2e` fails on the release tip: under 5.4.1 a SET from a WITH column reads back as text (`'3'`), and under both drivers `... SET n.r = k RETURN ...` answers no rows (fixes36)

- [x] R286 [FIX] fixes40 leftovers: `MATCH (n) WITH n CREATE (:H {v: n.k})` fails "Unsupported argument type" (the CREATE binds the AST as a parameter; any graph); `r {.s, .u}` fails -29 and `properties(r).s` -25; with Arno loaded and no "gaps" capability `BuildNKG` keeps the dictionary, so leftover named-graph nodes stay (R274's fix does not hold there); a string `'true'` from a row list through a WITH reads as the boolean; JSON `1.0` from `CY_LIST_VAL` reads 1; a flagged UNWIND boolean through a later WITH reads 1; the OPTIONAL MATCH null-row fallback with SKIP keeps its old form. Test first each, both drivers (fixes40)

- [x] R287 [DEV] The `ivg_236_scratch` fixture (`tests/conftest.py`) ignores `IVG_NAMESPACE` and deploys packaged classes into the shared IVG236 without running `initialize_schema()`, so files using it fail on missing setup functions (`GRAPH_KG.CY_TYPED does not exist`, 9 of 13 in `test_025_varlen_path_functions.py`) and an agent's run overwrites IVG236 for everyone: take the namespace from `IVG_NS_IVG236` (R191 rule), run setup, and extend the R191 guard to fixtures (fixes42)

- [x] R288 [SECURITY] `ivg_arno_zf_call_2s(libpath, fname, …)` (and any `ivg_arno_*` wrapper taking a library path or function name) loads any library and calls any function in it for anyone holding EXECUTE plus the callout resource: arbitrary native code. Restrict to the resolved Arno library (R132's `LibPath`) and a fixed function allow-list, refuse anything else; test first with a caller naming another path/function (fixes42)
- [x] R289 [SECURITY] SQL-callable writers with no privilege check: `backfill_temporal_rows`, `rebuild_edge_indices` (and any other SQL-callable that writes a global or table): require the write privilege on the table they write (INSERT/UPDATE/DELETE as fits), as R280 does for SELECT; structural unit test like R280's for writers (fixes42)
- [x] R290 [FIX] `LedgerApply` writes node properties without regard to graph: `set_prop`'s UPDATE and `DeleteNodeRows` match by node id across every graph; scope to the revision's graph (as R281); test first with two graphs sharing an id (fixes42)
- [x] R291 [FIX] The VLP path-function route returns the wrong columns (`RETURN b, length(p) AS l` gives `a, b, l`; `RETURN b, relationships(p) AS r` one column named `relationships(p)`) and formats relationships as `":knows {w: 1}"` text where the SQL route gives `[{"type","props"}]`: one shape; test first (fixes42)
- [x] R292 [FIX] fixes43 leftovers: `OPTIONAL MATCH (:L {name:'b'})-[r:R]->() RETURN r` with an anonymous start returns no rows (one null row by hand); `WITH n.k + 1 AS k RETURN k` returns 4 for k = 2.5 (RETURN n.k + 1 is right); `MATCH (n) CREATE (:H {v: n.k * 2})` stores text; `WITH n.k AS k CREATE (:H {v: k})` turns a stored string `'7'` into 7; `WITH n CREATE (n)-[:R]->(:H)` makes one node for all rows (and with `{v: n.k}` fails "Unsupported argument type"); comparisons ignore the boolean flag (`v = true` and `v = 1` both true after UNWIND [true, 1]; a list's boolean through WITH `b = true` false; `WITH [1.0, 2] AS l WITH l[0] AS x RETURN x = 1.0` false). Test first each, both drivers (fixes43)

- [x] R293 [SECURITY] A namespace upgraded from 4.5.x keeps the legacy `SQLUser.ivg_arno_*` wrappers, which still open any library and call any function (the R288 hole), until `remove_legacy_objects` runs. Tom decided (2026-11-04): every `initialize_schema()` revokes EXECUTE on the legacy `SQLUser.ivg_arno_*` wrappers from every grantee except `%All` holders and reports what it revoked; the functions themselves stay. Test first on a 4.5.1-built namespace with a grantee; document in migration and ADMIN_GUIDE §3 (fixes44)
- [x] R294 [FIX] fixes44 leftovers: `rebuild_edge_indices(pSchema)` pastes `pSchema` into `BUILD INDEX` unvalidated (validate as an SQL identifier); the path-function route keeps old shapes when RETURN has an aggregate or expression, and `_execute_var_length_labeled`'s `RETURN r` builds `[":TYPE"]`; with two parallel same-type edges `_vlp_qualifiers` returns the first edge's qualifiers, not the walked edge's; `tests/e2e/test_host_conformance.py` hard-codes `NAMESPACE = "IVG236"` and the FHIR files `"IVGFHIR"`. Test first each (fixes44)

- [x] R295 [FIX] fixes45 leftovers (all on the release head): under 5.4.1 `UNWIND range(1, 2) AS i WITH i RETURN i` answers text; `MATCH (n) WITH n RETURN id(n)` fails -29; `RETURN elementId(n)` fails -359; on the native path an empty-string property reads `'\x00'` (SQL path `''`); a leading OPTIONAL MATCH followed by a WITH answers no row instead of the null row when the label has nodes; an OPTIONAL MATCH whose start has no label but whose target does (`(x {name:'b'})-[r]->(:L)`) answers no null row. Test first each, both drivers (fixes45)

- [x] R296 [FIX] `MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id` answers no rows with SQLCODE -23 in `error` (the outer ORDER BY names `n0`, which exists only inside the derived table); and `MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})` creates nothing, no exception, `error` None (the -23 only logged): a silent no-op write. Test first both, both builds (docsgate)

- [x] R297 [FIX] Error semantics: `WITH n, n.k / 0 AS z RETURN z` answers `nan` for an integer divided by zero (openCypher raises), and `WITH n.k / 0 AS z CREATE (:KD2 {v: z})` writes nodes from it; `n.id * 2` and `n.id - 1` over strings answer null (openCypher TypeError). Raise as openCypher does, or list each in KNOWN_ISSUES if the TCK disagrees; test first (fixes47)

- [x] R298 [FIX] fixes46 leftovers: `OPTIONAL MATCH (a:L {name:'c'})-[:knows]->(b) RETURN b.name, count(*)` answers `[null, null]` (by hand `[null, 1]`, and `count(b)` 0; the fallback's null items, fixed-length patterns too); a relationship number Cypher CREATE stored as text (`{"w": "7"}`) reads as text in the variable-length SQL route's `r` and the path qualifiers (read-side: type it as `properties(r)` does; no writer change); the labeled route for a non-string, non-integer property map (`[r*1..2 {w: 1.5}]`) ignores the start's property map, answers only `r` for `RETURN b.name, r`, and checks only the target's incoming edges. Test first each, both drivers (fixes46)

- [x] R299 [FIX] Upgrade deploy: on ivg-iris-enterprise IVGFIX, `initialize_schema()` logged "IVG setup: ObjectScript deploy error: IVG.Percentile: ERROR #5083: Stored procedure name is not unique: Percentile_PCONT" (a warning, `warnings` empty, not in `errors`) and a later run did not. 4.5.1's `IVG.Percentile` projects `PCONT`/`PDISC` as SqlProcs; 4.6.0 moves them to `Graph.KG.SqlProcs` (`Percentile_PCONT`, R084). Reproduce in a namespace 4.5.1 built (frozen-DDL rule), make the first 4.6.0 setup deploy with no error, and make a deploy error reach `errors`/the dry run rather than only the log; test first: after the first setup both functions answer and no class failed (2026-11-05)

- [x] R300 [FIX] fixes48 leftovers, arithmetic (all measured on the release head): `+` with a stored string (`n.id + 1` null, `n.id + n.id` 0, `n.id + 1.5` 1.5, `WHERE n.id + 1 = 'a1'` matches nothing; openCypher concatenates); a string starting with `P` goes to the duration path (`n.name * 2` for `'Paris'` null, not TypeError); `SET n.q = n.k % 2` fails SQLCODE -1 for any divisor and `SET n.q = n.k / 0.0` -400 (openCypher Infinity); `WHERE $a / n.k > 1` with a stored 0 fails -400 `<DIVIDE>` without naming ArithmeticError; a constant division under a row condition (`CASE WHEN n.k > 9 THEN 1 / 0 END`) raises at Query Open when no row takes the branch; a string element inside a list predicate reads as 0; on a null row `count(*) + 1` answers null (2); `WITH [1,2] AS l RETURN l['a']` answers `'1'` (TypeError) and `RETURN n.k ^ 'a'` no rows and no error. Test first each, TCK before/after under both drivers (fixes48)

- [x] R301 [FIX] fixes48 leftovers, relationship lists: over a variable-length relationship list `[x IN rs | x.w]` answers `[null,null]`, `UNWIND rs AS x RETURN x.w, properties(x)` null and `{}`, `size(rs)` the JSON text's length (71); a fixed-length `RETURN r` still shows stored text (`{"w": "7"}`); a list-valued variable-length property map (`[r*1..2 {l: [1]}]`) still takes the engine's labeled route, which reads the start by labels only; and `test_460_r248::test_a_variable_length_path_and_target_read_their_stored_types` fails on the release head. Test first each (fixes48)

- [x] R302 [FIX] `tests/unit` with the enterprise container up (the live cases the offline gate does not run) has 7 failures on the release head (a95e0729, fixes49's run): `test_203` TestTemporalArithmetic a/b (`2 == 2.5`); 5 in `test_snapshot` TestSnapshotE2E (`<LIST ERROR>` reading `^IVF` in the shared namespace); `test_weighted_fallback_to_unit_weight` (`ValueError` on the weight property). Decide per test: product defect (fix, test first) or a test that reads a shared namespace or a 4.5.x answer (make it own its namespace per R191, or assert the 4.6.0 answer with a reason); the full live unit suite green (fixes49)

- [x] R303 [FIX] `initialize_schema()` falls back to `LoadDir("/tmp/src/")` when the packaged class deploy raises, ignores that load's status, and so compiles whatever sources a server's `/tmp/src` holds (the test container's are pre-R084): a stale or foreign class set installed silently. Remove the fallback or limit it to sources IVG wrote and verified; the failure goes to the reply (R299's `objectscript_failed`/`warnings`); test first (fixes49)

- [x] R304 [FIX] Data written wrong (meets the freeze bar): `CREATE ()-[:Z {l: [1, 2]}]->()` stores the relationship property as the text `"[Literal(value=1), Literal(value=2)]"` (the parser's AST repr), so `r.l` answers that string; `create_edge(qualifiers={"l": [1]})` stores a JSON list. Store a Cypher-written list as the API does; check node properties, MERGE and SET for the same; test first; say in the CHANGELOG how to find rows already written so (fixes50)

- [x] R305 [FIX] Data lost or written wrong (meets the freeze bar): `MATCH (a),(b) CREATE (a)-[:T {l: [1, 2], w: 7}]->(b)` between matched endpoints stores no properties at all (`w` lost too; `properties(r)` is `{}`); `SET r.m = [3, 4]` stores the JSON text `"[3, 4]"`, so `r.m` reads as a string. Store both as R304 does; test first; check 4.5.1; CHANGELOG with how to find affected rows (fixes50)

- [x] R306 [TEST] Algorithm oracle (Tom, 2026-11-06: "write correct unit tests and fix why these were not tested"):
  generated small graphs with adversarial shapes run through every algorithm method and `CALL` procedure against
  networkx (scores, sets, order, cut); fixes the tier-1 defects it exposes (isolated nodes, closeness `both`, JSON id
  escaping) (oracle-algo)
- [x] R307 [TEST] Value-typing matrix: every writer (API, bulk, NDJSON, Cypher CREATE/SET/MERGE) x every reader (native,
  SQL) x both drivers over an adversarial value set; pins the documented number-as-text behaviour; fixes the `any(...)`
  text answer and duration ordering (oracle-values)
- [x] R308 [TEST] Cypher differential: the native and SQL executors as each other's oracle over generated queries and
  data, plus hand-specified expectations for disagreements; fixes the labeled variable-length start (oracle-cypher)
- [x] R309 [DEV] Gate integrity: every E2E file runs in the gate (a skip in a gate run fails unless listed with a
  reason), scratch namespaces provided by a session fixture so the 46 IVG460-gated files run in the normal suite, both
  drivers in ci-parity, a lint against tests whose expected answer is captured IVG output (gate-infra)

- [ ] R310 [TEST] The TCK harness swallowed setup errors (`graph_setup.py` `except Exception: pass`), so scenarios whose
  setup failed ran on an unbuilt graph, and every recorded TCK pass ran on Community 2026.1 only. Setup errors fail the
  scenario; full TCK on enterprise 2026.3 and Community 2026.1 under both drivers; fix every IVG defect it shows (first:
  Match4 [4], a CREATE over a collected node list failing `<SUBSCRIPT>` on 2026.3) (fixes64, merged 276fbb3b; open:
  Comparison2 [3], a 2026.3 `<LIST>LoadTableFunction` when a JSON_TABLE source expands a long stage column, a size
  threshold whose TCK result flips with named graphs present and cached-query order, oracle-cypher)

- [x] R311 [SECURITY] Values that reach SQL, Cypher, ObjectScript or a global unescaped (sec-audit's sweep): the
  non-translator half is merged (b8b7d70b: embed selectors over `POST /admin/embed`, embedded `_inline_params`, snapshot
  restore, vector text, registry route names in GRANT/Eraser/PyOps, a parser hang); the translator half (aliases,
  parameter map keys, JSON-path keys, labels, timezone literals, the edge-scan `$p` value, injectable by default in
  3.0.0-4.2.x) and a writer-role escalation through `^IVG.CypherCompiled` and `^ArnoAccel("lib_path")` (sec-translator, merged bd750311: battery 662 cases, 0
  breakouts, also under `IVG_EDGESCAN=1`; TCK touched areas 907/907 both drivers; offline 21805 passed)

**Deferred to 4.6.1** (scope freeze at R303, Tom 2026-11-05; found after the freeze, below the bar of security, data
loss, a doc example's wrong answer or a walkthrough blocker/wrong item):

- `test_203` TestCalendarAccessors::test_datetime_calendar_fields_with_zone_name fails under driver 5.4.1 only (the raw
  cursor reads `'45'`, 5.4.0 `45`); already failing on the release head (fixes51)
- `test_ivf_index` leaves its `TEST_*:vidx` IVF indexes in the session namespace (fixes51)
- `RETURN n.id, any(...)` beside another column answers `'1'` as text (alone it answers 1), enterprise 5.4.0 (fixes50)
- The engine's labeled variable-length route reads the start by labels only, for property maps SQL does not check (a
  float in a list, a map) (fixes50)
- Gen "sync" mode compiles a generated plan class in the caller's process; a plan that fails to compile leaves that
  process's later SQL compiles failing (`<UNDEFINED>lviter+16^%qaqpnl`). The default "job" mode compiles elsewhere.
  Found through `test_257`'s broken-build tests (e2efix-temporal, e2efix-vectors)
- `Adjacency.Indexed()` and `ensure_indexes` accept a plain index named `idx_edges_adj`; writes then stop maintaining
  `^KG`; `verify_graph` reports it, `status()` does not (e2efix-temporal)
- `test_r150_ledger_index_reload` errors in a namespace whose schema is `Graph_KG` (it expects RAG) (e2efix-temporal)
- `test_stress_ingest::test_bulk_ingest_throughput_10k` fails under container load (~210 e/s; ~10K e/s alone)
- `test_460_r192_all_graphs_procedures_e2e`, `test_460_r235_upgrade_sqlprocs_e2e` and `test_hla_kg_e2e` leave BM25
  indexes behind (e2efix-vectors)
- `test_230_migration::test_the_kg_rekey_leaves_every_procedure_in_place` leaves default-graph registry rows pointing at
  `kg_NodeEmbeddings_optimized` and `kg_EdgeEmbeddings` when run out of the suite's order (e2efix-vectors)
- `ClosenessGlobal` and `ClosenessGlobalMSBFS` read direction `both` as `out`, so `ivg.closeness` and
  `closeness_centrality(direction="both")` answer out-scores while no named graph has edges (read in the code, not run;
  fixes57); `kg_CDLP` has no converged flag; `IRISGraphStore._closeness_serverside` is never called
- Neighbourhood betweenness in `USER` with Arno loaded: check `kg_betweenness_neighborhood_v` against directed
  networkx (it is no longer called) for the unfiled Arno findings (fixes57)
- Include nodes with no edges in algorithm answers: PageRank over every `node_count()` node, `n = node_count()` for
  degree and classical closeness, a row for every node (component of its own, coreness 0, 0 triangles, score 0)
  (fixes60; documented as a KNOWN_ISSUES entry for 4.6.0)
- `Communities.cls` and `Centrality` write node ids into JSON without escaping quotes; ORDER BY over durations sorts by
  their text (`P1D` before `PT1H`), which the TCK accepts (fixes60, unverified)
- Setup registers `kg_EdgeEmbeddings` in `embedding_registry` with `kind = 'node'` (fixes61)
- An `UNWIND` of a list concatenation (`l1 + l2`, built as JSON text before `JSON_TABLE` reads it) reads a `''` element
  as null (scout of fixes68's `json_table_text`; not run)
- An `UNWIND` of a parameter list longer than 900 elements or holding a non-scalar goes through `JSON_TABLE`, where a
  `''` element reads as null (the 900-element UNION path R310 added keeps it) (fixes68 scout; not run)
- The literal shortestPath JSON (`literal_paths_json`) spells a `''` node id or relationship property as `""`, which
  `JSON_TABLE` reads as null (fixes68 scout)

**Gate A**: R003–R016 and R056–R311 tests green; unit suite green; TCK areas from R005 green.

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

- [x] R024 [P] [DOC] Unit test `tests/unit/test_api_reference_fresh.py`, written first: `scripts/dev/gen_api_reference.py --check` exits 0 (the committed `docs/API_REFERENCE.md` equals a fresh generation); every public method of `IRISGraphEngine` and `IRISGraphOperators` appears with its signature; none has an empty summary
- [x] R025 [DOC] Write `scripts/dev/gen_api_reference.py` (inspect signatures and the first docstring paragraph; group by mixin; stable order) and generate `docs/API_REFERENCE.md`; fill every empty or misleading docstring summary it reports (expected: some in `_engine/*.py`); R024 green

### C4. KNOWN_ISSUES split

- [x] R026 [DOC] For each of KNOWN_ISSUES' 119 sections, record in `specs/release-4.6.0/known-issues-triage.md`: fixed (with the CHANGELOG or commit that fixed it), open (with a reproduction run on the 4.6.0 build: command and output), or "cannot confirm" (and why)
- [x] R027 [DOC] New `docs/KNOWN_ISSUES.md`: only the confirmed-open entries, one short entry each (symptom, versions affected, workaround, what to check), grouped "For application developers" / "For IRIS operators"; everything else verbatim in `docs/dev/notes/known-issues-history.md`, "cannot confirm" entries marked so; update the tests that read the file (`tests/unit/test_236_known_issues.py` and the others `grep -rl KNOWN_ISSUES tests` lists) to read the half that holds what they check
- [x] R028 [DOC] Add as open issues for 4.6.0 anything A did not fix that the walkthrough found and R026 confirms (e.g. NR16's default-graph key `0` if not documented elsewhere)

### C5. Reader-facing docs, top to bottom (each written with its doc-test markers from D)

- [x] R029 [DOC] `README.md`: what IVG is, install, first query (with `# →` answers), "Where IVG lives in a namespace" (the Luca answer: one namespace, the IVG Schema, classes compiled there by `initialize_schema()`, nothing in `%SYS`; Arno optional and not distributed), feature table without IPM, two reading paths (application developer → QUICKSTART, USER_GUIDE, API_REFERENCE; IRIS operator → ADMIN_GUIDE), links checked
- [x] R030 [DOC] `docs/setup/QUICKSTART.md`: the compose recipe, install, load, query, one algorithm, each with its answer; no link to `PYTHON_SDK.md` (NR10); `docs/setup/DATA_FORMATS.md` reviewed against the loaders
- [x] R031 [DOC] `docs/USER_GUIDE.md`: task-oriented; adds global PageRank, shortest path (with the R005 behaviour) and BFS (NR10); the default graph's key explained once (NR16); every example with its answer; links to API_REFERENCE for signatures
- [x] R032 [DOC] `docs/ADMIN_GUIDE.md` as the single operator document, with OPERATIONS folded in and `docs/OPERATIONS.md` removed (links redirected), organised by an operator's questions: what IVG installs and where (spec 262 inventory, T061/T062); privileges needed for setup and for use (NR's "privileges never stated"); optional components (Arno: per-process, not distributed, no `%ZSTART`; `Graph.KG.Service`; `FHIRGraphSyncTask`; the run-time compiled `IVGC.*` plans and `^IVG.CypherGen("mode")`); backup (globals incl. `^IVG.AlgoCache`, `^ArnoAccel`, `^IVG.Deploy`); upgrade and `remove_legacy_objects`; inspecting with Portal and `iris session` (the `%ExistsId` check in the right namespace); env vars for users only; troubleshooting with stale entries removed (#5 `Graph.KG.Edge`; #4 reworded) (NR1, NR2, NR3, NR12, NR14, NR15)
- [x] R033 [P] [DOC] `docs/ADMIN_API.md`, `docs/ledger-prometheus-hook.md`, `docs/setup/IRIS_PASSWORD_RESET.md`, `docs/iris-sql-quirks.md`: reviewed against the code; operator or developer audience stated at the top; examples marked for D
- [x] R034 [P] [DOC] Feature docs `docs/FHIR_GRAPH.md`, `docs/SEMANTIC_LAYER.md`, `docs/COMMUNITIES.md`: reviewed against the code and the 4.6.0 behaviour (WCC/CDLP/PageRank changes); examples with answers
- [x] R035 [P] [DOC] Benchmarks: merge `docs/BENCHMARKS.md` and `docs/performance/BENCHMARKS.md` into one `docs/BENCHMARKS.md` (dated numbers, the run that produced each); `docs/performance/GRAPH_ALGORITHMS.md`, `DRKG_SCALE.md` reviewed or moved to `docs/dev/notes/`
- [x] R036 [P] [DOC] `docs/architecture/`: `ARCHITECTURE.md` corrected (embedded-Python claim, NR17; the IVG Schema), `ACTUAL_SCHEMA.md` regenerated or checked against a fresh namespace, `rust-accelerator.md` and `embedded_python_architecture.md` reviewed; audience "for contributors and curious operators" stated
- [x] R037 [P] [DOC] `docs/demos/*.md` and `docs/notebooks/README.md`: each demo runs on 4.6.0 (or is marked with a reason) and links current docs
- [x] R038 [P] [DOC] `docs/migration/`: `upgrading-from-older-releases.md` updated through 4.6.0; new `v4.6.0.md` (spec 262 T059's consumer section included); `docs/releases/` gains nothing (release notes stay in CHANGELOG)
- [x] R039 [DOC] `CONTEXT.md` and `docs/adr/`: glossary terms used by the rewritten docs exist (IVG Schema, Graph, Graph Key, Native Reader…); ADR index line in ADMIN_GUIDE's "further reading"

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
- [x] R045 [DT] Make every doc pass R042 and R043 (fix the doc, or the code where the doc states the intended behaviour and the code is wrong: then a test first, as in A)
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

- [ ] R051 [REL] (drafted b4a43987; fixes21/22 and later batches to add) CHANGELOG 4.6.0 complete (Security, Fixed, Changed with the approved breaks called out, Added, Removed, Docs); lint
- [ ] R052 [REL] Run every gate on the final commit (plan.md "Gates"); record in `PRE_RELEASE_CHECKLIST.md` "Sign-off" a 4.6.0 entry in the 4.5.1 entry's format, with commit, counts and spec 262's speed-gate table
- [ ] R053 [REL] On Tom's explicit word only: push `release-4.6.0`, watch CI, fast-forward `main`, push, tag `v4.6.0` (annotated), push the tag, build from a clean worktree at the tag, `twine check`, `twine upload`, `gh release create` from the CHANGELOG section, clean-venv install check with `-I -W error`, publish record committed to the checklist
- [x] R054 [REL] Draft for Tom (he sends it): the reply to ArnolfJp with the 4.6.0 version and date and the credit line they chose (drafted 2026-11-05 in ~/.cache/ivg-bench/rel-460/drafts/R054-R055-drafts.md, with the R055 bus message; Tom fills the date and credit and sends)
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
