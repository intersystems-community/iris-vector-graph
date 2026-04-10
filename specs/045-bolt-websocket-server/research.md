# Research: Bolt WebSocket Server

## Decision 1 — Bolt version: 4.4

**Chosen**: Bolt 4.4
**Rationale**: Neo4j Browser 5.26 proposes `[5.4, 5.3, 5.2, 4.4]` in its handshake. Bolt 5.1+ separates HELLO into HELLO+LOGON (two-phase auth), requiring more state machine complexity. Bolt 4.4 has a single HELLO with credentials — much simpler. The Browser fully supports 4.4. Python driver neo4j 5.x supports 4.4. This gives us full ecosystem compatibility with ~30% less code than v5.

**Alternatives**: Bolt 5.4 — rejected, requires LOGON message and different state machine.

## Decision 2 — Transport: WebSocket only (no TCP)

**Chosen**: WebSocket at `ws://localhost:8000/` only
**Rationale**: Neo4j Browser uses WebSocket. The `neo4j` Python driver also has a WebSocket transport mode (used when connecting via `http://`). TCP Bolt (port 7687) is needed for cypher-shell but that requires a separate TCP listener outside FastAPI. Defer TCP to a future spec.
**Alternatives**: TCP on 7687 — deferred (different spec, requires raw asyncio TCP server).

## Decision 3 — PackStream: implement from scratch

**Chosen**: Pure Python ~150 lines
**Rationale**: No published Python PackStream library exists. `asyncbolt` (2017, Bolt v1-3) has a PackStream implementation but it's stale and targets a different Bolt version. Writing it clean is faster than adapting old code.

## Decision 4 — Node ID mapping: hash-based integer

**Chosen**: `hash(node_id_string) & 0x7FFFFFFF`
**Rationale**: Bolt Node structures require an integer ID. IVG uses string node IDs. We need a deterministic mapping. Python's `hash()` with a positive mask gives a stable-within-session integer. Cross-session stability isn't required (Neo4j itself doesn't guarantee stable node IDs across restarts).
**Alternatives**: Sequential counter per session — rejected (breaks graph identity when same node appears in multiple results).

## Decision 5 — Graph detection: column-name heuristic

**Chosen**: Parse RETURN clause — if a return item is a bare variable name (no `.` property access), look it up as a node or edge.
**Rationale**: The Cypher translator already knows which columns are node IDs (columns named `n_id`, `n0_id`, etc.) vs properties. We can detect by checking if the column name ends with `_id` and correlate back.

Simpler: add a `result_types` metadata field to `execute_cypher` results — `{"columns": ["n", "r", "m"], "column_types": ["node", "rel", "node"], "rows": [...]}` — and use that in the Bolt encoder.

**Alternatives**: Always return scalars (no graph objects) — rejected, browser shows table not graph.

## Decision 6 — Auth: credentials field in HELLO

**Chosen**: Check `HELLO.extra["credentials"]` against `IVG_API_KEY`
**Rationale**: Bolt 4.4 HELLO carries `{principal, credentials, scheme, ...}`. We use `credentials` as the API key. If `IVG_API_KEY` is empty string, accept anything (dev mode).

## Decision 7 — Column type detection for graph encoding

**Final approach**: Inspect the SQL result metadata. The `execute_cypher` function already knows which columns are nodes (from Stage CTE aliases like `n0`, `n1`) vs scalars. We add a `_bolt_column_types` metadata field:
- `"node"` — column is a node_id, should be encoded as Node structure
- `"rel"` — column is an edge (type, source, target), encode as Relationship
- `"scalar"` — plain value

This is added to `IRISGraphEngine.execute_cypher` return value as an optional field, defaulting to all `"scalar"` if not present.
