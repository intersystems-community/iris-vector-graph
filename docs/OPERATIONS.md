# Operations Guide: IRIS Vector Graph

## Deployment

IVG deploys via the Python engine's `initialize_schema()` method. There is no separate deployment script — the engine handles SQL schema creation, ObjectScript class installation, and index setup in one idempotent call.

### Quick start

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname="localhost", port=1972, namespace="USER",
                    username="_SYSTEM", password="SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()   # idempotent — safe to call repeatedly
```

`initialize_schema()` creates the SQL tables (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`, `fhir_bridges`), compiles and loads all ObjectScript classes (`Graph.KG.*`), and creates the HNSW vector index if supported by the IRIS tier.

### Docker-based setup (development)

```bash
git clone https://github.com/intersystems-community/iris-vector-graph
cd iris-vector-graph
docker compose up -d    # starts IRIS Community on port 1972
conda run -n py312 python -c "
import iris
from iris_vector_graph.engine import IRISGraphEngine
conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
IRISGraphEngine(conn).initialize_schema()
print('ready')
"
```

### Connecting to an existing IRIS instance

```bash
IRIS_HOST=iris.example.com
IRIS_PORT=1972
IRIS_NAMESPACE=MYNAMESPACE
IRIS_USERNAME=myuser
IRIS_PASSWORD=secret
```

Pass these to `iris.connect()` directly, or use the environment-variable helpers in `iris_vector_graph.schema`.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `IRIS_HOST` | `localhost` | IRIS SuperServer hostname |
| `IRIS_PORT` | `1972` | IRIS SuperServer port |
| `IRIS_NAMESPACE` | `USER` | Target namespace |
| `IRIS_USERNAME` | `_SYSTEM` | Connection user |
| `IRIS_PASSWORD` | `SYS` | Connection password |
| `IRIS_CONTAINER` | — | Docker container name (used by `iris-devtester` in tests) |

---

## Index Management

### HNSW vector index

The HNSW index on `kg_NodeEmbeddings` is created automatically by `initialize_schema()` on IRIS tiers that support it (Community and Advanced Server). On Standard/HealthConnect tiers it is skipped silently; `ivf_build` / IVFFlat is used instead.

To rebuild the HNSW index after large data changes:

```sql
-- In IRIS SQL / Management Portal
ALTER INDEX kg_emb_hnsw ON Graph_KG.kg_NodeEmbeddings REBUILD;
```

### `^KG` and `^NKG` adjacency indexes

The graph adjacency index (`^KG`) is maintained automatically for individual writes (`create_edge`, `create_node`). After bulk loads via `bulk_ingest_edges()`, rebuild manually:

```python
engine.rebuild_kg()    # rebuilds ^KG from rdf_edges SQL table
engine.rebuild_nkg()   # rebuilds ^NKG integer index (needed for Arno/BFS acceleration)
```

`rebuild_nkg()` is slow on large graphs (422s for LDBC SF10). Only required when using Arno-accelerated BFS or variable-length Cypher path queries. The engine emits a `RuntimeWarning` if you attempt a BFS query with a stale `^NKG`.

---

## Maintenance

### Monitoring

Key metrics to track in IRIS System Management Portal:

- **Global buffer hits** for `^KG`, `^NKG`, `^BM25Idx` — high hit rate means hot data is cached
- **Query latency** for `kg_KNN_VEC` (HNSW vector search) and traversal procedures
- **Journal space** — large bulk loads generate significant journal volume; monitor `^KG` write amplification

### Backups

Standard IRIS backup (External Backup or Online Backup) covers all graph data. The `Graph_KG.*` SQL tables, `^KG`, `^NKG`, `^BM25Idx`, `^VecIdx`, `^PLAID`, and `^IVF` globals are all in the configured namespace database file.

### Rebuilding after restore

After restoring from backup, verify adjacency index consistency:

```python
status = engine.status()
print(status.adjacency.kg_populated, status.adjacency.nkg_populated)
# If False, run engine.rebuild_kg() and/or engine.rebuild_nkg()
```

---

## Security

IVG uses standard IRIS SQL permissions. No custom RBAC roles are created automatically — use IRIS Management Portal to assign SQL table privileges as appropriate for your deployment.

**Development defaults** (`_SYSTEM` / `SYS`) should be replaced with dedicated service accounts in production. Refer to IRIS Security documentation for user/role management.
