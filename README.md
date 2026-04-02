# iris-vector-graph

**Knowledge graph engine for InterSystems IRIS** — vector search, openCypher, graph analytics, and PLAID multi-vector retrieval.

[![PyPI](https://img.shields.io/pypi/v/iris-vector-graph)](https://pypi.org/project/iris-vector-graph/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![IRIS 2024.1+](https://img.shields.io/badge/IRIS-2024.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Install

```bash
pip install iris-vector-graph              # Core: just intersystems-irispython
pip install iris-vector-graph[full]        # Full: + FastAPI, GraphQL, numpy, networkx
pip install iris-vector-graph[plaid]       # + sklearn for PLAID K-means build
```

### ObjectScript Only (IPM)

```
zpm "install iris-vector-graph-core"
```

Pure ObjectScript — VecIndex, PLAIDSearch, PageRank, Subgraph, GraphIndex. No Python. Works on any IRIS 2024.1+, all license tiers.

---

## What It Does

| Capability | Description |
|-----------|-------------|
| **VecIndex** | RP-tree ANN vector search — pure ObjectScript + `$vectorop` SIMD. Annoy-style two-means splitting. |
| **PLAID** | Multi-vector retrieval (ColBERT-style) — centroid scoring → candidate gen → exact MaxSim. Single server-side call. |
| **HNSW** | Native IRIS VECTOR index via `kg_KNN_VEC`. Sub-2ms search. |
| **Cypher** | openCypher parser/translator — MATCH, WHERE, RETURN, CREATE, DELETE, WITH, named paths, CALL subqueries. |
| **Graph Analytics** | PageRank, WCC, CDLP, PPR-guided subgraph — pure ObjectScript over `^KG` globals. |
| **FHIR Bridge** | ICD-10→MeSH mapping via UMLS for clinical-to-KG integration. |
| **GraphQL** | Auto-generated schema from knowledge graph labels. |

---

## Quick Start

### Python

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname='localhost', port=1972, namespace='USER', username='_SYSTEM', password='SYS')
engine = IRISGraphEngine(conn)
engine.initialize_schema()
```

### Vector Search (VecIndex)

```python
engine.vec_create_index("drugs", 384, "cosine")
engine.vec_insert("drugs", "metformin", embedding_vector)
engine.vec_build("drugs")

results = engine.vec_search("drugs", query_vector, k=5)
# [{"id": "metformin", "score": 0.95}, ...]
```

### PLAID Multi-Vector Search

```python
# Build: Python K-means + ObjectScript inverted index
engine.plaid_build("colbert_idx", docs)  # docs = [{"id": "x", "tokens": [[f1,...], ...]}, ...]

# Search: single server-side call, pure $vectorop
results = engine.plaid_search("colbert_idx", query_tokens, k=10)
# [{"id": "doc_3", "score": 0.94}, ...]
```

### Cypher

```cypher
-- Named paths
MATCH p = (a:Protein)-[r:INTERACTS_WITH]->(b:Protein)
WHERE a.id = 'TP53'
RETURN p, length(p), nodes(p), relationships(p)

-- Subqueries
MATCH (p:Protein)
CALL {
    WITH p
    MATCH (p)-[:INTERACTS_WITH]->(partner)
    RETURN count(partner) AS degree
}
RETURN p.id, degree

-- Vector search in Cypher
CALL ivg.vector.search('Gene', 'embedding', [0.1, 0.2, ...], 5) YIELD node, score
RETURN node, score
```

### Graph Analytics

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)

# Personalized PageRank
scores = ops.kg_PAGERANK(seed_entities=["MeSH:D011014"], damping=0.85)

# Subgraph extraction
subgraph = ops.kg_SUBGRAPH(seed_ids=["TP53", "MDM2"], k_hops=3)

# PPR-guided subgraph (prevents D^k blowup)
guided = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["TP53"], top_k=50, max_hops=5)

# Community detection
communities = ops.kg_CDLP()
components = ops.kg_WCC()
```

### FHIR Bridge

```python
# Load ICD-10→MeSH mappings from UMLS MRCONSO
# python scripts/ingest/load_umls_bridges.py --mrconso /path/to/MRCONSO.RRF

# Query: ICD codes → KG anchors
anchors = engine.get_kg_anchors(icd_codes=["J18.0", "E11.9"])
# → ["MeSH:D001996", "MeSH:D003924"]  (filtered to nodes in KG)
```

### ObjectScript Direct (no Python)

```objectscript
// VecIndex
Do ##class(Graph.KG.VecIndex).Create("myidx", 384, "cosine", 4, 50)
Do ##class(Graph.KG.VecIndex).InsertJSON("myidx", "doc1", "[0.1, 0.2, ...]")
Do ##class(Graph.KG.VecIndex).Build("myidx")
Set results = ##class(Graph.KG.VecIndex).SearchJSON("myidx", "[0.3, ...]", 10, 8)

// PLAID
Set results = ##class(Graph.KG.PLAIDSearch).Search("myidx", queryTokensJSON, 10, 4)

// Graph analytics
Do ##class(Graph.KG.Traversal).BuildKG()
Set ppr = ##class(Graph.KG.PageRank).RunJson(seedsJSON, 0.85, 50)
Set sub = ##class(Graph.KG.Subgraph).SubgraphJson(seedsJSON, 3, "")
```

---

## Architecture

### Global Structure

| Global | Purpose |
|--------|---------|
| `^KG` | Knowledge graph — `("out",s,p,o)`, `("in",o,p,s)`, `("deg",s)`, `("label",label,s)`, `("prop",s,key)` |
| `^NKG` | Integer-encoded `^KG` for Arno acceleration — `(-1,sIdx,-(pIdx+1),oIdx)` |
| `^VecIdx` | VecIndex RP-tree ANN — centroids, tree nodes, leaf vectors |
| `^PLAID` | PLAID multi-vector — centroids, packed doc tokens, inverted index |

### Schema (Graph_KG)

| Table | Purpose |
|-------|---------|
| `nodes` | Node registry (node_id PK) |
| `rdf_edges` | Edges (s, p, o_id) |
| `rdf_labels` | Node labels (s, label) |
| `rdf_props` | Node properties (s, key, val) |
| `kg_NodeEmbeddings` | HNSW vector index (id, emb VECTOR) |
| `fhir_bridges` | ICD-10→MeSH clinical code mappings |

### ObjectScript Classes (iris-vector-graph-core)

| Class | Methods |
|-------|---------|
| `Graph.KG.VecIndex` | Create, Insert, InsertJSON, Build, Search, SearchJSON, SearchMultiJSON, SeededVectorExpand, Drop, Info |
| `Graph.KG.PLAIDSearch` | StoreCentroids, StoreDocTokens, BuildInvertedIndex, Search, Insert, Info, Drop |
| `Graph.KG.PageRank` | RunJson (PPR), PageRankGlobalJson (global) |
| `Graph.KG.Algorithms` | WCCJson, CDLPJson |
| `Graph.KG.Subgraph` | SubgraphJson, PPRGuidedJson |
| `Graph.KG.Traversal` | BuildKG, BuildNKG, BFSFastJson |
| `Graph.KG.GraphIndex` | InternNode, InternLabel, InsertIndex (dual ^KG+^NKG write) |

---

## Performance

| Operation | Latency | Details |
|-----------|---------|---------|
| VecIndex search (1K vecs, 128-dim) | 4ms | RP-tree + `$vectorop` SIMD |
| HNSW search (143K vecs, 768-dim) | 1.7ms | Native IRIS VECTOR index |
| PLAID search (500 docs, 4 query tokens) | ~14ms | Centroid scoring + MaxSim |
| PPR (10K nodes) | 62ms | Pure ObjectScript, early termination |
| 1-hop neighbors | 0.3ms | `$Order` on `^KG` |
| k=2 subgraph (10K nodes) | 1.8ms | BFS over `^KG` |

---

## Documentation

- [Python SDK Reference](docs/python/PYTHON_SDK.md)
- [Architecture](docs/architecture/ARCHITECTURE.md)
- [Schema Reference](docs/architecture/ACTUAL_SCHEMA.md)
- [Setup Guide](docs/setup/QUICKSTART.md)
- [Testing Policy](docs/TESTING_POLICY.md)
- [Enhancement Specs](docs/enhancements/)

---

## Changelog

### v1.39.0 (2026-04-01)
- Pre-aggregated temporal analytics: `^KG("tagg", bucket, source, predicate, key)` for O(1) COUNT/AVG/MIN/MAX
- New ObjectScript methods: `GetAggregate`, `GetBucketGroups`, `GetDistinctCount` (16-register HLL)
- New Python wrappers: `get_temporal_aggregate()`, `get_bucket_groups()`, `get_distinct_count()`
- HLL: 16-register HyperLogLog for COUNT DISTINCT using SHA1 hash, ~26% error (documented)
- MIN/MAX atomicity limitation documented (Phase 2: LOCK-based fix)
- `Purge()` extended to kill `^KG("tagg")` subscripts
- 7 unit tests + 6 E2E tests; 300 total tests passing

### v1.28.0 (2026-03-29)
- Lightweight default install — base requires only `intersystems-irispython`
- Optional extras: `[full]`, `[plaid]`, `[dev]`, `[ml]`, `[visualization]`, `[biodata]`
- IPM packages: `iris-vector-graph-core` (ObjectScript only) + `iris-vector-graph` (full)

### v1.27.0
- PLAID packed token storage — `$ListBuild` of `$vector` per document (53 `$Order` → 1 `$Get`)

### v1.26.0
- PLAID multi-vector retrieval — `PLAIDSearch.cls` pure ObjectScript + `$vectorop`
- Python wrappers: `plaid_build`, `plaid_search`, `plaid_insert`, `plaid_info`, `plaid_drop`

### v1.25.1
- VecIndex Annoy-style two-means tree splitting (fixes degenerate trees on clustered embeddings)

### v1.24.0
- VecIndex nprobe recall fix (counts leaf visits, not branch points)
- `SearchMultiJSON`, `InsertBatchJSON` batch APIs

### v1.22.0
- VecIndex `SearchJSON`/`InsertJSON` — eliminated xecute path (250ms → 4ms)

### v1.21.0
- VecIndex RP-tree ANN — `vec_create_index`, `vec_insert`, `vec_build`, `vec_search`

### v1.20.0
- Arno acceleration wrappers: `khop()`, `ppr()`, `random_walk()` with auto-detection

### v1.19.0
- `^NKG` integer index for Arno acceleration
- `GraphIndex.cls`, `BenchSeeder.SeedRandom()`

### v1.18.0
- FHIR-to-KG bridge: `fhir_bridges` table, `get_kg_anchors()`, UMLS MRCONSO ingest

### v1.17.0
- Cypher named path bindings (`MATCH p = ... RETURN p, length(p), nodes(p), relationships(p)`)
- Cypher CALL subqueries (independent CTE + correlated scalar)
- `kg_PPR_GUIDED_SUBGRAPH` with ObjectScript fast path
- Repo cleanup: 80+ stale files removed

### [Earlier versions →](docs/CHANGELOG_ARCHIVE.md)

---

**License**: MIT | **Author**: Thomas Dyar (thomas.dyar@intersystems.com)
