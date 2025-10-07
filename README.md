# IRIS Vector Graph

A knowledge graph system built on InterSystems IRIS that combines graph traversal, vector similarity search, and full-text search in a single database.

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

### Option A: Fraud Detection (Financial Services)

**Start fraud detection system** (130M transactions, bitemporal audit trails):

```bash
# Licensed IRIS with embedded Python fraud API
docker-compose -f docker-compose.fraud-embedded.yml up -d

# Test fraud scoring API (~2 min startup)
curl -X POST http://localhost:8100/fraud/score \
  -H 'Content-Type: application/json' \
  -d '{"mode":"MLP","payer":"acct:test","device":"dev:laptop","amount":1000.0}'

# Load bitemporal schema (for audit trails, chargebacks)
docker exec -i iris-fraud-embedded /usr/irissys/bin/irissession IRIS -U USER < sql/bitemporal/schema.sql

# Run fraud scenarios (late arrivals, chargebacks, compliance)
docker exec -e IRISUSERNAME=_SYSTEM -e IRISPASSWORD=SYS -e IRISNAMESPACE=USER \
    iris-fraud-embedded /usr/irissys/bin/irispython \
    /home/irisowner/app/examples/bitemporal/bitemporal_fraud.py
```

**What you get**:
- FastAPI fraud scoring at `:8100/fraud/score`
- Bitemporal data (track when transactions happened vs. when you learned about them)
- Complete audit trails (regulatory compliance: SOX, MiFID II)
- 130M transaction graph

**Learn more**: [`examples/bitemporal/README.md`](examples/bitemporal/README.md) - Fraud scenarios, chargeback defense, model tracking

---

### Option B: Biomedical Graph (Life Sciences)

**Start vector graph system** (protein networks, pathway analysis):

```bash
# ACORN-1 pre-release (HNSW optimization - fastest)
docker-compose -f docker-compose.acorn.yml up -d

# OR: Standard IRIS Community Edition
docker-compose up -d

# Install dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync && source .venv/bin/activate

# Load schema and sample data
docker exec -it iris-acorn-1 iris session iris
\i sql/schema.sql
\i sql/operators.sql
\i scripts/sample_data_768.sql

# Create REST API
Do ##class(Graph.KG.Service).CreateWebApp("/kg")

# Test graph queries
uv run python tests/python/run_all_tests.py --quick
```

**What you get**:
- Vector similarity search (find related proteins)
- Graph traversal (interaction pathways)
- Hybrid search (combine embeddings + full-text)
- REST API at `:52773/kg/`

**Learn more**: [`biomedical/README.md`](biomedical/README.md) - Protein networks, drug discovery workflows

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

## Architecture

```
Financial Services Stack          Biomedical Stack
┌────────────────────────┐        ┌────────────────────────┐
│ FastAPI Fraud Server   │        │ REST API (Graph.KG)    │
│ (embedded Python)      │        │ (ObjectScript)         │
├────────────────────────┤        ├────────────────────────┤
│ Bitemporal Tables      │        │ Vector Search (HNSW)   │
│ (valid_time, sys_time) │        │ Hybrid Search (RRF)    │
├────────────────────────┤        ├────────────────────────┤
│ IRIS Globals           │◄───────┤ IRIS Globals           │
│ (append-only audit)    │        │ (graph storage)        │
└────────────────────────┘        └────────────────────────┘

         Same Platform: InterSystems IRIS
         Same Features: Embedded Python, SQL, Globals
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
