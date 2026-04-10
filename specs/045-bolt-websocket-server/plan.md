# Plan: Bolt WebSocket Server (spec 045)

**Branch**: `044-bm25-index` (add to existing branch)
**Target**: v1.47.0

## Architecture

Single new file `iris_vector_graph/bolt_server.py` (~500 lines) added alongside `cypher_api.py`. The FastAPI app mounts a WebSocket route at `/` that dispatches to `BoltSession`. Each WebSocket connection gets its own `BoltSession` instance with independent state.

```
iris_vector_graph/
├── cypher_api.py          — HTTP API (existing) + bolt WebSocket route (add @app.websocket)
├── bolt_server.py         — NEW: PackStream codec + BoltSession state machine
└── engine.py              — unchanged
```

No new dependencies — uses only Python stdlib (`struct`, `asyncio`) + `websockets` (already installed via FastAPI/uvicorn).

## Tech Stack
- Python 3.11 asyncio WebSocket (via FastAPI `WebSocket`)
- PackStream v1 — pure Python, no deps
- Bolt 4.4 over WebSocket (ws://)
- Neo4j Browser 5.26 compatibility target

## Module Design

### `bolt_server.py`

```
PackStream codec
├── pack(value) → bytes
└── unpack(data) → (value, offset)

ChunkCodec
├── encode_message(msg_bytes) → chunked_bytes
└── decode_chunks(raw_bytes) → list[bytes]  (each = one message)

BoltSession(websocket, get_engine_fn)
├── state: CONNECTED | READY | STREAMING | FAILED | DEFUNCT
├── run() → coroutine (main loop)
├── _handle_hello(fields) → send SUCCESS or FAILURE
├── _handle_run(query, params, extra) → execute + send SUCCESS{fields}
├── _handle_pull(extra) → stream RECORDs + send SUCCESS
├── _handle_reset() → send SUCCESS, return READY
└── _handle_goodbye() → close

GraphEncoder
├── encode_row(columns, row, engine) → list[PackStream-encoded values]
├── _fetch_node(node_id, engine) → Node structure bytes
└── _fetch_edge(edge_data, engine) → Relationship structure bytes
```

### Integration in `cypher_api.py`

```python
from iris_vector_graph.bolt_server import BoltSession

@app.websocket("/")
async def bolt_ws(ws: WebSocket):
    session = BoltSession(ws, _get_engine)
    await session.run()
```

## Bolt 4.4 Handshake (over WebSocket)

```
Client → raw bytes: 60 60 B0 17  (magic)
Client → raw bytes: 00 04 04 04  (version range 4.4-4.0, packed as minor=4 range=4 major=4)
                    00 00 04 04  (version 4.4 explicit)
                    00 00 00 03  (version 3)
                    00 00 00 00  (padding)
Server → raw bytes: 00 00 04 04  (chosen: Bolt 4.4)
```

The WebSocket transport is transparent — raw binary frames, no wrapping.

## Message Flow

```
C: HELLO {user_agent:"neo4j-browser/5.26", routing:{...}, credentials:"<api_key>"}
S: SUCCESS {server:"iris-vector-graph/1.47.0", connection_id:"<uuid>"}

C: RUN "MATCH (n) RETURN count(n) AS c" {} {mode:"r", db:"neo4j"}
S: SUCCESS {fields:["c"], qid:0, t_first:1}

C: PULL {n:1000, qid:-1}
S: RECORD [42]
S: SUCCESS {type:"r", t_last:1, bookmark:"ivg:1", db:"neo4j"}

C: GOODBYE
(close)
```

## PackStream Types Needed

| Type | Tag range | Notes |
|------|-----------|-------|
| Null | `0xC0` | |
| True | `0xC3` | |
| False | `0xC2` | |
| TinyInt | `0x00`-`0x7F`, `0xF0`-`0xFF` | |
| Int8 | `0xC8` | |
| Int16 | `0xC9` | |
| Int32 | `0xCA` | |
| Int64 | `0xCB` | |
| Float64 | `0xC1` | |
| TinyString | `0x80`-`0x8F` | len ≤ 15 |
| String8 | `0xD0` | len ≤ 255 |
| String16 | `0xD1` | |
| String32 | `0xD2` | |
| TinyList | `0x90`-`0x9F` | |
| List8 | `0xD4` | |
| TinyMap | `0xA0`-`0xAF` | |
| Map8 | `0xD8` | |
| TinyStruct | `0xB0`-`0xBF` | size ≤ 15 |

## Graph Object Structures (Bolt 4.4)

| Type | Tag | Fields |
|------|-----|--------|
| Node | `0x4E` | `[id:Int, labels:List<String>, properties:Map]` |
| Relationship | `0x52` | `[id:Int, startNodeId:Int, endNodeId:Int, type:String, properties:Map]` |
| Path | `0x50` | `[nodes:List<Node>, rels:List<Rel>, sequence:List<Int>]` |

Node IDs: use a hash of the string node_id to produce a stable integer (Python `hash(node_id) & 0x7FFFFFFF`).

## Graph Detection Heuristic

When `RETURN` column name matches a query pattern node variable (single letter or short name) AND the row value is a string that matches a known node_id in the graph, encode as Node structure. This works for:
- `MATCH (n) RETURN n` → encode n as Node
- `MATCH (n)-[r]->(m) RETURN n, r, m` → encode as Node, Relationship, Node
- `MATCH (n) RETURN n.id` → encode as String (property access, not node)

Detection: if the query metadata says column is a node/rel type (from `fields` metadata — which we add), or if column name == a variable name in the MATCH pattern that isn't property-accessed.

Simpler approach: detect by checking if the RETURN expression has no `.` property access — then it's likely a full node/rel. Implement: parse RETURN clause for `n.prop` vs just `n`.

## Phases

### Phase 1 — PackStream + Chunking (T001-T010)
Pure Python, no IRIS needed. Unit testable in isolation.

### Phase 2 — BoltSession State Machine (T011-T020)
HELLO → READY → STREAMING. No graph objects yet — plain scalar responses.

### Phase 3 — Graph Object Encoding (T021-T028)
Node and Relationship structures. Neo4j Browser visualization.

### Phase 4 — Integration + Discovery Update (T029-T033)
Wire into `cypher_api.py`. Update `bolt_direct` in discovery doc.

### Phase 5 — Tests + Validation (T034-T040)
Unit tests for PackStream, integration test with `neo4j` Python driver.
