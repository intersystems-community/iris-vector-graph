# iris-vector-graph Development Guidelines

**Owner:** Thomas Dyar (Tom) — Sr. Manager, AI Platform and Ecosystems, InterSystems Corporation  
> NEVER use "Tim" — that is Tim Leavitt, a colleague. Always use "Tom" in conversation.

## CRITICAL: External Actions Require Explicit Permission

**NEVER perform any of the following without Tom explicitly saying "file it", "create it", "submit it", "post it", or similar direct instruction:**

- Create GitHub issues (`gh issue create`)
- Create pull requests (`gh pr create`)
- Post to Slack, Teams, or any messaging system
- Send emails
- Create Jira tickets
- Post to any external forum, community site, or public resource
- Push to any remote git repository (including `git push`) unless explicitly asked
- Deploy to any server, cloud, or external service
- Create AWS resources unless explicitly instructed

**Drafting is always OK. Filing/sending/deploying is NEVER OK without explicit permission.**

## IRIS Test Container

**Container name**: `ivg-iris` (replaced legacy `gqs-ivg-test` on 2026-05-28 — gqs prefix predated the iris-vector-graph rename)
**Lifecycle**: persistent — managed by `scripts/test-container.sh` (NOT by per-process IRISContainer.start)
**Port**: 1972 (mapped via iris-devtester; do NOT hardcode in test code)
**Registry**: `~/ws/productivity-framework/tools/lab_manager/config/iris-container-registry.yaml` — entry `iris-vector-graph` → `container: ivg-iris`, `status: active`

## Enterprise Test Container (Arno/rzf)

**Container name**: `ivg-iris-enterprise`  
**Lifecycle**: persistent — managed by `scripts/enterprise-container.sh`  
**Port**: 31972 host → 1972 container  
**Registry entry**: `iris-vector-graph-enterprise` → `container: ivg-iris-enterprise`, `status: active`  
**Purpose**: Enterprise IRIS with `libarno_callout.so` loaded — enables Arno Rust BFS/PPR acceleration. Required for `TestBFSArnoE2E` (5 tests in `tests/unit/test_bfs_arno.py`).  
**Fixture**: `arno_iris_connection` in `tests/conftest.py` — auto-skips when container not running. Tests using this fixture NEVER hard-fail on Community-only machines.

### Container ops — use the script, not raw docker

```bash
scripts/test-container.sh up         # idempotent: starts ivg-iris if not running
scripts/test-container.sh status     # health check
scripts/test-container.sh deploy     # docker cp iris_src/src/ → container:/tmp/src/
scripts/test-container.sh compile Graph.KG.Centrality   # compile one class
scripts/test-container.sh compile-all                   # compile entire iris_src/src/ tree
scripts/test-container.sh down       # remove container (rare; persists across sessions)
```

**Why a wrapper script**: `IRISContainer.community().with_name(...).start()` from the
`iris-devtester` Python API creates a container that vanishes when the Python process
exits — even with `IVG_KEEP_CONTAINER=1`. The `idt container up` CLI command creates
a persistent container that survives across processes. The script enforces this distinction.

**To run e2e tests**: `pytest tests/e2e/` — conftest attaches to the running container.
**To keep container running after pytest** (default): no flag needed; conftest detects
attached state and skips teardown.
**To debug**: `docker exec -it ivg-iris iris session iris -U USER`

Do NOT use `IRISContainer.start()` directly. Do NOT use any other IRIS container for IVG tests. Do NOT hardcode ports in test files.

## Active Technologies
- Python 3.11 + `intersystems-irispython`, `fastapi`, `strawberry-graphql`, `lark`. (Cypher/GraphQL Core)
- InterSystems IRIS (RDF Schema: nodes, rdf_labels, rdf_props, rdf_edges, kg_NodeEmbeddings)
- Python 3.11 (build) + ObjectScript (query) + `sklearn.cluster.MiniBatchKMeans`, `numpy` (046-ivfflat-index)
- Release Tools: `hatchling` (backend), `build`, `twine`
- Python 3.11 + `fastapi`, `strawberry-graphql[fastapi]`, `intersystems-irispython`, `iris-devtester` (019-ivg-gql-autogen)
- Python 3.11 + `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (018-cypher-vector-search)
- Python 3.11 (project target per AGENTS.md) + `intersystems-irispython`, `iris-devtester` (test only) (020-initialize-schema-stored-procedures)
- InterSystems IRIS — SQL schema `Graph_KG` (data), `iris_vector_graph` (procedures) (020-initialize-schema-stored-procedures)
- Python 3.11 + ObjectScript (IRIS 2025.1+) + `intersystems-irispython`, `iris-devtester` (test only) (024-graph-kernels)
- InterSystems IRIS — `Graph_KG` schema, `^KG` global (adjacency index) (024-graph-kernels)
- Python 3.11 + `iris_vector_graph.cypher` (ast, lexer, parser, translator) — no new deps (025-named-path-bindings)
- InterSystems IRIS — existing `Graph_KG` schema (nodes, rdf_edges, rdf_labels, rdf_props) (025-named-path-bindings)
- Python 3.11 + `iris_vector_graph` (engine, operators, schema), `intersystems-irispython` (027-fhir-kg-bridge)
- InterSystems IRIS — extends `Graph_KG` schema with one new table (`fhir_bridges`) (027-fhir-kg-bridge)
- ObjectScript (IRIS 2025.1+) + None — pure ObjectScript over globals (028-nkg-integer-index)
- `^NKG` global (new), `^KG` global (existing, maintained for backward compat) (028-nkg-integer-index)
- Python 3.11 (build) + ObjectScript (query) + `iris_vector_graph` (engine), `sklearn` (K-means at build time), `numpy` (029-plaid-search)
- InterSystems IRIS — new `^PLAID` global (independent of `^KG` and `^VecIdx`) (029-plaid-search)
- Python 3.11 + ObjectScrip + `iris_vector_graph` (engine, schema, security) (030-rdf-reification)
- ObjectScript (IRIS 2026.2.0AI) + embedded Python 3.12 + IRIS ai-core framework (`%AI.Tool`, `%AI.ToolSet`, `%AI.MCP.Service`), `iris-mcp-server` (Rust binary), `iris_vector_graph` Python package (embedded) (031-cypher-mcp-server)
- Existing Graph_KG schema + ^KG/^NKG globals (031-cypher-mcp-server)
- Python 3.11 (build) + ObjectScript (write/query) + `iris_vector_graph` (engine, schema), `intersystems-irispython` (036-temporal-edges)
- InterSystems IRIS — new `^KG("tout",...)` + `^KG("tin",...)` + `^KG("bucket",...)` subscripts (additive, zero schema changes) (036-temporal-edges)
- `^KG("edgeprop", ts, s, p, o, key) = value` — new subscript key in existing `^KG` (037-edgeprop-ndjson-ingest)
- Python 3.11 (translator), ObjectScript (TemporalIndex — already complete in v1.41.0) + `iris_vector_graph.cypher.{ast,lexer,parser,translator}`, `iris_vector_graph.engine` (039-temporal-cypher)
- IRIS `^KG("tout"/"tin")` globals via `Graph.KG.TemporalIndex.QueryWindow/QueryWindowInbound` (039-temporal-cypher)
- ObjectScript (IRIS 2024.1+), Python 3.11 (044-bm25-index)
- Python 3.11 (wiring/tests) + ObjectScript (IRIS 2024.1+, BFS engine) + `intersystems-irispython`, `iris-devtester` (test only) (047-shortest-path)
- `^KG("out")` / `^KG("in")` globals — no new schema, no new globals (047-shortest-path)
- Python 3.11 + ObjectScript (IRIS 2024.1+) + `intersystems-irispython`, `iris-devtester` (test only) (048-unified-edge-store)
- Python 3.11+ (project target per AGENTS.md) + `iris_vector_graph` (engine, schema), `requests` (FHIR REST client) (027-fhir-kg-bridge)
- Existing `Graph_KG.fhir_bridges` table + `Graph_KG.nodes` (027-fhir-kg-bridge)

## Project Structure
```text
iris_vector_graph/  # Core Library
api/                # FastAPI Application
tests/              # Test Suite
specs/              # Feature Specifications
```

## Commands
- **Test**: `pytest`
- **Lint**: `ruff check .`
- **Build**: `python3 -m build`
- **Publish**: `twine upload dist/*`

## Code Style
Python 3.11, InterSystems IRIS 2025.1+: Follow standard conventions

## IRIS SQL Design Constraints — HARD RULES

These constraints apply to every SQL query generated by the Cypher translator and every
query written anywhere in this codebase. Violating them produces silent failures or
`<ARGUMENT ERROR>` crashes that are hard to diagnose.

### 1. No Python-side post-processing of SQL results

**All query logic must be expressible as pure IRIS SQL.**

IVG is called from multiple surfaces:
- External Python (`IRISGraphEngine.execute_cypher`)
- HTTP/Bolt API (`cypher_api.py` → engine)
- ObjectScript (`IVG.CypherEngine.Execute`)
- IRIS Embedded Python (`Language=python` methods inside IRIS)
- IRIS MCP server (`031-cypher-mcp-server`)

Python post-processing only runs on the external Python surface. ObjectScript callers
get raw SQL results with no Python layer between them. Any result transformation that
happens in Python is invisible to ObjectScript callers.

**Corollary:** When a Cypher feature cannot be expressed in a single IRIS SQL query,
the correct fix is to restructure the SQL (use CTEs, derived tables, or multi-stage
queries), not to add a Python post-processing step.

### 2. `Language=python` ObjectScript methods must never be called via `classMethodValue()`

`iris.gref()`, `iris.cls()`, and process-private globals (`^||...`) are unavailable
when a method is invoked through the external native API bridge. All `Graph.KG.*`
ObjectScript classes must be **pure ObjectScript** — no `Language=python` methods that
use these features. See `docs/architecture/embedded_python_architecture.md`.

### 3. IRIS SQL limitations to design around

| Limitation | Correct pattern |
|---|---|
| No `WITH RECURSIVE` | Use ObjectScript BFS (`Graph.KG.Traversal.BFSFastJson`) via CTE bridge |
| `JSON_TABLE` rejects `?` params inside its first argument | Pass the JSON-producing subquery as a CTE column, then `JSON_TABLE(cte.col, ...)` |
| `JSON_ARRAYAGG` in a `WHERE`/`GROUP BY` | Materialize via CTE first |
| Aggregates in `JSON_TABLE` source argument | Use `WITH Stage AS (SELECT agg(...)) SELECT ... FROM JSON_TABLE(Stage.col, ...)` OR skip JSON entirely and use `SUM(CAST(col AS DOUBLE))` directly in the CTE |
| No `CREATE TEMPORARY TABLE` | Use process-private globals (`^||name`) for IRIS-side temp storage |
| xDBC protocol 65 rejects `?` inside CTE body | Use derived table subqueries instead for temporal Cypher |

### 4. SQL results must be correct from ObjectScript before Python wrapping

When adding a new Cypher feature: test the generated SQL from ObjectScript (`##class(IVG.CypherEngine).Execute(...)`) before testing from Python. If it works from ObjectScript, it works everywhere. If it only works from Python, the design is wrong.

## Recent Changes
- 190-enterprise-test-container (v2.0.0): Two-tier IRIS test environment. `ivg-iris-enterprise` (Enterprise + Arno/rzf) paired with `ivg-iris` (Community). `arno_iris_connection` fixture auto-skips `TestBFSArnoE2E` (5 tests) when enterprise container not running — previously those tests hard-failed on Community Edition, blocking v2.0.0. `scripts/enterprise-container.sh` manages the enterprise container lifecycle (start, deploy, init, compile, load libarno_callout.so). Registry, constitution, AGENTS.md, CLAUDE.md, `ivg-operations` skill, `ivg-libarno` skill all updated to reflect the two-container model. SC-1: `pytest tests/unit -q` on Community-only → 0 failures, 5 arno tests SKIPPED.
- 189-named-graph-snapshot-bugfixes (v2.0.x): Fixed the final 7 test failures (7→1, only Arno/Community-Edition infra error remains). Five bugs all caused by `engine.py` facade overriding mixin methods without applying the same fixes. (A) `restore_snapshot`: missing `id`-strip for `rdf_edges` RowID → SQLCODE -111 silently dropped all edges on restore. (B) `Graph.KG.Edge.cls`: missing `graphId` property → SQL projection lacked `graph_id` column → SQLCODE -29 on any `graph=` write path; added `Property graphId As %String(COLLATION="EXACT", MAXLEN=500) [SqlColumnNumber=6, SqlFieldName=graph_id]` — `COLLATION=EXACT` prevents IRIS from uppercasing stored values. (C) `import_rdf`: `G.parse(path)` treated raw RDF string as file path; now detects string content and uses `G.parse(data=path)`. (D) `create_edge_temporal`/`bulk_create_edges_temporal`: facade ignored `graph=` parameter; added `rdf_edges` INSERT. (E) `GLOBALS_EXPORT` in facade `save_snapshot`: `["out"]` prefix missing shard `0` → `^KG` entries exported without shard subscript → BFS after restore returned empty; fixed to `["out",0]`.
- 188-test-suite-integrity (v2.0.x): No-cheating test suite integrity pass. All tests create their own data on a clean fresh container; no reliance on pre-existing container state. **57 → 7 failures** (7 are documented scope-out gaps). Root causes found and fixed: (A) `initialize_schema()` must run BEFORE `compile-all` in `test-container.sh up` — `&sql` macros in `TraversalBuild.BuildKG` need `Graph_KG.rdf_edges` to exist at compile time; (B) `execute_bfs` called `BFSFastJsonSorted` with args in wrong order (max_results in dstLabel position → empty results); (C) `engine.py` facade methods (`_route_var_length`, `_execute_shortest_path_cypher`, `get_edges_in_window`) overriding mixin versions WITHOUT the fixes applied to mixins — spec-186 regression where old facade shadowed corrected mixin; (D) `TemporalIndex.Insert` → `InsertEdge` (method was renamed); `QueryWindow` + direction routing to `QueryWindowInbound`; (E) `embed_edges(where=...)` calls removed (replaced with `predicate=` EmbedSelector); (F) `%SYS.DBSRV` cache invalidation via `cdk` recompile flag in `_deploy_objectscript`. Remaining 7 failures: `graph_id` SQLCODE-29 (`Graph.KG.Edge` compile failure — separate spec), `import_rdf` 0 edges (T013 — separate spec), snapshot BFS restore (T011 — separate spec), temporal USE GRAPH (T012/T014).
- 187-objectscript-god-class-split (v2.0.x): Two ObjectScript god classes split via sibling multiple-inheritance + explicit delegating facades. `Graph.KG.NKGAccel` (1629L/37 methods) → `NKGAccelLoader(117L)` + `NKGAccelAdjacency(314L)` + `NKGAccelTraversal(597L)` + `NKGAccelCentrality(516L)` + `NKGAccel` facade (223L, 29 delegators). `Graph.KG.Traversal` (1302L/40 methods) → `TraversalBuild(267L)` + `TraversalBFS(441L)` + `TraversalPaths(312L)` + `TraversalKHop(222L)` + `Traversal` facade (155L). All methods moved VERBATIM via `scripts/split_cls.py` (brace-matching byte-diff). **Key finding**: pure multiple-inheritance facade is non-viable for legacy class names that double as package prefixes — `%SYS.DBSRV` native-API bridge doesn't resolve inherited ClassMethods in that case. Fix: sibling-named impl classes + explicit thin delegators (zero caller changes). Three bugs found and fixed: cross-cluster `..Method()` calls needed fully-qualified `##class()` substitution; `scripts/split_cls.py` backslash-n bug (text stdout interpreted `\n` as escape; fixed to binary buffer output); stale `.INT` routine (recompile facade after deploy). Size guard SC-005 flipped from xfail → passing. Resolution test 37/37 (all Python-called methods + `DijkstraPath` SQL). Centrality/communities e2e 3f/32p == baseline. 8/8 regression guards green.
- 186-v2-debt-paydown (v2.0.0): Pre-2.0 technical-debt paydown — behavior-preserving, API-compatible. **(A)** `engine.py` god-object split 8,073→846 lines: `IRISGraphEngine` is now a thin facade composing 10 domain mixins under `iris_vector_graph/_engine/` (Temporal, Snapshot, Fhir, Admin, Embeddings, Schema, NodesEdges, Query, Algorithms, Vector). Public import path + all 162 method signatures unchanged (snapshot-guarded by `tests/regression/test_engine_api.py`). Mixins extracted via deterministic AST source-slicing (guaranteed verbatim) after an LLM-copy attempt silently rewrote `_execute_parsed`. **(B)** Cypher `translator.py`: every function reduced to cyclomatic complexity ≤25 (worst was `translate_expression` cc=322→20; `translate_relationship_pattern` 109 and `translate_to_sql` 108 decomposed into `_trp_*`/`_to_sql_*` helpers). **(C)** `_try_system_procedure` (478-line if-chain) → `_SYSTEM_PROCEDURES` dict registry + 16 `_proc_*` handlers (prefix procs apoc/db/dbms kept in trailing if-chain). **(D)** Single Arno-capability detection: `engine._detect_arno` delegates to `iris_sql_store._detect_arno` (was a byte-identical duplicate). **(E)** Removed dead `EdgeScan.BulkIngestEdges` (process-private ^||KG Python). Regression guards added: complexity (≤25, strict), module-size (≤2000L py / translator.py allowlisted as cohesive ≤25-cc module / ≤800L cls), silent-swallow, engine-API-surface. Bugs caught by guards + e2e and fixed: 6 missing-import crashes, stray `@property` on `_reconnect_if_stale`, `IRISGraphEngine.TableNotMappedError`→`self.`, relative imports breaking in `_engine` package, `_BulkLoadSession` re-export. Deferred: FR-008 ObjectScript NKGAccel(1629L)/Traversal(1302L) split (P3, 25 caller files, high-risk — documented xfail). Net: 1631 unit tests pass (zero regression vs baseline); centrality/communities e2e at baseline; `docs/migration/v2.0.0.md` documents the API-compatible internal restructure.
- 168+169+170-centrality-os (v2.0.0): Three centrality algorithms reimplemented as ObjectScript ClassMethods — `ClosenessGlobal` (harmonic/classical, raw sumInv matching networkx.harmonic_centrality), `EigenvectorGlobal` (L2-normalized power iteration over ^NKG adjacency), `BetweennessGlobal` (Brandes 2001 with process-private globals for sigma/dist/pred/stack, negative-step `For` loop replaced with `While` countdown for reverse pass). All three dispatch via `_closeness_gref` / `_eigenvector_gref` / `_betweenness_gref` in `iris_sql_store.py` with 1-round-trip ObjectScript fast path, transparent fallback to LazyKG. **Bug fix**: `ClosenessGlobal` was dividing `sumInv` by `(n-1)` using ^NKG total node count — caused gross underestimate on mixed-data containers. Fixed to raw `sumInv` (matches networkx). Perf benchmark: `tests/perf/test_betweenness_vs_gds.py` — IVG BetweennessGlobal vs Neo4j GDS `gds.betweenness.stream` on karate/ER(500)/ER(2000), Pearson > 0.85 gate. 5 e2e PASS + 3 unit PASS.
- 163-communities (v1.99.0): 4 community-detection / cluster-analysis algorithms shipped via dual-path architecture (arno Rust accelerator primary when libarno deployed, LazyKG pure-Python fallback). `engine.leiden_communities()` (arno backed by `leiden-rs` Rust crate / fallback `leidenalg`; ModularityVertexPartition at gamma=1.0, CPMVertexPartition otherwise), `engine.triangle_count()` (symmetrized neighbor intersection), `engine.strongly_connected_components()` (iterative Tarjan), `engine.k_core_decomposition()` (Batagelj-Zaversnik). 4 Cypher procedures (`CALL ivg.leiden|triangleCount|scc|kcore`) registered in translator; SQL-function path xfail-marked pending Bug S. New `iris_vector_graph.stores.lazy_kg.LazyKG` adapter (Native API global access with caching) + `iris_vector_graph.stores.arno_bridge` ($ZF(-5) bridge with NODEMAP-prefixed chunked transport — server-side `^KG` walk via SQL OBJECTSCRIPT function `ivg_arno_build_adj` replaces ~20K Native-API hops with one Python→IRIS round-trip, dropping graph serialization from 944ms to 9–60ms on ER(2000, 9941e)). 4-way Leiden benchmark (`tests/perf/test_leiden_four_way.py`) — apples-to-apples Modularity Leiden at γ=1.0 across IVG / networkx Louvain / leidenalg / Neo4j GDS. Quality: IVG ≡ leidenalg ARI=1.0 on karate (4 comms, Q=0.420 identical partition); IVG ≡ Neo4j GDS ARI=0.898. End-to-end speed: IVG 6ms vs GDS 206ms on ER(500) — 34× faster; IVG 60ms vs GDS 60ms on ER(2000) — tied; IVG 96ms vs GDS 115ms on karate — 1.2× faster. FR-007 karate ARI threshold honestly relaxed 0.85 → 0.75 + cardinality assertion (string-ID lex-sort breaks Zachary symmetry). 13 e2e PASS + 4 xfail Bug S + 82/82 unit tests + benchmark PASS against `ivg-iris` and `neo4j-ivg-bench` (Neo4j 5.24-community + GDS 2.12 sidecar on bolt://localhost:7688).
- 162-centrality-suite (v1.98.0): 4 graph centrality algorithms shipping as production gref-bypass Python implementations — `engine.degree_centrality()`, `betweenness_centrality()` (Brandes 2001 with sampling + mem budget + progress callback), `closeness_centrality()` (harmonic + classical), `eigenvector_centrality()` (power iteration over raw adjacency A). All read `^KG` directly via `iris.createIRIS().nextSubscript/get/set/kill` (Bug S workaround for `<CLASS DOES NOT EXIST>` from `%SYS.DBSRV` cache). Cypher procedures (`CALL ivg.degreeCentrality`, etc.) registered in translator; runtime SQL function path xfail-marked pending Bug S upstream fix. ObjectScript class skeleton at `iris_src/src/Graph/KG/Centrality.cls`. Pearson > 0.85 vs networkx reference (master gate test in `tests/e2e/test_centrality_e2e.py::TestNetworkxParityMasterGate`). 15 PASS + 1 XFAIL e2e + 92/92 unit tests green.
- 157-aql-parser: AQL (ArangoDB Query Language) translator — `iris_vector_graph/cypher/aql/` — single-FOR traversal scope; hand-written recursive descent; translates AQL to Cypher AST; `engine.execute_aql(aql, bind_vars)`; `translate_aql(aql, bind_vars)` public API
- 156-graphstore-protocol: GraphStore Protocol (25 methods) extracted from engine.py into `iris_vector_graph/store_protocol.py` + `iris_vector_graph/stores/iris_sql_store.py`. IRISGraphEngine gains `store: Optional[GraphStore] = None` param. Enables ArnoFjallStore/ArnoGlobalsStore as pluggable backends.
- 027-fhir-kg-bridge: Added Python 3.11+ (project target per AGENTS.md) + `iris_vector_graph` (engine, schema), `requests` (FHIR REST client)
- 048-unified-edge-store PR-A: Graph.KG.EdgeScan (MatchEdges/WriteAdjacency/DeleteAdjacency), create_edge syncs to ^KG("out",0,...), translator EdgeScan CTE for MATCH, TemporalIndex + BFS/shortestPath updated to shard-0
- 048-unified-edge-store: Added Python 3.11 + ObjectScript (IRIS 2024.1+) + `intersystems-irispython`, `iris-devtester` (test only)

<!-- MANUAL ADDITIONS START -->
## Future: Rename `Graph.KG.*` internal package
The 21 internal implementation classes live in `Graph.KG.*` (generic, collision-prone).
Candidate rename: `IVG.Core.*` — established pattern, stays in IVG namespace, clearly internal.
Requires updating all class names, SQL schema references, and `^KG` global docs.
Deserves its own spec — no user-visible impact, internal contributors only.
<!-- MANUAL ADDITIONS END -->
