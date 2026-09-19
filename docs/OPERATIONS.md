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

`initialize_schema()` creates the SQL tables (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`, `fhir_bridges`), compiles and loads all ObjectScript classes (`Graph.KG.*`), and installs the stored procedures. It does **not** create a vector index: there is no HNSW index on any embedding table, and creating one is an explicit operator step — see [Index Management](#index-management) for what that step can and cannot do today.

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

| Variable         | Default     | Description                                               |
| ---------------- | ----------- | --------------------------------------------------------- |
| `IRIS_HOST`      | `localhost` | IRIS SuperServer hostname                                 |
| `IRIS_PORT`      | `1972`      | IRIS SuperServer port                                     |
| `IRIS_NAMESPACE` | `USER`      | Target namespace                                          |
| `IRIS_USERNAME`  | `_SYSTEM`   | Connection user                                           |
| `IRIS_PASSWORD`  | `SYS`       | Connection password                                       |
| `IRIS_CONTAINER` | —           | Docker container name (used by `iris-devtester` in tests) |

---

## Index Management

### HNSW vector index

There is no HNSW index on `kg_NodeEmbeddings` or `kg_NodeEmbeddings_optimized`, and `initialize_schema()` does not create one. Index creation is an explicit operator step, and on these two tables it is currently **refused by IRIS**:

```text
ERROR #7222: %SQL.Index ANN indices are only supported when the IDKEY
             is based on a single positive integer attribute
```

Both classes declare `Index IDKEY On id [ IdKey ]` with `id As %String`, so an ANN index cannot be added to them without changing that key. Vector search therefore runs as a `VECTOR_COSINE` scan through `Graph_KG.kg_KNN_VEC` — correct, and linear in the number of embeddings. `ivf_build` / IVFFlat and PLAID are the index paths that do work on this schema.

`SHOW INDEXES` reports HNSW indexes it can find in the class dictionary and nothing when there are none. Before 3.2.0 it always reported one row named `hnsw_node_embeddings`, in state `ONLINE` when `kg_NodeEmbeddings_optimized` had rows and `BUILDING` when it did not. No index was ever behind that row, and `BUILDING` described a build that was not running.

### The embedding registry

`Graph_KG.embedding_registry` records what each embedding table holds: which model produced
its vectors, how wide they are, and how that was established. Every embedding write is
checked against it, so this is the table to read when a write is refused with
`EmbeddingIdentityConflict`.

```sql
SELECT table_name, mechanism, model_key, dimension, dtype, set_at, set_by
FROM Graph_KG.embedding_registry
ORDER BY table_name
```

One row per embedding table, keyed `(table_name, graph_id)`. `graph_id` is always `''` in
3.2.0 and means "all graphs" — it is reserved so per-graph identity can be added later
without a migration, and nothing reads it as anything else today.

`set_by` says where the row came from, which is what tells you how much to trust
`model_key`:

| `set_by`  | Meaning                                                                                                                                                                                                                                                     |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `adopted` | Derived from the column declaration during `initialize_schema()`. Width and dtype are true; `model_key` is `NULL` because it is not recoverable from the data — two models of the same width produce indistinguishable vectors. Only the width is enforced. |
| `claimed` | A writer declared a model and took the row. Both model and width are enforced from here on.                                                                                                                                                                 |
| `forced`  | An operator overrode a recorded identity with `force=True`. Treat the table's existing vectors as suspect (see below).                                                                                                                                      |

An adopted row's `NULL` model never conflicts, so the first writer that declares one claims
it. That claim is atomic: of two writers declaring different models concurrently, exactly
one wins and the other is refused.

**`force=True` invalidates every vector already stored.**

```python
from iris_vector_graph import identity_from_config
engine.set_embedding_identity(
    identity_from_config("my-new-model", dimension=768), "kg_NodeEmbeddings", force=True
)
```

This overwrites the recorded identity without checking it. The vectors already in the table
do not change — they stay there, produced by a model that is no longer the table's
recorded one, and they will be scored against queries from the new model. Distances will
compute and the rankings will be meaningless. Re-embed the table after forcing, or clear it
first. The call logs at `WARNING` saying so.

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
- **Query latency** for `kg_KNN_VEC` (a `VECTOR_COSINE` scan — no HNSW index exists, so this grows linearly with the embedding count) and traversal procedures
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
