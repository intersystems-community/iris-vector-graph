# Installation Guide

Complete setup instructions for IRIS Vector Graph with multiple deployment options.

---

## Prerequisites

- **Docker** (for IRIS database)
- **Python 3.9+** (3.11 recommended)
- **Git** (for cloning repository)
- **8GB RAM minimum** (16GB recommended for large datasets)

---

## Quick Install (5 minutes)

### 1. Clone Repository

```bash
git clone https://github.com/your-org/iris-vector-graph.git
cd iris-vector-graph
```

### 2. Start IRIS Database

**Option A: Community Edition (Default)**
```bash
docker-compose up -d
```

**Option B: ACORN-1 Pre-Release (HNSW Optimization)**
```bash
docker-compose -f docker-compose.acorn.yml up -d
```

Wait ~30 seconds for IRIS to initialize.

### 3. Install Python Dependencies

**Using uv (recommended - faster)**:
```bash
# Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
source .venv/bin/activate
```

**Using pip**:
```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install package
pip install iris-vector-graph        # Core features
pip install iris-vector-graph[ml]    # + Machine learning (fraud models)
pip install iris-vector-graph[dev]   # + Development tools
```

### 4. Initialize Database Schema

**Core graph schema**:
```bash
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/schema.sql
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/operators.sql
```

**Optional: Fraud detection schema**:
```bash
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/fraud/schema.sql
```

### 5. Verify Installation

```bash
# Test database connection
python -c "import iris; conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS'); print('✓ Connected to IRIS')"

# Run quick validation tests
uv run python tests/python/run_all_tests.py --quick
```

---

## Deployment Options

### Option A: External Python (Default - Simpler)

Python application connects to IRIS via `iris.connect()`:

```
┌────────────────────────┐
│ Python Application     │
│ (your machine)         │
│                        │
│  iris.connect()   ─────┼───► IRIS Database
│  to localhost:1972     │     (Docker container)
└────────────────────────┘
```

**Pros**:
- Easier debugging
- Faster development
- Standard Python environment
- Works with any IRIS edition

**Cons**:
- Network overhead (~1-2ms per query)
- Not suitable for sub-millisecond requirements

### Option B: Embedded Python (Advanced - Faster)

Python application runs INSIDE IRIS container:

```
┌──────────────────────────────┐
│ IRIS Container               │
│ ┌──────────────────────────┐ │
│ │ Python Application       │ │
│ │ (/usr/irissys/bin/       │ │
│ │  irispython)             │ │
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │
│ │ IRIS Database Engine     │ │
│ │ (zero-copy access)       │ │
│ └──────────────────────────┘ │
└──────────────────────────────┘
```

**Pros**:
- Zero-copy data access
- Sub-millisecond performance
- Direct Global access

**Cons**:
- Requires licensed IRIS (Community Edition has limitations)
- More complex setup
- Harder to debug

**Setup**:
```bash
# Licensed IRIS with embedded Python
docker-compose -f docker-compose.fraud-embedded.yml up -d
```

---

## Port Configuration

### Default Ports (Community IRIS)

| Service | Port | Description |
|---------|------|-------------|
| IRIS SuperServer | 1972 | Database connections (`iris.connect()`) |
| IRIS Management Portal | 52773 | Web UI (http://localhost:52773/csp/sys/UtilHome.csp) |
| Demo Server | 8200 | Interactive demos (biomedical + fraud) |
| GraphQL API | 8000 | GraphQL playground and REST API |

### ACORN-1 Ports (Licensed IRIS)

| Service | Port | Description |
|---------|------|-------------|
| IRIS SuperServer | 21972 | Database connections |
| IRIS Management Portal | 252773 | Web UI |
| Embedded Fraud API | 8100 | FastAPI running inside IRIS |

**Update .env for ACORN-1**:
```bash
IRIS_HOST=localhost
IRIS_PORT=21972
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS
```

---

## Loading Sample Data

### Biomedical: STRING Protein Database

Load 10K proteins (~1 minute):
```bash
python scripts/performance/string_db_scale_test.py --max-proteins 10000
```

Load 100K proteins (~10 minutes):
```bash
python scripts/performance/string_db_scale_test.py --max-proteins 100000
```

**What you get**:
- Protein interaction networks from STRING database
- Pre-computed embeddings (768-dimensional)
- Graph relationships (binds, regulates, etc.)

### Financial: Fraud Transaction Data

**Community Edition** (30M transactions):
```bash
python scripts/fraud/load_transactions.py --count 30000000
```

**Licensed IRIS** (130M transactions):
```bash
python scripts/fraud/load_transactions.py --count 130000000
```

**What you get**:
- Synthetic fraud transaction data
- Bitemporal audit trails
- Device fingerprints and merchant networks

---

## Starting Services

### Interactive Demo Server (Biomedical + Fraud)

```bash
# Start demo server (external Python)
PYTHONPATH=src python -m iris_demo_server.app

# Open in browser
open http://localhost:8200/
```

**Available demos**:
- `/bio` - Biomedical protein network visualization
- `/fraud` - Fraud detection scoring interface

### GraphQL API Server

```bash
# Start GraphQL API (external Python)
uvicorn api.main:app --reload --port 8000

# Open GraphQL Playground
open http://localhost:8000/graphql
```

**Available endpoints**:
- `/graphql` - GraphQL Playground (interactive)
- `/api/cypher` - openCypher query endpoint
- `/health` - Health check

---

## Troubleshooting

### IRIS Password Expired

**Symptom**: `Password change required` error

**Fix**:
```bash
docker exec iris_db /usr/irissys/bin/iris session iris -U%SYS \
  "##class(Security.Users).UnExpireUserPasswords(\"*\")"
```

See [IRIS_PASSWORD_RESET.md](IRIS_PASSWORD_RESET.md) for details.

### Schema Not Found

**Symptom**: `Table 'SQLUSER.RDF_LABELS' not found`

**Fix**: Reload schema
```bash
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/schema.sql
```

### Docker Container Not Starting

**Symptom**: `docker-compose up -d` fails

**Fix**: Check logs and ports
```bash
docker-compose logs iris_db
docker ps -a  # Check if port is already in use
```

### Python Module Import Errors

**Symptom**: `ModuleNotFoundError: No module named 'iris_vector_graph'`

**Fix**: Install package in development mode
```bash
# Using uv
uv sync

# Using pip
pip install -e .
```

---

## Next Steps

1. **Run Tests**: Verify installation with `pytest tests/`
2. **Load Data**: Follow [biomedical-demo-setup.md](../biomedical-demo-setup.md) or [Bitemporal README](../../examples/bitemporal/README.md)
3. **Start Demo**: Launch demo server and explore interactive visualizations
4. **Read Docs**: Check [QUICKSTART.md](QUICKSTART.md) for usage examples

---

## Advanced Configuration

### Environment Variables

Create `.env` file in project root:

```bash
# IRIS Database Connection
IRIS_HOST=localhost
IRIS_PORT=1972
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS

# API Configuration
CORS_ORIGINS=*
DEBUG=false

# Demo Mode (fallback when API unavailable)
DEMO_MODE=false
FRAUD_API_URL=http://localhost:8100
```

### Custom IRIS Configuration

Edit `docker-compose.yml` to customize IRIS settings:

```yaml
services:
  iris:
    image: intersystemsdc/iris-community:latest
    environment:
      - ISC_CPF_MERGE_FILE=/merge.cpf
    volumes:
      - ./docker/iris-custom.cpf:/merge.cpf
    ports:
      - "1972:1972"   # SuperServer
      - "52773:52773" # Management Portal
```

### HNSW Vector Index Optimization

Requires IRIS 2025.3+ or ACORN-1 pre-release:

```sql
-- Enable HNSW index (100x faster vector search)
CREATE INDEX IF NOT EXISTS idx_node_embeddings_hnsw
ON kg_NodeEmbeddings(emb)
USING VECTOR(TYPE=HNSW, M=16, EF_CONSTRUCTION=200);
```

See [ACORN-1 Performance Comparison](../performance/ACORN-1_vs_Community_Performance_Comparison.md) for benchmarks.

---

## Uninstall

```bash
# Stop and remove containers
docker-compose down -v

# Remove virtual environment
rm -rf .venv

# Remove downloaded data (optional)
rm -rf data/
```

---

## Support

- **Documentation**: [docs/](../)
- **Issues**: [GitHub Issues](https://github.com/your-org/iris-vector-graph/issues)
- **Community**: [InterSystems Developer Community](https://community.intersystems.com/)
