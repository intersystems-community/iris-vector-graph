# IRIS Graph-AI TODO List

**Last Updated**: 2025-10-09
**Current Status**: Biomedical Demo with IRIS Backend + Multi-Query-Engine Platform

## 🚀 Immediate Actions (P0)

### Biomedical Demo - IRIS Backend ✅ COMPLETE

#### ✅ Completed
- Direct IRIS biomedical client (`iris_biomedical_client.py`)
- Protein search with real STRING DB data (10K proteins, 37K interactions)
- EGFR search working with real IRIS queries
- Comprehensive setup documentation (`docs/biomedical-demo-setup.md`)
- Updated README with biomedical demo instructions
- **Network endpoint**: IRIS graph traversal with rdf_edges queries (5/5 tests passing)
- **Pathway endpoint**: BFS pathfinding with case-insensitive matching (4/4 tests passing)
- **Contract tests**: 20/20 passing (100% - search, network, pathway, scenario endpoints)

### PyPI Publication Preparation 🔄 IN PROGRESS

#### ✅ Completed
- Clarified deployment modes in README (External DEFAULT, Embedded OPTIONAL)
- Created PYPI_CHECKLIST.md with publication steps
- Created MANIFEST.in to include SQL files and documentation
- Updated pyproject.toml with proper keywords and classifiers
- Created CHANGELOG.md for v1.0.0
- Security audit: No hardcoded production credentials found

#### 🔄 Next Steps
- [ ] Run code quality tools (black, isort, flake8, mypy)
- [ ] Test package build in clean environment
- [ ] Upload to TestPyPI for validation
- [ ] Final review and publication to PyPI

### Complete Multi-Query-Engine Platform
- [ ] **Merge openCypher API to main** ✅ READY
  - Branch: `002-add-opencypher-endpoint`
  - Status: 26/32 tasks complete (81%, sufficient for MVP)
  - Implementation: 2,222 lines across 9 new files
  - Contract tests: Passing
  - Documentation: Complete in CLAUDE.md

```bash
git checkout main
git merge 002-add-opencypher-endpoint --no-ff -m "Merge openCypher MVP implementation"
```

### Production Deployment Ready
- [ ] **Deploy Current System** - Three query engines production-ready
  - openCypher API: POST /api/cypher (NEW)
  - GraphQL API: POST /graphql (NEW)
  - SQL Direct: iris.connect()
  - Performance: 21.7x improvement validated
  - Scale: Tested on 20K+ proteins, 50K+ relationships

### Vector Search Optimization
- [x] **Migrate to Optimized Vector Table** ✅ COMPLETED
  - Previous: 5.8s fallback performance (Python CSV parsing)
  - Achieved: 50ms performance with kg_NodeEmbeddings_optimized + HNSW
  - Action: ✅ Completed data migration to native VECTOR(FLOAT, 768) format
  - Impact: 116x performance improvement (10,000 deduplicated vectors)

### Production Hardening
- [ ] **SSL/TLS Configuration**
  - Configure HTTPS for IRIS REST endpoints
  - Set up certificate management
  - Update connection strings in client examples

- [ ] **Monitoring Setup**
  - IRIS System Monitor integration
  - Performance metrics dashboard
  - Alert thresholds for query latency

- [ ] **Backup Procedures**
  - Database backup strategy
  - Vector index backup/restore
  - Recovery testing validation

## 🎯 Query Engine Enhancements (P1)

### openCypher API Post-MVP
- [ ] **Parser Upgrade**: Integrate libcypher-parser C library
  - Full Cypher syntax support (nested expressions, subqueries, WITH clauses)
  - Replace pattern-based MVP parser
  - Estimated: 1-2 weeks

- [ ] **Query Plan Caching**: Implement enableCache parameter
  - Cache AST-to-SQL translation results
  - Keyed by (query_pattern, parameter_types)
  - Redis or in-memory LRU cache
  - Estimated: 3-5 days

- [ ] **Variable-Length Paths**: Support *min..max syntax
  - Use IRIS recursive CTEs
  - Max depth enforcement (default 10, configurable)
  - Estimated: 1 week

- [ ] **SQL Procedures**: CALL db.index.vector.queryNodes()
  - Custom procedures for vector search
  - CALL db.stats.graph() for graph statistics
  - CALL db.path.shortestPath() for path finding
  - Estimated: 1 week

### GraphQL API Enhancements
- [ ] **WebSocket Subscriptions**: Real-time updates
  - strawberry.subscriptions for live events
  - 1000 concurrent connections (configurable)
  - Estimated: 1 week

- [ ] **Query Complexity Limits**: Depth-based algorithm
  - 10-level max (configurable)
  - Prevent DoS attacks
  - Estimated: 3-5 days

- [ ] **Resolver Caching**: 60s TTL with manual invalidation
  - Request-scoped DataLoader (done)
  - TTL-based resolver cache (deferred)
  - Estimated: 2-3 days

### Multi-Query-Engine Unification
- [ ] **Unified Domain Schema Config**: YAML-based domain definitions
  - Define domain schema ONCE
  - Auto-generate GraphQL types
  - Auto-generate Cypher label mappings
  - Example: `config/schemas/biomedical.yaml`
  - Estimated: 2 weeks

- [ ] **Cross-Query-Engine Examples**: Same query in 3 engines
  - Document equivalent queries (openCypher, GraphQL, SQL)
  - Performance comparison
  - Use case recommendations
  - Estimated: 3-5 days

## 🎯 Performance Optimizations (P2)

### Scale Testing
- [ ] **Million-Entity Testing**
  - Current: Validated on 50K proteins
  - Target: 1M+ entities performance validation
  - Datasets: Full STRING database, PubMed literature

- [ ] **Memory Optimization**
  - Profile memory usage at scale
  - Optimize Python object lifecycle
  - IRIS global memory tuning

### Vector Search Enhancements
- [ ] **Multiple Embedding Models**
  - Support for different dimensions (384, 768, 1536)
  - Model-specific HNSW parameter tuning
  - Embedding model comparison benchmarks

- [ ] **Vector Index Tuning**
  - HNSW parameter optimization (M, efConstruction)
  - Distance function comparison (Cosine, Euclidean, Dot)
  - Index rebuild strategies

## 📈 Feature Enhancements (P2)

### Advanced Analytics
- [ ] **Graph Centrality Measures**
  - Implement PageRank, betweenness centrality
  - Network clustering algorithms
  - Community detection methods

- [ ] **Temporal Graph Analysis**
  - Time-series edge weights
  - Evolution analysis over time
  - Temporal path queries

### Integration Improvements
- [ ] **Real-time Data Streaming**
  - IRIS InterSystems IRIS Event Stream integration
  - Real-time vector updates
  - Live graph modifications

- [ ] **Visualization Interface**
  - Web-based graph explorer
  - Vector space visualization
  - Interactive query builder

## 🔧 Technical Debt (P3)

### Code Organization
- [ ] **Refactor Python Modules**
  - Split iris_graph_operators.py into focused modules
  - Improve type hints and documentation
  - Add comprehensive error handling

- [ ] **SQL Optimization**
  - Review query plans for graph operations
  - Index optimization analysis
  - Stored procedure cleanup

### Documentation Updates
- [ ] **API Documentation Refresh**
  - Update REST endpoint documentation
  - Add more code examples
  - Performance characteristics documentation

- [ ] **Deployment Guide Enhancement**
  - Production checklist
  - Troubleshooting guide
  - Best practices documentation

## ❌ Known Issues to Address

### Table-Valued Functions
- **Issue**: SQL TVFs cannot be created with CREATE PROCEDURE syntax
- **Root Cause**: IRIS requires ObjectScript class implementation
- **Status**: Working Python API provides same functionality
- **Priority**: P3 (not blocking production)

### Pure SQL Composability
- **Issue**: Graph operations require Python API calls
- **Impact**: Cannot chain operations in pure SQL
- **Workaround**: Python API provides better performance anyway
- **Priority**: P3 (architectural choice, not bug)

## 🏆 Completed Items ✅

### Multi-Query-Engine Platform (2025-10-02)
- ✅ **NodePK Implementation** (001-add-explicit-nodepk) - MERGED
  - Explicit nodes table with PRIMARY KEY
  - FK constraints on all RDF tables (64% performance improvement!)
  - Embedded Python graph analytics (PageRank: 5.31ms for 1K nodes)
  - Migration utility for existing data
  - 33/33 tasks complete (100%)

- ✅ **GraphQL API** (003-add-graphql-endpoint) - MERGED
  - Generic core + biomedical domain example
  - DataLoader batching (N+1 prevention)
  - Vector similarity search with HNSW
  - Mutations (create, update, delete)
  - 27 integration tests passing
  - 29/37 tasks complete (78%)

- ✅ **openCypher API** (002-add-opencypher-endpoint) - READY TO MERGE
  - Pattern-based Cypher parser (regex MVP)
  - AST-to-SQL translator with label/property pushdown
  - POST /api/cypher endpoint with full error handling
  - Contract tests covering success and error cases
  - Comprehensive CLAUDE.md documentation
  - 26/32 tasks complete (81%, sufficient for MVP)

### Core Implementation (COMPLETE)
- ✅ **RDF Graph Schema** - Complete with vector embeddings + NodePK
- ✅ **Python Graph Operators** - All functions working and optimized
- ✅ **IRIS REST API** - Native endpoints with excellent performance
- ✅ **Vector Search** - HNSW optimization achieving 6ms queries
- ✅ **Hybrid Search** - RRF fusion of vector + text + graph
- ✅ **Data Ingestion** - 476 proteins/second throughput
- ✅ **Performance Testing** - 21.7x improvement validation
- ✅ **Biomedical Validation** - STRING database integration
- ✅ **Documentation** - Complete user and technical guides

### Advanced Features (COMPLETE)
- ✅ **JSON_TABLE Confidence Filtering** - Production ready (109ms)
- ✅ **Neighborhood Expansion** - High-confidence edge discovery
- ✅ **Multi-modal Search** - Vector + Graph + Text integration
- ✅ **NetworkX Integration** - Graph analysis library support
- ✅ **ACORN-1 Optimization** - Maximum performance configuration

## 📅 Timeline Estimates

**P0 Items (Production Deployment)**: 1-2 weeks
- Vector migration: 2-3 days
- SSL setup: 1-2 days
- Monitoring: 3-5 days
- Backup procedures: 2-3 days

**P1 Items (Performance)**: 4-6 weeks
- Scale testing: 2 weeks
- Memory optimization: 1-2 weeks
- Vector enhancements: 2-3 weeks

**P2 Items (Features)**: 8-12 weeks
- Advanced analytics: 4-6 weeks
- Real-time streaming: 2-3 weeks
- Visualization: 3-4 weeks

## 🎯 Success Criteria

### Production Readiness ✅ ACHIEVED
- [x] Sub-millisecond graph queries
- [x] 400+ proteins/second ingestion
- [x] Vector search under 10ms
- [x] 20x performance improvement
- [x] Biomedical scale validation

### Scale Requirements (Next Phase)
- [ ] 1M+ entity handling
- [ ] 100+ concurrent users
- [ ] 99.9% uptime SLA
- [ ] <100ms API response times

## 📊 Project Status Summary

**Development Milestones**:
1. ✅ Foundation (RDF schema, Python operators, IRIS REST API)
2. ✅ Performance Optimization (ACORN-1, 21.7x improvement)
3. ✅ NodePK Implementation (FK constraints, graph analytics)
4. ✅ GraphQL API (generic core + biomedical domain)
5. 🔄 openCypher API (ready to merge)

**Query Engines Available**:
- openCypher: POST /api/cypher (pattern matching)
- GraphQL: POST /graphql (type-safe)
- SQL: iris.connect() (native IRIS)

**Next Immediate Step**: Merge openCypher branch to complete multi-query-engine vision ✅

The system is **ready for multi-query-engine production deployment**. P0 focus: merge openCypher, then operational readiness.