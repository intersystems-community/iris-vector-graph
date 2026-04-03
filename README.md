# iris-vector-graph

**Knowledge graph engine for InterSystems IRIS** — temporal property graph, vector search, openCypher, graph analytics, and pre-aggregated analytics.

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

Pure ObjectScript — VecIndex, PLAIDSearch, PageRank, Subgraph, GraphIndex, TemporalIndex. No Python. Works on any IRIS 2024.1+, all license tiers.

---

## What It Does

| Capability | Description |
|-----------|-------------|
| **Temporal Graph** | Bidirectional time-indexed edges — `^KG("tout"/"tin"/"bucket")`. O(results) window queries via B-tree traversal. 134K+ edges/sec ingest (RE2-TT benchmark). |
| **Pre-aggregated Analytics** | `^KG("tagg")` per-bucket COUNT/SUM/AVG/MIN/MAX and HLL COUNT DISTINCT. O(1) aggregation queries — 0.085ms for 1-bucket, 0.24ms for 24-hour window. |
| **VecIndex** | RP-tree ANN vector search — pure ObjectScript + `$vectorop` SIMD. Annoy-style two-means splitting. |
| **PLAID** | Multi-vector retrieval (ColBERT-style) — centroid scoring → candidate gen → exact MaxSim. Single server-side call. |
| **HNSW** | Native IRIS VECTOR index via `kg_KNN_VEC`. Sub-2ms search. |
| **Cypher** | openCypher parser/translator — MATCH, WHERE, RETURN, CREATE, UNION, CASE WHEN, variable-length paths, CALL subqueries. |
| **Graph Analytics** | PageRank, WCC, CDLP, PPR-guided subgraph — pure ObjectScript over `^KG` globals. |
| **FHIR Bridge** | ICD-10→MeSH mapping via UMLS for clinical-to-KG integration. |
| **GraphQL** | Auto-generated schema from knowledge graph labels. |
| **Embedded Python** | `EmbeddedConnection` — zero-boilerplate dbapi2 adapter for IRIS Language=python methods. |

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

### Inside IRIS (Language=python, no connection needed)

```python
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(EmbeddedConnection())
engine.initialize_schema()
# Same API as external callers — use from any ObjectScript Language=python method
```

---

## Temporal Property Graph

Store and query time-stamped edges — service calls, events, metrics, log entries — with sub-millisecond window queries and O(1) aggregation.

### Ingest

```python
import time

# Single edge
engine.create_edge_temporal(
    source="service:auth",
    predicate="CALLS_AT",
    target="service:payment",
    timestamp=int(time.time()),
    weight=42.7,            # latency_ms, metric value, or 1.0
)

# Bulk ingest — 134K+ edges/sec (RE2-TT benchmark, 535M edges validated)
edges = [
    {"s": "service:auth",    "p": "CALLS_AT",       "o": "service:payment", "ts": 1712000000, "w": 42.7},
    {"s": "service:payment", "p": "CALLS_AT",       "o": "db:postgres",     "ts": 1712000001, "w": 8.1},
    {"s": "service:auth",    "p": "EMITS_METRIC_AT","o": "metric:cpu",      "ts": 1712000000, "w": 73.2},
]
engine.bulk_create_edges_temporal(edges)
```

### Window Queries

```python
now = int(time.time())

# All calls from auth in the last 5 minutes
edges = engine.get_edges_in_window(
    source="service:auth",
    predicate="CALLS_AT",
    start=now - 300,
    end=now,
)
# [{"s": "service:auth", "p": "CALLS_AT", "o": "service:payment", "ts": 1712000042, "w": 38.2}, ...]

# Edge velocity — call count in last N seconds (reads pre-aggregated bucket, O(1))
velocity = engine.get_edge_velocity("service:auth", window_seconds=300)
# 847

# Burst detection — which nodes exceeded threshold in last N seconds
bursts = engine.find_burst_nodes(predicate="CALLS_AT", window_seconds=60, threshold=500)
# [{"id": "service:auth", "velocity": 1243}, {"id": "service:checkout", "velocity": 731}]
```

### Pre-aggregated Analytics (O(1) per bucket)

```python
now = int(time.time())

# Average latency for auth→payment calls in the last 5 minutes
avg_latency = engine.get_temporal_aggregate(
    source="service:auth",
    predicate="CALLS_AT",
    metric="avg",           # "count" | "sum" | "avg" | "min" | "max"
    ts_start=now - 300,
    ts_end=now,
)
# 41.3  (float, milliseconds)

# All metrics for count, and extremes
count = engine.get_temporal_aggregate("service:auth", "CALLS_AT", "count", now-300, now)
p_min = engine.get_temporal_aggregate("service:auth", "CALLS_AT", "min", now-300, now)
p_max = engine.get_temporal_aggregate("service:auth", "CALLS_AT", "max", now-300, now)

# GROUP BY source — all services, CALLS_AT, last 5 minutes
groups = engine.get_bucket_groups(predicate="CALLS_AT", ts_start=now-300, ts_end=now)
# [
#   {"source": "service:auth",     "predicate": "CALLS_AT", "count": 847, "avg": 41.3, "min": 2.1, "max": 312.0},
#   {"source": "service:checkout", "predicate": "CALLS_AT", "count": 312, "avg": 28.7, "min": 1.4, "max": 189.0},
#   ...
# ]

# COUNT DISTINCT targets — fanout detection (16-register HLL, ~26% error, good for threshold detection)
distinct_targets = engine.get_distinct_count("service:auth", "CALLS_AT", now-3600, now)
# 14   (distinct services called by auth in last hour)
```

### Rich Edge Properties

```python
# Attach arbitrary attributes to any temporal edge
engine.create_edge_temporal(
    source="service:auth",
    predicate="CALLS_AT",
    target="service:payment",
    timestamp=1712000000,
    weight=42.7,
    attrs={"trace_id": "abc123", "status": 200, "region": "us-east-1"},
)

# Retrieve attributes
attrs = engine.get_edge_attrs(
    ts=1712000000,
    source="service:auth",
    predicate="CALLS_AT",
    target="service:payment",
)
# {"trace_id": "abc123", "status": 200, "region": "us-east-1"}
```

### NDJSON Import / Export

```python
# Export temporal edges for a time window
engine.export_temporal_edges_ndjson(
    path="traces_2026-04-01.ndjson",
    start=1743465600,
    end=1743552000,
)

# Import — resume an ingest from a file
engine.import_graph_ndjson("traces_2026-04-01.ndjson")
```

### ObjectScript Direct

```objectscript
// Ingest
Do ##class(Graph.KG.TemporalIndex).InsertEdge("svc:auth","CALLS_AT","svc:pay",ts,42.7,"")

// Bulk ingest (JSON array)
Set n = ##class(Graph.KG.TemporalIndex).BulkInsert(edgesJSON)

// Query window — returns JSON array
Set result = ##class(Graph.KG.TemporalIndex).QueryWindow("svc:auth","CALLS_AT",tsStart,tsEnd)

// Pre-aggregated average latency
Set avg = ##class(Graph.KG.TemporalIndex).GetAggregate("svc:auth","CALLS_AT","avg",tsStart,tsEnd)

// GROUP BY source
Set groups = ##class(Graph.KG.TemporalIndex).GetBucketGroups("CALLS_AT",tsStart,tsEnd)

// COUNT DISTINCT targets (HLL)
Set n = ##class(Graph.KG.TemporalIndex).GetDistinctCount("svc:auth","CALLS_AT",tsStart,tsEnd)
```

---

## Vector Search (VecIndex)

```python
engine.vec_create_index("drugs", 384, "cosine")
engine.vec_insert("drugs", "metformin", embedding_vector)
engine.vec_build("drugs")

results = engine.vec_search("drugs", query_vector, k=5)
# [{"id": "metformin", "score": 0.95}, ...]
```

---

## PLAID Multi-Vector Search

```python
# Build: Python K-means + ObjectScript inverted index
engine.plaid_build("colbert_idx", docs)  # docs = [{"id": "x", "tokens": [[f1,...], ...]}, ...]

# Search: single server-side call, pure $vectorop
results = engine.plaid_search("colbert_idx", query_tokens, k=10)
# [{"id": "doc_3", "score": 0.94}, ...]
```

---

## Cypher

```cypher
-- Named paths
MATCH p = (a:Service)-[r:CALLS]->(b:Service)
WHERE a.id = 'auth'
RETURN p, length(p), nodes(p), relationships(p)

-- Variable-length paths
MATCH (a:Service)-[:CALLS*1..3]->(b:Service)
WHERE a.id = 'auth'
RETURN b.id

-- CASE WHEN
MATCH (n:Service)
RETURN n.id,
       CASE WHEN n.calls > 1000 THEN 'high' WHEN n.calls > 100 THEN 'medium' ELSE 'low' END AS load

-- UNION
MATCH (n:ServiceA) RETURN n.id
UNION
MATCH (n:ServiceB) RETURN n.id

-- Vector search in Cypher
CALL ivg.vector.search('Service', 'embedding', [0.1, 0.2, ...], 5) YIELD node, score
RETURN node, score
```

---

## Graph Analytics

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)

# Personalized PageRank
scores = ops.kg_PAGERANK(seed_entities=["service:auth"], damping=0.85)

# K-hop subgraph
subgraph = ops.kg_SUBGRAPH(seed_ids=["service:auth"], k_hops=3)

# PPR-guided subgraph (prevents k^n blowup)
guided = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["service:auth"], top_k=50, max_hops=5)

# Community detection
communities = ops.kg_CDLP()
components  = ops.kg_WCC()
```

---

## FHIR Bridge

```python
# Load ICD-10→MeSH mappings from UMLS MRCONSO
# python scripts/ingest/load_umls_bridges.py --mrconso /path/to/MRCONSO.RRF

anchors = engine.get_kg_anchors(icd_codes=["J18.0", "E11.9"])
# → ["MeSH:D001996", "MeSH:D003924"]  (filtered to nodes in KG)
```

---

## Architecture

### Global Structure

| Global | Purpose |
|--------|---------|
| `^KG("out", s, p, o)` | Knowledge graph — outbound edges |
| `^KG("in", o, p, s)` | Knowledge graph — inbound edges |
| `^KG("tout", ts, s, p, o)` | Temporal index — outbound, ordered by timestamp |
| `^KG("tin", ts, o, p, s)` | Temporal index — inbound, ordered by timestamp |
| `^KG("bucket", bucket, s)` | Pre-aggregated edge count per 5-minute bucket |
| `^KG("tagg", bucket, s, p, key)` | Pre-aggregated COUNT/SUM/MIN/MAX/HLL per bucket |
| `^KG("edgeprop", ts, s, p, o, key)` | Rich edge attributes |
| `^NKG` | Integer-encoded `^KG` for Arno acceleration |
| `^VecIdx` | VecIndex RP-tree ANN |
| `^PLAID` | PLAID multi-vector |

### Schema (Graph_KG)

| Table | Purpose |
|-------|---------|
| `nodes` | Node registry (node_id PK) |
| `rdf_edges` | Edges (s, p, o_id) |
| `rdf_labels` | Node labels (s, label) |
| `rdf_props` | Node properties (s, key, val) |
| `kg_NodeEmbeddings` | HNSW vector index (id, emb VECTOR) |
| `fhir_bridges` | ICD-10→MeSH clinical code mappings |

### ObjectScript Classes

| Class | Key Methods |
|-------|-------------|
| `Graph.KG.TemporalIndex` | InsertEdge, BulkInsert, QueryWindow, GetVelocity, FindBursts, GetAggregate, GetBucketGroups, GetDistinctCount, Purge |
| `Graph.KG.VecIndex` | Create, InsertJSON, Build, SearchJSON, SearchMultiJSON, InsertBatchJSON |
| `Graph.KG.PLAIDSearch` | StoreCentroids, BuildInvertedIndex, Search |
| `Graph.KG.PageRank` | RunJson, PageRankGlobalJson |
| `Graph.KG.Algorithms` | WCCJson, CDLPJson |
| `Graph.KG.Subgraph` | SubgraphJson, PPRGuidedJson |
| `Graph.KG.Traversal` | BuildKG, BuildNKG, BFSFastJson |
| `Graph.KG.BulkLoader` | BulkLoad (`INSERT %NOINDEX %NOCHECK` + `%BuildIndices`) |

---

## Performance

| Operation | Latency | Dataset |
|-----------|---------|---------|
| Temporal edge ingest | 134K edges/sec | RE2-TT 535M edges, Enterprise IRIS |
| Window query (selective) | 0.1ms | O(results), B-tree traversal |
| GetAggregate (1 bucket, 5min) | 0.085ms | 50K-edge dataset |
| GetAggregate (288 buckets, 24hr) | 0.160ms | O(buckets), not O(edges) |
| GetBucketGroups (3 sources, 1hr) | 0.193ms | |
| GetDistinctCount (1 bucket) | 0.101ms | 16-register HLL |
| VecIndex search (1K vecs, 128-dim) | 4ms | RP-tree + `$vectorop` SIMD |
| HNSW search (143K vecs, 768-dim) | 1.7ms | Native IRIS VECTOR index |
| PLAID search (500 docs, 4 tokens) | ~14ms | Centroid scoring + MaxSim |
| PPR (10K nodes) | 62ms | Pure ObjectScript |
| 1-hop neighbors | 0.3ms | `$Order` on `^KG` |

---

## Documentation

- [Python SDK Reference](docs/python/PYTHON_SDK.md)
- [Architecture](docs/architecture/ARCHITECTURE.md)
- [Schema Reference](docs/architecture/ACTUAL_SCHEMA.md)
- [Temporal Graph Full Spec](docs/enhancements/006-temporal-property-graph-full-spec.md)
- [Setup Guide](docs/setup/QUICKSTART.md)
- [Testing Policy](docs/TESTING_POLICY.md)

---

## Changelog

### v1.41.0 (2026-04-03)
- `get_edges_in_window()` now returns `source`/`target`/`predicate`/`timestamp`/`weight` aliases alongside `s`/`o`/`p`/`ts`/`w` — backward compatible
- `get_edges_in_window(direction="in")` — query inbound edges by target node (uses `^KG("tin")`)
- `create_edge_temporal(..., upsert=True)` and `bulk_create_edges_temporal(..., upsert=True)` — skip write if edge already exists at that timestamp
- `purge_before(ts)` — delete all temporal edges older than `ts`, with `^KG("tagg")` and `^KG("bucket")` cleanup
- `Graph.KG.TemporalIndex.PurgeBefore(ts)` and `QueryWindowInbound(target, predicate, ts_start, ts_end)` ObjectScript methods

### v1.40.0 (2026-04-02)
- `iris_vector_graph.embedded.EmbeddedConnection` — dbapi2 adapter for IRIS Language=python methods
- Zero-boilerplate: `IRISGraphEngine(EmbeddedConnection())` works inside IRIS identically to external `iris.connect()`
- `commit()`/`rollback()` are intentional no-ops (IRIS manages transactions in embedded context)
- `START TRANSACTION`/`COMMIT`/`ROLLBACK` via `cursor.execute()` silently dropped (avoids `<COMMAND>` in wgproto jobs)
- `fetchmany()`, `rowcount`, `description` fully implemented

### v1.39.0 (2026-04-01)
- Pre-aggregated temporal analytics: `^KG("tagg")` COUNT/SUM/AVG/MIN/MAX at O(1)
- `GetAggregate`, `GetBucketGroups`, `GetDistinctCount` ObjectScript methods
- `get_temporal_aggregate()`, `get_bucket_groups()`, `get_distinct_count()` Python wrappers
- 16-register HyperLogLog COUNT DISTINCT (SHA1, ~26% error — suitable for fanout threshold detection)
- Benchmark: 134K–157K edges/sec sustained across RE2-TT/RE2-OB/RE1-TT (535M edges total)

### v1.38.0
- Rich edge properties: `^KG("edgeprop", ts, s, p, o, key)` — arbitrary typed attributes per temporal edge
- `get_edge_attrs()`, `create_edge_temporal(attrs={...})`
- NDJSON import/export: `import_graph_ndjson()`, `export_graph_ndjson()`, `export_temporal_edges_ndjson()`

### v1.37.0
- Temporal property graph: `create_edge_temporal()`, `bulk_create_edges_temporal()`
- `get_edges_in_window()`, `get_edge_velocity()`, `find_burst_nodes()`
- `^KG("tout"/"tin"/"bucket")` globals — bidirectional time-indexed edge store
- `Graph.KG.TemporalIndex` ObjectScript class

### v1.35.0
- UNION / UNION ALL in Cypher
- EXISTS {} subquery predicates

### v1.34.0
- Variable-length paths: `MATCH (a)-[:REL*1..5]->(b)` via BFSFastJson bridge

### v1.33.0
- CASE WHEN / THEN / ELSE / END in Cypher RETURN and WHERE

### v1.32.0
- CAST functions: `toInteger()`, `toFloat()`, `toString()`, `toBoolean()`

### v1.31.0
- RDF 1.2 reification API: `reify_edge()`, `get_reifications()`, `delete_reification()`

### v1.30.0
- BulkLoader: `INSERT %NOINDEX %NOCHECK` + `%BuildIndices` — 46K rows/sec SQL ingest
- RDF 1.2 reification schema DDL

### v1.29.0
- OBO ontology ingest: `load_obo()`, `load_networkx()`

### v1.28.0
- Lightweight install — base requires only `intersystems-irispython`
- Optional extras: `[full]`, `[plaid]`, `[dev]`, `[ml]`, `[visualization]`, `[biodata]`

### v1.26.0–v1.27.0
- PLAID multi-vector retrieval — `PLAIDSearch.cls` pure ObjectScript + `$vectorop`
- PLAID packed token storage: 53 `$Order` → 1 `$Get`

### v1.24.0–v1.25.1
- VecIndex nprobe recall fix (counts leaf visits, not branch points)
- Annoy-style two-means tree splitting (fixes degenerate trees)
- Batch APIs: `SearchMultiJSON`, `InsertBatchJSON`

### v1.21.0–v1.22.1
- VecIndex RP-tree ANN
- `SearchJSON`/`InsertJSON` — eliminated xecute path (250ms → 4ms)

### v1.20.0
- Arno acceleration wrappers: `khop()`, `ppr()`, `random_walk()`

### v1.19.0
- `^NKG` integer index for Arno acceleration

### v1.18.0
- FHIR-to-KG bridge: `fhir_bridges` table, `get_kg_anchors()`, UMLS MRCONSO ingest

### v1.17.0
- Cypher named path bindings, CALL subqueries, PPR-guided subgraph

### [Earlier versions →](docs/CHANGELOG_ARCHIVE.md)

---

**License**: MIT | **Author**: Thomas Dyar (thomas.dyar@intersystems.com)
