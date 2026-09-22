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

### The embedding inventory

Read this before anything else about embeddings. One row per route, plus one row
per graph that has nodes and no route at all:

```bash
ivg embeddings inventory              # table
ivg embeddings inventory --json-output
```

```python
for row in engine.embedding_inventory():
    print(row.graph_id, row.model_key, row.table_name, row.row_count,
          row.index_state, row.recall_measured)
```

| Column                            | Authority                                                   |
| --------------------------------- | ----------------------------------------------------------- |
| `model_key`, `dimension`, `dtype` | `embedding_registry` — what a write is checked against      |
| `row_count`                       | the route's table, counted **inside the route's graph**     |
| `index_name`, `index_state`       | `%Dictionary.CompiledIndex`, via `GraphSchema.hnsw_indexes` |
| `recall_measured`, `..._at`       | the last measured recall for that route                     |

Three columns, three authorities, deliberately not reconciled. `index_state`
never comes from the registry's recorded state — that records what one
`CREATE INDEX` replied, not what the namespace holds now — and never from a row
count, which is the synthesized index row spec 226 removed.

`table_name=None` is a graph with nodes and no route. It is reported rather than
omitted, because "the vectors are in another route" and "there are no vectors"
need opposite actions and an absent row makes them the same answer.

An unreadable registry yields `[]`: that is a 3.2.0-or-earlier database with no
routes to report, and a status command must not raise on one.

Cost, measured on `ivg-iris-enterprise` at the tested ceiling: **one inventory
read over 100 routes takes 1.49 s.** It is one pass over the registry plus one
count per route, so it grows with the number of routes, not with the number of
vectors.

### HNSW vector index

One HNSW index per routed table (`kg_emb_<16 hex>`), and none on the legacy
`kg_NodeEmbeddings` / `kg_NodeEmbeddings_optimized`. Index creation is an
explicit operator step; `initialize_schema()` does not create one.

A routed table can carry an index because it is keyed `emb_rowid BIGINT
IDENTITY` — that column exists for exactly this reason. Before 4.0.0 the
embedding tables were keyed `id As %String` and IRIS refused:

```text
ERROR #7222: %SQL.Index ANN indices are only supported when the IDKEY
             is based on a single positive integer attribute
```

A route with no index is not broken: `Graph_KG.kg_KNN_VEC` falls back to a
`VECTOR_COSINE` scan, which is correct and linear in the route's row count.
`ivf_build` / IVFFlat and PLAID also work on this schema.

`SHOW INDEXES` and the inventory's `index_state` report what the class dictionary
holds and nothing when there is nothing:

| `index_state` | Meaning                                                                                                                                     |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `present`     | `%Dictionary.CompiledIndex` holds an HNSW index on that table                                                                               |
| `refused`     | a build was attempted and IRIS said no; its wording is in `index_error`                                                                     |
| `absent`      | nobody tried, **or** the route has no table — indistinguishable from the index's point of view, and both mean searches over this route scan |

Before 3.2.0 `SHOW INDEXES` always reported one row named
`hnsw_node_embeddings`, `ONLINE` when `kg_NodeEmbeddings_optimized` had rows and
`BUILDING` when it did not. No index was ever behind that row, and `BUILDING`
described a build that was not running.

### Recall per route, measured

A graph predicate on top of an ANN index degrades to post-filtering, which
changes recall. Routing removes the predicate — a routed table holds one graph's
vectors, so a scoped search over it needs no graph filter to be correct — but
that is an argument, not a measurement, so each route carries its own number:

```sql
SELECT table_name, graph_id, model_key, index_state,
       recall_measured, recall_measured_at
FROM Graph_KG.embedding_registry
ORDER BY graph_id, model_key
```

Measured on `ivg-iris-enterprise` (`tests/e2e/test_227_recall.py`), each route's
scoped search against an exact scan of the same rows:

| Route           | Model   | `index_state` | recall@10 |
| --------------- | ------- | ------------- | --------- |
| graph A's route | model A | `present`     | 1.0000    |
| graph B's route | model B | `present`     | 1.0000    |

`recall_measured` is `NULL` until something measures it. A `NULL` means unknown,
not perfect — the test that publishes this table fails if any recall claim has no
measurement behind it. Re-measure after a bulk load or an index rebuild:
`recall_measured_at` is how you tell a stale number from a current one.

### Routed tables: the tested ceiling

**50 graphs × 2 models = 100 routed tables, verified.** That is a measured
figure, not an architectural limit, and not an unbounded one either. Measured on
`ivg-iris-enterprise` (`tests/e2e/test_227_scale.py`):

| Operation                          | Wall clock | Per route |
| ---------------------------------- | ---------- | --------- |
| Create 100 routes (table + index)  | 12.4 s     | 0.124 s   |
| One inventory read over 100 routes | 1.49 s     | —         |
| Erase all 100                      | 2.0 s      | 0.020 s   |

Every route came back `index_state='present'`. A route is created by the first
write that names its `(graph, model_key)` pair, so route count grows with graphs
multiplied by models — watch it in the inventory before it becomes a problem,
because nothing caps it.

### The quarantine

`Graph_KG.embedding_quarantine` holds vectors the 4.0.0 migration could not place
in a graph. No search path reaches it, and a quarantined vector does not become
searchable by sitting still — an operator moves it.

```python
for row in engine.list_quarantine():            # or reason="ambiguous_graph"
    print(row.q_rowid, row.node_id, row.source_table, row.dimension, row.reason)
```

| Reason              | Meaning                                            |
| ------------------- | -------------------------------------------------- |
| `ambiguous_graph`   | more than one graph holds a node with that ID      |
| `no_node`           | no node holds that ID at all                       |
| `resolver_declined` | a resolver was offered the row and returned `None` |

Placement is one row at a time, with the graph named explicitly:

```python
engine.place_quarantined(q_rowid, graph="tenant-a", model_key="bge-small")
```

There is no bulk drain and no `force`. An operator who knows where a row belongs
knows it one row at a time; placing a whole table at once is the silent
default-graph assignment the migration refuses to make, under another name.

`place_quarantined` raises and leaves the row where it is when the pair has no
route, when the route's declared width or dtype disagrees with the row's, or when
the node does not exist *in that graph*. It never creates a route — that would
declare a width taken from a row whose provenance is the thing in doubt. Create
the route by storing a known-good vector into the pair first, then place.

An empty listing on a database with no quarantine table means a 3.2.0 install,
which has never quarantined anything.

Full upgrade path: [`docs/migration/v4.0.0.md`](migration/v4.0.0.md).

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

One row per embedding **route**, keyed `(table_name, graph_id)` and indexed
`(graph_id, model_key)`. In 3.2.0 `graph_id` was reserved and always `''`; since 4.0.0 it
is real, and a row names the graph whose vectors that table holds. `''` is the default
graph, not "all graphs". The registry is the only authority on what a route is called —
`route_table_name()` says what a route *would* be named, but IRIS may have derived a class
name the table name does not predict, so never derive a name to find a route.

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

`rebuild_nkg()` is slow on large graphs (422s for LDBC SF10). Only required when using Arno-accelerated BFS or variable-length Cypher path queries.

A variable-length Cypher query against a stale `^NKG` **raises `IndexNotSyncedError`** — it does not warn and answer. Both var-length routes check the flag before any traversal runs, so an unsynced bulk load cannot produce a quietly incomplete path result. `engine.sync()` clears it; `engine.status().pending_sync` reports it. (Through 3.2.0 this paragraph promised a `RuntimeWarning`, which no code path emits. The `RuntimeWarning`s the engine does raise are about the Arno accelerator not being loaded and about `degree_centrality(top_k=0)` on a large graph, neither of which is a staleness signal.)

`bulk_ingest_edges()` defaults to `auto_sync=True`, which syncs before returning, so the flag is only left set when you pass `auto_sync=False` — which is the right choice for a multi-batch load, with one `sync()` at the end.

### Table statistics

`initialize_schema` and `upgrade_to_4_0_0` both run `TUNE TABLE` over the core tables and report the result — `status["tuned"]` and `UpgradeReport.tuned` respectively, one entry per qualified table name. You do not have to run it by hand after either.

You do want it after loading a lot of rows into an install that was small when the schema was built, because `TUNE TABLE` records how many rows are there:

```python
from iris_vector_graph.schema import GraphSchema

cursor = engine.conn.cursor()
GraphSchema.tune_tables(cursor)   # or tables=["nodes"] for one
engine.conn.commit()
```

An untuned table says so in every plan it appears in: `Warning: Table Graph_KG.nodes is not tuned.` in the output of `EXPLAIN <statement>`. That is the signal to check when a graph-scoped read is slower than it should be. Without statistics, `WHERE node_id = ? AND graph_id = ?` can plan as `Read master map Graph_KG.nodes.IDKEY, looping on ID` — a scan of the whole extent — instead of an index-map read on `pk_nodes_graph`.

### Bulk ingest: choose a chunk size

`Graph.KG.EdgeScan.BulkIngestEdgesSQL` and `BulkIngestNodesSQL` wrap exactly the batch you hand them in one transaction and never commit early, and a transaction's per-row cost grows with its size. Measured on `ivg-iris-enterprise` over the same 10,000 edges:

| rows per call | edges/s |
| ------------- | ------- |
| 250           | 13,803  |
| 500           | 13,123  |
| 1,000         | 10,989  |
| 2,000         | 11,093  |
| 5,000         | 7,885   |
| 10,000        | 6,426   |

A few hundred rows per call is the fast shape. One enormous call is the slow one, and it is also the one whose failure rolls back the most work.

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
