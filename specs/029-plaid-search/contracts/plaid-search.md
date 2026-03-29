# Contracts: PLAID Multi-Vector Retrieval

**Feature**: 029-plaid-search | **Date**: 2026-03-29

## Contract 1: ObjectScript — PLAIDSearch.StoreCentroids

**Input**: `name: %String`, `centroidsJSON: %String` (JSON array of arrays `[[f1,...,f128], ...]`)
**Behavior**: Parse JSON, store each centroid as `$vector` in `^PLAID(name, "centroid", k)`. Set `^PLAID(name, "meta", "nCentroids")`.

## Contract 2: ObjectScript — PLAIDSearch.StoreDocTokens

**Input**: `name: %String`, `docId: %String`, `tokensJSON: %String` (JSON array of arrays)
**Behavior**: Parse JSON, store each token as `$vector` in `^PLAID(name, "docTokens", docId, tokPos)`. Increment `^PLAID(name, "meta", "totalTokens")` and `"nDocs"`.

## Contract 3: ObjectScript — PLAIDSearch.BuildInvertedIndex

**Input**: `name: %String`, `assignmentsJSON: %String` (JSON array of `{"docId": "x", "tokPos": 0, "centroid": 5}`)
**Behavior**: For each assignment, set `^PLAID(name, "docCentroid", centroidId, docId) = ""`. Deduplicates via global key.

## Contract 4: ObjectScript — PLAIDSearch.Search

**Input**: `name: %String`, `queryTokensJSON: %String` (JSON array of arrays), `k: %Integer = 10`, `nprobe: %Integer = 4`
**Output**: JSON array of `[{"id": "docId", "score": 0.95}, ...]` sorted by MaxSim score descending.
**Behavior**: Three-stage pipeline — all server-side, single call:
1. Score query tokens against all centroids → pick top nprobe
2. Collect candidate docs from top centroids via inverted index
3. Compute exact MaxSim for each candidate → rank and return top-k

## Contract 5: ObjectScript — PLAIDSearch.Insert

**Input**: `name: %String`, `docId: %String`, `tokensJSON: %String`
**Behavior**: Store token embeddings, assign each to nearest existing centroid, update inverted index. No centroid re-training.

## Contract 6: ObjectScript — PLAIDSearch.Info

**Input**: `name: %String`
**Output**: JSON `{"name": "x", "nCentroids": K, "nDocs": N, "dim": 128, "totalTokens": M}`

## Contract 7: ObjectScript — PLAIDSearch.Drop

**Input**: `name: %String`
**Behavior**: `Kill ^PLAID(name)`

## Contract 8: Python — plaid_build()

```python
def plaid_build(self, name: str, docs: list, n_clusters: int = None, dim: int = 128) -> dict:
    # docs = [{"id": "doc1", "tokens": [[f1,...], ...]}, ...]
    # Runs sklearn K-means, stores centroids + tokens + inverted index
    # Returns {"nCentroids": K, "nDocs": N, "totalTokens": M}
```

## Contract 9: Python — plaid_search()

```python
def plaid_search(self, name: str, query_tokens: list, k: int = 10, nprobe: int = 4) -> list:
    # query_tokens = [[f1,...,f128], [f1,...], ...]
    # Single classMethodValue call
    # Returns [{"id": "doc1", "score": 0.95}, ...]
```
