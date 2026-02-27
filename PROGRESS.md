# IRIS Vector Graph - Development Progress

**Last Updated**: 2026-02-27

## Project Evolution

Research Prototype → Production Platform → Multi-Query-Engine System

## Completed Phases

### Phase 7: Stored Procedure Installation Fix (Feature 020)
**Goal**: Fix `initialize_schema()` to correctly install server-side stored procedures

- **Fixed SyntaxError** in `schema.py` (dead code block lines 436–520 removed)
- **Fixed `get_procedures_sql_list()`**: accepts `embedding_dimension` parameter; removed hardcoded dimension
- **Fixed procedure DDL syntax**: `CREATE OR REPLACE PROCEDURE ... LANGUAGE SQL BEGIN SELECT ...; END` (correct IRIS syntax — no `RETURN`, no `DECLARE VECTOR`, no `RETURNS TABLE`)
- **Fixed parameter syntax**: colon-prefix (`:param`) for procedure body parameter references in IRIS SQL
- **Fixed row limiting**: `TOP :k` (not `FETCH FIRST :k`) for variable-limit queries
- **Added RuntimeError** in `initialize_schema()` for unexpected DDL failures (optional procedures `kg_TXT`/`kg_RRF_FUSE` are non-fatal)
- **Integration tests**: 11/12 pass; 1 xfailed (known IRIS dbapi limitation: schema-qualified `CALL schema.proc(?, ...)` not supported — procedure IS installed correctly)
- **Known limitation**: IRIS Python dbapi 5.x does not support `CALL iris_vector_graph.proc(?, ...)` with `?` params; direct call workaround pending dbapi fix

### Phase 1: Foundation
**Goal**: Core IRIS integration with graph operations

- IRIS schema with RDF-style tables (nodes, rdf_edges, rdf_labels, rdf_props)
- Basic SQL operators for graph traversal
- Python-IRIS integration via embedded Python
- Vector embedding storage (768-dimensional)
- Docker environment setup

### Phase 2: Performance Optimization
**Goal**: Production-level performance

- **21.7x overall improvement** with ACORN-1
- HNSW vector index (1.7ms queries vs 5.8s fallback)
- Parallel data ingestion (476 nodes/sec)
- Sub-millisecond graph queries (0.25ms avg)

### Phase 3: NodePK & Referential Integrity
**Goal**: Data consistency with foreign key constraints

- Explicit `nodes` table with PRIMARY KEY
- FK constraints on all RDF tables
- **64% query performance improvement** from constraint optimization
- Migration utility for existing data
- Embedded Python graph analytics (PageRank: 5.31ms for 1K nodes)

### Phase 4: GraphQL API
**Goal**: Type-safe graph queries

- Strawberry GraphQL with FastAPI
- Generic core + domain-specific types
- DataLoader batching (N+1 prevention)
- Vector similarity via `similar()` resolver
- CRUD mutations with validation

### Phase 5: openCypher API
**Goal**: Pattern matching queries

- Cypher-to-SQL translation
- Label and property pushdown optimization
- AST-based query representation
- Contract tests for API behavior

### Phase 6: Demo Applications & E2E Tests
**Goal**: Showcase and validate the platform

- Biomedical research demo (protein interactions)
- Fraud detection demo (transaction networks)
- Interactive UI with D3.js visualization
- Comprehensive E2E test suite (38 tests)
- Connection management for IRIS CE license limits

## Current State

### Production Ready
| Component | Status |
|-----------|--------|
| GraphQL API | Production |
| openCypher API | Production |
| SQL Direct | Production |
| HNSW Vector Search | Production |
| Hybrid Search (RRF) | Production |
| Demo Applications | Production |

### Test Coverage
| Suite | Tests | Status |
|-------|-------|--------|
| E2E | 38 | Passing |
| GraphQL Integration | 21 | Passing |
| Contract | 48 | Passing |
| Total | 303 | Collected |

### Performance Benchmarks
| Operation | Latency |
|-----------|---------|
| Vector Search (HNSW) | 1.7ms |
| Graph Query | 0.25ms |
| Hybrid Search | <100ms |
| Data Ingestion | 476 nodes/sec |

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Query Engines                                  │
│  - GraphQL: /graphql (Strawberry + FastAPI)     │
│  - openCypher: /api/cypher (Cypher-to-SQL)      │
│  - SQL: iris.connect() (native)                 │
└─────────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────────┐
│  Core Python Module (iris_vector_graph/)        │
│  - Engine, Fusion, Vector Utils                 │
│  - Cypher Parser & Translator                   │
└─────────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────────┐
│  IRIS Database                                  │
│  - nodes (PRIMARY KEY)                          │
│  - rdf_edges, rdf_labels, rdf_props (FK)        │
│  - kg_NodeEmbeddings (HNSW indexed)             │
└─────────────────────────────────────────────────┘
```

## Metrics

- **Development**: ~6 months
- **Python**: ~3,000 lines
- **SQL**: ~800 lines
- **ObjectScript**: ~500 lines
- **Tests**: ~2,000 lines
- **Documentation**: ~5,000 lines
