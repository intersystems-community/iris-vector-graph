# Research: PLAID Multi-Vector Retrieval

**Feature**: 029-plaid-search | **Date**: 2026-03-29

## R1: Build Architecture — Hybrid Python/ObjectScript

**Decision**: Python runs K-means (sklearn), passes centroids + assignments to ObjectScript via JSON. ObjectScript builds the inverted index over globals.

**Rationale**: K-means on 25K×128 vectors takes ~50ms in numpy vs ~400ms in ObjectScript. Build is a batch operation — no latency sensitivity. The query path (which IS latency-sensitive) stays pure ObjectScript. This matches the VecIndex pattern: Python for orchestration, ObjectScript for the hot path.

**Build pipeline**:
1. Python: `sklearn.cluster.KMeans(n_clusters=K).fit(all_tokens)` → centroids, labels
2. Python → IRIS: `classMethodVoid("PLAIDSearch", "StoreCentroids", name, centroidsJSON)`
3. Python → IRIS: `classMethodVoid("PLAIDSearch", "StoreDocTokens", name, docId, tokensJSON)` per doc
4. Python → IRIS: `classMethodVoid("PLAIDSearch", "BuildInvertedIndex", name, assignmentsJSON)`

## R2: ^PLAID Global Structure

**Decision**: Flat global structure optimized for `$Get` and `$Order` in the query hot path.

```
^PLAID(name, "centroid", k) = $vector (128-dim)     — centroid embeddings
^PLAID(name, "docCentroid", centroidId, docId) = ""  — inverted index (which docs have tokens in centroid k)
^PLAID(name, "docTokens", docId, tokPos) = $vector   — per-document token embeddings for MaxSim
^PLAID(name, "meta", "nCentroids") = K
^PLAID(name, "meta", "nDocs") = N
^PLAID(name, "meta", "dim") = 128
^PLAID(name, "meta", "totalTokens") = M
```

**Rationale**: Each global node is a single `$Get` (0.005ms). The inverted index uses `$Order` for fast iteration. Token embeddings stored per-document for MaxSim scoring — `$Order` on `^PLAID(name, "docTokens", docId, *)` iterates all tokens for a candidate document.

## R3: Three-Stage Search in ObjectScript

**Decision**: Single `ClassMethod Search()` implements all three stages using process-private globals for intermediate results.

**Stage 1 — Centroid scoring** (~0.5ms):
- For each query token: compute dot product against all K centroids via `$vectorop`
- Accumulate scores per centroid: `^||centroidScore(k) += dot`
- Pick top `nprobe` centroids

**Stage 1.5 — Candidate generation** (~0.1ms):
- `$Order` on `^PLAID(name, "docCentroid", topCentroid, *)` to collect candidate doc IDs
- Union across top centroids into `^||candidates(docId) = ""`

**Stage 2 — Exact MaxSim** (~8.5ms for 50 candidates):
- For each candidate doc: for each query token: find max dot product against all doc tokens
- Sum the max dots = MaxSim score
- Store in `^||ranked(-score, docId)` for sorted output

**Total**: ~9ms on 500 docs, 4 query tokens.

## R4: nprobe Default

**Decision**: Default `nprobe = 4` (probe top 4 centroids per query token).

**Rationale**: With √25000 ≈ 160 centroids, nprobe=4 probes 2.5% of centroids. The PLAID paper uses nprobe=2-4 for ColBERT. Higher nprobe increases recall at the cost of more candidate documents to score in Stage 2. nprobe=4 targets 90%+ recall.

## R5: Python Wrapper Design

**Decision**: `plaid_build()` is the only complex Python method (runs K-means, marshals data). All other methods are thin wrappers around `classMethodValue`.

```python
def plaid_build(self, name, docs, n_clusters=None, dim=128):
    # docs = [{"id": "doc1", "tokens": [[f1,...,f128], [f1,...], ...]}, ...]
    all_tokens = np.vstack([np.array(d["tokens"]) for d in docs])
    K = n_clusters or int(np.sqrt(len(all_tokens)))
    kmeans = KMeans(n_clusters=K).fit(all_tokens)
    # Store centroids
    # Store doc tokens
    # Build inverted index from kmeans.labels_

def plaid_search(self, name, query_tokens, k=10, nprobe=4):
    return json.loads(self._iris_obj().classMethodValue(
        "Graph.KG.PLAIDSearch", "Search", name, json.dumps(query_tokens), k, nprobe))
```
