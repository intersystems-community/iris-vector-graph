# IRIS Vector Graph

A knowledge graph system built on InterSystems IRIS that combines graph traversal, vector similarity search, and full-text search in a single database.

> **NEW**: [Interactive Demo Server](src/iris_demo_server/) showcasing fraud detection + biomedical capabilities

**Proven at Scale Across Industries**:
- **Financial Services**: Real-time fraud detection (130M+ transactions), bitemporal audit trails, <10ms queries
- **Biomedical Research**: Protein interaction networks (100K+ proteins), drug discovery, <50ms multi-hop queries

Same IRIS platform. Different domains. Powerful results.

---

## Table of Contents

- [Quick Start](#quick-start)
  - [Option A: Fraud Detection (Financial Services)](#option-a-fraud-detection-financial-services)
  - [Option B: Biomedical Graph (Life Sciences)](#option-b-biomedical-graph-life-sciences)
- [Use Cases by Industry](#use-cases-by-industry)
- [Architecture](#architecture)
- [Key Features](#key-features)
- [Performance](#performance)
- [Documentation](#documentation)

---

## Quick Start

**Two Deployment Modes**:
1. **External** (DEFAULT - simpler): Python app connects to IRIS via `iris.connect()`
2. **Embedded** (ADVANCED - optional): Python app runs INSIDE IRIS container

### Option A: Fraud Detection (Financial Services)

#### External Mode (Default - Simpler)

```bash
# 1. Start IRIS database (Community Edition with fraud schema)
docker-compose -f docker-compose.fraud-community.yml up -d

# 2. Install Python dependencies
pip install iris-vector-graph        # Core features
pip install iris-vector-graph[ml]    # + Machine learning (fraud scoring models)

# 3. Wait for IRIS startup (~30 seconds), then connect external fraud API
PYTHONPATH=src IRIS_PORT=51972 python -m iris_fraud_server

# Test fraud scoring API (external server on :8000)
curl -X POST http://localhost:8000/fraud/score \
  -H 'Content-Type: application/json' \
  -d '{"mode":"MLP","payer":"acct:test","device":"dev:laptop","amount":1000.0}'
```

#### Embedded Mode (Advanced - Optional)

```bash
# Run FastAPI INSIDE IRIS container (licensed IRIS required)
docker-compose -f docker-compose.fraud-embedded.yml up -d

# Test fraud scoring API (~2 min startup)
curl -X POST http://localhost:8100/fraud/score \
  -H 'Content-Type: application/json' \
  -d '{"mode":"MLP","payer":"acct:test","device":"dev:laptop","amount":1000.0}'
```

**What you get**:
- FastAPI fraud scoring (external `:8000` or embedded `:8100`)
- Bitemporal data (track when transactions happened vs. when you learned about them)
- Complete audit trails (regulatory compliance: SOX, MiFID II)
- Direct IRIS queries (no middleware)

**Learn more**: [`examples/bitemporal/README.md`](examples/bitemporal/README.md) - Fraud scenarios, chargeback defense, model tracking

---

### Option B: Biomedical Graph (Life Sciences)

#### External Mode (Default - Simpler)

```bash
# Prerequisites: Running IRIS instance (any docker-compose or external IRIS)
# Default connection: localhost:1972, namespace USER, user _SYSTEM, password SYS

# 1. Install dependencies (using uv package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync && source .venv/bin/activate

# 2. Load STRING protein database (1K proteins for quick demo, ~30 seconds)
IRIS_PORT=1972 python scripts/performance/string_db_scale_test.py --max-proteins 1000

# 3. Start interactive demo server (external Python)
PYTHONPATH=src IRIS_PORT=1972 python -m iris_demo_server.app

# 4. Open browser
open http://localhost:8200/bio
```

**Configuration**: Use `IRIS_PORT`, `IRIS_HOST`, `IRIS_NAMESPACE`, `IRIS_USER`, and `IRIS_PASSWORD` environment variables to connect to your IRIS instance.

#### Embedded Mode (Advanced - Optional)

```bash
# Run demo server INSIDE IRIS container (licensed IRIS required)
# Coming soon - currently only external mode supported for biomedical demo
```

**What you get**:
- **Interactive protein search** with vector similarity (EGFR, TP53, etc.)
- **D3.js graph visualization** with click-to-expand nodes showing interaction networks
- **Pathway analysis** between proteins using BFS graph traversal
- **Real STRING DB data** (10K proteins, 37K interactions)
- **<100ms queries** powered by direct IRIS integration (no API middleware)
- **20/20 contract tests passing** - production-ready biomedical demo

**Learn more**:
- [`docs/biomedical-demo-setup.md`](docs/biomedical-demo-setup.md) - Complete setup guide with scaling options
- [`biomedical/README.md`](biomedical/README.md) - Architecture and development patterns

---

## Use Cases by Industry

### Financial Services (IDFS)

| Use Case | Features | Performance |
|----------|----------|-------------|
| **Real-Time Fraud Detection** | Graph-based scoring, MLP models, device fingerprinting | <10ms scoring, 130M+ transactions |
| **Bitemporal Audit Trails** | Valid time vs. system time, chargeback defense, compliance | <10ms time-travel queries |
| **Late Arrival Detection** | Settlement delay analysis, backdated transaction flagging | Pattern detection across 130M events |
| **Regulatory Compliance** | SOX, GDPR, MiFID II, Basel III reporting | Complete audit trail preservation |

**Files**:
- `examples/bitemporal/` - Fraud scenarios, audit queries, Python API
- `sql/bitemporal/` - Schema (2 tables, 3 views, 8 indexes)
- `src/iris_fraud_server/` - FastAPI fraud scoring server
- `docker-compose.fraud-embedded.yml` - Licensed IRIS + embedded Python

**Quick Links**:
- [Bitemporal Fraud Detection README](examples/bitemporal/README.md)
- [Fraud API Documentation](src/iris_fraud_server/README.md)

---

### Biomedical Research

| Use Case | Features | Performance |
|----------|----------|-------------|
| **Protein Interaction Networks** | STRING DB integration, pathway analysis, vector similarity | <50ms multi-hop queries (100K+ proteins) |
| **Drug Discovery** | Compound similarity, target identification, graph analytics | <10ms vector search (HNSW) |
| **Literature Mining** | Hybrid search (embeddings + BM25), entity extraction | RRF fusion, sub-second queries |
| **Pathway Analysis** | Multi-hop traversal, PageRank, connected components | NetworkX integration, embedded Python |

**Files**:
- `biomedical/` - Protein queries, pathway examples
- `sql/schema.sql` - Graph schema (nodes, edges, properties, embeddings)
- `iris_vector_graph/` - Core Python graph engine
- `docker-compose.acorn.yml` - ACORN-1 with HNSW optimization

**Quick Links**:
- [Biomedical Examples](biomedical/README.md)
- [STRING DB Integration](docs/setup/STRING_DB.md)

---

### Graph Algorithms (TSP Examples)

Two standalone implementations of the **Traveling Salesman Problem** demonstrating graph algorithms on IRIS:

#### Option A: Python + NetworkX (Biomedical)

Find optimal pathways through protein interaction networks:

```bash
# Test with 10 cancer-related proteins
python scripts/algorithms/tsp_demo.py --proteins 10 --compare-methods
```

**Algorithms**: Greedy (1ms), Christofides (15ms), 2-opt (8ms)
**Use case**: Optimize order to study protein interactions in cancer pathways

#### Option B: ObjectScript (Healthcare Interoperability)

Optimize caregiver routes for home healthcare:

```bash
# Load sample data (8 patients, 26 travel edges)
docker exec -i iris /usr/irissys/bin/irissession IRIS -U USER < sql/caregiver_routing_demo.sql

# Run optimization demo (IRIS Terminal)
Do ^TestCaregiverRouter
```

**Performance**: <2ms for 8-patient routes
**Integration**: Direct Business Process method calls
**Impact**: 53% travel time reduction (75min → 35min)

**What you get**:
- **Python approach**: NetworkX integration, multiple algorithms, FastAPI endpoint example
- **ObjectScript approach**: Zero dependencies, Interoperability production integration, bitemporal audit
- **Comprehensive docs**: Neo4j comparison, performance benchmarks, real-world use cases

**Files**:
- `scripts/algorithms/tsp_demo.py` - Python demo (works with STRING protein data)
- `iris/src/Graph/CaregiverRouter.cls` - ObjectScript TSP optimizer
- `iris/src/Graph/ScheduleOptimizationProcess.cls` - Business Process integration
- `sql/caregiver_routing_demo.sql` - Sample healthcare data

**Learn more**:
- [`docs/algorithms/TSP_ANALYSIS.md`](docs/algorithms/TSP_ANALYSIS.md) - Deep dive and Neo4j comparison
- [`docs/algorithms/TSP_IMPLEMENTATION_SUMMARY.md`](docs/algorithms/TSP_IMPLEMENTATION_SUMMARY.md) - Overview and benchmarks
- [`docs/examples/CAREGIVER_ROUTING_DEMO.md`](docs/examples/CAREGIVER_ROUTING_DEMO.md) - Step-by-step tutorial

---

## Architecture

**Deployment Options**:
- **External (Default)**: Python app connects to IRIS via `iris.connect()` - simpler setup, easier debugging
- **Embedded (Advanced)**: Python app runs inside IRIS container - maximum performance, requires licensed IRIS

```
External Deployment (DEFAULT)        Embedded Deployment (OPTIONAL)
┌────────────────────────┐          ┌──────────────────────────────┐
│ FastAPI Server         │          │ IRIS Container               │
│ (external Python)      │          │ ┌──────────────────────────┐ │
│                        │          │ │ FastAPI Server           │ │
│  iris.connect()   ─────┼──────────┤►│ (/usr/irissys/bin/       │ │
│  to localhost:1972     │          │ │  irispython)             │ │
└────────────────────────┘          │ └──────────────────────────┘ │
                                    │ ┌──────────────────────────┐ │
                                    │ │ IRIS Database Engine     │ │
                                    │ │ (Bitemporal/Graph/Vector)│ │
                                    │ └──────────────────────────┘ │
                                    └──────────────────────────────┘

         Same Platform: InterSystems IRIS
         Same Features: Vector Search, Graph Traversal, Bitemporal Audit
         Different Domains: Finance vs. Life Sciences
```

**Core Components**:
- **IRIS Globals**: Append-only storage (perfect for audit trails + graph data)
- **Embedded Python**: Run ML models and graph algorithms in-database
- **SQL Procedures**: `kg_KNN_VEC` (vector search), `kg_RRF_FUSE` (hybrid search)
- **HNSW Indexing**: 100x faster vector similarity (requires IRIS 2025.3+ or ACORN-1)

---

## Key Features

### Cross-Domain Capabilities

| Feature | Financial Services Use | Biomedical Use |
|---------|------------------------|----------------|
| **Embedded Python** | Fraud ML models in-database | Graph analytics (PageRank, etc.) |
| **Personalized PageRank** | Entity importance scoring | Document ranking, pathway analysis |
| **Temporal Queries** | Bitemporal audit ("what did we know when?") | Time-series biomarker analysis |
| **Graph Traversal** | Fraud ring detection (multi-hop) | Protein interaction pathways |
| **Vector Search** | Transaction similarity | Protein/compound similarity |
| **Partial Indexes** | `WHERE system_to IS NULL` (10x faster) | `WHERE label = 'protein'` |

### IRIS-Native Optimizations

- **Globals Storage**: Append-only (no UPDATE contention)
- **Partial Indexes**: Filter at index level (`WHERE system_to IS NULL`)
- **Temporal Views**: Pre-filter current versions
- **Foreign Key Constraints**: Referential integrity across graph
- **HNSW Vector Index**: 100x faster than flat search (ACORN-1)
- **PPR Functional Index**: ObjectScript adjacency maintenance with real-time graph synchronization (see [PPR Performance](#personalized-pagerank-ppr) section)

---

## Performance

### Financial Services (Fraud Detection)

| Metric | Community IRIS | Licensed IRIS |
|--------|---------------|---------------|
| **Transactions** | 30M | 130M |
| **Database Size** | 5.3GB | 22.1GB |
| **Fraud Scoring** | <10ms | <10ms |
| **Bitemporal Queries** | <10ms (indexed) | <10ms (indexed) |
| **Time-Travel Queries** | <50ms | <50ms |
| **Late Arrival Detection** | Pattern search across 30M | Pattern search across 130M |

### Biomedical (Protein Networks)

| Metric | Pure Python | ObjectScript Native |
|--------|------------|---------------------|
| **Vector Search** | 5800ms (flat) → 1.7ms (HNSW) | Same (HNSW index) |
| **Multi-hop Queries** | <50ms | <50ms |
| **Hybrid Search (RRF)** | <100ms | <20ms |
| **Personalized PageRank (1K)** | 14.5ms | 14.3ms |
| **Personalized PageRank (10K)** | **1,631ms** | **184ms (8.9x faster)** ✨ |
| **Graph Analytics** | NetworkX integration | Zero-copy Global access |

**Tested At Scale**:
- ✅ 130M fraud transactions (licensed IRIS)
- ✅ 100K+ protein interactions (STRING DB)
- ✅ 768-dimensional embeddings (biomedical models)

---

## Usage Examples

### Personalized PageRank (PPR)

Compute entity importance scores for knowledge graph ranking:

```python
from iris_vector_graph import IRISGraphEngine
import iris

# Connect to IRIS
conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)

# Compute PPR scores from seed entity
scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["PROTEIN:TP53"],  # Seed with cancer protein
    damping_factor=0.85,              # Standard PageRank parameter
    top_k=20                          # Return top 20 scored entities
)

# Results: {'PROTEIN:TP53': 0.152, 'PROTEIN:MDM2': 0.087, ...}

# Rank documents by PPR scores
docs = engine.kg_PPR_RANK_DOCUMENTS(
    seed_entities=["PROTEIN:TP53"],
    top_k=10
)

# Results: [{document_id, score, top_entities, entity_count}, ...]
```

**Performance**:
- **Pure Python**: <25ms (1K nodes), ~200ms (10K nodes)
- **PPR Functional Index**: Real-time adjacency list maintenance synchronized with SQL DML
- **Implementation Status**: Production-ready with 12/12 integration tests passing

**Architecture Notes**:
- **Pure Python** (current): SQL extraction + in-memory computation - best for current scale
- **Functional Index**: ObjectScript-based adjacency maintenance (deployed, works correctly)
- **Future**: Native ObjectScript PPR for sub-millisecond computation at 100K+ scale

See [PPR Optimization Documentation](#ppr-performance--implementation) for detailed performance analysis and implementation journey.

---

## Documentation

### 📚 Getting Started

**Quick Starts**:
- [Fraud Detection (Financial Services)](examples/bitemporal/README.md) - Real-time fraud scoring, bitemporal audit trails
- [Biomedical Graph Setup](docs/biomedical-demo-setup.md) - STRING protein data loading, interactive demo
- [Installation Guide](docs/setup/INSTALLATION.md) - Docker setup, Python environment, database initialization
- [Quick Start Guide](docs/setup/QUICKSTART.md) - Get running in 5 minutes

**Setup Guides**:
- [UV Package Manager Setup](docs/setup/UV_SETUP.md) - Modern Python dependency management
- [IRIS Password Reset](docs/setup/IRIS_PASSWORD_RESET.md) - Fix expired password issues
- [Data Formats](docs/setup/DATA_FORMATS.md) - RDF, JSON-LD, and graph data formats

### 🏗️ Architecture & Design

**Core Architecture**:
- [System Architecture](docs/architecture/ACTUAL_SCHEMA.md) - NodePK schema, vector search, graph traversal
- [Embedded Python Architecture](docs/architecture/embedded_python_architecture.md) - In-database Python execution patterns
- [Generic Graph API Design](docs/architecture/generic_graph_api_design.md) - Domain-agnostic GraphQL patterns
- [Query Patterns](docs/architecture/QUERY_PATTERNS.md) - Optimized SQL patterns for graph operations

**Advanced Topics**:
- [Cypher-to-SQL Translation](docs/architecture/cypher_to_sql_translation.md) - openCypher parser and optimizer
- [Competitive Advantages](docs/architecture/COMPETITIVE_ADVANTAGES.md) - vs. Neo4j, Neptune, TigerGraph
- [Graph Primitives](docs/GRAPH_PRIMITIVES.md) - BFS, DFS, shortest path, connected components
- [Advanced SQL Graph Patterns](docs/advanced-graph-sql-patterns.md) - Window functions, recursive CTEs

### ⚡ Performance & Optimization

**Benchmarks & Analysis**:
- [Performance Benchmarks](docs/performance/BENCHMARKS.md) - Vector search, graph queries, hybrid search
- [ACORN-1 vs Community](docs/performance/ACORN-1_vs_Community_Performance_Comparison.md) - HNSW optimization results
- [NodePK Benchmark Results](docs/performance/nodepk_benchmark_results.md) - Production-scale projections
- [Bottleneck Analysis](docs/performance/performance_bottleneck_analysis.md) - Query optimization guide
- [Biomedical Datasets](docs/performance/BIOMEDICAL_DATASETS.md) - STRING DB, UniProt, PubMed

**PPR Performance & Implementation**:
- [PPR Performance Journey](docs/ppr-optimization/ppr-performance-optimization-journey.md) - Complete optimization history
- [PPR Architecture Decision](docs/ppr-optimization/ppr-architecture-decision.md) - Pure Python vs Embedded vs ObjectScript
- [PPR Functional Index](docs/ppr-optimization/ppr-functional-index-deployment-summary.md) - Real-time adjacency maintenance
- [HippoRAG2 Integration](docs/ppr-optimization/hipporag2-ppr-functional-index.md) - Multi-hop reasoning with PPR

**Scale Testing**:
- [Fraud Detection Scale Testing](docs/FRAUD_SCALE_TESTING.md) - 130M transactions, <10ms queries
- [Graph Analytics Roadmap](docs/performance/graph_analytics_roadmap.md) - Future optimization plans

### 🔌 API Reference

**REST APIs**:
- [REST API Documentation](docs/api/REST_API.md) - `/api/cypher`, `/fraud/score`, `/graphql` endpoints
- Fraud API Server - See `src/iris_fraud_server/` for FastAPI fraud scoring implementation

**Python SDK**:
- [Python SDK Overview](docs/python/PYTHON_SDK.md) - `iris_vector_graph` module documentation
- [SQL Procedures](sql/operators.sql) - `kg_KNN_VEC`, `kg_RRF_FUSE`, `kg_GRAPH_PATH`, `kg_PERSONALIZED_PAGERANK`
- Core Module Documentation - See inline docstrings in `iris_vector_graph/` package

### 🧪 Examples & Use Cases

**Industry Examples**:
- [Bitemporal Fraud Detection](examples/bitemporal/) - Financial services, audit trails, late arrival detection
- [Biomedical Protein Networks](docs/biomedical-demo-setup.md) - STRING DB, pathway analysis, drug discovery
- [Caregiver Routing Demo](docs/examples/CAREGIVER_ROUTING_DEMO.md) - Healthcare TSP optimization

**Algorithm Demonstrations**:
- [TSP Analysis](docs/algorithms/TSP_ANALYSIS.md) - Python (NetworkX) vs ObjectScript implementation
- [TSP Implementation Summary](docs/algorithms/TSP_IMPLEMENTATION_SUMMARY.md) - Performance benchmarks

### 🚀 Progress & Roadmap

**Implementation Status**:
- [GraphQL Implementation](docs/progress/graphql_implementation_status.md) - Strawberry GraphQL with DataLoaders
- [openCypher Complete](docs/progress/opencypher_implementation_complete.md) - Pattern matching, Cypher-to-SQL
- [Phase 2 Refactoring](docs/progress/phase2_refactoring_summary.md) - NodePK migration summary

**Future Development**:
- [Enterprise Roadmap](docs/ENTERPRISE_ROADMAP.md) - Multi-tenancy, RBAC, compliance
- [Phase 2: openCypher Implementation](docs/roadmap/phase_2_opencypher_implementation.md)
- [GraphQL Endpoint Design](docs/roadmap/graphql_endpoint_design.md)

### 📋 Design Documents

**Research & Prototypes**:
- [Cypher Parser Prototype](docs/design/cypher_parser_prototype.md) - MVP parser design
- [SQLAlchemy + GraphQL Integration](docs/research/sqlalchemy_graphql_integration.md)
- [Benchmarking Technical Spec](docs/benchmarking/BENCHMARKING_TECHNICAL_SPEC.md)
- [Competitive Benchmarking Design](docs/benchmarking/COMPETITIVE_BENCHMARKING_DESIGN.md)

### 🛠️ Developer Resources

**Development Guides**:
- [CLAUDE.md](CLAUDE.md) - Claude Code agent development guidance
- [IRIS Embedded Python Lessons](docs/IRIS_EMBEDDED_PYTHON_LESSONS.md) - Lessons learned from embedded Python development
- [DevTester Enhancements](docs/iris-devtester-enhancements.md) - Testing framework improvements

**Community Edition**:
- [Fraud Detection (Community Edition)](docs/FRAUD_COMMUNITY_EDITION.md) - 30M transaction limits
- [Financial Services Summary](docs/FRAUD_FINANCIAL_SERVICES_SUMMARY.md) - Production deployment guide

---

## Repository Structure

```
sql/
  schema.sql              # Core graph schema
  bitemporal/             # Fraud detection schema
  fraud/                  # Transaction tables

examples/
  bitemporal/             # Financial services (fraud, audit)

biomedical/               # Life sciences (proteins, pathways)

iris_vector_graph/   # Python graph engine

src/iris_fraud_server/    # FastAPI fraud API

scripts/
  fraud/                  # 130M loader, benchmarks
  migrations/             # NodePK migration

docker/
  Dockerfile.fraud-embedded      # Licensed IRIS + fraud API
  start-fraud-server.sh          # Embedded Python startup
```

---

## License

MIT License - See [LICENSE](LICENSE)

---

## Contributing

We welcome contributions! This repo demonstrates IRIS versatility across:
- **Financial Services**: Fraud detection, bitemporal data, regulatory compliance
- **Biomedical Research**: Protein networks, drug discovery, literature mining

Feel free to add examples from other domains or improve existing implementations.

---

**Production-Ready**: Proven with 130M+ financial transactions and 100K+ biomedical interactions on InterSystems IRIS.
