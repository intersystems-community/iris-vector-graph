# IRIS Vector Graph - Project Status

**Last Updated**: 2026-01-25

## Overall Status: PRODUCTION READY

The IRIS Vector Graph system is a fully operational **multi-query-engine platform** supporting openCypher, GraphQL, and SQL queries over a unified graph database with HNSW-optimized vector search.

## Key Performance Metrics

| Metric | Performance | Notes |
|--------|-------------|-------|
| Vector Search | **1.7ms** | HNSW with ACORN-1 (vs 5.8s fallback) |
| Graph Queries | **0.25ms** | Sub-millisecond traversal |
| Data Ingestion | **476 nodes/sec** | Parallel processing |
| E2E Tests | **59 passing** | Full coverage |

## Query Engines

| Engine | Endpoint | Status |
|--------|----------|--------|
| **openCypher** | `POST /api/cypher` | Production |
| **GraphQL** | `POST /graphql` | Production |
| **SQL** | `iris.connect()` | Production |

## Core Components

### Production Ready
- **GraphQL API** - Strawberry-based with DataLoader batching, vector similarity
- **openCypher API** - Cypher-to-SQL translation with label/property pushdown
- **NodePK Schema** - Foreign key constraints ensuring referential integrity
- **HNSW Vector Index** - 768-dimensional embeddings with ACORN-1 optimization
- **Hybrid Search** - RRF fusion of vector + text + graph signals
- **Demo Applications** - Biomedical and Fraud detection interactive demos

### Infrastructure
- **Docker** - `docker-compose.yml` for standard IRIS, `docker-compose.acorn.yml` for ACORN-1
- **Tests** - Contract, integration, E2E, and UI tests
- **CI/CD** - pytest with iris-devtester for database lifecycle

## Recent Changes (2026-01-25)

- Fixed connection exhaustion in GraphQL API (DatabaseConnectionExtension)
- All 59 E2E + GraphQL integration tests passing
- Merged `008-demo-ux-e2e-tests` branch to main

## Known Limitations

- **IRIS CE License Limit**: 5 concurrent connections (handled by connection extension)
- **Cypher Parser**: Pattern-based MVP (full libcypher-parser planned)
- **iFind Index**: Must be created via Management Portal (not SQL DDL)

## File Structure

```
api/                    # FastAPI endpoints (GraphQL, openCypher)
iris_vector_graph/      # Core Python module
iris_src/src/           # ObjectScript classes
sql/                    # Schema and stored procedures
tests/                  # Test suites (contract, e2e, integration, unit)
examples/domains/       # Example domain implementations
docs/                   # Documentation
```

## Quick Start

```bash
uv sync
docker-compose up -d
uvicorn api.main:app --reload --port 8000
# Visit http://localhost:8000/graphql
```
