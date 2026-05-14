# IVG Engineering Debt
Last updated: 2026-05-12

Review at the start of each IVG session.

---

## LONG-TERM ARCHITECTURAL DIRECTION

### Pydantic-typed Public API (incremental — in progress)

Progress through v1.88.0:
- `SQLQuery` + `QueryMetadata` → Pydantic BaseModel (`translator.py`)
- `IndexHandle` → Pydantic BaseModel, `Literal[type]` validation (`index_protocol.py`)
- `IVGResult` → Pydantic BaseModel for `execute_cypher` return (v1.86.0)
- `_validate.py` → 10 input schemas on high-risk engine methods (v1.87.0)

**Next increment:** `IVGResult` warnings surface through callers; boundary validation on remaining methods.

---

## RESOLVED (session 2026-05-07)

- [x] **BuildNKG 422s → 19s via Rust** — `BuildNKGRust()` / `KG_BUILD_NKG_WRAPPER`. `engine.rebuild_nkg()` auto-picks Rust when `rust_callout=True`.
- [x] **IC3 2-hop COUNT upper bound** — `KHop2CountFast` = 0.07ms O(1) via `^KG("deg2p")`.
- [x] **Spec 152: IC3 2-hop COUNT exact** — `KHop2CountExact` = 0.095ms O(1) via `^KG("deg2p_exact")`. `Build2HopExactStats` Rust+ObjScript. `execute_cypher [:P*2] RETURN count(n)` routes here. Correctness verified (37276 on SF10). Known gap: Rust `HashSet<String>` too slow for SF10-scale build → see follow-up below.
- [x] **Spec 105: Index Protocol Unification** — `engine.index(name)` → `IndexHandle` (Pydantic), `IVGIndex` Protocol, PLAID renames. v1.84.0.
- [x] **All 10 openCypher gaps closed** — Pattern Comprehension + REDUCE. AGENTS.md SQL constraints locked in.
- [x] **Streaming BFS** — unbounded VL path → `_bfs_stream_pages`; no `<MAXSTRING>`. v1.85.0.
- [x] **IVGResult Pydantic model** — `execute_cypher` returns typed `IVGResult`. v1.86.0.
- [x] **Input validation at boundary** — `_validate.py`: 10 Pydantic schemas, 44 tests. v1.87.0.
- [x] **100% public engine method coverage** — 113/113 methods tested. `test_untested_methods.py`.
- [x] **BulkIngestEdges `[Internal]`** — marked in `EdgeScan.cls`.
- [x] **Open Exchange readiness** — Docker-first README, QUICKSTART with working demo.

---

## OPEN DEBT

### P1 — Performance

- [x] **Spec 152 / Build2HopExactStats build time: 323s → 33s** — Root cause was `zf_global`
  non-sequential write overhead (~2.4ms/write into new global pages). Fix: arno ships
  `kg_build_2hop_exact_stream` which serializes all results into `sName\x1fpName\x1fcount\n`
  records, chunks at 9KB into `^ArnoKG("2hs", N)`, returns `CHUNKED:2HS:N`.
  IVG's `NKGAccel.Build2HopExact()` reads chunks with ObjectScript `$Get` (fast sequential)
  and writes `^KG("deg2p_exact")` directly. API boundary cleaned up:
  - `Traversal.Build2HopExactStats` delegates to `NKGAccel.Build2HopExact()` — one call
  - `DecodeBuildResults` removed (arno internals no longer in IVG)
  - Build: **33s** (was 323s, 10× speedup) | Query: **0.108ms** ✅

### P1 — Vector Collection Architecture (spec needed before implementing)

**Problem**: Single `kg_NodeEmbeddings` table with one engine-wide `vector_dtype`.
Different use cases need different models, dtypes, and dimensions simultaneously.

**Proposed design:**

```python
engine.register_embedding_collection(
    name="protein_embeddings",
    dim=768,
    dtype="FLOAT",
    model="nomic-embed-text",
)
engine.store_embedding(node_id, vec, collection="protein_embeddings")
engine.kg_KNN_VEC(query_vec, k=10, collection="protein_embeddings")
```

**Storage**: `^IVG("collections", name) = {dim, dtype, model, created}`  
Collection metadata persisted in IRIS global, retrieved by `_build_index_registry()`.  
Default collection = existing `kg_NodeEmbeddings` with `engine.vector_dtype`.  
Named collections can either:
  - Partition `kg_NodeEmbeddings` by `collection` column (backward compat, no new table)
  - Or use `vec_create_index` infrastructure (`^VecIdx`) for full isolation

**Per-call dtype override** (simpler interim fix, no spec needed):
```python
engine.store_embedding(node_id, vec, dtype="FLOAT")  # overrides engine default
engine.kg_KNN_VEC(query_vec, k=10, dtype="FLOAT")    # match stored dtype
```
This is a 2-line fix per method. Ship in v1.91.1 alongside the Bug A/B/C fixes.

**Deeper named graph / multi-model design** — requires spec:
- Each named graph (`USE graphname`) could have its own embedding collection
- `engine.set_graph("protein_kg")` → uses `protein_embeddings` collection by convention
- Multi-model hybrid search: `engine.kg_RRF_FUSE(..., collections=["protein_embeddings", "pathway_embeddings"])`



- [ ] **HLL union bias ~89% on LDBC social graphs**
  `approx_count_distinct` systematically under-estimates for correlated friend-of-friend sets.
  Fix: HyperMinHash or KMV sketches in `UpdateStructuralHLL`.
  Low urgency — exact path is `KHop2CountExact` (0.095ms); approximate is fine for threshold detection.

### P2 — Cypher Translator Gaps

- [x] **`WHERE n.id IN [list]` with string values** — Was already working correctly; translator emits `IN (?, ...)` with proper parameterization. Confirmed on live IRIS. Tests added.

- [x] **MATCH + aggregation + ORDER BY generates `<UNDEFINED>ma` error** — Was already working in current codebase (SQLCODE -400 fixed in prior releases). Tests confirm DESC ordering and correct counts.

- [x] **`CALL ivg.bm25.search(...) YIELD node` column name mismatch** — Fixed: BM25 CTE now emits `j.node_id AS node` matching VecSearch column convention. `SELECT node AS node_id` resolves correctly.

- [x] **`CALL ivg.ppr(...) YIELD node` column name mismatch** — Fixed: PPR CTE now emits `j.node_id AS node` matching VecSearch column convention.

- [x] **`MATCH p = (...) RETURN p, length(p)` static value** — Fixed: `length(p)` now returns the actual hop count from the pattern structure. 1-hop returns 1, 2-hop returns 2. Variable-length path support uses `path_node_aliases` count.

All fixed in branch `cypher-gaps-complete`, gated by `tests/e2e/test_cypher_gaps_e2e.py` (9 tests, all pass).
  Queries like `WHERE n.pmid IN ["38901234", "38765432"]` or `WHERE n.id IN $ids` with string
  parameters fail in the Cypher translator. The `IN` clause with string literals/params generates
  invalid SQL. Integer `IN` lists work fine.
  **Workaround**: Use OR chaining (`WHERE n.pmid = "38901234" OR n.pmid = "38765432"`) or
  query one value at a time and merge results in the caller. SonOfAnton uses this pattern for
  PMID lookups and must use OR chains or single-value queries.
  Fix: translator needs to emit `IN (?, ?, ...)` with proper string quoting for literal lists,
  and `IN (SELECT value FROM JSON_TABLE($ids, ...))` for parameterized lists.

- [ ] **MATCH + aggregation + ORDER BY generates `<UNDEFINED>ma` error (`SQLCODE: -400`)**
  Queries like `MATCH (n)-[r]->() RETURN n.id, count(r) AS deg ORDER BY deg DESC` fail with
  a fatal SQL compilation error. The translator emits an invalid CTE alias (`ma`) when combining
  per-node aggregation with ORDER BY. Workaround: split into separate queries or aggregate
  at a different scope. Affects `test_hla_kg_e2e::TestCypherTraversal::test_aggregation_degree`
  and `test_multi_label_query` (reverted to simpler queries that work).

- [ ] **`CALL ivg.bm25.search(...) YIELD node` maps column name wrong**
  `YIELD node` expects a column named `NODE` but the BM25 SQL procedure returns `node_id`.
  The Cypher translator YIELD clause does not alias the column name correctly.
  Workaround: use `engine.bm25_search()` directly instead of the Cypher procedure.
  Affects `test_bm25_cypher_procedure`, `test_bm25_cypher_then_graph_join`,
  `test_full_hla_disease_pathway_cypher` (all reverted to engine method calls).

- [ ] **`CALL ivg.ppr(...) YIELD node` same column name mismatch**
  Same issue as BM25: `YIELD node` expects `NODE` but PPR procedure returns `node_id`.
  Workaround: use `engine.kg_PERSONALIZED_PAGERANK()` directly.

- [ ] **`MATCH p = (...) RETURN p, length(p)` not supported**
  Named path binding with `length(p)` returns 0 rows — either `length()` is not implemented
  for named paths, or the path variable `p` is not being resolved.
  Workaround: rewrite as explicit hop queries.

- [ ] **arno BFS `IRISGLOBALORDER` from `$ZF(-5)` DLL callout doesn't iterate globals**
  Root cause confirmed (2026-05-09): `ns.keys()` in rzf cannot iterate globals from a
  `$ZF(-5)` DLL callout because `iris_global_order` (callin API) is not accessible in that
  execution context. Workaround: `1d75d97` string-passing design — ObjectScript serializes
  adjacency via `ExportAdjacencyWithPreds()`, passes to Rust as adj_str, Rust parses in memory.
  Deployed and working. rzf team has repro snippet for the fix:
  ```
  Set dllid = $ZF(-4, 1, "/usr/irissys/mgr/libarno_callout.so")
  Set fnid  = $ZF(-4, 3, dllid, "test_neg_int_keys")
  Write $ZF(-5, dllid, fnid, "RZFTest"), !
  Expected: {"neg_int_keys_count":1,"bug":false}
  Actual:   {"neg_int_keys_count":0,"bug":true}
  ```
  Note: `ns.get()` works (confirmed), only `ns.keys()` / `iris_global_order` fails.


### P3 — API / DX

- [x] **`kg_KNN_VEC` in `engine.index()` protocol** — `"hnsw"` type added to `IndexHandle`
  and dispatch tables. `_build_index_registry` registers `"hnsw"` → `"hnsw"` when
  `_probe_native_vec()` is True (**Community + Advanced Server tiers** — NOT IRIS Server,
  Enterprise, Elite, or Entree which lack SQL vector search).
  **Note: Community Edition explicitly includes Vector Search** — `VECTOR_COSINE`, `EMBEDDING()`,
  HNSW index all work on Community. `engine.index("hnsw")` dispatches `.search()` to
  `search_nodes_by_vector`, `.insert()` to `store_embedding`, `.info()` returns
  `{"type": "hnsw", "available": True}`. `.drop()` is a no-op (IRIS manages HNSW lifecycle).
  **7/7 e2e tests pass on Community Edition.**

- [x] **Spec 153: NKGAccel BFS unified output** — `NKGAccel.BFSJson` now writes to
  `^ArnoKG("bfs_r", tag, step, o)` and returns `"SORTED:tag"` (same as `BFSFastJsonSorted`).
  Engine routes Rust BFS through `ReadBFSResults`/`_bfs_stream_pages` identically to ObjectScript path.
  `BFSFastJsonChunked` legacy branch removed from engine. v1.89.0.
  **Benchmark T010a/T010b verified on enterprise (synthetic 1500-node graph):**
    - Baseline (BFSFastJsonSorted, ObjectScript): **0.6ms p50**
    - New path (NKGAccel.BFSJson SORTED conversion, Rust+ObjectScript): **0.4ms p50**
    - Overhead: **-41% (faster than baseline)** — PASS (threshold ≤20%)
  Note: `NKGAccel.BFSJson` fallback now calls `BFSFastJsonSorted` (not `BFSFastJson`) for consistency.

- [x] **Bug K: `store_node()` / `store_edge()` don't commit in embedded Python context** — Fixed v1.92.2.
  In IRIS embedded Python (Language=python methods inside IRIS), `engine.store_node()` and
  `engine.store_edge()` call `self.conn.commit()` but the commit does not persist across sessions.
  Writes appear to succeed (no exception) but are not visible to subsequent queries in a different
  embedded Python execution context.
  **Confirmed workaround**: Use `iris.sql.exec("INSERT INTO rdf_edges ...")` with inlined literals
  instead of parameterized `cursor.execute()`. Direct `iris.sql.exec` writes persist correctly.
  **Root cause hypothesis**: `self.conn` in embedded context is not the same transaction context
  as `iris.sql` — the embedded Python `EmbeddedConnection` wraps a separate connection object
  that may not be auto-committed by IRIS on method exit.
  **Fix**: Investigate whether `EmbeddedConnection.commit()` needs to call `iris.sql.exec("COMMIT")`
  rather than the Python DB-API `conn.commit()`. See `iris_vector_graph/embedded.py`.


---

## AUDIT: Raw SQL and Non-Engine Patterns (2026-05-09)

Full codebase scan. Every item below bypasses the engine API.
Fix = replace with `engine.create_node()`, `engine.create_edge()`, `engine.delete_node()`,
`engine.execute_cypher()`, `engine.bulk_create_edges()`, etc.

### CRITICAL — `intersystems_iris` imports (must use `iris.createIRIS(conn)`)

| File | Lines | Fix |
|------|-------|-----|
| `tests/integration/test_cls_layer.py` | 12 | `import iris; iris.createIRIS(conn)` |
| `tests/integration/test_objectscript_classes.py` | 19 | same |
| `tests/e2e/test_ppr_cls_fast_path.py` | 22 | already has `iris` fallback, remove `intersystems_iris` branch |
| `scripts/mcp/ivg_mcp_server.py` | 28 | `import iris as irispy` |

### CRITICAL — `classMethodString()` (must use `classMethodValue()`)

| File | Lines |
|------|-------|
| `tests/benchmarks/bench_utils.py` | 29, 36, 46, 56, 66, 121, 127 |
| `tests/benchmarks/bench.py` | 177, 198, 205, 207, 238, 254 |
| `tests/benchmarks/ic2_profile.py` | 36, 44, 51, 63, 84, 93 |
| `tests/e2e/test_large_output_chunked.py` | 37, 39, 51, 69, 152, 155, 159, 175, 192, 201 |
| `tests/e2e/test_lazy_node_resolution.py` | 23, 39, 41, 43, 49 |

### HIGH — Unqualified table names (missing `Graph_KG.` prefix)

All `INSERT/DELETE/SELECT FROM rdf_edges/nodes/rdf_labels/rdf_props/kg_NodeEmbeddings` without `Graph_KG.`:

| File | Count | Category |
|------|-------|----------|
| `tests/integration/test_cls_layer.py` | ~20 | Replace with `engine.create_node/edge/delete_node` |
| `tests/integration/test_objectscript_classes.py` | ~30 | same |
| `tests/integration/test_bidirectional_ppr.py` | ~30 | same |
| `tests/integration/test_cypher_enhancements.py` | ~25 | same |
| `tests/integration/test_pagerank_sql_optimization.py` | ~20 | same |
| `tests/integration/test_nodepk_migration.py` | ~60 | migration-specific, add `Graph_KG.` prefix |
| `tests/integration/test_nodepk_constraints.py` | ~5 | engine methods |
| `tests/integration/test_nodepk_advanced_benchmarks.py` | ~10 | engine methods |
| `tests/integration/test_embeddings_api.py` | ~5 | engine methods |
| `tests/integration/gql/test_graphql_queries.py` | ~35 | engine methods |
| `tests/integration/gql/test_graphql_vector_search.py` | ~20 | engine methods |
| `tests/integration/gql/test_graphql_mutations.py` | ~30 | engine methods |
| `tests/contract/test_ppr_api.py` | ~30 | engine methods |
| `tests/python/test_python_operators.py` | ~5 | engine methods |
| `tests/e2e/test_ppr_cls_fast_path.py` | ~20 | engine methods |
| `tests/e2e/test_streaming_bfs.py` | 98 | engine.execute_cypher() |
| `tests/e2e/test_execution_contexts_new.py` | 158 | engine.execute_cypher() |
| `examples/domains/fraud/loaders.py` | ~10 | engine methods |
| `examples/domains/fraud/resolver.py` | ~20 | engine methods |
| `examples/domains/biomedical/loaders.py` | ~5 | engine methods |
| `examples/domains/biomedical/types.py` | ~5 | engine methods |
| `examples/domains/biomedical/resolver.py` | ~20 | engine methods |
| `examples/domains/biomedical_legacy/legacy_wrapper.py` | ~5 | engine methods |
| `examples/demo_working_system.py` | ~5 | engine methods |
| `examples/demo_biomedical.py` | ~10 | engine methods |
| `examples/demo_utils.py` | ~5 | engine methods |
| `scripts/demo/end_to_end_workflow.py` | ~40 | engine methods |
| `scripts/migrations/migrate_to_nodepk.py` | ~30 | keep as-is (migration script, needs raw SQL) |

### MEDIUM — Hardcoded ports in test code

| File | Line | Fix |
|------|------|-----|
| `tests/python/test_python_sdk.py` | 32 | `int(os.environ.get("IVG_TEST_PORT", "1972"))` |
| `tests/benchmarks/bfs_benchmark.py` | 29 | env var |
| `tests/benchmarks/establish_baseline.py` | 40 | env var |

### MEDIUM — Direct `iris.connect()` in non-conftest test files

| File | Fix |
|------|-----|
| `tests/python/test_python_sdk.py` | use `iris_connection` fixture |
| `tests/e2e/test_stress_setup.py` | use `iris_connection` fixture (partially done) |
| `tests/e2e/test_stress_api.py` | use `iris_connection` fixture (partially done) |
| `tests/benchmarks/*.py` | benchmarks legitimately need own connections — acceptable |
| `scripts/*.py` | scripts legitimately need own connections — acceptable |

### LOW — `IRISGraphOperators(conn)` should be `engine.kg_*()` methods

| File | Count |
|------|-------|
| `tests/e2e/test_graph_kernels_e2e.py` | ~15 instances |
| `tests/e2e/test_operator_wiring_e2e.py` | ~20 instances |
| `tests/e2e/test_subgraph_e2e.py` | ~20 instances |
| `tests/e2e/test_ppr_guided_e2e.py` | 1 |
| `tests/unit/test_bm25_index.py` | 1 |
| `tests/unit/test_subgraph.py` | ~5 |
| `tests/unit/test_graph_kernels.py` | ~3 |
| `tests/unit/test_operators_wiring.py` | ~15 |
| `tests/python/test_python_operators.py` | 1 |
| `examples/demo_working_system.py` | 1 |

### LOW — Raw SQL in library files (not engine.py)

| File | Notes |
|------|-------|
| `iris_vector_graph/bulk_loader.py` | Performance-critical batch loader — raw SQL acceptable, ensure `Graph_KG.` prefix |
| `iris_vector_graph/cypher_api.py:179` | Should use `engine.execute_cypher("MATCH (n) RETURN count(n)")` |
| `iris_vector_graph/gql/resolvers.py:13-18` | `resolve_stats` already uses raw SQL — acceptable for GQL stats resolver |
| `iris_vector_graph/gql/resolvers.py:35,98` | GQL resolvers — acceptable, uses `Graph_KG.` prefix |

### Additional findings from deep scan

**`iris_vector_graph/engine.py` itself — `classMethodString()` at lines 1737, 1770**
These should be `classMethodValue()` per the API contract.

**`iris_vector_graph/operators.py`** — ~50+ raw SQL violations throughout.
The entire `IRISGraphOperators` class contains raw SQL on unqualified tables.
These should be thin wrappers over engine `kg_*` methods. The class is effectively
a pre-engine legacy layer that was never migrated.

**`api/gql/resolvers/mutation.py`, `api/gql/core/resolvers.py`, `api/gql/schema.py`** —
GraphQL resolvers bypass engine, hitting tables directly. Critical because
ObjectScript callers via `IVG.CypherEngine` will see inconsistent behavior.

**`api/gql/loaders.py`, `api/gql/core/loaders.py`** — raw cursor on unqualified
`rdf_labels`, `rdf_edges`. Should use `engine.get_node_labels()` etc.

**`iris_vector_graph/cypher_api.py:180`** — raw cursor for node count. Use
`engine.execute_cypher("MATCH (n) RETURN count(n)")`.

**`src/iris_demo_server/services/iris_biomedical_client.py:24`** —
hardcodes port 1972; also raw cursor throughout. Use env var + engine.

**`tests/e2e/test_lazy_node_resolution.py:8`, `test_large_output_chunked.py:8,21,143`** —
hardcoded port 2972. Fix: `int(os.getenv("IRIS_TEST_PORT", "1972"))`.

**`iris_vector_graph/cypher/algorithms/paths.py`** — raw cursor on
unqualified tables (lines 110, 113, 190, 192).


|----------|-------|-----------------|
| CRITICAL (`intersystems_iris`, `classMethodString`) | 9 files | ~50 |
| HIGH (unqualified tables) | 27 files | ~400 |
| MEDIUM (hardcoded ports, bare `iris.connect`) | 5 files | ~10 |
| LOW (`IRISGraphOperators`, lib raw SQL) | 13 files | ~90 |
| **Total** | **54 files** | **~550** |

**Priority order**: CRITICAL → HIGH integration tests → HIGH examples → MEDIUM → LOW



Hardware: MacBook Pro (M3 Ultra, 128GB RAM), LDBC SF10, IRIS 2025.1 Enterprise in Docker.
Comparison: GES/GraphScope published SF1000 numbers on large server cluster.

| Query | IVG p50 | GES SF1000 p50 | Notes |
|---|---|---|---|
| IC13 ShortestPath (SF1) | 0.22ms | 2.69ms | IVG faster |
| IC13 ShortestPath (SF10) | 2.1–3.2ms | 2.69ms | Comparable |
| IC2 1-hop COUNT (`KHopCount`) | 0.29ms | 0.14ms | Competitive (was 2.8ms) |
| IC2 1-hop IDs (`KHopNeighborIds`) | 0.9ms | — | Fast path |
| IC3 2-hop LIMIT 1000 (`KHop2NeighborIds`) | **1.2ms** | 4.19ms | 3.5× faster than GES |
| IC3 2-hop COUNT exact (`KHop2CountExact`) | **0.095ms** | — | O(1); was 70ms. Requires `Build2HopExactStats` at `BuildNKG` time |
| IC3 2-hop COUNT upper bound (`KHop2CountFast`) | 0.07ms | — | 3.67× overcount; threshold detection only |
| approx_count_distinct 2-hop | 5.3ms | — | 74× vs exact; ~89% accuracy on social graphs |
| BulkIngestEdges | 190–312K edges/s | — | Fast; `^NKG` stale until `rebuild_nkg()` |
| BuildNKG (SF10, Rust) | **19s** | — | Was 422s; 22× speedup via `ffi_kg_build_nkg` |
| Build2HopExactStats (SF10) | timeout | — | `HashSet<String>` too slow; integer-indexed version needed |
| Arno BFSJson 2-hop (SF10, no MAXSTRING) | ~3.5s | — | Chunk-read loop working; `HashSet<String>` bottleneck |
