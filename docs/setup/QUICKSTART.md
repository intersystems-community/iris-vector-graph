# IRIS Vector Graph Quickstart Guide

## Prerequisites

- **Docker** and **Docker Compose**
- **Python 3.8+** with pip or uv
- **Git** for version control

## Quick Start (Community Edition)

1. **Clone and Setup**
```bash
git clone <repository-url>
cd iris-vector-graph
cp .env.sample .env
# Edit .env with your IRIS connection details
```

2. **Start IRIS Database**
```bash
# Start IRIS Community Edition
docker-compose up -d

# Wait for IRIS to be ready
docker-compose logs -f iris_db
```

3. **Setup Python Environment**
```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. **Initialize Database Schema**
```bash
# Run schema setup
python scripts/setup_schema.py
```

5. **Load Sample Data**
```bash
# Load biomedical sample data
python scripts/sample_data.py
```

## Production Setup (ACORN-1)

1. **Setup ACORN-1 Environment**
```bash
# Requires iris.key license file in project root
cp docker-compose.acorn.yml docker-compose.yml
docker-compose up -d
```

2. **Run Performance Tests**
```bash
# Test vector search performance
python scripts/performance/test_vector_performance.py

# Test full system performance
python scripts/performance/benchmark_suite.py
```

## Environment Management

```bash
# Start environment
docker-compose up -d

# Stop environment (keep data)
docker-compose down

# Stop and remove all data
docker-compose down -v

# View logs
docker-compose logs -f
```

## API Endpoints

### Vector Search
```bash
POST http://localhost:52773/kg/vectorSearch
{
  "vector": [0.1, 0.2, ...], // 768 dimensions
  "k": 10
}
```

### Text Search
```bash
GET http://localhost:52773/kg/search?q=cancer&n=5
```

### Graph Traversal
```bash
POST http://localhost:52773/kg/metaPath
{
  "startNode": "protein_1",
  "path": ["interacts_with", "participates_in"]
}
```

## Performance Benchmarks

| Operation | Community Edition | ACORN-1 | Improvement |
|-----------|------------------|---------|-------------|
| Data Ingestion | 29 proteins/sec | 476 proteins/sec | **21.7x** |
| Graph Query | 1.2ms avg | 0.25ms avg | **4.8x** |
| Index Build | 120s | 0.054s | **2,278x** |

## Directory Structure

```
iris-vector-graph/
├── iris_src/src/          # IRIS ObjectScript classes
├── scripts/
│   ├── setup/         # Environment setup
│   ├── performance/   # Performance testing
│   └── testing/       # Test scripts
├── docs/
│   ├── architecture/  # System architecture
│   ├── setup/         # Setup guides
│   └── performance/   # Performance analysis
└── docker-compose.*   # Docker configurations
```

## Troubleshooting

### Connection Issues
```bash
# Check IRIS status
docker ps
docker logs iris_test_graph_ai

# Test Python connectivity
python -c "import iris; print('IRIS module available')"

# Test database connection
python -c "
import iris
conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
print('✅ IRIS connection successful')
conn.close()
"
```

### Performance Issues
```bash
# Run performance diagnostics
python scripts/performance/test_vector_performance.py

# Check resource usage
docker stats iris_test_graph_ai

# Test iris_vector_graph functionality
python -c "
from iris_vector_graph.engine import IRISGraphEngine
import iris
conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
engine = IRISGraphEngine(conn)
print('✅ iris_vector_graph working')
conn.close()
"
```

### License Issues (ACORN-1)
- Ensure `iris.key` is in project root
- Verify ACORN-1 features in license
- Check IRIS logs for licensing errors

## Next Steps

1. **Explore Architecture**: Read `docs/architecture/ARCHITECTURE.md`
2. **Performance Analysis**: Review `docs/performance/`
3. **API Documentation**: See REST endpoint details
4. **Custom Data**: Modify data loading scripts for your domain