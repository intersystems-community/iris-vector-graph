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
| **BM25Index** | Pure ObjectScript Okapi BM25 lexical search — `^BM25Idx` globals, zero SQL tables. Automatic `kg_TXT` upgrade when `"default"` index exists. Cypher `CALL ivg.bm25.search(name, query, k)`. 0.3ms median search. |
| **VecIndex** | RP-tree ANN vector search — pure ObjectScript + `$vectorop` SIMD. Annoy-style two-means splitting. |
| **IVFFlat** | Inverted File flat vector index — Python k-means build (sklearn), pure ObjectScript query. Tunable `nprobe` recall/speed tradeoff. `nprobe=nlist` → exact search. Cypher `CALL ivg.ivf.search(name, vec, k, nprobe)`. |
| **PLAID** | Multi-vector retrieval (ColBERT-style) — centroid scoring → candidate gen → exact MaxSim. Single server-side call. |
| **HNSW** | Native IRIS VECTOR index via `kg_KNN_VEC`. Sub-2ms search. |
| **Edge Embeddings** | Semantic search over graph relationships — `embed_edges()` encodes each `(s, p, o_id)` triple into `kg_EdgeEmbeddings`; `edge_vector_search()` retrieves the most similar edges to a query vector. Snapshot-portable. |
| **Cypher** | openCypher parser/translator — MATCH, WHERE, RETURN, CREATE, UNION, CASE WHEN, variable-length paths, `shortestPath()` / `allShortestPaths()`, CALL subqueries. Bolt 5.4 protocol (TCP + WebSocket) for standard driver connectivity. |
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
```

### Graph Browser + Bolt Connectivity

A built-in Cypher server speaks the Bolt protocol, so standard graph tooling (drivers, visualization, LangChain) works out of the box:

```bash
IRIS_HOST=localhost IRIS_PORT=1972 IRIS_NAMESPACE=USER \
IRIS_USERNAME=_SYSTEM IRIS_PASSWORD=SYS \
python3 -m uvicorn iris_vector_graph.cypher_api:app --port 8000
```

- **Browser** — `http://localhost:8000/browser/` (force-directed graph visualization)
- **Bolt TCP** — `bolt://localhost:7687` (Python/Java/Go/.NET drivers, LangChain, cypher-shell)
- **HTTP API** — `http://localhost:8000/api/cypher` (curl, httpie, REST clients)

---

## Temporal Property Graph

Store and query time-stamped edges — service calls, events, metrics, log entries — with sub-millisecond window queries and O(1) aggregation.

### Two edge APIs: structural vs. temporal

IVG has two distinct edge APIs that write to different storage and support different query patterns:

| | `create_edge` / `bulk_create_edges` | `create_edge_temporal` / `bulk_create_edges_temporal` |
|--|-------------------------------------|-------------------------------------------------------|
| **Writes to** | `Graph_KG.rdf_edges` SQL (durability) + `^KG("out",0,...)` globals (query, synchronous) | `^KG("tout"/"tin")` (time-ordered) + `^KG("out",0,...)` (adjacency) |
| **Query via** | `MATCH (a)-[:R]->(b)` — immediately visible, no `BuildKG()` needed | `get_edges_in_window()`, `get_temporal_aggregate()`, temporal Cypher `WHERE r.ts >= $start`; also visible in `MATCH (a)-[:R]->(b)` |
| **Models** | Structural relationship — "A is connected to B" | Event log — "A called B at time T with weight W" |
| **Example** | `(service:auth)-[:DEPENDS_ON]->(service:payment)` | `(service:auth)-[:CALLS_AT {ts: 1705000042, weight: 38ms}]->(service:payment)` |

**Use `create_edge` when** the relationship is a permanent structural fact: schema dependencies, ontology hierarchies, entity co-occurrences, foreign key relationships.

**Use `create_edge_temporal` when** the relationship is a time-series event: service calls, metric emissions, log events, cost observations, anything you'll query by time window or aggregate over time.

The same node pair can have both: a structural `DEPENDS_ON` edge (created once) and thousands of temporal `CALLS_AT` events (one per call). Both are immediately visible in `MATCH (a)-[r]->(b)` — no rebuild required.

**Deleting an edge:**
```python
engine.delete_edge("service:auth", "DEPENDS_ON", "service:payment")
# removes from rdf_edges SQL and kills ^KG("out",0,...) immediately
```

> **Note — bulk ingest**: `bulk_create_edges` is optimized for high-volume ingest (535M edges validated) and intentionally skips the per-edge `^KG` write for performance. Edges inserted in bulk are visible to `MATCH`/BFS only after calling `BuildKG()` at the end of the ingest session. `bulk_create_edges_temporal` does write `^KG` immediately. `create_edge` (single) always writes immediately.

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

## IVFFlat Vector Index

Inverted File with Flat quantization — Python k-means build, pure ObjectScript query. Tunable `nprobe` recall/speed tradeoff; `nprobe=nlist` gives exact results.

```python
# Build: reads kg_NodeEmbeddings, runs MiniBatchKMeans, stores ^IVF globals
result = engine.ivf_build("kg_idx", nlist=256, metric="cosine")
# {"nlist": 256, "indexed": 10000, "dim": 768}

# Search: finds nprobe nearest centroids, scores their cells
results = engine.ivf_search("kg_idx", query_vector, k=10, nprobe=32)
# [("NCIT:C12345", 0.97), ("NCIT:C67890", 0.94), ...]

# Lifecycle
info = engine.ivf_info("kg_idx")   # {"nlist":256,"dim":768,"indexed":10000,...}
engine.ivf_drop("kg_idx")
```

Cypher:
```cypher
CALL ivg.ivf.search('kg_idx', $query_vec, 10, 32) YIELD node, score
RETURN node, score ORDER BY score DESC
```

Global storage: `^IVF(name, "cfg"|"centroid"|"list")` — independent of `^KG`, `^VecIdx`, `^PLAID`, `^BM25Idx`.

---

## Edge Embeddings

Embed every graph triple as a natural-language sentence and search relationships semantically. Useful for retrieving the edges most similar to a free-text query — e.g., "drug strongly associated with autoimmune disease".

```python
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

engine.embed_edges(
    text_fn=lambda s, p, o: f"{s} {p.replace('_', ' ')} {o}",
    batch_size=500,
)

results = engine.edge_vector_search(
    query_embedding=my_encoder.encode("drug associated with autoimmune disease"),
    top_k=10,
    score_threshold=0.7,
)
for r in results:
    print(r["s"], r["p"], r["o_id"], r["score"])
```

**`embed_edges(model, text_fn, where, batch_size, force, progress_callback) -> dict`**

| Param | Default | Description |
|-------|---------|-------------|
| `text_fn` | `lambda s,p,o: f"{s} {p} {o}"` | Serializes each triple to the string that gets embedded |
| `where` | None | SQL fragment on `(s, p, o_id)` to embed a subset — e.g. `"p = 'associated_with'"` |
| `force` | False | Re-embed edges already in `kg_EdgeEmbeddings` |
| `batch_size` | 500 | Edges per batch; commits after each batch |

Returns `{"embedded": int, "skipped": int, "errors": int, "total": int}`. Restores the original embedder in a `finally` block.

**`edge_vector_search(query_embedding, top_k=10, score_threshold=None) -> list[dict]`**

Returns `[{"s": str, "p": str, "o_id": str, "score": float}, ...]` sorted descending by cosine similarity. The `kg_EdgeEmbeddings` table (`VECTOR(DOUBLE, {dim})`, composite PK on `(s, p, o_id)`) is included in `save_snapshot()` / `restore_snapshot()` — edge embeddings survive a snapshot round-trip without re-embedding.

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

## Weighted Shortest Path (Dijkstra)

Finds the minimum-**cost** path between two nodes using Dijkstra's algorithm. Unlike `shortestPath()` which minimizes hops, this minimizes the sum of edge weights.

Edge weights come from the numeric value stored in `^KG("out",0,s,p,o)` — set automatically when you call `create_edge` or `WriteAdjacency` with a weight parameter.

```python
# Store weighted edges
engine.create_node("svc:auth")
engine.create_node("svc:db")
iris_obj = engine._iris_obj()
iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency",
    "svc:auth", "CALLS", "svc:db", "5.2")  # weight=5.2ms latency

iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency",
    "svc:auth", "CALLS", "svc:cache", "0.3")
iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency",
    "svc:cache", "CALLS", "svc:db", "0.8")
```

```cypher
-- Minimum-latency path (prefers cache hop at cost 1.1 over direct at cost 5.2)
CALL ivg.shortestPath.weighted(
  'svc:auth', 'svc:db',
  'weight',
  9999,
  10
) YIELD path, totalCost
RETURN path, totalCost
```

Returns:
```json
{
  "nodes": ["svc:auth", "svc:cache", "svc:db"],
  "rels":  ["CALLS", "CALLS"],
  "costs": [0.3, 0.8],
  "length": 2,
  "totalCost": 1.1
}
```

**Parameters**: `(from, to, weightProp, maxCost, maxHops)`

| Parameter | Description | Default |
|-----------|-------------|---------|
| `from` | Source node ID (string or `$param`) | required |
| `to` | Target node ID | required |
| `weightProp` | Edge weight property name (currently uses `^KG` value) | `"weight"` |
| `maxCost` | Stop searching if cost exceeds this | `9999` |
| `maxHops` | Maximum path length | `10` |

**YIELD columns**: `path` (JSON with nodes/rels/costs/length/totalCost), `totalCost` (float)

Falls back to unit weight (1.0 per hop = equivalent to BFS) when no weight is stored for an edge.

---

## Cypher

### Temporal edge filtering (v1.42.0+)

```cypher
-- Filter edges by timestamp — routes to ^KG("tout") B-tree, O(results)
MATCH (a)-[r:CALLS_AT]->(b)
WHERE r.ts >= $start AND r.ts <= $end
RETURN r.ts, r.weight
ORDER BY r.ts DESC

-- Temporal + property filter
MATCH (a:Service)-[r:CALLS_AT]->(b)
WHERE r.ts >= $start AND r.ts <= $end
  AND r.weight > 1000
RETURN a.id, b.id, r.ts, r.weight
ORDER BY r.weight DESC

-- Inbound direction — routes to ^KG("tin")
MATCH (b:Service)<-[r:CALLS_AT]-(a)
WHERE r.ts >= $start AND r.ts <= $end
RETURN a.id, b.id, r.ts
```

> **Sweet spot**: Temporal Cypher is designed for trajectory-style queries (≤~50 edges, ordered output). For aggregation over large windows, use `get_temporal_aggregate()` / `get_bucket_groups()` — these are O(1) pre-aggregated and 400× faster.

```cypher
-- Named paths
MATCH p = (a:Service)-[r:CALLS]->(b:Service)
WHERE a.id = 'auth'
RETURN p, length(p), nodes(p), relationships(p)

-- Variable-length paths
MATCH (a:Service)-[:CALLS*1..3]->(b:Service)
WHERE a.id = 'auth'
RETURN b.id

-- Shortest path between two nodes (v1.49.0+)
MATCH p = shortestPath((a {id: $from})-[*..8]-(b {id: $to}))
RETURN p, length(p), nodes(p), relationships(p)

-- All shortest paths — returns every minimum-length path
MATCH p = allShortestPaths((a {id: $from})-[*..8]-(b {id: $to}))
RETURN p

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
| `^BM25Idx` | BM25 lexical search index |

### Schema (Graph_KG)

| Table | Purpose |
|-------|---------|
| `nodes` | Node registry (node_id PK) |
| `rdf_edges` | Edges (s, p, o_id) |
| `rdf_labels` | Node labels (s, label) |
| `rdf_props` | Node properties (s, key, val) |
| `kg_NodeEmbeddings` | HNSW vector index (id, emb VECTOR) |
| `kg_EdgeEmbeddings` | Triple embeddings (s, p, o_id, emb VECTOR) — composite PK |
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
| `Graph.KG.Traversal` | BuildKG, BuildNKG, BFSFastJson, ShortestPathJson |
| `Graph.KG.BulkLoader` | BulkLoad (`INSERT %NOINDEX %NOCHECK` + `%BuildIndices`) |
| `Graph.KG.BM25Index` | Build, Search, Insert, Drop, Info, SearchProc (`kg_BM25` stored procedure) |
| `Graph.KG.IVFIndex` | Build, Search, Drop, Info, SearchProc (`kg_IVF` stored procedure) |
| `Graph.KG.EdgeScan` | MatchEdges (`Graph_KG.MatchEdges` stored procedure), WriteAdjacency, DeleteAdjacency |

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
| BM25Index search (174 nodes, 3-term) | 0.3ms | Pure ObjectScript `$Order` posting-list |
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

### v1.61.0 (2026-04-24)

Three more openCypher gaps closed, verified against the official openCypher grammar:

- feat: `WITH *` — pass-through all bound variables to next stage; fixes `ValueError: Undefined` on any var after `WITH *` (spec 072)
- feat: Multi-pattern `CREATE (a:Gene {id:"x"}), (b:Drug {id:"y"}), (a)-[:BINDS]->(b)` — parser now loops on comma to accept any number of patterns (spec 073)
- feat: Relationship property filter on variable-length paths: `[r*1..3 {weight: 5}]` — parser accepts `{prop:val}` after `*min..max`; properties passed through to BFS execution (spec 074)


### v1.60.0 (2026-04-24)

Four openCypher gaps closed, all from structured gap analysis against the openCypher grammar spec:

- feat: `WHERE n:Label` predicate — `MATCH (n) WHERE n:Gene AND n.id = 'x'` now works; translates to `EXISTS (SELECT 1 FROM rdf_labels WHERE label = ?)` (spec 068)
- feat: Map literal expressions — `RETURN {id: n.id, score: 0.9} AS obj` translates to `JSON_OBJECT(...)` (spec 069)
- feat: `WITH agg-alias HAVING filter` — `WITH n, count(r) AS cnt WHERE cnt > 2` now emits SQL `HAVING cnt > 2` correctly; was `ValueError: Undefined: cnt` (spec 070)
- feat: Subscript/slice/property-access postfix — `list[n]`, `list[start..end]`, `expr.key` on any expression; translates to `JSON_ARRAYGET`, `JSON_ARRAY_SLICE`, `JSON_VALUE` (spec 071)
- fix: `DELETE r` by relationship variable now emits `WHERE (s,p,o_id) IN (SELECT ...)` instead of broken correlated subquery (spec 071)


### v1.59.2 (2026-04-24)
- fix: Cypher `WHERE x IN $param` and `WHERE x IN [list]` now correctly emit `IN (?,?,?)` — previously emitted `IN ?` which IRIS DBAPI can't expand. Enables batch multi-node queries like `MATCH (a)-[r]-(b) WHERE a.id IN $node_ids RETURN ...` (20× speedup for 2-hop expansion vs N sequential queries).


### v1.59.1 (2026-04-21)
- perf: `embed_nodes()` and `embed_edges()` — 4–10x speedup for SentenceTransformer embedders: batch `model.encode(texts_list)` replaces N serial calls; `executemany()` replaces N per-row INSERTs; batch `DELETE WHERE id IN (...)` replaces N individual DELETEs. Estimated 94min → 10–25min for 205K nodes. Falls back gracefully for non-SentenceTransformer embedders and IRIS EMBEDDING() path.


### v1.59.0 (2026-04-21)
- feat: `embed_edges(model, text_fn, where, batch_size, force, progress_callback)` — embed every `(s, p, o_id)` triple into `kg_EdgeEmbeddings(VECTOR(DOUBLE))` (spec 065)
- feat: `edge_vector_search(query_embedding, top_k, score_threshold)` — cosine similarity search over edge embeddings
- feat: `kg_EdgeEmbeddings` added to schema DDL (`CREATE TABLE IF NOT EXISTS`, composite PK), `get_schema_status()` required tables, and snapshot save/restore
- Default text serialization: `"{s} {p} {o_id}"` — caller-overridable via `text_fn`; `force=False` skips already-embedded edges; mirrors `embed_nodes` API exactly


### v1.58.1 (2026-04-20)
- feat: `startNode(r)` and `endNode(r)` functions — return source/target node IDs from a relationship variable
- feat: Property access on function call results — `startNode(r).id`, `endNode(r).name` etc
- fix: `UNWIND relationships(p) AS r RETURN startNode(r).id, endNode(r).id, type(r)` — canonical path unpacking pattern now works


### v1.58.0 (2026-04-20)
- feat: `engine.save_snapshot(path)` — portable `.ivg` ZIP: SQL tables as NDJSON + globals as NDJSON (endian-safe, cross-version) (spec 064)
- feat: `IRISGraphEngine.snapshot_info(path)` — @staticmethod, no connection needed; metadata header with IRIS version, ivg version, has_vector_sql
- feat: `engine.restore_snapshot(path, merge=False)` — destructive or additive restore; UPSERT on merge
- feat: `engine.get_unembedded_nodes()` — find nodes with no embedding after restore
- feat: `embed_fn` and `use_iris_embedding` params on IRISGraphEngine.__init__
- feat: `Graph.KG.Snapshot` ObjectScript class for file I/O helpers
- fix: save_snapshot skips IRIS RowID columns (edge_id etc) — prevents non-insertable column errors on restore
- 5 E2E tests: roundtrip, snapshot_info staticmethod, destructive restore, merge restore, globals BFS after restore


### v1.56.0 (2026-04-19)
- feat: `CALL ivg.shortestPath.weighted(from, to, weightProp, maxCost, maxHops) YIELD path, totalCost` — Dijkstra minimum-cost path in pure ObjectScript
- Uses edge weights from `^KG("out",0,...)` globals (set by create_edge WriteAdjacency)
- Falls back to unit weight 1.0 when weightProp not found
- Supports directed ("out") and undirected ("both") traversal
- 4 E2E tests: prefer lower-cost longer path, no path, same source/target, unit weight fallback


### v1.55.3 (2026-04-19)
- fix: Bug 6 final — SQLCODE -400 on rdf_edges CREATE INDEX now debug-level (ALTER TABLE fallback handles it)
- fix: type(r) now returns edge predicate column (e.p) not node_id
- fix: id(n) now returns actual node_id column
- feat: =~ regex match operator — translates to IRIS %MATCHES
- fix: N-Quads import captures graph URI from quad's 4th element as graph_id


### v1.55.2 (2026-04-19)
- fix: Bug 6 (final) — SQLCODE -400 on rdf_edges index creation now falls back to ALTER TABLE ADD INDEX; all standard indexes created even when Graph.KG.Edge class was never compiled


### v1.55.1 (2026-04-19)
- fix: Graph.KG.Edge/TestEdge persistent classes excluded from ObjectScript deploy (fix DDL table ownership conflict — Bug 6)
- fix: conftest removes conflicting .cls before LoadDir
- fix: apoc.meta.data() samples all nodes per label via JOIN on rdf_labels (no longer skips labels with no first-node properties)


### v1.55.0 (2026-04-19)
- feat: import_rdf/bulk_create_edges/create_edge_temporal/bulk_create_edges_temporal all accept graph= parameter
- feat: USE GRAPH filtering now strict (exact graph_id match, no NULL leakage)
- feat: UNIQUE constraint updated to (s,p,o_id,graph_id) allowing same triple in multiple named graphs
- feat: db.schema.relTypeProperties() returns actual relationship property names
- fix: import_rdf _ensure_node uses WHERE NOT EXISTS (no duplicate key errors)
- fix: import_rdf edge INSERT scoped to graph_id in WHERE NOT EXISTS check
- fix: graph_id column uses %EXACT for case-sensitive storage
- test: 8 E2E tests proving fail-before/pass-after for all 5 FRs (spec 061)


### v1.54.1 (2026-04-18)
- fix: initialize_schema() idempotent — "already has index" suppressed (Bug 1)
- fix: idx_props_val_ifind (iFind) and idx_edges_confidence (JSON_VALUE) now optional — graceful skip on Community (Bugs 2+3)
- test: 6 new E2E schema init tests covering idempotency, required tables, optional indexes, core procedures (spec 060)


### v1.54.0 (2026-04-18)
- fix: materialize_inference respects named graphs — inferred triples use correct graph_id (spec 055)
- fix: materialize_inference/retract_inference accept graph= parameter
- feat: Cypher % (modulo → MOD) and ^ (power → POWER) operators (spec 056)
- feat: FOREACH clause — `FOREACH (x IN list | update_clause)` (spec 057)
- fix: EXISTS { (n)-[r]->(m) } with edge patterns now works; MATCH keyword optional inside EXISTS (spec 058)
- feat: Pattern comprehension `[(a)-[r]->(b) | proj]` collecting edge projections (spec 059)


### v1.53.1 (2026-04-18)
- feat: `engine.materialize_inference(rules="rdfs"|"owl")` — transitive subClassOf/subPropertyOf closure, rdf:type inheritance, domain/range, OWL equivalentClass/inverseOf/TransitiveProperty/SymmetricProperty
- feat: `engine.retract_inference()` — removes all inferred triples, restoring asserted-only graph
- feat: `import_rdf(path, infer="rdfs")` — runs inference automatically after load
- Inferred triples tagged `qualifiers={"inferred":true}` for easy exclusion


### v1.53.0 (2026-04-18)
- feat: Named graphs — `create_edge(graph='name')`, `list_graphs()`, `drop_graph(name)`
- feat: `USE GRAPH 'name' MATCH (a)-[r]->(b)` Cypher syntax adds graph_id filter
- feat: Schema migration — `graph_id` column added to `rdf_edges` (idempotent, run on initialize_schema)


### v1.52.1 (2026-04-18)
- feat: `engine.import_rdf(path)` — load Turtle (.ttl), N-Triples (.nt), N-Quads (.nq) into the graph
- Format auto-detected from extension; streaming batch ingest; blank node synthetic IDs; language tags preserved


### v1.52.0 (2026-04-18)
- feat: `ALL/ANY/NONE/SINGLE(x IN list WHERE ...)` list predicate expressions
- feat: `[x IN list WHERE pred | proj]` list comprehensions
- feat: `reduce(acc = init, x IN list | body)` reduce expressions
- feat: `filter()/extract()` legacy list functions as aliases
- feat: Arithmetic operators `+`, `-`, `*`, `/` in Cypher expressions


### v1.51.1 (2026-04-18)
- feat: `apoc.meta.data()` returns proper schema columns — LangChain `Neo4jGraph()` connects without error
- feat: `apoc.meta.schema()` returns schema summary


### v1.51.0 (2026-04-18)
- feat: `keys(n)` returns node property keys via rdf_props subquery
- feat: `range(start, end)` and `range(start, end, step)` generate integer lists
- feat: `size(list)` uses JSON_ARRAYLENGTH; `head()`, `last()`, `tail()`, `isEmpty()` implemented


### v1.50.3 (2026-04-18)
- Fix: `initialize_schema()` creates `SQLUser.*` views automatically — no more manual DEFAULT_SCHEMA workaround
- Fix: `initialize_schema()` detects pre-compiled ObjectScript classes via `%Dictionary` — fast 0.2ms PPR path activates correctly instead of falling back to 1800ms Python path


### v1.50.2 (2026-04-18)
- Fix: `MATCH (a)-[r]->(b)` with unbound source falls back to `rdf_edges` SQL (avoids IRIS SqlProc 32KB string limit for large graphs with 88K+ edges)
- `MatchEdges` is now only used when source node ID is bound — safe path for single-node traversal


### v1.50.1 (2026-04-18)
- Fix: `bulk_create_edges` now calls `BuildKG()` after batch SQL — bulk-inserted static edges immediately visible to MATCH/BFS
- Fix: `BuildKG()` already uses shard-0 `^KG("out",0,...)` layout (confirmed, no code change needed)


### v1.50.0 (2026-04-18)
- **Unified edge store PR-A** — `MATCH (a)-[r]->(b)` now returns both static and temporal edges (spec 048)
- `Graph.KG.EdgeScan` — `MatchEdges(sourceId, predicate, shard)` SqlProc scans `^KG("out",0,...)` globals
- `create_edge` writes `^KG` synchronously; `delete_edge` (new) kills `^KG` entry synchronously
- Cypher `MATCH (a)-[r]->(b)` routes to `MatchEdges` CTE — no SQL JOIN on rdf_edges
- `TemporalIndex` and all traversal code updated to shard-0 layout
- IVF index fixes: `$vector("double")`, JSON float arrays, leading-zero scores, `VECTOR(DOUBLE)` schema
- Parser: negative float literals in list expressions now work


### v1.49.0 (2026-04-18)
- **`shortestPath()` / `allShortestPaths()` openCypher syntax** — fixes parse error reported by mindwalk (spec 047)
- `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to})) RETURN p` now works end-to-end
- `RETURN p` → JSON `{"nodes":[...],"rels":[...],"length":N}`; `RETURN length(p)`, `nodes(p)`, `relationships(p)` all supported
- `allShortestPaths(...)` returns all minimum-length paths (diamond graphs return both paths)
- `Graph.KG.Traversal.ShortestPathJson` — pure ObjectScript BFS with multi-parent backtracking for all-paths support
- Parser fix: `[*..N]` (dot-dot without leading integer) now parses correctly
- Parser fix: bare `--` undirected relationship pattern now parses correctly
- Translator/engine fix: `CREATE` without RETURN clause no longer throws `UnboundLocalError`

### v1.48.0 (2026-04-18)
- **IVFFlat vector index** — `Graph.KG.IVFIndex` ObjectScript class + `^IVF` globals (spec 046)
- `ivf_build(name, nlist, metric, batch_size)` — Python MiniBatchKMeans build from `kg_NodeEmbeddings`; stores centroids + inverted lists as `$vector` in `^IVF` globals
- `ivf_search(name, query, k, nprobe)` — pure ObjectScript centroid scoring → cell scan → top-k; `nprobe=nlist` gives exact search
- `ivf_drop(name)` / `ivf_info(name)` — lifecycle management
- `Graph_KG.kg_IVF` SQL stored procedure — enables `JSON_TABLE` CTE pattern
- Cypher `CALL ivg.ivf.search(name, query_vec, k, nprobe) YIELD node, score`
- Translator fix: `ORDER BY <alias> DESC` now resolves SELECT-level aliases (e.g. `count(r) AS deg`) without `Undefined` error
- `cypher_api.py`: Bolt TCP/WS sessions use dedicated IRIS connections (`_make_engine`) to prevent connection contention with HTTP handlers; `threading.Lock` on shared engine cache
- `test_bolt_server.py`: fixed 2 `TestBoltSessionHello` tests using deprecated `asyncio.get_event_loop().run_until_complete()` → `asyncio.run()`

### v1.47.0 (2026-04-10)
- **Bolt 5.4 protocol server** — TCP (port 7687) + WebSocket (port 8000). Standard graph drivers (Python, Java, Go, .NET), LangChain, and visualization tools connect via `bolt://`
- **Graph browser** — bundled at `/browser/` with force-directed visualization, schema sidebar, `:sysinfo`
- **Cypher HTTP API** — `/api/cypher` + Bolt-compatible transactional endpoints. API key auth via `X-API-Key`
- **System procedures** — `db.labels()`, `db.relationshipTypes()`, `db.schema.visualization()`, `dbms.queryJmx()`, `SHOW DATABASES/PROCEDURES/FUNCTIONS`
- **Graph object encoding** — `RETURN n, r, m` produces typed Node/Relationship structures for visualization
- **SQL audit** — `FETCH FIRST` → `TOP`, `DISTINCT TOP` order, IN clause chunking at 499
- **Translator fixes** — anonymous nodes, BM25 CTE literals, var-length min-hop, UNION ALL with LIMIT
- **Embedding fixes** — probe false negative, string model loading
- `scripts/load_demo_data.py` — canonical dataset loader (NCIT + HLA immunology + embeddings + BM25)
- 456 tests, 0 skipped

### v1.46.0 (2026-04-07)
- **BM25Index** — pure ObjectScript Okapi BM25 lexical search over `^BM25Idx` globals. Zero SQL tables, no Enterprise license required.
- `Graph.KG.BM25Index.Build(name, propsCSV)` — indexes all graph nodes by specified text properties; returns `{"indexed":N,"avgdl":F,"vocab_size":V}`
- `Graph.KG.BM25Index.Search(name, query, k)` — Robertson BM25 scoring via `$Order` posting-list traversal; returns JSON `[{"id":nodeId,"score":S},...]`
- `Graph.KG.BM25Index.Insert(name, docId, text)` — incremental document add/replace; updates IDF only for new document's terms (O(doc_length))
- `Graph.KG.BM25Index.Drop(name)` — O(1) Kill of full index
- `Graph.KG.BM25Index.Info(name)` — returns `{"N":N,"avgdl":F,"vocab_size":V}` or `{}` if not found
- Python wrappers: `engine.bm25_build()`, `bm25_search()`, `bm25_insert()`, `bm25_drop()`, `bm25_info()`
- `kg_TXT` automatic upgrade: `_kg_TXT_fallback` detects a `"default"` BM25 index and routes through BM25 instead of LIKE-based fallback
- Cypher `CALL ivg.bm25.search(name, $query, k) YIELD node, score` — Stage CTE using `Graph_KG.kg_BM25` SQL stored procedure
- Translator fix: `BM25` and `PPR` CTEs now use own column names in RETURN clause (`BM25.node` not `BM25.node_id`)
- SC-002 benchmark: 0.3ms median search on 174-node community IRIS instance

### v1.45.3 (2026-04-04)
- `translate_relationship_pattern`: inline property filters on relationship nodes were silently dropped — `MATCH (t)-[:R]->(c {id: 'x'})` returned all nodes instead of filtering. Fixed by applying `source_node.properties` and `target_node.properties` after JOIN construction.
- `vector_search`: `TO_VECTOR(?, DOUBLE, {dim})` now includes explicit dimension in query cast, resolving type mismatch on IRIS 2025.1 when column dimension is known
- 2 regression tests added (375 unit tests total)

### v1.45.2 (2026-04-03)
- `embedded.py`: auto-fixes `sys.path` shadowing — ensures `/usr/irissys/lib/python` is first so the embedded `iris` module takes priority over pip-installed `intersystems_irispython`
- `embedded.py`: clear error message when shadowed iris (no `iris.sql`) is detected, naming the root cause
- Documented the XD timeout constraint and embed_daemon pattern for long-running ML operations in embedded context
- 3 new tests covering path-fix and shadowing detection

### v1.45.1 (2026-04-03)
- `embed_nodes`: FK-safe delete — DELETE failure on `kg_NodeEmbeddings` (spurious FK error in embedded Python context) is silently ignored; INSERT proceeds correctly
- `vector_search`: uses `VECTOR_COSINE(TO_VECTOR(col), ...)` so it works on both native VECTOR columns AND VARCHAR-stored vectors (e.g. DocChunk.VectorChunk from fhir-017)

### v1.45.0 (2026-04-03)
- `embed_nodes(model, where, text_fn, batch_size, force, progress_callback)` — incremental node embedding over `Graph_KG.nodes` with SQL WHERE filter, custom text builder, and per-call model override. Unblocks mixed-ontology graphs (embed only KG8 nodes without re-embedding NCIT's 200K nodes).
- `vector_search(table, vector_col, query_embedding, top_k, id_col, return_cols, score_threshold)` — search any IRIS VECTOR column, not just `kg_NodeEmbeddings`. Works on DocChunk tables, RAG corpora, custom HNSW indexes.
- `multi_vector_search(sources, query_embedding, top_k, fusion='rrf')` — unified search across multiple IRIS VECTOR tables with RRF fusion. Returns `source_table` per result. Powers hybrid KG+FHIR document search.
- `validate_vector_table(table, vector_col)` — returns `{dimension, row_count}` for any IRIS VECTOR column.

### v1.44.0 (2026-04-03)
- **SQL Table Bridge** — map existing IRIS SQL tables as virtual graph nodes/edges with zero data copy
- `engine.map_sql_table(table, id_column, label)` — register any IRIS table as a Cypher-queryable node set; no ETL, no data movement
- `engine.map_sql_relationship(source, predicate, target, target_fk=None, via_table=None)` — FK and M:M join relationships traversable via Cypher
- `engine.attach_embeddings_to_table(label, text_columns, force=False)` — overlay HNSW vector search on existing table rows
- `engine.list_table_mappings()`, `remove_table_mapping()`, `reload_table_mappings()` — mapping lifecycle management
- Cypher `MATCH (n:MappedLabel)` routes to registered SQL table with WHERE pushdown — O(SQL query), not O(copy)
- Mixed queries: `MATCH (p:MappedPatient)-[:HAS_DOC]->(d:NativeDocument)` spans both mapped and native nodes seamlessly
- SQL mapping wins over native `Graph_KG.nodes` rows for the same label (FR-016)
- `TableNotMappedError` raised with helpful message when `attach_embeddings_to_table` is called on unregistered label

### v1.43.0 (2026-04-03)
- `EmbeddedConnection` and `EmbeddedCursor` now importable directly from `iris_vector_graph` (top-level)
- `IRISGraphEngine(iris.sql)` — accepts `iris.sql` module directly; auto-wraps in `EmbeddedConnection` (no manual wrapper needed inside IRIS Language=python methods)
- `load_obo(encoding=, encoding_errors='replace')` — handles UTF-8 BOM and Latin-1 bytes from IRIS-written files; fixes NCIT.obo loading edge case
- `load_obo` / `load_networkx` accept `progress_callback=lambda n_nodes, n_edges: ...` — called every 10K items; enables progress reporting for large ontologies (NCIT.obo: 200K+ concepts)
- Verified: temporal Cypher (`WHERE r.ts >= $start AND r.ts <= $end`) works end-to-end via `EmbeddedConnection` path

### v1.42.0 (2026-04-03)
- Cypher temporal edge filtering: `WHERE r.ts >= $start AND r.ts <= $end` routes MATCH patterns to `^KG("tout")` B-tree — O(results), not O(total edges)
- `r.ts` and `r.weight` accessible in RETURN and ORDER BY on temporal edges
- Inbound direction `(b)<-[r:P]-(a) WHERE r.ts >= $start` routes to `^KG("tin")`
- `r.ts` without WHERE filter → NULL + query-level warning (prevents accidental full scans)
- `r.weight > expr` in WHERE applies as post-filter on temporal result set
- Uses IRIS-compatible derived table subquery (not WITH CTE) — works on protocol 65 xDBC
- `w` → `weight` canonical field name in temporal CTE (consistent with v1.41.0 API aliases)
- Sweet spot: trajectory queries ≤50 edges. For aggregation, use `get_temporal_aggregate()`.

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
