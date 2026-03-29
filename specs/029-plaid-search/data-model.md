# Data Model: PLAID Multi-Vector Retrieval

**Feature**: 029-plaid-search | **Date**: 2026-03-29

## ^PLAID Global Structure

```
^PLAID(name, "centroid", k)                    = $vector(dim, "double")
^PLAID(name, "docCentroid", centroidId, docId) = ""
^PLAID(name, "docTokens", docId, tokPos)       = $vector(dim, "double")
^PLAID(name, "meta", "nCentroids")             = K (integer)
^PLAID(name, "meta", "nDocs")                  = N (integer)
^PLAID(name, "meta", "dim")                    = dim (integer)
^PLAID(name, "meta", "totalTokens")            = M (integer)
```

**Access patterns**:
- Centroid read: `$Get(^PLAID(name, "centroid", k))` — 0.005ms, used K×nQ times in Stage 1
- Inverted index scan: `$Order(^PLAID(name, "docCentroid", centroidId, docId))` — 0.001ms/step, used in Stage 1.5
- Token read: `$Order(^PLAID(name, "docTokens", docId, tokPos))` — 0.005ms, used nQ×nTokens times in Stage 2

## Process-Private Globals (Query Intermediates)

```
^||centroidScore(k)      — accumulated centroid scores across query tokens
^||candidates(docId)     — candidate documents from top centroids
^||ranked(-score, docId) — MaxSim-ranked results for TopK extraction
```

Killed at start of each Search() call.

## No SQL Tables

PLAID does not use SQL tables. All storage is in `^PLAID` globals. No schema changes to `Graph_KG`.

## Relationships

- `^PLAID` is independent of `^KG`, `^VecIdx`, `^NKG`
- Documents in `^PLAID("docTokens")` may correspond to nodes in `^KG` but there is no foreign key relationship
- The inverted index `^PLAID("docCentroid")` is derived from K-means assignments — rebuild regenerates it
