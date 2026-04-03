# Tasks: SQL Table Bridge

**Input**: Design documents from `/specs/040-sql-table-bridge/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story. Tests written first per Constitution III (TDD non-negotiable).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US5 per spec.md

---

## Phase 1: Setup

- [ ] T001 Add `table_mappings` and `relationship_mappings` DDL to `GraphSchema._DDL_STATEMENTS` in `iris_vector_graph/schema.py` — exact SQL from data-model.md
- [ ] T002 Add `Graph_KG.table_mappings` and `Graph_KG.relationship_mappings` to `GraphSchema._EXISTING_TABLES_CHECK` set in `iris_vector_graph/schema.py`
- [ ] T003 Add `_table_mapping_cache: Optional[Dict[str, dict]] = None` and `_rel_mapping_cache: Optional[Dict[tuple, dict]] = None` fields to `IRISGraphEngine.__init__` in `iris_vector_graph/engine.py`
- [ ] T004 Add `mapped_node_aliases: Dict[str, dict]` field to `TranslationContext.__init__` in `iris_vector_graph/cypher/translator.py` (initialise to `{}`)
- [ ] T005 Create empty test file `tests/unit/test_sql_table_bridge.py` with `SKIP_IRIS_TESTS` guard, `PREFIX` fixture, and two empty classes: `TestSQLTableBridgeUnit` and `TestSQLTableBridgeE2E`

**Checkpoint**: `pytest tests/unit/ -q --timeout=20` — 353 tests pass, new file imports cleanly.

---

## Phase 2: Foundational (blocking all user stories)

**Purpose**: Internal cache helpers + `get_table_mapping` / `get_rel_mapping` accessors used by all translator interceptions. Tests written first.

- [ ] T006 Write unit test `test_get_table_mapping_returns_none_for_unmapped` in `tests/unit/test_sql_table_bridge.py::TestSQLTableBridgeUnit`: mock engine with empty cache, assert `engine.get_table_mapping("Patient")` returns `None` — must FAIL before T008
- [ ] T007 Write unit test `test_get_table_mapping_returns_cached_entry` in `tests/unit/test_sql_table_bridge.py::TestSQLTableBridgeUnit`: seed `_table_mapping_cache` with a dict entry, assert `engine.get_table_mapping("Patient")` returns the dict — must FAIL before T008
- [ ] T008 Implement `IRISGraphEngine.get_table_mapping(label: str) -> Optional[dict]` in `iris_vector_graph/engine.py`: lazy-load cache from `Graph_KG.table_mappings` on first call; return `_table_mapping_cache.get(label)`
- [ ] T009 Implement `IRISGraphEngine.get_rel_mapping(source_label: str, predicate: str, target_label: str) -> Optional[dict]` in `iris_vector_graph/engine.py`: same lazy-load pattern from `Graph_KG.relationship_mappings`; key is `(source_label, predicate, target_label)`
- [ ] T010 Implement `IRISGraphEngine._invalidate_mapping_cache()` in `iris_vector_graph/engine.py`: set both `_table_mapping_cache = None` and `_rel_mapping_cache = None`
- [ ] T011 Run `pytest tests/unit/test_sql_table_bridge.py -v -k "Unit"` — T006, T007 must pass

---

## Phase 3: User Story 1 — Register table as node set (P1)

**Goal**: `map_sql_table` + Cypher routing to mapped table for node queries.

**Independent test criteria**: `MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name` against a real table returns identical results to direct SQL.

### Tests first (TDD)

- [ ] T012 [US1] Write unit test `test_cypher_mapped_label_uses_mapped_table_not_nodes` in `TestSQLTableBridgeUnit`: seed cache with `Patient → {sql_table: "T.Pat", id_column: "PID", ...}`; translate `MATCH (n:Patient) RETURN n.Name`; assert SQL does NOT contain `Graph_KG.nodes` and DOES contain `T.Pat` — must FAIL before T015
- [ ] T013 [US1] Write unit test `test_cypher_unmapped_label_unchanged` in `TestSQLTableBridgeUnit`: empty cache; translate `MATCH (n:Service) RETURN n.id`; assert SQL contains `Graph_KG.nodes` (regression guard) — must FAIL before T015
- [ ] T014 [US1] Write unit test `test_map_sql_table_upsert_updates_existing` in `TestSQLTableBridgeUnit`: mock conn; call `map_sql_table` twice for same label with different table names; assert second call updates, not duplicates — must FAIL before T016

### Implementation

- [ ] T015 [US1] Intercept `translate_node_pattern` in `iris_vector_graph/cypher/translator.py`: before `nodes_tbl = _table('nodes')` block, call `getattr(context, '_engine', None).get_table_mapping(label)` for each label; if mapping found: append `FROM {sql_table} {alias}` to `from_clauses`, set `context.mapped_node_aliases[alias] = mapping`, skip `Graph_KG.nodes + rdf_labels JOIN`, return early
- [ ] T016 [US1] Implement `IRISGraphEngine.map_sql_table(table, id_column, label, property_columns=None)` in `iris_vector_graph/engine.py`: validate table + id_column exist via `INFORMATION_SCHEMA`; upsert into `Graph_KG.table_mappings`; call `_invalidate_mapping_cache()`; return mapping dict
- [ ] T017 [US1] Update `translate_expression` in `iris_vector_graph/cypher/translator.py` for `PropertyReference`: when `alias in context.mapped_node_aliases`, return `{alias}.{sanitize_identifier(property_name)}`; when `property_name in ('id', 'node_id')` → return `{alias}.{mapping['id_column']}`
- [ ] T018 [US1] Pass `engine=self` to `translate_to_sql` inside `IRISGraphEngine.execute_cypher` in `iris_vector_graph/engine.py` — sets `context._engine` so translator can call `get_table_mapping`
- [ ] T019 [US1] Run unit tests: `pytest tests/unit/test_sql_table_bridge.py -v -k "mapped_label or unmapped_label or upsert"` — T012, T013, T014 must pass

### E2E tests

- [ ] T020 [P] [US1] Write E2E test `test_cypher_returns_same_as_direct_sql` in `TestSQLTableBridgeE2E`: create `Bridge_Test.Patient(PatientID, Name, MRN)`; insert 2 rows; `map_sql_table`; `execute_cypher MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name`; assert equals direct SQL result (SC-001)
- [ ] T021 [P] [US1] Write E2E test `test_zero_writes_to_graph_kg_nodes` in `TestSQLTableBridgeE2E`: after mapped query, `SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE 'Patient:%'` = 0 (SC-004, FR-007)
- [ ] T022 [P] [US1] Write E2E test `test_cypher_count_matches_sql_count` in `TestSQLTableBridgeE2E`: `MATCH (n:Patient) RETURN count(n)` equals `SELECT COUNT(*) FROM Bridge_Test.Patient` (US1 AC3)
- [ ] T023 [US1] Compile no new ObjectScript needed; run E2E: `pytest tests/unit/test_sql_table_bridge.py -v -k "E2E and (direct_sql or zero_writes or count)"` — T020, T021, T022 must pass

---

## Phase 4: User Story 2 — Relationships between mapped tables (P1)

**Goal**: `map_sql_relationship` + FK/via-table traversal in Cypher.

**Independent test criteria**: `MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) WHERE p.MRN = $mrn RETURN e.AdmitDate` matches direct SQL JOIN.

### Tests first

- [ ] T024 [US2] Write unit test `test_fk_relationship_generates_correct_join` in `TestSQLTableBridgeUnit`: seed cache with Patient + Encounter mappings + HAS_ENCOUNTER rel (target_fk="PatientID"); translate `MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) RETURN e.AdmitDate`; assert SQL contains `JOIN ... ON e.PatientID = p.PatientID` (no `rdf_edges`) — must FAIL before T026
- [ ] T025 [US2] Write unit test `test_via_table_relationship_generates_correct_join` in `TestSQLTableBridgeUnit`: seed M:M mapping (via_table="T.PatMed", via_source="PatientID", via_target="MedicationID"); translate `MATCH (p:Patient)-[:PRESCRIBED]->(m:Medication) RETURN m.Name`; assert SQL contains two JOINs through via table — must FAIL before T026

### Implementation

- [ ] T026 [US2] Intercept `translate_relationship_pattern` in `iris_vector_graph/cypher/translator.py`: after computing `edge_alias`, check `getattr(context, '_engine', None).get_rel_mapping(src_label, predicate, tgt_label)`; if FK mapping: replace `rdf_edges JOIN` with `JOIN {target_table} {target_alias} ON {target_alias}.{target_fk} = {source_alias}.{source_id_col}`; if via-table: emit two JOINs; register target in `context.mapped_node_aliases`
- [ ] T027 [US2] Implement `IRISGraphEngine.map_sql_relationship(source_label, predicate, target_label, target_fk=None, via_table=None, via_source=None, via_target=None)` in `iris_vector_graph/engine.py`: validate at least one of `target_fk` / `via_table` provided; validate both labels are registered in `table_mappings`; upsert into `Graph_KG.relationship_mappings`; call `_invalidate_mapping_cache()`
- [ ] T028 [US2] Run unit tests: `pytest tests/unit/test_sql_table_bridge.py -v -k "fk_relationship or via_table"` — T024, T025 must pass

### E2E tests

- [ ] T029 [P] [US2] Write E2E test `test_fk_traversal_matches_sql_join` in `TestSQLTableBridgeE2E`: create Patient + Encounter tables with FK; map both + relationship; Cypher traversal result equals `SELECT p.Name, e.AdmitDate FROM Patient JOIN Encounter ON Encounter.PatientID = Patient.PatientID WHERE ...` (SC-005, US2 AC1)
- [ ] T030 [P] [US2] Write E2E test `test_via_table_traversal` in `TestSQLTableBridgeE2E`: create M:M via join table; verify `MATCH (p:Patient)-[:PRESCRIBED]->(m:Medication) RETURN m.Name` returns correct results (US2 AC2)
- [ ] T031 [US2] Run E2E: `pytest tests/unit/test_sql_table_bridge.py -v -k "E2E and (fk_traversal or via_table)"` — T029, T030 must pass

---

## Phase 5: User Story 3 — Mixed mapped + native nodes (P2)

**Goal**: A single Cypher MATCH spans both a mapped SQL table and a native `Graph_KG.nodes` node.

**Independent test criteria**: Register `Patient` as mapped; create native `Document` node; create edge `(patient)-[:HAS_DOCUMENT]->(doc)`; `MATCH (p:Patient)-[:HAS_DOCUMENT]->(d:Document) RETURN p.Name, d.title` returns correct results.

### Tests first

- [ ] T032 [US3] Write unit test `test_mixed_match_routes_mapped_and_native_independently` in `TestSQLTableBridgeUnit`: seed Patient mapping, no Document mapping; translate `MATCH (p:Patient)-[:HAS_DOCUMENT]->(d:Document) RETURN p.Name, d.title`; assert SQL contains both `Bridge_Test.Patient` (for p) and `Graph_KG.nodes` (for d) — must FAIL before T033

### Implementation

- [ ] T033 [US3] Verify `translate_node_pattern` intercept in `iris_vector_graph/cypher/translator.py` correctly handles the second node in a MATCH pattern: if label is unmapped, falls through to existing `Graph_KG.nodes` path; if mapped, uses mapped table. The intercept already does this per T015 — confirm by running T032 after T015.

### E2E tests

- [ ] T034 [P] [US3] Write E2E test `test_mixed_mapped_and_native_query` in `TestSQLTableBridgeE2E`: map `Bridge_Test.Patient`; create native `Document` node in `Graph_KG`; create `rdf_edges` entry `(Patient:P001)-[:HAS_DOCUMENT]->(doc:001)`; query spanning both; assert correct join result (US3 AC1)
- [ ] T035 [US3] Run: `pytest tests/unit/test_sql_table_bridge.py -v -k "mixed"` — T032 + T034 must pass

---

## Phase 6: User Story 4 — Attach vector embeddings (P2)

**Goal**: `attach_embeddings_to_table` generates and stores HNSW-searchable embeddings for mapped table rows.

**Independent test criteria**: After calling `attach_embeddings_to_table("Patient", text_columns=["Name"])`, `CALL ivg.vector.search('Patient', 'embedding', $vec, 5)` returns results referencing `Bridge_Test.Patient` rows.

### Tests first

- [ ] T036 [US4] Write unit test `test_attach_embeddings_skips_existing_by_id` in `TestSQLTableBridgeUnit`: mock `kg_NodeEmbeddings` with one existing `Patient:P001` entry; call `attach_embeddings_to_table(label="Patient", text_columns=["Name"], force=False)`; assert `Patient:P001` is NOT re-embedded but `Patient:P002` is — must FAIL before T038
- [ ] T037 [US4] Write unit test `test_attach_embeddings_force_reembeds_all` in `TestSQLTableBridgeUnit`: same setup; call with `force=True`; assert both P001 and P002 are embedded — must FAIL before T038
- [ ] T038 [US4] Write unit test `test_attach_embeddings_raises_for_unmapped_label` in `TestSQLTableBridgeUnit`: call `attach_embeddings_to_table("Provider", ...)` with empty cache; assert raises `TableNotMappedError` — must FAIL before T039

### Implementation

- [ ] T039 [US4] Add `class TableNotMappedError(ValueError)` to `iris_vector_graph/engine.py`
- [ ] T040 [US4] Implement `IRISGraphEngine.attach_embeddings_to_table(label, text_columns, batch_size=1000, force=False, progress_callback=None)` in `iris_vector_graph/engine.py`: raise `TableNotMappedError` if label not in mappings; query all rows from mapped SQL table; for each batch: check `kg_NodeEmbeddings` for existing IDs (skip if `force=False`); generate embeddings via `embed_text(concat(text_columns))`; bulk-insert into `kg_NodeEmbeddings` with ID `{label}:{id_value}`; call `progress_callback(n_done, n_total)` every batch if provided; return `{"embedded": int, "skipped": int, "total": int}`
- [ ] T041 [US4] Run unit tests: `pytest tests/unit/test_sql_table_bridge.py -v -k "attach_embeddings"` — T036, T037, T038 must pass

### E2E tests

- [ ] T042 [P] [US4] Write E2E test `test_attach_embeddings_stores_to_kg_node_embeddings` in `TestSQLTableBridgeE2E`: map Patient table; call `attach_embeddings_to_table`; assert `SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id LIKE 'Patient:%'` = num rows (US4 AC1, SC-008)
- [ ] T043 [P] [US4] Write E2E test `test_attach_embeddings_idempotent` in `TestSQLTableBridgeE2E`: call twice; assert second run returns `skipped = n_rows, embedded = 0` (US4 AC3, FR-011)
- [ ] T044 [P] [US4] Write E2E test `test_attach_embeddings_force_reembeds` in `TestSQLTableBridgeE2E`: call with `force=True`; assert `embedded = n_rows` regardless of existing entries (FR-011 `force=True`)
- [ ] T045 [US4] Run E2E: `pytest tests/unit/test_sql_table_bridge.py -v -k "E2E and (embed)"` — T042, T043, T044 must pass

---

## Phase 7: User Story 5 — List and remove mappings (P3)

**Goal**: `list_table_mappings` + `remove_table_mapping` + `reload_table_mappings`.

### Tests first

- [ ] T046 [US5] Write unit test `test_list_table_mappings_returns_both_node_and_rel_mappings` in `TestSQLTableBridgeUnit`: seed cache with 2 node mappings + 1 rel mapping; call `list_table_mappings()`; assert returns `{"nodes": [2 items], "relationships": [1 item]}` — must FAIL before T048
- [ ] T047 [US5] Write unit test `test_remove_table_mapping_invalidates_cache` in `TestSQLTableBridgeUnit`: seed cache; call `remove_table_mapping("Patient")`; assert cache is None (invalidated) and DB DELETE was called — must FAIL before T048

### Implementation

- [ ] T048 [US5] Implement `IRISGraphEngine.list_table_mappings()` in `iris_vector_graph/engine.py`: query both `Graph_KG.table_mappings` and `Graph_KG.relationship_mappings`; return `{"nodes": [...], "relationships": [...]}`
- [ ] T049 [US5] Implement `IRISGraphEngine.remove_table_mapping(label: str)` in `iris_vector_graph/engine.py`: DELETE from `Graph_KG.table_mappings` WHERE label = ?; DELETE from `Graph_KG.relationship_mappings` WHERE source_label = ? OR target_label = ?; call `_invalidate_mapping_cache()`; raise `ValueError` if label not found
- [ ] T050 [US5] Implement `IRISGraphEngine.reload_table_mappings()` in `iris_vector_graph/engine.py`: call `_invalidate_mapping_cache()`; call `get_table_mapping("")` to trigger reload (or explicit reload from DB)
- [ ] T051 [US5] Run unit tests: `pytest tests/unit/test_sql_table_bridge.py -v -k "list_table or remove_table"` — T046, T047 must pass

### E2E tests

- [ ] T052 [P] [US5] Write E2E test `test_list_and_remove_mapping` in `TestSQLTableBridgeE2E`: register 2 mappings; `list_table_mappings()` returns both; `remove_table_mapping("Patient")`; `list_table_mappings()` returns 1; `MATCH (n:Patient)` falls back to `Graph_KG.nodes` (US5 AC1, AC2)
- [ ] T053 [US5] Run: `pytest tests/unit/test_sql_table_bridge.py -v -k "E2E and list_and_remove"` — T052 must pass

---

## Phase 8: Polish & Cross-Cutting

- [ ] T054 [P] Run full unit + E2E regression: `pytest tests/unit/ -q --timeout=30` — all 353 + new tests must pass (SC-007, NFR-004)
- [ ] T055 [P] Run SC-001 benchmark from `specs/040-sql-table-bridge/quickstart.md`: Cypher result == direct SQL result on test table
- [ ] T056 [P] Run SC-003 benchmark from `specs/040-sql-table-bridge/quickstart.md`: Cypher latency ≤ 2× direct SQL on mapped table; document measured values in spec.md §Clarifications
- [ ] T057 Bump version in `pyproject.toml` to `1.44.0`
- [ ] T058 Update `README.md` — add SQL Table Bridge section with `map_sql_table` + `map_sql_relationship` examples
- [ ] T059 Commit: `feat: v1.44.0 — SQL Table Bridge (map_sql_table, map_sql_relationship, attach_embeddings_to_table)`
- [ ] T060 Tag `v1.44.0`, build with `python3 -m build`, publish with `twine upload dist/iris_vector_graph-1.44.0*`
