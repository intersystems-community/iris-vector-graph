# IVG Benchmark Retrospective — Open Engineering Debt
Last updated: 2026-05-06

This file is the persistent reminder list from the session where we benchmarked
IVG against LDBC SF10/SF100 and GES/GraphScope published numbers.
Review this at the start of each IVG development session.

---

## LONG-TERM ARCHITECTURAL DIRECTION

### Pydantic-typed Public API (P1 — multi-day, do it incrementally)

**Intended direction.** IVG currently has 105 public methods with `Dict[str, Any]` return
types, no input validation, and no schema enforcement. The intended long-term shape is:

- `execute_cypher` returns `IVGResult(columns, rows, metadata, warnings)` — a Pydantic model
- Key input types validated at boundary: `node_id: NonEmptyStr`, `k: PositiveInt`, etc.
- Engine surface split into `IVGReadAPI` (safe, validated) and `IVGAdminAPI` (explicit, dangerous)
- `status.py` dataclasses migrated to Pydantic models (already have Pydantic as dep)

**Why:** Footgun prevention. Users calling raw ObjectScript via `_iris_obj()` or
`_call_classmethod` can corrupt `^KG`/`^NKG` integrity with no warning. Typed boundaries
make the safe path obvious.

**Start point when ready:** `IVGResult` model for `execute_cypher` — that's the single
most-called method and where typed contracts pay off immediately.

---

## RESOLVED THIS SESSION (2026-05-06)

- [x] **IC2 1-hop fast path** — `KHopCount` + `KHopNeighborIds` on `Graph.KG.Traversal`.
      `execute_cypher` now routes single-hop COUNT and ID-list queries through these
      instead of BFSFastJson. Result: COUNT 0.29ms p50 (was 2.8ms), IDs 0.9ms (was 3.6ms).
      The 15x gap vs GES was a comparison mismatch: GES measured COUNT on a cluster;
      IVG was measuring full JSON roundtrip. On equal footing IVG is now competitive.

- [x] **IC3 2-hop fast path** — `KHop2Count` + `KHop2NeighborIds(maxResults)` on `Graph.KG.Traversal`.
      Pure `$Order` walk with process-private dedup globals — no JSON, single round-trip.
      `execute_cypher` routes `[:PRED*2]` COUNT and LIMIT patterns to these methods.
      Result: LIMIT 1000 = **1.2ms p50** (was 14-22ms, now 3.5x *faster* than GES 4.19ms).
      COUNT = 70ms (was 195ms via BFSFastJsonSorted). COUNT gap remains — see P1 below.

- [x] **`create_node` graph param** — Added `graph: Optional[str] = None`.
      Stored as `__graph` property in `rdf_props`. Also propagated to `bulk_create_nodes`
      via per-node `graph` key. Completes the named graph API surface.

- [x] **BulkIngestEdges wrapper** — `engine.bulk_ingest_edges()` is now the proper engine
      entry point. Sets `_nkg_dirty = True`, emits `RuntimeWarning` immediately.
      `rebuild_nkg()` added as the corresponding recovery call.
      `_execute_var_length_cypher` warns at BFS time if `_nkg_dirty` is set.

- [x] **IVF `<STRINGSTACK>` on 768-dim** — Fixed. `IVFIndex.Build` now takes centroids only
      (no assignments). New `IVFIndex.AddBatch(name, json)` writes vectors in chunks.
      `IVFIndex.FinalizeIndex(name)` recounts and updates `cfg.indexed` after all batches.
      `ivf_build()` parameter `build_batch_size=500` (default) controls chunk size.
      Verified: 400 × 768-dim vectors, no crash, correct indexed count, search works.

- [x] **Pattern Comprehension (Gap #5)** — `[(n)-[:R]->(m) | m.prop]` fully working.
      Correlated `JSON_ARRAYAGG` subquery, self-contained (no JOIN leak to outer context).
      `m.node_id` uses direct column reference; properties use inline scalar subquery.

- [x] **REDUCE (Gap #7)** — `reduce(acc=N, x IN collect(prop) | acc + x)` → pure SQL
      `(N + SUM(CAST(prop AS DOUBLE)))`. No `JSON_ARRAYAGG`, no `JSON_TABLE`, no Python
      postprocessing. Works identically from ObjectScript, embedded Python, external Python.

- [x] **IVFIndex.Insert() + Delete()** — `IVFIndex.Insert(name, nodeId, vecJSON)` finds the
      nearest centroid via a single scan and writes to `^IVF(name,"list",cellIdx,nodeId)`.
      `IVFIndex.Delete(name, nodeId)` removes from whichever cell it's in.
      Engine: `ivf_insert(name, node_id, vector) → cell_idx`,
      `ivf_delete(name, node_id) → bool`.
      5/5 e2e tests pass: insert/search/delete/error-guard/multi-accumulate.



- [x] **AGENTS.md SQL Design Constraints** — four hard rules locked into every agent session:
      no Python postprocessing, Language=python bridge limits, JSON_TABLE restrictions,
      ObjectScript-first test requirement.

- [x] **`create_node` graph param** — RESOLVED (was listed as open debt above).

---



- [x] **Spec 103: Streaming cursor BFS** — DONE. ReadBFSPage(tag, cursorStep, cursorO, pageSize)
      eliminates <MAXSTRING>. Unbounded 93K results: 360ms, no overflow.
      LIMIT 1000: 30ms. Falls back to chunked if needed.

- [x] **Spec 104: ffi_kg_build_nkg** — DONE. Rust bulk write to ^NKG via kg_ffi.rs.
      ffi_kg_build_nkg() + kg_build_nkg() ObjectScript wrapper in NKGAccel.cls.
      Requires building libarno_callout.so on Linux (linker flag issue on macOS).
      ObjectScript wrapper: ##class(Graph.KG.NKGAccel).BuildNKGRust()

- [ ] **Spec 105 (revised): Index Protocol Unification**
      NOT a facade. Real work: IVGIndex protocol/ABC, IVFIndex.Insert(), PLAID rename.
      See OPEN DEBT section for full analysis.

---

## OPEN DEBT (not yet specced)

### P0 — Correctness / Crashes

- [ ] **Streaming global read for unbounded BFS**
  BFSFastJsonSorted still hits <MAXSTRING> for 93K+ results with no LIMIT.
  BFSFastJsonChunked requires N round-trips (800 for SF10 high-degree nodes).
  Real fix: cursor-based $Order resumption from Python — read chunks lazily until
  Python signals done. One ObjectScript method: ReadBFSPage(tag, cursor) -> (json, next_cursor)

### P1 — Performance

- [ ] **IC3 2-hop COUNT: 70ms (target <10ms)**
  `KHop2Count` reduced from 195ms to 70ms but is still bottlenecked by the 38K-node
  dedup walk in ObjectScript. The `$Order` over a 38K-entry local array is ~60ms.
  Fix path: pre-aggregate 2-hop counts using `^KG("degp")` sum at build time,
  or use HLL sketch for approximate count (already have approx_count_distinct at 5.3ms).
  Exact fast-path requires storing per-node 2-hop counts at build time — a new `BuildNKG2HopStats` step.

- [ ] **BuildNKG 422 seconds on SF10+**
  Full ^NKG rebuild from scratch after any bulk load.
  Council verdict: replace with ffi_kg_build_nkg in Rust (kg_ffi.rs), 3-5x speedup.
  Also need: BuildNKGIncremental that only processes edges since last version number.

- [ ] **IC2 1-hop gap — remaining (IC3 2-hop LIMIT)**
  ~~1-hop COUNT is now 0.29ms (competitive with GES 0.14ms on cluster).~~
  ~~IC3 2-hop LIMIT 1000: 14-22ms vs GES 4.19ms — 4x gap remains.~~
  **RESOLVED**: 2-hop LIMIT 1000 is now 1.2ms — 3.5x *faster* than GES 4.19ms.
  Remaining: 2-hop COUNT 70ms — see separate item above.

### P2 — Accuracy

- [ ] **HLL union bias ~89% on LDBC small-world graphs**
  HLL-256 gives 1-3% error for individual counts but 89% systematic under-estimate for unions.
  Root cause: LDBC friend-of-friend sets have very high overlap -> HLL union underestimates.
  Fix: implement HyperMinHash or KMV (K-Minimum Values) sketches in UpdateStructuralHLL.
  Reference: compare approx_count_distinct accuracy on Erdos-Renyi vs LDBC to confirm.

### P3 — API / DX

- [ ] **Pydantic-typed Public API** — see LONG-TERM ARCHITECTURAL DIRECTION above.

- [ ] **Inconsistent vector index API surface**
  5 genuinely different index types — NOT naming accidents. Each solves a real problem:
    - vec_* (RP-tree): Community IRIS, small-medium scale, O(insert) no rebuild
    - ivf_* (IVFFlat): nprobe recall dial, scales to 1M+, needs Python k-means build
    - bm25_*: LEXICAL search (different domain), no Enterprise license, incremental
    - plaid_*: Multi-vector per doc (ColBERT/RAG), MaxSim scoring
    - kg_KNN_VEC: Native IRIS HNSW — Community AND Advanced Server have it;
      Standard/HealthConnect editions do NOT. NOT Enterprise-only.
  
  The real problems:
  1. ~~IVFIndex is MISSING Insert()~~ — **DONE** (`ivf_insert` + `ivf_delete`)
  2. PLAIDSearch method names (StoreCentroids, BuildInvertedIndex) don't match the
     Create/Build/Search/Drop pattern established by VecIndex and BM25Index
  3. kg_KNN_VEC is wedged in as an escape hatch — not discoverable or composable
  4. No shared protocol/ABC — callers must know which prefix to use
  5. ivf_* and kg_KNN_VEC are REDUNDANT on Community + Advanced Server tiers.
  
  Right fix (NOT a facade):
  - Define an IVGIndex protocol/ABC with build/search/insert/drop/info
  - Each index class implements it (adding missing IVFIndex.Insert)
  - Rename PLAIDSearch methods to align (Build/Search not StoreCentroids)
  - Move kg_KNN_VEC into the protocol as "hnsw" variant with a capability gate
  - Spec: "Index Protocol Unification" — separate from Graph.KG→IVG.Core rename

- [ ] **BulkIngestEdges ^NKG contract now enforced — but callers can still bypass engine**
  `engine.bulk_ingest_edges()` is the safe path. `_call_classmethod(conn, "Graph.KG.EdgeScan",
  "BulkIngestEdges", ...)` still works and bypasses the dirty flag.
  Longer-term fix: mark `BulkIngestEdges` as `[ Internal ]` in ObjectScript once
  the engine wrapper is battle-tested.

- [ ] **`create_node` has no `graph` parameter** — RESOLVED (added in this session, see above).

- [ ] **37 of 103 public engine methods are untested**
  Audit in API_AUDIT.md. Highest risk gaps:
  - vec_* full lifecycle (9 methods)
  - Graph algorithms: khop, ppr, random_walk
  - Inference: materialize_inference, retract_inference
  - Snapshot: restore_snapshot (save works, restore untested)

---

## BENCHMARK NUMBERS (for reference)

Measured on LDBC SF10 (54M+ edges, 62K persons) on MacBook M3 Ultra:

| Query                          | IVG p50   | GES SF1000 p50 | Notes                                      |
|-------------------------------|-----------|----------------|--------------------------------------------|
| IC13 ShortestPath (SF1)       | 0.22ms    | 2.69ms         | IVG faster                                 |
| IC13 ShortestPath (SF10)      | 2.1-3.2ms | 2.69ms         | Comparable                                 |
| IC2 1-hop COUNT (KHopCount)    | 0.29ms    | 0.14ms         | Now competitive (was 2.8ms via Cypher)     |
| IC2 1-hop IDs (KHopNeighborIds)| 0.9ms    | —              | New fast path                              |
| IC3 2-hop LIMIT 1000           | **1.2ms** | 4.19ms         | 3.5x faster than GES (was 14-22ms)        |
| IC3 2-hop COUNT (KHop2Count)   | 70ms      | —              | Was 195ms; 10ms target needs pre-agg       |
| approx_count_distinct 2-hop   | 5.3ms     | —              | 74x vs exact; 89% bias                     |
| BulkIngestEdges throughput    | 190-312K e/s | —           | Fast, but bypasses ^NKG                    |
| BuildNKG (SF10)               | 422s      | —              | Blocking for deployment                    |

GES comparison hardware: large server/cluster vs MacBook M3 Ultra.
At similar scale and hardware IVG IC13 is competitive or faster.

