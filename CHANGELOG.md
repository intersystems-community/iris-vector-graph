# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-10-19

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
