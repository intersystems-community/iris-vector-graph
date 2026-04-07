# Architecture

## Overview

iris-vector-graph is a knowledge graph engine built on InterSystems IRIS. All data lives in IRIS globals and SQL tables. All graph analytics and search run as pure ObjectScript with `$vectorop` SIMD. Python provides the API layer and build-time tooling (K-means for PLAID).

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
├──────────────────────────────────────────────────────────────────┤
│  Python SDK            │  ObjectScript Direct  │  Cypher         │
│  (IRISGraphEngine)     │  (classMethodValue)   │  (translator)   │
├──────────────────────────────────────────────────────────────────┤
│                      Execution Layer                             │
├──────────────────────────────────────────────────────────────────┤
│  BM25Index.cls   │  VecIndex.cls    │  PLAIDSearch.cls           │
│  (BM25 lexical)  │  (RP-tree ANN)   │  (multi-vector)            │
│                  │                  │                             │
│  PageRank.cls    │  Algorithms.cls  │  Subgraph.cls              │
│  Traversal.cls   │  GraphIndex.cls  │  Cypher translator         │
│  (BFS/^KG build) │  (^NKG int idx)  │  (parser → SQL)            │
├──────────────────────────────────────────────────────────────────┤
│                       Storage Layer                              │
├──────────────────────────────────────────────────────────────────┤
│  ^KG         │  ^BM25Idx    │  ^VecIdx     │  ^PLAID   │  ^NKG  │
│  (graph)     │  (BM25 idx)  │  (RP-tree)   │  (PLAID)  │  (int) │
│              │              │              │           │         │
│  Graph_KG.*  │              │  HNSW VECTOR │  fhir_    │         │
│  (SQL tables) │             │  (SQL index) │  bridges  │         │
├──────────────────────────────────────────────────────────────────┤
│                  InterSystems IRIS 2024.1+                       │
└──────────────────────────────────────────────────────────────────┘
```

## Global Structures

### ^KG — Knowledge Graph

```
^KG("out", source, predicate, target) = weight
^KG("in", target, predicate, source) = weight
^KG("tout", ts, source, predicate, target) = weight   — temporal outbound
^KG("tin",  ts, target, predicate, source) = weight   — temporal inbound
^KG("bucket", bucket_key, source) = count             — pre-aggregated 5-min bucket
^KG("tagg", bucket, source, predicate, key) = value   — COUNT/SUM/AVG/MIN/MAX/HLL
^KG("edgeprop", ts, s, p, o, key) = value             — rich edge attributes
```

Used by: PageRank, WCC, CDLP, PPR, Subgraph, BFS, TemporalIndex.

### ^BM25Idx — BM25 Lexical Search

```
^BM25Idx(name, "cfg", "N")           — integer: document count
^BM25Idx(name, "cfg", "avgdl")       — float: average document length
^BM25Idx(name, "cfg", "k1")          — float: BM25 k1 parameter
^BM25Idx(name, "cfg", "b")           — float: BM25 b parameter
^BM25Idx(name, "cfg", "vocab_size")  — integer: distinct token count
^BM25Idx(name, "idf",  term)         — float: Robertson IDF
^BM25Idx(name, "tf",   term, docId)  — integer: term frequency  ← term-first!
^BM25Idx(name, "len",  docId)        — integer: document token count
```

Term-first `"tf"` subscript order enables O(postings) posting-list traversal via `$Order(^BM25Idx(name,"tf",term,""))`.

### ^NKG — Integer-Encoded Graph (Arno Acceleration)

```
^NKG("$NI", stringId) = integerIdx       — node string→int
^NKG("$ND", integerIdx) = stringId       — node int→string
^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight — out-edges
^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight — in-edges
^NKG(-3, sIdx) = degree
^NKG("$meta", "nodeCount"|"edgeCount"|"version") = value
```

### ^VecIdx — VecIndex RP-Tree

```
^VecIdx(name, "cfg", "dim"|"metric"|"numTrees"|"leafSize") = config
^VecIdx(name, "vec", docId) = $vector
^VecIdx(name, "tree", treeId, nodeId, "plane") = $vector
^VecIdx(name, "tree", treeId, nodeId, "leaf", docId) = ""
^VecIdx(name, "meta", "count") = N
```

### ^PLAID — Multi-Vector Retrieval

```
^PLAID(name, "centroid", k) = $vector
^PLAID(name, "docPacked", docId) = $ListBuild   — packed token $vectors
^PLAID(name, "docCentroid", centroidId, docId) = ""
^PLAID(name, "meta", "nCentroids"|"nDocs"|"dim"|"totalTokens") = value
```

## ObjectScript Classes

All classes in `Graph.KG` package. Pure ObjectScript + `$vectorop` — no `Language = python`.

| Class | Purpose | Key Methods |
|-------|---------|-------------|
| **BM25Index** | Okapi BM25 lexical search | Build, Search, Insert, Drop, Info, SearchProc (`kg_BM25`) |
| **VecIndex** | RP-tree ANN vector search | Create, Search, SearchJSON, SearchMultiJSON, InsertJSON, InsertBatchJSON, Build, Drop |
| **PLAIDSearch** | PLAID multi-vector retrieval | StoreCentroids, StoreDocTokens, BuildInvertedIndex, Search, Insert, Info, Drop |
| **TemporalIndex** | Time-indexed edge store | InsertEdge, BulkInsert, QueryWindow, QueryWindowInbound, GetAggregate, GetBucketGroups, GetDistinctCount, PurgeBefore |
| **PageRank** | Personalized + Global PageRank | RunJson, PageRankGlobalJson |
| **Algorithms** | Graph analytics | WCCJson, CDLPJson |
| **Subgraph** | Bounded subgraph extraction | SubgraphJson, PPRGuidedJson |
| **Traversal** | Graph build + BFS | BuildKG, BuildNKG, BFSFastJson |
| **GraphIndex** | Functional index for ^NKG | InternNode, InternLabel, InsertIndex, DeleteIndex |

### Call Context Rule

Methods callable via `classMethodValue()` (native API bridge from Python) MUST be pure ObjectScript. `Language = python` methods using `iris.gref()` only work inside IRIS embedded Python contexts. All IVG ObjectScript classes follow this rule.

## SQL Schema (Graph_KG)

```sql
Graph_KG.nodes          (node_id VARCHAR(256) PK)
Graph_KG.rdf_labels     (s, label — composite PK)
Graph_KG.rdf_props      (s, "key", val — composite PK)
Graph_KG.rdf_edges      (edge_id BIGINT IDENTITY PK, s, p, o_id)
Graph_KG.kg_NodeEmbeddings  (id, emb VECTOR(DOUBLE, 768) — HNSW index)
Graph_KG.fhir_bridges   (fhir_code, kg_node_id — composite PK, bridge_type, confidence)
```

No SQL table is created for BM25 — all state is in `^BM25Idx` globals.

## Cypher Translation

The Cypher parser is a hand-written recursive-descent parser that translates openCypher to IRIS SQL:

- Patterns → JOINs on `rdf_edges`/`rdf_labels`/`nodes`
- Named paths → JSON concatenation
- CALL subqueries → CTEs (independent) or scalar subqueries (correlated)
- `ivg` procedures → Stage CTEs via SQL stored procedures

### Supported `ivg` procedures

| Procedure | SQL Stored Proc | YIELD |
|-----------|----------------|-------|
| `ivg.vector.search` | `Graph_KG.kg_KNN_VEC` | `node, score` |
| `ivg.neighbors` | `Graph_KG.kg_NEIGHBORS` | `neighbor` |
| `ivg.ppr` | `Graph_KG.kg_PPR` | `node, score` |
| `ivg.bm25.search` | `Graph_KG.kg_BM25` | `node, score` |

Note: IRIS xDBC protocol 65 does not support `?` params inside `WITH ... AS (...)` CTE bodies. Temporal Cypher uses derived table subqueries instead.
