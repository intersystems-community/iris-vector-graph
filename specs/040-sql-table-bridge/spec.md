# Feature Specification: SQL Table Bridge

**Feature Branch**: `040-sql-table-bridge`
**Created**: 2026-04-03
**Status**: Draft
**Input**: Layer vector-enabled graph onto existing IRIS SQL tables without ETL or data copying. Sister project iris-vector-rag is a first-class consumer.

---

## Overview

IVG currently requires all data to be ingested into `Graph_KG.nodes` and `Graph_KG.rdf_edges` before graph queries or vector search can run. This forces users with existing IRIS applications to either copy their data (doubling storage, creating sync problems) or forgo the graph layer entirely.

The SQL Table Bridge lets a user point IVG at their existing IRIS tables and immediately run Cypher queries — no data copy, no ETL pipeline, no schema migration. The existing tables become the graph. Vector search is layered on top as an opt-in overlay.

This is the foundational "I already have data" primitive. Everything else in the overlay roadmap (RAG corpus bridge, embedding overlay, cross-table vector search) depends on this working cleanly first.

---

## Clarifications

### Session 2026-04-03

- Q: In `map_sql_relationship`, which table holds the FK column? → A: The **target** table holds the FK referencing the source's PK (standard 1:many). Parameter renamed `target_fk`. FR-004, relationship_mappings schema, and US2 AC1 updated accordingly.
- Q: When a label has both a SQL mapping and native rows in `Graph_KG.nodes`, which takes precedence? → A: SQL mapping always wins; native nodes are ignored. Edge cases section updated with migration guidance.
- Q: Does the bridge execute SQL under the mapping creator's credentials or the current query user's credentials? → A: Current connection user — IRIS SQL permissions apply natively. IVG neither grants nor restricts access beyond what IRIS enforces. NFR-005 added.
- Q: Are table mappings re-read from the database on every Cypher query, or cached? → A: Cached in engine instance; invalidated only when mappings are modified via that engine. External changes not reflected until engine restart. NFR-006 added.
- Q: How does `attach_embeddings_to_table` detect already-embedded rows to skip them? → A: ID presence in `kg_NodeEmbeddings` (`Patient:{id}` already exists → skip). `force=True` param overrides. FR-011 and US4 AC3-4 updated.

---

## User Scenarios & Testing

### User Story 1 — Register an existing table as a node set (Priority: P1)

A developer has an existing IRIS table (e.g., `HealthShare.Patient`) and wants to run Cypher queries over it as if it were a graph node collection. They call one method to register the mapping, then immediately query without waiting for any ETL.

**Why this priority**: This is the entire value proposition — "zero copy, immediate graph queries." Without it, none of the other overlay features make sense.

**Independent Test**: Register one table mapping; execute `MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name` against the existing table; verify correct rows returned with no new data written.

**Acceptance Scenarios**:

1. **Given** `HealthShare.Patient` exists with columns `PatientID`, `Name`, `MRN`, `DOB`, **When** `engine.map_sql_table("HealthShare.Patient", id_column="PatientID", label="Patient")` is called, **Then** the mapping is registered and subsequent `MATCH (n:Patient)` Cypher queries route to `HealthShare.Patient`.
2. **Given** the mapping is registered, **When** `MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name, n.DOB` is executed, **Then** results match a direct SQL query `SELECT Name, DOB FROM HealthShare.Patient WHERE MRN = ?`.
3. **Given** the mapping is registered, **When** `MATCH (n:Patient) RETURN count(n)` is executed, **Then** the count matches `SELECT COUNT(*) FROM HealthShare.Patient`.
4. **Given** the mapping does not exist for label `Provider`, **When** `MATCH (n:Provider)` is executed, **Then** the query falls back to `Graph_KG.nodes` as before (no regression on unregistered labels).
5. **Given** the mapping is registered, **When** no rows match a WHERE filter, **Then** zero rows are returned, no error.
6. **Given** the source table is modified (rows added/deleted), **When** a Cypher query is executed, **Then** results reflect the current table state — no cache invalidation needed.

---

### User Story 2 — Register a relationship between two mapped tables (Priority: P1)

A developer has two existing tables with a foreign-key relationship (or a join table) and wants to traverse it as a graph edge in Cypher.

**Why this priority**: Node mapping without edge traversal produces an isolated graph — no relationships means no graph value. P1 alongside US1.

**Independent Test**: Register two table mappings plus one relationship; execute `MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) WHERE p.MRN = $mrn RETURN e.AdmitDate` and verify results match a direct SQL join.

**Acceptance Scenarios**:

1. **Given** `HealthShare.Patient` (PatientID PK) and `HealthShare.Encounter` (EncounterID PK, PatientID FK referencing Patient), **When** `engine.map_sql_relationship("Patient", "HAS_ENCOUNTER", "Encounter", target_fk="PatientID")` is called, **Then** subsequent `MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter)` queries traverse the foreign key via `Encounter.PatientID = Patient.PatientID`.
2. **Given** a join table `HealthShare.PatientMedication(PatientID, MedicationID)`, **When** `engine.map_sql_relationship("Patient", "PRESCRIBED", "Medication", via_table="HealthShare.PatientMedication", via_source="PatientID", via_target="MedicationID")` is called, **Then** `MATCH (p:Patient)-[:PRESCRIBED]->(m:Medication)` correctly joins through the intermediary table.
3. **Given** a registered relationship, **When** the relationship is traversed with a WHERE filter on both ends, **Then** the SQL query pushes both filters down to the underlying tables.
4. **Given** a registered relationship, **When** `MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter)-[:PRODUCED]->(r:Result)` (three hops, all mapped), **Then** the multi-hop query executes correctly as nested JOINs.

---

### User Story 3 — Mix mapped tables and native KG nodes in one query (Priority: P2)

A user has some data in existing IRIS tables and some data ingested natively into IVG. A single Cypher query should span both transparently.

**Why this priority**: Real deployments will never be pure-mapped or pure-native. The bridge must compose. P2 because P1 (pure mapped) must work first.

**Independent Test**: Register `HealthShare.Patient` as mapped; create a native IVG `Document` node; create an edge `(patient)-[:HAS_DOCUMENT]->(doc)`; execute a query spanning both and verify results.

**Acceptance Scenarios**:

1. **Given** `Patient` is mapped to `HealthShare.Patient` and `Document` is a native IVG node, **When** `MATCH (p:Patient)-[:HAS_DOCUMENT]->(d:Document) WHERE p.MRN = $mrn RETURN p.Name, d.title` is executed, **Then** results join mapped and native nodes correctly.
2. **Given** a mixed query with vector search `CALL ivg.vector.search('Document', 'embedding', $vec, 5) YIELD node MATCH (node)<-[:HAS_DOCUMENT]-(p:Patient) RETURN p.Name`, **Then** vector results expand into mapped patient data correctly.

---

### User Story 4 — Attach vector embeddings to a mapped table (Priority: P2)

A user wants to add vector search over an existing SQL table's rows. They call one method to generate and store embeddings for a text column; HNSW search then works over those rows.

**Why this priority**: This is the "overlay" value — zero copy graph queries are the baseline, vector search on top is the next layer. P2 because P1 must be stable first.

**Independent Test**: Map a table; call `attach_embeddings`; run `kg_KNN_VEC` search; verify top-k results correspond to semantically similar rows in the original table.

**Acceptance Scenarios**:

1. **Given** `HealthShare.Patient` is mapped (via prior `map_sql_table` call with `PatientID` as id_column), **When** `engine.attach_embeddings_to_table("Patient", text_columns=["Name", "Diagnosis"], batch_size=500)` is called, **Then** embeddings are stored in `kg_NodeEmbeddings` with IDs of the form `Patient:{PatientID_value}`.
2. **Given** embeddings are stored, **When** `CALL ivg.vector.search('Patient', 'embedding', $vec, 10) YIELD node, score RETURN node.Name, score` is executed, **Then** results reference existing rows in `HealthShare.Patient`.
3. **Given** the source table is updated (new rows added), **When** `attach_embeddings_to_table` is called again, **Then** only rows whose ID is absent from `kg_NodeEmbeddings` are embedded; existing rows are skipped.
4. **Given** `force=True` is passed, **When** `attach_embeddings_to_table` is called, **Then** all rows are re-embedded regardless of existing entries.
5. **Given** `batch_size=500`, **When** the table has 50,000 rows, **Then** the method processes all rows in batches of 500 with progress logging.

---

### User Story 5 — List and remove table mappings (Priority: P3)

A developer needs to inspect, update, or remove table mappings — for debugging, migration, or schema changes.

**Why this priority**: Operational hygiene. Not needed for initial value but essential for production use.

**Acceptance Scenarios**:

1. **Given** two mappings are registered, **When** `engine.list_table_mappings()` is called, **Then** both mappings are returned with table name, label, id_column, and registered_at.
2. **Given** a mapping is registered, **When** `engine.remove_table_mapping("Patient")` is called, **Then** subsequent `MATCH (n:Patient)` falls back to `Graph_KG.nodes`.
3. **Given** no mappings exist, **When** `engine.list_table_mappings()` is called, **Then** an empty list is returned.

---

## Requirements

### Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-001 | `map_sql_table(table, id_column, label, property_columns=None)` MUST register a mapping from a graph label to an existing SQL table. `table` is fully-qualified (e.g., `HealthShare.Patient`); schema is encoded in the table name, not a separate parameter. |
| FR-002 | Registered node mappings MUST be persisted (survive engine restart) in a dedicated metadata table |
| FR-003 | Cypher `MATCH (n:Label)` MUST route to the registered SQL table when a mapping exists for `Label` |
| FR-004 | `map_sql_relationship(source_label, predicate, target_label, target_fk=None, via_table=None, via_source=None, via_target=None)` MUST register a traversable edge mapping. `target_fk` is the FK column on the **target** table that references the source table's PK (the standard 1:many pattern). Use `via_table` for many-to-many. |
| FR-005 | Relationship mappings MUST be persisted alongside node mappings |
| FR-006 | Cypher `MATCH (a:L1)-[:PRED]->(b:L2)` MUST route to the registered join/FK when a relationship mapping exists |
| FR-007 | Queries over mapped tables MUST NOT write any data to `Graph_KG.nodes` or `Graph_KG.rdf_edges` |
| FR-008 | Queries spanning mapped and native IVG nodes in a single MATCH MUST execute correctly |
| FR-009 | WHERE filters on mapped node properties MUST be pushed down to the underlying SQL table |
| FR-010 | `attach_embeddings_to_table(label, text_columns, batch_size=1000, force=False)` MUST generate and store embeddings for all rows in the table registered under `label`. The `id_column` is taken from the existing `map_sql_table` registration. Embeddings are stored in `kg_NodeEmbeddings` addressable by `Label:{id_value}`. Raises `TableNotMappedError` if `label` is not registered. |
| FR-011 | `attach_embeddings_to_table` MUST be idempotent — rows whose ID already exists in `kg_NodeEmbeddings` are skipped; a `force=True` parameter MUST be supported to re-embed all rows regardless |
| FR-012 | `list_table_mappings()` MUST return all registered node and relationship mappings |
| FR-013 | `remove_table_mapping(label)` MUST remove a node mapping and its associated relationship mappings |
| FR-014 | When no mapping exists for a label, Cypher MUST fall back to `Graph_KG.nodes` unchanged (no regression) |
| FR-015 | `map_sql_table` MUST validate that the specified table and id_column exist before registering |
| FR-016 | When a SQL mapping exists for a label, Cypher MUST query the mapped SQL table exclusively — native rows in `Graph_KG.nodes` for that label are not included in results |

### Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | Cypher query over a mapped table MUST return results within 2× of an equivalent direct SQL query on the same data |
| NFR-002 | `map_sql_table` MUST complete in under 5 seconds regardless of table size (metadata-only operation) |
| NFR-003 | `attach_embeddings_to_table` MUST process at minimum 1,000 rows per minute |
| NFR-004 | Existing Cypher tests (unmapped labels) MUST continue to pass unchanged |
| NFR-005 | SQL queries generated by the bridge MUST execute under the security context of the current IRIS connection — IVG does not grant or expand SQL permissions beyond what IRIS enforces natively |
| NFR-006 | Table mappings MUST be cached in the engine instance after first load; cache is invalidated and refreshed only when `map_sql_table` or `remove_table_mapping` is called on that engine instance. External changes to `Graph_KG.table_mappings` (from another process) are not reflected until the engine is restarted or mappings are explicitly reloaded. |

---

## Key Entities

### `Graph_KG.table_mappings` (new metadata table)

| Column | Type | Description |
|--------|------|-------------|
| `label` | VARCHAR PRIMARY KEY | Graph label (e.g., `Patient`) |
| `sql_table` | VARCHAR | Fully-qualified SQL table (e.g., `HealthShare.Patient`) |
| `id_column` | VARCHAR | Column whose value becomes the node ID |
| `property_columns` | VARCHAR (JSON) | Columns exposed as node properties; NULL = all columns |
| `registered_at` | TIMESTAMP | When the mapping was created |

### `Graph_KG.relationship_mappings` (new metadata table)

| Column | Type | Description |
|--------|------|-------------|
| `predicate` | VARCHAR | Edge predicate (e.g., `HAS_ENCOUNTER`) |
| `source_label` | VARCHAR | Source node label |
| `target_label` | VARCHAR | Target node label |
| `target_fk` | VARCHAR | FK column on the **target** table referencing the source table's PK (1:many). NULL when via_table is used. |
| `via_table` | VARCHAR | Join table for many-to-many; NULL if FK-based |
| `via_source` | VARCHAR | Column in via_table referencing source |
| `via_target` | VARCHAR | Column in via_table referencing target |

---

## Success Criteria

| ID | Criterion | Measurement |
|----|-----------|-------------|
| SC-001 | Cypher over a mapped table returns identical results to a direct SQL query | US1 AC2 — Cypher vs SQL result comparison |
| SC-002 | `map_sql_table` completes in under 5 seconds | Time the call on a 10M-row table |
| SC-003 | Cypher performance within 2× of direct SQL | NFR-001 benchmark |
| SC-004 | Zero rows written to Graph_KG.nodes during mapped queries | FR-007 — verify with row count before/after |
| SC-005 | Multi-hop query spanning 3 mapped tables executes correctly | US2 AC4 |
| SC-006 | Mixed mapped + native query works | US3 AC1 |
| SC-007 | Existing 353 unit tests pass unchanged | `pytest tests/unit/ -q` |
| SC-008 | `attach_embeddings_to_table` idempotent: second run skips existing rows | US4 AC3 |

---

## Edge Cases

- `map_sql_table` called with a label already registered → update the mapping, not error
- `property_columns=None` → expose all columns as node properties
- Source table has NULL values in property columns → return as NULL in Cypher results
- `id_column` value contains special characters → ID is formed as `Label:{id_value}` with the raw value; escaping is the caller's responsibility
- Two tables mapped to the same label → raise a clear error (ambiguous)
- Label has both a SQL mapping AND rows in `Graph_KG.nodes` → SQL mapping wins; native nodes for that label are ignored during Cypher queries. Users migrating incrementally should call `engine.delete_nodes_by_label("Patient")` after registering the mapping to avoid stale native rows.
- Mapped table is dropped from the database → Cypher query fails with a clear error naming the missing table, not a cryptic SQL error
- `MATCH (n:Patient)-[:HAS_ENCOUNTER]->(e:Encounter)` where `Patient` is mapped but `Encounter` is not → fail with a clear error: "Encounter is not mapped and has no native IVG nodes"
- `attach_embeddings_to_table` called on an unmapped table → raise `TableNotMappedError` with helpful message
- `map_sql_table` on an IRIS table in a different namespace → support via fully-qualified name `Namespace.Schema.Table` syntax
- Two engine instances share the same IRIS instance; one adds a mapping while the other is running → the second engine's cache is stale until it calls `map_sql_table` / `remove_table_mapping` or is restarted. This is documented behavior, not a bug.

---

## Out of Scope

- Writeback: mapped tables are read-only via the bridge (INSERT/UPDATE via Cypher on mapped tables is not supported)
- Automatic schema sync: IVG does not track DDL changes to mapped tables
- Cross-instance table mapping (tables on a different IRIS instance)
- Mapping views (`SELECT`-based virtual tables) — only base tables in Phase 1
- Full-text search (`iFind`) over mapped table columns — separate spec
- iris-vector-rag `SourceDocuments` → IVG bridge — this is US3 of spec 045 (RAG corpus bridge)

---

## Assumptions

- The user's existing tables are in the same IRIS instance and namespace as the IVG schema, or accessible via a fully-qualified name
- The id_column is unique per row (behaves as a primary key); IVG does not enforce uniqueness but results are undefined if duplicates exist
- Property columns are scalar types (VARCHAR, INTEGER, DATE, etc.); BLOB/CLOB columns are excluded from properties by default
- `iris-vector-rag` HybridGraphRAG's `SchemaManager` will be updated to call `map_sql_table` for `RAG.SourceDocuments` in a follow-on spec (045); this spec does not modify iris-vector-rag
- This spec is IVG-only; the joint IVG+IVR story is spec 045
