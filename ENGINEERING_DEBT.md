# IVG Engineering Debt
Last updated: 2026-05-07

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

### P2 — Accuracy

- [ ] **HLL union bias ~89% on LDBC social graphs**
  `approx_count_distinct` systematically under-estimates for correlated friend-of-friend sets.
  Fix: HyperMinHash or KMV sketches in `UpdateStructuralHLL`.
  Low urgency — exact path is `KHop2CountExact` (0.095ms); approximate is fine for threshold detection.

### P3 — API / DX

- [ ] **`kg_KNN_VEC` in `engine.index()` protocol**
  Native HNSW (Community + Advanced Server) not yet in `_index_registry`.
  Low priority — IVF is the fallback for those tiers.

- [x] **Spec 153: NKGAccel BFS unified output** — `NKGAccel.BFSJson` now writes to
  `^ArnoKG("bfs_r", tag, step, o)` and returns `"SORTED:tag"` (same as `BFSFastJsonSorted`).
  Engine routes Rust BFS through `ReadBFSResults`/`_bfs_stream_pages` identically to ObjectScript path.
  `BFSFastJsonChunked` legacy branch removed from engine. v1.89.0.
  **Benchmark gates deferred** (enterprise container unavailable during implementation).
  Note: `NKGAccel.BFSJson` fallback now calls `BFSFastJsonSorted` (not `BFSFastJson`) for consistency.


---

## BENCHMARK NUMBERS (for reference)

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
