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
  `SYS`). With no `IRIS_HOST` it must run inside IRIS (embedded Python); anywhere else it answers every request with
  "No IRIS connection available".
- At start-up it also opens a Bolt listener on port 7687 for Neo4j drivers (`BOLT_TCP_PORT`; `BOLT_ENABLED=0` turns it
  off).

## Authentication

Setting `IVG_API_KEY` makes the server require the key on every route, the admin, discovery, Neo4j HTTP (`/db/...`)
and Cypher routes included. Send it in either header:

- `X-Api-Key: <key>`. `IVGClient`, `AsyncIVGClient` and the `ivg` command (`--api-key`, or `IVG_API_KEY` in the
  client's environment) send this one. It is the only header servers before 4.6.0 read, so a 4.6.0 client works with
  them.
- `Authorization: Bearer <key>`. Clients before 4.6.0 sent only this, as do tools that speak only Bearer, such as a
  Prometheus scrape of `/metrics` (`authorization: {credentials: <key>}` in the scrape config). A 4.6.0 server accepts
  it.

<!-- doc-test: skip reason="needs `ivg server start` listening on 8200 with IVG_API_KEY set; the doc-test namespace runs no HTTP server" -->

```bash
curl -H "X-Api-Key: $IVG_API_KEY" http://localhost:8200/stats
```

A missing or wrong key gets `401` and `{"error": "unauthorized", "message": "X-Api-Key header required"}`, before any
handler runs and before the `IVG_READ_ONLY` check.

Only `/health` and the API docs (`/docs`, `/redoc` and the OpenAPI document, `/openapi` on this server) answer without
the key. The match is on the exact path: `/health/` and `/Health` need the key; a query string does not change the
path. A route added to the server later needs the key unless it is added to that list
(`iris_vector_graph.api_auth.PUBLIC_PATHS`).

- Every HTTP method needs it, `HEAD` and `OPTIONS` too. A browser's CORS preflight is answered without it, and runs no
  handler.
- WebSocket upgrades need the header too, except Bolt over WebSocket at `/`: Bolt sends the key inside the protocol, as
  the `credentials` of `HELLO` (Bolt 4, and Bolt 5.0 clients) or `LOGON` (Bolt 5.1 and later). The TCP Bolt listener
  works the same way. A Bolt session that has not presented the key can run nothing. A refused key, in `HELLO` or
  `LOGON`, ends the session's authentication even if an earlier `LOGON` had the right key, and `RESET` does not restore
  it: send `LOGON` with the key again.
- `/browser`, the graph browser's static files, needs the key like any other path.
- The key is compared in constant time. Use an ASCII key: HTTP header values are not UTF-8.

## Read-only mode

`IVG_READ_ONLY=true` makes the server refuse every write, after the key check (a request without the key still gets
`401`):

- Every `POST`, `PUT`, `PATCH` and `DELETE` under `/admin/` gets `403` and
  `{"error": "read_only_mode", "detail": "..."}`, and runs nothing: `/admin/schema/init`, `/admin/indexes/rebuild`,
  `/admin/embed`, `/admin/load`, `/admin/snapshot` and `DELETE /admin/queries/{id}`. `POST /admin/explain` only
  translates, and still answers. An `/admin/` route added later is refused until it is named a read.
- Cypher that names a write (`CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `FOREACH`) gets `403` on `/api/cypher` and
  `/db/{db}/query/v2`. A `/db/neo4j/tx/commit` request with one such statement runs none of its statements, and answers
  `403` with `Neo.ClientError.Statement.AccessMode` in `errors`. Bolt answers that `RUN` with a `FAILURE` carrying the
  same code. Read queries run as before.
- The `GET` routes, `/admin/export` included, are unaffected.

## Discovery and observability

Every example below uses `curl`; the bodies shown are examples of the shape, from a graph with three labels.

### `GET /schema`

Labels, relationship types, property keys, node and edge counts, and nodes per label
(`get_labels`, `get_relationship_types`, `get_property_keys`, `get_node_count`, `get_edge_count`,
`get_label_distribution`). The labels, types and keys are every graph's; the counts and nodes per label are the default
graph's (from 4.6.0; before, every graph's).

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

The answer of the Cypher `SHOW INDEXES`, which Neo4j Browser and LangChain send on connect: the `^KG` and `^NKG`
adjacency indexes (`ONLINE`, or `NOT_BUILT`), an HNSW index on `kg_NodeEmbeddings` or `kg_NodeEmbeddings_optimized`
when one exists, every BM25, IVF and PLAID index by name (`FULLTEXT(BM25)`, `VECTOR(IVF)`, `VECTOR(PLAID)`), and fixed
rows for the node and edge keys. It lists no per-route HNSW index. Before 4.6.0 no BM25, IVF or PLAID index was listed.

```bash
curl http://localhost:8200/indexes
```

```json
{
  "columns": ["name", "type", "entityType", "labelsOrTypes", "properties", "state"],
  "indexes": [
    ["nkg_adjacency", "ADJACENCY(^NKG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["kg_adjacency", "ADJACENCY(^KG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["pk_nodes", "UNIQUE", "NODE", ["*"], ["node_id"], "ONLINE"],
    ["pk_rdf_edges", "UNIQUE", "RELATIONSHIP", ["*"], ["s", "p", "o_id"], "ONLINE"]
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

Nodes per label, node and edge counts (the default graph's, from 4.6.0), and the number of node embeddings.

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

Runs `MATCH (n) RETURN count(n)` and answers status only, with no key needed: `200` and `{"status": "ok"}` when IRIS
answered, `503` and `{"status": "unavailable"}` when it did not. A load balancer can read the status code. The body
carries no count and no error text; the server logs the failure at `WARNING` (logger `iris_vector_graph.cypher_api`).
Use `/stats` or `/server`, which need the key, for counts.

```bash
curl http://localhost:8200/health
```

```json
{ "status": "ok" }
```

Before 4.6.0 it answered `200` and `"status": "ok"` either way, with the node count, or `"engine": false` and the
exception text.

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

`export_graph_ndjson`, as a download: the default graph's nodes and edges, which `POST /admin/load` reads back.
Named graphs and temporal edges are not in it.

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
process ID, `client` the user name, `command` the first 200 characters of the statement. If the lookup fails, the
server answers an empty list rather than an error.

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
