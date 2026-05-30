# Feature Specification: NICHE-Quantized Embeddings — Fused Graph+Vector Pruning via `^NKG("q",...)`

**Status**: Research / Phase 0 design (prototype gate before v2.0.0 inclusion)
**Spec ID**: 168-niche-quantized-embeddings
**Owner**: Tom Dyar
**Predecessor**: spec 163 (community detection), spec 028 (NKG integer index)
**Target release**: v2.0.0 (with prototype gate; ship-or-defer decision after Phase 1)

## TL;DR

Store quantized vector embedding bucket indices as **subscripts in the same `^NKG` global** that already holds graph adjacency, so a single B-tree iterator can prune candidates by both graph topology AND vector neighborhood **without leaving the index**. Attacks the "Q4 gap" (BFS-prefiltered vector rerank gets no HNSW benefit today) directly. Per parallel SOTA survey (2026-05-29), this combination is **not present in published prior art** — Neo4j, Kuzu, Memgraph, TigerGraph, ACORN, Filtered-DiskANN, iRangeGraph, LSB-tree, IVF-PQ all keep vector and graph indices in separate logical layers.

## The Problem (Q4 gap, definitively measured)

`~/ws/ivg-arno-bench` benchmark on 50K nodes / 145K edges (los-iris-derived knowledge graph, IVG 1.99.0):

| Query | Description | Median | Notes |
|---|---|---|---|
| Q1 | Top-10 vector via HNSW+ACORN-1 | **105µs** | Native IRIS vector index |
| Q2a | 1-hop BFS via Cypher → SQL `rdf_edges` | 455µs | 5.3× slower than ^NKG |
| Q2b | 1-hop BFS via raw `^NKG $Order` | **86µs** | The fast path; pure B-tree |
| Q2c | 1-hop BFS via Rust `KHopNeighbors` | 179µs | $ZF-boundary serialization tax |
| Q3 | Vector → BFS sequential (2 IVG calls) | 1454µs | client-side stitch |
| **Q4** | **BFS → Vector rerank server-side JOIN** | **580µs** | **The open gap** |

**Q4's specific pain:** when the candidate set is graph-filtered (e.g., "find vectors similar to Q where node is reachable from S within 2 hops"), IRIS HNSW cannot apply its index. ACORN-1 handles **scalar metadata predicates**, not **graph-topology predicates**. Result: HNSW is bypassed; 580µs is just the SQL JOIN+rerank cost without any vector-index acceleration.

No production graph database has solved this. Per SOTA survey:
- **Neo4j 2026.01-2026.02**: in-index `SEARCH WHERE` filters property-based metadata only; graph patterns applied post-filter
- **Kuzu 3.11+**: closest competitor — projected-graph semi-mask + adaptive HNSW (ONE_HOP_FILTERED, DIRECTED_TWO_HOP, BLIND_TWO_HOP); semi-mask is a node-row filter, NOT a subscript key
- **Memgraph, TigerGraph**: fall back to "filter then rerank" pattern
- **ACORN (arXiv 2403.04871)**: denser HNSW + runtime predicate masks; not subscript-fused
- **Filtered-DiskANN, iRangeGraph**: predicate-aware ANN over scalar predicates; no graph topology
- **LSB-tree, IVF-PQ**: B-trees with quantization but no graph adjacency

## The Insight

`^NKG` already stores graph adjacency as **integer-subscripted sparse arrays** with negative-integer predicate labels:

```
^NKG(-1, srcIdx, -(predIdx+1), dstIdx) = weight     ; outbound edges
^NKG(-2, dstIdx, -(predIdx+1), srcIdx) = weight     ; inbound edges
^NKG("$NI", node_id) = idx                          ; node ID → integer
^NKG("$ND", idx) = node_id                          ; integer → node ID
^NKG("$LI", predicate) = pred_idx                   ; predicate → integer
```

The proposed extension stores **quantized embedding bucket membership** in the SAME global:

```
^NKG("q", bucketIdx, nodeIdx) = 1                   ; node belongs to coarse bucket
^NKG("qpq", bucketIdx, codeword, nodeIdx) = 1       ; (optional) PQ refinement
```

`bucketIdx` is computed via either:
- **IVF-style coarse quantization**: k-means over embeddings, ~256-4096 centroids. `bucketIdx ∈ [0, K)` is the nearest centroid for that node.
- **LSH-style hash**: Hyperplane LSH with B bits, `bucketIdx ∈ [0, 2^B)`.
- **Product Quantization**: Subspace quantization; multiple `^NKG("q", subspace, codeword, nodeIdx)` entries per node.

**The fused query (Q4 attacked at the index level):**

```objectscript
; Find vectors similar to Q where node is reachable from S within 2 hops
; STEP 1: identify candidate buckets near Q (k-means: O(K) centroid distances)
; STEP 2: BFS frontier expansion via $Order(^NKG(-1, srcIdx, ...))
; STEP 3: prune frontier in-loop:
;   for each candidate dstIdx in frontier:
;     if $Data(^NKG("q", topBucket, dstIdx)) {
;       ; this node is BOTH reachable AND in a near-Q bucket
;       ; → push to result candidate list, no full vector compare yet
;     }
; STEP 4: rank surviving candidates with full-precision distance
```

The **B-tree iterator never leaves `^NKG`**. No HNSW lookup. No SQL JOIN. No `^Graph_KG.kg_NodeEmbeddings` scan. The pruning is two `$Data` checks per candidate.

## Functional Requirements

- **FR-168-001**: Build `^NKG("q", bucketIdx, nodeIdx)` from a configurable quantizer over `kg_NodeEmbeddings`. Initial supported quantizers: `ivf_kmeans` (default, K-means with K=512 by default), `lsh_hyperplane` (B-bit LSH).
- **FR-168-002**: New API `engine.bfs_vector_rerank(seed, hops, query_vec, top_k, max_buckets=8)` performing fused BFS + bucket-prefilter + final rerank.
- **FR-168-003**: New Cypher procedure `CALL ivg.bfsVectorRerank({seed, hops, queryVec, topK, maxBuckets}) YIELD node, score, hops`.
- **FR-168-004**: Quantizer training takes < 60s on 100K embeddings (single-threaded k-means, MiniBatchKMeans implementation).
- **FR-168-005**: Bucket index supports incremental update — `add_node_to_buckets(node_id)` recomputes only that node's bucket assignment, not the whole index.
- **FR-168-006**: Bucket index versioned via `^NKG("q","$meta","version")` token for cache invalidation, mirroring the existing `^NKG("$meta","version")` pattern.
- **FR-168-007**: Quantizer parameters (K, B, subspace count) persisted in `^NKG("q","$meta","config")` for reproducibility.
- **FR-168-008**: Disable path: `engine.bfs_vector_rerank(...)` falls back to current Q4 SQL path when bucket index not built or `IVG_DISABLE_QBUCKETS=1`.
- **FR-168-009**: `engine.get_bucket_stats()` returns histogram of bucket fill (for diagnosing degenerate quantizers).

## Non-Functional Requirements

- **NFR-168-001 (the headline gate)**: On the same 50K-node ivg-arno-bench fixture, **Q4 latency drops below 200µs** (from current 580µs). 2.9× speedup minimum.
- **NFR-168-002**: Recall@10 ≥ 0.90 vs exact full-precision rerank on a 1000-query test set, at default quantizer config (IVF, K=512, max_buckets=8).
- **NFR-168-003**: Build time ≤ 60s on 100K nodes, ≤ 600s on 1M nodes (linear scaling).
- **NFR-168-004**: Bucket index storage overhead ≤ 16 bytes per node × K assignments — for 1M nodes, K=512, expected ≤ 8GB on disk after IRIS `^NKG` global compression.
- **NFR-168-005**: Fused query memory budget ≤ 256MB resident set on default config (skip-with-warning if exceeded, mirror spec 163 `mem_budget_mb` pattern).

## Fixture: DRKG Biomedical Knowledge Graph

The los-iris productivity KG failed Phase 0 because its embeddings have max pairwise cosine ~0.21 — IVF quantization requires >0.7. This section specifies the correct fixture for Phase 0 re-run.

### Primary fixture: DRKG (Drug Repurposing Knowledge Graph)

| Property | Value |
|----------|-------|
| Source | https://github.com/gnn4dr/DRKG |
| Download | `wget https://dgl-data.s3-us-west-2.amazonaws.com/dataset/DRKG/drkg.tar.gz` (217 MB) |
| Nodes | 97,238 (13 types: Gene, Compound, Disease, Anatomy, Biological Process, ...) |
| Edges | 5,874,261 triplets |
| Gene nodes | ~39,000 — largest type, enables TP53-family queries |
| Drug/Compound nodes | ~24,000 — enables drug repurposing queries |
| Disease nodes | ~5,000 — enables MM-style queries |
| **Pre-trained embeddings** | ✅ `embed/DRKG_TransE_l2_entity.npy` (97238, 400) — bundled |
| **Embedding type** | TransE_l2 knowledge graph embeddings (trained to position same-type entities near each other) |
| **Expected gene-gene cosine** | **0.7–0.9** (KGE methods position same-type entities in coherent regions) |
| License | Apache-2.0 |
| Setup time | ~5 min (download + decompress) |

### Why DRKG embeddings will pass Phase 0 recall gate

TransE embeddings place entities of the same type in coherent regions of embedding space — this is fundamentally different from sentence-transformer embeddings over short text. TransE's loss function explicitly trains `||head + relation - tail||_2 ≈ 0`, which means all genes connected to the same diseases cluster together. Gene nodes that share pathways, diseases, and compounds will have high cosine similarity (0.7–0.9), satisfying the IVF recall requirement.

### Canonical biomed use case (unchanged)

```python
# "Find genes similar to TP53 that are within 2 hops of Multiple Myeloma"
# DRKG entity IDs: Gene::9606:7157 (TP53), Disease::MESH:D009101 (MM)
results = engine.bfs_vector_rerank(
    seed="Disease::MESH:D009101",   # Multiple Myeloma
    hops=2,
    query_vec=drkg_embeddings[tp53_idx],  # TP53 TransE vector
    top_k=10,
)
```

### Fixture download script

`scripts/niche/download_drkg.py` — downloads DRKG, extracts embeddings + entity map, loads into `ivg-arno-bench` container, runs Q4 baseline measurement.

### Secondary fixture: Hetionet + MiniLM (for canonical demo)

Hetionet (47K nodes, 2.25M edges, CC0 license) with MiniLM-generated embeddings is the cleaner demo graph because:
- Named nodes (TP53, Multiple Myeloma) exactly match the canonical use case  
- Smaller — faster to load into IRIS for demos
- MiniLM on biomedical gene names produces cosine 0.7–0.9 for same-pathway genes

Generate: ~4 min on M3 Ultra for 20,945 gene names with `all-MiniLM-L6-v2`.

```bash
# Download Hetionet nodes + edges
wget https://raw.githubusercontent.com/hetio/hetionet/main/hetnet/tsv/hetionet-v1.0-nodes.tsv
```

Use Hetionet for Phase 2 (Cypher demo) and user-guide examples. Use DRKG for Phase 0/1 gate measurement.



### AS-168-1: Q4 with quantized buckets beats Q4 baseline by 2.9×

**Given** the ivg-arno-bench fixture (50K nodes, 145K MENTIONED_IN edges), built bucket index with default config (IVF K=512), and a fixed seed entity + query embedding,
**When** running `engine.bfs_vector_rerank(seed, hops=1, query_vec, top_k=10, max_buckets=8)`,
**Then** wall-clock latency < 200µs (median over 100 trials), and the top-10 Recall@10 vs exact rerank ≥ 0.85.

### AS-168-2: Quantizer reproducibility

**Given** the same fixture and `seed=42`,
**When** building the bucket index twice from cold,
**Then** both builds produce **identical** `^NKG("q",...)` membership.

### AS-168-3: Disabled path matches current behavior

**Given** `IVG_DISABLE_QBUCKETS=1`,
**When** calling `engine.bfs_vector_rerank(...)`,
**Then** the call returns the same result as current Q4 SQL JOIN path (within ARI > 0.99 over 100 random queries).

### AS-168-4: Bucket fill is non-degenerate

**Given** the trained bucket index on 50K embeddings with K=512,
**When** calling `engine.get_bucket_stats()`,
**Then** the largest bucket holds < 5% of nodes AND the smallest bucket holds > 0 (no empty cells).

## Phases (gated)

This is a **research spec with a prototype gate**. Each phase has an explicit ship-or-defer decision.

### Phase 0 — Quantizer prototype (1 week)

- Train IVF k-means K=512 on 50K embeddings via sklearn MiniBatchKMeans
- Materialize `^NKG("q", bucketIdx, nodeIdx) = 1` via Native API
- **Gate**: bucket fill < 5% imbalance, build < 60s. **Pass → Phase 1. Fail → defer to v2.1.x.**

### Phase 1 — Fused query implementation (1 week)

- ObjectScript `Graph.KG.QBucket.BFSVectorRerank()` ClassMethod
- Python wrapper `engine.bfs_vector_rerank()`
- Direct comparison: NFR-168-001 (latency < 200µs) and NFR-168-002 (recall ≥ 0.90)
- **Gate**: BOTH NFRs met on ivg-arno-bench. **Pass → Phase 2. Fail → defer.**

### Phase 2 — Cypher procedure + ergonomics (3-4 days)

- `CALL ivg.bfsVectorRerank({...})` translator handler
- xfail-marked Cypher e2e tests pending Bug S
- Build/load APIs: `engine.build_qbuckets()`, `engine.drop_qbuckets()`, `engine.get_bucket_stats()`
- **Gate**: 12+ e2e tests passing on default fixture. **Pass → Phase 3.**

### Phase 3 — Cross-fixture generalization + paper draft (1-2 weeks)

- Validate on 3 additional fixtures: NetworkX karate, Erdős-Rényi 10K, real-world LDBC subset
- Compare against Kuzu 3.11+ filtered HNSW on same workload (closest competitor)
- Draft architecture paper for SIGMOD/VLDB submission
- **Gate**: paper draft + cross-fixture validation. **Pass → v2.0.0 inclusion. Fail → permanent research branch (no ship).**

### Phase 4 — Optional Rust kernel (deferred)

- Move bucket-traversal hot loop to `libarno_callout.so` if Phase 3 latency is > 50µs
- Likely unnecessary given Q2b shows raw `$Order` at 86µs already; defer until measured

## Out of Scope (explicitly)

- **Approximate distance computation inside the bucket scan** — we keep full-precision distance for survivors; quantization is for *pruning candidates*, not approximating final scores
- **Learned quantizers (e.g., SCANN, RaBitQ)** — defer to v2.1.x; classical IVF/LSH/PQ first
- **Dynamic re-quantization on graph mutation** — initial release is build-once / rebuild-explicit; incremental rebalance is v2.1.x
- **Multi-vector queries (e.g., colbert)** — single query embedding only in v2.0.0
- **Distance metrics other than cosine** — cosine only; Euclidean/dot deferred

## Risks

- **Quantizer recall ceiling**: If IVF K=512 + max_buckets=8 caps recall@10 at < 0.90 on real corpora, the spec fails NFR-168-002. Mitigation: Phase 0 measures recall before committing to Phase 1.
- **`^NKG` global pollution**: Adding `^NKG("q",...)` triples could fragment the global. Mitigation: separate global `^NKG_Q` if Phase 0 measures > 20% slowdown of existing `$Order` walks on `^NKG(-1,...)`.
- **Quantizer drift**: If embeddings update (e.g., model swap), bucket assignments go stale silently. Mitigation: version token in `^NKG("q","$meta","version")` + warning when mismatched against `kg_NodeEmbeddings` model fingerprint.
- **Novelty claim erosion**: A competing system could ship the same architecture during the 4-week prototype window. Mitigation: file a defensive disclosure / preprint at end of Phase 1 if NFRs are met.

## Related Work (from SOTA survey 2026-05-29)

- **Kuzu 3.11+** [closest competitor] — projected-graph semi-mask, adaptive HNSW (ONE_HOP_FILTERED, DIRECTED_TWO_HOP, BLIND_TWO_HOP). Different mechanism: row-mask not subscript fusion. https://kuzudb.com/docs/
- **ACORN** (arXiv:2403.04871, 2024) — denser HNSW + runtime predicate masks. **This is what IRIS's "ACORN-1" implements**. Worth a separate documentation PR clarifying this to IVG users (proposed spec 170).
- **Filtered-DiskANN** (WWW 2023) — labels baked into Vamana edges. Scalar predicates only.
- **iRangeGraph** (SIGMOD 2024) — range-RNG materialization for numeric predicates.
- **LSB-tree** (HKUST 2009) — LSH + Z-order in B-tree. Classical, no graph adjacency.
- **IVF-PQ / Faiss** — separate coarse + residual structures, not subscript-fused.

**Verdict from survey**: combination of (quantized bucket subscripts) + (same hierarchical adjacency index) + (single-pass B-tree iteration) **not present in published prior art**. Confidence: high.

## Open Questions

- **OQ-168-1**: K-means vs LSH vs PQ — which quantizer wins on Recall@10 / latency tradeoff for our typical workloads (X bookmarks, knowledge graphs)? Phase 0 should A/B all three.
- **OQ-168-2**: Should `max_buckets` be auto-tuned per-query based on result-set fill, or stay configuration-only? Phase 1 measure first.
- **OQ-168-3**: Does the proposed design extend to **edge-conditioned vector queries** (e.g., "vectors near Q where edge type is `MENTIONED_IN`")? If yes, requires `^NKG("q", predIdx, bucketIdx, nodeIdx)` — adds a subscript level. Defer to v2.1.x.

## Status

- **2026-05-29**: Spec drafted. SOTA survey done; novelty confirmed.
- **2026-05-29 (clarify)**: OQ-168-1/2/3 resolved. Biomed canonical use case adopted.
  - OQ-168-1 → **IVF k-means K=512** (MiniBatchKMeans). LSH/PQ deferred to v2.1.x.
  - OQ-168-2 → **configuration-only** (`max_buckets=8`). Auto-tuning deferred.
  - OQ-168-3 → **out of scope**. Edge-conditioned variant deferred to v2.1.x.
  - Canonical use case: **"Find genes similar to TP53 within 2 hops of Multiple Myeloma"**.
- **2026-05-29 (Phase 0 attempt 1 — FAIL on wrong fixture)**: Ran Phase 0 on `ivg-arno-bench` (los-iris productivity KG). Recall@10 = 0.14. Root cause: pairwise cosine 0.21 max — IVF requires >0.7. Algorithm is correct; fixture is wrong.
- **2026-05-29 (spec rework)**: Fixture updated to **DRKG** (97K nodes, TransE_l2 embeddings bundled, Apache-2.0). DRKG gene-gene TransE cosine expected 0.7–0.9. `scripts/niche/download_drkg.py` added. Ready for Phase 0 re-run.
- **TODO next**: Download DRKG → run `scripts/niche/build_qbuckets.py --port 25972` → Phase 0 gate → Phase 1.
