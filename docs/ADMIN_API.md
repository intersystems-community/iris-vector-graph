<!-- markdownlint-disable MD013 -->

# The HTTP server: admin and observability endpoints

For IRIS operators who run IVG's HTTP server, and for application developers who call it through `IVGClient` or the
`ivg` command. A Python application that holds an `IRISGraphEngine` does not need this server: every endpoint below
calls an engine method you can call directly ([API reference](API_REFERENCE.md)).

The endpoints are served by `iris_vector_graph.cypher_api`, beside the Cypher endpoints (`/api/cypher`, the Neo4j
HTTP routes) and a Bolt listener.

## Starting the server

```bash
pip install 'iris-vector-graph[full,cli]'
IRIS_HOST=localhost IRIS_PORT=1972 IRIS_NAMESPACE=USER IRIS_USERNAME=_SYSTEM IRIS_PASSWORD=SYS \
  ivg server start                      # HTTP on 0.0.0.0:8200
```

- `ivg server start` listens on port 8200 (`--port`, `--host`, `--workers`). `--iris-host`, `--iris-port`,
  `--iris-namespace` and `--iris-password` set the matching `IRIS_*` variables; the user name is read from
  `IRIS_USERNAME` only. `python -m iris_vector_graph.cypher_api` starts the same app on port 8000.
- The server connects to IRIS on its first request, with the `IRIS_*` variables (defaults `1972`, `USER`, `_SYSTEM`,
  `SYS`). With no `IRIS_HOST` it needs to run inside IRIS (embedded Python), and otherwise answers every request with
  "No IRIS connection available".
- At start-up it also opens a Bolt listener on port 7687 for Neo4j drivers (`BOLT_TCP_PORT`; `BOLT_ENABLED=0` turns it
  off).

## Authentication

Setting `IVG_API_KEY` makes the server require the header `X-Api-Key: <key>` on paths that start `/api/`,
`/fhir-event` or `/graphql`. A missing or wrong key gets `401` and
`{"error": "unauthorized", "message": "X-Api-Key header required"}`. `/health`, `/docs`, `/openapi.json` and `/redoc`
never need it.

**In 4.6.0 the key protects nothing else on this page.** `/schema`, `/indexes`, `/server`, `/metrics`, `/stats`, every
`/admin/*` route (including `DELETE /admin/queries/{id}`, which ends an IRIS process) and the Neo4j HTTP routes
(`/db/...`) answer without it. Do not expose the server beyond the hosts you trust; put it behind a proxy that
authenticates if it must be reachable.

**`IVGClient` and the `ivg` command send the key as `Authorization: Bearer <key>`, which the server does not read.**
With `IVG_API_KEY` set on the server, `IVGClient(url, api_key=...)` gets `401` from `execute_cypher` (`/api/cypher`).
The admin calls succeed, because the server does not check them.

## Discovery and observability

Every example below uses `curl`; the bodies shown are examples of the shape, from a graph with three labels.

### `GET /schema`

Labels, relationship types, property keys, node and edge counts, and nodes per label
(`get_labels`, `get_relationship_types`, `get_property_keys`, `get_node_count`, `get_edge_count`,
`get_label_distribution`).

```bash
curl http://localhost:8200/schema
```

```json
{
  "labels": ["Disease", "Drug", "Gene"],
  "relationshipTypes": ["CAUSES", "INTERACTS_WITH", "TREATS"],
  "propertyKeys": ["id", "name", "organism"],
  "nodeCount": 8904,
  "edgeCount": 31000,
  "labelDistribution": { "Gene": 4200, "Disease": 2100, "Drug": 2604 }
}
```

### `GET /indexes`

The answer of the Cypher `SHOW INDEXES`, which Neo4j Browser and LangChain send on connect. It lists the `^KG` and
`^NKG` adjacency indexes and the node key, plus any BM25, IVF, PLAID or HNSW index that exists.

```bash
curl http://localhost:8200/indexes
```

```json
{
  "columns": ["name", "type", "entityType", "labelsOrTypes", "properties", "state"],
  "indexes": [
    ["nkg_adjacency", "ADJACENCY(^NKG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["kg_adjacency", "ADJACENCY(^KG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["pk_nodes", "UNIQUE", "NODE", ["*"], ["node_id"], "ONLINE"]
  ]
}
```

### `GET /server`

The installed IVG version, the IRIS version, the namespace from `IRIS_NAMESPACE`, and part of `engine.status()`.

```bash
curl http://localhost:8200/server
```

```json
{
  "ivg_version": "4.6.0",
  "iris_version": "IRIS for UNIX (Ubuntu Server LTS for ARM64 Containers) 2026.1 (Build 228U)",
  "namespace": "USER",
  "schema": { "nodes": 8904, "edges": 31000, "labels": 8904, "embeddings": 0 },
  "adjacency": { "kg_populated": true, "nkg_populated": false, "bfs_path": "objectscript" },
  "objectscript_deployed": true,
  "arno_loaded": false,
  "probe_ms": 4.2,
  "errors": []
}
```

`schema.labels` counts `rdf_labels` rows, not distinct labels. `ivg status` prints this answer.

### `GET /metrics`

Prometheus text format, from `engine.status()`. For ledger counters, see
[Ledger metrics](ledger-prometheus-hook.md).

```bash
curl http://localhost:8200/metrics
```

```text
# HELP ivg_nodes_total Total nodes in the graph
# TYPE ivg_nodes_total gauge
ivg_nodes_total 8904
# HELP ivg_edges_total Total edges in the graph
# TYPE ivg_edges_total gauge
ivg_edges_total 31000
# HELP ivg_embeddings_total Total node embeddings
# TYPE ivg_embeddings_total gauge
ivg_embeddings_total 0
# HELP ivg_kg_populated Whether ^KG adjacency index is built (0/1)
# TYPE ivg_kg_populated gauge
ivg_kg_populated 1
# HELP ivg_nkg_populated Whether ^NKG adjacency index is built (0/1)
# TYPE ivg_nkg_populated gauge
ivg_nkg_populated 0
# HELP ivg_status_probe_ms Time to collect status in milliseconds
# TYPE ivg_status_probe_ms gauge
ivg_status_probe_ms 4.20
```

### `GET /stats`

Nodes per label, node and edge counts, and the number of node embeddings.

```bash
curl http://localhost:8200/stats
```

```json
{
  "labelDistribution": { "Gene": 4200, "Disease": 2100, "Drug": 2604 },
  "nodeCount": 8904,
  "edgeCount": 31000,
  "embeddingCount": 0
}
```

### `GET /health`

Runs `MATCH (n) RETURN count(n)`. It answers `200` with `"status": "ok"` whether or not IRIS answered; read `engine`.

```bash
curl http://localhost:8200/health
```

```json
{ "status": "ok", "engine": true, "nodes": 8904 }
```

When the query fails: `{"status": "ok", "engine": false, "error": "<message>"}`. A load balancer that checks only the
status code cannot tell the two apart.

## Lifecycle operations

Each answers `{"status": "ok", ...}`, or `500` with the error in `detail`.

### `POST /admin/schema/init`

Runs `initialize_schema(auto_deploy_objectscript=...)` on the server's engine and returns its result under `result`.
The body's `embedding_dimension` is accepted and not used: the server's engine is built with the default width (768).
To set up a namespace at another width, run `initialize_schema()` from Python.

```bash
curl -X POST http://localhost:8200/admin/schema/init \
  -H "Content-Type: application/json" \
  -d '{"auto_deploy_objectscript": true}'
```

### `POST /admin/indexes/rebuild`

Rebuilds `^KG` and `^NKG` from `rdf_edges` (`rebuild_kg()` and `rebuild_nkg()`, the deprecated forms of `sync()`).
`ivg indexes rebuild` calls it.

```bash
curl -X POST http://localhost:8200/admin/indexes/rebuild
```

```json
{ "status": "ok", "kg": true, "nkg": true }
```

### `POST /admin/embed`

`embed_nodes(label=..., force=...)` in the default graph, with the engine's configured embedder.

```bash
curl -X POST http://localhost:8200/admin/embed \
  -H "Content-Type: application/json" \
  -d '{"label": "Gene", "force": false}'
```

### `POST /admin/load`

Writes the NDJSON body with `import_graph_ndjson`, into the default graph unless an edge line names a `graph`. The
`result` counts lines processed: `{"nodes": n, "edges": n, "temporal_edges": n}`. `ivg load <file>` and
`IVGClient.load_ndjson` call it.

```bash
curl -X POST http://localhost:8200/admin/load \
  -H "Content-Type: application/x-ndjson" \
  --data-binary @graph.ndjson
```

One object a line:

```ndjson
{"kind": "node", "id": "mesh:D003924", "labels": ["Disease"], "properties": {"name": "Diabetes mellitus"}}
{"kind": "edge", "source": "mesh:D003924", "predicate": "INTERACTS_WITH", "target": "mesh:D011014"}
{"kind": "temporal_edge", "source": "svc:a", "predicate": "CALLS", "target": "svc:b", "timestamp": 1700000000, "weight": 12.5}
```

An edge line may carry `graph` and `qualifiers`. Temporal edges go to the default graph. The format is in
[Data formats](setup/DATA_FORMATS.md).

### `GET /admin/export`

`export_graph_ndjson`, as a download. **It exports nodes only, no edges**, and only nodes the default graph holds
(R088 in 4.6.0's open defects).

```bash
curl http://localhost:8200/admin/export -o graph.ndjson
```

### `POST /admin/snapshot`

`save_snapshot` to `IVG_SNAPSHOT_DIR`, or with the variable unset to the system temporary directory, which a reboot
can empty ([known issue](KNOWN_ISSUES.md#snapshots-made-through-the-rest-api-land-in-the-temporary-directory)).

```bash
curl -X POST http://localhost:8200/admin/snapshot
```

```json
{ "status": "ok", "path": "/var/ivg/snapshots/ivg_snapshot_1747353600.snapshot", "result": {} }
```

## Running queries

### `GET /admin/queries`

The statements running in the namespace, at most 50, from `INFORMATION_SCHEMA.CURRENT_STATEMENTS`. `id` is the IRIS
process ID, `client` the user name, `command` the first 200 characters of the statement. If the lookup fails the
list is empty, not an error.

```bash
curl http://localhost:8200/admin/queries
```

```json
{
  "queries": [
    {
      "id": "12345",
      "state": "RUN",
      "client": "_SYSTEM",
      "command": "SELECT ... FROM Graph_KG.rdf_edges ..."
    }
  ]
}
```

### `DELETE /admin/queries/{id}`

Ends the IRIS process `id` (`%SYSTEM.SYS.KillProcess`), whatever it is running. `400` when IRIS refuses.

```bash
curl -X DELETE http://localhost:8200/admin/queries/12345
```

```json
{ "status": "ok", "killed": "12345" }
```

## Debugging

### `POST /admin/explain`

Translates Cypher to SQL without running it: the SQL, its parameters, the variable-length hops the translator found
and whether the statement writes. `400` with the parser's message for a query that does not parse. The SQL is the SQL
path's; a read the native executor answers does not run it.

```bash
curl -X POST http://localhost:8200/admin/explain \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $s RETURN b.node_id", "parameters": {"s": "mesh:D003924"}}'
```

The answer's keys are `cypher`, `sql`, `parameters`, `var_length_paths` and `is_transactional`.

## From Python and the shell

`IVGClient` (`from iris_vector_graph import IVGClient`) wraps the same routes: `schema()`, `server_info()`, `stats()`,
`explain(query)`, `load_ndjson(path)`, `ping()` (`/health`) and `execute_cypher(query, parameters)`.

<!-- doc-test: skip reason="needs `ivg server start` listening on 8200; the doc-test namespace runs no HTTP server" -->

```python
from iris_vector_graph import IVGClient

with IVGClient("http://localhost:8200") as client:
    print(client.server_info()["namespace"])
    print(client.execute_cypher("MATCH (n) RETURN count(n) AS c").rows)
```

The `ivg` command (from the `[cli]` extra) talks to `--url` (default `http://localhost:8200`, or `IVG_URL`):

```bash
ivg connect
ivg status
ivg schema status
ivg indexes list
ivg indexes rebuild
ivg query "MATCH (n) RETURN count(n)"
```

`ivg embeddings inventory` and the `ivg fhir` commands connect to IRIS directly with the `IRIS_*` variables, not
through the server ([FHIR graph](FHIR_GRAPH.md)).
