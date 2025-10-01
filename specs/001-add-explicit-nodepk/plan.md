# Implementation Plan: Explicit Node Identity Table

**Branch**: `001-add-explicit-nodepk` | **Date**: 2025-09-30 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/Users/tdyar/ws/iris-vector-graph/specs/001-add-explicit-nodepk/spec.md`

## Summary

This feature introduces an explicit nodes table to establish referential integrity across all graph entities. Currently, node identifiers (VARCHAR strings) are scattered across rdf_labels, rdf_props, rdf_edges, and kg_NodeEmbeddings tables without enforcement of node existence. The implementation will create a central nodes table with foreign key constraints from all dependent tables, ensuring every edge, label, property, and embedding references a valid node. Migration utilities will discover existing nodes, deduplicate them, and validate data integrity before enforcing constraints.

**Technical Approach**: IRIS SQL DDL with foreign key constraints, Python migration utilities for existing data discovery and validation, integration tests against live IRIS database to verify referential integrity enforcement.

**Strategic Context** (from [GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md](../../docs/GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md)):
- **Current Gap**: NodePK identified as ⚠️ Implicit Implementation (line 43) - "No explicit nodes table with enforced uniqueness constraints"
- **Baseline Requirement**: NodePK is part of the minimal baseline for graph primitives (90%+ workload coverage)
- **Priority**: Immediate/High Priority (recommendation line 287) - "Create explicit `nodes(node_id PK, ...)` with foreign keys from rdf_* tables"
- **Impact**: Closes critical gap in Identity primitive layer, enabling future optimizations (query statistics, composite indexes)
- **Alignment**: Completes baseline indexing palette alongside existing EdgePK implementation (✅ line 38)

## Technical Context

**Language/Version**: Python 3.8+, IRIS SQL (Standard SQL with IRIS extensions)
**Primary Dependencies**: InterSystems IRIS 2025.1+, iris Python driver, pytest, uv package manager
**Storage**: InterSystems IRIS database (multi-model SQL + globals + embedded Python)
**Testing**: pytest with @pytest.mark.requires_database, @pytest.mark.integration markers for live IRIS testing
**Target Platform**: Docker-based IRIS (ACORN-1 or Community Edition)
**Project Type**: Single project (database schema + Python utilities + SQL migrations)
**Performance Goals**:
- Node lookup: <1ms per query
- Bulk node insertion: ≥1000 nodes/second
- FK constraint overhead: <10% degradation on edge insertion
- Migration processing: ≥10,000 nodes/second
**Constraints**:
- Must support existing data migration without data loss
- Foreign key enforcement must work with concurrent writes
- Schema change must be backwards compatible with existing queries (additive only)
**Scale/Scope**:
- Current datasets: 27K+ entities, 4K+ relationships, 20K+ embeddings
- Target scale: 1M+ nodes without performance degradation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. IRIS-Native Development ✅ PASS
- **Compliance**: Uses IRIS SQL DDL (CREATE TABLE, FOREIGN KEY constraints), IRIS globals for performance tracking, embedded Python for migration utilities
- **Approach**: Foreign keys leverage IRIS's native constraint validation; migration uses iris.connect() for direct database access

### II. Test-First Development with Live Database Validation ✅ PASS
- **Compliance**: All constraint validation tests MUST run against live IRIS instance
- **Test Strategy**:
  - Integration tests verify FK constraint rejection (@pytest.mark.integration)
  - Migration tests validate data discovery on real IRIS (@pytest.mark.requires_database)
  - Performance tests measure constraint overhead against live database
- **Red-Green-Refactor**: Tests written first (constraint violations, migration validation), implementation follows

### III. Performance as a Feature ✅ PASS
- **Compliance**: Performance requirements explicitly defined (PR-001 through PR-004)
- **Benchmarking**: Migration performance tracked in docs/performance/, FK overhead measured pre/post deployment
- **Degradation trigger**: >10% edge insertion slowdown requires investigation and optimization

### IV. Hybrid Search by Default ⚠️ NOT APPLICABLE
- **Status**: This feature is pure schema/integrity, no search operations involved
- **Note**: Foreign keys may improve join performance for hybrid queries by enabling optimizer statistics

### V. Observability & Debuggability ✅ PASS
- **Compliance**: Migration scripts log progress (nodes discovered, duplicates found, constraints validated)
- **Error Messages**: FK violations return SQLSTATE with constraint name and conflicting node ID
- **Validation Reports**: Migration generates detailed report (orphans detected, duplicates resolved, validation results)

### VI. Modular Core Library ✅ PASS
- **Compliance**: Migration utilities in separate module (scripts/migrations/), schema changes isolated to sql/migrations/
- **Independence**: Core iris_vector_graph_core remains unchanged; FK enforcement is IRIS-layer only

### VII. Explicit Error Handling ✅ PASS
- **Compliance**: FK constraint violations surface as SQLEXCEPTION with actionable details
- **Migration Errors**: Explicit exceptions for orphaned references, duplicate nodes, validation failures
- **No Silent Failures**: All constraint violations halt operation and report exact cause

### VIII. Standardized Database Interfaces ✅ PASS
- **Compliance**: Uses iris.connect() pattern established in existing codebase
- **Migration Pattern**: Follows established batch processing utilities from iris_vector_graph_core/schema.py
- **Contribution**: New FK validation patterns documented for reuse in future constraints

### Additional Constraints

**Versioning & Breaking Changes**:
- **Classification**: MINOR version bump (new table + FKs, additive change)
- **Migration Required**: Yes - `sql/migrations/001_add_nodepk_table.sql`
- **Backwards Compatibility**: Existing queries continue to work; INSERT/DELETE behavior changes (stricter validation)

**Security Requirements**:
- **Compliance**: No new security concerns; uses existing .env credential management
- **Input Validation**: Migration script validates node ID format before insertion

**Documentation Standards**:
- **SQL Documentation**: FK constraints documented in schema.sql with performance notes
- **Migration Guide**: Step-by-step migration procedure in docs/setup/
- **Performance Impact**: Benchmarks included in docs/performance/ showing FK overhead measurements

## Project Structure

### Documentation (this feature)
```
specs/001-add-explicit-nodepk/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
sql/
├── schema.sql                    # ADD: nodes table definition
├── migrations/
│   └── 001_add_nodepk_table.sql # NEW: Migration script
└── migrations/
    └── 001_rollback_nodepk.sql  # NEW: Rollback script

scripts/
└── migrations/
    └── migrate_to_nodepk.py      # NEW: Python migration utility

tests/
└── integration/
    ├── test_nodepk_constraints.py   # NEW: FK constraint tests
    └── test_nodepk_migration.py     # NEW: Migration validation tests

docs/
├── setup/MIGRATION_GUIDE_NodePK.md  # NEW: Migration documentation
└── performance/nodepk_fk_overhead_benchmark.md  # NEW: Performance analysis
```

**Structure Decision**: Single project structure (default). This is a database schema enhancement affecting SQL layer and Python utilities. No REST API changes required (existing endpoints inherit FK validation automatically). Testing structure follows existing pattern with integration/ for live IRIS tests.

## Phase 0: Outline & Research

**Status**: No NEEDS CLARIFICATION items in Technical Context - all technologies known and validated.

**Context from Graph Primitives Assessment**: This implementation addresses the #1 Immediate Priority recommendation from the comprehensive assessment of graph primitives. The assessment identified NodePK as a critical baseline gap (⚠️ Implicit Implementation) that must be resolved before advancing to composite property indexes or path accelerators.

### Research Items

1. **IRIS Foreign Key Performance Characteristics**
   - **Decision**: Use standard SQL FOREIGN KEY syntax with ON DELETE RESTRICT
   - **Rationale**: IRIS supports standard FK constraints with B-tree index-backed validation (~1ms overhead per check)
   - **Performance**: FK lookups use existing PK index on nodes table (O(log n) lookup), minimal overhead for constraint checking
   - **Alternatives Considered**:
     - Application-level validation: Rejected due to race conditions and inconsistent enforcement
     - Triggers: Rejected due to complexity and maintenance burden

2. **Migration Strategy for Existing Data**
   - **Decision**: UNION query to discover all node IDs across tables, then INSERT IGNORE to handle duplicates
   - **Rationale**: Single-pass discovery with automatic deduplication via UNIQUE constraint
   - **SQL Pattern**:
     ```sql
     INSERT INTO nodes (node_id)
     SELECT DISTINCT s FROM (
       SELECT s FROM rdf_labels
       UNION SELECT s FROM rdf_props
       UNION SELECT s FROM rdf_edges
       UNION SELECT o_id FROM rdf_edges
       UNION SELECT id FROM kg_NodeEmbeddings
     ) all_nodes
     ON DUPLICATE KEY IGNORE;
     ```
   - **Alternatives Considered**:
     - Incremental migration per table: Rejected due to duplicate handling complexity
     - Pre-validation scan: Rejected as redundant (UNIQUE constraint handles it)

3. **Constraint Timing: DEFERRABLE vs. IMMEDIATE**
   - **Decision**: IMMEDIATE constraints (IRIS default)
   - **Rationale**: Simpler failure detection; nodes must exist before edges
   - **Import Pattern**: Require nodes-first insertion order (bulk load nodes, then edges)
   - **Alternatives Considered**:
     - DEFERRABLE constraints: IRIS may not support; adds transaction complexity

4. **Cascade Behavior for Node Deletion**
   - **Decision**: ON DELETE RESTRICT (default), explicit CASCADE via migration utility
   - **Rationale**: Prevent accidental data loss; make cascade operations explicit and audited
   - **Cascade Utility**: Python function for safe cascade delete with confirmation
   - **Alternatives Considered**:
     - ON DELETE CASCADE: Rejected as too dangerous for production graph data

**Output**: All research consolidated above; no separate research.md needed (no unknowns to resolve).

## Phase 1: Design & Contracts

*Prerequisites: research.md complete (incorporated above)*

### 1. Data Model (data-model.md)

**Entity: Node**
- **Purpose**: Central registry of all node identifiers in the graph
- **Fields**:
  - `node_id VARCHAR(256) PRIMARY KEY` - Unique node identifier
  - `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` - Node creation timestamp (for audit)
- **Relationships**:
  - Referenced by rdf_labels (s) - 1:N
  - Referenced by rdf_props (s) - 1:N
  - Referenced by rdf_edges (s, o_id) - 1:N each
  - Referenced by kg_NodeEmbeddings (id) - 1:1
- **Indexes**: Primary key B-tree on node_id (automatic)
- **Validation**: node_id NOT NULL, UNIQUE enforced by PK

**Modified Entity: Edge (rdf_edges)**
- **New Constraints**:
  - `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`
  - `FOREIGN KEY (o_id) REFERENCES nodes(node_id) ON DELETE RESTRICT`
- **Impact**: Edge inserts now require source/destination nodes to exist

**Modified Entity: Label (rdf_labels)**
- **New Constraint**:
  - `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`
- **Impact**: Label assignments require node existence

**Modified Entity: Property (rdf_props)**
- **New Constraint**:
  - `FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT`
- **Impact**: Property assignments require node existence

**Modified Entity: Embedding (kg_NodeEmbeddings)**
- **New Constraint**:
  - `FOREIGN KEY (id) REFERENCES nodes(node_id) ON DELETE RESTRICT`
- **Impact**: Embeddings require node existence

### 2. API Contracts

**Note**: This feature has no REST API changes. Existing endpoints (/kg/vectorSearch, /kg/hybridSearch, /kg/metaPath) inherit FK validation automatically via SQL layer.

**Internal SQL Contract** (for data ingestion scripts):

```sql
-- Contract: Insert node before referencing it
-- Precondition: None
-- Postcondition: node_id exists in nodes table
INSERT INTO nodes (node_id) VALUES (:node_id);

-- Contract: Insert edge with valid nodes
-- Precondition: nodes with :source_id and :dest_id exist
-- Postcondition: edge created OR FK violation raised
INSERT INTO rdf_edges (s, p, o_id)
VALUES (:source_id, :predicate, :dest_id);
```

### 3. Contract Tests

**File**: `tests/integration/test_nodepk_constraints.py`

```python
import pytest

@pytest.mark.requires_database
@pytest.mark.integration
class TestNodePKConstraints:

    def test_edge_insert_requires_source_node(self, iris_connection):
        """
        GIVEN: nodes table with no node 'nonexistent_node'
        WHEN: inserting edge with s='nonexistent_node'
        THEN: FK constraint violation raised
        """
        # Test MUST fail before implementation
        cursor = iris_connection.cursor()
        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                ['nonexistent_source', 'relates_to', 'any_dest']
            )
        assert 'FOREIGN KEY' in str(exc_info.value) or 'constraint' in str(exc_info.value).lower()

    def test_edge_insert_requires_dest_node(self, iris_connection):
        """FK constraint on destination node"""
        # Similar test for o_id FK
        pass

    def test_node_delete_blocked_by_edge(self, iris_connection):
        """
        GIVEN: node 'A' with edges referencing it
        WHEN: attempting DELETE FROM nodes WHERE node_id='A'
        THEN: FK constraint violation (ON DELETE RESTRICT)
        """
        pass

    def test_concurrent_node_insert_deduplication(self, iris_connection):
        """
        GIVEN: two processes attempting to insert same node_id
        WHEN: executing concurrent INSERTS
        THEN: one succeeds, one gets UNIQUE violation (graceful handling)
        """
        pass
```

**File**: `tests/integration/test_nodepk_migration.py`

```python
@pytest.mark.requires_database
@pytest.mark.integration
class TestNodePKMigration:

    def test_migration_discovers_all_nodes(self, iris_connection_with_sample_data):
        """
        GIVEN: existing graph data (edges, labels, props, embeddings)
        WHEN: running migration discover_nodes()
        THEN: all unique node IDs collected in nodes table
        """
        pass

    def test_migration_handles_duplicates(self, iris_connection):
        """Duplicate node IDs across tables handled gracefully"""
        pass

    def test_migration_detects_orphans(self, iris_connection):
        """Orphaned edge references detected before constraint enforcement"""
        pass
```

### 4. Test Scenarios from User Stories

**Scenario 1**: Edge creation with FK validation
- **Setup**: Create nodes 'PROTEIN:p53' and 'DISEASE:cancer'
- **Action**: Insert edge (s='PROTEIN:p53', p='associated_with', o_id='DISEASE:cancer')
- **Expected**: Edge created successfully
- **Negative**: Insert edge with o_id='nonexistent' → FK violation

**Scenario 2**: Migration of existing data
- **Setup**: Load 27K entities from biomedical dataset
- **Action**: Run migrate_to_nodepk.py
- **Expected**: All nodes discovered, dedupl

icated, nodes table populated
- **Validation**: COUNT(nodes) = COUNT(DISTINCT node IDs across all tables)

**Scenario 3**: Concurrent node creation
- **Setup**: Two processes inserting same node_id simultaneously
- **Action**: Process A: INSERT nodes; Process B: INSERT nodes (same ID)
- **Expected**: One succeeds, other gets UNIQUE violation (handled gracefully)

### 5. Quickstart Test Script

**File**: `specs/001-add-explicit-nodepk/quickstart.md`

```markdown
# NodePK Feature Quickstart

## Prerequisites
- IRIS database running (docker-compose up -d)
- Python environment activated (source .venv/bin/activate)

## Step 1: Run Migration
\`\`\`bash
uv run python scripts/migrations/migrate_to_nodepk.py --validate-only
# Expected: Report of nodes discovered, duplicates, orphans

uv run python scripts/migrations/migrate_to_nodepk.py --execute
# Expected: Nodes table populated, FKs added, validation passed
\`\`\`

## Step 2: Verify Constraints
\`\`\`bash
uv run pytest tests/integration/test_nodepk_constraints.py -v
# Expected: All tests pass (FK violations correctly raised)
\`\`\`

## Step 3: Test Data Insertion
\`\`\`python
import iris
conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
cursor = conn.cursor()

# Insert node first
cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST:node1')")

# Insert edge (should succeed)
cursor.execute(
    "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
    ['TEST:node1', 'relates_to', 'TEST:node1']
)

# Try inserting edge with invalid node (should fail)
try:
    cursor.execute(
        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
        ['INVALID:node', 'relates_to', 'TEST:node1']
    )
except Exception as e:
    print(f"Expected FK violation: {e}")
\`\`\`

## Step 4: Performance Validation
\`\`\`bash
uv run python scripts/migrations/benchmark_fk_overhead.py
# Expected: <10% degradation on edge insertion
\`\`\`

## Success Criteria
- ✅ Migration completes without data loss
- ✅ FK constraints enforce node existence
- ✅ Performance overhead within acceptable range (<10%)
- ✅ All integration tests pass
\`\`\`
```

## Phase 2: Task Planning Approach

*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
1. **Schema Tasks**: SQL migration script creation (001_add_nodepk_table.sql, rollback script)
2. **Migration Utility Tasks**: Python script for node discovery, deduplication, validation
3. **Test Tasks**: Contract tests (constraints, migration validation, performance benchmarks)
4. **Documentation Tasks**: Migration guide, performance analysis, updated README

**Task Categories**:
- **Setup** (T001-T003): Schema migration files, Python migration utility structure
- **Tests First** (T004-T008): Contract tests for FK violations, migration tests, performance tests
- **Core Implementation** (T009-T015): SQL DDL execution, migration logic, FK constraint addition
- **Validation** (T016-T020): Migration dry-run, performance benchmarking, documentation

**Ordering Strategy**:
- **TDD**: All constraint tests written before SQL execution (tests fail initially)
- **Dependencies**: nodes table creation → FK addition → migration utility → performance validation
- **Parallel**: Test writing can occur in parallel with schema design ([P] markers)

**Estimated Output**: 20-25 numbered tasks in tasks.md

**Key Task Examples**:
- T001: Create sql/migrations/001_add_nodepk_table.sql with nodes table DDL
- T004 [P]: Write test_edge_insert_requires_source_node (must fail initially)
- T005 [P]: Write test_node_delete_blocked_by_edge (must fail initially)
- T009: Execute nodes table creation in IRIS
- T010: Add FK constraint to rdf_edges (s column)
- T011: Add FK constraint to rdf_edges (o_id column)
- T012: Implement migrate_to_nodepk.py node discovery logic
- T016: Run migration dry-run on test dataset, validate report
- T019: Benchmark FK overhead, document in docs/performance/

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation

*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following constitutional principles)
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

### Relationship to Graph Primitives Roadmap

This feature is **Step 1 of 3** in completing the baseline indexing palette per [GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md](../../docs/GRAPH_PRIMITIVES_IMPLEMENTATION_ASSESSMENT.md):

**Completed with this feature**:
- ✅ **Identity Layer**: NodePK + EdgePK (EdgePK already exists)

**Enabled for future features**:
- ⏭️ **Composite Property Indexes** (#2 Near-term Priority) - Requires NodePK for FK relationships
- ⏭️ **Query Statistics Enhancement** (#3 Near-term Priority) - Requires NodePK for histogram generation
- ⏭️ **Globals Maintenance** (#2 Immediate Priority) - Can leverage NodePK for incremental updates

**Impact on Architecture**:
- **Query Optimizer**: FK constraints enable optimizer to collect cardinality statistics for better join planning
- **Data Integrity**: Prevents orphaned references that could corrupt graph traversal results
- **Performance**: Baseline overhead <10% enables future optimizations without regression
- **Testing**: Establishes pattern for constraint-based validation applicable to future indexes

## Competitive Intelligence & Strategic Positioning

### Market Landscape Analysis

#### TigerGraph
**Strengths**:
- Enterprise-proven graph database with major customers (JPMC, Mastercard, Microsoft, Unilever)
- GSQL query language with high-performance graph algorithms
- GPU acceleration via NVIDIA cuGraph integration (45x-137x speedup for PageRank, Louvain)
- Massively parallel architecture scaling "without size limits"

**GPU Architecture**:
- Streams edges to NVIDIA A100 GPUs (8 GPUs, 320GB HBM2 each) via Thrift RPC
- cuGraph library accelerates graph-only algorithms (PageRank, community detection, Jaccard)
- Cost efficiency: 50x cost savings despite higher GPU hourly rates ($32.77/hr vs $7.41/hr CPU)
- Performance: 100x speed improvement for large graphs (Graph 26: 4842s → 41s)

**Limitations**:
- No native vector search (HNSW) capability
- Proprietary GSQL language (vs standard SQL)
- GPU acceleration requires external cuGraph library (RPC overhead)
- Cannot accelerate vector + graph hybrid queries on GPU

#### Neo4j
**Strengths**:
- Market-leading graph database with Cypher query language
- Pregel API implementation via Graph Data Science (GDS) library
- Vertex-centric computation model with superstep message passing
- JAR plugin ecosystem for custom algorithms

**Limitations**:
- No graph-centric partition-aware programming model
- No native HNSW vector search in same engine (requires separate vector database)
- GPU acceleration not mentioned in core product

### IRIS Vector Graph Unique Positioning

**Capabilities No Competitor Has**:

1. **Native Vector + Graph Fusion**: HNSW vector search in same SQL engine as graph traversal
2. **Multi-Model Integration**: SQL, globals, vectors, embedded Python - single platform
3. **Graph-Centric + Vectors Opportunity**: Partition-aware programming + HNSW (TigerGraph has partition-awareness without vectors; vector DBs have HNSW without graph-centric partitions)

**Competitive Differentiation Matrix**:

| Capability | TigerGraph | Neo4j | IRIS Vector Graph |
|------------|------------|-------|-------------------|
| Graph traversal | ✅ GSQL | ✅ Cypher | ✅ SQL |
| Vector search (HNSW) | ❌ External only | ❌ External only | ✅ Native SQL |
| GPU acceleration | ✅ cuGraph (graph-only) | ❌ Not advertised | 🎯 **Opportunity** |
| Hybrid vector+graph | ❌ Requires 2 systems | ❌ Requires 2 systems | ✅ Single engine |
| Graph-centric programming | ✅ Partition-aware | ❌ Vertex-centric only | 🎯 **Opportunity** |
| Standard SQL | ❌ Proprietary GSQL | ❌ Cypher | ✅ ANSI SQL |

### Strategic Roadmap: Medium-Term Features

#### Feature: Graph-Centric Computation Framework
**Priority**: Medium (after baseline primitives complete)
**Inspiration**: IBM "Think Like a Graph" paper (Giraph++), TigerGraph partition-aware architecture

**Concept**: Expose partition structure to enable local optimization within partitions, reducing message passing overhead by 60x-200x (per IBM paper showing 63x speedup for connected components).

**IRIS-Specific Implementation**:
```python
class GraphPartition:
    def compute_partition(self, partition: GraphPartition):
        # Step 1: HNSW vector clustering WITHIN partition (local SQL - fast!)
        vector_clusters = partition.execute_sql("""
            SELECT id, cluster_id FROM kg_NodeEmbeddings_Partitioned
            WHERE partition_id = ? AND VECTOR_COSINE(emb, ?) > 0.8
        """, [self.partition_id, seed_vector])

        # Step 2: BFS structural communities (local globals ^KG - fast!)
        structural_communities = partition.bfs_community_detection()

        # Step 3: Merge locally without cross-partition messages
        combined = self.merge_communities(vector_clusters, structural_communities)

        # Step 4: Only send boundary vertex labels across network
        for boundary in partition.boundaryVertices():
            partition.sendMsg(boundary.owner, combined[boundary.id])
```

**Three Killer Use Cases**:

1. **Semantic Community Detection** (hybrid vector+topology)
   - HNSW clustering within partition for semantic similarity
   - BFS for structural communities
   - Merged scoring: nodes similar semantically AND structurally connected
   - **Competitor gap**: TigerGraph can't do HNSW clustering; Neo4j lacks partition-awareness

2. **Vector-Guided Knowledge Graph Completion**
   - HNSW search within partition for semantically similar target nodes
   - BFS path finding from source to similar nodes (all local, no messages)
   - Hybrid scoring: graph distance + vector similarity
   - **Competitor gap**: Requires vector+graph fusion in same partition

3. **GNN Training with HNSW Negative Sampling**
   - Asynchronous neighbor aggregation within partition (graph-centric advantage)
   - HNSW-based negative sampling (unique to vector-enabled systems)
   - Bulk SQL updates per partition, not per vertex (60x fewer operations)
   - **Competitor gap**: No system combines GNN + HNSW + partition-awareness

**Dependencies**:
- ✅ NodePK table (this feature) - enables partition assignment by node
- ⏭️ Partition-aware embedding table schema
- ⏭️ IRIS globals partition key strategy

**Performance Target**: 60x-100x speedup over vertex-centric Pregel (per IBM paper benchmarks)

#### Feature: GPU-Accelerated Hybrid Search
**Priority**: Medium-High (competitive parity + differentiation)
**Inspiration**: TigerGraph cuGraph integration, but applied to vector+graph fusion

**Concept**: Accelerate both HNSW vector search AND graph traversal on GPU, then fuse results in same GPU kernel - capability no competitor has.

**Architecture**:
```python
class GPUHybridSearch:
    def search(self, query_vector, graph_constraints):
        # CUDA kernel 1: HNSW vector search on GPU (like Pinecone/Weaviate)
        vector_results = cuda_hnsw_search(query_vector, top_k=100)

        # CUDA kernel 2: Graph traversal on GPU (like TigerGraph's cuGraph)
        graph_results = cuda_graph_filter(vector_results, graph_constraints)

        # CUDA kernel 3: RRF fusion scoring on GPU
        # *** TigerGraph CANNOT do this - requires 2 separate systems ***
        fused_results = cuda_rrf_fusion(vector_results, graph_results)

        return fused_results  # All on GPU, no CPU roundtrips
```

**Competitive Positioning**:
- TigerGraph: GPU-accelerates graph-only algorithms, no vector search
- Vector DBs (Pinecone, Weaviate): GPU-accelerate vectors-only, no graph traversal
- IRIS: GPU-accelerate vector + graph + fusion scoring in single kernel

**Implementation Strategy**:
1. **Phase 1**: CUDA kernels for HNSW search (leverage FAISS GPU libraries)
2. **Phase 2**: CUDA kernels for graph traversal (leverage cuGraph patterns)
3. **Phase 3**: Custom RRF fusion kernel (unique to IRIS, no library available)

**Dependencies**:
- ✅ NodePK table (this feature) - enables GPU memory layout optimization
- ✅ Existing HNSW index (sql/schema.sql)
- ⏭️ CUDA development environment + IRIS embedded Python GPU bindings

**Performance Target**:
- 50x-100x speedup for HNSW search (per GPU vector DB benchmarks)
- 45x-137x speedup for graph traversal (per TigerGraph cuGraph benchmarks)
- **Unique advantage**: Zero-copy fusion (no CPU serialization/deserialization)

**Cost Target**: 50x cost reduction (per TigerGraph's own analysis showing GPU hourly rate premium offset by speed gains)

### Positioning Statement

**IRIS Vector Graph: The Only Multi-Model Database with Native GPU-Accelerated Vector + Graph Fusion**

While TigerGraph accelerates graph algorithms and vector databases accelerate similarity search, IRIS Vector Graph uniquely combines:
- **Native HNSW** vector search in SQL tables (not external integration)
- **IRIS Globals** for persistent partition-local state (graph-centric programming)
- **GPU acceleration roadmap** for hybrid vector+graph queries (competitive leap)
- **Standard SQL** with embedded Python (vs proprietary query languages)

Target workloads IRIS uniquely serves:
- Semantic fraud detection (vector similarity + transaction graph)
- Knowledge graph completion (embedding-guided link prediction)
- Customer 360 with semantic clustering (vector + topology fusion)
- GNN training with partition-aware HNSW negative sampling

## Graph Query Language Strategy

### Query Language Landscape (2024-2025)

**GQL (ISO/IEC 39075:2024)**:
- Published April 2024 - first new ISO database language since SQL in 1987
- 14-0-6 approval vote (approve-disapprove-abstain) - strong industry consensus
- Neo4j, Amazon major contributors
- Property graph focus with SQL-like declarative syntax
- Cypher users have migration path (languages converging)

**Cypher (openCypher)**:
- Dominant in enterprise graph databases (Neo4j market leader)
- SQL-like declarative syntax with ASCII-art patterns: `(a)-[r:KNOWS]->(b)`
- Wide adoption: Neo4j, AgensGraph, RedisGraph, AWS Neptune (partial)
- **Strategic note**: Converging with GQL standard (Neo4j committed to both)

**Gremlin (Apache TinkerPop)**:
- Imperative/declarative graph traversal language
- Multi-vendor support: JanusGraph, AWS Neptune, Azure Cosmos DB, DataStax
- Polyglot bindings: Java, Python, JavaScript, Scala
- Step-by-step traversal model: `g.V().has('name', 'Alice').out('knows')`

**GSQL (TigerGraph)**:
- Proprietary single-vendor language (TigerGraph only)
- High-performance parallel execution capabilities
- Combines declarative + imperative patterns
- **Strategic note**: Vendor lock-in concern for enterprises

### IRIS Current State Analysis

**Existing Capability**: Standard SQL with IRIS extensions
```sql
-- Current approach: verbose SQL for graph patterns
SELECT e3.o_id FROM rdf_edges e1
JOIN rdf_edges e2 ON e1.o_id = e2.s
JOIN rdf_edges e3 ON e2.o_id = e3.s
WHERE e1.s = 'START_NODE' AND e1.p = 'knows'
  AND e2.p = 'works_at' AND e3.p = 'located_in'
```

**Problem**: SQL poorly suited for expressing graph patterns
- Multi-hop traversals require complex self-joins
- Pattern matching not intuitive
- Competitive disadvantage vs. Cypher/Gremlin user experience

### Strategic Options Evaluation

| Option | Time to Market | Developer Appeal | Future-Proof | Implementation Cost |
|--------|---------------|------------------|--------------|---------------------|
| SQL-only (status quo) | ✅ Immediate | ❌ Poor for graphs | ✅ ANSI standard | ✅ Zero |
| Native GQL implementation | ❌ 18-24 months | ✅ ISO standard | ✅✅ Best | ❌❌ High |
| Native Cypher implementation | ⚠️ 12-18 months | ✅✅ Huge base | ⚠️ Converging to GQL | ❌ High |
| Native Gremlin implementation | ⚠️ 12-18 months | ✅ Cloud users | ✅ Apache project | ❌ High (TinkerPop) |
| **Query Translation Layer** | ✅✅ 3-6 months | ✅✅ Multi-language | ✅✅ Flexible | ✅ Moderate |

### Recommended Strategy: Multi-Dialect Translation Layer ⭐

**Concept**: Translate graph query languages → optimized IRIS SQL + embedded Python + globals

**Architecture**:
```python
# Users write in their preferred graph language
query_cypher = """
MATCH (p:Protein)-[:INTERACTS_WITH]->(d:Disease)
WHERE VECTOR_COSINE(p.embedding, $query_vector) > 0.8
RETURN p.name, d.name, VECTOR_COSINE(p.embedding, $query_vector) AS similarity
ORDER BY similarity DESC
LIMIT 10
"""

# Translation layer converts to optimized IRIS SQL
translator = GraphQueryTranslator(dialect='cypher')
execution_plan = translator.to_iris_sql(query_cypher)

# Generated IRIS SQL leverages native capabilities:
# - Foreign keys from NodePK (this feature!)
# - HNSW vector index for fast similarity search
# - IRIS globals for graph traversal optimization
# - SQL optimizer statistics from FK constraints
```

**Why Translation Wins**:
1. **No vendor lock-in**: Users write portable Cypher/GQL, IRIS translates internally
2. **Leverage IRIS strengths**: Translation targets optimized SQL + globals + HNSW
3. **Competitive positioning**: "Supports Cypher, GQL, AND Gremlin" (no competitor does all three)
4. **Fast time-to-market**: Focus on pattern translation, not full language runtime
5. **Unique hybrid queries**: Extend Cypher/GQL with vector similarity syntax

### Implementation Roadmap

#### Phase 1: Cypher Translation Layer (6-9 months)
**Priority**: High (capture Neo4j developer migration opportunity)
**Scope**: Core Cypher pattern matching + IRIS vector extensions

**Supported Cypher Patterns (v1.0)**:
```cypher
-- Node matching with labels and properties
MATCH (n:Label {property: value})

-- Relationship patterns with direction and types
MATCH (a)-[r:REL_TYPE]->(b)

-- Variable-length paths
MATCH (start)-[:KNOWS*1..3]->(end)

-- WHERE clause filtering (standard + vector extension)
WHERE n.property = value
  AND VECTOR_COSINE(n.embedding, $query_vec) > 0.8  -- IRIS extension!

-- RETURN with aggregation and ordering
RETURN n, COUNT(r) AS degree
ORDER BY VECTOR_COSINE(n.embedding, $query_vec) DESC
LIMIT 10
```

**Translation Example**:
```cypher
-- User writes Cypher
MATCH (p:Protein {name: 'TP53'})-[:INTERACTS*1..3]->(target:Protein)
WHERE VECTOR_COSINE(target.embedding, $query_emb) > 0.85
RETURN target.name, target.description
ORDER BY VECTOR_COSINE(target.embedding, $query_emb) DESC

-- Translator generates optimized IRIS SQL:
SELECT DISTINCT t.node_id, p_name.val AS name, p_desc.val AS description,
       VECTOR_COSINE(emb.emb, ?) AS similarity
FROM nodes p
JOIN rdf_labels l_p ON p.node_id = l_p.s AND l_p.label = 'Protein'
JOIN rdf_props p_p ON p.node_id = p_p.s AND p_p.key = 'name' AND p_p.val = 'TP53'
-- Recursive CTE for variable-length path
WITH RECURSIVE path_traverse(node_id, depth) AS (
  SELECT p.node_id, 0 FROM nodes p WHERE p.node_id = ?
  UNION ALL
  SELECT e.o_id, pt.depth + 1
  FROM path_traverse pt
  JOIN rdf_edges e ON pt.node_id = e.s AND e.p = 'INTERACTS'
  WHERE pt.depth < 3
)
JOIN nodes t ON path_traverse.node_id = t.node_id
JOIN rdf_labels l_t ON t.node_id = l_t.s AND l_t.label = 'Protein'
JOIN kg_NodeEmbeddings emb ON t.node_id = emb.id
JOIN rdf_props p_name ON t.node_id = p_name.s AND p_name.key = 'name'
LEFT JOIN rdf_props p_desc ON t.node_id = p_desc.s AND p_desc.key = 'description'
WHERE VECTOR_COSINE(emb.emb, ?) > 0.85
ORDER BY similarity DESC
```

**Dependencies**:
- ✅ NodePK table with foreign keys (this feature!) - enables JOIN optimization
- ⏭️ Cypher parser (ANTLR grammar for openCypher)
- ⏭️ Pattern-to-SQL translator with IRIS-specific optimizations
- ⏭️ Vector function syntax extensions to Cypher

**Deliverables**:
- `iris_vector_graph_core/query/cypher_translator.py` - Core translation engine
- `iris_vector_graph_core/query/cypher_parser.py` - ANTLR-based parser
- `tests/integration/test_cypher_translation.py` - Translation validation tests
- Documentation: "Cypher on IRIS Vector Graph" guide

#### Phase 2: GQL Translation Layer (12-18 months)
**Priority**: Medium-High (align with ISO standard as it matures)
**Timing**: Wait for GQL reference implementations to stabilize

**Strategic Rationale**:
- GQL standard published April 2024 (very recent)
- Industry moving toward GQL (Cypher converging with standard)
- Positioning: "First vector database with native GQL support"
- Cypher translator provides foundation (languages converging)

**Approach**:
- Monitor GQL reference implementation development
- Leverage Cypher translator codebase (syntax similarity)
- Add GQL-specific features (graph schema types, SQL/PGQ integration)

**Unique Opportunity**: GQL standard includes SQL integration path (SQL/PGQ) - IRIS already has SQL foundation

#### Phase 3: Gremlin Translation Layer (18-24 months)
**Priority**: Medium (cloud compatibility and multi-vendor positioning)

**Strategic Rationale**:
- Strong AWS Neptune and Azure Cosmos DB adoption
- Enables "cloud-compatible graph database" positioning
- Gremlin Python bindings fit IRIS embedded Python model

**Scope**: Gremlin traversal steps → IRIS SQL + embedded Python
```python
# Gremlin query (Python style)
g.V().has('Protein', 'name', 'TP53') \
  .out('INTERACTS').has('Protein') \
  .where(__.values('embedding').is_(P.vectorSimilar(query_vec, 0.8))) \
  .values('name', 'description') \
  .order().by(__.values('embedding').vectorDistance(query_vec))

# Translates to same optimized IRIS SQL as Cypher example above
```

**Implementation Note**: Imperative Gremlin steps may require embedded Python execution for complex traversals (leverage IRIS strength)

#### Phase 4: Multi-Dialect Query Gateway (24+ months)
**Priority**: Low (polish and unification)

**Vision**: Unified query interface supporting all dialects
```python
from iris_vector_graph_core import GraphQueryGateway

gateway = GraphQueryGateway(connection=iris_conn)

# Same query, three different dialects - all execute optimally
results_cypher = gateway.execute("""
  MATCH (n)-[r]->(m) WHERE n.name = 'Alice'
  RETURN n, r, m
""", dialect='cypher')

results_gql = gateway.execute("""
  SELECT n, r, m FROM GRAPH my_graph
  MATCH (n)-[r]->(m)
  WHERE n.name = 'Alice'
""", dialect='gql')

results_gremlin = gateway.execute("""
  g.V().has('name', 'Alice').outE().inV()
""", dialect='gremlin')
```

### Competitive Differentiation Matrix: Query Languages

| Database | SQL | Cypher | GQL | Gremlin | HNSW Vector Integration |
|----------|-----|--------|-----|---------|-------------------------|
| Neo4j | ❌ | ✅ Native | 🔄 Converging | ❌ | ❌ External only |
| TigerGraph | ❌ | ❌ | ❌ | ❌ | ❌ External only |
| JanusGraph | ❌ | ❌ | ❌ | ✅ Native | ❌ |
| AWS Neptune | ❌ | ✅ Subset | ❌ | ✅ Native | ❌ External only |
| Azure Cosmos DB | ❌ | ❌ | ❌ | ✅ Native | ✅ Limited |
| **IRIS Vector Graph** | ✅ Native | 🎯 **Phase 1** | 🎯 **Phase 2** | 🎯 **Phase 3** | ✅ **Native HNSW** |

**Unique Positioning**: "The only graph database supporting SQL, Cypher, GQL, AND Gremlin with native vector search"

### Developer Experience Value Proposition

**Problem**: Developers forced to choose between graph query language familiarity and vector search capability

**IRIS Solution**: Choose your preferred graph language, get native vector search free

**Example: Neo4j Cypher User Migration**:
```cypher
-- Familiar Cypher syntax (works on IRIS!)
MATCH (p:Protein {name: 'TP53'})-[:INTERACTS*1..3]->(target)

-- IRIS extension: native vector similarity (Neo4j can't do this!)
WHERE VECTOR_COSINE(target.embedding, $query_emb) > 0.85

RETURN target.name, target.description
ORDER BY VECTOR_COSINE(target.embedding, $query_emb) DESC
```

**Marketing Message**:
- "Write Cypher, Get Vectors Free" (Neo4j migration pitch)
- "GQL-Ready for the ISO Standard Future" (forward-looking enterprises)
- "Gremlin-Compatible for Cloud Portability" (AWS/Azure users)
- "Pure SQL When You Need It" (traditional SQL shops)

### Dependencies on NodePK Feature

The query translation strategy **critically depends** on NodePK table (this feature):

1. **JOIN Optimization**: Foreign keys enable SQL optimizer to use cardinality statistics
   ```sql
   -- Cypher: MATCH (n)-[r]->(m)
   -- Translates to JOIN with FK-backed optimization:
   SELECT n.*, r.*, m.* FROM nodes n
   JOIN rdf_edges r ON n.node_id = r.s      -- FK constraint → optimizer stats!
   JOIN nodes m ON r.o_id = m.node_id       -- FK constraint → optimizer stats!
   ```

2. **Pattern Validation**: Translation layer can validate node existence before generating complex queries
   ```python
   # Translator can check if start node exists before building recursive CTE
   if not translator.node_exists(start_node_id):
       raise CypherQueryError("Start node does not exist")
   ```

3. **Graph Schema Discovery**: NodePK enables translation layer to query graph structure
   ```sql
   -- Translator introspects available node labels, relationship types
   SELECT DISTINCT label FROM rdf_labels;
   SELECT DISTINCT p FROM rdf_edges;
   ```

**Conclusion**: NodePK is foundational - complete this feature before starting Cypher translation layer

## Complexity Tracking

*No constitutional violations - all gates passed. This section intentionally left empty.*

## Progress Tracking

*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved
- [x] Complexity deviations documented (none)

---
*Based on Constitution v1.1.0 - See `.specify/memory/constitution.md`*
