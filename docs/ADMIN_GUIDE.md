# IVG Admin Guide

**Audience**: Ops engineers and DBAs deploying, configuring, and monitoring iris-vector-graph.

For the developer API reference, see [USER_GUIDE.md](USER_GUIDE.md).

---

## 1. Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| InterSystems IRIS | 2025.1 Community | 2026.1 Enterprise |
| Python | 3.10 | 3.11+ |
| Docker | 24+ | latest |
| RAM | 4GB | 16GB+ (large graphs) |

Install the library:

```bash
pip install iris-vector-graph              # core
pip install iris-vector-graph[communities] # + Leiden community detection
pip install iris-vector-graph[full]        # everything
```

---

## 2. Container Setup

IVG uses a persistent named IRIS container. Use the wrapper script — **do not** call `IRISContainer.start()` directly (it creates ephemeral containers that vanish when the process exits).

```bash
# Start the container (idempotent — safe to run if already running)
scripts/test-container.sh up

# Check health
scripts/test-container.sh status

# Deploy updated ObjectScript source
scripts/test-container.sh deploy

# Compile one class
scripts/test-container.sh compile Graph.KG.NKGAccel

# Compile everything
scripts/test-container.sh compile-all

# Remove container (rare — destroys all data)
scripts/test-container.sh down
```

**Container name**: `ivg-iris`  
**Default port**: 1972 (override with `IVG_TEST_PORT`)  
**Namespace**: `USER`

The container persists across machine restarts as long as Docker is running. Data survives container restarts only if IRIS was stopped gracefully before the container stopped (see Backup & Restore).

---

## 3. Schema Initialization

On a fresh IRIS instance, initialize the schema before loading data:

```python
from iris_vector_graph.engine import IRISGraphEngine
import iris

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=384)
engine.initialize_schema()
```

**What it creates:**

| Object | Purpose |
|--------|---------|
| `Graph_KG.nodes` | Node registry |
| `Graph_KG.rdf_edges` | Edge store |
| `Graph_KG.rdf_labels` | Node labels |
| `Graph_KG.rdf_props` | Node properties |
| `Graph_KG.kg_NodeEmbeddings` | HNSW vector index |
| `Graph_KG.kg_EdgeEmbeddings` | Edge embeddings |
| SQL stored procedures | `kg_KNN_VEC`, `kg_NEIGHBORS`, `kg_PPR`, etc. |

`initialize_schema()` is **idempotent** — safe to call on an existing deployment. It checks for each object before creating it.

Re-run when:
- Upgrading IVG to a new minor version
- After restoring from a backup
- If stored procedures are missing (symptom: Cypher CALL ivg.* returns errors)

---

## 4. Index Management

IVG maintains two graph indices on top of the SQL schema:

### `^KG` — String adjacency index

Built from `Graph_KG.rdf_edges`. Required for Cypher queries and PPR.

```python
engine.rebuild_kg()    # ~8s for 500K edges
```

Or from ObjectScript:
```objectscript
Do ##class(Graph.KG.Traversal).BuildKG()
```

Rebuild when:
- After bulk-loading edges via `executemany` or direct SQL
- After importing from NDJSON or RDF
- After restoring `rdf_edges` from backup

### `^NKG` — Integer adjacency index

Built from `^KG`. Required for all graph algorithms (betweenness, closeness, Leiden, etc.). Also required for the native accelerator.

```python
engine.rebuild_nkg()   # ~15s for 500K edges
```

Or from ObjectScript:
```objectscript
Do ##class(Graph.KG.Traversal).BuildNKG()
```

Rebuild when:
- After `rebuild_kg()` (NKG is derived from KG)
- If algorithm methods return `[]` unexpectedly

**Cost**: `BuildKG` + `BuildNKG` together take ~20–30s for a 500K-edge graph on Community Edition. On Enterprise with the native accelerator, `BuildNKG` includes the Rust-accelerated 2-hop precomputation.

---

## 5. Native Accelerator Library

The native accelerator (`libarno_callout.so`) is a compiled Rust library that accelerates graph algorithms 10–300× vs the ObjectScript fallback. It is **optional** — all algorithms work without it via the ObjectScript parallel tier.

### Deploy

```bash
# Copy the pre-built arm64 Linux library to your IRIS container
docker cp libarno_callout_arm64_linux.so ivg-iris:/usr/irissys/mgr/libarno_callout.so
```

### Load at startup

Add to your IRIS `%ZSTART` routine or application init:

```objectscript
Do ##class(Graph.KG.NKGAccel).Load("/usr/irissys/mgr/libarno_callout.so")
```

Or from Python before running algorithms:

```python
import iris as _iris
iris_obj = _iris.createIRIS(conn)
iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
```

### Verify it loaded

```python
loaded = iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded")
print("Accelerator loaded:", bool(loaded))  # True = fast path active
```

### Disable (for testing)

Set env var before starting your process:

```bash
IVG_DISABLE_ARNO=1 python3 my_script.py
```

### Performance without accelerator

| Algorithm | With accelerator | Without (OS parallel) |
|-----------|-----------------|----------------------|
| Betweenness sampled | ~8ms | ~500ms |
| Leiden | ~60ms | ~2s |
| Betweenness exact | ~43ms | ~5s |

---

## 6. Environment Variables

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `IVG_TEST_PORT` | int | `1972` | IRIS superserver port for tests |
| `IVG_DISABLE_ARNO` | `1`/unset | unset | Force ObjectScript fallback (no Rust accelerator) |
| `IVG_ARNO_LIB` | path | `/usr/irissys/mgr/libarno_callout.so` | Path to accelerator library |
| `IVG_SNAPSHOT_DIR` | path | `/tmp/ivg_snapshots` | Directory for `save_snapshot()` / `restore_snapshot()` |
| `IVG_API_KEY` | string | unset | Bearer token for IVG HTTP server (spec 160) |
| `IVG_URL` | URL | unset | Remote IVG server URL for SDK client mode |
| `IVG_KEEP_CONTAINER` | `1`/unset | unset | Prevent test teardown from removing container |

---

## 7. Health Check

```python
status = engine.status()
print(status)
```

Key fields in the returned `EngineStatus` object:

| Field | What it means |
|-------|---------------|
| `ready_for_bfs` | `^NKG` is built — algorithms will work |
| `ready_for_vector_search` | HNSW index has embeddings |
| `ready_for_full_text` | BM25 index is built |
| `kg_edge_count` | Edges in `^KG` (should match SQL `rdf_edges` count) |
| `nkg_node_count` | Nodes indexed in `^NKG` |
| `adjacency.bfs_path` | `"arno"` = Rust accelerator active, `"objectscript"` = fallback |

**Common problem**: `ready_for_bfs = False` means `rebuild_nkg()` was never called or failed silently (see Troubleshooting #4).

---

## 8. Backup & Restore

IVG data lives in two places:

**SQL tables** (backed up by IRIS backup):
- `Graph_KG.nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`

**IRIS globals** (require global backup or export):
- `^KG` — string adjacency index
- `^NKG` — integer adjacency index  
- `^VecIdx` — VecIndex RP-tree
- `^BM25Idx` — BM25 lexical index
- `^PLAID` — PLAID multi-vector index

Globals can be rebuilt from SQL tables via `rebuild_kg()` + `rebuild_nkg()`, so they don't strictly need to be backed up — but rebuilding on large graphs takes time.

**Critical**: IRIS data persists across container restarts **only** if IRIS is stopped gracefully before the container stops:

```bash
# Graceful stop (preserves data)
docker exec ivg-iris iris stop IRIS quietly
docker stop ivg-iris

# Emergency stop (data recovers on next start but takes 30–300s)
docker stop ivg-iris
```

For production: configure your container orchestration to run the graceful stop before SIGKILL.

---

## 9. Troubleshooting

**1. Algorithm returns `[]` for all nodes**

Cause: `^NKG` not built.  
Fix: `engine.rebuild_nkg()` or `Do ##class(Graph.KG.Traversal).BuildNKG()`.  
Verify: `engine.status().ready_for_bfs` should be `True`.

**2. Algorithms slow (~500ms instead of ~8ms)**

Cause: Native accelerator not loaded.  
Fix: Deploy `libarno_callout.so` and call `NKGAccel.Load(...)` before running algorithms.  
Verify: `engine.status().adjacency.bfs_path == "arno"`.

**3. Embedding insert fails with dimension mismatch**

Cause: `kg_NodeEmbeddings` HNSW index was created with a different vector dimension than what you're inserting.  
Fix: Drop and recreate the table. `engine.initialize_schema()` alone won't change the dimension — you must explicitly drop `Graph_KG.kg_NodeEmbeddings` first.

**4. `BuildNKG` fails silently (no error, but `^NKG` empty)**

Cause: `Graph.KG.NKGAccel.InvalidateAdjCache()` throws `<DYNAMIC LIBRARY LOAD>` when the accelerator is not loaded. This error is non-trappable by ObjectScript `Try/Catch`, causing `BuildKG`'s internal `Try { Do ..BuildNKG() } Catch {}` to swallow it.  
Fix: Load the accelerator library **before** calling `BuildKG`, or call `BuildNKG` directly after confirming the accelerator is absent.

**5. `<CLASS DOES NOT EXIST> Graph.KG.Edge` after test run**

Cause: `iris_master_cleanup` test fixture recompiles ObjectScript, which can invalidate the `Graph.KG.Edge.1` compiled routine.  
Fix: `scripts/test-container.sh compile Graph.KG.Edge` or `compile-all`.

---

## See Also

- [User Guide](USER_GUIDE.md) — developer API reference
- [Architecture](ARCHITECTURE.md) — global structure, SQL schema, ObjectScript classes
- [Performance Benchmarks](../performance/GRAPH_ALGORITHMS.md) — algorithm latency vs networkx
