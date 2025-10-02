# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Environment Setup
```bash
# Initial setup (using uv for modern Python package management)
uv sync                      # Install dependencies via pyproject.toml
source .venv/bin/activate    # Activate virtual environment

# Alternative (if using requirements.txt)
cp .env.sample .env          # Edit connection details for IRIS
uv venv
uv pip install -r requirements.txt
```

### Database Setup

**Option 1: Docker (Recommended)**

```bash
# Standard IRIS Community Edition
docker-compose up -d

# ACORN-1 (pre-release build with HNSW optimization - fastest)
docker-compose -f docker-compose.acorn.yml up -d
```

**Option 2: Manual IRIS Installation**

Requires **InterSystems IRIS 2025.1+** with Vector Search (HNSW) feature:

```sql
-- Run in IRIS SQL tool
\i sql/schema.sql
\i sql/operators.sql       # Use operators_fixed.sql if this fails
\i scripts/sample_data.sql # Optional sample data
```

Access IRIS Management Portal: http://localhost:52773/csp/sys/UtilHome.csp (or port 252773 for ACORN-1)

### Testing
```bash
# Run test suite
uv run python tests/python/run_all_tests.py       # All tests
uv run python tests/python/run_all_tests.py --quick  # Quick validation

# Direct pytest execution
pytest tests/                                      # All tests
pytest tests/unit/                                 # Unit tests only
pytest tests/integration/                          # Integration tests only
pytest -m requires_database                        # Database-dependent tests
pytest --cov=iris_vector_graph_core               # With coverage
```

### Development Operations
```bash
# Schema and data management
python scripts/setup_schema.py                     # Initialize database schema
python scripts/sample_data.py                      # Load sample data

# Performance testing
uv run python scripts/performance/test_vector_performance.py
uv run python scripts/performance/string_db_scale_test.py --max-proteins 10000
```

### openCypher API Server
```bash
# Start openCypher API server (ASGI)
uvicorn api.main:app --reload --port 8000

# Alternative: Direct execution
uv run uvicorn api.main:app --reload --port 8000

# Access API documentation
open http://localhost:8000/docs  # Swagger UI
open http://localhost:8000       # API info

# Health check
curl http://localhost:8000/health

# Execute Cypher query
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein {id: \"PROTEIN:TP53\"}) RETURN p.name",
    "timeout": 30
  }'

# Parameterized Cypher query
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein) WHERE p.id = $proteinId RETURN p.name",
    "parameters": {"proteinId": "PROTEIN:TP53"}
  }'
```

### Linting and Formatting
```bash
# Format code (per pyproject.toml configuration)
black .
isort .

# Lint code
flake8 .
mypy iris_vector_graph_core/
```

## Architecture Overview

This is a **Graph + Vector Retrieval** system targeting **InterSystems IRIS** that combines:

- **Vector search (HNSW)** + **lexical search** + **graph constraints**
- **openCypher query endpoint** for graph pattern matching
- **IRIS-native Python** integration with embedded operations
- **REST API** via FastAPI (openCypher) and IRIS ObjectScript classes
- **iris_vector_graph_core** Python module for high-performance operations
- **Direct iris.connect()** for optimal performance

### Query Engines

The system supports multiple query interfaces on the same NodePK schema:

1. **openCypher API** (`/api/cypher`) - Graph pattern matching with Cypher syntax
   - Parser: Pattern-based MVP parser (regex-based for common queries)
   - Translator: AST-to-SQL with label/property pushdown optimizations
   - Endpoint: FastAPI async endpoint at http://localhost:8000/api/cypher

2. **SQL Direct** - Native IRIS SQL for maximum control
   - Direct access to `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`
   - Full IRIS SQL capabilities including VECTOR functions

3. **GraphQL API** (on main branch) - Type-safe graph queries
   - Generic core + domain-specific types (Protein, Gene, Pathway as examples)
   - DataLoader batching for N+1 prevention
   - Vector similarity via `similar()` field resolver

### Core Components

1. **SQL Layer** (`sql/`):
   - `schema.sql` - RDF-ish tables (`rdf_labels`, `rdf_props`, `rdf_edges`) + vector embeddings table with HNSW index
   - `operators.sql` - Stored procedures: `kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE`, `kg_GRAPH_PATH`

2. **Python SDK** (`python/`, `iris_vector_graph_core/`):
   - `IRISGraphEngine` - Core graph operations and vector search
   - `HybridSearchFusion` - RRF fusion of vector + text results
   - `TextSearchEngine` - IRIS iFind integration
   - `VectorOptimizer` - HNSW optimization utilities

3. **IRIS REST API** (`iris/src/`):
   - `GraphAPI.cls` - REST endpoints for graph operations
   - `VectorSearch.cls` - Vector similarity search endpoints
   - `HybridSearch.cls` - Multi-modal search endpoints

4. **Performance Testing** (`scripts/performance/`):
   - `test_vector_performance.py` - Vector search benchmarks
   - `string_db_scale_test.py` - Large-scale biomedical testing
   - `benchmark_suite.py` - Comprehensive performance analysis

### Environment Configuration

Configure IRIS connection in `.env`:
- `IRIS_HOST` - IRIS server hostname (default: localhost)
- `IRIS_PORT` - IRIS SuperServer port (default: 1972 or 21972 for ACORN-1)
- `IRIS_NAMESPACE` - IRIS namespace (default: USER)
- `IRIS_USER` - Database username (default: _SYSTEM)
- `IRIS_PASSWORD` - Database password (default: SYS)

### IRIS Docker Port Mapping Strategy

**Standardized Port Ranges** (per constitution):
- **Default IRIS**: `1972:1972` and `52773:52773` (docker-compose.yml)
- **Licensed IRIS (ACORN-1)**: `21972:1972` and `252773:52773` (docker-compose.acorn.yml)
- **Development instances**: `11972:1972` and `152773:52773` (if needed for multiple instances)

**Rationale**: Predictable ports avoid conflicts, enable easy configuration, support multiple IRIS instances

### Key Technical Notes

- **Vector embeddings**: Configured for 768-dimensional vectors (biomedical embeddings). HNSW optimization provides ~100x performance improvement.
- **HNSW Index**: Optimized with ACORN=1 for production performance (1.7ms vs 5800ms baseline).
- **RRF Fusion**: Uses Reciprocal Rank Fusion (Cormack & Clarke SIGIR'09) to combine vector and text search results.
- **Graph queries**: Performance-optimized with bounded hops and confidence filtering.
- **iris_vector_graph_core**: Modular design for integration with other RAG systems.
- **Cypher-to-SQL Translation**: Label pushdown and property pushdown optimizations for fast queries.
- **Query Pattern Matching**: MVP parser supports common Cypher patterns (MATCH, WHERE, RETURN, ORDER BY, LIMIT).

### File Structure

- `iris_vector_graph_core/` - Core Python module for graph operations
- `biomedical/` - Domain-specific biomedical graph operations
- `sql/` - Database schema and stored procedures
- `iris/src/` - IRIS ObjectScript classes for REST API
- `scripts/` - Setup, testing, and performance scripts
- `tests/` - Comprehensive test suite
- `docs/` - Documentation and performance analysis

## Constitutional Requirements

When developing, ensure compliance with `.specify/memory/constitution.md`:

### Core Principles Summary
1. **IRIS-Native Development** - Leverage IRIS capabilities directly (embedded Python, SQL procedures, ObjectScript)
2. **Test-First with Live Database** - TDD with running IRIS instance (no mocked database for integration tests)
3. **Performance as a Feature** - HNSW indexing, bounded queries, tracked benchmarks
4. **Hybrid Search by Default** - Vector + text + graph using RRF fusion
5. **Observability & Debuggability** - Structured logging at each layer
6. **Modular Core Library** - Database-agnostic iris_vector_graph_core
7. **Explicit Error Handling** - No silent failures, actionable error messages
8. **Standardized Database Interfaces** - Use proven utilities, contribute patterns back

### Testing Requirements (NON-NEGOTIABLE)
- All tests involving data storage, vector operations, or graph operations MUST use live IRIS
- Test categories:
  - `@pytest.mark.requires_database` - MUST connect to live IRIS
  - `@pytest.mark.integration` - MUST use IRIS for data operations
  - `@pytest.mark.e2e` - MUST use complete IRIS + vector workflow
  - Unit tests MAY mock IRIS for isolated component testing
- Performance tests MUST verify: vector search <10ms, graph queries <1ms (with HNSW)

### Development Standards
- **Package Management**: Use `uv` for all Python dependency management
- **Code Quality**: Pass black, isort, flake8, mypy before commits
- **Documentation**: Comprehensive docstrings for all public APIs
- **Versioning**: Follow semantic versioning for schema/API changes

### AI Development Constraints
- Follow constraint-based architecture, not "vibecoding"
- Constitutional validation gates prevent repeating known bugs
- Every bug fix MUST be captured as new validation rule or enhanced guideline
- Work within established frameworks, patterns, and validation loops
- **Constraint Philosophy**: Less freedom = less chaos. Constraints prevent regression.

## Integration with RAG Systems

The `iris_vector_graph_core` module is designed for integration with RAG frameworks like `rag-templates`:

```python
# Example usage in RAG pipeline
from iris_vector_graph_core import HybridSearchFusion, IRISGraphEngine

# Initialize engine with IRIS connection
engine = IRISGraphEngine(connection_params)

# Hybrid search combining vector, text, and graph
results = engine.hybrid_search(
    query_vector=embeddings,
    query_text="cancer pathway",
    k=15,
    use_rrf=True
)
```

See `docs/architecture/ACTUAL_SCHEMA.md` for working patterns and integration examples.
