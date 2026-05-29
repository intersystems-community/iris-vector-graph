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

## Active Technologies — spec 166/167

- Python 3.11+ shared LazyKG instance on IRISGraphEngine (spec 166)
- SQL OBJECTSCRIPT ivg_graph_json_build/chunk for ^KG→JSON server-side (spec 167)

## Recent Changes

- 166-shared-lazy-kg (v1.99.3): IRISGraphEngine._shared_lkg + _get_shared_lkg() + _invalidate_shared_lkg(). All 8 LazyKG-backed algorithm methods share one LazyKG instance per engine lifecycle, invalidated on rebuild_kg()/bulk_ingest_edges(). Eliminates O(V) redundant iter_nodes() round-trips when calling multiple algorithms in sequence.
- 167-graph-json-os (v1.99.3): ivg_graph_json_build SQL OBJECTSCRIPT function collapses O(V+E) Python→IRIS nextSubscript calls in LazyKG community fallbacks (_leiden_lazykg, _triangle_count_lazykg, _scc_lazykg, _k_core_lazykg) to 2-5 SQL calls. Each method tries build_graph_json_serverside() first, falls back to LazyKG per-node walk on failure.
