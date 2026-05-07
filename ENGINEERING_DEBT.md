# IVG Engineering Debt
Last updated: 2026-05-06

This is the persistent reminder list from LDBC benchmark sessions and ongoing development.
Review at the start of each IVG session.

---

## LONG-TERM ARCHITECTURAL DIRECTION

### Pydantic-typed Public API (P1 — incremental)

**Progress so far:**
- `SQLQuery` + `QueryMetadata` → Pydantic BaseModel (translator.py)
- `IndexHandle` → Pydantic BaseModel with `Literal[type]` validation (index_protocol.py)
- **`IVGResult` → Pydantic BaseModel for `execute_cypher` return type (v1.86.0)** ← NEW
  - Backward-compatible via `__getitem__`, `__contains__`, `.get()` overrides
  - `bool(result)` = True on success, False on error
  - 23/23 unit tests pass, all existing call sites unchanged

**Next increment:** Input validation at boundary — `node_id: NonEmptyStr`, `k: PositiveInt` on key engine methods.

---

## RESOLVED (most recent session — 2026-05-06)

- [x] **IC2 1-hop fast path** — `KHopCount` + `KHopNeighborIds`: COUNT 0.29ms (was 2.8ms)
- [x] **IC3 2-hop fast path** — `KHop2Count` + `KHop2NeighborIds`: LIMIT 1000 = 1.2ms (was 14–22ms, 3.5× faster than GES)
- [x] **create_node(graph=)** — stored as `__graph` in rdf_props; propagated to bulk_create_nodes
- [x] **bulk_ingest_edges() + rebuild_nkg()** — safe engine wrappers with `_nkg_dirty` flag + `RuntimeWarning`
- [x] **IVF `<STRINGSTACK>` on 768-dim** — `IVFIndex.AddBatch` + `FinalizeIndex`; `build_batch_size=500`
- [x] **IVFIndex.Insert() + Delete()** — nearest-centroid single-pass insert; 5/5 e2e tests
- [x] **Pattern Comprehension (Gap #5)** — `[(n)-[:R]->(m) | m.prop]` → self-contained correlated subquery
- [x] **REDUCE (Gap #7)** — `reduce(acc, x IN collect(prop) | acc+x)` → `SUM(CAST(...))`, no Python postprocessing
- [x] **SQLQuery + QueryMetadata → Pydantic BaseModel** — first Pydantic increment
- [x] **AGENTS.md SQL Design Constraints** — 4 hard rules in every agent session
- [x] **Spec 105: Index Protocol Unification** — `engine.index(name)` → `IndexHandle` (Pydantic),
      `IVGIndex` runtime_checkable Protocol, `PLAIDSearch.Build` public / helpers Private,
      all `*_info` return `"type"` key, full PLAID e2e coverage. v1.84.0.
- [x] **All 10 openCypher gaps closed** — including Pattern Comprehension and REDUCE
- [x] **Doc audit** — OPERATIONS.md, DATA_FORMATS.md, BENCHMARKS.md, PYTHON_SDK.md all updated
- [x] **ENGINEERING_DEBT.md Pydantic roadmap** — captured as long-term direction

---

## OPEN DEBT

### P0 — Correctness

- [x] **Streaming BFS for unbounded queries** — Fixed in v1.85.0. Unbounded VL path queries
  (`max_results == 0`) now always use `_bfs_stream_pages` (cursor-based paging) instead of
  `ReadBFSResults` (single JSON string that hits `<MAXSTRING>` at 93K+ results).
  Bounded queries (LIMIT present) continue using the fast single-call `ReadBFSResults` path.
  5/5 tests pass (3 e2e + 2 routing unit tests).

### P1 — Performance

- [ ] **IC3 2-hop COUNT: 70ms → <10ms**
  `KHop2Count` bottlenecked by 38K-node `$Order` dedup walk in ObjectScript.
  Fix: `BuildNKG2HopStats` pre-aggregation at `BuildNKG` time, or accept `approx_count_distinct` (5.3ms, ~89% accuracy on social graphs).

- [ ] **BuildNKG 422s on SF10**
  Rust `ffi_kg_build_nkg` compiles clean — needs Linux build of `libarno_callout.so`.
  Also need: `BuildNKGIncremental` (only processes edges since last version number).

### P2 — Accuracy

- [ ] **HLL union bias ~89% on LDBC social graphs**
  `approx_count_distinct` systematically under-estimates for correlated friend-of-friend sets.
  Fix: HyperMinHash or KMV sketches in `UpdateStructuralHLL`.

### P3 — API / DX

- [ ] **`IVGResult` Pydantic model for `execute_cypher`**
  The single highest-value Pydantic increment. `execute_cypher` is the most-called method.
  Returns `Dict[str, Any]` today; should return `IVGResult(columns, rows, metadata, warnings)`.

- [ ] **BulkIngestEdges `[ Internal ]` in ObjectScript**
  `engine.bulk_ingest_edges()` is battle-tested. Mark raw `BulkIngestEdges` as `[ Internal ]`
  in `EdgeScan.cls` so it stops appearing in external callers' autocomplete.

- [ ] **37 untested public engine methods**
  Highest risk: `vec_*` full lifecycle (9 methods), `khop`, `ppr`, `random_walk`,
  `materialize_inference`, `retract_inference`, `restore_snapshot`.

- [ ] **Spec 105 remainder: `kg_KNN_VEC` in `engine.index()` protocol**
  Native HNSW available on Community + Advanced Server tiers is not yet registered in
  `_index_registry` or dispatchable via `engine.index(name)`. Low priority — IVF covers the gap.

---

## BENCHMARK NUMBERS (for reference)

Hardware: MacBook Pro (M3 Ultra, 128GB RAM), LDBC SF10, IRIS 2025.1 Enterprise in Docker.
Comparison: GES/GraphScope published SF1000 numbers on large server cluster.

| Query | IVG p50 | GES SF1000 p50 | Notes |
|---|---|---|---|
| IC13 ShortestPath (SF1) | 0.22ms | 2.69ms | IVG faster |
| IC13 ShortestPath (SF10) | 2.1–3.2ms | 2.69ms | Comparable |
| IC2 1-hop COUNT (KHopCount) | 0.29ms | 0.14ms | Competitive (was 2.8ms) |
| IC2 1-hop IDs (KHopNeighborIds) | 0.9ms | — | New fast path |
| IC3 2-hop LIMIT 1000 (KHop2NeighborIds) | **1.2ms** | 4.19ms | 3.5× faster than GES |
| IC3 2-hop COUNT (KHop2Count) | 70ms | — | Was 195ms; 10ms target needs pre-agg |
| approx_count_distinct 2-hop | 5.3ms | — | 74× vs exact; ~89% accuracy on social graphs |
| BulkIngestEdges | 190–312K edges/s | — | Fast; `^NKG` stale until `rebuild_nkg()` |
| BuildNKG (SF10) | 422s | — | Rust fix ready, needs Linux build |
