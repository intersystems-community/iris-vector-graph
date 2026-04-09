# Spec 045: Bolt WebSocket Server

## Overview

Add a Bolt 4.4 protocol server to `cypher_api.py` so that **Neo4j Browser**, **Neo4j Desktop**, **LangChain Neo4jGraph**, and any standard Neo4j driver can connect to IVG using `bolt://` or `neo4j://` URIs without configuration changes.

Bolt is the published binary protocol used by all Neo4j tooling. The spec is at `https://neo4j.com/docs/bolt/current/` under Creative Commons license. The serialization format is PackStream, also fully specified. Neo4j Browser 5 is Bolt-only — HTTP fallback was removed in v5. Implementing Bolt 4.4 unlocks the entire Neo4j ecosystem.

## Problem Statement

- Neo4j Browser 5 (served at `/browser/`) always tries WebSocket/Bolt and has no HTTP fallback.
- Every attempt to use Neo4j tools (Desktop, Browser, cypher-shell, LangChain) fails with `ServiceUnavailable: WebSocket connection failure`.
- The HTTP transactional API we implemented works for curl/httpie/direct HTTP clients but nothing in the Neo4j toolchain.

## Functional Requirements

### FR-001: Bolt Handshake
The server MUST accept WebSocket connections and perform the Bolt version negotiation handshake:
- Accept WebSocket upgrade on port 8000 at path `/` (Bolt over WebSocket)
- Read 20 bytes: magic `0x6060B017` + four 4-byte version proposals
- Respond with 4 bytes: chosen version `0x00000404` (Bolt 4.4) if proposed; `0x00000000` otherwise
- If no shared version, close the connection cleanly

### FR-002: PackStream Encoding/Decoding
The server MUST encode and decode messages using PackStream v1:
- Null, Boolean, Integer (tiny/int8/int16/int32/int64), Float (float64)
- String (tiny/string8/string16/string32)
- List (tiny/list8/list16/list32)
- Map (tiny/map8/map16/map32)
- Structure (tiny struct with tag byte)

### FR-003: Bolt Message Chunking
Messages MUST be transmitted using the Bolt chunked encoding:
- Each message is split into chunks: 2-byte big-endian chunk size + chunk data
- A message is terminated by a zero-length chunk (0x00 0x00)

### FR-004: HELLO Message
Server MUST handle `HELLO` (tag `0x01`):
- Accept `{user_agent, routing, bolt_agent}` dict
- Respond `SUCCESS {server: "iris-vector-graph/1.46.0", connection_id: "<uuid>", hints: {}}`
- Authentication: if `IVG_API_KEY` is set, validate against `credentials` dict field or `Authorization` field; if not set, accept all

### FR-005: RUN + PULL
Server MUST handle the auto-commit transaction pattern:
- `RUN` (tag `0x10`): `{query, parameters, extra}` → `SUCCESS {fields: [...], qid: 0, t_first: 0}`
- `PULL` (tag `0x3F`): `{n: -1, qid: -1}` → N × `RECORD [...]` + `SUCCESS {type: "r", t_last: 0, bookmark: "..."}`

### FR-005a: Graph Object Encoding
For graph visualization in Neo4j Browser, queries returning nodes/relationships MUST encode them as PackStream Structures:
- Node: tag `0x4E`, fields `[id::int, labels::list<string>, properties::map]`
- Relationship: tag `0x52`, fields `[id::int, start_node_id::int, end_node_id::int, type::string, properties::map]`
- When a RETURN column contains a node ID (string), the server MUST fetch the full node (labels + properties) and encode as a Node structure.
- When a RETURN column contains an edge, encode as Relationship structure.
- Scalar values (strings, ints, floats) are encoded as plain PackStream types.

### FR-006: RESET + GOODBYE
- `RESET` (tag `0x0F`): return server to READY state, respond `SUCCESS {}`
- `GOODBYE` (tag `0x02`): close connection cleanly

### FR-007: Error Handling
- Invalid Cypher: respond `FAILURE {"code": "Neo.ClientError.Statement.SyntaxError", "message": "..."}`
- After FAILURE, server enters FAILED state; `RESET` returns to READY
- `IGNORED` response for messages received in FAILED state

### FR-008: Concurrent Connections
- Multiple simultaneous WebSocket connections MUST work independently
- Each connection has its own state machine (CONNECTED → READY → STREAMING → ...)

### FR-009: Neo4j Browser Auto-Connect
- Discovery doc at `GET /` and `GET /db/neo4j` MUST return `bolt_direct: "bolt://localhost:8000"`
- Neo4j Browser served at `/browser/` with `?connectURL=bolt://localhost:8000` pre-filled

### FR-010: API Key Auth in Bolt
- If `IVG_API_KEY` is set, validate in `HELLO` message credentials: `{principal: "", credentials: "<api_key>"}`
- Return `FAILURE` with `Neo.ClientError.Security.Unauthorized` if wrong key

## Non-Functional Requirements

### NFR-001: Performance
- RUN + PULL for a 1000-row result MUST complete in < 2s (bolt overhead only, not IRIS query time)
- Handshake MUST complete in < 100ms

### NFR-002: Compatibility
- MUST work with Neo4j Browser 5.26.x (the version bundled in our `/browser/` endpoint)
- MUST work with `neo4j` Python driver v5.x (`from neo4j import GraphDatabase`)
- SHOULD work with cypher-shell 5.x

### NFR-003: Scope
- Bolt 4.4 only — no routing, no clusters, no bookmarks beyond stub
- No TLS — plain WebSocket ws:// and TCP bolt:// (TLS deferred)
- Single database (`neo4j`) — multi-database deferred

## User Stories

### US-1: Neo4j Browser Graph Visualization
As a developer, I want to open `http://localhost:8000/browser/` and run `MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 50` and see a force-directed graph visualization of IVG data.

**Acceptance criteria:**
- Browser loads at `/browser/`
- Clicking Connect (URL pre-filled as `bolt://localhost:8000`) succeeds
- Query returns and renders nodes + relationships as graph

### US-2: Python Driver Integration
As a developer, I want to use the official `neo4j` Python driver to query IVG:
```python
from neo4j import GraphDatabase
driver = GraphDatabase.driver("bolt://localhost:8000", auth=("", ""))
with driver.session() as s:
    result = s.run("MATCH (n) RETURN count(n) AS c")
    print(result.single()["c"])
```
**Acceptance criteria:** above code executes without error and returns correct count.

### US-3: LangChain Neo4jGraph
As an AI developer, I want to use LangChain's `Neo4jGraph` with IVG:
```python
from langchain_community.graphs import Neo4jGraph
graph = Neo4jGraph(url="bolt://localhost:8000", username="", password="")
print(graph.query("MATCH (n) RETURN count(n) AS c"))
```
**Acceptance criteria:** above code works.

## Out of Scope
- Bolt 5.x (different HELLO/LOGON split)
- Explicit transactions (BEGIN/COMMIT/ROLLBACK)
- Routing protocol / causal clustering
- TLS / bolt+s://
- cypher-shell compatibility (uses TCP socket not WebSocket — deferred to separate spec)
- Named databases beyond "neo4j"
