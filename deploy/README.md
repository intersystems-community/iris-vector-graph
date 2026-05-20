# IVG Deploy

Choose your setup path:

## Path 1: Fresh IRIS in Docker (5 minutes)

```bash
cd deploy/docker
export IVG_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export IVG_IRIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
docker compose -f compose.yml up -d
```

IVG available at `http://localhost:8200`. Connect with `IVG_API_KEY`.

## Path 2: Bolt onto existing IRIS

```bash
pip install "iris-vector-graph[full]"
cd deploy/bolt-on
./install.sh myiris.company.com 1972 IVG Admin
```

Prompts for admin password, creates namespace, installs schema, outputs API key and start command.

## Path 3: Existing namespace with data

```bash
pip install "iris-vector-graph[full]"
IVG_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Start server pointing at existing namespace
IVG_API_KEY=$IVG_API_KEY \
IVG_IRIS_PASSWORD=your_password \
ivg server start \
  --iris-host myiris.com \
  --iris-port 1972 \
  --iris-namespace MYAPP \
  --port 8200
```

The server detects existing `Graph_KG.*` tables and uses them without re-initializing.

## Path 4: Embedded inside IRIS (no external server)

See `iris_src/src/IVG/CypherEngine.cls` — IVG runs as Language=python inside IRIS.
The MCP server (spec 031) uses this path. No external Python process required.

## Querying the server

```bash
# Connect
ivg --url http://localhost:8200 --api-key $IVG_API_KEY connect

# Query
ivg query "MATCH (n:Gene) RETURN n.node_id LIMIT 5"

# AQL
ivg query --aql "FOR v IN 1..2 OUTBOUND @s g RETURN v._key" --bind s=mesh:D003924

# Check indexes
ivg indexes list

# Rebuild adjacency indexes
ivg indexes rebuild
```

## SDK usage (no intersystems-irispython required)

```python
from iris_vector_graph import IVGClient

with IVGClient("http://localhost:8200", api_key="your-key") as client:
    result = client.execute_cypher(
        "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $s RETURN b.node_id",
        parameters={"s": "mesh:D003924"}
    )
    for r in result.records:
        print(r["node_id"])
```

## Security notes

- The API key is the only auth layer for external callers. Keep it secret.
- The `ivg_service` IRIS user (created by bolt-on installer) has access only to `Graph_KG.*` tables. Even if the API key is leaked, the attacker cannot access other IRIS data.
- Set `auth: none` only for local dev. The server refuses `auth: none` with `host: 0.0.0.0` unless `--force-no-auth` is passed.
- For production, put IVG behind a reverse proxy (nginx/caddy) and use TLS.
