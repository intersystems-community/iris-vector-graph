# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup
cp .env.sample .env  # Edit connection details for IRIS
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Development
python scripts/setup_schema.py           # Initialize database schema
python scripts/sample_data.py            # Load sample data
python tests/python/run_all_tests.py     # Run test suite
```

## IRIS Database Setup

### Option 1: Docker (Recommended)

Run IRIS in Docker using the community edition:

```bash
# Create docker-compose.yml for IRIS
cat > docker-compose.yml << 'EOF'
services:
  iris_db:
    image: intersystemsdc/iris-community:latest
    container_name: iris_db_iris_vector_graph
    ports:
      - "1972:1972"   # IRIS SuperServer port (host:container)
      - "52773:52773" # IRIS Management Portal (host:container)
    environment:
      - IRISNAMESPACE=USER
      - ISC_DEFAULT_PASSWORD=SYS
    volumes:
      - iris_db_data:/usr/irissys/mgr # Named volume for IRIS data persistence
      - .:/home/irisowner/dev # Mount project directory
    stdin_open: true # Keep container running
    tty: true        # Keep container running
    healthcheck:
      test: ["CMD", "/usr/irissys/bin/iris", "session", "iris", "-U%SYS", "##class(%SYSTEM.Process).CurrentDirectory()"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 60s
    command: --check-caps false -a "iris session iris -U%SYS '##class(Security.Users).UnExpireUserPasswords(\"*\")'"

volumes:
  iris_db_data: {} # Defines the named volume for IRIS data
EOF

# Start IRIS
docker-compose up -d

# Configure ODBC DSN or use connection string in .env:
# IRIS_DSN=localhost:1972/USER
# IRIS_USER=_SYSTEM
# IRIS_PASS=SYS
```

Access IRIS Management Portal: http://localhost:52773/csp/sys/UtilHome.csp

### Option 2: Manual IRIS Installation

Requires **InterSystems IRIS 2025.1+** with Vector Search (HNSW) feature:

```sql
-- Run in IRIS SQL tool
\i sql/schema.sql
\i sql/operators.sql
-- Optional sample data:
\i scripts/sample_data.sql
```

## Architecture Overview

This is a **Graph + Vector Retrieval** system targeting **InterSystems IRIS** that combines:

- **Vector search (HNSW)** + **lexical search** + **graph constraints**
- **IRIS-native Python** integration with embedded operations
- **REST API** via IRIS ObjectScript classes
- **iris_vector_graph_core** Python module for high-performance operations
- **Direct iris.connect()** for optimal performance

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
- `IRIS_PORT` - IRIS SuperServer port (default: 1973)
- `IRIS_NAMESPACE` - IRIS namespace (default: USER)
- `IRIS_USER` - Database username (default: _SYSTEM)
- `IRIS_PASSWORD` - Database password (default: SYS)

### Key Technical Notes

- **Vector embeddings**: Configured for 768-dimensional vectors (biomedical embeddings). HNSW optimization provides ~100x performance improvement.
- **HNSW Index**: Optimized with ACORN=1 for production performance (1.7ms vs 5800ms baseline).
- **RRF Fusion**: Uses Reciprocal Rank Fusion (Cormack & Clarke SIGIR'09) to combine vector and text search results.
- **Graph queries**: Performance-optimized with bounded hops and confidence filtering.
- **iris_vector_graph_core**: Modular design for integration with other RAG systems.

### File Structure

- `iris_vector_graph_core/` - Core Python module for graph operations
- `biomedical/` - Domain-specific biomedical graph operations
- `sql/` - Database schema and stored procedures
- `iris/src/` - IRIS ObjectScript classes for REST API
- `scripts/` - Setup, testing, and performance scripts
- `tests/` - Comprehensive test suite
- `docs/` - Documentation and performance analysis