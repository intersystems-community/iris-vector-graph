# IVG Admin API Reference

IVG exposes an admin and observability surface over HTTP. All endpoints are served by the Cypher API server (`cypher_api.py`) alongside the standard query endpoints.

---

## Discovery & Observability

### `GET /schema`

Returns the current graph schema: labels, relationship types, property keys, node/edge counts, and label distribution.

```bash
curl http://localhost:8200/schema
```

```json
{
  "labels": ["Gene", "Disease", "Drug"],
  "relationshipTypes": ["INTERACTS_WITH", "CAUSES", "TREATS"],
  "propertyKeys": ["name", "organism", "score"],
  "nodeCount": 8904,
  "edgeCount": 31000,
  "labelDistribution": {"Gene": 4200, "Disease": 2100, "Drug": 2604}
}
```

### `GET /indexes`

Returns the full index inventory: HNSW, IVF, BM25, PLAID, ^NKG, ^KG adjacency indexes, and SQL unique constraints.

```bash
curl http://localhost:8200/indexes
```

```json
{
  "columns": ["name", "type", "entityType", "labelsOrTypes", "properties", "state"],
  "indexes": [
    ["hnsw_node_embeddings", "VECTOR(HNSW)", "NODE", ["*"], ["emb"], "ONLINE"],
    ["nkg_adjacency", "ADJACENCY(^NKG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["kg_adjacency", "ADJACENCY(^KG)", "RELATIONSHIP", ["*"], ["*"], "ONLINE"],
    ["pk_nodes", "UNIQUE", "NODE", ["*"], ["node_id"], "ONLINE"]
  ]
}
```

Also exposed as Cypher: `SHOW INDEXES` — used automatically by Neo4j Browser, LangChain, and Neo4j-compatible tools on connect.

### `GET /server`

Returns server version, IRIS version, namespace, schema status, and BFS path selection.

```bash
curl http://localhost:8200/server
```

```json
{
  "ivg_version": "1.96.0",
  "iris_version": "IRIS for UNIX (Apple M3 Ultra) 2026.2 (Build 161)",
  "namespace": "IVG",
  "schema": {"nodes": 8904, "edges": 31000, "labels": 12453, "embeddings": 8904},
  "adjacency": {"kg_populated": true, "nkg_populated": true, "bfs_path": "arno"},
  "objectscript_deployed": true,
  "arno_loaded": true,
  "probe_ms": 4.2,
  "errors": []
}
```

### `GET /metrics`

Returns Prometheus-format metrics for scraping.

```bash
curl http://localhost:8200/metrics
```

```
# HELP ivg_nodes_total Total nodes in the graph
# TYPE ivg_nodes_total gauge
ivg_nodes_total 8904
# HELP ivg_edges_total Total edges in the graph
# TYPE ivg_edges_total gauge
ivg_edges_total 31000
# HELP ivg_embeddings_total Total node embeddings
# TYPE ivg_embeddings_total gauge
ivg_embeddings_total 8904
# HELP ivg_kg_populated Whether ^KG adjacency index is built (0/1)
# TYPE ivg_kg_populated gauge
ivg_kg_populated 1
# HELP ivg_nkg_populated Whether ^NKG adjacency index is built (0/1)
# TYPE ivg_nkg_populated gauge
ivg_nkg_populated 1
# HELP ivg_status_probe_ms Time to collect status in milliseconds
# TYPE ivg_status_probe_ms gauge
ivg_status_probe_ms 4.20
```

### `GET /stats`

Returns node/edge counts by label, plus embedding coverage.

```bash
curl http://localhost:8200/stats
```

```json
{
  "labelDistribution": {"Gene": 4200, "Disease": 2100},
  "nodeCount": 8904,
  "edgeCount": 31000,
  "embeddingCount": 8904
}
```

### `GET /health`

Lightweight connection check. Used by Docker healthchecks and load balancers.

```bash
curl http://localhost:8200/health
```

```json
{"status": "ok", "node_count": 8904}
```

---

## Lifecycle Operations

All `/admin/*` endpoints require the API key if auth is enabled.

### `POST /admin/schema/init`

Initialize the `Graph_KG.*` SQL schema tables. Safe to call on a running instance — idempotent (`CREATE TABLE IF NOT EXISTS`).

```bash
curl -X POST http://localhost:8200/admin/schema/init \
  -H "Authorization: Bearer $IVG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"embedding_dimension": 768, "auto_deploy_objectscript": false}'
```

### `POST /admin/indexes/rebuild`

Rebuild the `^KG` and `^NKG` globals-based adjacency indexes from the `rdf_edges` SQL table. Run after bulk-loading graph data to restore BFS acceleration.

```bash
curl -X POST http://localhost:8200/admin/indexes/rebuild \
  -H "Authorization: Bearer $IVG_API_KEY"
```

```json
{"status": "ok", "kg": true, "nkg": true}
```

### `POST /admin/embed`

Trigger node embedding for all unembedded nodes (or a specific label).

```bash
curl -X POST http://localhost:8200/admin/embed \
  -H "Authorization: Bearer $IVG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "Gene", "force": false}'
```

### `POST /admin/load`

Stream an NDJSON file of graph events into the database.

```bash
curl -X POST http://localhost:8200/admin/load \
  -H "Authorization: Bearer $IVG_API_KEY" \
  -H "Content-Type: application/x-ndjson" \
  --data-binary @graph.ndjson
```

NDJSON format — one event per line:
```json
{"kind": "node", "id": "mesh:D003924", "labels": ["Disease"], "properties": {"name": "Diabetes mellitus"}}
{"kind": "edge", "source": "mesh:D003924", "predicate": "INTERACTS_WITH", "target": "mesh:D011014", "properties": {}}
```

### `GET /admin/export`

Export the entire graph as NDJSON. Returns a streaming download.

```bash
curl http://localhost:8200/admin/export \
  -H "Authorization: Bearer $IVG_API_KEY" \
  -o graph.ndjson
```

### `POST /admin/snapshot`

Save a point-in-time snapshot to the server's snapshot directory (`IVG_SNAPSHOT_DIR` env var, defaults to `/tmp`).

```bash
curl -X POST http://localhost:8200/admin/snapshot \
  -H "Authorization: Bearer $IVG_API_KEY"
```

```json
{"status": "ok", "path": "/tmp/ivg_snapshot_1747353600.snapshot"}
```

---

## Query Management

### `GET /admin/queries`

List active IRIS queries (via `%SYS.ProcessQuery`). Useful for identifying slow or hung queries.

```bash
curl http://localhost:8200/admin/queries \
  -H "Authorization: Bearer $IVG_API_KEY"
```

```json
{
  "queries": [
    {"id": "12345", "state": "RUN", "client": "127.0.0.1", "command": "SELECT * FROM Graph_KG.rdf_edges ..."}
  ]
}
```

### `DELETE /admin/queries/{id}`

Kill a running query by IRIS process ID.

```bash
curl -X DELETE http://localhost:8200/admin/queries/12345 \
  -H "Authorization: Bearer $IVG_API_KEY"
```

```json
{"status": "ok", "killed": "12345"}
```

---

## Debugging

### `POST /admin/explain`

Translate a Cypher query to SQL without executing it. Shows the generated SQL, parameters, and routing metadata.

```bash
curl -X POST http://localhost:8200/admin/explain \
  -H "Authorization: Bearer $IVG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $s RETURN b.node_id",
    "parameters": {"s": "mesh:D003924"}
  }'
```

```json
{
  "cypher": "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $s RETURN b.node_id",
  "sql": null,
  "parameters": [["mesh:D003924"]],
  "var_length_paths": [{"min_hops": 1, "max_hops": 3, "direction": "out"}],
  "is_transactional": false
}
```

When `var_length_paths` is set, the query routes to `store.execute_bfs()` rather than generating SQL.

---

## SDK Access

All admin endpoints are accessible via the `IVGClient` SDK:

```python
from iris_vector_graph import IVGClient

with IVGClient("http://localhost:8200", api_key="...") as client:
    print(client.schema())
    print(client.server_info())
    print(client.stats())
    print(client.explain("MATCH (n) RETURN count(n)"))
    client.load_ndjson("graph.ndjson")
```

---

## CLI Access

```bash
ivg --url http://localhost:8200 --api-key $IVG_API_KEY schema status
ivg indexes list
ivg indexes rebuild
```
