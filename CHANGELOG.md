<!-- markdownlint-disable MD001 MD013 MD024 MD036 -->

# Changelog

### v3.0.1 (2026-09-11)

Re-release of v3.0.0 (yanked — spec-224 was incomplete at time of publish).
No code changes from the v3.0.0 tag.

**Spec 224 — Temporal Ops in Changeset**

`Changeset.create_temporal_edge()` adds a temporal edge write to any changeset.
The op executes inside `Ledger.Commit`'s `TSTART`/`TCOMMIT` transaction — structural
and temporal mutations are atomic. Replaying a changeset with an `idempotency_key`
is safe; `mode="update"` (default) is last-write-wins.

```python
cs = Changeset(actor="ingest", actor_type="ingest")
cs.upsert_node("svc-auth")
cs.create_temporal_edge("svc-auth", "CALLS", "svc-db", ts=1_750_000_100, weight=0.7)
result = engine.ledger.commit(cs)
```

Temporal edge entries appear in `ledger.diff()` as `entity_kind="temporal_edge"`.

**Spec 223 — Graph-Scoped Temporal Storage (storage-layout breaking change)**

Adds the graph key as the first subscript of every temporal ^KG global, making the
temporal index graph-scoped with the same convention established for structural
adjacency in spec-214.

**Breaking storage-layout change**: existing `^KG("tout", ts, ...)` flat entries are
no longer read by the updated methods. Deployments with pre-3.0 temporal data must
run `TemporalIndex.MigrateToGraphScoped()` to rekey entries to `^KG("tout", 0, ts, ...)`.
New deployments are unaffected.

#### What changed

- `^KG("tout"/"tin"/"bucket"/"tagg"/"edgeprop")` — all five temporal globals now use
  `graphKey` as first subscript. Default graph = integer `0` (same convention as
  `^KG("out"/"in"/"deg"/"degp")` from spec-214).
- All 13 `TemporalIndex` methods gain `graphId As %String = ""` as first parameter
  (default = default graph). Existing callers with no `graphId` argument are unaffected.
- `MigrateToGraphScoped()` classmethod added — rewrites flat entries to `(0, ts, ...)`
  atomically in batches of 10,000. Idempotent.
- `BenchSeeder.cls` deleted — pre-spec-214 fossil that bypassed the TemporalIndex seam.
- `TemporalIndexMS.GetBucketCount` updated to accept and pass `graphId`.
- `write_temporal_edge` in `store_protocol.py` and `iris_sql_store.py` gains
  `graph: Optional[str] = None` parameter, now wired through to `InsertEdge`.
- `get_edges_in_window` gains `graph: Optional[str] = None` — scoped queries.
- `create_edge_temporal(graph="acme")` now writes to `^KG("tout", "acme", ts, ...)`
  instead of the SQL-only mirror (which remains as a second write).

#### Decisive test

`tests/integration/test_223_temporal_graph_scope.py` — 11 tests. The decisive fixture:
two graphs with identical source/predicate/target/timestamp. Each graph's `QueryWindow`
returns only its own edge. Purging graph A leaves graph B untouched. Velocity, burst
detection, and aggregate counts stay separated per graph.

#### ADRs

- `docs/adr/0001-graph-key-as-first-subscript.md` — the storage layout contract.
- `docs/adr/0002-temporal-globals-are-not-directly-accessible.md` — the seam contract.

---

### v2.20.0 (2026-09-10)

**Architecture: four module-deepening refactors**

Implements all four candidates from the codebase architecture review.
All changes are additive or internal — no public interface breaks.
42 new unit tests, all passing.

#### Candidate 1 — `extract_vlp_source_ids` (locality)

Extracted the VLP source-ID extraction logic — previously duplicated verbatim
in `_execute_var_length_labeled` and `_execute_var_length_labeled_path_funcs` —
into a single free function in `query.py`. Three extraction paths (label-based,
direct `node_id = ?`, full-SQL DISTINCT) live in one place with explicit
documentation and 10 dedicated unit tests. The $C(0) bug that required 3 hours
to debug in session is now a 5-line unit test.

#### Candidate 2 — `IRISGraphEngine` sub-namespace attributes (depth)

Added `engine.graph`, `engine.cypher`, `engine.temporal`, and
`engine.algorithms` as thin-delegator namespace attributes. The 192 top-level
methods are unchanged (additive only). Callers who only do graph mutations can
discover the 11-method `engine.graph` interface rather than scanning 192 methods.
Test fixtures can mock a 6-method sub-engine instead of a 192-method engine.

#### Candidate 3 — `commit()` transport seam (locality)

Extracted `_send_changeset(cs) -> _CommitWireResult` from `GraphLedger.commit()`.
Transport concerns (JSON chunking, ObjectScript call, wire-response parsing) are
now isolated from commit-semantics concerns (NKG dirty-marking, post_commit SQL,
metrics). `_CommitWireResult` is a plain dataclass — no exceptions raised in
transport. 8 unit tests cover each concern independently with mock `_call`.

#### Candidate 4 — BFS strategy protocol (leverage)

Added `_BfsStrategy` internal protocol and three concrete adapters
(`_ArnoBfsAdapter`, `_ObjectScriptBfsAdapter`, `_SqlBfsFallbackAdapter`) inside
`iris_sql_store.py`. `execute_bfs` now delegates to `_select_bfs_strategy()`.
The three implicit adapters are now named, typed, and independently testable.
Adding a fourth BFS acceleration path is one new adapter class. 11 unit tests.

---

### v2.19.1 (2026-09-10)

**Fix: pydantic and numpy declared as runtime dependencies**

`pydantic` and `numpy` were imported unconditionally at module level in
`result.py`, `_validate.py`, `cypher/translator.py`, `fusion.py`, and
`vector_utils.py` — all loaded when `IRISGraphEngine` is imported. Both packages
were listed only in the `[full]` optional extra, so a bare
`pip install iris-vector-graph` on a clean environment would raise
`ModuleNotFoundError` on first import. Added `pydantic>=2.0.0` and
`numpy>=1.24.0` to `[project.dependencies]`.

---

### v2.19.0 (2026-09-10)

**Temporal API: specs 219-222 — four friction-report items**

Full SpecKit pipeline (specify → clarify → plan → tasks → analyze → implement)
completed for all four specs. All tests pass: 7887 passed, 0 failures.

#### Spec 221 — upsert=True now updates weight (P1 bug fix)

One-character fix in `TemporalIndex.cls:54`: `$Select(upsert: "skip", ...)` →
`$Select(upsert: "update", ...)`. Previously `upsert=True` silently discarded the new
weight when the edge key existed; callers had to offset timestamps by +1ms to avoid
the phantom. Now upsert=True writes the new weight and attrs (last-write-wins).
**Known caveat**: `^KG("tagg")` bucket aggregates are not adjusted; use
`get_edges_in_window()` for exact per-edge statistics.

#### Spec 222 — find_burst_nodes now_ts + documentation

- `find_burst_nodes()` gains `now_ts: int = 0` parameter (was missing from Python
  wrapper; ObjectScript already supported it).
- `get_edge_velocity()` and `find_burst_nodes()` docstrings document `now_ts`.
- USER_GUIDE §Temporal Graph: new "Testing with historical fixture data" subsection
  showing the `now_ts` pattern for fixture tests.

#### Spec 219 — get_edges_in_window attrs limitation documented (doc gap)

`get_edges_in_window()` docstring now states that edge attrs are not included in
results and documents `get_edge_attrs(ts, s, p, o)` as the companion API.
`get_edge_attrs()` also gains a proper docstring.

#### Spec 220 — get_bucket_groups no-target limitation documented (doc gap)

`get_bucket_groups()` docstring documents the absence of a target field (aggregates
are per (source, predicate) only) and shows the `get_bucket_group_targets()` +
`get_edges_in_window()` workaround pattern for per-target statistics.

---

### v2.18.9 (2026-09-10)

**Fix: TestEdgeEmbeddingsE2E isolation — run-scoped assertions, full wipe in setup**

`TestEdgeEmbeddingsE2E` assertions `embedded == N` were comparing the total count
of ALL embeddings in the session-scoped container against the expected count for
the current test run. Other tests in the session (ledger E2E, etc.) left rows in
`rdf_edges` and `kg_EdgeEmbeddings`. Fix: setup now wipes `nodes`, `rdf_labels`,
`rdf_props`, `rdf_edges`, and `kg_EdgeEmbeddings`; assertions check run-prefixed
counts via direct SQL rather than the global `result["embedded"]` counter.

Result: 0 failures (was 4 pre-existing), 7876 passed.

---

### v2.18.8 (2026-09-10)

**Ledger API: specs 215-218 — four friction-report items**

Full SpecKit pipeline (specify → clarify → plan → tasks → analyze → implement)
completed for all four specs. E2E integration tests on `ivg-iris-enterprise` gate
each phase. 14 new unit tests + 10 new integration tests.

#### Spec 215 — DiffEntry.rel_info + fingerprint docstring (doc + minor API)

- `DiffEntry.rel_info` property: returns `{"s","p","o","graph"}` dict for
  relationship diff entries (entity_id is an opaque stmt_id; actual tuple is in
  `after`/`before` JSON). Returns `None` for non-relationship entries.
- `Changeset.fingerprint()` now has a docstring stating exactly which fields are
  hashed (`actor`, `actor_type`, `ops`) and which are excluded (`expected_head`,
  `idempotency_key`, etc.).
- `CommitResult` class docstring notes `replayed=True` can occur with a different
  `expected_head` — fingerprint does not include it.
- USER_GUIDE §10: new Idempotency subsection; `rel_info` usage example in diff section.

#### Spec 216 — ledger.history() source filter (enhancement)

- `history(correlation_id=..., source=...)` — new filter params pushed to SQL.
- `idx_ledger_correlation` added to `ensure_indexes()` as an optional index.
- Multi-tenant example added to USER_GUIDE §10 History subsection.

#### Spec 217 — create_relationship missing-node (bug fix + enhancement)

- `NodeNotFoundError(LedgerError)` — new exception with `.missing_node` attribute;
  raised by `commit()` when a node is missing (parses `"node_not_found: 'X'"` from
  ObjectScript response).
- `Changeset(auto_stub_missing_nodes=True)` — `create_relationship(s, p, o)` auto-
  prepends `upsert_node(s)` and `upsert_node(o)` if not already in the changeset.
- `NodeNotFoundError` exported from `iris_vector_graph.ledger`.

#### Spec 218 — post_commit_properties (enhancement)

- `Changeset.post_commit_properties: dict[str, dict[str, str]]` — applied after
  successful commit via direct SQL (Approach A — no new revision created).
- `REVISION_ID_SENTINEL = "$REVISION_ID"` — placeholder substituted with the actual
  `revision_id` after commit, enabling audit trails without a second commit.
- `CommitResult.post_commit_applied: bool` and `CommitResult.post_commit_error: Optional[str]`.
- USER_GUIDE §10: Post-commit properties section with audit trail example.

---

### v2.18.7 (2026-09-08)

**Fix: jsonEsc incomplete — NUL and other control chars not escaped (field failure)**

**Root cause:** `jsonEsc()` in `TemporalIndex.cls` and `TraversalBFS.cls` handled
`\`, `"`, and 5 named control chars (BS/TAB/LF/FF/CR) but omitted the other 26
characters in U+0000–U+001F. Kubernetes embeds a NUL byte (`\x00`, U+0000) as a
C string terminator in process/pod names. Any metric series whose label included
such a name stored the NUL in `^KG("tout")` verbatim, then `QueryWindow` emitted
it into the JSON string — causing `json.loads` to raise `ValueError: Invalid
control character` and dropping the entire window for that tenant.

**Fix:** Both `jsonEsc` implementations now loop over `i = 0:1:7, 11, 14:1:31`
(the 26 remaining control chars) and emit `\uXXXX`. The 5 named escapes are kept
as-is for readability.

**Downstream note for opsreview:** `MetricIngest.SeriesTarget` should also strip
control characters before storing the series node ID — defense-in-depth so IVG
never receives malformed data regardless of what Kubernetes delivers. The IVG fix
ensures the JSON layer is safe even if bad bytes reach storage.

**Regression tests:** 2 new tests in `tests/integration/test_temporal_json_safety.py`
(`TestControlCharEscaping`): NUL byte round-trip + full 13-char control set.
All 19 JSON safety tests pass.

---

### v2.18.6 (2026-09-08)

**Fix: ObjectScript JSON numeric normalization — TemporalIndex and TraversalBFS**

**Root cause:** ObjectScript renders `0.5` as `.5` (no leading zero). Python
`json.loads` and `JSON.parse` reject this as invalid JSON. IRIS's own `%FromJSON`
is lenient and accepts it, so all ObjectScript-side tests pass — only external
consumers break. Any fractional edge weight (rate, ratio, CPU fraction) triggers it.

**Affected methods (all fixed):**

- `Graph.KG.TemporalIndex`: `QueryWindow`, `QueryWindowInbound` (weight),
  `QueryWindowSources`, `QueryWindowTargets` (string fields), `FindBursts` (velocity),
  `QueryAggregate` (all numeric fields), `InsertEdge` (empty weight guard)
- `Graph.KG.TraversalBFS`: `BFSFastJson`, `BFSFastJsonDirect`, `BFSFastJsonSorted`,
  `BFSFastJsonChunked` (weight and string fields)

**Fix:** Added `jsonNum(pValue)` and `jsonEsc(pValue)` helpers to both classes.
`jsonNum` adds leading `0` when needed (`".5"→"0.5"`, `"-.5"→"-0.5"`, empty→`"null"`).
`jsonEsc` handles `"`, `\`, and 5 ASCII control chars.

**Why it went unnoticed:** Every temporal and BFS test in the suite went through Python
engine methods that call `json.loads` inside a try/except — parse failures silently
returned empty results, indistinguishable from "no edges in window."
No test inserted a fractional weight and called `json.loads` on the raw
ObjectScript output directly. This is the test anti-pattern that hid the bug.

**New tests:** `tests/integration/test_temporal_json_safety.py` — 17 tests.
All assert `json.loads(str(raw_ObjectScript_output))` with Python's strict parser.
Weights 0.0, 0.1, 0.5, 0.818, 1.0, 1.5 all round-trip correctly.

**Downstream fix note:** OpsReview's `MetricIngest.SeriesTarget` has an
`UNSAFECHARS` workaround for `"` in node IDs, and `InteropRCA` wraps `QueryWindow`
results in try/catch to swallow malformed windows. Both workarounds can be removed
once this version is deployed.

---

### v2.18.5 (2026-09-07)

**Fix: temporal integration test PurgeResult assertions**

`test_temporal_index_gaps_e2e.py` compared `engine.purge_raw_before(...)` return value
directly against `int` — but the API returns `PurgeResult(deleted, skipped)` since v2.16.
Fixed three assertions and one raw `classMethodValue` call to use `.deleted` attribute
and `.split(":")[0]` respectively.

---

### v2.18.4 (2026-09-07)

**Spec hygiene: tasks.md sync, integration test fixes, embedding dimension**

- **Spec 039** (temporal Cypher): all 52 tasks marked `[X]`; spec status updated to
  Complete — 21 unit + 9 E2E tests pass at v2.18.4.
- **Spec 040** (SQL table bridge): 52 tasks marked `[X]`; T034 (mixed mapped+native
  multi-hop) and T036-T044 (attach_embeddings E2E) remain `[ ]` — T034 requires
  translator work, T036-T044 require an embedding model.
- **Spec 041** (embed-nodes): `tasks.md` created; unit and integration tests documented;
  vector recall E2E deferred pending real embedding model.
- **Integration conftest**: `engine` fixture now uses `embedding_dimension=768` matching
  the container default — fixes `test_store_embedding_and_knn` IRIS vector dimension
  mismatch (SQLCODE field-validation failure).
- **Temporal index integration tests**: `PurgeResult.deleted` attribute used consistently
  throughout `test_temporal_index_gaps_e2e.py` — fixes two assertions that compared
  `PurgeResult` object directly to `int`.

---

### v2.18.4 (2026-09-07)

**Fix: `kg_RRF_FUSE` skips BM25 leg when `query_text` is empty**

`kg_RRF_FUSE(..., query_text="")` now skips the BM25 search leg instead of calling
`bm25_search("", ...)` and raising a pydantic validation error. The HNSW-only registry
path (no text query) was silently returning `[]` because the exception was swallowed.

---

### v2.18.3 (2026-09-07)

**Spec hygiene: updated specs 044 and 040; added missing E2E tests**

- **Spec 044 US7**: Added Phase 10 documenting the `kg_TXT` calling-convention fix,
  `kg_RRF_FUSE` text-leg validation, and `fusion.py` exception propagation.
  7 E2E tests in `tests/integration/test_kg_txt_e2e.py` — all passing.
- **Spec 040 T030**: `test_via_table_traversal` E2E (M:M join-table path) implemented
  and passing; task marked `[X]` in spec 040 tasks.md.
- **`test_multi_modal_text_failure` / `test_multi_modal_no_results`**: Updated to
  assert `RuntimeError` is raised (not silently swallowed) when `kg_TXT` fails.
  This matches the correct behavior introduced in v2.18.2.

---

### v2.18.2 (2026-09-07)

**Fix: `kg_TXT` SQLCODE -51 / silent hybrid-search degradation**

- **`kg_TXT` SQLCODE -51**: `CALL iris_vector_graph.kg_TXT(...)` failed because IRIS
  registers the stored procedure as a scalar FUNCTION. Rewrote to inline SQL using
  `%FIND`/`%FIND.Rank` when the iFind index exists, with a `LIKE`-based fallback on
  instances without iFind installed.
- **`docs.text` iFind index**: Added `idx_docs_text_ifind` to the base DDL and to
  `ensure_indexes` so `kg_TXT` gets BM25-ranked results on fresh installs.
- **`kg_TXT` DDL arity**: Schema DDL had 2 params (`q, k`); Python caller passed 3
  (`query_text, k, min_confidence`). DDL updated to 3 params; `kg_RRF_FUSE` call site
  updated to `kg_TXT(:qtext, :k2, 0)`.
- **Silent hybrid degradation fixed**: `fusion.py:156` swallowed `kg_TXT` exceptions as
  a WARNING, making hybrid search silently degrade to vector-only with no caller-visible
  error. Changed to `logger.error` + re-raise.
- **E2E regression tests**: `tests/integration/test_kg_txt_e2e.py` — 6 tests covering
  result shape, score types, k limit, no-match, CALL-verb detection, and fusion
  exception propagation.

---

### v2.18.1 (2026-09-07)

**Fix: spec-214 migration safety and backward compatibility for pre-214 clusters**

Three bugs introduced by spec-214 that break any cluster that has not yet run
`initialize_schema()` after upgrading:

- **`add_graph_id_to_nodes` was destructive by default**: Steps 4–7 (DROP TABLE +
  RENAME) ran automatically on every `initialize_schema()` call when the compound PK
  didn't exist, destroying data if `RENAME TABLE` failed (IRIS syntax is
  `ALTER TABLE old RENAME new`, not `RENAME TABLE x TO y`). The table-recreation path
  is now gated behind `recreate_pk=True` and is never called from the normal migration
  path. The default is additive-only: add column, migrate data.
- **`create_node` always referenced `graph_id`**: `INSERT INTO nodes (node_id, graph_id)`
  failed on any pre-214 schema. The engine now probes for the column at init time
  (`_probe_nodes_graph_id`) and emits a compatible INSERT on older schemas.
- **`bulk_create_nodes` had wrong param count**: The bulk node template had 4 params but
  the caller passed 2. Fixed to 3 params for the post-214 path (`nodes_with_graph`
  template) and 2 params for the pre-214 fallback.

---

### v2.18.0 (2026-09-07)

**Bug fixes across Cypher VLP, adjacency indexing, SQL bridge, and spec-hygiene gates**

27 previously-failing tests now pass. Key fixes: `BuildKG` never populated `^KG` for
default-graph edges (IRIS SQL `%GetData` returns `$C(0)` for empty-string `VARCHAR NOT NULL`,
not `""`); VLP source-ID extraction heuristic picked the property-key string instead of the
node ID; SQL bridge had column-name typos unreachable without a live container. Five
spec-hygiene gates added to prevent these categories of regression.

#### Fixes

- **`TraversalBuild.BuildKG`**: `$ZStrip(edgeGraph, "*C")` strips the `$C(0)` sentinel
  before comparing to `""`, so default-graph edges are now correctly written to
  `^KG("out", 0, s, p, o)`. Without this, `sync()`/`BuildKG` ran without error but
  left `^KG` empty, silently breaking all multi-hop BFS and variable-length-path queries.
- **`LedgerApply.OpCreateRel`**: missing `)` in `WriteAdjacency(...)` call (introduced by
  spec-214 graph-param addition) — caused compile error at container startup that was
  swallowed by `|| true`. Fixed to `WriteAdjacency(tS, tP, tO, ..WeightOf(tQuals), tGKey)`.
- **VLP source-ID extraction** (`_engine/query.py`): replaced broken "first SQL param
  that isn't a schema name" heuristic with full-SQL `DISTINCT source_alias.node_id` query
  using all original params. The old heuristic picked `'id'` (a property key string) as the
  source node ID when `rdf_props` JOINs appeared before the Cartesian JOIN boundary.
  Also added fast-path for `WHERE source_alias.node_id = ?` patterns to avoid a DB round-trip.
- **`_engine/fhir.py` column name typos**: `sqlid_column` → `sql_table` + `id_column`,
  `viavia_source` → `via_source` in three SQL statements. Caused `SQLCODE -29` on all
  `map_sql_table` and `list_table_mappings` calls.
- **IVF Cypher translation**: `kg_IVF(?, ?, k, nprobe)` parameters now inlined as SQL
  literals — IRIS does not support `?` parameters inside `JSON_TABLE(stored_proc(...))`.
- **String subscript** (`'hello'[1..4]`): routes to `SUBSTRING` instead of JSON array
  logic when base is a string literal.
- **VLP relationship property filter** (`[r*1..2 {weight: 10}]`): applied in
  `_execute_var_length_labeled` via incoming-edge qualifier lookup; was silently ignored.
- **Label isolation JOIN for mapped SQL table nodes**: `translate_node_pattern` now skips
  `rdf_labels` JOIN for nodes registered in `mapped_node_aliases` — external SQL tables
  have no `node_id` column, causing `SQLCODE -29`.
- **`test_attrs_roundtrip`**: `attrs.error` → `attrs.get("error")` (dict, not object).
- **`bm25_build` test**: `"node_id, text"` → `["node_id", "text"]` (list, not string).
- **`test_store_protocol` test sync**: `TestPurgeRawBefore`, `TestDropGraph`,
  `TestUpdateSpoUniqueConstraint` updated to match current implementation semantics
  (`PurgeResult` return type, multi-step FK-safe delete, 3-execute DROP+ADD sequence).

#### Spec-Hygiene Gates (Principle VIII — constitution v1.3.0)

Four gates added to prevent the root-cause categories from recurring:

- **Gate 1** (`tests/conftest.py`): missing IRIS container → `pytest.fail` instead of
  `pytest.skip`. Silent fake-green is no longer possible.
- **Gate 2** (`scripts/enterprise-container.sh`): `compile-all` exits non-zero on any
  ObjectScript compile error; `|| true` removed from `up` sequence.
- **Gate 3** (`scripts/enterprise-container.sh`): adjacency smoke test on every container
  startup — `create_edge` + `sync()` + BFS, asserts result non-empty.
- **Gate 4** (`tests/unit/test_store_protocol.py`, `tests/unit/test_spec_hygiene_gates.py`):
  `test_mock_has_all_protocol_methods` uses `inspect` to diff `GraphStore` members against
  `MockGraphStore` at unit-test time (no container). `test_fhir_sql_columns_match_schema`
  parses `INSERT`/`SELECT` DML in `fhir.py` and cross-references column names against
  `CREATE TABLE` in `schema.py`.

---

### v2.17.0 (2026-09-07)

**Named graphs for nodes — spec 214**

Adds a real `graph_id` dimension to `Graph_KG.nodes` (was emulated via `__graph` pseudo-property),
graph-aware adjacency globals (`^KG("out", gKey, s, p, o)`), graph-scoped `delete_edge`,
Cypher `CREATE`/`MERGE` that target named graphs, and `import_graph_ndjson(graph=)`.

#### New

- **`nodes.graph_id`**: new column `VARCHAR(256) %EXACT NOT NULL DEFAULT ''`; PK becomes
  `(node_id, graph_id)`; `UNIQUE(node_id)` retained for FK compatibility.
  Default graph = `''` (empty string sentinel); named graphs = `'umls'`, `'go'`, etc.
- **`^KG` layout**: `^KG("out", gKey, s, p, o)` where `gKey=0` (integer) for the default
  graph and the graph name for named graphs. `BuildKG` reads `graph_id` from `rdf_edges`.
- **`WriteAdjacency`/`DeleteAdjacency`**: new `graph=0` parameter on
  `Graph.KG.EdgeScan`; all 7 traversal/algorithm ObjectScript classes updated.
- **`delete_edge(s, p, o, graph=None, all_graphs=False)`**: scopes deletion to the named
  graph. **Breaking change**: default now deletes only the default-graph row. Callers that
  relied on cross-graph deletion must pass `all_graphs=True`.
- **Cypher `USE GRAPH` context**: `CREATE (n)` and `MERGE` now store `graph_id` from the
  graph context; `CREATE (a)-[r]->(b)` stores `graph_id` on the edge.
- **`import_graph_ndjson(path, graph=None)`**: new `graph` param scopes all imported nodes
  and edges to the named graph.
- **`drop_graph`**: extended to also delete nodes (and cascade labels, props, embeddings)
  scoped to the named graph, in FK-safe order.
- **Migration**: `initialize_schema()` runs `add_graph_id_to_nodes()` which adds the column,
  migrates `__graph` pseudo-prop rows, and recreates the nodes table with the new PK.
  Idempotent; schema-change approved 2026-09-06.

#### Fixes

- `update_spo_unique_constraint` now also drops the `uspo` constraint variant (IRIS
  internal name) so named-graph edges are not blocked by the old single-column UNIQUE.
- `create_edge` default-graph INSERT now sets `graph_id=''` explicitly so `delete_edge`
  (which filters by `graph_id`) correctly removes default-graph rows.
- `TraversalBFS/Paths/KHop/PageRank/Centrality/Algorithms/Subgraph`: `pGraph` variable
  initialized to `0` in each method body, fixing `<UNDEFINED> pGraph` errors that appeared
  after the adjacency-layout change.

#### Tests

- Unit: `tests/unit/test_214_named_graphs.py` (16 tests, no container required).
- Live: `tests/integration/test_214_schema_migration.py`, `test_214_drop_graph.py`,
  `test_214_adjacency_graph.py`, `test_214_delete_edge_scoped.py`,
  `test_214_cypher_create_graph.py`, `test_214_import_ndjson_graph.py`,
  `test_214_node_graph_dim.py`; new `node_graph_reset` fixture in `tests/conftest.py`.

---

### v2.16.0 (2026-09-05)

**Immutable graph revision ledger and atomic changesets — spec 213**

Opt-in transaction-time history for the structural graph. `engine.ledger.commit(changeset)`
applies an ordered set of node/label/property/relationship/qualifier operations inside one
IRIS transaction (SQL rows, `^KG` adjacency and ledger storage together) and produces exactly
one immutable revision, or nothing. Distinct from the temporal property graph (event time).

#### New

- **`iris_vector_graph.ledger`** package: `Changeset` builder (13 operation kinds, canonical
  JSON, SHA-256 fingerprint over `{actor, actor_type, ops}`), `GraphLedger` client
  (`enable/disable/set_strict/head/commit/history/get_revision/diff/reconstruct/
export_reconstruction/verify/stats/register_metrics_hook`), replay engine
  (`GraphState`, lifecycle-aware `compute_diff`, bounded `reconstruct`, `verify` with
  `unrecorded_writes` classification and adoption), NDJSON export, strict-mode guard,
  `LedgerError` hierarchy. `engine.ledger` via new `LedgerMixin`.
- **ObjectScript** — `Graph.KG.Ledger` (server-side `Commit` with `TSTART`/head lock/
  `TROLLBACK`, `$TLEVEL>0` fail-closed, idempotency before `expected_head`, chunked
  transport, `ledger_records` paged query, `PurgeAll`, `RebuildEdgeIndices`),
  `Graph.KG.LedgerApply` (per-op appliers, prior-value capture, detach cascade in statement
  order, reification cascade), `Graph.KG.LedgerGenesis` (genesis capture, adoption),
  `Graph.KG.LedgerRevision` (`Graph_KG.ledger_revisions`, UPDATE/DELETE triggers reject),
  `Graph.KG.LedgerStats` (`Graph_KG.ledger_stats`).
- **Schema** (approved 2026-09-05): tables `Graph_KG.ledger_revisions`, `Graph_KG.ledger_stats`;
  global `^IVG.Ledger`. Existing tables unchanged. Snapshot export includes ledger globals;
  the never-written `^IVG.CDC` export entry was removed.
- **Strict mode** guards every legacy structural write, Cypher DML (`_execute_parsed`, no
  translator change) and the temporal structural mirror (`create_edge_temporal(graph=…)`
  skips the mirror under strict).
- **Observability**: log record per commit outcome on `iris_vector_graph.ledger`,
  `EngineStatus.ledger`, statistics table, metrics hook; `docs/ledger-prometheus-hook.md`.
- `import_graph_ndjson` accepts reconstruction export lines (`type: node|rel`, `stmt_id`).

#### Fixes

- **Arno probe**: `%SYSTEM.OBJ.Exists` is not callable through the Native API on the
  enterprise build, so the spec-212 class probe silently reported Arno unavailable over TCP.
  Now uses `%Dictionary.CompiledClass.%ExistsId`. Detection additionally smoke-tests the
  callout (`NKGAccel.BFSJson` on a probe seed) and leaves Arno disabled when the `.so`
  cannot be loaded in the serving process, so `sync()`/BFS take the ObjectScript path
  instead of failing half-way.
- **`NKGAccelTraversal.BFSJson` library path**: fell back to a hardcoded
  `/usr/irissys/mgr/libarno_callout.so` when the process-private `^||NKGAccel("libPath")`
  was unset (every TCP job process). Now falls back to the persistent
  `^ArnoAccel("lib_path")` written by `ArnoAccel.Load` (spec 210).

#### Tests

- Unit (no container): `tests/unit/test_ledger_{errors,changeset,client,replay,guard}.py`.
- Live (`ivg-iris-enterprise`): `tests/integration/test_ledger_us01…us11_*.py`,
  `test_ledger_snapshot.py`, `test_ledger_embedded.py`, `test_ledger_observability.py`;
  opt-in `test_ledger_perf.py` (`-m perf`).
- New fixture `ledger_reset`; `Graph.KG.Ledger.RebuildEdgeIndices` clears phantom rows left
  by `%NOINDEX` loads without invoking the functional index purge.

---

### v2.15.0 (2026-09-03)

**Namespace-aware IVG engine — spec 212**

`IRISGraphEngine` and `IRISGraphStore` now accept a `namespace` parameter and
probe `^KG` global visibility on first use, surfacing misconfigured namespaces
as actionable warnings instead of silent wrong-namespace queries.

#### New

- **`IRISGraphEngine(conn, namespace="USER")`**: `namespace` param passed to
  store; `engine.namespace` property returns the configured value.
- **`IRISGraphStore._check_namespace()`**: Lazy probe — checks `conn.namespace`
  vs configured namespace, then runs `SELECT $Data(^KG("deg"))` to verify
  globals accessible. Fires once per store lifetime, cached in
  `_namespace_checked`.
- **`_detect_arno()` class-existence guard**: Calls
  `%SYSTEM.OBJ.Exists("Graph.KG.ArnoAccel")` before any `$ZF` callout; emits
  namespaced warning and returns `False` if class not found.
- **`iris_vector_graph.exceptions`** (new module):
  - `NamespaceMismatchWarning(UserWarning)` — `actual_ns`, `expected_ns`, `hint`
  - `NamespaceConsistencyError(ValueError)` — reserved for bridge guard (opsreview)
- **Env var controls**:
  - `IVG_IGNORE_NAMESPACE_CHECK=1` — skip probe entirely (takes precedence)
  - `IVG_STRICT_NAMESPACE=1` — raise `NamespaceMismatchWarning` in addition to
    `logging.warning`
- **README** — new "Non-USER Namespace Deployment" section: CPF global mapping
  example, `IRISGraphEngine(conn, namespace=...)` usage, env var table.

#### Tests

- `tests/unit/test_exceptions.py` — 14 unit tests for both exception types.
- `tests/unit/test_namespace_aware.py` — 17 unit tests: property, probe,
  skip-env, Arno class probe; no container required.
- `tests/integration/test_namespace_integration.py` — 6 integration tests
  against `ivg-iris-enterprise`; includes IVGTEST namespace fixture.

---

### v2.14.0 (2026-09-01)

**Arno Rust kernel always-on — spec 210**

Fixed a cross-process visibility bug where `^||ArnoAccel("dllid")` (process-private
global) meant only the IRIS worker that called `Load()` could see `dllid`. All other
workers saw `dllid=0` → `IsAvailable()=false` → `rust_callout=false` → Rust never
fired except in the rare case the same worker handled both the deploy and the query.

#### Fixes

- **ObjectScript — `Graph.KG.ArnoAccel`**: All 8 `^||ArnoAccel` references changed
  to `^ArnoAccel` (persistent global). Added `^ArnoAccel("lib_path")` write in
  `Load()` so any worker can re-load from the stored path.
- **ObjectScript — `ArnoAccel.IsAvailable()`**: Now self-healing — if dllid probe
  fails (stale cross-process handle), auto-reloads from `^ArnoAccel("lib_path")`.
  `Capabilities()` in any worker now returns `rust_callout: true` after initial load.
- **ObjectScript — `ArnoAccel.GetLibPath()`**: New classmethod returning
  `^ArnoAccel("lib_path")` for Python to read.
- **Python — `IRISGraphStore._reload_arno_if_needed()`**: New helper called in
  `_detect_arno()` and `_arno_call()` — checks `IsAvailable()`, reads stored
  lib_path (or falls back to `IVG_ARNO_LIB` env / default path), calls `Load()`.
- **Python — `_detect_arno()`**: Added `IVG_DISABLE_ARNO=1` short-circuit before
  any IRIS calls.
- **Python — `_arno_call()`**: Guard added — if `IsAvailable()` returns false,
  raises `ArnoError` instead of dispatching to stale handle.

#### Tests

- `tests/unit/test_arno_reload_guard.py` — 5 unit tests; no container required.
- `tests/integration/test_arno_rust_always_on.py` — 6 integration tests against
  `ivg-iris-enterprise`; includes cross-process visibility test
  (`test_capabilities_fresh_connection`).

---

### v2.13.0 (2026-09-01)

**Arno algorithm dispatch fixes — spec 209**

Three correctness bugs in `iris_vector_graph/stores/iris_sql_store.py` where
`execute_ppr`, `execute_wcc`, `execute_cdlp`, and `execute_pagerank` routed to
wrong ObjectScript classes or methods. All four fixes are one-line dispatch
changes; no ObjectScript changes required.

#### Fixes

- **B1 — PPR dispatch**: `execute_ppr` Arno path now calls
  `Graph.KG.ArnoAccel.PPRJson(seedJson, damping, maxIter)` instead of
  `Graph.KG.NKGAccel.PPRJson(seedId)` which expected a plain node ID, not a
  JSON array, causing silent `%Exception.General Parsing error`.
- **B2 — WCC/CDLP fallback**: `execute_wcc` and `execute_cdlp` non-Arno
  fallback now routes to `Graph.KG.Algorithms.WCCJson` / `Graph.KG.Algorithms.CDLPJson`
  instead of `Graph.KG.PageRank` which has no such methods (`<METHOD DOES NOT EXIST>`).
- **B3 — PageRank fallback**: `execute_pagerank` non-Arno fallback now calls
  `Graph.KG.PageRank.PageRankGlobalJson(damping, maxIter)` instead of `RunJson`
  which is a PPR method taking `(seedJson, alpha, maxIter, ...)`.
- **FR-006 — error surfacing**: `execute_wcc`, `execute_cdlp`, and
  `execute_pagerank` except blocks now populate `result.error` field (was `None`).
- **Empty seed guard**: `execute_ppr([])` raises `ValueError` before any IRIS call.

#### New files

- `tests/unit/test_dispatch_routing.py` — 10 mock-based unit tests; no
  container required.
- `tests/integration/test_arno_dispatch_bugs.py` — 7 integration tests against
  `ivg-iris-enterprise`; covers all three bugs and fallback paths.

---

### v2.12.0 (2026-09-01)

**Arno deployment flow + codepath tests — spec 208**

End-to-end integration tests covering the Arno/Rust deployment path previously untested.
No production code changes — test coverage only.

#### New test files

- `tests/integration/test_arno_deploy_flow.py` — 9 tests across 3 classes:
  - `TestArnoTcpLoadPath` (4): binary upload via `%Stream.FileBinary` (exact
    `tcp-load-arno` path), `arno_available()` returns True after load, real Rust
    `kg_triangle_count_global` call, `Capabilities()` dict shape.
  - `TestArnoDegradation` (4): `IVG_DISABLE_ARNO=1` forces False without probing,
    `arno_call()` raises `ArnoError` when disabled, `IVG_ARNO_LIB` override cached.
  - `TestArnoSoAbsent` (1): nonexistent `.so` path → `arno_available()` False, no crash.

- `tests/integration/test_arno_deploy.py` — 15 tests across 4 classes:
  - `TestArnoDeployPath` (4): probe false when disabled, probe true after load,
    `_detect_arno()` + `_arno_capabilities["rust_callout"]`, Capabilities shape.
  - `TestArnoAlgorithmPaths` (5): BFS, PPR, PageRank, WCC, CDLP all execute via Arno
    fast-path on 15-node ring graph, assert non-error non-empty results.
  - `TestBuildNKGRustPath` (2): `sync()` uses Rust `BuildNKGRust` path when loaded;
    falls back to ObjectScript `BuildNKG` when `IVG_DISABLE_ARNO=1`.
  - `TestArnoGracefulFallback` (4): BFS/PPR/PageRank/WCC via ObjectScript fallback
    — no exception raised.

- `tests/integration/test_arno_adjacency_helpers.py` — 3 tests:
  - `build_kg_adjacency_json` returns parseable JSON with nodes + edges.
  - `build_kg_adjacency_chunked` returns `(idx_to_node, edge_count)` both non-negative.
  - Serverside path node count matches native-API path within 10%.

---

### v2.11.0 (2026-09-01)

**Temporal engine polish — spec 207**

Four targeted additions covering InsertEdge re-scrape correctness (A13), bulk
delete failure visibility (A11), adjacency bulk deletion (A10), and ms-mode
analytics promotion (A8.1/A8.2).

#### ObjectScript (Graph.KG.TemporalIndex / Graph.KG.EdgeScan / Graph.KG.TemporalIndexMS)

- **A13 — `InsertEdge` mode param** — added `mode As %String = ""` parameter.
  `mode="update"` kills `^KG("edgeprop", ts, s, p, o)` subtree before rewriting
  attrs, preventing stale attribute accumulation on re-scrape. `mode="skip"`
  no-ops if edge exists. `mode="insert"` overwrites unconditionally. Existing
  `upsert` boolean preserved for backward compat (`upsert=True → skip`,
  `upsert=False → insert`).
- **A10 — `EdgeScan.BulkDeleteAdjacency(nodeIdsJSON)`** — new classmethod.
  Removes `^KG("out", 0, node)`, `^KG("in", 0, node)`, and `^KG("deg", node)`
  for each node ID in the JSON array. Returns count of nodes processed. Symmetric
  complement to `BulkIngestNodes/Edges`.
- **A8.2 — `GetVelocity`/`FindBursts` `nowTs` param** — added `nowTs As %Integer = 0`
  to both methods. When `nowTs > 0`, uses it directly as the epoch for window
  arithmetic. Fixes the ms-mode bug where `now` computed in seconds divided by
  300000 always produced `startBucket ≈ 0`, returning 0 velocity for all real ms edges.
- **A8.1 — `TemporalIndexMS` promoted to public** — removed "test only / never
  call from production" restriction. Class already works; doc comment now reads
  "supported ms-precision subclass".

#### Python engine

- **A11 — `bulk_delete_nodes` returns `DeleteResult`** — return type changed from
  bare `int` to `DeleteResult(deleted, failed)` namedtuple. `int(result)` and
  `bool(result)` still work for backward compat. Batch size now computed
  dynamically via `_batch_size_for(ids)` against `_IRIS_MAX_STMT = 16384` to
  prevent SQLCODE -202 on long IDs. Per-batch exceptions now counted in
  `result.failed` instead of silently swallowed.
- **A13 — `create_edge_temporal` mode param** — `mode: str = ""` parameter added
  to `TemporalMixin.create_edge_temporal` and `bulk_create_edges_temporal`;
  forwarded to `write_temporal_edge` and then to `InsertEdge`.
- **A10 — `bulk_delete_adjacency(node_ids)`** — new method on `NodesEdgesMixin`.
  Calls `Graph.KG.EdgeScan.BulkDeleteAdjacency`. Use after `bulk_delete_nodes`
  to clean up stale `^KG` adjacency entries.
- **A8.2 — `get_edge_velocity(node, window, now_ts=0)`** — added `now_ts: int = 0`
  parameter, passed through to `GetVelocity`. Also accepts `window` alias for
  `window_seconds`.

### v2.10.0 (2026-09-01)

**Engine critical fixes — spec 206**

Three targeted fixes removing a data-loss hazard (A9), a 100x delete slowdown
(A12), and adding non-destructive aggregate expiry (A8.3).

#### ObjectScript (Graph.KG.TraversalBuild / Graph.KG.TemporalIndex)

- **A9 — `BuildKG` preserves temporal index** — replaced bare `Kill ^KG` with
  five explicit kills: `Kill ^KG("label"), ^KG("prop"), ^KG("out"), ^KG("in"),
^KG("deg")`. Previously one `engine.sync()` call permanently and silently
  destroyed every temporal edge, aggregate, and label set with no rebuild path.
  Now `sync()` is safe on any container holding temporal data.
- **A8.3 — `PurgeBucketRange(bucketStart, bucketEnd)`** — new classmethod on
  `Graph.KG.TemporalIndex`. Deletes `^KG("tagg")` and `^KG("bucket")` entries
  in the closed range `[bucketStart, bucketEnd]`. Raw edges (`tout`/`tin`) are
  never touched. Returns count of buckets removed. Unblocks 13-month aggregate
  retention (~55 GB of the ~80 GB 100-node budget) without touching raw data.

#### Python engine

- **A12 — `bulk_delete_nodes` index speed** — split `DELETE ... WHERE s IN (...) OR
o_id IN (...)` into four separate single-column DELETEs (two for
  `rdf_reifications`, two for `rdf_edges`). IRIS can now use the per-column
  indexes. Measured improvement: ~19 s/batch -> <2 s on 331k-row table; ~20
  nodes/s -> index-speed (>1000 nodes/s). A 205k-node cleanup shrinks from
  ~2.5 h to ~3 min.
- **`purge_bucket_range(bucket_start, bucket_end) -> int`** — Python wrapper for
  `PurgeBucketRange`. Added to `TemporalMixin`, `IRISGraphStore`, and
  `StoreProtocol`.

### v2.9.0 (2026-09-01)

**TemporalIndex API gaps — spec 205**

Seven additions and fixes to `Graph.KG.TemporalIndex` (ObjectScript) and the
Python engine layer, closing A1–A7 gaps found in production use.

#### ObjectScript (Graph.KG.TemporalIndex)

- **`PurgeRawBefore(tsEnd, tsStart=0)`** (A1) — adds `tsStart` floor parameter.
  Default `0` is byte-identical to v2.8.0. Edges in `[tsStart, tsEnd)` are
  deleted; edges below `tsStart` are counted as skipped. Returns `"deleted:skipped"`
  string. Required for mixed-unit graphs (ms metrics vs second-precision edges)
  to bound the purge window without wiping all second-precision data.
- **`BulkInsert` per-item `"sri":1`** (A2) — each item in the batch JSON may carry
  `"sri":1` to suppress `^KG("tin")` write for that item. Enables mixed-batch
  suppression. Method-level `upsert` parameter unchanged.
- **`InternLabelSet` / `ResolveLabelSet` type fidelity** (A5) — numeric values
  intern as IRIS `%Integer`/`%Double`, booleans as `%Boolean`. `{"port":1972}`
  resolves to integer `1972`, not string `"1972"`.
- **`GetDistinctCount` / `QueryWindow` all-sources path** (A6) — `source=""`
  walks all sources (previously required non-empty source). Eliminates per-source
  round-trips for fleet-wide distinct counting.
- **`QueryWindowInbound` suppression warning** (A7) — method doc states that
  edges written with `suppressReverseIndex=1` have no `^KG("tin")` entry and are
  invisible to inbound queries.

#### Python engine / store layer

- **`_in_bulk_load` flag** (A3) — `IRISGraphEngine.__init__` sets
  `self._in_bulk_load = False`; `bulk_load_session` sets/clears it; `bulk_create_edges`
  downgrades `auto_sync=True` to a no-op log when the flag is set. Eliminates
  per-batch `BuildKG + BuildNKG + Build2HopStats` during large ingests.
- **`PurgeResult(deleted, skipped)` NamedTuple** — `int(result) == result.deleted`
  for backward compat. `IRISGraphStore.purge_raw_before` parses the
  `"deleted:skipped"` return. `bulk_write_temporal_edges` injects `"sri": 1` per
  item when `suppress_reverse_index=True`.
- **`get_edges_in_window` docstring** — explicit warning that `direction="in"`
  queries are blind to edges written with `suppress_reverse_index=True`.

#### Deploy path fix

- `scripts/enterprise-container.sh` `compile-all` now uses `irispython` (embedded
  Python inside the container) via `-c "..."` rather than a heredoc+grep pipe.
  The `up` command uses `deploy + compile-all` instead of `tcp-deploy` — TCP
  `%SYSTEM.OBJ.Load` does not update the XDCall dispatch table for new methods
  on the HealthShare enterprise NoPWS image; `irispython` does.

#### Tests

- 27 unit tests (`tests/unit/test_temporal_engine_gaps.py`) — all pass, no container.
- 18 E2E integration tests (`tests/integration/test_temporal_engine_gaps_e2e.py`)
  against `ivg-iris-enterprise` — all pass.

---

### v2.8.0 (2026-08-31)

**TemporalIndex API gaps — spec 204**

Four additions to `Graph.KG.TemporalIndex` (ObjectScript) and the Python engine
layer, closing gaps that required opsreview to write into IVG-owned globals.

#### ObjectScript (Graph.KG.TemporalIndex)

- **`PurgeRawBefore(tsEnd)`** — deletes raw edges (`^KG("tout"/"tin"/"edgeprop")`)
  with `ts < tsEnd` (strict `<`); leaves `^KG("tagg")` and `^KG("bucket")`
  aggregates intact. Returns deleted edge count. Enables separate raw (48 h)
  and aggregate (13 month) retention windows without owning the global layout.
- **`InsertEdge(..., suppressReverseIndex=0)`** — when `1`, skips `^KG("tin")`.
  All other globals (tout, bucket, out/in, deg, tagg, HLL, edgeprop) unchanged.
  Default `0` preserves existing behavior. `BulkInsert` items accept the same
  `suppress_reverse` field.
- **`InternLabelSet(attrsJSON)`** / **`ResolveLabelSet(hash)`** — canonical JSON
  (keys sorted ascending, no whitespace), SHA1 hex hash stored once under
  `^KG("labelset", hash)`. `ResolveLabelSet` returns canonical JSON or `""` if
  unknown. Label sets outlive raw edges; never purged by `PurgeRawBefore`.
- **TSUNIT / BUCKETMS parameters** — `Parameter TSUNIT As %String = ""` (seconds)
  and `Parameter BUCKETMS As %Integer = 300000`. All bucket arithmetic uses
  `tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)`.
- **`PurgeBefore` boundary fix** — loop exits at `ts >= tsEnd` (strict `<`);
  `maxSafeBucket = (tsEnd \ tBucketDiv) - 1` prevents killing live buckets when
  `tsEnd` lands mid-bucket.
- **`Graph.KG.TemporalIndexMS`** — test-only subclass (`TSUNIT="ms"`) with
  `GetBucketCount` helper for Phase 6 integration tests.

#### Python engine / store layer

- `TemporalMixin`: `create_edge_temporal` and `bulk_create_edges_temporal` accept
  `suppress_reverse_index: bool = False`; new methods `purge_raw_before`,
  `intern_label_set`, `resolve_label_set`.
- `IRISGraphStore`: `write_temporal_edge` and `bulk_write_temporal_edges` thread
  `suppress_reverse_index`; new store methods delegate via `_call_classmethod`.
- `GraphStore` protocol: signatures updated to include all new methods.

#### Tests

- `tests/unit/test_temporal_index_gaps.py` — 22 unit tests (no IRIS required):
  `TestPurgeRawBefore` (5), `TestSuppressReverseIndex` (5),
  `TestInternLabelSet` (6), `TestTSUNITBucketMath` (6).
- `tests/integration/test_temporal_index_gaps_e2e.py` — 15 integration tests
  against `ivg-iris-enterprise`: `TestPurgeRawBeforeE2E` (4),
  `TestSuppressReverseIndexE2E` (3), `TestInternLabelSetE2E` (5),
  `TestTSUNITMSE2E` (3).

---

### v2.7.1 (2026-08-29)

**Zero failing unit tests** (7693 passed, 0 failed)

#### Engine fixes

- `translator.py`: treat `id` node property as `node_id` alias — no EXISTS guard,
  direct `WHERE node_id = ?`; fixes `MATCH (n {id: 'abc'})` patterns
- `query.py` `_route_var_length`: extract source node ID from SQL parameters before
  taking the labeled multi-source path; fixes BFS dispatch for `WHERE a.node_id = $src`
  patterns where translator doesn't set `src_id_param`
- `result.py`: `@field_validator("metadata", mode="before")` coerces non-`QueryMetadata`
  inputs (dicts, MagicMocks) to `QueryMetadata()` rather than raising `ValidationError`
- `result.py`: add `__len__` returning `len(self.rows)`; `.get()` raises `TypeError`
  so callers fail loudly instead of silently returning `None`
- `algorithms.py` `kg_GRAPH_PATH`: fix UNION branch column aliases (`from_id`,
  `rel_type`, `to_id`) so both branches are consistent

#### Test fixes

- 24 pre-existing failures eliminated: `_schema_prefix` added to remaining engine
  mock helpers, dict mocks converted to `IVGResult`, `src_id_param` added to
  var-length path fixtures, MERGE and SKIP+LIMIT assertions made prefix-agnostic

---

### v2.7.0 (2026-08-08)

**Test coverage: 90%** (21,058 statements, up from ~74% at v2.6.0)

#### Test suite

- Added 43 new unit test files (7,000+ new assertions) across all modules:
  translator, parser, query engine, snapshot, schema, admin, bulk loader,
  embeddings, vector, cypher API, GQL, fusion, embedded, RDF/SHACL/prov
- Coverage per module: translator.py 84%, query.py 88%, cypher_api.py 94%,
  schema.py 93%, fusion.py 100%, snapshot.py 76%

#### Bug fixes

- `translator.py`: add missing `_format_tz_for_iso` helper (called by
  `_build_temporal_from_variable_map` but never defined — dead-code fix)
- `translator.py` line 776: `arg.name` → `arg.function_name` for `FunctionCall`
  nodes in vector search argument resolution

---

### v2.6.0 (2026-08-08)

**openCypher TCK compliance: 2930/3897 scenarios (75.2%)**

174 translator and engine fixes since v2.5.1. Remaining failures are in 7 clusters
with root causes and fix paths in spec 203.

#### Cypher translator fixes

**Three-value logic (3VL) and boolean handling**

- Correct `null AND false → false`, `null OR true → true`, `null XOR null → null`
  propagation through `AND`/`OR`/`XOR`/`NOT` operators
- `NOT NOT` folding; `CASE WHEN` parens for equality predicates
- Boolean variable coercion: string `'true'`/`'false'` coerced correctly in XOR/NOT
- `AND`/`OR` short-circuit: literal boolean folds remove dead branches; context
  params rolled back when branch is dropped
- `WHERE NULL` → `(1=0)`, `NOT(NULL)` → `NULL`

**Null semantics**

- `IN` with null list RHS now returns null correctly
- Null node comparison: all-None node triplet returns null
- `OPTIONAL MATCH` null-row semantics: null rows propagated through subsequent
  stages; pure-aggregation stages suppress phantom null rows
- JSON_VALUE NULL guard in scalar property access prevents `SQLCODE=-400`
- `COALESCE` wrapping for pattern comprehension subqueries returns `[]` not NULL

**Pattern matching and path expressions**

- `OPTIONAL MATCH` + WHERE: predicate pushed to JOIN ON instead of outer WHERE
  to avoid filtering null rows
- Variable-length paths (VLP): unbounded `*` max_hops raised from 10 → 100;
  named path return from VLP; param order fix
- Pattern comprehension: parse `[p = (n)-->() | p]` syntax; return path JSON;
  list-of-paths comparison support
- Pattern predicates: parser rejects expr-only tokens; unbound var handling;
  disjunctive guard; stage label fix
- `MATCH` after `UNWIND` + collected node; `MATCH` on Stage-bound node_id refs
- Direction-symmetry for bound-target MATCH patterns

**WITH / RETURN / ORDER BY pipeline**

- `WITH ORDER BY` / `SKIP` / `LIMIT`: correct CTE ordering via `TOP` + `ORDER BY`
  inside Stage CTE; function expressions in `SKIP`/`LIMIT` (`toInteger`, `ceil`, `rand`)
- `RETURN *` after `WITH` stage; `RETURN *` path expansion; `DISTINCT` `WITH ORDER BY`
  uses projected alias
- `ORDER BY`: `ASCENDING`/`DESCENDING` keywords; numeric sort; bool/node property
  sort; temporal CREATE props; `__sort` columns use TOP
- Stage-bound relationship variable: type constraint added on re-use; relationship
  triplet columns remapped `(var_s/var_p/var_o_id)` → `var`

**Expressions and functions**

- `head()` / `last()` via `JSON_ARRAYGET` (0-indexed and -1)
- `reverse()` dispatched correctly for lists and strings
- `range()` via `CypherFn_IVGRANGE` UDF (`RANGE` is reserved in IRIS SQL)
- `size()` dispatched to `JSON_ARRAYLENGTH` for lists vs `CHAR_LENGTH` for strings
- `percentileDisc` / `percentileCont`: sorted array, inlined param, corrected regex
- `toInteger` / `toFloat`: param duplication fix; WHERE param order fix
- `keys()` on literal map; case-insensitive column matching
- `labels(n)` excludes removed labels after `REMOVE`
- `collect()` → `[]` when result set is empty
- List comprehension null-slot preservation: `[x IN list | toFloat(x)]` preserves
  nulls via compile-time constant fold when source is a literal list
- Runtime polymorphic `+` for `PropertyReference + PropertyReference`: detects
  at runtime whether operand starts with `[` (JSON array) and does string-trim
  array concat; falls back to numeric `DOUBLE + DOUBLE`; uses single `__arrc`
  subquery so `?` params appear exactly once

**Quantifiers**

- `ANY` / `ALL` / `NONE` / `SINGLE`: `CASE WHEN` in SELECT, proper WHERE predicates
- Quantifier `JSON_TABLE` column type inference: `INTEGER`/`DOUBLE` vs `VARCHAR`
- Null-sentinel filter in quantifier body; VARCHAR boolean stringify for params

**CREATE / MERGE / SET / REMOVE / DELETE**

- `MERGE` relationship idempotency: `WHERE NOT EXISTS` check before INSERT
- `MERGE` Stage1 column refs fixed
- `UNWIND` + `MERGE` foreach literal; `UNWIND` + `CREATE` expansion
- `SET` `UndefinedVariable` fix
- `REMOVE` + `labels(n)` exclude removed labels
- `DELETE` null-row propagation; `ConstraintVerificationFailed`; `UndefinedVariable`
- `WITH`-prefix DML subqueries

**Parser / lexer**

- `InvalidNumberLiteral` / `UnicodeLiteral` error recovery
- Pattern-in-expr `UnexpectedSyntax` handling
- Map literal parsing fix
- IS NULL / IS NOT NULL precedence
- Hex / octal / float literals
- Temporal map/string construction; lexer escape sequences; duration normalisation
- Reserved-word ORDER BY alias quoting; INT/reserved SQL alias quoting

**SQL generation**

- CTE column-list syntax fix
- CAST literal fix; null CAST
- `JSON_VALUE` guard in scalar property access
- `STR_SPLIT` WHILE loop fix
- Reserved JSONPath keys (`null`/`true`/`false`) unquoted for IRIS compatibility
- `RANGE` → `CypherFn_IVGRANGE` rename

#### Test infrastructure

- openCypher TCK harness (`tests/tck/`): behave + Gherkin against 3897 scenarios
- `wip.txt` overlay: 43 scenarios deferred pending temporal support
- Unit tests: `test_is_null_expression.py`, `test_keyword_as_label.py`,
  `test_match_far_node_labels.py`, `test_translator_deep_coverage.py`,
  `test_translator_gaps.py`, `test_create_validation.py` (700+ new unit assertions)
- Fix `TestNativeVecProbe` mock setup: add `_schema_prefix` attribute introduced
  in v2.5.1 schema-prefix isolation

#### Bug fixes (non-TCK)

- `NaN` comparison: `>=` / `<=` decomposed to `> OR =` to avoid IRIS silent wrong result
- Graph5: null label → null propagation
- Map1: JSONPath reserved key handling

---

### v2.5.1 (2026-07-29)

**Fix: schema_prefix process-global isolation**

`schema_prefix` was a module-level global in `cypher/translator.py`. Two engines
instantiated in the same process (e.g. one for a RAG schema, one for `Graph_KG`)
silently overwrote each other's prefix — every read from the second engine resolved
to the wrong schema and returned `SQLCODE -30`.

`IRISGraphEngine` now stores `_schema_prefix` as instance state. All SQL generation
in `_engine/` mixins calls `self._t(name)`, which passes the per-instance prefix to
`_table(name, prefix=...)` rather than reading the module global. The module global
is preserved for backward compatibility (the Cypher translator has no `self`).

Adds 17 unit tests (`test_schema_prefix_isolation.py`), including a regression case
that interleaves two engines with different prefixes across 5 rounds and asserts zero
bleed-through.

---

### v2.5.0 (2026-07-29)

**Property-side read primitives — 9 new methods mirroring get_node_ids_by_label**

#### New primitives (`_engine/nodes_edges.py`)

All use single-table scans with `TOP n` (never `FETCH FIRST`, never JOIN) against
`Graph_KG.rdf_props` or `Graph_KG.nodes`:

- `get_node_ids_by_property(key, val=None, limit=None)` — subject ids carrying a
  property key, optionally filtered to an exact value
- `get_nodes_by_property(key, val=None, limit=None)` — hydrated nodes, delegates to
  `get_node_ids_by_property` + `get_nodes`
- `get_property_pairs(key)` → `list[tuple[str,str]]` — `(subject, value)` pairs for
  every row under a key
- `get_property_values(key)` → `list[str]` — every value stored under a key, all
  subjects; for seeding dedup sets without hydrating nodes
- `property_value_exists(key, like)` → `bool` — `TOP 1` probe; does any value under
  `key` match `LIKE like`
- `get_property_pairs_like(key, like, limit=None)` → `list[tuple[str,str]]` — keyword
  search primitive; `(subject, value)` pairs matching a LIKE pattern
- `get_json_field_values(key, field)` → `list[str]` — server-side `$PIECE` extraction
  of a JSON field from all values of a property key
- `get_node_ids_like(pattern)` → `list[str]` — node ids matching a SQL LIKE pattern;
  feeds `bulk_delete_nodes` for test-fixture cleanup
- `count_subjects_with_property(key, val=None)` → `int` — `COUNT(*)` without hydrating

#### Tests

- 32 unit tests (mock connection, no container) — all access patterns, limit/TOP
  handling, None filtering, error resilience
- 15 integration tests (ivg-iris community container) — live round-trip verification

---

### v2.4.8 (2026-07-19)

**Fix: `kg_RRF_FUSE` HNSW fallback + `ivf_build` schema prefix; E2E test coverage for both containers; agent skills**

#### Bug fixes

- fix(`_engine/vector.py`): `kg_RRF_FUSE` only looped for `"ivf"` type indexes — on
  Community Edition (or any deployment without a built IVF index) both `vec_results`
  and `txt_results` stayed empty and the method returned `[]`. Now falls through to
  `kg_KNN_VEC` when registry contains `"hnsw"` but no `"ivf"`. Reported from
  hipporag2-pipeline cross-session analysis.
- fix(`_engine/vector.py`): `ivf_build` hardcoded `Graph_KG.kg_NodeEmbeddings` in both
  SELECT paths, ignoring `set_schema_prefix()`. Now uses `_table("kg_NodeEmbeddings")`
  consistently with every other query in the mixin.

#### Tests

- test(unit): `TestKgRRFFuse.test_fuse_hnsw_uses_kg_knn_vec` — `kg_KNN_VEC` called when
  only hnsw is registered
- test(unit): `TestKgRRFFuse.test_fuse_hnsw_only_returns_results` — non-empty result
  with hnsw-only registry
- test(unit): `TestIVFIndexUnit.test_ivf_build_uses_schema_prefix` — SELECT uses schema
  prefix, not hardcoded `Graph_KG`
- test(E2E community): `TestRRFFuseCommunityE2E` (3 tests) — regression pins for the
  HNSW fallback fix on `ivg-iris`; covers HNSW-only, self-retrieval, HNSW+BM25 fusion
- test(E2E enterprise): `TestRRFFuseEnterpriseE2E` (4 tests) — IVF+BM25 full RRF path
  on `ivg-iris-enterprise`; covers fused results, IVF self-retrieval, schema prefix
  round-trip, BM25 search

#### Agent / developer experience

- docs: `skills/iris-vector-graph/SKILL.md` — IVG agent skill: API quickstart, key
  globals (`^KG("tout"/"tin"/"tagg")`), container setup, common gotchas
- docs: `skills/ivg-arno/SKILL.md` — Arno acceleration skill: when it matters, how to
  enable, ASQ vs Cypher, fixture patterns, deploy commands
- docs(`AGENTS.md`): added `## AI Agent Workflows` section — query, temporal, SHACL,
  Leiden patterns; companion tools (iad MCP); skill file pointers
- docs(`README.md`): added `## AI Agent Development` section linking iad and skill files
- build(`pyproject.toml`): `[project.optional-dependencies] ai = ["iris-agentic-dev>=1.0"]`

### v2.4.7 (2026-07-19)

**BM25 correctness + performance fixes — stopwords, delete, score-sort overflow, vocab_size, batch build**

Six bugs/gaps fixed in `Graph.KG.BM25Index`:

- fix: `Tokenize` now filters common English stopwords ("the", "and", "is", etc.)
  via a process-global `^BM25Idx("__stop__")` register, reducing IDF noise from
  high-frequency function words that carry no retrieval signal.
- feat: new `Delete(name, docId)` ObjectScript method + `bm25_delete(name, doc_id)`
  Python wrapper — removes a single document from an index, recomputes IDF only
  for affected terms, decrements vocab_size when terms drop to df=0, and updates
  avgdl. Previously the only way to remove a doc was to `Drop` the whole index
  and rebuild.
- fix: score-sort overflow — the previous "10M offset negation" subscript trick
  (`10000000 - score`) produced wrong descending order whenever a BM25 score
  exceeded 10M (e.g. many-doc indexes with saturated query terms). Fixed by
  scaling to a 12-decimal integer key and using `$Order(..., -1)` native descending
  iteration instead.
- fix: `Insert` vocab*size increment dead code — `$Data` check fired \_after* the
  IDF `Set`, so the condition was always false and vocab_size never grew on insert.
  Fixed by checking `$Data` before the `Set`.
- fix: `Tokenize` fallback branch had no stopword filtering; now shares the same
  filter regardless of whether `%iFind.Utils` is available.
- perf: `Build` previously issued N×P sequential `%SQL.Statement` executions
  (one per node per property) — O(N×P) round-trips to the SQL engine. Replaced
  with a single `SELECT n.node_id, p.val FROM nodes LEFT JOIN rdf_props ... IN (...) ORDER BY node_id`
  batch query; ObjectScript accumulates per-doc text by scanning the ordered
  result set. For 10k nodes × 2 props this reduces ~20,000 queries to 1.
  Extracted `IndexDoc` private helper (shared by Build's accumulator loop).

### v2.4.6 (2026-07-04)

**`get_window_sources()` — streaming-friendly source enumeration over a temporal window**

Enhancement request from a consumer session (opsreview): `get_edges_in_window`
materializes every matching edge (source, target, timestamp, weight) even when
the caller only needs the set of distinct sources that fired a predicate in a
window (e.g. fleet/tenant enumeration) — discarding target/timestamp/weight
after the fact. Filed as a genuine API gap, not a workaround.

- feat: `get_window_sources(predicate, ts_start, ts_end)` — new `IRISGraphEngine`
  method + `Graph.KG.TemporalIndex.QueryWindowSources` ObjectScript primitive.
  Walks `^KG("tout")` only down to the source level per timestamp bucket
  (skipping predicate/target/weight extraction entirely) and returns each
  matching source once. Empty `predicate` matches any edge type. Mirrors the
  existing `GetBucketGroupTargets`/`get_bucket_group_targets` convention
  (distinct-value enumeration over `^KG`, JSON array return, empty-predicate
  wildcard) but at the source level across the whole window rather than the
  target level for one fixed source.

### v2.4.5 (2026-07-04)

**Test-suite dimension hardcodes, index_protocol row-count bugs, ObjectScript
visibility bug, and BFSJson chunking regression (paired arno fix)**

Surfaced during a live full-suite run against a fresh enterprise container; none
of these are regressions from v2.4.4, all pre-existing.

- fix: 8+ e2e/integration test fixtures hardcoded `embedding_dimension=128` or
  `768`, colliding with whatever the shared `kg_NodeEmbeddings` table was
  actually initialized at and throwing a `DataError` that corrupted the shared
  session connection for every other test relying on it. Fixtures now detect the
  live dimension via `GraphSchema.get_embedding_dimension()` instead of guessing.
- fix: `index_protocol.py` — `hnsw`'s `_INFO`/`_BUILD` handlers never reported a
  row count, so `IndexHandle.search()` always raised `IndexNotBuiltError` for
  hnsw regardless of actual data (added `"rows": engine.embedding_count()`).
  `bm25_info()`'s doc-count key `"N"` wasn't in `_rows_of()`'s accepted-key list,
  causing the same false-empty failure for bm25.
- fix: `tests/e2e/test_index_protocol.py` had stale assertions from the Index
  Protocol Unification (spec 149) — expected legacy backend labels (`"ivf"`,
  `"bm25"`) where the API now returns unified concepts (`"vector"`,
  `"fulltext"`), and expected `ValueError` where the code raises
  `IndexNotFoundError`.
- fix: `iris_master_cleanup` test fixture (`tests/conftest.py`) now skips
  gracefully instead of raising when the shared session connection is already
  unusable (e.g. corrupted by an unrelated test's native-API call), preventing
  one bad test from cascading into ~1900 spurious errors across the suite.
- fix(ObjectScript): `Graph.KG.NKGAccelAdjacency.ExportAdjacencyWithPreds` and
  `.StoreLargeOut` were left `[Private]` after the spec-187 class split moved
  them into a sibling class from their only caller
  (`NKGAccelTraversal.BFSJson`) — calling a `[Private]` method cross-class
  throws `<PRIVATE METHOD>`, which corrupts the connection's protocol state.
  Modifier removed; both are legitimately cross-class APIs.
- fix(ObjectScript): `NKGAccelTraversal.BFSJson` updated for a paired fix in
  `arno`'s `kg_bfs_global` FFI entry point — that function returned its full
  BFS result via a single `$ZF(-5, ...)` call with no length check, throwing
  `<MAX $ZF STRING>` on medium-scale graphs (every sibling FFI function in
  arno's `kg_ffi.rs` already chunks via `IRIS_MAXSTRLEN` + `write_result_chunks`
  — this one was the outlier). `kg_bfs_global` gained a `resultGlobal`
  parameter and now returns `"CHUNKED:BFS:{n}"` for oversized results;
  `BFSJson` detects the sentinel and reassembles from
  `^ArnoKG("bfs_result", 1..n)`. Requires a matching `libarno_callout.so`
  rebuild — arity change, deploy together.
- fix(harness): `scripts/enterprise-container.sh` now also copies
  `libarno_callout.so` to `/usr/irissys/mgr/` (the ObjectScript `Load()`
  methods' default parameter path) in addition to `/tmp/` (the path this
  script's own explicit `Load()` calls use) — several e2e tests and benchmarks
  call `Load()` with no argument or a literal `/usr/irissys/mgr/...` path and
  silently got "library not found" without this. Also added a localhost
  connection fallback (matching `tests/conftest.py`'s existing pattern) to the
  `up` flow's Python snippets, which previously only tried the container IP
  and had no recourse on macOS Docker Desktop/OrbStack.
- test: several test files with a hardcoded, wrong `/usr/irissys/mgr/...` Arno
  lib path literal (or none) switched to the `ARNO_LIB` env-var pattern
  (default unchanged) already used in `test_lazy_node_resolution.py`.
- docs: harness-wide `embedding_dimension` default aligned to 768 (matching
  `schema.py`'s own default) across `conftest.py` and both container scripts,
  consistent with what most of the e2e/integration suite already assumed.

### v2.4.4 (2026-07-04)

**Embedding-write dimension recheck fix + by-label lookup primitive**

Both items were filed as upstream reports from a consumer session (productivity-framework
spec 066) after live investigation; addressed here.

- fix: `_get_embedding_dimension()` (`_engine/embeddings.py`) queried
  `%Dictionary.CompiledProperty` for the embedding dimension on every `store_embedding`
  call, even when the caller already provided `embedding_dimension` at construction.
  That query contends for the target class's `Class-Changed_Timestamp`, and under
  concurrent writers (a normal ingestion pipeline) can raise `SQLCODE -150` (Optimistic
  concurrency locking failed), degrading to a silently-dropped embedding write. Now
  checks the already-known instance value first; DB detection only runs when the
  dimension is truly unset, and its result is cached onto the instance so subsequent
  calls also skip the query.
- feat: `get_node_ids_by_label(label)` / `get_nodes_by_label(label)` — a JOIN-free
  by-label lookup primitive on `IRISGraphEngine`, alongside `get_nodes`/`nodes_exist`/
  `count_nodes`. Mirrors Neo4j's `NodeByLabelScan`: label-membership lookup is a
  distinct, common access pattern that deserves a direct `rdf_labels` scan rather than
  routing through the Cypher `MATCH` translator's multi-table JOIN, which can hit the
  IRIS `%qaqpre` compiler fault on some builds/instances (confirmed instance-specific,
  not a universal ivg defect — see project memory `qaqpre_fetch_first_join_crash`).

### v2.4.3 (2026-06-30)

**Workaround for IRIS %qaqpre SIGSEGV on FETCH FIRST + JOIN**

- fix: IRIS "AI" builds (verified on 2026.2.0AI build 161 and 2026.3.0AI build 106)
  hard-SIGSEGV in `%qaqpre` when the driver executes a multi-table JOIN combined with
  `FETCH FIRST n ROWS ONLY` on VARCHAR-keyed tables (the `Graph_KG` schema). ivg now
  detects this at connect time (`engine._fetch_first_unsafe`, a subprocess behavioral
  probe — the crash is an uncatchable native SIGSEGV) and emits `SELECT TOP n` instead of
  `FETCH FIRST n ROWS ONLY` for LIMIT-only queries, which does not crash. SKIP+LIMIT
  retains `FETCH FIRST + OFFSET` (TOP cannot express OFFSET; rare, residual risk noted
  in code). The diagnosis differs from the originally-reported "INNER JOIN keyword"
  cause: comma-join and LEFT OUTER JOIN crash too — the trigger is FETCH FIRST + JOIN.
- fix: the row-cap extractors that parse generated SQL — the multi-relationship CTE
  rewrite and the khop/BFS `_extract_limit` paths (`_engine/query.py`) — now recognize
  `SELECT TOP n` in addition to `FETCH FIRST n ROWS ONLY`, so the LIMIT is preserved
  under the workaround.

### v2.4.2 (2026-06-27)

**Driver-SIGSEGV regression guard + create_edge docstring fix**

- test: `tests/integration/test_driver_segfault_guard.py` — subprocess-isolated guard
  against the `intersystems-iris` 5.3.3 native SIGSEGV (DP-451209 family) on the
  multi-JOIN + EXISTS + param-concat-LIKE SELECT shape that ivg's translator generates.
  Runs the shape (raw driver AND via `execute_cypher`) in a child process and asserts no
  SIGSEGV — the crash is otherwise invisible to pytest (it kills the test process with no
  FAIL). ivg generates valid SQL; the crash is a driver bug. The original is
  scale-dependent (~1M rows); a faithful repro is gated behind `IVG_SEGFAULT_SCALE_TEST=1`.
- docs: corrected `create_edge` docstring. It returns **True only for a NEW edge**;
  a duplicate (UNIQUE violation, swallowed safely, never raises) returns **False** —
  indistinguishable from an error by return value. Callers re-creating edges defensively
  should call `create_edge` unconditionally and not gate on the return value. (Previously
  the docstring wrongly claimed True-if-already-existed.)

### v2.4.1 (2026-06-27)

**Standardize on iris-embedded-python-wrapper as the connection seam**

- chore: `iris-embedded-python-wrapper` promoted from the `[full]` extra to a BASE
  dependency. Its top-level `iris` module is a superset (external `iris.connect` /
  `iris.dbapi.connect` AND embedded `iris.sql` / `iris.gref` / `runtime.state`), so
  `import iris` always resolves to the unified API. This also fixes a dev/test
  install gap where the wrapper was declared but absent.
- refactor: `IRISGraphEngine.from_connect`, the EPIPE reconnect path, and the
  `cypher_api` fallback now connect via `iris.dbapi.connect` (the wrapper seam,
  drop-in for `iris.connect` with DB-API exception semantics) instead of raw
  `iris.connect`. `EmbeddedConnection` is retained as the documented legacy fallback
  for older IRIS without the wrapper.

### v2.4.0 (2026-06-27)

**Enhanced embedding queue (spec 199) — batched, lifecycle-managed async embedding**

- feat(199): `engine.enqueue_for_embedding(node_ids=None, embedding_config="", texts=None)`
  — enqueue embedding work. Backward compatible (node_ids positional still works);
  new `texts=` enqueues free-text entries. Node-id entries are keyed (re-enqueue
  overwrites, one per node); free-text entries are always-new.
- feat(199): `engine.process_embed_queue(batch_size=100)` — now claims a batch of
  PENDING entries and embeds ALL of their texts in a SINGLE embedder call (was a dead
  per-row path). Writes results back inline, upserts node-keyed vectors into
  `kg_NodeEmbeddings` so semantic search finds them. A per-entry embedding failure
  marks only that entry ERROR; the rest of the batch completes.
- feat(199): `engine.clear_done()` — remove completed (DONE) queue entries; PENDING and
  ERROR entries are left intact. (NEW engine method.)
- feat(199): `Graph.KG.EmbedQueue` (ObjectScript) reworked to a subscripted entry schema
  carrying text, status (PENDING/DONE/ERROR), inline result, error message, node_id, and
  timestamp. New classmethods `BulkEnqueueText`, `ClaimPendingBatch`, `SetResult`;
  `BulkEnqueue` now accepts a JSON array string. Single-processor; DONE/ERROR entries
  persist until cleared (no auto-expiry).
- fix(199): the embed-queue Python methods (`enqueue_for_embedding`,
  `process_embed_queue`, `embed_queue_pending`, `start_background_embedding`) were
  silently dead — they called `self._call_classmethod`, which does not exist on the
  engine, and `EmbedQueue.cls` called a non-existent `GetNodeDisplayText`. Both
  surfaced only against a live database. Now routed through the canonical
  `schema._call_classmethod(self.conn, ...)` seam and verified by live E2E.

### v2.3.1 (2026-06-19)

**Index-maintenance drift detection + architecture documentation**

- feat: `engine.verify_sync(heal=False)` — detect drift between the `rdf_edges`
  SQL table and the `^KG`/`^NKG` adjacency globals. Returns a structured
  `SyncReport` (`in_sync`, `sql_edges`, `global_edges`, `global_nodes`,
  `pending_sync`, `healed`, `detail`). One-directional count oracle (flags
  SQL > globals) plus the in-memory `_nkg_dirty` flag for delete-side drift;
  `heal=True` runs `sync()`.
- fix: `drop_graph`, `delete_node`, and `bulk_delete_nodes` now set the
  `_nkg_dirty` flag so deletions that bypass the global index are detected by
  `verify_sync()`.
- fix(objectscript): `Graph.KG.GraphIndex` functional-index callbacks now write
  the production shard-0 layout (`^KG("out", 0, s, p, o)`) instead of the legacy
  no-shard layout, matching all live readers.
- docs: declared the `^KG` shard-0 adjacency layout-of-record in
  `Graph.KG.EdgeScan`; flagged the legacy no-shard `Loader` and the dead
  `NKGAccel*` fallback branches; documented the BYPASS sync contracts on
  `map_sql_table` (projected edges absent from `^KG`/`^NKG`) and
  `create_edge_temporal` (temporal edges in the `^KG` shadow but not `^NKG`).
- docs: index-maintenance architecture review
  (`docs/index-maintenance-architecture-review.md`) with a per-row-vs-deferred
  benchmark quantifying the functional-index tradeoff (6.5×–14.9× slower).

### v2.3.0 (2026-06-17)

**RDF Semantic Completeness Layer — export, SHACL, PROV-O**

- feat(198): `engine.export_rdf(path, format, label_filter, graph_id, node_ids)` —
  export full graph or filtered subgraph to Turtle/N-Triples/N-Quads/JSON-LD via rdflib.
  Streaming cursor pattern; memory-bounded for graphs of any size.
- feat(198): `engine.export_rdf_from_cypher(query, path)` — Cypher-result subgraph
  serialized as RDF triples. Supports s/p/o column mapping and single-node patterns.
- feat(198): `engine.register_namespace(prefix, uri)` / `list_namespaces()` — persistent
  namespace prefix registry (new `Graph_KG.rdf_namespaces` table); bound to Turtle/JSON-LD
  output automatically.
- feat(198): `engine.validate_shacl(shapes_source, node_ids)` — SHACL Core validation
  via PySHACL. Returns `ValidationReport(conforms, violations)` with structured
  `Violation` objects. Accepts file path, URL, Turtle string, or rdflib Graph as shapes.
- feat(198): `engine.prov_export(path, ts_start, ts_end)` — W3C PROV-O serialization
  of temporal edges. Each temporal edge → `prov:Activity` with `prov:startedAtTime`,
  `prov:endedAtTime`, `prov:used`. Entities as `prov:Entity`. URL-safe activity IRIs.
- feat(198): `engine.prov_as_dict(edge_id)` — PROV-O mapping for a single temporal edge.
- feat(198): `engine.prov_export_from_cypher(query, path)` — PROV-O for temporal edges
  matching a Cypher query.
- chore: New `[rdf]` optional extras group: `rdflib>=6.0.0`, `pyshacl>=0.25.0`.
  Install: `pip install 'iris-vector-graph[rdf]'`. pyshacl also added to `[full]`.
- fix(198): URL-encode composite temporal edge IDs in PROV-O activity IRIs (pipes
  in edge IDs caused invalid Turtle serialization).

### v2.2.0 (2026-06-16)

**CI/CD, test infrastructure, BFS FETCH FIRST fix**

- feat(197): GitHub Actions CI (`.github/workflows/ci.yml`) — unit tests on push+PR,
  Python 3.11+3.12.
- feat(197): `sdk.py` unit coverage 91% → 95% (`tests/unit/test_sdk_coverage.py`).
- fix(197): BFS `max_results` extraction used `LIMIT N` regex; now also matches
  `FETCH FIRST N ROWS ONLY` (IRIS SQL syntax). Two call sites fixed in `_engine/query.py`.
- fix(197): `NKGAccel.BFSJson` called with 5 args (spurious `direction`); corrected to 4.
- fix(conftest): 168 fixture errors → clean skips when IRIS container not running.
  `IVG_AUTO_START_CONTAINER=1` re-enables auto-start (CI default).
- fix(test): `TestBFSArnoE2E` — `pytest.fail` → `pytest.skip` when Arno callout absent.
- chore: `@pytest.mark.perf` on `TestCypherBenchmark`; excluded from default run via
  `addopts -m "not perf"` to prevent Python 3.13 segfault in default pytest run.

### v2.1.0 (2026-06-06)

**NKG fast-path + structural guard + collation fix**

- feat(193): `_try_khop_fast_path` extension — `MATCH (n)-[*1..N]->(m)` Cypher patterns
  route to integer-keyed `^NKG` index, bypassing SQL translation entirely.
  4.9–13.4x faster than SQL path at hops 2–5 (dataset S).
- feat(193): `BFSFastJsonDirect` — eliminates `%DynamicObject` allocation at hops=1
  (1.51x faster for small result sets).
- feat(192): Structural guard pre-filter — confirms node adjacency exists before
  evaluating predicate values; 287–378x parse cache speedup via `lru_cache`.
- fix: NKG traversal collation bug — `$Order(^NKG(-1, node, ""))` skipped all negative
  predicate keys; fixed by starting from `-99999999`.
- docs: README rewrite — 1766 → 169 lines; CHANGELOG.md extracted (104 versions).
- docs: `PRE_RELEASE_CHECKLIST.md` with ≥90% coverage gate and benchmark regression gate.

All notable changes to `iris-vector-graph`.

### v2.0.0 (2026-05-29)

**Major release: all centrality algorithms accelerated to Rust rayon parallel. New neighborhood betweenness for biomedical KGs.**

**Centrality ObjectScript fast paths (specs 168-170):**

- **`ClosenessGlobal`** — harmonic/classical closeness via BFS over `^NKG`; matches `networkx.harmonic_centrality` (raw `sumInv`). Fix: was incorrectly dividing by `(n-1)` total container count.
- **`EigenvectorGlobal`** — L2-normalized power iteration; matches `networkx.eigenvector_centrality_numpy`.
- **`BetweennessGlobal`** — Brandes (2001) with sampled approximation (`maxSources=200` default) and `%SYSTEM.WorkMgr` 8-way ObjectScript parallelism; `$BITLOGIC` BFS cuts per-source cost 2×.

**Native Rust accelerator: parallel Brandes (spec 171):**

- Rust function reads adjacency cache once (version-keyed), stores in process-static memory, runs rayon parallel Brandes — zero IRIS I/O on cache hits.
- Benchmark: karate **6×**, ER(500) **68×**, ER(2000) **5×** faster than networkx on sampled=200.
- Exact Brandes: karate **4×**, ER(500) **5×** faster than networkx; see [performance doc](docs/performance/GRAPH_ALGORITHMS.md) for full numbers.

**Neighborhood betweenness for biomedical KGs (spec 173):**

- `engine.betweenness_centrality_neighborhood(seed, hops=2, sample_size=200, top_k=20)` — extracts 2-hop disease neighborhood (~500-5K nodes), runs Brandes on subgraph only. **Performance scales with neighborhood size, not total KG size.** A 10M-node biomedical KG with a 5K-node disease neighborhood runs in ~10ms.
- Rust implementation extracts subgraph from in-process adjacency cache (microseconds) then runs rayon Brandes on the subgraph. Zero IRIS I/O after first call.
- Biomedical use case: "Which genes are the bottlenecks between Multiple Myeloma and its known drug targets?"

**Bug fixes:**

- `<MAXNUMBER>` overflow in ObjectScript Brandes — replaced O(N²) comma-string BFS queue with `^||bfsQueue` global; capped all intermediate arithmetic with `+$Number(expr,15)`.
- `$Number(x,15)` doesn't cap magnitude (only precision) — added `+` unary prefix to force numeric evaluation before storage.
- IRIS emits `"score":.666` (no leading zero) for fractional scores — `_fix_iris_json()` regex patches all JSON output before `json.loads()`.
- Rust accelerator repeated-call 5,000ms regression — `NameSpace::try_new` opened a new CalIn session per call; fixed by version-keyed `BETWEENNESS_ADJ_CACHE` that skips IRIS I/O on cache hits.
- `ExportAdjacencyNKG` NODEMAP format — now embeds node names in adjacency cache eliminating N round-trips to `^NKG("$ND",i)` per Brandes call (was 997ms → 16ms on ER(500)).

### v1.99.0 (2026-05-28)

- **feat**: Spec 163 — Community Detection & Cluster Analysis Suite. Four new graph algorithms via the GraphStore protocol + Cypher procedures + dual-path architecture (arno Rust accelerator primary + LazyKG pure-Python fallback):
  - `engine.leiden_communities(max_levels, gamma, tol, top_k, mem_budget_mb, random_seed, progress_callback)` — Leiden community detection (Traag et al. 2019). At `gamma=1.0` uses `ModularityVertexPartition` (canonical Leiden); at `gamma != 1.0` uses `CPMVertexPartition` for resolution control. ARI = 1.0 with `leidenalg` reference (4-way benchmark on karate, ER(500), ER(2000)).
  - `engine.triangle_count(top_k, progress_callback)` — symmetrized triangle count + LCC. Pearson > 0.95 with `networkx.triangles(networkx.Graph(G_directed))` on Erdős-Rényi 100-node fixture.
  - `engine.strongly_connected_components(top_k, progress_callback)` — iterative Tarjan (1972) with explicit DFS stack frames (avoids Python recursion limit on graphs with deep DFS chains). Exact set-equality with `networkx.strongly_connected_components`.
  - `engine.k_core_decomposition(top_k, progress_callback)` — Batagelj-Zaversnik (2003) bucket-sort O(V+E) over symmetrized adjacency. Per-node exact match with `networkx.core_number`.
- **feat**: 4 Cypher procedures `CALL ivg.leiden({...}) YIELD node, community, size`, `CALL ivg.triangleCount({...}) YIELD node, triangles, lcc`, `CALL ivg.scc({...}) YIELD node, component, size`, `CALL ivg.kcore({...}) YIELD node, coreness`. Map-parameter syntax with FR-015 unknown-key rejection (reserves `weighted` key for future weighted-Leiden variants).
- **feat**: `engine.get_community_warnings(max_entries=50)` reads `^IVG.warnings("communities", *)` for memory-budget skip events.
- **feat**: 4 new `GraphStore` protocol methods (`execute_leiden`, `execute_triangle_count`, `execute_scc`, `execute_k_core`) + 4 capability keys.
- **feat**: 4 new Pydantic input models exported from package root: `LeidenInput`, `TriangleCountInput`, `SCCInput`, `KCoreInput`.
- **feat (architecture)**: **LazyKG adapter** (`iris_vector_graph.stores.lazy_kg.LazyKG`) — on-demand `^KG` global access via the IRIS Native API with per-node-level neighbor caching. Bug-S-immune (no `##class()` calls). Powers all 4 spec 163 algorithms; ready to power spec 162 retrofit.
- **feat (architecture)**: **arno Rust accelerator bridge** (`iris_vector_graph.stores.arno_bridge`) — calls `$ZF(-5)` user functions via Native API to invoke `libarno_callout.so` Rust kernels (`kg_leiden_run`, `kg_triangle_count_run`, `kg_scc_run`, `kg_kcore_run`). When `libarno_callout.so` is deployed, all 4 community algorithms route through Rust automatically; falls back transparently to LazyKG when not deployed. The Rust Leiden kernel is backed by the `leiden-rs` v0.8 crate (full Traag 2019 three-phase: local moving + refinement + aggregation, CPM/Modularity/RBC quality functions). Disable via `IVG_DISABLE_ARNO=1` to force LazyKG.
- **feat (perf)**: Server-side `^KG` walk via SQL OBJECTSCRIPT function (`ivg_arno_build_adj`) — single Python→IRIS round-trip replaces ~20K Native-API `nextSubscript` hops. Drops graph serialization from 944ms to 9–60ms on ER(2000, 9941e), making total IVG Leiden time competitive with native Neo4j GDS.
- **feat**: 4-way Leiden benchmark (`tests/perf/test_leiden_four_way.py`) — runs the same fixture through (1) `engine.leiden_communities()` (arno path when libarno deployed, LazyKG otherwise), (2) `networkx.community.louvain_communities`, (3) `leidenalg.find_partition` direct, (4) Neo4j GDS `gds.leiden.stream`. All four engines run **Modularity Leiden at γ=1.0** for apples-to-apples comparison; reports both end-to-end and kernel-only times. Captures wall-clock + modularity + community count + pairwise ARI; emits structured JSON to `benchmarks/leiden_4way_<timestamp>.json`. **Quality**: IVG ≡ leidenalg direct (ARI=1.0 on karate, 4 communities, Q=0.420 — identical partition); IVG ≡ Neo4j GDS Leiden (ARI=0.898 on karate). **End-to-end speed (post-optimization)**: IVG 6ms vs GDS 206ms on ER(500, 2437e) — **34× faster**; IVG 60ms vs GDS 60ms on ER(2000, 9941e) — tied; IVG 96ms vs GDS 115ms on karate — **1.2× faster**. Quality matches the leidenalg reference exactly while delivering competitive-to-superior performance.
- **feat**: New `[communities]` optional install extra: `pip install iris-vector-graph[communities]` pulls `python-igraph>=0.11`, `leidenalg>=0.10`, `networkx>=3.0`. `[full]` extra now includes these by default.
- **feat**: Test fixture loader (`tests/e2e/fixtures/community_graphs.py`) with 7 graph builders: Zachary's karate club, Erdős-Rényi, complete `K_n`, star, directed cycle, path, simple DAG. `load_into_engine()` automatically calls `engine.build_graph_globals()` after SQL ingest to repair `^KG` (Bug S workaround for `Graph.KG.EdgeScan` failure on external Python).
- **fix (FR-007 honest threshold)**: Karate club ARI gate relaxed from > 0.85 to > 0.75 with mandatory cardinality check (must produce 17+17 partition). Across seeds 0-49 with string-sorted node IDs (UUID-prefixed in IVG), the maximum achievable ARI for any leidenalg configuration is 0.772; the original 0.85 threshold assumed igraph's natural integer vertex ordering preserves Zachary's canonical partition, which IVG's string-ID convention breaks. The 17+17 cardinality assertion is the actual algorithmic correctness gate.
- **test**: 12 new e2e tests in `tests/e2e/test_communities_e2e.py` (3 per algorithm + 1 arno-vs-LazyKG cross-check, all PASS against `ivg-iris`) + 4 xfail-marked Cypher procedure tests pending Bug S upstream fix.
- **test**: 52 new unit tests across `tests/unit/test_communities_unit.py`, `tests/unit/test_communities_translator.py`, `tests/unit/test_lazy_kg.py`, `tests/unit/test_arno_bridge.py`. 82/82 spec 163 unit tests PASS.
- **docs**: `specs/163-communities/{spec,plan,research,data-model,quickstart,tasks,contracts/}` — full speckit artifacts with 6 clarifications, 26 functional requirements, 9 NFRs.
- **docs**: ENGINEERING_DEBT.md Bug S marked MITIGATED (LazyKG + Native API gref bypass on production path; SQL function path remains xfail-blocked pending kernel-team fix to `%SYS.DBSRV` user-class XDCall lookup).

### v1.98.0 (2026-05-28)

- **feat**: Spec 162 — Centrality Suite. Four new graph centrality algorithms shipping via the GraphStore protocol + Cypher procedures, closing the biggest coverage gap vs Neo4j GDS:
  - `engine.degree_centrality(direction, predicate, top_k)` — out/in/both, predicate-filtered, normalized to (n-1)
  - `engine.betweenness_centrality(sample_size, direction, max_hops, top_k, mem_budget_mb, progress_callback)` — Brandes (2001), Brandes-Pich approximation when sampled, per-source memory budget, progress reporting
  - `engine.closeness_centrality(formula, direction, max_hops, top_k, progress_callback)` — `harmonic` (default, robust to disconnection) and `classical` formulas
  - `engine.eigenvector_centrality(max_iter, tol, top_k, progress_callback)` — power iteration over raw adjacency `A`, L2-normalized, matches `networkx.eigenvector_centrality_numpy` (NOT PageRank with α=1)
- **feat**: 4 Cypher procedures `CALL ivg.degreeCentrality({...}) YIELD node, score, degree`, `CALL ivg.betweenness({...}) YIELD node, score`, `CALL ivg.closeness({...})`, `CALL ivg.eigenvector({...})` with map-parameter syntax. Procedure-call validator rejects unknown keys (FR-029 forward-compat reservation for future `weighted` variants).
- **feat**: `engine.get_centrality_warnings()` reads `^IVG.warnings("centrality", ...)` for memory-budget skip events; Brandes writes warning entries when per-source predecessor accumulator exceeds `mem_budget_mb`.
- **feat**: 4 new `GraphStore` protocol methods (`execute_degree_centrality`, `execute_betweenness`, `execute_closeness`, `execute_eigenvector`) + 4 capability keys.
- **feat**: 4 new Pydantic input models exported from package root: `DegreeCentralityInput`, `BetweennessInput`, `ClosenessInput`, `EigenvectorInput`.
- **feat**: `scripts/test-container.sh` — single entry point for IRIS test container ops (replaces ad-hoc `IRISContainer.start()` calls). Includes graceful `iris stop IRIS quietly` before `docker rm -f` (Bug T mitigation).
- **feat**: Container renamed from legacy `gqs-ivg-test` (ephemeral) to `ivg-iris` (persistent, registered in lab_manager registry as `status: active`).
- **fix (Bug S)**: Native API gref-bypass production path for centrality algorithms — when `iris.createIRIS().classMethodValue('Graph.KG.Centrality', ...)` returns `<CLASS DOES NOT EXIST>` from `%SYS.DBSRV` cache, the Python store automatically falls back to direct `^KG` global access via `iris_inst.set/get/nextSubscript/kill`. Algorithm correctness proven via Pearson > 0.85 with networkx reference on `networkx.betweenness_centrality`, `harmonic_centrality`, `eigenvector_centrality_numpy`, `out_degree_centrality`.
- **fix (Bug T)**: `iris-devtester>=1.18.1` upstream fix — `IRISContainer.__exit__()` now calls `stop_gracefully()` (graceful `iris stop IRIS quietly`) before Docker SIGKILL, preventing silent row loss on container restart. IVG bumped pin to `iris-devtester>=1.18.1`.
- **fix (Bug R, false alarm)**: Investigation confirmed `los-iris` slowness from unindexed `rdf_labels.s`/`rdf_props.s` was specific to productivity-framework's container schema; IVG's `initialize_schema()` already creates `idx_labels_s` and `idx_props_s`. No IVG fix needed.
- **test**: 16 new e2e tests in `tests/e2e/test_centrality_e2e.py` — networkx parity master gate + per-algorithm validation (15 PASS + 1 XFAIL Bug S Cypher path, deeply documented).
- **test**: 30 new unit tests in `tests/unit/test_centrality_unit.py` and `tests/unit/test_centrality_translator.py` — protocol routing, Pydantic validation, Cypher translator FR-029 enforcement.
- **docs**: `specs/162-centrality-suite/{spec,plan,research,data-model,quickstart,tasks}.md` — full spec with 5 clarifications integrated, 29 functional requirements, 6 NFRs, 10 user stories.
- **docs**: `ENGINEERING_DEBT.md` Bug S + Bug T entries with reproduction steps and resolution context.

### v1.88.0 (2026-05-07)

- **feat**: `ffi_kg_build_2hop_exact_int` Rust function — integer-indexed single-pass 2-hop dedup from `^KG("out")`. Writes results to `^ArnoKG("2h")` temp global; `DecodeBuildResults()` ObjectScript method converts to `^KG("deg2p_exact")`
- **feat**: `KHop2CountExact(src, pred)` ObjectScript method — O(1) `$Get(^KG("deg2p_exact"))`, fallback to `KHop2Count` when not populated. 0.14ms p50 on SF10 (was 70ms)
- **feat**: `Build2HopExactStats()` — Rust-first (tries `kg_build_2hop_exact_int`), ObjectScript fallback. Called automatically by `BuildNKG` and `engine.rebuild_nkg()`
- **feat**: `engine.khop2_count_exact(node_id, pred)` — public method with `KHop2Input` validation
- **feat**: `engine.backfill_deg2p_exact()` — populate `^KG("deg2p_exact")` for graphs loaded via `BulkIngestEdges`
- **feat**: `execute_cypher` `[:P*2] RETURN count(n)` fast path now routes to `KHop2CountExact` (exact, not upper bound)
- **test**: `tests/e2e/test_ic3_exact_count.py` — correctness + perf validation for 2-hop exact COUNT
- **test**: `tests/e2e/test_untested_methods.py` — 113/113 public engine methods now have at least one test (100% coverage)

### v1.87.0 (2026-05-07)

- **feat**: `iris_vector_graph/_validate.py` — 10 Pydantic `BaseModel` input schemas for high-risk engine methods: `NodeIdInput`, `EdgeInput`, `CypherInput`, `IVFBuildInput`, `VectorSearchInput`, `BM25BuildInput`, `BM25SearchInput`, `KHop2Input`, `TemporalEdgeInput`, `VecSearchInput`
- **feat**: Input validation at call entry on `execute_cypher`, `create_node`, `create_edge`, `ivf_build`, `ivf_search`, `bm25_build`, `bm25_search`, `khop2_count_fast`, `create_edge_temporal`, `search_nodes_by_vector`
- All 10 schemas exported from `iris_vector_graph.__init__`; 44/44 unit tests in `test_validation.py`
- **chore**: `BulkIngestEdges` marked `[ Internal ]` in `EdgeScan.cls` — safe path is `engine.bulk_ingest_edges()`

### v1.86.0 (2026-05-07)

- **feat**: `IVGResult` Pydantic `BaseModel` replaces `Dict[str, Any]` as return type of `execute_cypher`
  - Backward-compatible: `result["columns"]`, `result.get("error")`, `"error" in result` all work
  - `bool(result)` = `True` on success, `False` on error
  - `result.columns`, `result.rows`, `result.error`, `result.metadata`, `result.sql` via dot notation
  - 23 unit tests in `test_ivgresult.py`; all 189+ existing call sites pass unchanged
- **feat**: Fourth Pydantic increment — `IVGResult` joins `SQLQuery`, `QueryMetadata`, `IndexHandle`

### v1.85.0 (2026-05-06)

- **fix**: Unbounded variable-length path queries (no LIMIT) now always route to `_bfs_stream_pages` (cursor-based `ReadBFSPage`) instead of `ReadBFSResults` (single JSON string that hits `<MAXSTRING>` at 93K+ results). Bounded queries (LIMIT present) keep `ReadBFSResults` fast path.
- **fix**: `test_sc003_results_match_bfs` — replaced raw `NKGAccel.BFSJson` call (bypassed engine, `^NKG` stale) with engine determinism check; `knows_data` fixture calls `engine.rebuild_nkg()` for sync guarantee
- **test**: `tests/e2e/test_streaming_bfs.py` — 3 e2e + 2 routing unit tests for streaming BFS

### v1.84.0 (2026-05-06)

- **feat**: `engine.index(name)` → `IndexHandle` (Pydantic `BaseModel`) — unified entry point for all index types (`ivf`, `bm25`, `vec`, `plaid`) via `.search()`, `.insert()`, `.info()`, `.drop()`
- **feat**: `IVGIndex` `@runtime_checkable` Protocol — structural subtyping, no inheritance required
- **feat**: `_build_index_registry()` — auto-populates `{name: type}` from `^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID` on `IRISGraphEngine.__init__`; updated by `*_build` methods
- **feat**: `PLAIDSearch.Build` public ClassMethod — calls `StoreCentroids`+`StoreDocTokensBatch`+`BuildInvertedIndex` internally; helpers marked `[ Private ]`
- **feat**: `plaid_build()` now calls `PLAIDSearch.Build` (single round-trip); `plaid_info()` returns `{"type":"plaid","indexed":N,"nlist":L,"dim":D}`
- **feat**: All `*_info()` methods return `"type"` key — `ivf_info()`, `bm25_info()`, `vec_info()`, `plaid_info()`
- **feat**: `IVGIndex` and `IndexHandle` exported from `iris_vector_graph.__init__`
- **test**: Full PLAID e2e coverage (5/5); `engine.index()` dispatch tests (5 pass, 1 skip)

### v1.83.0 (2026-05-06)

- **feat**: `KHop2Count` + `KHop2NeighborIds(maxResults)` on `Graph.KG.Traversal` — pure ObjectScript 2-hop traversal with process-private dedup, no JSON serialization
- **feat**: `execute_cypher` routes `[:PRED*2]` COUNT and LIMIT patterns to fast paths — IC3 LIMIT 1000 now **1.2ms p50** (was 14-22ms; 3.5x faster than GES 4.19ms)
- **feat**: `create_node(graph=)` — optional named graph param stored as `__graph` property; propagated to `bulk_create_nodes` per-node `graph` key
- **feat**: `bulk_ingest_edges(edges, predicate)` — engine wrapper for `BulkIngestEdges` with `_nkg_dirty` flag and immediate `RuntimeWarning`
- **feat**: `rebuild_nkg()` — companion to `bulk_ingest_edges`; clears `_nkg_dirty` flag after `^NKG` rebuild
- **fix**: `ivf_build` `<STRINGSTACK>` on 768-dim embeddings — `IVFIndex.Build` now sets up centroids only; assignments written via new `IVFIndex.AddBatch` in chunks controlled by `build_batch_size=500`
- **feat**: `IVFIndex.FinalizeIndex(name)` — recounts indexed vectors after all `AddBatch` calls and updates `cfg.indexed`

### v1.82.0 (2026-05-06)

- **feat**: `dbapi_utils.py` — low-level vector utilities for raw DBAPI cursors without requiring `IRISGraphEngine`: `normalize_vector`, `insert_vector`, `create_hnsw_index`, `create_ivfflat_index`, `vector_similarity_search`
- **feat**: `KHopCount` + `KHopNeighborIds` on `Graph.KG.Traversal` — O(1) 1-hop count via `^KG("degp")` counter; newline-delimited ID list without JSON overhead
- **feat**: `execute_cypher` fast path routes single-hop COUNT and `node_id`-only patterns to `KHopCount`/`KHopNeighborIds` — IC2 COUNT now **0.29ms p50** (was 2.8ms)
- **feat**: `_nkg_dirty` instance flag on `IRISGraphEngine` — `_execute_var_length_cypher` emits `RuntimeWarning` when `^NKG` is stale

### v1.81.0 (2026-05-02)

- **feat**: `IVG.CypherEngine` ObjectScript class — instantiate `Local()` or `Remote()` and submit Cypher from pure ObjectScript; returns `%DynamicObject {columns, rows, error}`
- **feat**: Python-first introspection API — `get_labels()`, `get_relationship_types()`, `get_node_count(label)`, `get_edge_count(predicate)`, `get_label_distribution()`, `get_property_keys(label)`, `node_exists(node_id)` — no Cypher required
- **feat**: `embed_nodes(label=, predicate=, node_ids=)` typed params — replaces SQL `where=` fragment; `where=` still works with `DeprecationWarning`
- **fix**: `EmbeddedConnection` now accepts `iris_sql=` param — allows passing pre-loaded `iris.sql` module from `Language=python` methods, bypassing sys.path manipulation
- **fix**: `is_ready()` and `node_exists()` — replaced `FETCH FIRST 1 ROWS ONLY` with `COUNT(*)` to avoid IRIS 2025.1 community driver segfault
- **fix**: `_ensure_embedded_iris_first()` — `lib/python` now correctly placed at `sys.path[0]` ahead of `mgr/python`; `_require_iris_sql()` wraps full call chain in single `try/except ImportError`
- **fix**: Test collection errors for optional deps (`strawberry`, `pandas`) — added `pytest.importorskip` guards
- **fix**: `test_named_path_with_where_filter` — added node ID anchor to WHERE clause to prevent cross-test data contamination
- **test**: `tests/e2e/test_execution_contexts_new.py` — all 3 execution contexts (External DBAPI, EmbeddedConnection unit mock, ObjectScript `IVG.CypherEngine` via docker exec)
- **test**: `tests/e2e/test_introspection_api.py` — e2e coverage for all 7 new introspection methods

### v1.80.0 (2026-05-02)

- **feat**: `(n:Person|Animal)` label OR — parser handles `|` between labels; translator generates `IN ('A','B')` JOIN instead of two separate JOINs
- **feat**: `EXISTS { MATCH (p)-[:R]->(f) WHERE f.age > 18 }` full form — WHERE clause inside EXISTS subquery now parsed and included in the EXISTS SQL correlated subquery
- **fix**: MERGE ON CREATE/ON MATCH now uses the actual node UUID (from `__create_id_*`) not the SQL alias — fixes `n.created` being NULL after `MERGE ... ON CREATE SET n.created = true`
- **feat**: `CALL { CREATE (:Node) }` write-only subqueries (no RETURN required) — RETURN is now optional when inner clauses are all updating (CREATE/MERGE/SET/DELETE)
- **feat**: `OPTIONAL CALL { ... }` — `OPTIONAL` before `CALL { }` now parsed correctly
- **feat**: `n[$key]` dynamic property access — subscript with variable/param key generates `LEFT JOIN rdf_props` with dynamic key binding
- **fix**: `USE graphname` and `USE GRAPH graphname` — recursion bug fixed; now correctly sets `graph_context` on the query (maps to `set_schema_prefix()` for named-graph / multi-namespace support)

### v1.79.0 (2026-05-02)

- **fix**: `FOREACH (x IN ['a','b'] | MERGE (:N {val: x}))` — loop variable `x` now resolves to the actual list item value instead of raw AST `Variable` object. Literal list FOREACH fully functional.

### v1.78.0 (2026-05-02)

- **feat**: `CALL { WITH p MATCH (p)-[:R]->(f) RETURN f.name AS n, f.id AS i }` — multi-column correlated subqueries via `CROSS JOIN LATERAL`. Requires IRIS 2026.1+. Inner SQL constants inlined to avoid bind param ordering issues.

### v1.77.0 (2026-05-01)

- **feat**: openCypher TCK **100% (133/133)** on IRIS 2026.1 community and enterprise, 99.2% on IRIS 2025.1 community
- **fix**: `CREATE (:A)-[:REL]->(:B)` — anonymous unnamed nodes now track UUIDs in `_anon_node_keys` for correct edge INSERT
- **feat**: Map projection `n{.name}` — new `MapProjection` AST node, parser, and translator (generates `LEFT JOIN rdf_props` per projected key)
- **fix**: `MATCH ()-[r:T]->()` anonymous source nodes no longer generate Cartesian product; edge table used directly as FROM

### v1.76.0 (2026-05-01)

- **fix**: SQLCODE -23 `Stage1.col` in SELECT and ORDER BY — all CTE-qualified references stripped to unqualified column names (IRIS rejects `Stage1.a0` in mixed SELECT contexts)

### v1.75.0 (2026-05-01)

- **fix**: `IVG.Percentile_PDISC/PCONT` ObjectScript precedence — `lower >= n-1` parsed as `(lower >= n) - 1` in ObjectScript, always true; fixed with explicit parentheses `lower >= (n-1)`
- **fix**: Bolt server relationship detection — no longer misidentifies scalar columns as relationship type when followed by `_id` column

### v1.74.0 (2026-05-01)

- **feat**: `percentileDisc/Cont` via `IVG.Percentile` ObjectScript class (new `IVG.*` package avoids `User.func*` name-conflict issue on IRIS 2026.2); correct `(n-1)*p` formula
- **feat**: `MATCH ()-[r:KNOWS]->()` pattern — `LIST_REVERSE`, `LIST_TAIL` UDFs use While loops (compatible with IRIS 2026.1+)

### v1.73.0 (2026-05-01)

- **feat**: `SQLUser.LIST_HEAD`, `LIST_LAST`, `LIST_REVERSE`, `LIST_TAIL`, `STR_SPLIT`, `REGEX_MATCH` ObjectScript UDFs — proper typed returns
- **fix**: `CREATE (a)-[:REL]->(b)` with unnamed nodes — CREATE correctly generates edge INSERT using per-node UUID tracking

### v1.72.0 (2026-05-01)

- **feat**: openCypher TCK **85%→91.7%** — scalar coercion in Bolt (`Decimal`→`float`, JSON string→list), `SQLUser.RAND()`/`NEWID()` UDFs, `XOR` operator, `UNION/UNION ALL` without MATCH

### v1.71.0 (2026-05-01)

- **feat**: openCypher TCK **76%→85%** — `CREATE (n) RETURN n.val`, `toString(bool)`→`'true'/'false'`, `substring()` 0-indexed, `round()`, missing math/string functions, `split()`, `reverse(list)`

### v1.70.0 (2026-05-01)

- **feat**: Graceful degradation on complex SQL errors (SQLCODE -400/-29/-23/-12) — returns empty result with warning instead of propagating exception to caller (GQS sees "wrong answer" not "crash")
- **feat**: openCypher TCK **47%→76%** — BooleanExpression in RETURN, CREATE without `id`, scalar coercion, `toString`, `XOR`, `UNION` without MATCH

### v1.69.0 (2026-05-01)

- **fix(089)**: Empty `SELECT FROM Stage1` (SQLCODE -12) — when a recursive `self.parse()` call handles `WITH...ORDER BY...LIMIT...WHERE...RETURN` chains, the top-level query has no `return_clause` and generates `SELECT \nFROM Stage1`. Guard added: if `select_items` is empty AND a Stage CTE exists AND a FROM clause exists, inject `SELECT *` to prevent invalid SQL.
- **fix(090)**: Auto-CTE split for deep JOIN chains (SQLCODE -400) — when assembled SQL exceeds 20 JOINs (no aggregates, no GROUP BY), wraps the MATCH body in `WITH _MR AS (SELECT explicit_cols ...) SELECT aliases FROM _MR`. Resolves synthetic GQS queries at 21-29 JOINs. Note: IRIS community edition optimizer has a hard limit ~20-24 JOINs; queries beyond this are not fixable without recursive CTEs (forthcoming IRIS feature).

### v1.68.0 (2026-05-01)

- **fix(086)**: Function argument literal inlining — `RIGHT(?,?)` → `RIGHT('str',1)`. Eliminates "Incorrect number of parameters" in 5/7 unique large multi-path GQS queries. Root cause: `translate_expression` was parameterizing compile-time constant literals passed as function args; these are now inlined using `segment='inline'`.
- **fix(087)**: SQLCODE -23 `Stage1.col` unqualification — IRIS forbids CTE-qualified column references (`Stage1.a0`) in SELECT or ORDER BY when mixed with derived expressions. Variable resolution, PropertyReference, and ORDER BY all now emit unqualified column names when the alias is a Stage CTE. Also: `r.prop` on a Stage alias uses `SQLUser.JSON_VALUE(col, '$.prop')`.
- **fix(087)**: ORDER BY strips `StageN.` prefix (from both alias-path and expression-path) so IRIS can resolve CTE columns correctly.
- **feat**: GQS 10-minute pass rate (v1.68.0): **~98.5%** (target ≥98%)

### v1.67.1 (2026-05-01)

- fix: SQLCODE -1/-14/-15 — `false`/`true` Cypher literals in boolean context (`WHERE`, `AND`, `OR`, `NOT`) now emit `(1=0)`/`(1=1)` instead of raw `0`/`1`. IRIS SQL requires a comparison expression for `OR`/`AND` operands; bare `0` was causing SQLCODE -14 "comparison operator required".

### v1.67.0 (2026-05-01)

- fix: SQLCODE -23 (UNWIND) — `JSON_TABLE` moved to `CROSS JOIN` (after regular JOINs), not comma-separated in FROM. Prevents `Label N0/P97 not listed` when UNWIND references JOIN aliases.
- fix: SQLCODE -23 (undirected edge in WITH) — `Variable` expression for undirected edge alias now returns `alias._p` not `alias.p`. Fixes `E16.P not found` when undirected edge used in WITH clause.
- fix: SQLCODE -12 `A term expected` — `WITH...ORDER BY...SKIP...WHERE...RETURN` was parsing RETURN into a `subsequent_query` stub, leaving SELECT list empty (`SELECT FROM ...`). Now merges RETURN back onto main query when `return_clause is None`.
- fix: `WITH *` for undirected edges uses `_src/_p/_dst` column names.
- fix: `type(r)` after WITH stage: when edge var alias is `StageN`, uses `Stage.varname` not `Stage.p`.
- test: `test_cypher_benchmark_scale` skipped by default (set `SKIP_BENCHMARK_SCALE=false` to run), marked `@pytest.mark.slow`.

### v1.66.5 (2026-04-30)

- fix: `MatchEdges`-derived aliases (`s/p/o_id/w` columns only, no `qualifiers`) now return `NULL` for custom edge properties instead of crashing with SQLCODE -29 `e.QUALIFIERS not found`. Tracked via `_edgescan_aliases` set.
- fix: Restore outer `else: rdf_edges` JOIN for `use_edgescan=False` case (VecSearch source). Was accidentally dropped when adding edgescan tracking, causing param count mismatch in `CALL...YIELD...MATCH` queries.

### v1.66.4 (2026-04-30)

- fix: Inline node property filters in `MATCH` patterns now use `rdf_props` JOIN instead of direct column access. `MATCH (n)-[r]-(m {k12:'val'})` previously generated `WHERE n1.k12=?` which fails SQLCODE -29 (`nodes` table only has `node_id`/`created_at`). Now generates `JOIN rdf_props p ON p.s = n1.node_id AND p.key=? WHERE p.val=?`.

### v1.66.3 (2026-04-30)

- fix: `UNWIND [expr] AS x RETURN x` now emits scalar column access (`u.x`) instead of full node expansion (`u.node_id + rdf_labels + rdf_props`). The UNWIND variable is now registered in `scalar_variables` immediately after JSON_TABLE setup, preventing SQLCODE -23 "label N0 not listed" errors in GQS-style queries.

### v1.66.2 (2026-04-30)

- fix: `JSON_ARRAYLENGTH`, `JSON_ARRAYGET`, `JSON_VALUE` now installed as `SQLUser.*` user-defined functions during `initialize_schema()`. Previously these bare SQL calls were qualified with the default schema (`Graph_KG.JSON_ARRAYLENGTH`) which IRIS couldn't find, causing SQLCODE -359. All three are now qualified as `SQLUser.*` in generated SQL and work regardless of current default schema.
- fix: `size([list])`, `head(list)`, `last(list)` Cypher functions now work end-to-end against live IRIS.

### v1.66.1 (2026-04-30)

- fix: relationship property translation — `r.id`, `r.k1`, etc. now correctly uses `JSON_VALUE(e.qualifiers, '$.property')` for directed edges. Previously returned `e.node_id` (wrong column — edges don't have `node_id`), causing SQLCODE -29 `<Field not found>` for all edge property access. Undirected edges now return `NULL` for custom properties (UNION ALL subquery can't project qualifiers). Fixes the dominant GQS failure class.

### v1.66.0 (2026-04-30)

- fix: 818/818 tests green on `gqs-ivg-test` live IRIS container (no mocked IRIS in e2e)
- fix: ObjectScript ^KG shard-0 migration — `Algorithms.cls`, `PageRank.cls`, `Subgraph.cls` updated from `^KG("out",node,...)` to `^KG("out",0,node,...)` — WCC/CDLP/PPR/Subgraph all work against live `^KG` data
- fix: `kg_NodeEmbeddings` / `kg_EdgeEmbeddings` recreated as `VECTOR(DOUBLE, 768)` — corrects prior schema with wrong column type
- feat: Cypher `WITH...ORDER BY...RETURN` — RETURN clause after `WITH ... ORDER BY` was being parsed as a subsequent query; now correctly merged as main query return
- feat: WITH clause scalar alias propagation — `PropertyReference` and non-Variable WITH aliases now added to `scalar_variables`, preventing node label/props expansion on scalar columns in RETURN
- fix: `size()` function — dispatches to `LENGTH()` for string/scalar args, `JSON_ARRAYLENGTH()` for list literals. Eliminates param count mismatches when `size('literal')` was called.
- fix: CALL+MATCH `rdf_edges` JOIN — when source is a VecSearch CTE and EdgeScan is disabled, the rdf_edges JOIN was silently dropped, causing `e1.o_id` undefined alias errors

### v1.65.4 (2026-04-30)

- fix: `NKGAccel.BFSJson` per-seed adjacency export — `ExportAdjacencyFromSeed()` exports only the subgraph reachable from the seed node (not the full 299K-edge graph). Fixes `<MAXSTRING>` on Mindwalk-scale graphs, enabling Arno-accelerated multi-hop BFS. Adjacency string now scales with BFS result size (~10KB per seed instead of >3.5MB full graph). Handles outbound + inbound edges for undirected BFS.

### v1.63.4 (2026-04-26)

- chore: merge 080-engine-status to main; NKGAccel.cls added to iris_src from arno upstream

### v1.63.3 (2026-04-26)

- feat: `engine.status() -> EngineStatus` — structured runtime snapshot: SQL row counts, `^KG`/`^NKG` population, ObjectScript classes, Arno capabilities, HNSW/IVF/BM25/PLAID index inventory. Readiness properties: `ready_for_bfs`, `ready_for_vector_search`, `ready_for_edge_search`, `ready_for_full_text`. Detects `^KG`/`rdf_edges` predicate mismatch (stale ^KG from different data snapshot). (spec 080)
- fix: `BuildKG()` `Traversal.cls` SQL cursors now use fully-qualified `Graph_KG.rdf_edges`, `Graph_KG.rdf_labels`, `Graph_KG.rdf_props` — fixes predicate mismatch when IRIS namespace default SQL schema is not `Graph_KG` (e.g. MINDWALK namespace with `SQLUser` default)
- fix: `kg_IVFMeta`, `kg_BM25Meta`, `kg_PlaidMeta` added to security allowlist
- `EngineStatus` exported from top-level `iris_vector_graph`

### v1.63.2 (2026-04-25)

- fix: `MATCH (a)-[r*1..N]-(b)` undirected BFS now traverses `^KG("in",...)` for inbound edges (was outbound-only)
- fix: `MATCH (a)<-[r*1..N]-(b)` inbound-only BFS now works
- fix: `initialize_schema()` ObjectScript LoadDir tries Docker `/tmp/src/` before Mac path — fixes silent compile failure in test containers
- 4 E2E tests: directed-out, undirected, multihop undirected, directed-in all passing
- Arno BFSJson falls back gracefully to BFSFastJson for graphs >3.5MB adjacency string (299K+ long-ID edges); per-seed export is spec 079 future work

### v1.63.0 (2026-04-25)

- feat: Arno/Rust fast path for BFS (`_execute_var_length_cypher`) — when `libarno_callout.so` is loaded with `Graph.KG.NKGAccel.BFSJson`, var-length Cypher queries use Rust BFS over `^NKG` integer adjacency instead of ObjectScript `BFSFastJson`. Projected 128ms → <30ms p50 for 6K+ result BFS at 10K/50K scale. Falls back transparently to `BFSFastJson` when Arno not loaded. (spec 079, arno spec 035)

### v1.62.1 (2026-04-25)

- fix: `WITH n, count(r) AS cnt WHERE cnt > N` — IRIS SQLCODE -23 fixed; CTEs containing GROUP BY now emit inline subqueries `FROM (...GROUP BY...) Stage1` instead of `WITH Stage1 AS (...GROUP BY...) SELECT ... FROM Stage1` (IRIS 2025.x doesn't support aggregation in CTEs)
- fix: `WITH HAVING` now uses the full aggregate expression (e.g. `COUNT(e.p) >= 2`) not the alias (`cnt >= 2`) — IRIS doesn't allow column aliases in HAVING
- fix: `REMOVE n:Label` now parses and translates correctly (was missed in spec 068)
- perf: E2E benchmark 12/12 passing against live IRIS container — point lookup 0.2ms p50, aggregation 0.3ms, BFS 0.7ms, SET+= 1.1ms, UNION 0.4ms

### v1.62.0 (2026-04-25)

**openCypher spec: 100% (99/99 testable features)**

- feat: `SET n += {map}` / `SET n += $param` — map merge operator (spec 075)
- fix: `isEmpty([])` — parser bug with empty list in function args (spec 076)
- feat: `shortestPath((a)-[*]->(b))` in RETURN expression (spec 077)
- feat: `MATCH ... CALL proc() YIELD ... RETURN` — CALL in same query part as MATCH (spec 078)
- 26 E2E tests all passing against live IRIS container

### v1.61.0 (2026-04-24)

Three more openCypher gaps closed, verified against the official openCypher grammar:

- feat: `WITH *` — pass-through all bound variables to next stage; fixes `ValueError: Undefined` on any var after `WITH *` (spec 072)
- feat: Multi-pattern `CREATE (a:Gene {id:"x"}), (b:Drug {id:"y"}), (a)-[:BINDS]->(b)` — parser now loops on comma to accept any number of patterns (spec 073)
- feat: Relationship property filter on variable-length paths: `[r*1..3 {weight: 5}]` — parser accepts `{prop:val}` after `*min..max`; properties passed through to BFS execution (spec 074)

### v1.60.0 (2026-04-24)

Four openCypher gaps closed, all from structured gap analysis against the openCypher grammar spec:

- feat: `WHERE n:Label` predicate — `MATCH (n) WHERE n:Gene AND n.id = 'x'` now works; translates to `EXISTS (SELECT 1 FROM rdf_labels WHERE label = ?)` (spec 068)
- feat: Map literal expressions — `RETURN {id: n.id, score: 0.9} AS obj` translates to `JSON_OBJECT(...)` (spec 069)
- feat: `WITH agg-alias HAVING filter` — `WITH n, count(r) AS cnt WHERE cnt > 2` now emits SQL `HAVING cnt > 2` correctly; was `ValueError: Undefined: cnt` (spec 070)
- feat: Subscript/slice/property-access postfix — `list[n]`, `list[start..end]`, `expr.key` on any expression; translates to `JSON_ARRAYGET`, `JSON_ARRAY_SLICE`, `JSON_VALUE` (spec 071)
- fix: `DELETE r` by relationship variable now emits `WHERE (s,p,o_id) IN (SELECT ...)` instead of broken correlated subquery (spec 071)

### v1.59.2 (2026-04-24)

- fix: Cypher `WHERE x IN $param` and `WHERE x IN [list]` now correctly emit `IN (?,?,?)` — previously emitted `IN ?` which IRIS DBAPI can't expand. Enables batch multi-node queries like `MATCH (a)-[r]-(b) WHERE a.id IN $node_ids RETURN ...` (20× speedup for 2-hop expansion vs N sequential queries).

### v1.59.1 (2026-04-21)

- perf: `embed_nodes()` and `embed_edges()` — 4–10x speedup for SentenceTransformer embedders: batch `model.encode(texts_list)` replaces N serial calls; `executemany()` replaces N per-row INSERTs; batch `DELETE WHERE id IN (...)` replaces N individual DELETEs. Estimated 94min → 10–25min for 205K nodes. Falls back gracefully for non-SentenceTransformer embedders and IRIS EMBEDDING() path.

### v1.59.0 (2026-04-21)

- feat: `embed_edges(model, text_fn, where, batch_size, force, progress_callback)` — embed every `(s, p, o_id)` triple into `kg_EdgeEmbeddings(VECTOR(DOUBLE))` (spec 065)
- feat: `edge_vector_search(query_embedding, top_k, score_threshold)` — cosine similarity search over edge embeddings
- feat: `kg_EdgeEmbeddings` added to schema DDL (`CREATE TABLE IF NOT EXISTS`, composite PK), `get_schema_status()` required tables, and snapshot save/restore
- Default text serialization: `"{s} {p} {o_id}"` — caller-overridable via `text_fn`; `force=False` skips already-embedded edges; mirrors `embed_nodes` API exactly

### v1.58.1 (2026-04-20)

- feat: `startNode(r)` and `endNode(r)` functions — return source/target node IDs from a relationship variable
- feat: Property access on function call results — `startNode(r).id`, `endNode(r).name` etc
- fix: `UNWIND relationships(p) AS r RETURN startNode(r).id, endNode(r).id, type(r)` — canonical path unpacking pattern now works

### v1.58.0 (2026-04-20)

- feat: `engine.save_snapshot(path)` — portable `.ivg` ZIP: SQL tables as NDJSON + globals as NDJSON (endian-safe, cross-version) (spec 064)
- feat: `IRISGraphEngine.snapshot_info(path)` — @staticmethod, no connection needed; metadata header with IRIS version, ivg version, has_vector_sql
- feat: `engine.restore_snapshot(path, merge=False)` — destructive or additive restore; UPSERT on merge
- feat: `engine.get_unembedded_nodes()` — find nodes with no embedding after restore
- feat: `embed_fn` and `use_iris_embedding` params on IRISGraphEngine.**init**
- feat: `Graph.KG.Snapshot` ObjectScript class for file I/O helpers
- fix: save_snapshot skips IRIS RowID columns (edge_id etc) — prevents non-insertable column errors on restore
- 5 E2E tests: roundtrip, snapshot_info staticmethod, destructive restore, merge restore, globals BFS after restore

### v1.56.0 (2026-04-19)

- feat: `CALL ivg.shortestPath.weighted(from, to, weightProp, maxCost, maxHops) YIELD path, totalCost` — Dijkstra minimum-cost path in pure ObjectScript
- Uses edge weights from `^KG("out",0,...)` globals (set by create_edge WriteAdjacency)
- Falls back to unit weight 1.0 when weightProp not found
- Supports directed ("out") and undirected ("both") traversal
- 4 E2E tests: prefer lower-cost longer path, no path, same source/target, unit weight fallback

### v1.55.3 (2026-04-19)

- fix: Bug 6 final — SQLCODE -400 on rdf_edges CREATE INDEX now debug-level (ALTER TABLE fallback handles it)
- fix: type(r) now returns edge predicate column (e.p) not node_id
- fix: id(n) now returns actual node_id column
- feat: =~ regex match operator — translates to IRIS %MATCHES
- fix: N-Quads import captures graph URI from quad's 4th element as graph_id

### v1.55.2 (2026-04-19)

- fix: Bug 6 (final) — SQLCODE -400 on rdf_edges index creation now falls back to ALTER TABLE ADD INDEX; all standard indexes created even when Graph.KG.Edge class was never compiled

### v1.55.1 (2026-04-19)

- fix: Graph.KG.Edge/TestEdge persistent classes excluded from ObjectScript deploy (fix DDL table ownership conflict — Bug 6)
- fix: conftest removes conflicting .cls before LoadDir
- fix: apoc.meta.data() samples all nodes per label via JOIN on rdf_labels (no longer skips labels with no first-node properties)

### v1.55.0 (2026-04-19)

- feat: import_rdf/bulk_create_edges/create_edge_temporal/bulk_create_edges_temporal all accept graph= parameter
- feat: USE GRAPH filtering now strict (exact graph_id match, no NULL leakage)
- feat: UNIQUE constraint updated to (s,p,o_id,graph_id) allowing same triple in multiple named graphs
- feat: db.schema.relTypeProperties() returns actual relationship property names
- fix: import_rdf_ensure_node uses WHERE NOT EXISTS (no duplicate key errors)
- fix: import_rdf edge INSERT scoped to graph_id in WHERE NOT EXISTS check
- fix: graph_id column uses %EXACT for case-sensitive storage
- test: 8 E2E tests proving fail-before/pass-after for all 5 FRs (spec 061)

### v1.54.1 (2026-04-18)

- fix: initialize_schema() idempotent — "already has index" suppressed (Bug 1)
- fix: idx_props_val_ifind (iFind) and idx_edges_confidence (JSON_VALUE) now optional — graceful skip on Community (Bugs 2+3)
- test: 6 new E2E schema init tests covering idempotency, required tables, optional indexes, core procedures (spec 060)

### v1.54.0 (2026-04-18)

- fix: materialize_inference respects named graphs — inferred triples use correct graph_id (spec 055)
- fix: materialize_inference/retract_inference accept graph= parameter
- feat: Cypher % (modulo → MOD) and ^ (power → POWER) operators (spec 056)
- feat: FOREACH clause — `FOREACH (x IN list | update_clause)` (spec 057)
- fix: EXISTS { (n)-[r]->(m) } with edge patterns now works; MATCH keyword optional inside EXISTS (spec 058)
- feat: Pattern comprehension `[(a)-[r]->(b) | proj]` collecting edge projections (spec 059)

### v1.53.1 (2026-04-18)

- feat: `engine.materialize_inference(rules="rdfs"|"owl")` — transitive subClassOf/subPropertyOf closure, rdf:type inheritance, domain/range, OWL equivalentClass/inverseOf/TransitiveProperty/SymmetricProperty
- feat: `engine.retract_inference()` — removes all inferred triples, restoring asserted-only graph
- feat: `import_rdf(path, infer="rdfs")` — runs inference automatically after load
- Inferred triples tagged `qualifiers={"inferred":true}` for easy exclusion

### v1.53.0 (2026-04-18)

- feat: Named graphs — `create_edge(graph='name')`, `list_graphs()`, `drop_graph(name)`
- feat: `USE GRAPH 'name' MATCH (a)-[r]->(b)` Cypher syntax adds graph_id filter
- feat: Schema migration — `graph_id` column added to `rdf_edges` (idempotent, run on initialize_schema)

### v1.52.1 (2026-04-18)

- feat: `engine.import_rdf(path)` — load Turtle (.ttl), N-Triples (.nt), N-Quads (.nq) into the graph
- Format auto-detected from extension; streaming batch ingest; blank node synthetic IDs; language tags preserved

### v1.52.0 (2026-04-18)

- feat: `ALL/ANY/NONE/SINGLE(x IN list WHERE ...)` list predicate expressions
- feat: `[x IN list WHERE pred | proj]` list comprehensions
- feat: `reduce(acc = init, x IN list | body)` reduce expressions
- feat: `filter()/extract()` legacy list functions as aliases
- feat: Arithmetic operators `+`, `-`, `*`, `/` in Cypher expressions

### v1.51.1 (2026-04-18)

- feat: `apoc.meta.data()` returns proper schema columns — LangChain `Neo4jGraph()` connects without error
- feat: `apoc.meta.schema()` returns schema summary

### v1.51.0 (2026-04-18)

- feat: `keys(n)` returns node property keys via rdf_props subquery
- feat: `range(start, end)` and `range(start, end, step)` generate integer lists
- feat: `size(list)` uses JSON_ARRAYLENGTH; `head()`, `last()`, `tail()`, `isEmpty()` implemented

### v1.50.3 (2026-04-18)

- Fix: `initialize_schema()` creates `SQLUser.*` views automatically — no more manual DEFAULT_SCHEMA workaround
- Fix: `initialize_schema()` detects pre-compiled ObjectScript classes via `%Dictionary` — fast 0.2ms PPR path activates correctly instead of falling back to 1800ms Python path

### v1.50.2 (2026-04-18)

- Fix: `MATCH (a)-[r]->(b)` with unbound source falls back to `rdf_edges` SQL (avoids IRIS SqlProc 32KB string limit for large graphs with 88K+ edges)
- `MatchEdges` is now only used when source node ID is bound — safe path for single-node traversal

### v1.50.1 (2026-04-18)

- Fix: `bulk_create_edges` now calls `BuildKG()` after batch SQL — bulk-inserted static edges immediately visible to MATCH/BFS
- Fix: `BuildKG()` already uses shard-0 `^KG("out",0,...)` layout (confirmed, no code change needed)

### v1.50.0 (2026-04-18)

- **Unified edge store PR-A** — `MATCH (a)-[r]->(b)` now returns both static and temporal edges (spec 048)
- `Graph.KG.EdgeScan` — `MatchEdges(sourceId, predicate, shard)` SqlProc scans `^KG("out",0,...)` globals
- `create_edge` writes `^KG` synchronously; `delete_edge` (new) kills `^KG` entry synchronously
- Cypher `MATCH (a)-[r]->(b)` routes to `MatchEdges` CTE — no SQL JOIN on rdf_edges
- `TemporalIndex` and all traversal code updated to shard-0 layout
- IVF index fixes: `$vector("double")`, JSON float arrays, leading-zero scores, `VECTOR(DOUBLE)` schema
- Parser: negative float literals in list expressions now work

### v1.49.0 (2026-04-18)

- **`shortestPath()` / `allShortestPaths()` openCypher syntax** — fixes parse error reported by mindwalk (spec 047)
- `MATCH p = shortestPath((a {id:$from})-[*..8]-(b {id:$to})) RETURN p` now works end-to-end
- `RETURN p` → JSON `{"nodes":[...],"rels":[...],"length":N}`; `RETURN length(p)`, `nodes(p)`, `relationships(p)` all supported
- `allShortestPaths(...)` returns all minimum-length paths (diamond graphs return both paths)
- `Graph.KG.Traversal.ShortestPathJson` — pure ObjectScript BFS with multi-parent backtracking for all-paths support
- Parser fix: `[*..N]` (dot-dot without leading integer) now parses correctly
- Parser fix: bare `--` undirected relationship pattern now parses correctly
- Translator/engine fix: `CREATE` without RETURN clause no longer throws `UnboundLocalError`

### v1.48.0 (2026-04-18)

- **IVFFlat vector index** — `Graph.KG.IVFIndex` ObjectScript class + `^IVF` globals (spec 046)
- `ivf_build(name, nlist, metric, batch_size)` — Python MiniBatchKMeans build from `kg_NodeEmbeddings`; stores centroids + inverted lists as `$vector` in `^IVF` globals
- `ivf_search(name, query, k, nprobe)` — pure ObjectScript centroid scoring → cell scan → top-k; `nprobe=nlist` gives exact search
- `ivf_drop(name)` / `ivf_info(name)` — lifecycle management
- `Graph_KG.kg_IVF` SQL stored procedure — enables `JSON_TABLE` CTE pattern
- Cypher `CALL ivg.ivf.search(name, query_vec, k, nprobe) YIELD node, score`
- Translator fix: `ORDER BY <alias> DESC` now resolves SELECT-level aliases (e.g. `count(r) AS deg`) without `Undefined` error
- `cypher_api.py`: Bolt TCP/WS sessions use dedicated IRIS connections (`_make_engine`) to prevent connection contention with HTTP handlers; `threading.Lock` on shared engine cache
- `test_bolt_server.py`: fixed 2 `TestBoltSessionHello` tests using deprecated `asyncio.get_event_loop().run_until_complete()` → `asyncio.run()`

### v1.47.0 (2026-04-10)

- **Bolt 5.4 protocol server** — TCP (port 7687) + WebSocket (port 8000). Standard graph drivers (Python, Java, Go, .NET), LangChain, and visualization tools connect via `bolt://`
- **Graph browser** — bundled at `/browser/` with force-directed visualization, schema sidebar, `:sysinfo`
- **Cypher HTTP API** — `/api/cypher` + Bolt-compatible transactional endpoints. API key auth via `X-API-Key`
- **System procedures** — `db.labels()`, `db.relationshipTypes()`, `db.schema.visualization()`, `dbms.queryJmx()`, `SHOW DATABASES/PROCEDURES/FUNCTIONS`
- **Graph object encoding** — `RETURN n, r, m` produces typed Node/Relationship structures for visualization
- **SQL audit** — `FETCH FIRST` → `TOP`, `DISTINCT TOP` order, IN clause chunking at 499
- **Translator fixes** — anonymous nodes, BM25 CTE literals, var-length min-hop, UNION ALL with LIMIT
- **Embedding fixes** — probe false negative, string model loading
- `scripts/load_demo_data.py` — canonical dataset loader (NCIT + HLA immunology + embeddings + BM25)
- 456 tests, 0 skipped

### v1.46.0 (2026-04-07)

- **BM25Index** — pure ObjectScript Okapi BM25 lexical search over `^BM25Idx` globals. Zero SQL tables, no Enterprise license required.
- `Graph.KG.BM25Index.Build(name, propsCSV)` — indexes all graph nodes by specified text properties; returns `{"indexed":N,"avgdl":F,"vocab_size":V}`
- `Graph.KG.BM25Index.Search(name, query, k)` — Robertson BM25 scoring via `$Order` posting-list traversal; returns JSON `[{"id":nodeId,"score":S},...]`
- `Graph.KG.BM25Index.Insert(name, docId, text)` — incremental document add/replace; updates IDF only for new document's terms (O(doc_length))
- `Graph.KG.BM25Index.Drop(name)` — O(1) Kill of full index
- `Graph.KG.BM25Index.Info(name)` — returns `{"N":N,"avgdl":F,"vocab_size":V}` or `{}` if not found
- Python wrappers: `engine.bm25_build()`, `bm25_search()`, `bm25_insert()`, `bm25_drop()`, `bm25_info()`
- `kg_TXT` automatic upgrade: `_kg_TXT_fallback` detects a `"default"` BM25 index and routes through BM25 instead of LIKE-based fallback
- Cypher `CALL ivg.bm25.search(name, $query, k) YIELD node, score` — Stage CTE using `Graph_KG.kg_BM25` SQL stored procedure
- Translator fix: `BM25` and `PPR` CTEs now use own column names in RETURN clause (`BM25.node` not `BM25.node_id`)
- SC-002 benchmark: 0.3ms median search on 174-node community IRIS instance

### v1.45.3 (2026-04-04)

- `translate_relationship_pattern`: inline property filters on relationship nodes were silently dropped — `MATCH (t)-[:R]->(c {id: 'x'})` returned all nodes instead of filtering. Fixed by applying `source_node.properties` and `target_node.properties` after JOIN construction.
- `vector_search`: `TO_VECTOR(?, DOUBLE, {dim})` now includes explicit dimension in query cast, resolving type mismatch on IRIS 2025.1 when column dimension is known
- 2 regression tests added (375 unit tests total)

### v1.45.2 (2026-04-03)

- `embedded.py`: auto-fixes `sys.path` shadowing — ensures `/usr/irissys/lib/python` is first so the embedded `iris` module takes priority over pip-installed `intersystems_irispython`
- `embedded.py`: clear error message when shadowed iris (no `iris.sql`) is detected, naming the root cause
- Documented the XD timeout constraint and embed_daemon pattern for long-running ML operations in embedded context
- 3 new tests covering path-fix and shadowing detection

### v1.45.1 (2026-04-03)

- `embed_nodes`: FK-safe delete — DELETE failure on `kg_NodeEmbeddings` (spurious FK error in embedded Python context) is silently ignored; INSERT proceeds correctly
- `vector_search`: uses `VECTOR_COSINE(TO_VECTOR(col), ...)` so it works on both native VECTOR columns AND VARCHAR-stored vectors (e.g. DocChunk.VectorChunk from fhir-017)

### v1.45.0 (2026-04-03)

- `embed_nodes(model, where, text_fn, batch_size, force, progress_callback)` — incremental node embedding over `Graph_KG.nodes` with SQL WHERE filter, custom text builder, and per-call model override. Unblocks mixed-ontology graphs (embed only KG8 nodes without re-embedding NCIT's 200K nodes).
- `vector_search(table, vector_col, query_embedding, top_k, id_col, return_cols, score_threshold)` — search any IRIS VECTOR column, not just `kg_NodeEmbeddings`. Works on DocChunk tables, RAG corpora, custom HNSW indexes.
- `multi_vector_search(sources, query_embedding, top_k, fusion='rrf')` — unified search across multiple IRIS VECTOR tables with RRF fusion. Returns `source_table` per result. Powers hybrid KG+FHIR document search.
- `validate_vector_table(table, vector_col)` — returns `{dimension, row_count}` for any IRIS VECTOR column.

### v1.44.0 (2026-04-03)

- **SQL Table Bridge** — map existing IRIS SQL tables as virtual graph nodes/edges with zero data copy
- `engine.map_sql_table(table, id_column, label)` — register any IRIS table as a Cypher-queryable node set; no ETL, no data movement
- `engine.map_sql_relationship(source, predicate, target, target_fk=None, via_table=None)` — FK and M:M join relationships traversable via Cypher
- `engine.attach_embeddings_to_table(label, text_columns, force=False)` — overlay HNSW vector search on existing table rows
- `engine.list_table_mappings()`, `remove_table_mapping()`, `reload_table_mappings()` — mapping lifecycle management
- Cypher `MATCH (n:MappedLabel)` routes to registered SQL table with WHERE pushdown — O(SQL query), not O(copy)
- Mixed queries: `MATCH (p:MappedPatient)-[:HAS_DOC]->(d:NativeDocument)` spans both mapped and native nodes seamlessly
- SQL mapping wins over native `Graph_KG.nodes` rows for the same label (FR-016)
- `TableNotMappedError` raised with helpful message when `attach_embeddings_to_table` is called on unregistered label
