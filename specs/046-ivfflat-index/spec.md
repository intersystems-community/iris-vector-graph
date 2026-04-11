# Spec 046: IVFFlat Vector Index

## Overview

Add an IVFFlat (Inverted File with Flat quantization) vector index to iris-vector-graph as a complement to the existing RP-tree (`VecIndex`) and HNSW indexes.

IVFFlat partitions the vector space into `nlist` Voronoi cells via k-means clustering. At query time, only the `nprobe` nearest cells are scanned, giving a tunable accuracy/speed tradeoff. Unlike RP-tree and HNSW (both approximate), IVFFlat with `nprobe == nlist` is exact (full scan, partitioned for cache efficiency). At `nprobe < nlist` it is approximate — higher recall than RP-tree at the same query time for medium-to-large datasets (10K–1M vectors).

## Motivation

- **RP-tree (`VecIndex`)** is fast but recall degrades on datasets > 50K vectors with high-dim embeddings
- **HNSW** (IRIS native) has high recall but no `nprobe` tuning and no Python build path
- **IVFFlat** fills the gap: tunable recall, scales to millions, well-understood recall/speed curve, `nprobe=nlist` gives exact results for correctness testing

The k-means build step is already implemented in Python (`sklearn.cluster.MiniBatchKMeans`) and used by PLAID. The ObjectScript centroid scoring + inverted list scan is already implemented in `PLAIDSearch.cls`. IVFFlat reuses these components for dense single-vector search.

## Functional Requirements

### FR-001: Build Index
```python
engine.ivf_build(
    name: str,
    nlist: int = 256,        # number of Voronoi cells
    metric: str = "cosine",  # cosine | l2 | dot
    batch_size: int = 10000, # k-means batch size
) -> dict                    # {"nlist": N, "indexed": N_docs, "dim": D}
```
- Reads vectors from `kg_NodeEmbeddings`
- Runs `sklearn.cluster.MiniBatchKMeans(nlist)` at build time (Python)
- Stores centroids and inverted lists in `^IVF` globals
- Idempotent — rebuilds if called again

### FR-002: Search Index
```python
engine.ivf_search(
    name: str,
    query: list[float],
    k: int = 10,
    nprobe: int = 8,         # 1 ≤ nprobe ≤ nlist; nprobe=nlist → exact
) -> list[tuple[str, float]] # [(node_id, score), ...] sorted DESC
```
- Finds `nprobe` nearest centroids to query
- Scans all vectors in those cells, scores by exact distance
- `nprobe == nlist` → exact search

### FR-003: Drop / Info
```python
engine.ivf_drop(name: str) -> None
engine.ivf_info(name: str) -> dict  # {} if not found
```

### FR-004: Cypher Procedure
```cypher
CALL ivg.ivf.search(name, query_vec, k, nprobe) YIELD node, score
RETURN node, score ORDER BY score DESC
```

### FR-005: Global Storage
```
^IVF(name, "cfg", "nlist"|"dim"|"metric"|"indexed") = value
^IVF(name, "centroid", k)         $vector — centroid k
^IVF(name, "list", k, node_id)    $vector — node_id vector in cell k
```

## Non-Functional Requirements

- Build: nlist=256, 10K vectors, 768-dim in < 30s
- Search: nprobe=8, nlist=256, 10K vectors in < 10ms
- Recall@10 at nprobe=nlist: 1.0 (exact)
- Recall@10 at nprobe=32, nlist=256, 10K vectors: ≥ 0.90

## Reuse from Existing Code

| Component | Reused from |
|-----------|-------------|
| k-means build (Python) | PLAID build pipeline (`MiniBatchKMeans`) |
| Centroid scoring (ObjectScript) | `PLAIDSearch.Search` Stage 1 |
| `$vectorop` distance | `VecIndex.Distance`, `VecIndex.Cosine` |
| Stage CTE translator | `_translate_bm25_search` pattern |
| Engine wrapper | `plaid_build()`, `vec_build()` patterns |

## Out of Scope
- IVF-PQ (product quantization)
- Online insert without rebuild
- Multi-vector queries (use PLAID)
