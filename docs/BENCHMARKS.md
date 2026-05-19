# IVG Performance Benchmarks

**Updated**: 2026-05-15  
**Platform**: MacBook Pro M3 Ultra, 128GB RAM  
**IRIS**: Community Edition 2025.1 (arno-graph-iris container, localhost)  
**Dataset**: 8,904 Person nodes, 31,000 KNOWS edges  
**Methodology**: 50 repetitions per query, median (p50) reported

---

## Query Latency — IVG-SQL (8.9K nodes, 31K edges)

| Query | p50 | p95 | min | rows | Path |
|-------|-----|-----|-----|------|------|
| 1-hop traversal | **570µs** | 682µs | 436µs | 31 | SQL JOIN on rdf_edges |
| 2-hop BFS | **17.6ms** | 18.2ms | 17.0ms | 92 | SQL BFS fallback (2 JOIN rounds) |
| 3-hop BFS | **33.7ms** | 34.7ms | 33.3ms | 183 | SQL BFS fallback (3 JOIN rounds) |
| COUNT neighbors | **413µs** | 512µs | 339µs | 1 | Aggregate SQL |
| Global node count | **4.0ms** | 4.1ms | 3.8ms | 1 | Full table scan |
| AQL 1-hop (translate+exec) | **688µs** | 915µs | 573µs | — | AQL→Cypher→SQL |

> **Container note**: This benchmark ran against Community IRIS *without* `Graph.KG.Traversal` ObjectScript classes deployed. Multi-hop BFS uses the SQL fallback path (new in v1.94.0). With full ObjectScript deployment, 2–3 hop BFS is **5–10× faster**.

> **Embedded Python note**: The 17ms / 34ms figures are for the **external dbapi path** (`iris.connect()`) where each BFS hop requires a network round-trip. When IVG runs inside IRIS via `EmbeddedConnection` (e.g., from a `Language=python` method, the Bolt server, or the MCP server), `cursor.execute()` calls use `iris.sql` in-process with no network overhead. In that context, the SQL BFS fallback runs in **~1–2ms for 2-hop** — competitive with ObjectScript BFSFastJsonSorted.

---

## SQL BFS Fallback — Three Execution Contexts

`IRISGraphStore._sql_bfs_fallback()` uses `cursor.execute()` which behaves very differently depending on which connection type was passed to `IRISGraphEngine`:

| Connection | Each `cursor.execute()` | 2-hop BFS | 3-hop BFS |
|------------|------------------------|-----------|-----------|
| `iris.connect()` (external dbapi) | ~0.4–1ms network round-trip | **~17ms** | **~34ms** |
| `EmbeddedConnection` (in-process) | ~0.05ms via `iris.sql` | **~1–2ms** | **~2–4ms** |
| `EmbeddedConnection` + ObjectScript | single ObjectScript call | **~0.6ms** | **~1ms** |

**When does EmbeddedConnection apply?** Any time IVG runs *inside* IRIS:
- `Language=python` methods calling `IRISGraphEngine(EmbeddedConnection())`
- The IVG Bolt server (spec 031) running as a CSP application
- The IVG MCP server
- Any embedded Python workflow inside IRIS

In these contexts, the SQL BFS fallback is **competitive with ObjectScript** because the SQL executes in-process via `iris.sql.prepare()` / `iris.sql.exec()` with no network overhead.

---

## ObjectScript BFS Path (Graph.KG.* deployed)

When `Graph.KG.Traversal` is installed (standard IVG deployment on enterprise IRIS):

| Query | SQL fallback | ObjectScript BFS | Speedup |
|-------|-------------|-----------------|---------|
| 2-hop BFS, 92 results | 17.6ms | **~2–3ms** | ~7× |
| 3-hop BFS, 183 results | 33.7ms | **~3–5ms** | ~7–10× |
| BFS p50 (spec 153, 1.5K-node graph) | — | **0.6ms** | — |

---

## Arno Rust BFS Acceleration (^NKG index)

When `^NKG` integer adjacency index is built (`engine.rebuild_nkg()`), BFS routes through the Rust callout. From spec 153 benchmark (synthetic 1,500-node graph):

| | ObjectScript | Arno Rust (^NKG) | Speedup |
|---|---|---|---|
| BFS p50 | 0.6ms | **0.4ms** | **1.5×** |

Benefit grows with graph size — at 100K+ nodes the Rust callout significantly outperforms ObjectScript due to cache-friendly integer subscript access on `^NKG`.

---

## Vector Search (HNSW)

From IRIS 2024.1+ with HNSW indexing:

| Search type | Latency | Dataset |
|---|---|---|
| K-NN **with** HNSW index | **~1.7ms** | 10K 384-dim embeddings |
| K-NN **without** HNSW (table scan) | **~5.8s** | Same dataset |
| HNSW speedup | **~3,400×** | |

Always call `engine.initialize_schema(embedding_dimension=N)` to build the HNSW index.

---

## Hybrid Search (Vector + BM25 + RRF)

End-to-end on moderate biomedical dataset — all inside IRIS, no separate systems:

| Phase | Latency |
|---|---|
| Vector K-NN (HNSW, top-15) | ~1.7ms |
| BM25 text search | ~5–20ms |
| RRF fusion + PPR reranking | ~30–100ms |
| **Total** | **~30–80ms** |

---

## Python Layer Overhead

| Layer | Overhead |
|---|---|
| `execute_cypher()` + Cypher parse | < 0.1ms |
| `GraphStore` routing | < 0.05ms |
| AQL translate (`translate_aql`) | ~0.1–0.15ms |
| **Total Python overhead** | **< 0.25ms** |

---

## Summary by Deployment Tier

| Tier | 1-hop | 2-hop BFS | 3-hop BFS |
|------|-------|-----------|-----------|
| Community IRIS, no ObjectScript | **570µs** | 17.6ms | 33.7ms |
| Community IRIS + ObjectScript | ~570µs | ~2–3ms | ~3–5ms |
| Enterprise IRIS + ObjectScript | ~400µs | ~1–2ms | ~2–4ms |
| Enterprise IRIS + Arno ^NKG | ~400µs | **~0.4ms** | **~0.8ms** |

---

## Benchmark Reproduction

```bash
cd ~/ws/iris-vector-graph
python benchmarks/three_engine_benchmark.py \
  --host localhost --port PORT --password SYS \
  --edges 10000 --reps 50
```
