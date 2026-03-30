# Architecture

## Overview

iris-vector-graph is a knowledge graph engine built on InterSystems IRIS. All data lives in IRIS globals and SQL tables. All graph analytics run as pure ObjectScript with `$vectorop` SIMD. Python provides the API layer and build-time tooling (K-means for PLAID).

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Client Layer                            │
├──────────────────────────────────────────────────────────────┤
│  Python SDK           │  ObjectScript Direct  │  Cypher      │
│  (IRISGraphEngine)    │  (classMethodValue)   │  (translate) │
├──────────────────────────────────────────────────────────────┤
│                    Execution Layer                            │
├──────────────────────────────────────────────────────────────┤
│  VecIndex.cls    │  PLAIDSearch.cls  │  PageRank.cls         │
│  (RP-tree ANN)   │  (multi-vector)   │  Algorithms.cls       │
│                  │                    │  Subgraph.cls         │
│  Traversal.cls   │  GraphIndex.cls   │  Cypher translator    │
│  (BFS/^KG build) │  (^NKG int index) │  (parser → SQL)       │
├──────────────────────────────────────────────────────────────┤
│                     Storage Layer                             │
├──────────────────────────────────────────────────────────────┤
│  ^KG          │  ^VecIdx       │  ^PLAID       │  ^NKG       │
│  (graph)      │  (RP-tree)     │  (centroids)  │  (int index)│
│               │                │               │             │
│  Graph_KG.*   │  HNSW VECTOR   │  fhir_bridges │             │
│  (SQL tables) │  (SQL index)   │  (SQL table)  │             │
├──────────────────────────────────────────────────────────────┤
│                InterSystems IRIS 2024.1+                      │
└──────────────────────────────────────────────────────────────┘
```

## Global Structures

### ^KG — Knowledge Graph

```
^KG("out", source, predicate, target) = weight
^KG("in", target, predicate, source) = weight
^KG("deg", node) = degree_count
^KG("degp", node, predicate) = predicate_degree
^KG("label", label, node) = ""
^KG("prop", node, key) = value
```

Used by: PageRank, WCC, CDLP, PPR, Subgraph, BFS. Built from SQL tables by `Traversal.BuildKG()`.

### ^NKG — Integer-Encoded Graph (Arno Acceleration)

```
^NKG("$NI", stringId) = integerIdx       — node string→int
^NKG("$ND", integerIdx) = stringId       — node int→string
^NKG("$LI", label) = labelIdx            — label string→int
^NKG("$LS", labelIdx) = label            — label int→string (0=out, 1=in, 2=deg)
^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight — out-edges
^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight — in-edges
^NKG(-3, sIdx) = degree
^NKG("$meta", "nodeCount"|"edgeCount"|"version") = value
```

Built by `Traversal.BuildNKG()`. Populated automatically by `GraphIndex.InsertIndex()` on edge writes.

### ^VecIdx — VecIndex RP-Tree

```
^VecIdx(name, "cfg", "dim"|"metric"|"numTrees"|"leafSize") = config
^VecIdx(name, "vec", docId) = $vector                        — stored vectors
^VecIdx(name, "tree", treeId, nodeId, "plane") = $vector     — split hyperplane
^VecIdx(name, "tree", treeId, nodeId, "leaf", docId) = ""    — leaf membership
^VecIdx(name, "meta", "count") = N
```

Uses Annoy-style two-means splitting (data-adaptive, not random hyperplane).

### ^PLAID — Multi-Vector Retrieval

```
^PLAID(name, "centroid", k) = $vector          — K-means cluster center
^PLAID(name, "docPacked", docId) = $ListBuild  — packed token $vectors (1 node per doc)
^PLAID(name, "docNTok", docId) = count
^PLAID(name, "docCentroid", centroidId, docId) = ""  — inverted index
^PLAID(name, "meta", "nCentroids"|"nDocs"|"dim"|"totalTokens") = value
```

Packed storage: all tokens for a document in one `$ListBuild` of `$vector` values. Stage 2 MaxSim reads 1 global node per candidate document instead of N.

## ObjectScript Classes

All classes in `Graph.KG` package. Pure ObjectScript + `$vectorop` — no `Language = python`, no `iris.gref`.

| Class | Purpose | Key Methods |
|-------|---------|-------------|
| **VecIndex** | RP-tree ANN vector search | Create, Search, SearchJSON, SearchMultiJSON, InsertJSON, InsertBatchJSON, Build, Drop |
| **PLAIDSearch** | PLAID multi-vector retrieval | StoreCentroids, StoreDocTokens, BuildInvertedIndex, Search, Insert, Info, Drop |
| **PageRank** | Personalized + Global PageRank | RunJson (PPR), PageRankGlobalJson |
| **Algorithms** | Graph analytics | WCCJson, CDLPJson |
| **Subgraph** | Bounded subgraph extraction | SubgraphJson, PPRGuidedJson |
| **Traversal** | Graph build + BFS | BuildKG, BuildNKG, BFSFastJson |
| **GraphIndex** | Functional index for ^NKG | InternNode, InternLabel, InsertIndex, DeleteIndex |
| **BenchSeeder** | Benchmark graph generation | SeedRandom, SeedFromStaging |

### Call Context Rule

Methods callable via `classMethodValue()` (native API bridge from Python) MUST be pure ObjectScript. `Language = python` methods using `iris.gref()` only work inside IRIS embedded Python contexts. All IVG ObjectScript classes follow this rule.

## SQL Schema (Graph_KG)

```sql
Graph_KG.nodes (node_id VARCHAR(256) PK)
Graph_KG.rdf_labels (s, label — composite PK)
Graph_KG.rdf_props (s, "key", val — composite PK)
Graph_KG.rdf_edges (edge_id BIGINT IDENTITY PK, s, p, o_id)
Graph_KG.kg_NodeEmbeddings (id, label, property_name, emb VECTOR(DOUBLE, 768))
Graph_KG.fhir_bridges (fhir_code %EXACT, kg_node_id %EXACT — composite PK, bridge_type, confidence, source_cui)
```

## Cypher Translation

The Cypher parser is a hand-written recursive-descent parser that translates openCypher to IRIS SQL:

- Patterns → JOINs on `rdf_edges`/`rdf_labels`/`nodes`
- Named paths → JSON concatenation (`'{"nodes":' || JSON_ARRAY(...) || ...`)
- CALL subqueries → CTEs (independent) or scalar subqueries (correlated)
- Procedures → `ivg.vector.search`, `ivg.neighbors`, `ivg.ppr`

Note: Uses string concatenation instead of `JSON_OBJECT()` due to IRIS `%QPAR` bug (DP-399447).
