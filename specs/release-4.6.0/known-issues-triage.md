<!-- markdownlint-disable MD013 MD024 -->

# KNOWN_ISSUES triage for 4.6.0 (R026)

Every `##`/`###`/`####` section of `docs/KNOWN_ISSUES.md` at `09c2adb8` (119 sections), in document order, checked
against the code on branch `460-kitriage` (= `09c2adb8`). Input for R027.

## How this was checked

- Python: `/Users/tdyar/ws/iris-vector-graph/.venv/bin/python` with `PYTHONPATH=<worktree>` (`iris_vector_graph.__file__`
  checked to be the worktree's). Driver `intersystems-irispython` 5.4.0.
- IRIS: `ivg-iris-enterprise` (irishealth 2026.3.0AI.113.0), port 31972, namespace `IVG460KI` only, `IVG_NAMESPACE=IVG460KI`
  on every run. Engine built as `IRISGraphEngine(conn, namespace="IVG460KI", embedding_dimension=8)` then
  `initialize_schema()`. Arno library configured there (`^ArnoAccel("lib_path") = /tmp/libarno_callout.so`).
- Tests named below were run one file or one test at a time, never `tests/e2e` as a whole. "N passed" means that run.
- Fix commits come from `git log -S` (pickaxe) and the CHANGELOG. `cc546a29` is the 4.0.0 spec 227/230 commit that
  carries most 4.0.0 fixes.
- Verdicts: `fixed`, `open`, `not a defect`, `cannot confirm`. A group heading (`##` with entries under it) gets the
  verdict of its entries. "(262)" / "(A)" marks an area spec 262 or workstream A is changing now.
- Audience for open entries: "application developer", "IRIS operator", or "internal" (only IVG's own test suite or
  this workspace is affected; R027 should move these to the history file, not publish them).

## Entries

### 1. L12 `## Embedded mode (inside IRIS, measured 2026-10-11 on Build 154)`

- **Verdict:** open (group: entry 2 open, entry 3 fixed).

### 2. L14 `### A SELECT of a VECTOR column returns no rows under an embedded cursor`

- **Verdict:** open.
- **Evidence:** IRIS raises while iterating a VECTOR row in embedded Python, run in `IVG460KI`:
  `docker exec -e IRISNAMESPACE=IVG460KI ivg-iris-enterprise /usr/irissys/bin/irispython -c "import iris; rs = iris.sql.exec(\"SELECT TO_VECTOR('1,2,3', DOUBLE) AS v\"); [tuple(r) for r in rs]"`
  gives `RuntimeError <OBJECT DISPATCH>next+9^%SYS.Python.SQLResultSet.1 *Invalid argument type`.
  `EmbeddedCursor._materialise` (`iris_vector_graph/embedded.py:349-357`) wraps that loop in `except Exception: pass`,
  so `fetchall()` returns `[]` with no error.
- **Audience:** application developer.
- **One-line:** Inside IRIS (embedded Python, `EmbeddedConnection`), a `SELECT` that returns a VECTOR column answers an
  empty list with no error on 2026.3 (all 4.x releases); select `VECTOR_COSINE(...)` or a `CAST` of the column instead
  of the raw vector, and check a row count against the external driver.

### 3. L21 `### The 4.0.0 embeddings step logs no progress, and a failed edge-vector step is not resumed at the right width`

- **Verdict:** fixed in 4.2.3 (`6d5efdea`, "progress logging, edge-vector resume width check").
- **Evidence:** `migrations/graph_scoped_embeddings.py:874,902` log "Placing N rows" and "n of N rows placed";
  `:913` logs before the silent index build; `migrations/docs_and_edge_vectors.py:235-254`
  (`_reset_wrong_width_tables`) rebuilds an empty wrong-width staging/quarantine table and refuses a populated one.
  Not run (needs a 3.2.0 install at scale).

### 4. L28 `## Derived indexes (verified 2026-10-31, 4.5.0)`

- **Verdict:** cannot confirm (group: entry 5).

### 5. L30 `### With an Arno library before 1.2.1 configured, a removed node stays in ^NKG's dictionary`

- **Verdict:** cannot confirm.
- **Why:** needs an arno-callout library older than 1.2.1. Code: `Graph/KG/TraversalBuild.cls:670-713` sweeps dead
  entries only when the library reports `nkg_dict_gaps >= 1`. In `IVG460KI` (library configured) `$meta("dictSwept") = 1`.
- **If R027 keeps it (IRIS operator):** with an arno-callout library before 1.2.1 configured (4.5.0+), deleted nodes stay
  in `^NKG`'s dictionary as isolated nodes in whole-graph algorithms; upgrade the library to 1.2.1, check
  `^NKG("$meta","dictSwept")`.

### 6. L43 `## Performance (measured 2026-10-10, 4.2.0 release gate)`

- **Verdict:** open (group: entries 8, 9 open; 7 cannot confirm).

### 7. L45 `### The Arno BFS speedup criteria (SC-008 to SC-010) are not met on this build`

- **Verdict:** cannot confirm.
- **Why:** needs `tests/benchmarks/bench.py --datasets S M --runs 20` on a quiet machine plus the same bench on v4.1.1; not
  run (shared container, other sessions active). A performance claim, not a wrong answer.

### 8. L59 `### A temporal purge leaves the endpoint nodes (reported 2026-10-25, 4.4.1; option added, default unchanged)`

- **Verdict:** open (default unchanged; `retire_bare_nodes` exists).
- **Evidence:** 5 temporal edges `tsrc -EMITS-> ttgt0..4`, then `e.purge_raw_before(t0 + 100)`:
  `PurgeResult(deleted=5, skipped=0, nodes_retired=0)`; `SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH 'ttgt'`
  is 5 before and 5 after; rdf_edges for `tsrc`: 0.
- **Audience:** application developer.
- **One-line:** `purge_raw_before`/`purge_before` keep every endpoint node of the purged edges by default (4.4.1 on), so a
  one-target-per-event workload grows edgeless nodes forever; pass `retire_bare_nodes=True`, clear the backlog with
  `erase_bare_nodes(graph, dry_run=True)` then `erase_bare_nodes(graph)`; check `nodes_retired` in the result.

### 9. L79 `### A temporal purge misses spec 254's cost target (SC-004) (measured 2026-10-22, 4.3.0)`

- **Verdict:** open.
- **Evidence:** 2,000 one-node-per-event temporal edges, `purge_raw_before(t0 + 2001)`:
  `PurgeResult(deleted=2000, ...)`, 44.3 us per purged edge (SC-004 target about 6 us).
- **Audience:** IRIS operator.
- **One-line:** a temporal purge costs about 35-45 us per purged edge (4.3.0 on), over the 6 us target, because each
  temporal-only `rdf_edges` row is deleted with its index maintenance; the answer is correct; size purge windows by
  that rate.

### 10. L89 `### The undirected-hop rewrite makes a query shape's first call compile longer (measured 2026-10-19, 4.3.0)`

- **Verdict:** open (by design; not re-measured at SNB scale).
- **Evidence:** `translate_to_sql` of `MATCH (a:UD {name: $n})-[:L]-(b)-[:L]-(c)-[:L]-(d) ... RETURN count(*)` emits 8
  `UNION ALL` branches and no `_ue` CTE. On a 6-node graph the first call took 0.107 s, the next two 0.001 s. BI14's
  11 s first call needs SNB SF0.1, not run.
- **Audience:** application developer.
- **One-line:** a query with up to three anchored undirected hops compiles up to 8 branches on its first call (4.3.0
  on), seconds for large shapes, paid once per shape; warm each shape at startup if first-call latency matters.

### 11. L99 `## Test and build environment (verified 2026-09-16, re-measured 2026-09-18)`

- **Verdict:** open (group: entries 20, 21, 26, 29, 30 open, all internal; 12, 13, 16-19, 23-25, 28, 31-33, 37 fixed).

### 12. L101 `### Five integration files segfault on their first test`

- **Verdict:** fixed (no longer reproduces; no fixing commit identified, driver now 5.4.0).
- **Evidence:** each file alone, `IVG_NAMESPACE=IVG460KI IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 python -m pytest -q -p no:randomly tests/integration/<f>.py`:
  `test_cypher_multi_type` 1 passed, `test_cypher_rd` 8 passed, `test_cypher_rel_vars` 2 passed,
  `test_cypher_single_type` 2 passed, `test_cypher_untyped` 1 passed. No crash.

### 13. L144 `### ~70 integration failures and 26 errors against the v3 API, all pre-existing`

- **Verdict:** fixed (tests migrated in 4.0.0, `cc546a29`); one internal residue.
- **Evidence:** the six files the entry names (`test_query_engine_special`, `test_nodes_edges_deep`,
  `test_schema_and_engine_procs`, `test_lazykg_algorithms`, `test_vector_engine_deep`, `test_diabolical_qa`): 252 passed,
  1 failed. The failure is a stale message assertion, `test_nodes_edges_deep.py:188`: expects
  `'set_edge_weight failed: weight fail'`, code logs `set_edge_weight failed, no row changed: weight fail`
  (`nodes_edges.py:1062`). Internal.

### 14. L202 `### One unit test depends on container state: the k-hop fast path`

- **Verdict:** cannot confirm.
- **Why:** state-dependent; on a fresh `IVG460KI`
  `tests/unit/test_193_bfs_nkg_fast_path.py::TestKhopFastPathE2E` is 1 passed. The flaky case needs the shared USER `^NKG`.
  Internal either way.

### 15. L225 `### A Cypher DELETE with no USE GRAPH deletes the node in every graph its MATCH binds`

- **Verdict:** not a defect (by design); the 4.1.0 fix it records holds.
- **Evidence:** `Patient/kp3` created in `''` and `kig2`; `MATCH (n {id: 'Patient/kp3'}) DETACH DELETE n` leaves no row
  in either. Pinned by `tests/unit/test_230_default_graph_predicate.py::test_no_use_graph_clause_still_spans_the_namespace`
  (passed). R027 may keep it as a usage note beside entry 115.

### 16. L241 `### ~~One integration test fails on the tree: the snapshot plan misses the FHIR tables~~ (fixed in 4.1.0)`

- **Verdict:** fixed in 4.1.0 (CHANGELOG v4.1.0, "A snapshot dropped a FHIR graph's bookkeeping").
- **Evidence:** `tests/integration/test_snapshot_inventory.py` 10 passed.

### 17. L251 `### Fixed in 4.0.0: test_embeddings_api.py depended on the column's leftover width`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_clear_embedding_tables` in `tests/integration/conftest.py`). Not rerun.

### 18. L282 `### ~~conftest.py cannot tell a stopped container from a missing one~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`63850e42`).
- **Evidence:** `tests/unit/test_230_container_identity.py` 13 passed.

### 19. L312 `### tests/python had no fixtures, so iris.cls handed back a MagicMock (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. `tests/python/conftest.py` exists. Not rerun.

### 20. L358 `### The quickstart claims host port 1972, which belongs to opsreview-iris`

- **Verdict:** open (internal: a conflict in this workspace only).
- **Evidence:** `docker-compose.yml:20` publishes `"1972:1972"`. For a user, 1972 is IRIS's standard port, so this is no
  defect outside this machine.

### 21. L382 `### IVG_PORT is inert on the two paths the fixture actually takes`

- **Verdict:** open (internal).
- **Evidence:** `tests/conftest.py:466` (OrbStack DNS) and `:482` (container IP) still connect on hard-wired 1972; only
  `:496` reads `IVG_PORT`.

### 22. L420 `### irispython-dx-iris holds a stale partial IVG deployment`

- **Verdict:** cannot confirm.
- **Why:** that container is not running (`docker ps`); it is another project's, not IVG's code.

### 23. L428 `### ~~_detect_arno's smoke probe disables a healthy Arno~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`804c4e7f`).
- **Evidence:** `tests/unit/test_arno_probe_seed.py` passed; live `engine.status().arno.loaded = True` in `IVG460KI`.

### 24. L463 `### ~~The Rust callout and the ObjectScript class disagree on what a scored node is called~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_scored_node_rows`).
- **Evidence:** live capabilities still list `algorithms = ['khop','ppr','random_walk','export_adjacency']`, and
  `docs/arno-reader-proof.json` proves only `pagerank`, so the latent WCC/CDLP/subgraph shape mismatches the entry
  lists are still unrouted, not live.

### 25. L506 `### ~~User.PageRankEmbedded does not compile~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `iris_src/src/PageRankEmbedded.cls:5` is `ComputePageRankCore`; no method starts with `_`.

### 26. L519 `### ruff check . reports 2048 findings`

- **Verdict:** open (internal).
- **Evidence:** `pyproject.toml` has no `[tool.ruff]`. `ruff check . --exclude .kitriage --statistics` (ruff 0.15.14):
  2,393 findings (706 F401, 674 F405, 295 F841, 211 E702, 191 F541, 72 E722, ...). F821 is now 0;
  `ruff check --select F821,F811,E711,E712 .` gives 55 (53 F811, 2 E712).

### 27. L538 `### rdflib's ConjunctiveGraph is deprecated, and the suite ignores the warning`

- **Verdict:** open.
- **Evidence:** `_engine/_rdf_utils.py:102,110` and `_engine/snapshot.py:412,435` build `ConjunctiveGraph`;
  `pyproject.toml:258` filters the warning and `:75,86` pin `rdflib>=6.0.0,<8`.
- **Audience:** application developer.
- **One-line:** IVG's RDF extra pins `rdflib<8` (all 4.x) because its SPARQL/SHACL export uses `ConjunctiveGraph`, which
  rdflib 8 removes; keep rdflib 7 in the same environment; check `pip show rdflib`.

### 28. L552 `### The container drifts from the tree, and the deploy script used to hide it`

- **Verdict:** fixed (the script part): `scripts/enterprise-container.sh:282-284` asks `%SYSTEM.Status.IsOK` and prints
  `GetOneErrorText`. The drift itself is container state (internal), not re-measured.

### 29. L578 `### Four size/complexity gates from spec 186 have been red since spec 187`

- **Verdict:** open (internal).
- **Evidence:** `SKIP_IRIS_TESTS=true python -m pytest -q tests/regression/test_complexity.py tests/regression/test_module_size.py`:
  4 failed. Sizes now: `translator.py` 23,031 lines (budget 4,300), `iris_sql_store.py` 3,649 (2,000), `_engine/schema.py`
  2,659 (2,000), `TemporalIndex.cls` 1,580 (800); 66 translator functions over cc 25 (was 45). The doc's table is
  stale.

### 30. L621 `### Two fixtures reshape shared state, and the failure lands on another test`

- **Verdict:** open (internal).
- **Evidence:** `tests/e2e/test_stress_ingest.py:12` still builds `IRISGraphEngine(iris_connection, embedding_dimension=4)`.
  Seen live as the same class of message: after a 16-wide engine initialised `IVG460KI`, an 8-wide one logged
  `CRITICAL: Graph_KG.kg_NodeEmbeddings.emb is VECTOR(DOUBLE, 16) but the engine is configured for 8`.

### 31. L645 `### Failures in the 4.1.1 gate run`

- **Verdict:** fixed (historical record; CHANGELOG v4.1.1). The `tests/regression` part is entry 29. Individual cases not
  rerun.

### 32. L674 `### ~~An Arno library call moved the process to USER: 135 E2E failures outside USER~~ (fixed in 4.4.1)`

- **Verdict:** fixed in 4.4.1 (`abfbd48f`). `tests/e2e/test_arno_namespace_kept_e2e.py` not run (it drives USER, which
  this task may not touch).

### 33. L692 `### ~~Three SQL table bridge E2E tests fail, pre-existing~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/unit/test_sql_table_bridge.py::TestSQLTableBridgeE2E` 7 passed.

### 34. L710 `### Arno callout defects (re-verified for spec 236)`

- **Verdict:** cannot confirm (Arno's defects; each needs the gate run against a given library build).
- **Evidence seen:** `docs/arno-reader-proof.json` (arno-callout 1.2.1, 2026-11-01) proves only `pagerank`, as the entry
  says. Gate (`scripts/arno_reader_gate.py`) not rerun.

### 35. L743 `#### Open on 1.1.0+3b2123b8e7bb`

- **Verdict:** cannot confirm for the library bullets (need arno-callout 1.1.0 and x86_64 hardware); one IVG bullet fixed.
- **Evidence:** the IVG-owned bullet "`Traversal.BFSFastJsonSorted` with a predicate list stops after hop 1" does not
  reproduce: on chain `bp0-P->bp1-P->bp2-P->bp3`, `classMethodValue("Graph.KG.Traversal","BFSFastJsonSorted","bp0",'["P"]',h,"","out",0,"")`
  answers `SORTED:…:2` at h=2 and `SORTED:…:3` at h=3, the same counts as `[]`. Fixing commit not identified (candidates
  `df170518`, `748d6339`). The write-path coverage gap and "not verified" bullets are test gaps (internal).

### 36. L798 `#### Ruled out, or fixed`

- **Verdict:** fixed (each bullet cites its test). One internal test gap stays: no test asserts PageRank is unchanged by
  a weight-only `set_edge_weight`.

### 37. L841 `### ~~A Graph.KG package compile drops kg_RRF_FUSE~~ (fixed in 4.1.1)`

- **Verdict:** fixed in 4.1.1 (CHANGELOG v4.1.1: compiles the shipped classes with `CompileList`).
- **Evidence:** `migrations/kg_node_stores.py:251` calls `%SYSTEM.OBJ.CompileList`.

### 38. L859 `### Operational facts the 4.0.0 gate established`

- **Verdict:** not a defect (facts; the struck bullets fixed in 4.0.0/4.1.1).
- **Evidence:** live: `engine.is_ready` is a `method`; `nat.set(1, "^KGtriageProbe", "")` raises `<SUBSCRIPT>`;
  `tests/tck/__init__.py` and `tests/unit/tck/__init__.py` both exist; `tests/benchmarks/bench.py:24` defaults
  `IRIS_PORT` to 1972; the library lives at `/tmp/libarno_callout.so`, outside the image. The rebuild and port facts
  are internal; R027 could carry the Arno-after-rebuild fact to operators (A is changing `NKGAccelLoader.Load`).

### 39. L960 `## Graph scope (verified 2026-09-16)`

- **Verdict:** open (group: entry 43 open, entries 40-42 and 44-51 fixed).

### 40. L962 `### ~~The default graph is falsy, so '' reads as "every graph"~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/unit/test_230_default_graph_predicate.py` passed.

### 41. L991 `### ~~Graph.KG.EdgeScan.MatchEdges overloads 0~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`). `EdgeScan.cls:22,44` declare `MatchEdgesAllGraphs` as the only cross-graph read.

### 42. L1012 `### ~~A query with no USE GRAPH clause fans out across graphs, and the row count is wrong~~ (fixed in 4.3.0)`

- **Verdict:** fixed in 4.3.0 (`56de32f5`).
- **Evidence:** `tests/e2e/test_253_all_graphs_read_e2e.py` 8 passed; `tests/unit/test_253_graph_count_probe.py` passed.

### 43. L1049 `### Unscoped count(DISTINCT m) disagrees between the native and SQL paths when graphs share a node ID`

- **Verdict:** open.
- **Evidence:** `tests/e2e/test_253_id_anchor_e2e.py::test_a_distinct_count_over_graphs_sharing_a_node_id` (strict xfail)
  is XFAIL in `IVG460KI`: SQL counts ids (1), native counts (graph, node) pairs (2).
- **Audience:** application developer.
- **One-line:** with no `USE GRAPH` and one node id in two graphs, `count(DISTINCT m)` answers 1 or 2 depending on the
  execution path (4.3.0 on); say `USE GRAPH '<g>'`; check by running the query scoped to each graph.

### 44. L1066 `### ~~ledger.verify() diverges over relationships written before the ledger was enabled~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/integration/test_ledger_verify_pre_enable_writes.py` 4 passed.

### 45. L1081 `### ~~A duplicate create_edge costs about 30 ms: the failed insert is rolled back through the journal~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/e2e/test_duplicate_edge_cost_e2e.py` 6 passed; 20 duplicate `create_edge("dc_a","R","dc_b")` at
  5.39 ms each against 2.82 ms for a new edge (not 30 ms).

### 46. L1097 `### ~~In a multigraph, relationship equality treats parallel relationships as one~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/e2e/test_multigraph_rel_identity_e2e.py` 8 passed.

### 47. L1110 `### ~~USE GRAPH did not scope the reads inside a WITH stage~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/e2e/test_with_stage_graph_scope_e2e.py` 10 passed.

### 48. L1120 `### ~~approx_count_distinct over a variable-length hop drops the far end's checks~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0 (`4b4eaa08`).
- **Evidence:** `tests/e2e/test_approx_count_distinct_checks_e2e.py` 8 passed; `tests/unit/test_approx_hll_read.py` passed.

### 49. L1139 `### ~~The native path answered no rows for a label mapped to a SQL table~~ (fixed in 4.4.0)`

- **Verdict:** fixed in 4.4.0.
- **Evidence:** `tests/unit/test_253_native_declines_mapped_tables.py` passed; bridge E2E as entry 33.

### 50. L1147 `### ~~USE GRAPH read other graphs for some read shapes~~ (fixed in 4.4.2; every earlier release is affected)`

- **Verdict:** fixed in 4.4.2 (`1b3f079a`).
- **Evidence:** `tests/e2e/test_use_graph_scope_shapes_e2e.py` all passed (part of a 254-pass run);
  `tests/unit/test_use_graph_scope_every_alias.py` and `tests/unit/test_parser_union_terminates.py` passed. Its "Still
  open" `CALL { }` column order is entry 94 (open).

### 51. L1216 `### Nodes cannot sit in the default graph while their edge sits in a named graph`

- **Verdict:** fixed (since 4.0.0, `cc546a29`: `create_edge` registers both endpoints in the edge's graph,
  `_ensure_edge_endpoints`).
- **Evidence:** `create_node("dg_a")`, `create_node("dg_b")` in the default graph, then
  `create_edge("dg_a","REL","dg_b", graph="kig1")` returns `True`; `nodes` then holds `('', dg_a), ('', dg_b),
  ('kig1', dg_a), ('kig1', dg_b)`. The composite FK still refuses a raw-SQL edge whose endpoints are not in its graph
  (`SQLCODE -121`), which is the schema's intent. The entry's workaround is unnecessary for `create_edge`.

### 52. L1248 `## ~~graph_id writers and the scan that is supposed to catch them~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/unit/test_graph_id_tightening.py` passed.

### 53. L1279 `### graph_id predicates: one reconciled, one still asymmetric`

- **Verdict:** open, harmless (internal).
- **Evidence:** `iris_vector_graph/schema.py:1075` still emits `ALTER TABLE Graph_KG.rdf_edges ADD COLUMN graph_id
  VARCHAR(256) %EXACT NULL`; `tighten_graph_id_column` repairs it. The reconciled half is fixed (`a0b30ca5`).

### 54. L1304 `## ~~^KG layout leftovers~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`). Not rerun.

### 55. L1318 `### ~~Graph.KG.Eraser leaves the flat ^KG subscripts behind~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`). Not rerun.

### 56. L1345 `### A %NOINDEX bulk load left rows no reader could see (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/unit/test_bulk_loader_index_rebuild.py` passed.

### 57. L1387 `## Installing the SQL routines (verified 2026-09-20)`

- **Verdict:** fixed (group: entry 58 fixed, entry 59 not a defect).

### 58. L1389 `### ~~kg_Betweenness never existed, because one $SELECT will not go through the DDL parser~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/integration/test_shipped_routines_exist.py` passed in `IVG460KI`;
  `tests/unit/test_schema_procedures.py` passed.

### 59. L1424 `### SQLUser.STR_SPLIT's SQLCODE -300 is harmless duplication, not a failure`

- **Verdict:** not a defect (262 / A: SQL functions leave `SQLUser`; setup log noise).
- **Evidence:** a fresh `initialize_schema()` in `IVG460KI` logs, at DEBUG, `Optional procedure DDL skipped (non-fatal):
  CREATE OR REPLACE FUNCTION SQLUser.STR_SPLIT ... [SQLCODE: <-300>:<DDL not allowed on this table definition>]`;
  `SELECT SQLUser.STR_SPLIT('a,b,c', ',')` gives `["a","b","c"]`; 0 WARNING lines.

### 60. L1449 `## Cypher procedure calls (verified 2026-09-20)`

- **Verdict:** fixed (group).

### 61. L1451 `### ~~Four of the ivg.* procedures emitted SQL this build cannot run~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/integration/test_227_procedure_statements_execute.py` passed in `IVG460KI`;
  `tests/unit/test_api_security.py` passed.

### 62. L1492 `### ~~ivg.retrieve demanded an %Embedding.Config that no namespace had~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_retrieve_query_vector`).
- **Evidence:** `tests/unit/test_227_retrieve_embedding_source.py` passed.

### 63. L1542 `## Constraint errors after an index build (verified 2026-09-20)`

- **Verdict:** cannot confirm (group: entry 64).

### 64. L1544 `### Building a class's indices permanently breaks constraint-error decoding on other connections`

- **Verdict:** cannot confirm (driver 5.3.x not installed; 5.4.0 decodes the error itself).
- **Evidence:** venv driver is 5.4.0; IVG's guard `tests/unit/test_swallow_duplicate.py` passed. `pyproject.toml` does
  not require driver >= 5.4, so 5.3.x users can still hit it. The live test
  `tests/integration/test_list_error_decode_live.py` errors outside USER (`<CLASS DOES NOT EXIST> *IVGTest.DecodeDup`):
  its second connection is hard-wired to `namespace="USER"` (line 39, 45), an internal test defect.
- **If R027 keeps it (application developer):** with intersystems-irispython 5.3.x, after another connection rebuilds a
  table's indices, constraint errors on older connections read `<LIST ERROR> Incorrect list format`; use driver 5.4.0+
  or a fresh connection; check `importlib.metadata.version("intersystems-irispython")`.

### 65. L1593 `## Embeddings and vector width (verified 2026-09-18)`

- **Verdict:** open (group: entry 69 open, the rest fixed).

### 66. L1595 `### ~~A named graph cannot carry its own embedding model or dimension~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (spec 227, `cc546a29`). Not rerun.

### 67. L1628 `### ~~Callers that still cannot pass a graph~~, and one that read a column that does not exist`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** `tests/unit/test_operators_coverage.py` passed; `PyOps.cls:88` is `getExpectedDimension(graph, modelKey)`.

### 68. L1672 `### ~~get_procedures_sql_list(embedding_dimension=...) is inert~~ (removed in 4.0.0)`

- **Verdict:** fixed (removed) in 4.0.0.
- **Evidence:** `tests/unit/test_embedding_dimension_default.py` passed.

### 69. L1689 `### The dimension migration cannot see a second writer`

- **Verdict:** open.
- **Evidence:** engine A (`embedding_dimension=8`) and then engine B (16) each run `initialize_schema()` on the empty
  table: both report `needs_manual_migration: []`, `warnings: []`; B's call silently re-declared the column at 16. Then
  `A.store_embedding("sw1", [0.1]*8)` raises `EmbeddingIdentityConflict ... recorded dimension 16 but this writer
  declares 8`; B's write succeeds; registry rows read `dimension 16, set_by 'adopted'`.
- **Audience:** IRIS operator.
- **One-line:** two processes that call `initialize_schema()` on one namespace with different `embedding_dimension`
  values silently re-declare an empty vector column to the last caller's width (3.2.0 on); the other process's writes
  then fail with `EmbeddingIdentityConflict`; use one width per namespace (or per `(graph, model_key)` route); check
  `Graph_KG.embedding_registry`.

### 70. L1716 `### ~~A recorded width can go stale against the column it describes~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `reconcile_recorded_dimensions`).
- **Evidence:** in the entry 69 run the registry followed the column (16) on B's `initialize_schema()`.

### 71. L1758 `## Cypher writes (verified 2026-09-20)`

- **Verdict:** open (group: entry 75 open, 74 fixed with an open residue, 72-73 fixed).

### 72. L1760 `### ~~MERGE with inline node IDs could not be prepared~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_merge_literal_node_id`).
- **Evidence:** `MERGE (a:Person {id:'mx'})-[:LIKES]->(b:Person {id:'my'})` runs; the follow-up MATCH returns `['my']`.

### 73. L1781 `### ~~A CREATE after a zero-row MATCH wrote the node and not the edge~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_create_match_gate`).
- **Evidence:** `MATCH (a:Person {id:'missing'}) CREATE (a)-[:R]->(b:Person {id:'zr_new'})` leaves
  `COUNT(*) ... node_id = 'zr_new'` at 0.

### 74. L1799 `### A MATCH returning N rows still creates one node, not N`

- **Verdict:** fixed in 4.1.0 (`38c3e8dc`, "CREATE after MATCH makes one node per matched row"), with an open residue.
- **Evidence:** 3 `Gene` nodes; `MATCH (n:Gene) CREATE (c:Copy {tag: 'x'})` then `count(c)` = 3. `UNWIND` literal also 3.
  Residue: `MATCH (n:Gene) WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (c:Copy {src2: i})` raises
  `IntegrityError ... SQLCODE -119 ... Table 'Graph_KG.rdf_props', Constraint 'pk_props', ... key="src2"` and writes
  nothing (was a silent 0). `_create_row_key` returns None after a stage (`translator.py:6804-6809`), so one generated id
  serves every row. The translator docstring at `translator.py:6567-6569` still claims the N-to-1 deviation.
- **Audience:** application developer.
- **One-line:** a `CREATE` of a node without an `id` after a `WITH` stage (e.g. `WITH collect(..) AS ids UNWIND ids AS i
  CREATE (...)`) fails with SQLCODE -119 when more than one row reaches it (4.1.0 on); give the node an `id`
  (`CREATE (c {id: i})`) or create one per row from the caller.

### 75. L1834 `### CREATE of an identical edge raises SQLCODE -119 instead of adding a parallel edge`

- **Verdict:** open, changed: it no longer raises; it silently adds nothing.
- **Evidence:** `CREATE (a:DupT {id:'dupa'})-[:R]->(b:DupT {id:'dupb'})`, then twice
  `MATCH (a:DupT {id:'dupa'}), (b:DupT {id:'dupb'}) CREATE (a)-[:R]->(b)`: no error, `count(r)` = 1. With
  `set_multigraph('kimg', True)` three such CREATEs under `USE GRAPH 'kimg'` give `count(r)` = 3 (spec 234, 4.1.0).
- **Audience:** application developer.
- **One-line:** outside multigraph mode a Cypher `CREATE` of a relationship that already exists between the same pair is
  a silent no-op (openCypher adds a parallel one); turn on `engine.set_multigraph(graph, True)` for parallel edges, or use
  `MERGE`; check `count(r)` after the write.

### 76. L1852 `## Variable-length traversal (verified 2026-09-21)`

- **Verdict:** open (group: entry 80 open, plus new finding N1; the rest fixed).

### 77. L1862 `### ~~An inbound or undirected pattern came back with the source's successors~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`).
- **Evidence:** chain `vf0->vf1->...->vf5`, Arno loaded: `MATCH (x)<-[r*1..1]-(y) WHERE x.id = $id RETURN y.id` with
  `vf2` gives `[['vf1']]`; undirected gives `[['vf1'], ['vf3']]`.

### 78. L1885 `### ~~A property-bound variable-length pattern ignored its LIMIT~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `_extract_sql_row_limit`).
- **Evidence:** a 20-spoke hub, `MATCH (x)-[:SPOKE*1..1]->(y) WHERE x.id = $id RETURN y.id LIMIT 5`: 5 rows.

### 79. L1904 `### ~~A variable-length count from {id: $x} counted a node once per depth, and ignored the start's label~~ (fixed in 4.3.0, f9c292ad)`

- **Verdict:** fixed in 4.3.0 (`f9c292ad`).
- **Evidence:** `tests/e2e/test_vl_count_distinct_e2e.py` 21 passed; `tests/unit/test_vl_id_source_check.py` passed.

### 80. L1923 `### The ID-bound variable-length route answers (id, hops, pred) rows for an unaliased id`

- **Verdict:** open.
- **Evidence:** chain `vl0->vl1->vl2` plus `vl0->vl2`:
  `MATCH (s {node_id: $x})-[*1..2]->(m) RETURN m.id` gives columns `['id','hops','pred']`, rows
  `[['vl1',1,'N'], ['vl2',1,'N'], ['vl2',2,'N']]`; the same for `RETURN m.node_id`; `RETURN DISTINCT m.node_id` gives
  `[['vl1',1,'N'], ['vl2',1,'N']]`; `RETURN m.node_id AS i` gives `['i']`, `[['vl1'], ['vl2'], ['vl2']]`.
- **Audience:** application developer.
- **One-line:** `MATCH (s {node_id: $x})-[*a..b]->(m) RETURN m.id` (or `m.node_id`, unaliased) answers three columns
  `id, hops, pred`, one row per node per depth (all 4.x); alias the column (`RETURN m.id AS id`) to get the
  openCypher shape; check `result.columns`.

### 81. L1945 `### ~~RETURN DISTINCT over a variable-length path did not deduplicate~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (dedupe holds), but the same query shape gives a wrong answer: see N1.
- **Evidence:** `MATCH (a {node_id: 'vf0'})-[:NEXT*1..3]-(b) RETURN DISTINCT b.node_id LIMIT 20` gives 4 distinct rows,
  `vf1, vf0, vf2, vf3`; `vf0` (the start) should not be there.

### 82. L1959 `### ~~RETURN b.node_id came back as a column of NULL~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0.
- **Evidence:** `MATCH (a {node_id: 'vf0'})-[:NEXT*1..2]->(b) RETURN b.node_id` gives `[['vf1'], ['vf2']]`.

### 83. L1972 `### ~~approx_count_distinct answered a silent zero~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0, superseded in 4.4.0 (`4b4eaa08`).
- **Evidence:** `MATCH (a {node_id: 'vf0'})-[:NEXT*1..2]-(b) RETURN approx_count_distinct(b) AS c` gives `[[2]]`.

### 84. L1993 `## Bulk ingest and table statistics (verified 2026-09-21)`

- **Verdict:** fixed (group).

### 85. L1998 `### ~~IVG ran TUNE TABLE nowhere, so a graph-scoped lookup planned as a scan~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `GraphSchema.tune_tables`, `schema.py:870`); `initialize_schema()` returns a
  `tuned` key (seen live).

### 86. L2032 `### ~~A failed INSERT was the bulk paths' upsert, at ~40 ms each~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`, `RegisterDefaultNode`). Not rerun.

### 87. L2075 `### ~~BulkIngestNodesSQL wrote labels and properties with no graph_id~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`cc546a29`). Not rerun.

### 88. L2087 `## Text search and fusion (verified 2026-09-20)`

- **Verdict:** fixed (group).

### 89. L2089 `### ~~Every iFind index was declared with syntax IRIS rejects~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. `schema.py:435,519` declare `... AS %iFind.Index.Basic`.

### 90. L2103 `### ~~Graph_KG.kg_TXT could not be created, and it took kg_RRF_FUSE with it~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. `schema.py:2067` uses `%ID %FIND search_index(idx_docs_text_ifind, :q)`; both routines
  pass `test_shipped_routines_exist.py` live.

### 91. L2129 `### ~~The Python text search had never used iFind either~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. Not rerun.

### 92. L2140 `### ~~kg_RRF_FUSE's text leg is namespace-wide, and its two legs use different key spaces~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (spec 230 FR-007). Not rerun.

### 93. L2180 `## Cypher expression semantics (verified 2026-09-22)`

- **Verdict:** open (group: two documented deviations).

### 94. L2182 `### Division by zero answers null, where openCypher raises`

- **Verdict:** open (deliberate deviation).
- **Evidence:** `MATCH (n:DZ) RETURN 1/0 AS a, 1 % 0 AS b LIMIT 1` gives `[[nan, nan]]`, `error=None`.
  `tests/unit/test_230_division_by_zero_semantics.py` passed; `tests/e2e/test_stress_api.py::TestCypherRESTEndpoint::test_division_by_zero_is_a_null_not_an_error` passed.
- **Audience:** application developer.
- **One-line:** `x / 0` and `x % 0` answer NaN (JSON `null`) instead of raising (all 4.x, by design); test the divisor
  yourself when the difference matters.

### 95. L2208 `### RETURN column order is not preserved across CALL { ... }`

- **Verdict:** open (deliberate deviation).
- **Evidence:** `MATCH (a:CO) CALL { WITH a MATCH (a)-[:R]->(q) RETURN count(q) AS deg } RETURN a.node_id AS id, deg`
  gives columns `['deg', 'id']`; `tests/unit/test_230_call_subquery_column_order.py` passed.
- **Audience:** application developer.
- **One-line:** a correlated `CALL { ... }` column comes first in the result whatever `RETURN` says (all 4.x); read rows
  by `result.columns`, not by position.

### 96. L2238 `## TCK error matching (verified 2026-09-26)`

- **Verdict:** not a defect (describes the TCK harness; internal).

### 97. L2247 `### What the harness cannot tell apart`

- **Verdict:** open (internal: harness limits; 262 touches the `SQLUSER.JSON_VALUE` naming it cites).
- **Evidence:** `tests/tck/steps/errors.py` exists; `CypherFn_IVGTYPEERROR` still the runtime tag (`translator.py:15645`).
  The "no `ArithmeticError`" bullet is entry 94.

### 98. L2271 `### Engine gaps the strict step exposed (fixed)`

- **Verdict:** fixed (203 branch, `937d4b7` capture 3896/3896). TCK not rerun.

### 99. L2284 `## GraphQL API (verified 2026-09-20)`

- **Verdict:** open (group: entry 102 open, internal; the rest fixed or documented).

### 100. L2293 `### ~~The contract's graphStats ships as stats~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0.
- **Evidence:** `IVG_RUN_CONTRACT=1 python -m pytest tests/contract/test_graphql_schema.py`: 15 passed. Note: without
  `IVG_RUN_CONTRACT=1` all 15 skip ("TDD stub"), so no default run checks the contract (internal).

### 101. L2307 `### ~~There is no Subscription root type~~ (documented in 4.0.0)`

- **Verdict:** not a defect (unimplemented and documented); `test_there_is_no_subscription_root` in the same 15-pass run.

### 102. L2326 `### api/gql/types.py and api/gql/resolvers/ are an unreachable duplicate`

- **Verdict:** open (internal).
- **Evidence:** `api/gql/types.py` and `api/gql/resolvers/{query,mutation}.py` exist; `api/gql/schema.py:15-16,78,85`
  imports only `.core.*` and `examples.domains.biomedical.*`; importers of the copy are each other plus
  `tests/unit/test_gql_protein_mutations.py` and `tests/unit/test_biomed_create_protein_embedding.py`.

### 103. L2355 `### ~~/graphql answered empty against a populated namespace~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0 (`e44cbeb2`, `build_graphql_context`).
- **Evidence:** `tests/e2e/test_230_graph_scoped_node_reads_e2e.py` passed in `IVG460KI`.

### 104. L2383 `### ~~nodes and get_node read every graph that shared a node ID~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. Same test file as entry 103 passed.

### 105. L2415 `## Packaging (verified 2026-09-19)`

- **Verdict:** fixed (group).

### 106. L2417 `### pip install iris-vector-graph alone could not import the package`

- **Verdict:** fixed in 3.2.0. `pyproject.toml:61` has `requests>=2.28.0` in core dependencies;
  `tests/unit/test_core_dependencies_cover_eager_imports.py` passed.

### 107. L2450 `## Deployment and namespaces`

- **Verdict:** not a defect (how to tell a deployed namespace from a DDL shell).
- **Evidence:** `SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'Graph.KG.Eraser'` is 1 in `IVG460KI`.
  262 is changing what is installed where; R032 owns the operator wording.

### 108. L2473 `### ~~deploy/ ships a v1.37.0 snapshot of Graph.KG~~ (fixed in 4.0.0)`

- **Verdict:** fixed in 4.0.0. `deploy/` holds only `bolt-on/`, `docker/`, `iris-build/`, `README.md`.

### 109. L2487 `### Upgrading a real 3.2.0 install: six orderings, all fixed in 4.0.0`

- **Verdict:** fixed in 4.0.0 (`cc546a29`: `RdfEdgesRescueError`, entry-point verification, iFind owner recompiles).
  Not rerun (needs a `pip install iris-vector-graph==3.2.0` namespace).

### 110. L2563 `### An upgraded install's embedding tables are owned by differently-named classes`

- **Verdict:** not a defect (inert by construction; pinned by
  `tests/integration/test_227_fresh_vs_migrated.py::test_every_compared_table_resolves_a_class_on_both_installs`, not
  rerun).

### 111. L2592 `## FHIR graph and PageRank (verified 2026-09-23)`

- **Verdict:** open (group: entries 112, 114, 115 open; 113 fixed).

### 112. L2594 `### delete_node reaches every graph that holds the node ID`

- **Verdict:** open (by design: the default reach is unchanged; `graph=` since 4.1.1).
- **Evidence:** `Patient/kp1` in `''` and `kig2`: `delete_node("Patient/kp1")` returns `True` and leaves no row in either;
  recreated, `delete_node("Patient/kp1", graph=None)` leaves `[('kig2',)]`. Default is `_EVERY_GRAPH`
  (`_engine/nodes_edges.py:1722`).
- **Audience:** application developer.
- **One-line:** `delete_node(id)` and `bulk_delete_nodes(ids)` without `graph=` delete the id from every graph that holds
  it, FHIR graphs included (all 4.x); pass `graph=None` for the default graph or `graph='<g>'`.

### 113. L2608 `### MCPTools.PPRWalk passes topK as bidir and reads an array shape RunJson never returns`

- **Verdict:** fixed in 4.2.0 (`cc36b47d`, "PPRWalk misread PageRank rows"); residue: it still passes graph `""`.
- **Evidence:** `MCPTools.cls:179` calls `RunJson(seedJson, damping, 50, 0, 1.0, "", "", topK)` and reads
  `item.%Get("id")`; covered by `tests/integration/test_mcp_tools.py` (not rerun). No graph parameter, so the MCP tool
  walks the default graph only (minor; application developer if R027 keeps it).

### 114. L2617 `### ^NKG has no graph dimension`

- **Verdict:** open (design).
- **Evidence:** `Graph/KG/Adjacency.cls:72-78` writes `^NKG(-1, sIdx, -(pIdx+1), oIdx)` with no graph subscript.
- **Audience:** application developer.
- **One-line:** Arno-accelerated BFS and PPR read `^NKG`, which has no graph dimension, so a named graph's (a FHIR
  graph's) walks always run in ObjectScript or Python, correct but slower on large graphs (all 4.x); expect the
  default graph to be the fast one.

### 115. L2628 `### Snapshots carry no fhir_graphs and no code_crosswalk (verified 2026-09-28)`

- **Verdict:** open.
- **Evidence:** `_engine/snapshot.py` `STORE_PLAN` (lines 70-100) lists `fhir_unresolved`, `fhir_definitions`,
  `fhir_canonical_refs`; `grep -n "fhir_graphs\|code_crosswalk" iris_vector_graph/_engine/snapshot.py` finds nothing.
  Not restored live (needs a FHIR repository).
- **Audience:** IRIS operator.
- **One-line:** a snapshot restore (4.1.0 on) brings back a FHIR graph's nodes and edges but not its `fhir_graphs`
  registration or `code_crosswalk` rows; re-run `fhir_graph_register()`, `fhir_graph_rebuild(graph)` and re-add the
  crosswalk; keep the crosswalk source under version control.

### 116. L2642 `## Loose ends`

- **Verdict:** open (internal), partly fixed.
- **Evidence:** `tests/e2e/test_betweenness_neighborhood_e2e.py:20-21` still recompiles `Graph.KG.Traversal` and
  `Graph.KG.EdgeScan` with `cuk-d` from a fixture. The flat `^KG("deg", ...)` write is gone (the fixture writes
  `^KG("out"/"in", 0, ...)` directly, `:30-33`). The spec-221 docs bullet is done.

### 117. L2661 `## Investigated, not a defect`

- **Verdict:** not a defect, confirmed; one stale sentence: `Graph/KG/Ledger.cls:211-217` now describes the purge hook,
  so "The class comment ... still says the table has no purge hook" is no longer true.

### 118. L2686 `## Resolved history`

- **Verdict:** not a defect (group: entry 119).

### 119. L2688 `### IVF index tests — isolation marker`

- **Verdict:** not a defect (test-architecture note). `tests/unit/test_ivf_index.py:48,224` carry
  `@pytest.mark.requires_clean_isolation`. Internal.

## New findings (not in KNOWN_ISSUES)

### N1. An undirected variable-length pattern from a `node_id`-bound start returns the start node

- **Verdict:** open. Wrong answer, no error. Found while checking entry 81.
- **Evidence:** chain `uz0-NEXT->uz1-NEXT->uz2-NEXT->uz3` (three edges, no cycle), `IVG460KI`:

  | Query (start `{node_id: 'uz0'}`, pattern `-[:NEXT*..]-(b)`) | Answer                         | Right answer     |
  | ----------------------------------------------------------- | ------------------------------ | ---------------- |
  | `*1..3` `RETURN DISTINCT b.node_id`                         | `uz1, uz0, uz2, uz3`           | `uz1, uz2, uz3`  |
  | `*2..2` `RETURN b.node_id`                                  | `uz0, uz2`                     | `uz2`            |
  | `*1..3` `RETURN count(DISTINCT b) AS c`                     | `4`                            | `3`              |
  | `*1..3` `RETURN DISTINCT b.node_id AS i` (SQL route)        | `uz1, uz2, uz3`                | same             |
  | `(a:UZ {id: 'uz0'})` ... `count(DISTINCT b)`                | `3`                            | same             |

  The BFS routes (unaliased property or `count(DISTINCT ...)`, entry 80's `_vlp_bfs_answers`) answer from an edge BFS
  in direction `both` that walks the arriving edge back, so the start comes back at hop 2. openCypher forbids reusing a
  relationship in one path. The aliased form expands in SQL and is right.
- **Audience:** application developer.
- **One-line:** `MATCH (a {node_id: $x})-[*m..n]-(b)` (undirected) with an unaliased `RETURN b.node_id` or
  `count(DISTINCT b)` includes the start node, reached back over the same relationship (seen on 4.6.0; older releases
  not checked); alias the column (`RETURN DISTINCT b.node_id AS id`) or bind the start as `{id: $x}`; compare with
  `count(DISTINCT b)` from the aliased form.

### N2. Test-suite defects met while triaging (internal)

- `tests/integration/test_list_error_decode_live.py:39,45` opens its second connection in `USER` whatever
  `IVG_NAMESPACE` says; outside USER it errors, inside USER it rebuilds indices on a USER table.
- `tests/integration/test_nodes_edges_deep.py:188` asserts a log message the code no longer emits (entry 13).
- `tests/contract/test_graphql_schema.py` skips all 15 unless `IVG_RUN_CONTRACT=1` (entry 100).

## Summary

| Verdict        | Sections | Group headings | Entries |
| -------------- | -------- | -------------- | ------- |
| fixed          | 67       | 5              | 62      |
| open           | 33       | 10             | 23      |
| not a defect   | 10       | 1              | 9       |
| cannot confirm | 9        | 2              | 7       |
| **total**      | **119**  | **18**         | **101** |

A group heading (a `##` with entries under it: 1, 4, 6, 11, 39, 57, 60, 63, 65, 71, 76, 84, 88, 93, 99, 105, 111, 118)
is open when any entry under it is open. Entry 74 counts as fixed; its residue is listed below as open. Plus two new
findings: N1 (open, application developer) and N2 (internal).

Open entries by audience:

**Application developer (11 entries, plus 74's residue and N1):** 2 embedded VECTOR SELECT answers empty; 8 temporal
purge keeps endpoint nodes; 10 undirected-hop first call compiles long; 27 RDF export pins `rdflib<8`; 43 unscoped
`count(DISTINCT)` depends on the path; 74 (residue) CREATE after a `WITH` stage raises -119; 75 duplicate CREATE is a
silent no-op outside multigraph; 80 unaliased id answers `(id, hops, pred)` rows; 94 division by zero answers null; 95
a `CALL {}` column comes first; 112 `delete_node` without `graph=` reaches every graph; 114 `^NKG` has no graph
dimension; N1 undirected variable-length from a `node_id` start returns the start. If R027 keeps cannot-confirm or
residue items: 64 `<LIST ERROR>` on driver 5.3.x; 113 `PPRWalk` takes no graph.

**IRIS operator (3):** 9 temporal purge cost over target; 69 a second writer re-declares the vector width; 115
snapshots miss `fhir_graphs` and `code_crosswalk`. If kept: 5 Arno before 1.2.1 keeps dead dictionary entries.

**Internal (9, history file, not published):** 20, 21, 26, 29, 30, 53, 97, 102, 116; plus the residues and gaps in 13,
35, 36, 100 and N2.

Skipped: the Arno reader gate and the BFS bench (7, 34, 35), the 3.2.0 upgrade rehearsal (3, 109), SNB-scale compile
timing (10), a FHIR snapshot restore (115), the Arno E2E files that drive USER (32), the TCK (98), driver 5.3.x (64).
