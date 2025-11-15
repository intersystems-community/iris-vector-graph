# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.5] - 2025-11-10

### Fixed
- **CRITICAL: IRIS SQL Schema Compatibility**: Fixed PostgreSQL-incompatible SQL syntax in `GraphSchema.get_base_schema_sql()`
  - Changed `VECTOR(768)` to `VECTOR(FLOAT, 768)` (IRIS requires type specification)
  - Removed `IF NOT EXISTS` from CREATE INDEX statements (IRIS doesn't support it for indexes)
  - Added `execute_schema_sql()` helper for graceful error handling when indexes already exist
  - Location: `iris_vector_graph/schema.py`
  - **Impact**: This bug prevented iris-vector-rag from creating graph schema, causing PPR to fail

### Added
- **Schema Contract Tests**: Created comprehensive test suite to prevent SQL syntax regressions
  - Tests enforce IRIS-specific SQL syntax requirements
  - Validates VECTOR datatype syntax, index creation, HNSW syntax
  - Prevents PostgreSQL-specific syntax from sneaking into codebase
  - Location: `tests/contract/test_schema_contract.py` (8 contract tests)
  - **Why this matters**: Previous tests didn't catch the bug because they used `sql/schema.sql` directly

### Why We Didn't Catch This
The bug existed because:
1. Integration tests used `sql/schema.sql` directly (which had correct syntax)
2. `GraphSchema.get_base_schema_sql()` Python method was never tested against live IRIS
3. iris-vector-rag calls the Python method, not the SQL file
4. No contract tests enforcing IRIS SQL syntax requirements

**Contract Established**: iris-vector-graph MUST maintain IRIS SQL compatibility for integration with iris-vector-rag

## [1.1.4] - 2025-11-09

### Fixed
- **Functional Index PPR Error Handling**: Added explicit capability check for `iris.createIRIS()` before attempting to use Functional Index PPR
  - Provides informative error message when `intersystems-irispython` version is too old (< 5.3.0)
  - Error message directs users to upgrade package or use Pure Python PPR fallback
  - Improves debugging experience by making version requirements explicit
  - Location: `iris_vector_graph/ppr_functional_index.py:86-92`

## [1.1.3] - 2025-11-09

### Added
- **ConnectionManager Compatibility**: IRISGraphEngine now supports both direct IRIS connections and ConnectionManager objects from iris-vector-rag
  - Automatically detects ConnectionManager via duck typing (`hasattr(connection, 'get_connection')`)
  - Fixes `AttributeError: 'ConnectionManager' object has no attribute 'cursor'` in kg_NEIGHBORHOOD_EXPANSION and kg_PERSONALIZED_PAGERANK
  - Enables synonym expansion and graph traversal in HippoRAG pipeline
  - Added comprehensive unit tests in `tests/unit/test_connection_manager_compatibility.py`
  - Location: `iris_vector_graph/engine.py:28-46` in `__init__()`

## [1.1.2] - 2025-11-08

### Fixed
- **PPR Correctness**: Fixed `get_incoming_edges()` to filter out edges with endpoints not in graph
  - Bug: Edges in `rdf_edges` table can reference nodes not in `nodes` table (orphaned references)
  - Symptom: `KeyError` when PPR tried to access scores for non-existent nodes
  - Fix: Added check that both source and target nodes exist before including edge in adjacency
  - Test: `test_ppr_scores_sum_to_one` now passes (was failing with random KeyError)
  - Location: `iris_vector_graph/ppr.py:160` in `get_incoming_edges()`

## [1.1.1] - 2025-11-08

### Fixed
- **Import UX (BREAKING)**: Renamed package module from `iris_vector_graph_core` to `iris_vector_graph`
  - Package name (`iris-vector-graph`) now matches import name (`iris_vector_graph`)
  - **Breaking change**: `from iris_vector_graph_core import ...` is no longer supported
  - **New import**: `from iris_vector_graph import IRISGraphEngine`
  - Cleaner, more intuitive structure following Python conventions

## [1.1.0] - 2025-11-08

### Added

#### Personalized PageRank (PPR) Optimization
- **ObjectScript Native PPR**: Achieved 8.9x performance improvement at 10K nodes (184ms vs 1,631ms Pure Python)
- **Functional Index with Packed Lists**: `$LISTBUILD` + `$LISTNEXT` pattern for zero-copy Global traversal
- **Real-time Graph Adjacency**: Functional Index maintains `^PPR` Globals synchronized with SQL DML operations
- **Dual Implementation**: Pure Python (portable, simple) + ObjectScript Native (IRIS-optimized, faster at scale)
- **Scaling Advantage**: Speedup increases with graph size (1.0x @ 1K nodes → 8.9x @ 10K nodes)

#### ObjectScript Classes
- `Graph.KG.PPRFunctionalIndex`: Functional Index maintaining packed adjacency lists (`^PPR("outL", *)`, `^PPR("inL", *)`)
- `Graph.KG.PPRNative`: ObjectScript PPR implementation using `$LISTNEXT` for in-memory neighbor iteration
- `Graph.KG.PPRCompute`: Embedded Python investigation (documented for future IRIS enhancements)

#### Documentation
- **PPR Optimization Journey**: Complete optimization chronicle from 14ms baseline to 8.9x improvement (docs/ppr-optimization/)
- **Embedded Python API Investigation**: Detailed analysis of `iris.gref()` limitations for IRIS dev team
- **Pre-Release Checklist**: Constitutional amendment defining mandatory quality gates for releases

### Changed
- **README Performance Section**: Updated with PPR scaling benchmarks showing ObjectScript Native advantage
- **Documentation Organization**: Created `docs/ppr-optimization/` subdirectory for related docs
- **Constitution v1.2.0**: Added comprehensive pre-release checklist covering documentation, code hygiene, and versioning

### Fixed
- Removed temporary files (`.sesskey`, `.DS_Store`, `*.log`)
- Updated `.gitignore` with patterns for session files and test outputs
- Scrubbed informal terminology from professional documentation

### Performance
- **10K nodes PPR**: 184ms (ObjectScript Native) vs 1,631ms (Pure Python) = **8.9x faster**
- **1K nodes PPR**: 14.3ms (ObjectScript Native) vs 14.5ms (Pure Python) = **comparable**
- **Scaling behavior**: Sub-linear (10x nodes = 13x time with ObjectScript, 113x time with Python)

## [1.0.0] - 2025-11-05

### Added

#### Core Features
- **IRIS-Native Graph Database**: RDF-based schema (`rdf_labels`, `rdf_props`, `rdf_edges`) with native IRIS globals storage
- **Vector Search with HNSW**: 768-dimensional embeddings with HNSW optimization (100x performance improvement)
- **Hybrid Search**: RRF fusion combining vector similarity + text search + graph constraints
- **Bitemporal Data Model**: Track valid time vs. system time for regulatory compliance (SOX, MiFID II, Basel III)
- **Embedded Python**: Run ML models and graph algorithms in-database using `/usr/irissys/bin/irispython`

#### Deployment Modes
- **External Deployment (DEFAULT)**: Python app connects to IRIS via `iris.connect()` - simpler setup, easier debugging
- **Embedded Deployment (OPTIONAL)**: Python app runs inside IRIS container - maximum performance, requires licensed IRIS

#### Financial Services (Fraud Detection)
- Real-time fraud scoring API (<10ms) with MLP models
- Device fingerprinting and graph-based fraud ring detection
- Bitemporal audit trails for chargeback defense
- Tested at scale: 130M transactions (licensed IRIS), 30M transactions (community IRIS)
- FastAPI fraud server with external and embedded deployment options

#### Biomedical Research
- **Interactive Demo Server**: http://localhost:8200/bio
- **STRING Database Integration**: 10K proteins, 37K interactions from STRING v12.0
- **Protein Search**: Vector similarity search with HNSW (<2ms queries)
- **Network Expansion**: Interactive D3.js visualization with click-to-expand nodes
- **Pathway Analysis**: BFS pathfinding between proteins with confidence scoring
- **Contract Tests**: 20/20 passing (search 6/6, network 5/5, pathway 4/4, scenario 5/5)

#### Performance Optimizations
- **HNSW Vector Index**: 1.7ms vs 5800ms flat search (3400x improvement with ACORN-1)
- **Partial Indexes**: 10x faster queries with `WHERE system_to IS NULL`
- **Foreign Key Constraints**: Referential integrity with 64% performance improvement
- **Bounded Graph Queries**: Max 500 nodes per network expansion (FR-018)

#### Python SDK (`iris_vector_graph_core`)
- `IRISGraphEngine` - Core graph operations
- `HybridSearchFusion` - RRF fusion algorithms
- `TextSearchEngine` - IRIS iFind integration
- `VectorOptimizer` - HNSW optimization utilities
- `BiomedicalClient` - Direct IRIS queries for protein data

#### Package Distribution
- **Optimized Dependencies**: Default install reduced from 75 to 56 packages (25% fewer)
- **Optional ML Features**: `pip install iris-vector-graph[ml]` for fraud detection models (torch, scikit-learn, scipy)
- **Modular Installation**: Core features ~200MB, full ML suite ~1.2GB
- **PyPI Publication**: Available at https://pypi.org/project/iris-vector-graph/

#### Documentation
- Comprehensive README with quick start for both domains
- Deployment mode clarity: External (DEFAULT) vs Embedded (ADVANCED)
- `CLAUDE.md` - Development guidance and architecture
- `TODO.md` - Project roadmap and completed milestones
- `PYPI_CHECKLIST.md` - Publication preparation guide
- Performance benchmarks and scale testing results

### Technical Details

#### Database Schema
- **Nodes Table**: `rdf_labels` with explicit PRIMARY KEY (NodePK implementation)
- **Properties Table**: `rdf_props` with key-value pairs
- **Edges Table**: `rdf_edges` with confidence scores and qualifiers
- **Embeddings Table**: `kg_NodeEmbeddings_optimized` with VECTOR(FLOAT, 768) type
- **Documents Table**: `kg_Documents` for full-text search

#### SQL Procedures
- `kg_KNN_VEC`: Vector similarity search with HNSW
- `kg_TXT`: Full-text search using IRIS iFind
- `kg_RRF_FUSE`: Reciprocal Rank Fusion (Cormack & Clarke SIGIR'09)
- `kg_GRAPH_PATH`: Graph pathfinding with bounded hops

#### Performance Metrics
- Vector search: <10ms (HNSW), <2ms (ACORN-1)
- Graph queries: <1ms (bounded hops)
- Fraud scoring: <10ms (130M transactions)
- Data ingestion: 476 proteins/second (STRING DB)
- Multi-hop queries: <50ms (100K+ proteins)

### Known Limitations
- IRIS database required (InterSystems IRIS 2025.1+)
- HNSW optimization requires ACORN-1 or IRIS 2025.3+
- Embedded deployment requires licensed IRIS
- Python 3.11+ required

### Dependencies
- `intersystems-irispython>=3.2.0` - IRIS database connectivity
- `fastapi>=0.118.0` - Web framework for APIs
- `networkx>=3.0` - Graph algorithms
- `torch>=2.0.0` - ML model support (optional)
- `sentence-transformers>=2.2.0` - Embeddings (optional)

### Testing
- 20/20 biomedical contract tests passing
- Integration tests with live IRIS database
- Performance benchmarks at scale (10K-100K proteins, 30M-130M transactions)

---

## [Unreleased]

### Planned Features
- openCypher API endpoint (branch: `002-add-opencypher-endpoint`)
- GraphQL API with DataLoader batching (merged)
- Multi-query-engine platform (SQL, openCypher, GraphQL)
- Production hardening (SSL/TLS, monitoring, backup procedures)

[1.0.0]: https://github.com/intersystems-community/iris-vector-graph/releases/tag/v1.0.0
