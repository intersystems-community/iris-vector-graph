# Tasks: Bolt WebSocket Server (spec 045)

---

## Phase 1 — PackStream Codec (TDD first)

- [ ] **T001** Write unit tests for PackStream pack: null, bool, int (tiny/8/16/32/64), float64, string (tiny/8), list (tiny), map (tiny)
- [ ] **T002** Write unit tests for PackStream unpack (round-trip for all types above)
- [ ] **T003** Write unit tests for chunk encoding: single message, multi-chunk message, zero-length terminator
- [ ] **T004** Write unit tests for chunk decoding: reassemble complete messages from raw bytes
- [ ] **T005** Implement `PackStream.pack(value) → bytes` in `bolt_server.py`
- [ ] **T006** Implement `PackStream.unpack(data, offset=0) → (value, new_offset)` in `bolt_server.py`
- [ ] **T007** Implement `encode_message(msg_bytes) → bytes` (chunked)
- [ ] **T008** Implement `decode_messages(raw_bytes) → list[bytes]` (unchunked)
- [ ] **T009** Implement `encode_bolt_message(tag, *fields) → bytes` (pack struct + chunk)
- [ ] **T010** **E2E GATE Phase 1**: Run T001-T004 — ALL must pass before Phase 2

---

## Phase 2 — Bolt Handshake

- [ ] **T011** Write unit test for handshake: client sends magic + proposals, server picks 4.4
- [ ] **T012** Write unit test for handshake: no shared version → server sends 0x00000000
- [ ] **T013** Implement `do_handshake(websocket) → int` — reads 20 bytes, responds with chosen version
- [ ] **T014** **E2E GATE Phase 2**: Run T011-T012 — ALL must pass before Phase 3

---

## Phase 3 — BoltSession State Machine

- [ ] **T015** Write unit tests for HELLO → SUCCESS (valid credentials)
- [ ] **T016** Write unit tests for HELLO → FAILURE (wrong credentials when IVG_API_KEY set)
- [ ] **T017** Write unit tests for RUN → SUCCESS{fields} + PULL → RECORD×N + SUCCESS
- [ ] **T018** Write unit tests for RESET → SUCCESS (FAILED → READY); IGNORED in FAILED state
- [ ] **T019** Implement `BoltSession` class with `run()` coroutine
  - `_do_handshake()` → negotiate version
  - `_recv_message()` → read + decode one Bolt message
  - `_send_message(tag, *fields)` → encode + send one Bolt message
  - `_handle_hello(fields)`
  - `_handle_run(query, params, extra)`
  - `_handle_pull(extra)`
  - `_handle_reset()`
  - `_handle_goodbye()`
  - State machine: CONNECTED → READY → STREAMING → READY | FAILED
- [ ] **T020** **E2E GATE Phase 3**: Run T015-T018 — ALL must pass before Phase 4

---

## Phase 4 — Graph Object Encoding

- [ ] **T021** Write unit test for Node structure: `pack_node("NCIT:C001", ["Gene"], {"name":"BRCA1"})` → correct PackStream bytes
- [ ] **T022** Write unit test for Relationship structure encoding
- [ ] **T023** Implement `pack_node(node_id, labels, properties) → bytes`
- [ ] **T024** Implement `pack_relationship(rel_id, start_id, end_id, rel_type, properties) → bytes`
- [ ] **T025** Implement `GraphEncoder.encode_row(columns, row, col_types, engine) → list`
  - For columns with type `"node"`: fetch node labels+props from IRIS, encode as Node struct
  - For columns with type `"rel"`: encode as Relationship struct
  - For scalars: pass through as PackStream value
- [ ] **T026** Add `_bolt_column_types` to `execute_cypher` metadata: bare variable name (no `.` property access) → `"node"`, edge variable → `"rel"`, else `"scalar"`
- [ ] **T027** Write unit test for full MATCH (n)-[r]->(m) round-trip: returns 3 PackStream structures per row
- [ ] **T028** **E2E GATE Phase 4**: Run T021-T022, T027 — ALL must pass before Phase 5

---

## Phase 5 — Integration

- [ ] **T029** Wire `BoltSession` into `cypher_api.py`:
  - Replace stub `@app.websocket("/")` with real BoltSession dispatch
  - Import `bolt_server.BoltSession`
- [ ] **T030** Update discovery doc: set `bolt_direct: "bolt://localhost:8000"` (actual host:port from env)
- [ ] **T031** Update `/browser/` default URL: serve index.html with `?connectURL=bolt://localhost:8000` injected
- [ ] **T032** **E2E GATE Phase 5 (manual)**: Neo4j Browser at `http://localhost:8000/browser/` connects and renders graph for `MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 25`

---

## Phase 6 — Tests + Regression

- [ ] **T033** **E2E GATE**: `neo4j` Python driver: `GraphDatabase.driver("bolt://localhost:8000", auth=("",""))` → `session.run("MATCH (n) RETURN count(n) AS c").single()` returns correct integer
- [ ] **T033b** **E2E GATE**: LangChain: `Neo4jGraph(url="bolt://localhost:8000", username="", password="").query("MATCH (n) RETURN count(n)")` works
- [ ] **T034** Write integration test `tests/unit/test_bolt_server.py` — all unit tests pass
- [ ] **T035** **E2E GATE**: Full unit test suite — 405+ pass, 0 skip
- [ ] **T036** **E2E GATE**: `bash tests/curl_suite.sh` — HTTP API still works (18+ pass)

---

## Phase 7 — Polish + Publish

- [ ] **T037** Bump version to `1.47.0` in `pyproject.toml`
- [ ] **T038** Update README.md: add Bolt WebSocket section, update connection examples
- [ ] **T039** Add `browser_static/` to `.gitignore` (too large for PyPI)
- [ ] **T040** Build and publish: `python3 -m build && twine upload dist/iris_vector_graph-1.47.0*`
- [ ] **T041** Commit all changes

---

**Total tasks**: 42
**E2E phase gates**: T010, T014, T020, T028, T032, T033, T033b, T035, T036
**Primary E2E gate**: T033 — Python driver must work before any publish
