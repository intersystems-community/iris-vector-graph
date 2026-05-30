# iris-vector-graph

**Knowledge graph engine for InterSystems IRIS** — temporal property graph, vector search, openCypher, graph analytics, and pre-aggregated analytics.

[![PyPI](https://img.shields.io/pypi/v/iris-vector-graph)](https://pypi.org/project/iris-vector-graph/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![IRIS 2024.1+](https://img.shields.io/badge/IRIS-2024.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Getting Started

**5 minutes from zero to running graph queries.**

### 1. Start IRIS

```bash
docker compose up -d
```

This starts IRIS Community Edition on `localhost:1972`. No license required. Default credentials: `_SYSTEM` / `SYS`.

Management Portal: http://localhost:52773/csp/sys/UtilHome.csp

### 2. Install the library

```bash
pip install iris-vector-graph
```

### 3. Run your first query

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
engine.create_node("bob",   labels=["Person"], properties={"name": "Bob"})
engine.create_edge("alice", "KNOWS", "bob")

result = engine.execute_cypher(
    "MATCH (a {node_id:$id})-[:KNOWS]->(b) RETURN b.name AS name",
    {"id": "alice"}
)
print(result["rows"])  # [('Bob',)]
```

> **Note:** On IRIS Community Edition, `initialize_schema()` prints some compile
> warnings. These are safe to ignore:
> - `Graph.KG.MCPService` / `Graph.KG.MCPToolSet` — Enterprise-only MCP classes, not needed
> - `Graph.KG.Meta` / `User.PageRankEmbedded` — optional classes, engine works without them
> - `Graph.KG.Edge` "Table name not unique" — schema already deployed, idempotent

**That's it.**

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
| **Cypher** | openCypher parser/translator — **100% TCK compliant on IRIS 2026.1+** (133/133 tests). MATCH, WHERE, RETURN, CREATE, UNION, CASE WHEN, CALL subqueries (correlated multi-col via LATERAL), FOREACH, MERGE ON CREATE/MATCH, EXISTS { WHERE }, label OR `(n:A\|B)`, dynamic props `n[$key]`, `USE graphname`. Bolt 5.4 protocol (TCP + WebSocket). |
| **Graph Analytics** | PageRank, WCC, CDLP, PPR-guided subgraph — pure ObjectScript over `^KG` globals. |
| **FHIR Bridge** | ICD-10→MeSH mapping via UMLS for clinical-to-KG integration. |
| **GraphQL** | Auto-generated schema from knowledge graph labels. |
| **Embedded Python** | `EmbeddedConnection` — zero-boilerplate dbapi2 adapter for IRIS Language=python methods. |
| **Multi-graph** | `USE graphname` maps to IRIS namespace/schema switching via `set_schema_prefix()`. |
| **NKGAccel** | Rust-accelerated BFS via `Graph.KG.NKGAccel` — requires the native accelerator library. |

## Compliance

| Benchmark | Score | IRIS Version |
|-----------|-------|-------------|
| **openCypher TCK** (133 tests) | **100%** (133/133) | IRIS 2026.1+ |
| **openCypher TCK** | 99.2% (132/133) | IRIS 2025.1 |
| **GQS fuzzer** (differential vs Neo4j) | 98.4% | IRIS 2025.1 community |
| **GDBMeter** (metamorphic oracle) | 0 logic bugs | 10-min run |
| **Multi-DB TCK comparison** | IVG=100%, Neo4j=100%, Memgraph=91.7% | — |

The single 2025.1 failure: `SKIP` clause uses `ORDER BY + OFFSET` on JSON_TABLE-based queries, which requires IRIS 2026.1+.

---

## Interactive Demo

Two live demos ship in `src/iris_demo_server/`:

| Demo | URL | What it shows | Docs |
|------|-----|--------------|------|
| **Fraud Detection** | `http://localhost:8200/fraud` | Real-time fraud scoring, ring detection, money mule identification, bitemporal audit trails | [docs/demos/FRAUD_DEMO.md](docs/demos/FRAUD_DEMO.md) |
| **Biomedical Research** | `http://localhost:8200/bio` | Protein similarity search, pathway traversal, hybrid vector+graph queries, D3 network visualization | [docs/demos/BIOMEDICAL_DEMO.md](docs/demos/BIOMEDICAL_DEMO.md) |

The fraud demo is inspired by the [AWS Neptune fraud graph reference notebook](https://github.com/aws/graph-notebook/blob/main/src/graph_notebook/notebooks/01-Neptune-Database/03-Sample-Applications/01-Fraud-Graphs/01-Building-a-Fraud-Graph-Application.ipynb) — the same fraud ring and identity theft patterns (first-party and third-party fraud on credit card transaction data), running on IRIS with Cypher instead of Gremlin.

```bash
# 1. Start IRIS
docker compose up -d

# 2. Install deps (once)
pip install "iris-vector-graph[full]"

# 3. Start demo server
python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1 \
  --app-dir src

# 4. Open browser
open http://localhost:8200
```

The demos use the generic IVG graph engine — no separate backend required.

---

## Quick Start

### Python

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname='localhost', port=1972, namespace='USER', username='_SYSTEM', password='SYS')
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
```

### Inside IRIS (Language=python, no connection needed)

```python
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(EmbeddedConnection(), embedding_dimension=768)
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

## Engine Status

Call `engine.status()` at any time to get a structured snapshot of all components. This is the canonical answer to "why is query X returning nothing?"

```python
s = engine.status()
print(s.report())

# Readiness gates — use before running query types
s.ready_for_bfs           # var-length / undirected / shortestPath — needs ^KG + edges
s.ready_for_vector_search # needs node embeddings
s.ready_for_edge_search   # needs edge embeddings
s.ready_for_full_text     # needs BM25 index

# Example: rebuild ^KG if stale
if not s.ready_for_bfs and s.tables.edges > 0:
    engine.build_graph_globals()  # calls BuildKG()
```

Sample output:
```
IVG Engine Status
══════════════════════════════════════════
SQL Tables  (probe: 23ms)
  nodes              10,000
  edges              50,000
  ...
Adjacency Globals
  ✓ ^KG   (50,000 source nodes indexed)
  ✗ ^NKG  (integer adjacency index for Rust acceleration)
...
```

`status()` is explicit-call only — never run automatically at init or before queries. Cost ~50ms.

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

IVG ships a full graph algorithm suite backed by a three-tier dispatch chain:

| Tier | Backend | When it fires | ER(2000) sampled |
|------|---------|---------------|-----------------|
| 1 | **Native Rust accelerator** | accelerator library deployed + `^NKG` built | ~8ms |
| 2 | **ObjectScript parallel** (`%SYSTEM.WorkMgr` 8×) | accelerator absent; `^NKG` built | ~500ms |
| 3 | **Python LazyKG** | `^NKG` not built | slow, always works |

Dispatch is **automatic and transparent** — call the engine method, get the fastest path available.

### Centrality (v1.98.0 + v2.0.0)

```python
# Degree centrality — out/in/both, optionally predicate-filtered
scores = engine.degree_centrality(direction="out", top_k=20)
# → [{"id": "auth-service", "score": 0.847, "degree": 12}, ...]

# Betweenness centrality — Brandes (2001), Rust parallel when accelerator loaded
# sample_size=200: Brandes-Pich approximation (fast, good ranking)
# sample_size=0:   exact full Brandes (slower, ground truth)
scores = engine.betweenness_centrality(sample_size=200, top_k=20)
# → [{"id": "api-gateway", "score": 4821.3}, ...]

# Neighborhood betweenness — O(neighborhood), not O(graph)
# Scales to any total graph size; performance depends on hops neighborhood only
scores = engine.betweenness_centrality_neighborhood(
    seed="MESH:D009101",   # Multiple Myeloma (or any node ID)
    hops=2,                # 2-hop neighborhood: ~500-5K nodes for biomedical KGs
    sample_size=200,
    top_k=20,
)
# → [{"id": "TP53", "score": 1234.5}, ...]   (hub bottlenecks in disease neighborhood)

# Closeness centrality — harmonic (default) or classical
scores = engine.closeness_centrality(formula="harmonic", top_k=20)
# formula="classical": standard Bavelas–Freeman, undefined for disconnected graphs
# formula="harmonic":  Beauchamp (1965), well-defined for disconnected graphs

# Eigenvector centrality — power iteration, L2-normalized
scores = engine.eigenvector_centrality(max_iter=50, tol=1e-6, top_k=20)
# matches networkx.eigenvector_centrality_numpy (raw adjacency A, not transition matrix)
```

**Via Cypher:**

```cypher
CALL ivg.degreeCentrality({direction: "out", topK: 20})
  YIELD node, score, degree

CALL ivg.betweenness({sampleSize: 200, topK: 20})
  YIELD node, score

CALL ivg.closeness({formula: "harmonic", topK: 20})
  YIELD node, score

CALL ivg.eigenvector({maxIter: 50, topK: 20})
  YIELD node, score
```

---

### Community Detection (v1.99.0)

```python
# Leiden community detection (Traag et al. 2019)
# gamma=1.0: ModularityVertexPartition (canonical Leiden, default)
# gamma != 1.0: CPMVertexPartition (resolution parameter, smaller communities)
communities = engine.leiden_communities(gamma=1.0, top_k=100)
# → [{"id": "node-a", "community": 0, "size": 23}, ...]

# Triangle count + local clustering coefficient
triangles = engine.triangle_count(top_k=100)
# → [{"id": "hub-node", "triangles": 45, "lcc": 0.73}, ...]

# Strongly connected components (iterative Tarjan 1972)
sccs = engine.strongly_connected_components(top_k=100)
# → [{"id": "node-a", "component": 0, "size": 8}, ...]

# K-core decomposition (Batagelj-Zaversnik 2003, O(V+E))
cores = engine.k_core_decomposition(top_k=100)
# → [{"id": "dense-hub", "coreness": 5}, ...]
```

**Via Cypher:**

```cypher
CALL ivg.leiden({gamma: 1.0, topK: 100})
  YIELD node, community, size

CALL ivg.triangleCount({topK: 100})
  YIELD node, triangles, lcc

CALL ivg.scc({topK: 100})
  YIELD node, component, size

CALL ivg.kcore({topK: 100})
  YIELD node, coreness
```

---

### Algorithm Selection Guide

| Question | Algorithm | Notes |
|----------|-----------|-------|
| Who has the most connections? | `degree_centrality` | Fast, O(V+E) |
| Who controls information flow? | `betweenness_centrality` | Use `sample_size=200` for large graphs |
| Which disease-network bottlenecks matter? | `betweenness_centrality_neighborhood` | O(neighborhood), not O(graph) |
| Who reaches others fastest? | `closeness_centrality(formula="harmonic")` | Handles disconnected graphs |
| Who is most influential by propagation? | `eigenvector_centrality` | Captures network prestige |
| What are the dense clusters? | `leiden_communities` | Best modularity; use `gamma<1.0` for smaller communities |
| How tightly connected are nodes? | `triangle_count` | LCC field = local clustering coefficient |
| Are there feedback loops? | `strongly_connected_components` | Directed-graph cycles |
| What is the network's backbone? | `k_core_decomposition` | High coreness = structural core |

---

### Native Accelerator (Rust, Production Performance)

```bash
# Copy the accelerator library to your IRIS container
docker cp libarno_callout_arm64_linux.so <container>:/usr/irissys/mgr/libarno_callout.so

# Load it at IRIS startup (e.g., in %ZSTART or your application init)
Do ##class(Graph.KG.NKGAccel).Load("/usr/irissys/mgr/libarno_callout.so")
```

Without the accelerator, all algorithms fall back gracefully to the ObjectScript parallel (Tier 2) or Python LazyKG (Tier 3) path. See [docs/performance/GRAPH_ALGORITHMS.md](docs/performance/GRAPH_ALGORITHMS.md) for tier latencies.

Algorithms that operate under memory budgets emit warnings to `^IVG.warnings`:

```python
# Check if any nodes were skipped due to memory budget
warnings = engine.get_community_warnings(max_entries=50)
warnings += engine.get_centrality_warnings(max_entries=50)
for w in warnings:
    print(w)  # {"node_id": "...", "reason": "mem_budget_exceeded", ...}
```

---


## FHIR Bridge

```python
from iris_vector_graph import get_kg_anchors, unified_clinical_pipeline, FHIRSearchTool

# Load ICD-10→MeSH mappings from UMLS MRCONSO
# python scripts/ingest/load_umls_bridges.py --mrconso /path/to/MRCONSO.RRF

# Resolve ICD-10 codes to KG node IDs
anchors = engine.get_kg_anchors(icd_codes=["J18.0", "E11.9"])
# → ["MeSH:D001996", "MeSH:D003924"]  (filtered to nodes in KG)

# Full pipeline: FHIR patient → conditions → KG anchors → PPR → ranked results
result = unified_clinical_pipeline(
    engine=engine,
    query="pneumonia elderly",
    fhir_base_url="http://localhost:8080/fhir",
    patient_id="maria-gonzalez-001",
)
# result["status"] → "ok"
# result["anchors"] → ["MeSH:D011014", "MeSH:D003924"]
# result["ppr_results"] → [{"node_id": "...", "score": 0.85}, ...]

# MCP-compatible tool for AI agents
tool = FHIRSearchTool(base_url="http://localhost:8080/fhir")
conditions = tool("patient-123")  # → {"conditions": [...], "error": None}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    iris-vector-graph  v2.0.0                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌───────────────┐   ┌───────────────┐   ┌───────────────────┐    │
│   │  Python SDK   │   │  Cypher/AQL   │   │   Bolt (wire)     │    │
│   │  IRISGraph    │   │  translator   │   │   neo4j-driver    │    │
│   │  Engine       │   │  + executor   │   │   compatible      │    │
│   └───────┬───────┘   └───────┬───────┘   └────────┬──────────┘    │
│           └──────────────┬────┘                    │               │
│                          ▼                          │               │
│             ┌────────────────────────┐              │               │
│             │   GraphStore protocol  │◄─────────────┘               │
│             │   (pluggable backend)  │                              │
│             └───────────┬────────────┘                              │
│                         │                                           │
│          ┌──────────────┼──────────────┐                           │
│          ▼              ▼              ▼                            │
│   ┌─────────────┐ ┌──────────┐ ┌───────────────┐                  │
│   │  SQL layer  │ │  ^KG     │ │  ^NKG         │                  │
│   │  Graph_KG.* │ │  globals │ │  integer adj  │                  │
│   │  (nodes,    │ │  (edges, │ │  index        │                  │
│   │   edges,    │ │   temp,  │ └───────┬───────┘                  │
│   │   vectors)  │ │   PPR)   │         │                          │
│   └─────────────┘ └──────────┘         │                          │
│                                         ▼                          │
│                              ┌────────────────────┐               │
│                              │  Algorithm tiers   │               │
│                              ├────────────────────┤               │
│                              │ 1. Rust accelerator│ ← fastest     │
│                              │    (rayon parallel)│               │
│                              │ 2. ObjectScript    │               │
│                              │    parallel 8×     │               │
│                              │ 3. Python LazyKG   │ ← always works│
│                              └────────────────────┘               │
│                                                                     │
│   Centrality:  betweenness (Brandes) · closeness · eigenvector     │
│                degree                                              │
│   Community:   Leiden · triangle count · SCC · k-core             │
│   Search:      vector (HNSW/IVF/PLAID) · BM25 · temporal · PPR   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

For global structure, SQL schema, and ObjectScript class reference, see [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

---

## Performance

**Graph traversal & search** (M3 Ultra, Community IRIS 2025.1, 8.9K nodes / 31K edges):

| Operation | Latency | Notes |
|-----------|---------|-------|
| 1-hop neighbors | 0.3ms | `$Order` on `^KG` |
| Temporal window query | 0.1ms | O(results), B-tree |
| GetAggregate (1 bucket, 5min) | 0.085ms | Pre-aggregated |
| GetAggregate (288 buckets, 24hr) | 0.160ms | O(buckets), not O(edges) |
| VecIndex search (1K vecs, 128-dim) | 4ms | RP-tree + `$vectorop` SIMD |
| HNSW search (143K vecs, 768-dim) | 1.7ms | Native IRIS VECTOR index |
| PLAID search (500 docs, 4 tokens) | ~14ms | Centroid scoring + MaxSim |
| BM25Index search (174 nodes, 3-term) | 0.3ms | `$Order` posting-list |
| PPR (10K nodes) | 62ms | Pure ObjectScript |

For graph algorithm benchmarks (betweenness, Leiden, centrality vs networkx, tier comparison), see **[docs/performance/GRAPH_ALGORITHMS.md](docs/performance/GRAPH_ALGORITHMS.md)**.

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

### v2.0.0 (2026-05-29)

**Major release: all centrality algorithms accelerated to Rust rayon parallel. New neighborhood betweenness for biomedical KGs.**

**Centrality ObjectScript fast paths (specs 168-170):**
- **`ClosenessGlobal`** — harmonic/classical closeness via BFS over `^NKG`; matches `networkx.harmonic_centrality` (raw `sumInv`). Fix: was incorrectly dividing by `(n-1)` total container count.
- **`EigenvectorGlobal`** — L2-normalized power iteration; matches `networkx.eigenvector_centrality_numpy`.
- **`BetweennessGlobal`** — Brandes (2001) with sampled approximation (`maxSources=200` default) and `%SYSTEM.WorkMgr` 8-way ObjectScript parallelism; `$BITLOGIC` BFS cuts per-source cost 2×.

**Native Rust accelerator: parallel Brandes (spec 171):**
- Rust function reads adjacency cache once (version-keyed), stores in process-static memory, runs rayon parallel Brandes — zero IRIS I/O on cache hits.
- Benchmark: karate **6×**, ER(500) **68×**, ER(2000) **5×** faster than networkx on sampled=200.
- Exact Brandes: karate **4×**, ER(500) **5×** faster than networkx; see [performance doc](docs/performance/GRAPH_ALGORITHMS.md) for full numbers.

**Neighborhood betweenness for biomedical KGs (spec 173):**
- `engine.betweenness_centrality_neighborhood(seed, hops=2, sample_size=200, top_k=20)` — extracts 2-hop disease neighborhood (~500-5K nodes), runs Brandes on subgraph only. **Performance scales with neighborhood size, not total KG size.** A 10M-node biomedical KG with a 5K-node disease neighborhood runs in ~10ms.
- Rust implementation extracts subgraph from in-process adjacency cache (microseconds) then runs rayon Brandes on the subgraph. Zero IRIS I/O after first call.
- Biomedical use case: "Which genes are the bottlenecks between Multiple Myeloma and its known drug targets?"

**Bug fixes:**
- `<MAXNUMBER>` overflow in ObjectScript Brandes — replaced O(N²) comma-string BFS queue with `^||bfsQueue` global; capped all intermediate arithmetic with `+$Number(expr,15)`.
- `$Number(x,15)` doesn't cap magnitude (only precision) — added `+` unary prefix to force numeric evaluation before storage.
- IRIS emits `"score":.666` (no leading zero) for fractional scores — `_fix_iris_json()` regex patches all JSON output before `json.loads()`.
- Rust accelerator repeated-call 5,000ms regression — `NameSpace::try_new` opened a new CalIn session per call; fixed by version-keyed `BETWEENNESS_ADJ_CACHE` that skips IRIS I/O on cache hits.
- `ExportAdjacencyNKG` NODEMAP format — now embeds node names in adjacency cache eliminating N round-trips to `^NKG("$ND",i)` per Brandes call (was 997ms → 16ms on ER(500)).

### v1.99.0 (2026-05-28)

- **feat**: Spec 163 — Community Detection & Cluster Analysis Suite. Four new graph algorithms via the GraphStore protocol + Cypher procedures + dual-path architecture (arno Rust accelerator primary + LazyKG pure-Python fallback):
  - `engine.leiden_communities(max_levels, gamma, tol, top_k, mem_budget_mb, random_seed, progress_callback)` — Leiden community detection (Traag et al. 2019). At `gamma=1.0` uses `ModularityVertexPartition` (canonical Leiden); at `gamma != 1.0` uses `CPMVertexPartition` for resolution control. ARI = 1.0 with `leidenalg` reference (4-way benchmark on karate, ER(500), ER(2000)).
  - `engine.triangle_count(top_k, progress_callback)` — symmetrized triangle count + LCC. Pearson > 0.95 with `networkx.triangles(networkx.Graph(G_directed))` on Erdős-Rényi 100-node fixture.
  - `engine.strongly_connected_components(top_k, progress_callback)` — iterative Tarjan (1972) with explicit DFS stack frames (avoids Python recursion limit on graphs with deep DFS chains). Exact set-equality with `networkx.strongly_connected_components`.
  - `engine.k_core_decomposition(top_k, progress_callback)` — Batagelj-Zaversnik (2003) bucket-sort O(V+E) over symmetrized adjacency. Per-node exact match with `networkx.core_number`.
- **feat**: 4 Cypher procedures `CALL ivg.leiden({...}) YIELD node, community, size`, `CALL ivg.triangleCount({...}) YIELD node, triangles, lcc`, `CALL ivg.scc({...}) YIELD node, component, size`, `CALL ivg.kcore({...}) YIELD node, coreness`. Map-parameter syntax with FR-015 unknown-key rejection (reserves `weighted` key for future weighted-Leiden variants).
- **feat**: `engine.get_community_warnings(max_entries=50)` reads `^IVG.warnings("communities", *)` for memory-budget skip events.
- **feat**: 4 new `GraphStore` protocol methods (`execute_leiden`, `execute_triangle_count`, `execute_scc`, `execute_k_core`) + 4 capability keys.
- **feat**: 4 new Pydantic input models exported from package root: `LeidenInput`, `TriangleCountInput`, `SCCInput`, `KCoreInput`.
- **feat (architecture)**: **LazyKG adapter** (`iris_vector_graph.stores.lazy_kg.LazyKG`) — on-demand `^KG` global access via the IRIS Native API with per-node-level neighbor caching. Bug-S-immune (no `##class()` calls). Powers all 4 spec 163 algorithms; ready to power spec 162 retrofit.
- **feat (architecture)**: **arno Rust accelerator bridge** (`iris_vector_graph.stores.arno_bridge`) — calls `$ZF(-5)` user functions via Native API to invoke `libarno_callout.so` Rust kernels (`kg_leiden_run`, `kg_triangle_count_run`, `kg_scc_run`, `kg_kcore_run`). When `libarno_callout.so` is deployed, all 4 community algorithms route through Rust automatically; falls back transparently to LazyKG when not deployed. The Rust Leiden kernel is backed by the `leiden-rs` v0.8 crate (full Traag 2019 three-phase: local moving + refinement + aggregation, CPM/Modularity/RBC quality functions). Disable via `IVG_DISABLE_ARNO=1` to force LazyKG.
- **feat (perf)**: Server-side `^KG` walk via SQL OBJECTSCRIPT function (`ivg_arno_build_adj`) — single Python→IRIS round-trip replaces ~20K Native-API `nextSubscript` hops. Drops graph serialization from 944ms to 9–60ms on ER(2000, 9941e), making total IVG Leiden time competitive with native Neo4j GDS.
- **feat**: 4-way Leiden benchmark (`tests/perf/test_leiden_four_way.py`) — runs the same fixture through (1) `engine.leiden_communities()` (arno path when libarno deployed, LazyKG otherwise), (2) `networkx.community.louvain_communities`, (3) `leidenalg.find_partition` direct, (4) Neo4j GDS `gds.leiden.stream`. All four engines run **Modularity Leiden at γ=1.0** for apples-to-apples comparison; reports both end-to-end and kernel-only times. Captures wall-clock + modularity + community count + pairwise ARI; emits structured JSON to `benchmarks/leiden_4way_<timestamp>.json`. **Quality**: IVG ≡ leidenalg direct (ARI=1.0 on karate, 4 communities, Q=0.420 — identical partition); IVG ≡ Neo4j GDS Leiden (ARI=0.898 on karate). **End-to-end speed (post-optimization)**: IVG 6ms vs GDS 206ms on ER(500, 2437e) — **34× faster**; IVG 60ms vs GDS 60ms on ER(2000, 9941e) — tied; IVG 96ms vs GDS 115ms on karate — **1.2× faster**. Quality matches the leidenalg reference exactly while delivering competitive-to-superior performance.
- **feat**: New `[communities]` optional install extra: `pip install iris-vector-graph[communities]` pulls `python-igraph>=0.11`, `leidenalg>=0.10`, `networkx>=3.0`. `[full]` extra now includes these by default.
- **feat**: Test fixture loader (`tests/e2e/fixtures/community_graphs.py`) with 7 graph builders: Zachary's karate club, Erdős-Rényi, complete `K_n`, star, directed cycle, path, simple DAG. `load_into_engine()` automatically calls `engine.build_graph_globals()` after SQL ingest to repair `^KG` (Bug S workaround for `Graph.KG.EdgeScan` failure on external Python).
- **fix (FR-007 honest threshold)**: Karate club ARI gate relaxed from > 0.85 to > 0.75 with mandatory cardinality check (must produce 17+17 partition). Across seeds 0-49 with string-sorted node IDs (UUID-prefixed in IVG), the maximum achievable ARI for any leidenalg configuration is 0.772; the original 0.85 threshold assumed igraph's natural integer vertex ordering preserves Zachary's canonical partition, which IVG's string-ID convention breaks. The 17+17 cardinality assertion is the actual algorithmic correctness gate.
- **test**: 12 new e2e tests in `tests/e2e/test_communities_e2e.py` (3 per algorithm + 1 arno-vs-LazyKG cross-check, all PASS against `ivg-iris`) + 4 xfail-marked Cypher procedure tests pending Bug S upstream fix.
- **test**: 52 new unit tests across `tests/unit/test_communities_unit.py`, `tests/unit/test_communities_translator.py`, `tests/unit/test_lazy_kg.py`, `tests/unit/test_arno_bridge.py`. 82/82 spec 163 unit tests PASS.
- **docs**: `specs/163-communities/{spec,plan,research,data-model,quickstart,tasks,contracts/}` — full speckit artifacts with 6 clarifications, 26 functional requirements, 9 NFRs.
- **docs**: ENGINEERING_DEBT.md Bug S marked MITIGATED (LazyKG + Native API gref bypass on production path; SQL function path remains xfail-blocked pending kernel-team fix to `%SYS.DBSRV` user-class XDCall lookup).

### v1.98.0 (2026-05-28)
- **feat**: Spec 162 — Centrality Suite. Four new graph centrality algorithms shipping via the GraphStore protocol + Cypher procedures, closing the biggest coverage gap vs Neo4j GDS:
  - `engine.degree_centrality(direction, predicate, top_k)` — out/in/both, predicate-filtered, normalized to (n-1)
  - `engine.betweenness_centrality(sample_size, direction, max_hops, top_k, mem_budget_mb, progress_callback)` — Brandes (2001), Brandes-Pich approximation when sampled, per-source memory budget, progress reporting
  - `engine.closeness_centrality(formula, direction, max_hops, top_k, progress_callback)` — `harmonic` (default, robust to disconnection) and `classical` formulas
  - `engine.eigenvector_centrality(max_iter, tol, top_k, progress_callback)` — power iteration over raw adjacency `A`, L2-normalized, matches `networkx.eigenvector_centrality_numpy` (NOT PageRank with α=1)
- **feat**: 4 Cypher procedures `CALL ivg.degreeCentrality({...}) YIELD node, score, degree`, `CALL ivg.betweenness({...}) YIELD node, score`, `CALL ivg.closeness({...})`, `CALL ivg.eigenvector({...})` with map-parameter syntax. Procedure-call validator rejects unknown keys (FR-029 forward-compat reservation for future `weighted` variants).
- **feat**: `engine.get_centrality_warnings()` reads `^IVG.warnings("centrality", ...)` for memory-budget skip events; Brandes writes warning entries when per-source predecessor accumulator exceeds `mem_budget_mb`.
- **feat**: 4 new `GraphStore` protocol methods (`execute_degree_centrality`, `execute_betweenness`, `execute_closeness`, `execute_eigenvector`) + 4 capability keys.
- **feat**: 4 new Pydantic input models exported from package root: `DegreeCentralityInput`, `BetweennessInput`, `ClosenessInput`, `EigenvectorInput`.
- **feat**: `scripts/test-container.sh` — single entry point for IRIS test container ops (replaces ad-hoc `IRISContainer.start()` calls). Includes graceful `iris stop IRIS quietly` before `docker rm -f` (Bug T mitigation).
- **feat**: Container renamed from legacy `gqs-ivg-test` (ephemeral) to `ivg-iris` (persistent, registered in lab_manager registry as `status: active`).
- **fix (Bug S)**: Native API gref-bypass production path for centrality algorithms — when `iris.createIRIS().classMethodValue('Graph.KG.Centrality', ...)` returns `<CLASS DOES NOT EXIST>` from `%SYS.DBSRV` cache, the Python store automatically falls back to direct `^KG` global access via `iris_inst.set/get/nextSubscript/kill`. Algorithm correctness proven via Pearson > 0.85 with networkx reference on `networkx.betweenness_centrality`, `harmonic_centrality`, `eigenvector_centrality_numpy`, `out_degree_centrality`.
- **fix (Bug T)**: `iris-devtester>=1.18.1` upstream fix — `IRISContainer.__exit__()` now calls `stop_gracefully()` (graceful `iris stop IRIS quietly`) before Docker SIGKILL, preventing silent row loss on container restart. IVG bumped pin to `iris-devtester>=1.18.1`.
- **fix (Bug R, false alarm)**: Investigation confirmed `los-iris` slowness from unindexed `rdf_labels.s`/`rdf_props.s` was specific to productivity-framework's container schema; IVG's `initialize_schema()` already creates `idx_labels_s` and `idx_props_s`. No IVG fix needed.
- **test**: 16 new e2e tests in `tests/e2e/test_centrality_e2e.py` — networkx parity master gate + per-algorithm validation (15 PASS + 1 XFAIL Bug S Cypher path, deeply documented).
- **test**: 30 new unit tests in `tests/unit/test_centrality_unit.py` and `tests/unit/test_centrality_translator.py` — protocol routing, Pydantic validation, Cypher translator FR-029 enforcement.
- **docs**: `specs/162-centrality-suite/{spec,plan,research,data-model,quickstart,tasks}.md` — full spec with 5 clarifications integrated, 29 functional requirements, 6 NFRs, 10 user stories.
- **docs**: `ENGINEERING_DEBT.md` Bug S + Bug T entries with reproduction steps and resolution context.

### v1.88.0 (2026-05-07)
- **feat**: `ffi_kg_build_2hop_exact_int` Rust function — integer-indexed single-pass 2-hop dedup from `^KG("out")`. Writes results to `^ArnoKG("2h")` temp global; `DecodeBuildResults()` ObjectScript method converts to `^KG("deg2p_exact")`
- **feat**: `KHop2CountExact(src, pred)` ObjectScript method — O(1) `$Get(^KG("deg2p_exact"))`, fallback to `KHop2Count` when not populated. 0.14ms p50 on SF10 (was 70ms)
- **feat**: `Build2HopExactStats()` — Rust-first (tries `kg_build_2hop_exact_int`), ObjectScript fallback. Called automatically by `BuildNKG` and `engine.rebuild_nkg()`
- **feat**: `engine.khop2_count_exact(node_id, pred)` — public method with `KHop2Input` validation
- **feat**: `engine.backfill_deg2p_exact()` — populate `^KG("deg2p_exact")` for graphs loaded via `BulkIngestEdges`
- **feat**: `execute_cypher` `[:P*2] RETURN count(n)` fast path now routes to `KHop2CountExact` (exact, not upper bound)
- **test**: `tests/e2e/test_ic3_exact_count.py` — correctness + perf validation for 2-hop exact COUNT
- **test**: `tests/e2e/test_untested_methods.py` — 113/113 public engine methods now have at least one test (100% coverage)

### v1.87.0 (2026-05-07)
- **feat**: `iris_vector_graph/_validate.py` — 10 Pydantic `BaseModel` input schemas for high-risk engine methods: `NodeIdInput`, `EdgeInput`, `CypherInput`, `IVFBuildInput`, `VectorSearchInput`, `BM25BuildInput`, `BM25SearchInput`, `KHop2Input`, `TemporalEdgeInput`, `VecSearchInput`
- **feat**: Input validation at call entry on `execute_cypher`, `create_node`, `create_edge`, `ivf_build`, `ivf_search`, `bm25_build`, `bm25_search`, `khop2_count_fast`, `create_edge_temporal`, `search_nodes_by_vector`
- All 10 schemas exported from `iris_vector_graph.__init__`; 44/44 unit tests in `test_validation.py`
- **chore**: `BulkIngestEdges` marked `[ Internal ]` in `EdgeScan.cls` — safe path is `engine.bulk_ingest_edges()`

### v1.86.0 (2026-05-07)
- **feat**: `IVGResult` Pydantic `BaseModel` replaces `Dict[str, Any]` as return type of `execute_cypher`
  - Backward-compatible: `result["columns"]`, `result.get("error")`, `"error" in result` all work
  - `bool(result)` = `True` on success, `False` on error
  - `result.columns`, `result.rows`, `result.error`, `result.metadata`, `result.sql` via dot notation
  - 23 unit tests in `test_ivgresult.py`; all 189+ existing call sites pass unchanged
- **feat**: Fourth Pydantic increment — `IVGResult` joins `SQLQuery`, `QueryMetadata`, `IndexHandle`

### v1.85.0 (2026-05-06)
- **fix**: Unbounded variable-length path queries (no LIMIT) now always route to `_bfs_stream_pages` (cursor-based `ReadBFSPage`) instead of `ReadBFSResults` (single JSON string that hits `<MAXSTRING>` at 93K+ results). Bounded queries (LIMIT present) keep `ReadBFSResults` fast path.
- **fix**: `test_sc003_results_match_bfs` — replaced raw `NKGAccel.BFSJson` call (bypassed engine, `^NKG` stale) with engine determinism check; `knows_data` fixture calls `engine.rebuild_nkg()` for sync guarantee
- **test**: `tests/e2e/test_streaming_bfs.py` — 3 e2e + 2 routing unit tests for streaming BFS

### v1.84.0 (2026-05-06)
- **feat**: `engine.index(name)` → `IndexHandle` (Pydantic `BaseModel`) — unified entry point for all index types (`ivf`, `bm25`, `vec`, `plaid`) via `.search()`, `.insert()`, `.info()`, `.drop()`
- **feat**: `IVGIndex` `@runtime_checkable` Protocol — structural subtyping, no inheritance required
- **feat**: `_build_index_registry()` — auto-populates `{name: type}` from `^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID` on `IRISGraphEngine.__init__`; updated by `*_build` methods
- **feat**: `PLAIDSearch.Build` public ClassMethod — calls `StoreCentroids`+`StoreDocTokensBatch`+`BuildInvertedIndex` internally; helpers marked `[ Private ]`
- **feat**: `plaid_build()` now calls `PLAIDSearch.Build` (single round-trip); `plaid_info()` returns `{"type":"plaid","indexed":N,"nlist":L,"dim":D}`
- **feat**: All `*_info()` methods return `"type"` key — `ivf_info()`, `bm25_info()`, `vec_info()`, `plaid_info()`
- **feat**: `IVGIndex` and `IndexHandle` exported from `iris_vector_graph.__init__`
- **test**: Full PLAID e2e coverage (5/5); `engine.index()` dispatch tests (5 pass, 1 skip)

### v1.83.0 (2026-05-06)
- **feat**: `KHop2Count` + `KHop2NeighborIds(maxResults)` on `Graph.KG.Traversal` — pure ObjectScript 2-hop traversal with process-private dedup, no JSON serialization
- **feat**: `execute_cypher` routes `[:PRED*2]` COUNT and LIMIT patterns to fast paths — IC3 LIMIT 1000 now **1.2ms p50** (was 14-22ms; 3.5x faster than GES 4.19ms)
- **feat**: `create_node(graph=)` — optional named graph param stored as `__graph` property; propagated to `bulk_create_nodes` per-node `graph` key
- **feat**: `bulk_ingest_edges(edges, predicate)` — engine wrapper for `BulkIngestEdges` with `_nkg_dirty` flag and immediate `RuntimeWarning`
- **feat**: `rebuild_nkg()` — companion to `bulk_ingest_edges`; clears `_nkg_dirty` flag after `^NKG` rebuild
- **fix**: `ivf_build` `<STRINGSTACK>` on 768-dim embeddings — `IVFIndex.Build` now sets up centroids only; assignments written via new `IVFIndex.AddBatch` in chunks controlled by `build_batch_size=500`
- **feat**: `IVFIndex.FinalizeIndex(name)` — recounts indexed vectors after all `AddBatch` calls and updates `cfg.indexed`

### v1.82.0 (2026-05-06)
- **feat**: `dbapi_utils.py` — low-level vector utilities for raw DBAPI cursors without requiring `IRISGraphEngine`: `normalize_vector`, `insert_vector`, `create_hnsw_index`, `create_ivfflat_index`, `vector_similarity_search`
- **feat**: `KHopCount` + `KHopNeighborIds` on `Graph.KG.Traversal` — O(1) 1-hop count via `^KG("degp")` counter; newline-delimited ID list without JSON overhead
- **feat**: `execute_cypher` fast path routes single-hop COUNT and `node_id`-only patterns to `KHopCount`/`KHopNeighborIds` — IC2 COUNT now **0.29ms p50** (was 2.8ms)
- **feat**: `_nkg_dirty` instance flag on `IRISGraphEngine` — `_execute_var_length_cypher` emits `RuntimeWarning` when `^NKG` is stale

### v1.81.0 (2026-05-02)
- **feat**: `IVG.CypherEngine` ObjectScript class — instantiate `Local()` or `Remote()` and submit Cypher from pure ObjectScript; returns `%DynamicObject {columns, rows, error}`
- **feat**: Python-first introspection API — `get_labels()`, `get_relationship_types()`, `get_node_count(label)`, `get_edge_count(predicate)`, `get_label_distribution()`, `get_property_keys(label)`, `node_exists(node_id)` — no Cypher required
- **feat**: `embed_nodes(label=, predicate=, node_ids=)` typed params — replaces SQL `where=` fragment; `where=` still works with `DeprecationWarning`
- **fix**: `EmbeddedConnection` now accepts `iris_sql=` param — allows passing pre-loaded `iris.sql` module from `Language=python` methods, bypassing sys.path manipulation
- **fix**: `is_ready()` and `node_exists()` — replaced `FETCH FIRST 1 ROWS ONLY` with `COUNT(*)` to avoid IRIS 2025.1 community driver segfault
- **fix**: `_ensure_embedded_iris_first()` — `lib/python` now correctly placed at `sys.path[0]` ahead of `mgr/python`; `_require_iris_sql()` wraps full call chain in single `try/except ImportError`
- **fix**: Test collection errors for optional deps (`strawberry`, `pandas`) — added `pytest.importorskip` guards
- **fix**: `test_named_path_with_where_filter` — added node ID anchor to WHERE clause to prevent cross-test data contamination
- **test**: `tests/e2e/test_execution_contexts_new.py` — all 3 execution contexts (External DBAPI, EmbeddedConnection unit mock, ObjectScript `IVG.CypherEngine` via docker exec)
- **test**: `tests/e2e/test_introspection_api.py` — e2e coverage for all 7 new introspection methods

### v1.80.0 (2026-05-02)
- **feat**: `(n:Person|Animal)` label OR — parser handles `|` between labels; translator generates `IN ('A','B')` JOIN instead of two separate JOINs
- **feat**: `EXISTS { MATCH (p)-[:R]->(f) WHERE f.age > 18 }` full form — WHERE clause inside EXISTS subquery now parsed and included in the EXISTS SQL correlated subquery
- **fix**: MERGE ON CREATE/ON MATCH now uses the actual node UUID (from `__create_id_*`) not the SQL alias — fixes `n.created` being NULL after `MERGE ... ON CREATE SET n.created = true`
- **feat**: `CALL { CREATE (:Node) }` write-only subqueries (no RETURN required) — RETURN is now optional when inner clauses are all updating (CREATE/MERGE/SET/DELETE)
- **feat**: `OPTIONAL CALL { ... }` — `OPTIONAL` before `CALL { }` now parsed correctly
- **feat**: `n[$key]` dynamic property access — subscript with variable/param key generates `LEFT JOIN rdf_props` with dynamic key binding
- **fix**: `USE graphname` and `USE GRAPH graphname` — recursion bug fixed; now correctly sets `graph_context` on the query (maps to `set_schema_prefix()` for named-graph / multi-namespace support)

### v1.79.0 (2026-05-02)
- **fix**: `FOREACH (x IN ['a','b'] | MERGE (:N {val: x}))` — loop variable `x` now resolves to the actual list item value instead of raw AST `Variable` object. Literal list FOREACH fully functional.

### v1.78.0 (2026-05-02)
- **feat**: `CALL { WITH p MATCH (p)-[:R]->(f) RETURN f.name AS n, f.id AS i }` — multi-column correlated subqueries via `CROSS JOIN LATERAL`. Requires IRIS 2026.1+. Inner SQL constants inlined to avoid bind param ordering issues.

### v1.77.0 (2026-05-01)
- **feat**: openCypher TCK **100% (133/133)** on IRIS 2026.1 community and enterprise, 99.2% on IRIS 2025.1 community
- **fix**: `CREATE (:A)-[:REL]->(:B)` — anonymous unnamed nodes now track UUIDs in `_anon_node_keys` for correct edge INSERT
- **feat**: Map projection `n{.name}` — new `MapProjection` AST node, parser, and translator (generates `LEFT JOIN rdf_props` per projected key)
- **fix**: `MATCH ()-[r:T]->()` anonymous source nodes no longer generate Cartesian product; edge table used directly as FROM

### v1.76.0 (2026-05-01)
- **fix**: SQLCODE -23 `Stage1.col` in SELECT and ORDER BY — all CTE-qualified references stripped to unqualified column names (IRIS rejects `Stage1.a0` in mixed SELECT contexts)

### v1.75.0 (2026-05-01)
- **fix**: `IVG.Percentile_PDISC/PCONT` ObjectScript precedence — `lower >= n-1` parsed as `(lower >= n) - 1` in ObjectScript, always true; fixed with explicit parentheses `lower >= (n-1)`
- **fix**: Bolt server relationship detection — no longer misidentifies scalar columns as relationship type when followed by `_id` column

### v1.74.0 (2026-05-01)
- **feat**: `percentileDisc/Cont` via `IVG.Percentile` ObjectScript class (new `IVG.*` package avoids `User.func*` name-conflict issue on IRIS 2026.2); correct `(n-1)*p` formula
- **feat**: `MATCH ()-[r:KNOWS]->()` pattern — `LIST_REVERSE`, `LIST_TAIL` UDFs use While loops (compatible with IRIS 2026.1+)

### v1.73.0 (2026-05-01)
- **feat**: `SQLUser.LIST_HEAD`, `LIST_LAST`, `LIST_REVERSE`, `LIST_TAIL`, `STR_SPLIT`, `REGEX_MATCH` ObjectScript UDFs — proper typed returns
- **fix**: `CREATE (a)-[:REL]->(b)` with unnamed nodes — CREATE correctly generates edge INSERT using per-node UUID tracking

### v1.72.0 (2026-05-01)
- **feat**: openCypher TCK **85%→91.7%** — scalar coercion in Bolt (`Decimal`→`float`, JSON string→list), `SQLUser.RAND()`/`NEWID()` UDFs, `XOR` operator, `UNION/UNION ALL` without MATCH

### v1.71.0 (2026-05-01)
- **feat**: openCypher TCK **76%→85%** — `CREATE (n) RETURN n.val`, `toString(bool)`→`'true'/'false'`, `substring()` 0-indexed, `round()`, missing math/string functions, `split()`, `reverse(list)`

### v1.70.0 (2026-05-01)
- **feat**: Graceful degradation on complex SQL errors (SQLCODE -400/-29/-23/-12) — returns empty result with warning instead of propagating exception to caller (GQS sees "wrong answer" not "crash")
- **feat**: openCypher TCK **47%→76%** — BooleanExpression in RETURN, CREATE without `id`, scalar coercion, `toString`, `XOR`, `UNION` without MATCH

### v1.69.0 (2026-05-01)
- **fix(089)**: Empty `SELECT FROM Stage1` (SQLCODE -12) — when a recursive `self.parse()` call handles `WITH...ORDER BY...LIMIT...WHERE...RETURN` chains, the top-level query has no `return_clause` and generates `SELECT \nFROM Stage1`. Guard added: if `select_items` is empty AND a Stage CTE exists AND a FROM clause exists, inject `SELECT *` to prevent invalid SQL.
- **fix(090)**: Auto-CTE split for deep JOIN chains (SQLCODE -400) — when assembled SQL exceeds 20 JOINs (no aggregates, no GROUP BY), wraps the MATCH body in `WITH _MR AS (SELECT explicit_cols ...) SELECT aliases FROM _MR`. Resolves synthetic GQS queries at 21-29 JOINs. Note: IRIS community edition optimizer has a hard limit ~20-24 JOINs; queries beyond this are not fixable without recursive CTEs (forthcoming IRIS feature).

### v1.68.0 (2026-05-01)
- **fix(086)**: Function argument literal inlining — `RIGHT(?,?)` → `RIGHT('str',1)`. Eliminates "Incorrect number of parameters" in 5/7 unique large multi-path GQS queries. Root cause: `translate_expression` was parameterizing compile-time constant literals passed as function args; these are now inlined using `segment='inline'`.
- **fix(087)**: SQLCODE -23 `Stage1.col` unqualification — IRIS forbids CTE-qualified column references (`Stage1.a0`) in SELECT or ORDER BY when mixed with derived expressions. Variable resolution, PropertyReference, and ORDER BY all now emit unqualified column names when the alias is a Stage CTE. Also: `r.prop` on a Stage alias uses `SQLUser.JSON_VALUE(col, '$.prop')`.
- **fix(087)**: ORDER BY strips `StageN.` prefix (from both alias-path and expression-path) so IRIS can resolve CTE columns correctly.
- **feat**: GQS 10-minute pass rate (v1.68.0): **~98.5%** (target ≥98%)

### v1.67.1 (2026-05-01)
- fix: SQLCODE -1/-14/-15 — `false`/`true` Cypher literals in boolean context (`WHERE`, `AND`, `OR`, `NOT`) now emit `(1=0)`/`(1=1)` instead of raw `0`/`1`. IRIS SQL requires a comparison expression for `OR`/`AND` operands; bare `0` was causing SQLCODE -14 "comparison operator required".

### v1.67.0 (2026-05-01)
- fix: SQLCODE -23 (UNWIND) — `JSON_TABLE` moved to `CROSS JOIN` (after regular JOINs), not comma-separated in FROM. Prevents `Label N0/P97 not listed` when UNWIND references JOIN aliases.
- fix: SQLCODE -23 (undirected edge in WITH) — `Variable` expression for undirected edge alias now returns `alias._p` not `alias.p`. Fixes `E16.P not found` when undirected edge used in WITH clause.
- fix: SQLCODE -12 `A term expected` — `WITH...ORDER BY...SKIP...WHERE...RETURN` was parsing RETURN into a `subsequent_query` stub, leaving SELECT list empty (`SELECT FROM ...`). Now merges RETURN back onto main query when `return_clause is None`.
- fix: `WITH *` for undirected edges uses `_src/_p/_dst` column names.
- fix: `type(r)` after WITH stage: when edge var alias is `StageN`, uses `Stage.varname` not `Stage.p`.
- test: `test_cypher_benchmark_scale` skipped by default (set `SKIP_BENCHMARK_SCALE=false` to run), marked `@pytest.mark.slow`.

### v1.66.5 (2026-04-30)
- fix: `MatchEdges`-derived aliases (`s/p/o_id/w` columns only, no `qualifiers`) now return `NULL` for custom edge properties instead of crashing with SQLCODE -29 `e.QUALIFIERS not found`. Tracked via `_edgescan_aliases` set.
- fix: Restore outer `else: rdf_edges` JOIN for `use_edgescan=False` case (VecSearch source). Was accidentally dropped when adding edgescan tracking, causing param count mismatch in `CALL...YIELD...MATCH` queries.

### v1.66.4 (2026-04-30)
- fix: Inline node property filters in `MATCH` patterns now use `rdf_props` JOIN instead of direct column access. `MATCH (n)-[r]-(m {k12:'val'})` previously generated `WHERE n1.k12=?` which fails SQLCODE -29 (`nodes` table only has `node_id`/`created_at`). Now generates `JOIN rdf_props p ON p.s = n1.node_id AND p.key=? WHERE p.val=?`.

### v1.66.3 (2026-04-30)
- fix: `UNWIND [expr] AS x RETURN x` now emits scalar column access (`u.x`) instead of full node expansion (`u.node_id + rdf_labels + rdf_props`). The UNWIND variable is now registered in `scalar_variables` immediately after JSON_TABLE setup, preventing SQLCODE -23 "label N0 not listed" errors in GQS-style queries.

### v1.66.2 (2026-04-30)
- fix: `JSON_ARRAYLENGTH`, `JSON_ARRAYGET`, `JSON_VALUE` now installed as `SQLUser.*` user-defined functions during `initialize_schema()`. Previously these bare SQL calls were qualified with the default schema (`Graph_KG.JSON_ARRAYLENGTH`) which IRIS couldn't find, causing SQLCODE -359. All three are now qualified as `SQLUser.*` in generated SQL and work regardless of current default schema.
- fix: `size([list])`, `head(list)`, `last(list)` Cypher functions now work end-to-end against live IRIS.

### v1.66.1 (2026-04-30)
- fix: relationship property translation — `r.id`, `r.k1`, etc. now correctly uses `JSON_VALUE(e.qualifiers, '$.property')` for directed edges. Previously returned `e.node_id` (wrong column — edges don't have `node_id`), causing SQLCODE -29 `<Field not found>` for all edge property access. Undirected edges now return `NULL` for custom properties (UNION ALL subquery can't project qualifiers). Fixes the dominant GQS failure class.

### v1.66.0 (2026-04-30)
- fix: 818/818 tests green on `gqs-ivg-test` live IRIS container (no mocked IRIS in e2e)
- fix: ObjectScript ^KG shard-0 migration — `Algorithms.cls`, `PageRank.cls`, `Subgraph.cls` updated from `^KG("out",node,...)` to `^KG("out",0,node,...)` — WCC/CDLP/PPR/Subgraph all work against live `^KG` data
- fix: `kg_NodeEmbeddings` / `kg_EdgeEmbeddings` recreated as `VECTOR(DOUBLE, 768)` — corrects prior schema with wrong column type
- feat: Cypher `WITH...ORDER BY...RETURN` — RETURN clause after `WITH ... ORDER BY` was being parsed as a subsequent query; now correctly merged as main query return
- feat: WITH clause scalar alias propagation — `PropertyReference` and non-Variable WITH aliases now added to `scalar_variables`, preventing node label/props expansion on scalar columns in RETURN
- fix: `size()` function — dispatches to `LENGTH()` for string/scalar args, `JSON_ARRAYLENGTH()` for list literals. Eliminates param count mismatches when `size('literal')` was called.
- fix: CALL+MATCH `rdf_edges` JOIN — when source is a VecSearch CTE and EdgeScan is disabled, the rdf_edges JOIN was silently dropped, causing `e1.o_id` undefined alias errors

### v1.65.4 (2026-04-30)
- fix: `NKGAccel.BFSJson` per-seed adjacency export — `ExportAdjacencyFromSeed()` exports only the subgraph reachable from the seed node (not the full 299K-edge graph). Fixes `<MAXSTRING>` on Mindwalk-scale graphs, enabling Arno-accelerated multi-hop BFS. Adjacency string now scales with BFS result size (~10KB per seed instead of >3.5MB full graph). Handles outbound + inbound edges for undirected BFS.


### v1.63.4 (2026-04-26)
- chore: merge 080-engine-status to main; NKGAccel.cls added to iris_src from arno upstream


### v1.63.3 (2026-04-26)
- feat: `engine.status() -> EngineStatus` — structured runtime snapshot: SQL row counts, `^KG`/`^NKG` population, ObjectScript classes, Arno capabilities, HNSW/IVF/BM25/PLAID index inventory. Readiness properties: `ready_for_bfs`, `ready_for_vector_search`, `ready_for_edge_search`, `ready_for_full_text`. Detects `^KG`/`rdf_edges` predicate mismatch (stale ^KG from different data snapshot). (spec 080)
- fix: `BuildKG()` `Traversal.cls` SQL cursors now use fully-qualified `Graph_KG.rdf_edges`, `Graph_KG.rdf_labels`, `Graph_KG.rdf_props` — fixes predicate mismatch when IRIS namespace default SQL schema is not `Graph_KG` (e.g. MINDWALK namespace with `SQLUser` default)
- fix: `kg_IVFMeta`, `kg_BM25Meta`, `kg_PlaidMeta` added to security allowlist
- `EngineStatus` exported from top-level `iris_vector_graph`


### v1.63.2 (2026-04-25)
- fix: `MATCH (a)-[r*1..N]-(b)` undirected BFS now traverses `^KG("in",...)` for inbound edges (was outbound-only)
- fix: `MATCH (a)<-[r*1..N]-(b)` inbound-only BFS now works  
- fix: `initialize_schema()` ObjectScript LoadDir tries Docker `/tmp/src/` before Mac path — fixes silent compile failure in test containers
- 4 E2E tests: directed-out, undirected, multihop undirected, directed-in all passing
- Arno BFSJson falls back gracefully to BFSFastJson for graphs >3.5MB adjacency string (299K+ long-ID edges); per-seed export is spec 079 future work


### v1.63.0 (2026-04-25)
- feat: Arno/Rust fast path for BFS (`_execute_var_length_cypher`) — when `libarno_callout.so` is loaded with `Graph.KG.NKGAccel.BFSJson`, var-length Cypher queries use Rust BFS over `^NKG` integer adjacency instead of ObjectScript `BFSFastJson`. Projected 128ms → <30ms p50 for 6K+ result BFS at 10K/50K scale. Falls back transparently to `BFSFastJson` when Arno not loaded. (spec 079, arno spec 035)


### v1.62.1 (2026-04-25)
- fix: `WITH n, count(r) AS cnt WHERE cnt > N` — IRIS SQLCODE -23 fixed; CTEs containing GROUP BY now emit inline subqueries `FROM (...GROUP BY...) Stage1` instead of `WITH Stage1 AS (...GROUP BY...) SELECT ... FROM Stage1` (IRIS 2025.x doesn't support aggregation in CTEs)
- fix: `WITH HAVING` now uses the full aggregate expression (e.g. `COUNT(e.p) >= 2`) not the alias (`cnt >= 2`) — IRIS doesn't allow column aliases in HAVING
- fix: `REMOVE n:Label` now parses and translates correctly (was missed in spec 068)
- perf: E2E benchmark 12/12 passing against live IRIS container — point lookup 0.2ms p50, aggregation 0.3ms, BFS 0.7ms, SET+= 1.1ms, UNION 0.4ms

### v1.62.0 (2026-04-25)

**openCypher spec: 100% (99/99 testable features)**

- feat: `SET n += {map}` / `SET n += $param` — map merge operator (spec 075)
- fix: `isEmpty([])` — parser bug with empty list in function args (spec 076)
- feat: `shortestPath((a)-[*]->(b))` in RETURN expression (spec 077)
- feat: `MATCH ... CALL proc() YIELD ... RETURN` — CALL in same query part as MATCH (spec 078)
- 26 E2E tests all passing against live IRIS container


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

## Changelog

### v1.97.0 (2026-05-16)

**Three new features closing the gap with NornicDB-style vector-graph fusion:**

**`CALL ivg.retrieve(query, limit, bm25_name?, vec_label?, rrf_k?)`** — single Cypher procedure for BM25 + vector + RRF fusion. Equivalent to NornicDB's `db.retrieve()`:
```cypher
CALL ivg.retrieve('insulin resistance', 10) YIELD node, score
MATCH (node)-[:INTERACTS_WITH]->(target)
RETURN target.node_id, score ORDER BY score DESC
```
Generates three-CTE SQL (BM25_Retrieve + Vec_Retrieve + Retrieve with FULL OUTER JOIN RRF fusion).

**`WHERE vector_distance(n, $vec) < 0.3`** — scalar vector similarity predicate in WHERE/RETURN clauses:
```cypher
MATCH (n:Gene) WHERE vector_distance(n, $vec) < 0.3 RETURN n.node_id
MATCH (n) RETURN n.node_id, vector_similarity(n, $vec) AS sim ORDER BY sim DESC LIMIT 10
```
Translates to `VECTOR_COSINE()` subquery against `kg_NodeEmbeddings`.

**`Graph.KG.EmbedQueue`** — async embedding queue (ObjectScript). Write nodes now, embeddings appear asynchronously:
```python
engine.enqueue_for_embedding(["n1", "n2", "n3"], embedding_config="my-model")
engine.start_background_embedding(batch_size=100)
count = engine.embed_queue_pending()
result = engine.process_embed_queue(batch_size=50)
```
Uses `^EmbedQueue` global + `Graph.KG.EmbedQueue.ProcessBatch()` via `%SYSTEM.Task`.

### v1.96.2 (2026-05-15)

**Fix**: `_build_index_registry()` infinite loop when `iris.gref` is a `MagicMock` (external connections via IVR or test mocks). `gref.order()` on a MagicMock returns a MagicMock, which is never `== ""`, causing infinite loop. Fix: `isinstance(name, str)` guard + `range(10000)` hard limit. Reported by IVR session.

### v1.96.1 (2026-05-15)

**Fix**: Lazy-load `sentence-transformers` and `torch` to prevent repeated memory allocation. Inline `from sentence_transformers import SentenceTransformer` in `embed_text()`, `embed_nodes()`, `embed_edges()` replaced with module-level singletons (`_get_sentence_transformers()`, `_load_sentence_transformer()`). Prevents torch reference counting from blocking GC between embedding batches.

### v1.96.0 (2026-05-15)

**IVG SDK, CLI, Deploy, and iris-embedded-python-wrapper adoption** (spec 160):

**`iris_vector_graph.sdk`** — new thin HTTP client, zero `intersystems-irispython` required:
```python
from iris_vector_graph import IVGClient
with IVGClient("http://localhost:8200", api_key="...") as client:
    result = client.execute_cypher("MATCH (n) RETURN count(n)")
    result = client.execute_aql("FOR v IN 1..2 OUTBOUND @s g RETURN v._key", bind_vars={"s": "n1"})
```
- `IVGRecord` — dict-style row access: `r["name"]` and `r[0]` both work
- `IVGError` / `IVGClientError` / `IVGServerError` — structured exception hierarchy
- `AsyncIVGClient` — identical async API
- Retry on 5xx (3× exponential backoff)
- `ping()`, `schema()`, `server_info()`, `stats()`, `explain()`, `load_ndjson()`

**`ivg` CLI** — `pip install "iris-vector-graph[cli]"`:
```bash
ivg connect http://localhost:8200
ivg query "MATCH (n) RETURN count(n)"
ivg query --aql "FOR v IN 1..2 OUTBOUND @s g RETURN v" --bind s=mesh:D003924
ivg load graph.ndjson
ivg schema init / status
ivg indexes list / rebuild
ivg server start --iris-host localhost --iris-port 1972
```

**`deploy/`** folder — four setup paths:
- `deploy/docker/compose.yml` — fresh IRIS + IVG server in Docker
- `deploy/bolt-on/install.sh` — bolt onto existing IRIS
- `deploy/README.md` — decision guide

**`iris-embedded-python-wrapper` adoption**:
- `IRISGraphEngine.from_wrapper(hostname=...)` — new classmethod using `iris.dbapi.connect()`
- `cypher_api.py` `_make_engine()` prefers wrapper's `iris.dbapi.connect()` when available
- `iris-embedded-python-wrapper>=0.5.20` added to `[full]` extra
- `EmbeddedConnection` retained for backward compatibility

### v1.95.0 (2026-05-15)

**Admin API** — IVG now has a production-grade admin surface matching Neo4j/ArangoDB:

**Fixed: `SHOW INDEXES` / `SHOW CONSTRAINTS`** — were empty stubs; now return actual BM25, IVF, HNSW, PLAID, ^KG, ^NKG indexes and uniqueness constraints. Neo4j Browser, LangChain, and all Neo4j-compatible tools now see the real index state on connect.

**New REST endpoints on the Cypher API:**
- `GET /schema` — labels, relationship types, property keys, counts
- `GET /indexes` — full index inventory (all types)
- `GET /server` — IVG version, IRIS version, namespace, schema status, BFS path
- `GET /metrics` — Prometheus-format metrics (node/edge/embedding counts, status)
- `GET /stats` — counts by label, predicate, embedding coverage
- `POST /admin/schema/init` — initialize schema
- `POST /admin/indexes/rebuild` — rebuild ^KG and ^NKG adjacency indexes
- `POST /admin/embed` — trigger node embedding
- `POST /admin/load` — stream NDJSON graph data
- `GET /admin/export` — export graph as NDJSON
- `POST /admin/snapshot` — save snapshot to disk
- `GET /admin/queries` — list active IRIS queries
- `DELETE /admin/queries/{id}` — kill a running query
- `POST /admin/explain` — translate Cypher to SQL (debugging + optimization)

**GraphStore protocol additions** (6 new methods):
`get_node_count()`, `get_edge_count()`, `get_labels()`, `get_relationship_types()`, `list_indexes()`, `server_info()`

**Engine additions:**
`engine.list_active_queries()`, `engine.kill_query(id)`

### v1.94.0 (2026-05-15)

**GraphStore Protocol** — `IRISGraphEngine` now has a pluggable storage backend (spec 156).

- `GraphStore` Protocol (25 methods): reads, mutations, SQL, traversal, analytics, temporal, lifecycle
- `IRISGraphStore`: existing behavior extracted verbatim — zero behavior change for current users
- `IRISGraphEngine(conn, store=ArnoFjallStore(...))` — inject any `GraphStore` implementation
- `from iris_vector_graph import GraphStore, IRISGraphStore`
- Engine routing: `execute_cypher` dispatches BFS/shortest-path/PPR/WCC/temporal through the store
- `capabilities()` dict: stores advertise what they support; engine falls back to Python implementations for unsupported operations
- 175 new unit tests + 25 e2e tests (all pass)

**Bug fixes:**
- `ShortestPathJson` returned single dict instead of list — `path.get()` raised `AttributeError`; fixed by normalizing to list
- `get_edges_in_window` `KeyError: 'w'` when temporal edge JSON omits weight field; fixed with `.get("w", 1.0)` fallback

### v1.93.0 (2026-05-14)

**All openCypher translator gaps closed:**

- `CALL ivg.bm25.search(...) YIELD node, score` — fixed `Field 'NODE' not found` error. BM25/PPR CTEs now expose `node` column matching the VecSearch convention.
- `CALL ivg.ppr(...) YIELD node, score` — same fix.
- `MATCH p = (...) RETURN length(p)` — now returns actual hop count (1 for 1-hop, 2 for 2-hop, etc.) instead of static 1.
- `WHERE n.id IN ["a", "b"]` — confirmed working; tests added.
- `MATCH (n)-[r]->() RETURN count(r) ORDER BY ...` — confirmed working; tests added.

9 new e2e tests in `tests/e2e/test_cypher_gaps_e2e.py` gate all fixes.

### v1.92.2 (2026-05-12)

**Bug K fix**: `EmbeddedConnection.commit()` and `rollback()` were no-ops, causing writes via `store_node()`/`store_edge()` to not persist across sessions in IRIS embedded Python (`Language=python` methods). Fixed by calling `iris.sql.exec("COMMIT"/"ROLLBACK")` directly.

**Bug I fix** (v1.92.1): `store_embedding()` DELETE raises `SQLError('')` in embedded Python on VECTOR tables — wrapped in try/except, INSERT proceeds normally.

### v1.92.0 (2026-05-11)

**FHIR-KG Clinical Bridge** — new `iris_vector_graph.fhir_bridge` module bridges clinical patient data to the biomedical knowledge graph.

- `get_kg_anchors(engine, icd_codes)` — resolve ICD-10 codes to KG node IDs via `fhir_bridges` table
- `extract_icd_codes(bundle)` — parse ICD-10 codes from FHIR Condition bundles
- `fhir_search_conditions(url, patient_id)` — FHIR REST client (10s independent timeout, BasicAuth)
- `unified_clinical_pipeline(engine, ...)` — full pipeline: FHIR → anchors → PPR → ranked results with provenance
- `FHIRSearchTool` — MCP-compatible FHIR search wrapper for AI agents
- `GetPatientKGNeighborhoodTool` — MCP-compatible patient → graph neighborhood tool
- Cypher API: `POST /api/cypher` accepts optional `fhir_patient_id` + `fhir_base_url` — auto-resolves patient anchors into `$patient_anchors` parameter

**Bug fix:**
- Duplicate key detection now catches IRIS's actual "failed unique check" error message (previously only checked for SQLCODE -119 and "duplicate" substring, which don't match)

### v1.91.0 (2026-05-09)

**Engine-first architecture** — `IRISGraphOperators` is now a thin shim over `IRISGraphEngine`.
All 17 `kg_*` operators are implemented directly on the engine.

- `kg_KNN_VEC`: node-ID input path works correctly (looks up stored embedding, excludes self)
- `kg_SUBGRAPH`: populates `node_labels`, `node_properties`, `node_embeddings` from `SubgraphJson`
- `kg_PPR_GUIDED_SUBGRAPH`: returns `PprGuidedSubgraphData`; backward-compat `top_k`/`max_hops` params
- `kg_NEIGHBORS`: uses `node_id` field, validates direction parameter
- `kg_GRAPH_WALK`: multi-hop traversal via `BFSFastJsonSorted`
- `kg_PAGERANK` / `kg_PPR`: empty seeds return `[]` gracefully
- `bulk_delete_nodes(ids)`: new engine method — FK-safe batch delete

**ObjectScript fixes:**
- `NKGAccel.BFSJson`: 1d75d97 string-passing approach (`ExportAdjacencyWithPreds`)
- `Traversal.BFSFast`: predicate filter applied to all hops, result/frontier logic separated
- `TraverseWithPredicateFast`: records results before applying `nextP` frontier filter
- `BuildNKG`: calls `InvalidateAdjCache()` before rebuild to prevent stale arno cache
- `IVFIndex` / `BM25Index` / `PLAIDSearch`: added `List()` ClassMethod
- `_build_index_registry`: ObjectScript fallback via `List()` when `gref` unavailable

**GQL / Demo:**
- GQL `stats` field added: `{ stats { nodeCount edgeCount labelCount } }`
- Dynamic GQL type creation: sanitize property names with spaces to valid Python identifiers
- Demo server: `/bio`, `/fraud`, `/arch/fraud`, `/arch/bio` routes all live
- `iris_demo_server`: Biomedical routes registered

**Test infrastructure:**
- 524 e2e / 768 unit — **0 failures, 0 unjustified skips**
- All test fixtures use engine methods — no raw `cursor.execute()` in test data setup
- All `classMethodString` → `classMethodValue`, all `intersystems_iris` → `iris`
- All hardcoded ports → `os.environ.get()`

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
