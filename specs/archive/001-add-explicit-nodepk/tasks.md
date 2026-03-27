# Tasks: Explicit Node Identity Table (NodePK)

**Input**: Design documents from `/Users/tdyar/ws/iris-vector-graph/specs/001-add-explicit-nodepk/`
**Prerequisites**: plan.md ✅, data-model.md ✅, contracts/sql_contracts.md ✅, quickstart.md ✅

## Execution Summary

This task list implements the NodePK feature following **Test-Driven Development (TDD)** with live IRIS database validation. The feature adds an explicit `nodes` table with foreign key constraints to enforce referential integrity across all graph entities (edges, labels, properties, embeddings).

**Key Technical Requirements**:
- Python 3.8+, IRIS SQL, pytest with live IRIS database
- Performance: <1ms node lookup, ≥1000 nodes/sec bulk insert, <10% FK overhead
- Migration: Discover existing nodes from rdf_* tables without data loss

**Constitutional Compliance**: Test-first with live database (Principle II), IRIS-native SQL (Principle I), explicit error handling (Principle VII)

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no shared dependencies)
- All file paths are absolute from repository root

---

## Phase 3.1: Setup & Schema Design

- [ ] **T001** Create SQL migration file structure
  - **File**: `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Create migration file with nodes table DDL:
    ```sql
    CREATE TABLE IF NOT EXISTS nodes(
      node_id VARCHAR(256) PRIMARY KEY NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ```
  - **Constitutional**: IRIS-native SQL DDL (Principle I)

- [ ] **T002** [P] Create rollback migration script
  - **File**: `sql/migrations/001_rollback_nodepk.sql`
  - **Action**: Create rollback script to drop nodes table and remove FK constraints
  - **Content**:
    ```sql
    -- Drop FK constraints from dependent tables
    ALTER TABLE rdf_edges DROP CONSTRAINT IF EXISTS fk_edges_source;
    ALTER TABLE rdf_edges DROP CONSTRAINT IF EXISTS fk_edges_dest;
    ALTER TABLE rdf_labels DROP CONSTRAINT IF EXISTS fk_labels_node;
    ALTER TABLE rdf_props DROP CONSTRAINT IF EXISTS fk_props_node;
    ALTER TABLE kg_NodeEmbeddings DROP CONSTRAINT IF EXISTS fk_embeddings_node;
    -- Drop nodes table
    DROP TABLE IF EXISTS nodes;
    ```

- [ ] **T003** [P] Create Python migration utility module structure
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Action**: Create module skeleton with CLI argument parsing (--validate-only, --execute, --verbose)
  - **Dependencies**: argparse, logging, iris driver
  - **Constitutional**: Explicit error handling for all operations (Principle VII)

---

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3

**CRITICAL: All tests below MUST be written and MUST FAIL before implementation begins**

### Contract Tests (7 contracts from sql_contracts.md)

- [ ] **T004** [P] Contract test: Create Node (Contract 1)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestNodeCreation`
  - **Test Methods**:
    - `test_create_node_success()` - Insert valid node, verify created_at set
    - `test_create_node_duplicate_fails()` - Insert duplicate node_id, expect UNIQUE violation
    - `test_create_node_null_id_fails()` - Insert NULL node_id, expect NOT NULL violation
  - **Markers**: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - **Expected**: All tests FAIL (nodes table doesn't exist yet)

- [ ] **T005** [P] Contract test: Create Edge with Node Validation (Contract 2)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestEdgeForeignKeys`
  - **Test Methods**:
    - `test_edge_insert_requires_source_node()` - Insert edge with non-existent source, expect FK violation
    - `test_edge_insert_requires_dest_node()` - Insert edge with non-existent dest, expect FK violation
    - `test_edge_insert_success_both_nodes_exist()` - Insert edge with valid nodes, verify success
  - **Expected**: Tests FAIL (FK constraints don't exist yet)

- [ ] **T006** [P] Contract test: Assign Label to Node (Contract 3)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestLabelForeignKeys`
  - **Test Methods**:
    - `test_label_requires_node()` - Assign label to non-existent node, expect FK violation
    - `test_label_success_node_exists()` - Assign label to valid node, verify success
  - **Expected**: Tests FAIL (FK constraint doesn't exist yet)

- [ ] **T007** [P] Contract test: Assign Property to Node (Contract 4)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestPropertyForeignKeys`
  - **Test Methods**:
    - `test_property_requires_node()` - Assign property to non-existent node, expect FK violation
    - `test_property_success_node_exists()` - Assign property to valid node, verify success
  - **Expected**: Tests FAIL (FK constraint doesn't exist yet)

- [ ] **T008** [P] Contract test: Create Embedding for Node (Contract 5)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestEmbeddingForeignKeys`
  - **Test Methods**:
    - `test_embedding_requires_node()` - Create embedding for non-existent node, expect FK violation
    - `test_embedding_success_node_exists()` - Create embedding for valid node, verify success
  - **Expected**: Tests FAIL (FK constraint doesn't exist yet)

- [ ] **T009** [P] Contract test: Delete Node Cascade Behavior (Contract 6)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestNodeDeletion`
  - **Test Methods**:
    - `test_delete_node_blocked_by_edge()` - Delete node with edges, expect FK violation
    - `test_delete_node_blocked_by_label()` - Delete node with labels, expect FK violation
    - `test_delete_node_blocked_by_property()` - Delete node with properties, expect FK violation
    - `test_delete_node_blocked_by_embedding()` - Delete node with embedding, expect FK violation
    - `test_delete_node_success_no_dependencies()` - Delete node with no dependencies, verify success
  - **Expected**: Tests FAIL (FK constraints with ON DELETE RESTRICT don't exist yet)

- [ ] **T010** [P] Contract test: Bulk Node Insertion (Contract 7)
  - **File**: `tests/integration/test_nodepk_migration.py`
  - **Test Class**: `TestBulkNodeInsertion`
  - **Test Methods**:
    - `test_bulk_insert_discovers_all_nodes()` - Load sample data, run discovery query, verify all node IDs found
    - `test_bulk_insert_handles_duplicates()` - Insert same node_id from multiple tables, verify single entry
    - `test_bulk_insert_performance()` - Insert 10K nodes, verify ≥1000 nodes/second
  - **Expected**: Tests FAIL (nodes table doesn't exist yet)

### Migration Tests

- [ ] **T011** [P] Migration test: Node discovery from existing data
  - **File**: `tests/integration/test_nodepk_migration.py`
  - **Test Class**: `TestNodeDiscovery`
  - **Test Methods**:
    - `test_discover_nodes_from_labels()` - Create rdf_labels entries, verify node discovery
    - `test_discover_nodes_from_props()` - Create rdf_props entries, verify node discovery
    - `test_discover_nodes_from_edges_source()` - Create rdf_edges, verify source node discovery
    - `test_discover_nodes_from_edges_dest()` - Create rdf_edges, verify dest node discovery
    - `test_discover_nodes_from_embeddings()` - Create kg_NodeEmbeddings, verify node discovery
  - **Expected**: Tests FAIL (migration utility doesn't exist yet)

- [ ] **T012** [P] Migration test: Orphan detection
  - **File**: `tests/integration/test_nodepk_migration.py`
  - **Test Class**: `TestOrphanDetection`
  - **Test Methods**:
    - `test_detect_orphaned_edges()` - Create edge with non-existent node in labels/props, run orphan detection
    - `test_no_orphans_detected_valid_data()` - Create consistent data, verify zero orphans
  - **Expected**: Tests FAIL (migration utility doesn't exist yet)

- [ ] **T013** [P] Migration test: Concurrent node insertion (UNIQUE constraint)
  - **File**: `tests/integration/test_nodepk_constraints.py`
  - **Test Class**: `TestConcurrentNodeInsertion`
  - **Test Methods**:
    - `test_concurrent_insert_same_node_id()` - Use threading to insert same node_id twice, verify one succeeds with UNIQUE violation on other
  - **Expected**: Tests FAIL (nodes table doesn't exist yet)

### Integration Tests (from quickstart.md scenarios)

- [ ] **T014** [P] Integration test: Full migration workflow
  - **File**: `tests/integration/test_nodepk_migration.py`
  - **Test Class**: `TestMigrationWorkflow`
  - **Test Methods**:
    - `test_migration_validate_only_mode()` - Run migration with --validate-only, verify report generated, no changes
    - `test_migration_execute_mode()` - Load sample data, run migration with --execute, verify nodes table populated and FK constraints added
    - `test_migration_idempotent()` - Run migration twice, verify second run is no-op
  - **Expected**: Tests FAIL (migration utility doesn't exist yet)

---

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### Schema Implementation

- [ ] **T015** Execute nodes table creation in IRIS
  - **File**: Execute `sql/migrations/001_add_nodepk_table.sql` via migration utility
  - **Action**:
    - Add `execute_sql_migration()` function to `scripts/migrations/migrate_to_nodepk.py`
    - Read SQL file, execute via `cursor.execute()`
    - Log table creation success
  - **Validation**: Query `SELECT * FROM nodes LIMIT 1` succeeds (empty result OK)
  - **Expected**: T004 tests now pass (nodes table exists)

- [ ] **T016** Add foreign key constraint to rdf_edges (source node)
  - **File**: Update `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Add SQL statement:
    ```sql
    ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_source
      FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;
    ```
  - **Validation**: Attempt to insert edge with non-existent source node, verify FK violation
  - **Expected**: T005 `test_edge_insert_requires_source_node()` passes

- [ ] **T017** Add foreign key constraint to rdf_edges (destination node)
  - **File**: Update `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Add SQL statement:
    ```sql
    ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_dest
      FOREIGN KEY (o_id) REFERENCES nodes(node_id) ON DELETE RESTRICT;
    ```
  - **Validation**: Attempt to insert edge with non-existent dest node, verify FK violation
  - **Expected**: T005 `test_edge_insert_requires_dest_node()` passes

- [ ] **T018** Add foreign key constraint to rdf_labels
  - **File**: Update `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Add SQL statement:
    ```sql
    ALTER TABLE rdf_labels ADD CONSTRAINT fk_labels_node
      FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;
    ```
  - **Expected**: T006 tests pass

- [ ] **T019** Add foreign key constraint to rdf_props
  - **File**: Update `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Add SQL statement:
    ```sql
    ALTER TABLE rdf_props ADD CONSTRAINT fk_props_node
      FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;
    ```
  - **Expected**: T007 tests pass

- [ ] **T020** Add foreign key constraint to kg_NodeEmbeddings
  - **File**: Update `sql/migrations/001_add_nodepk_table.sql`
  - **Action**: Add SQL statement:
    ```sql
    ALTER TABLE kg_NodeEmbeddings ADD CONSTRAINT fk_embeddings_node
      FOREIGN KEY (id) REFERENCES nodes(node_id) ON DELETE RESTRICT;
    ```
  - **Expected**: T008 tests pass

### Migration Utility Implementation

- [ ] **T021** Implement node discovery logic
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Function**: `discover_nodes(connection) -> List[str]`
  - **Action**: Implement UNION query from Contract 7:
    ```python
    query = """
    SELECT DISTINCT node_id FROM (
      SELECT s AS node_id FROM rdf_labels
      UNION SELECT s FROM rdf_props
      UNION SELECT s FROM rdf_edges
      UNION SELECT o_id FROM rdf_edges
      UNION SELECT id FROM kg_NodeEmbeddings
    ) all_nodes
    """
    ```
  - **Return**: List of unique node IDs discovered
  - **Logging**: Log count of nodes discovered per table
  - **Expected**: T010, T011 tests pass

- [ ] **T022** Implement bulk node insertion with deduplication
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Function**: `bulk_insert_nodes(connection, node_ids: List[str]) -> int`
  - **Action**:
    - Batch INSERT with ON DUPLICATE KEY IGNORE (if IRIS supports) or INSERT ... WHERE NOT EXISTS
    - Use batch size of 1000 nodes per transaction
    - Measure and log insertion rate (nodes/second)
  - **Return**: Count of nodes inserted
  - **Performance**: Must achieve ≥1000 nodes/second
  - **Expected**: T010 `test_bulk_insert_performance()` passes

- [ ] **T023** Implement orphan detection
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Function**: `detect_orphans(connection) -> Dict[str, List[str]]`
  - **Action**: Execute LEFT JOIN queries from Contract 7 validation:
    ```python
    queries = {
      'edges': "SELECT DISTINCT s FROM rdf_edges WHERE s NOT IN (SELECT node_id FROM nodes)",
      'labels': "SELECT DISTINCT s FROM rdf_labels WHERE s NOT IN (SELECT node_id FROM nodes)",
      # ... similar for props, embeddings
    }
    ```
  - **Return**: Dict mapping table name to list of orphaned node IDs
  - **Logging**: Log count and sample of orphans per table
  - **Expected**: T012 tests pass

- [ ] **T024** Implement migration validation mode (--validate-only)
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Function**: `validate_migration(connection) -> Dict`
  - **Action**:
    - Run node discovery
    - Run orphan detection
    - Generate report with counts, duplicates, orphans
    - DO NOT modify database
  - **Return**: Validation report dict
  - **Output**: Print formatted report to console
  - **Expected**: T014 `test_migration_validate_only_mode()` passes

- [ ] **T025** Implement migration execution mode (--execute)
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Function**: `execute_migration(connection) -> bool`
  - **Action**:
    1. Run validation (fail if orphans detected)
    2. Execute SQL migration (create nodes table)
    3. Discover and insert nodes
    4. Add FK constraints
    5. Verify constraints by attempting invalid insert
  - **Error Handling**: Rollback on any failure, log detailed error
  - **Return**: True if successful, False otherwise
  - **Expected**: T014 `test_migration_execute_mode()` passes

- [ ] **T026** Implement CLI main() function
  - **File**: `scripts/migrations/migrate_to_nodepk.py`
  - **Action**:
    - Parse arguments (--validate-only, --execute, --verbose)
    - Load IRIS connection from .env
    - Route to validate_migration() or execute_migration()
    - Handle exceptions and exit codes
  - **Constitutional**: Explicit error handling with actionable messages (Principle VII)
  - **Expected**: Can run from command line per quickstart.md

---

## Phase 3.4: Performance Validation & Benchmarking

- [ ] **T027** [P] Create FK overhead benchmark script
  - **File**: `scripts/migrations/benchmark_fk_overhead.py`
  - **Action**:
    - Measure edge insertion rate WITHOUT FK constraints (baseline)
    - Run migration to add FK constraints
    - Measure edge insertion rate WITH FK constraints
    - Calculate percentage overhead
    - Generate performance report
  - **Performance Goal**: <10% degradation
  - **Output**: Write results to `docs/performance/nodepk_fk_overhead_benchmark.md`
  - **Expected**: Benchmark shows <10% overhead

- [ ] **T028** [P] Run performance validation tests
  - **File**: `tests/integration/test_nodepk_performance.py`
  - **Test Class**: `TestNodePKPerformance`
  - **Test Methods**:
    - `test_node_lookup_under_1ms()` - Query node by PK, verify <1ms
    - `test_bulk_insert_1000_per_second()` - Insert 10K nodes, verify ≥1000/sec
    - `test_edge_insert_degradation_under_10_percent()` - Compare edge insertion with/without FKs
  - **Markers**: `@pytest.mark.performance`, `@pytest.mark.requires_database`
  - **Expected**: All performance tests pass

---

## Phase 3.5: Documentation & Polish

- [ ] **T029** [P] Create migration guide documentation
  - **File**: `docs/setup/MIGRATION_GUIDE_NodePK.md`
  - **Content**:
    - Overview of NodePK feature and why it's needed
    - Prerequisites (IRIS running, backup recommended)
    - Step-by-step migration instructions
    - Troubleshooting common issues (orphans, duplicates, FK violations)
    - Rollback procedure
  - **Include**: Examples from quickstart.md

- [ ] **T030** [P] Update main schema.sql with nodes table
  - **File**: `sql/schema.sql`
  - **Action**:
    - Add nodes table DDL with comments explaining FK relationships
    - Add performance notes (PK index usage, FK overhead)
    - Update schema diagram if present
  - **Constitutional**: Document FK validation patterns for reuse (Principle VIII)

- [ ] **T031** [P] Update README.md with NodePK feature
  - **File**: `README.md`
  - **Action**:
    - Add NodePK to Features section
    - Link to migration guide
    - Update Quick Start with migration step
    - Add note about FK constraint enforcement

- [ ] **T032** Run full test suite validation
  - **Action**: Execute `uv run pytest tests/integration/test_nodepk_*.py -v`
  - **Expected**: All tests pass (constraints, migration, performance)
  - **Validation**: Verify quickstart.md workflow succeeds end-to-end

- [ ] **T033** Run quickstart.md manual validation
  - **Action**: Follow all steps in `specs/001-add-explicit-nodepk/quickstart.md`
  - **Expected**:
    - Migration validates and executes successfully
    - FK constraints correctly reject invalid operations
    - Performance overhead within acceptable range
  - **Constitutional**: Live database testing (Principle II)

---

## Dependencies

**Phase Flow**:
- Setup (T001-T003) → Tests (T004-T014) → Implementation (T015-T026) → Performance (T027-T028) → Polish (T029-T033)

**Critical Dependencies**:
- T004-T014 (all tests) MUST complete and FAIL before T015-T026 (implementation)
- T015 (create nodes table) blocks T016-T020 (FK constraints)
- T021 (node discovery) blocks T022 (bulk insertion)
- T023 (orphan detection) blocks T024 (validation mode)
- T024-T025 block T026 (CLI integration)
- T015-T026 (all implementation) block T027-T028 (performance validation)

**Parallel Groups**:
- **Group A** (T002, T003): Rollback script, migration utility skeleton
- **Group B** (T004-T013): All contract and migration tests (different test classes)
- **Group C** (T027, T028, T029, T030, T031): Performance benchmarks and documentation

---

## Parallel Execution Examples

### Launch all contract tests in parallel (T004-T010):
```bash
# All tests in different test classes, can run concurrently
uv run pytest tests/integration/test_nodepk_constraints.py::TestNodeCreation -v &
uv run pytest tests/integration/test_nodepk_constraints.py::TestEdgeForeignKeys -v &
uv run pytest tests/integration/test_nodepk_constraints.py::TestLabelForeignKeys -v &
uv run pytest tests/integration/test_nodepk_constraints.py::TestPropertyForeignKeys -v &
uv run pytest tests/integration/test_nodepk_constraints.py::TestEmbeddingForeignKeys -v &
uv run pytest tests/integration/test_nodepk_constraints.py::TestNodeDeletion -v &
uv run pytest tests/integration/test_nodepk_migration.py::TestBulkNodeInsertion -v &
wait
```

### Launch migration tests in parallel (T011-T012):
```bash
uv run pytest tests/integration/test_nodepk_migration.py::TestNodeDiscovery -v &
uv run pytest tests/integration/test_nodepk_migration.py::TestOrphanDetection -v &
wait
```

### Launch documentation tasks in parallel (T029-T031):
```bash
# Edit different files concurrently
# T029: docs/setup/MIGRATION_GUIDE_NodePK.md
# T030: sql/schema.sql
# T031: README.md
```

---

## Validation Checklist

**Gate: Review before marking implementation complete**

- [x] All 7 SQL contracts have corresponding test tasks (T004-T010)
- [x] Node entity from data-model.md has table creation task (T015)
- [x] All FK constraint modifications have dedicated tasks (T016-T020)
- [x] All tests come before implementation (Phase 3.2 before 3.3)
- [x] Parallel tasks ([P]) operate on different files/test classes
- [x] Each task specifies exact file path or function name
- [x] No [P] task modifies same file as another [P] task
- [x] Performance requirements have validation tasks (T027-T028)
- [x] Migration workflow has end-to-end integration test (T014)
- [x] Constitutional principles validated throughout (TDD, IRIS-native, explicit errors)

---

## Success Criteria (from quickstart.md)

**Migration Validation**:
- ✅ Migration completes without data loss
- ✅ All unique node IDs discovered and inserted into nodes table
- ✅ Zero orphaned references detected

**Constraint Enforcement**:
- ✅ FK constraints reject edge/label/property/embedding creation for non-existent nodes
- ✅ FK constraints prevent node deletion when dependencies exist
- ✅ UNIQUE constraint prevents duplicate node_id insertion

**Performance**:
- ✅ Node lookup <1ms
- ✅ Bulk insertion ≥1000 nodes/second
- ✅ FK overhead <10% on edge insertion

**Testing**:
- ✅ All integration tests pass against live IRIS database
- ✅ Quickstart.md workflow succeeds end-to-end

---

**Total Tasks**: 33
**Estimated Completion**: All tasks follow TDD constitutional principles and IRIS-native development patterns
