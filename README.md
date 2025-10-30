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
# 1. Start IRIS database
docker-compose up -d

# 2. Install Python dependencies
pip install iris-vector-graph[dev]

# 3. Load fraud schema
docker exec -i iris /usr/irissys/bin/irissession IRIS -U USER < sql/fraud/schema.sql

# 4. Start fraud API (external Python)
PYTHONPATH=src python -m iris_fraud_server

# Test fraud scoring API
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
# 1. Start IRIS database
docker-compose up -d

# 2. Install dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync && source .venv/bin/activate

# 3. Load STRING protein database (10K proteins, ~1 minute)
python scripts/performance/string_db_scale_test.py --max-proteins 10000

# 4. Start interactive demo server (external Python)
PYTHONPATH=src python -m iris_demo_server.app

# 5. Open browser
open http://localhost:8200/bio
```

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
- `iris_vector_graph_core/` - Core Python graph engine
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

| Metric | Standard IRIS | ACORN-1 (HNSW) |
|--------|--------------|----------------|
| **Vector Search** | 5800ms (flat) | 1.7ms (HNSW) |
| **Multi-hop Queries** | <50ms | <50ms |
| **Hybrid Search (RRF)** | <100ms | <20ms |
| **Graph Analytics** | NetworkX integration | Embedded Python |

**Tested At Scale**:
- ✅ 130M fraud transactions (licensed IRIS)
- ✅ 100K+ protein interactions (STRING DB)
- ✅ 768-dimensional embeddings (biomedical models)

---

## Documentation

### Getting Started
- [Fraud Detection Quick Start](examples/bitemporal/README.md)
- [Biomedical Graph Setup](biomedical/README.md)
- [Installation Guide](docs/setup/INSTALLATION.md)

### Architecture & Design
- [System Architecture](docs/architecture/ACTUAL_SCHEMA.md)
- [IRIS-Native Features](docs/architecture/IRIS_NATIVE.md)
- [Performance Benchmarks](docs/performance/)

### API Reference
- [REST API](docs/api/REST_API.md)
- [Python SDK](iris_vector_graph_core/README.md)
- [SQL Procedures](sql/operators.sql)

### Examples
- [Bitemporal Fraud Detection](examples/bitemporal/)
- [Protein Interaction Networks](biomedical/)
- [Migration to NodePK](scripts/migrations/)

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

iris_vector_graph_core/   # Python graph engine

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
