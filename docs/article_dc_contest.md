# GraphRAG Inside InterSystems IRIS: Hybrid Retrieval for Fraud Detection

**Author:** Thomas Dyar  
**Date:** January 2026

> **TL;DR:** This article demonstrates how to run GraphRAG-style hybrid retrieval—combining vector similarity, graph traversal, and full-text search—entirely within InterSystems IRIS using the `iris-vector-graph` package. We'll use a fraud detection scenario with 150+ entities to show how graph patterns reveal what vector search alone would miss.

---

## The Problem: Vector Search Misses the Network

Generative AI applications typically rely on vector similarity for retrieval (RAG), but pure dense retrieval misses critical relationships. Consider fraud detection:

| Method | What It Finds | What It Misses |
|--------|---------------|----------------|
| **Vector-only** | Accounts with similar transaction patterns | Ring structures connecting money laundering networks |
| **Text-only** | Accounts flagged with keywords like "suspicious" | Mule accounts that look normal individually |
| **Hybrid + Graph** | Both similar patterns AND network connections | Nothing—catches both the signals |

A mule account might have perfectly normal transaction vectors, but graph traversal reveals it's connected to 10+ accounts in circular payment flows. That's the insight hybrid retrieval provides.

---

## What is IRIS Vector Graph?

`iris-vector-graph` is a knowledge graph system built on InterSystems IRIS that combines:

- **Graph traversal** — Multi-hop relationship queries via SQL or Cypher
- **Vector similarity search** — HNSW-indexed embeddings (~1.7ms queries)
- **Full-text search** — BM25 keyword matching via iFind
- **Hybrid fusion** — Reciprocal Rank Fusion (RRF) combining all signals

**Key SQL procedures** (see `sql/operators.sql`):
- `kg_KNN_VEC` — Vector K-nearest neighbors with HNSW optimization
- `kg_TXT` — Full-text search with BM25 ranking
- `kg_RRF_FUSE` — Hybrid ranking fusion combining vector + text
- `kg_PPR` — Personalized PageRank for graph context expansion

**Repository:** https://github.com/intersystems-community/iris-vector-graph

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Fraud Investigation Query               │
│              (account embedding + "suspicious activity")     │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │  Vector  │    │   Text   │    │  Graph   │
    │ Similarity│    │   BM25   │    │   PPR    │
    └────┬─────┘    └────┬─────┘    └────┬─────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                 ┌─────────────────┐
                 │   RRF Fusion    │
                 │  (rank-based)   │
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ Fraud Risk Score│
                 └─────────────────┘
```

All operations execute within IRIS—no external services, no data movement overhead.

---

## Quick Start (5 Minutes)

### Prerequisites
- Docker
- Python 3.10+
- `uv` package manager (recommended) or `pip`

### Setup

```bash
# Clone and install
git clone https://github.com/intersystems-community/iris-vector-graph.git
cd iris-vector-graph
uv sync  # or: pip install -e .

# Start IRIS with HNSW vector indexing
docker-compose up -d

# Wait for IRIS to start (~30s), then connect via IRIS SQL
# In Management Portal (http://localhost:52773/csp/sys/UtilHome.csp) or SQL client:
#   \i sql/schema.sql
#   \i sql/operators.sql
#   \i sql/fraud_sample_data.sql

# Start the API server
uvicorn api.main:app --reload --port 8000
```

### Verify

```bash
# Run the fraud detection demo
python examples/demo_fraud_detection.py
```

Visit http://localhost:8000/graphql for the GraphQL Playground, or http://localhost:8000/docs for OpenAPI docs.

---

## The Sample Data: A Fraud Detection Network

The repository includes a realistic fraud detection dataset in `sql/fraud_sample_data.sql`:

**150+ entities with three detectable fraud patterns:**

### 1. Ring Pattern (Money Laundering)
```
ACCOUNT:RING1_A → TXN → ACCOUNT:RING1_B → TXN → ACCOUNT:RING1_C 
       ↑                                              ↓
       └──── TXN ←── ACCOUNT:RING1_E ←── TXN ←── ACCOUNT:RING1_D
```

Three separate 5-account rings simulate circular money flows that are invisible to individual transaction analysis but obvious in graph traversal.

### 2. Star Pattern (Mule Accounts)
```
        ACCOUNT:A005 ──┐
        ACCOUNT:A010 ──┤
        ACCOUNT:A015 ──┼── TXN_IN ──→ MULE1 ──→ TXN_OUT ──→ ACCOUNT:A050
        ACCOUNT:A020 ──┤
        ACCOUNT:A025 ──┘
```

Two hub accounts (`MULE1`, `MULE2`) receive from 5+ sources and send to different destinations—classic mule behavior.

### 3. Velocity Violations
Eight accounts (`VELOCITY1`-`VELOCITY8`) with transaction bursts exceeding normal patterns.

### Data Summary
| Entity Type | Count | Description |
|-------------|-------|-------------|
| Accounts | 75 | 50 normal + 15 ring + 2 mule + 8 velocity |
| Transactions | 50+ | With FROM_ACCOUNT, TO_ACCOUNT edges |
| Alerts | 25 | Severity: critical/high/medium/low |

---

## Three Query Engines, One Database

IRIS Vector Graph exposes your data through multiple interfaces. Here's how each helps with fraud detection:

### 1. SQL: Ring Pattern Detection

Find accounts participating in circular payment flows:

```sql
-- Detect accounts in ring patterns (both sender and receiver)
SELECT DISTINCT e1.o_id as account_id
FROM rdf_edges e1
WHERE e1.p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
AND EXISTS (
    SELECT 1 FROM rdf_edges e2 
    WHERE e2.o_id = e1.o_id 
    AND e2.p != e1.p
)
AND e1.o_id LIKE 'ACCOUNT:RING%'
```

### 2. Cypher: Mule Account Detection

Find high-degree nodes using graph pattern matching:

```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (t:Transaction)-[:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) WHERE a.node_id STARTS WITH \"ACCOUNT:MULE\" RETURN a.node_id, count(t) as txn_count ORDER BY txn_count DESC"
  }'
```

### 3. GraphQL: Interactive Investigation

Navigate the fraud network with type-safe queries:

```graphql
query {
  node(id: "ACCOUNT:MULE1") {
    id
    labels
    properties
    edges {
      predicate
      target {
        id
        labels
      }
    }
  }
}
```

---

## How Hybrid Search Works

### Step 1: Vector Similarity

Find accounts with similar behavioral patterns using the HNSW index:

```sql
-- Using the kg_KNN_VEC procedure
SELECT id, score 
FROM TABLE(kg_KNN_VEC('[0.1, 0.2, ...]', 20, 'Account'))
```

Returns accounts whose behavioral embeddings are closest to the query vector.

### Step 2: Text Search

Find flagged entities by keywords:

```sql
-- Find accounts mentioned in alerts
SELECT s, val FROM rdf_props 
WHERE LOWER(val) LIKE '%suspicious%' 
   OR LOWER(val) LIKE '%mule%'
```

### Step 3: Reciprocal Rank Fusion

Combine ranked lists without needing normalized scores:

```sql
-- Hybrid search: vector + text combined
SELECT id, rrf, vs, bm25 
FROM TABLE(kg_RRF_FUSE(
    15,                    -- k: final result count
    20,                    -- k1: vector candidates
    20,                    -- k2: text candidates  
    60,                    -- c: RRF damping constant
    '[0.1, 0.2, ...]',     -- queryVector: 768D embedding
    'suspicious mule'      -- qtext: search keywords
))
```

**Why RRF works for fraud:** An account ranked #3 in vector similarity (unusual patterns) and #5 in text search (flagged in alerts) scores higher than an account that's #1 in only one list. This surfaces entities suspicious in multiple ways.

### Step 4: Graph Expansion via PageRank

Expand from suspicious accounts to find connected entities:

```python
from iris_vector_graph import IRISGraphEngine

engine = IRISGraphEngine(connection)
ppr_scores = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=["ACCOUNT:MULE1", "ACCOUNT:RING1_A"],
    damping_factor=0.85,
    max_iterations=100,
    bidirectional=True  # Follow edges in both directions
)

# Top connected accounts by PageRank score
for account_id, score in sorted(ppr_scores.items(), key=lambda x: -x[1])[:10]:
    print(f"{account_id}: {score:.4f}")
```

This reveals accounts connected to known suspicious entities—even if they weren't flagged directly.

---

## Real Query: Find Related Fraud Accounts

Here's a complete investigation flow using the Python API:

```python
from iris_vector_graph import IRISGraphEngine

# Connect to IRIS
engine = IRISGraphEngine(connection)

# Step 1: Start with a known suspicious account
seed_account = "ACCOUNT:MULE1"

# Step 2: Find similar accounts by behavioral embedding
similar = engine.kg_KNN_VEC(
    query_vector='[...]',  # MULE1's embedding
    k=10,
    label_filter='Account'
)
print(f"Similar accounts: {[acc for acc, _ in similar]}")

# Step 3: Expand via graph to find connected accounts
ppr = engine.kg_PERSONALIZED_PAGERANK(
    seed_entities=[seed_account],
    bidirectional=True
)
connected = [(acc, score) for acc, score in ppr.items() if score > 0.01]
print(f"Connected accounts: {len(connected)}")

# Step 4: Query relationships directly
relationships = engine.kg_NEIGHBORHOOD_EXPANSION(
    entity_list=[seed_account],
    expansion_depth=1,
    confidence_threshold=0
)
for rel in relationships:
    print(f"  {rel['source']} --{rel['predicate']}--> {rel['target']}")
```

---

## Performance

Benchmarks with HNSW indexing enabled:

| Operation | Latency | Notes |
|-----------|---------|-------|
| Vector KNN (k=20) | **1.7ms** | HNSW index with ACORN-1 |
| Graph traversal (2-hop) | **0.25ms** | SQL join on indexed edges |
| Text search (BM25) | **5ms** | iFind full-text search |
| Full hybrid query | **<50ms** | All modalities combined |
| PPR (1K nodes) | **5.3ms** | IRIS embedded Python |

Without HNSW, vector search falls back to ~5.8s (Python cosine similarity)—a 3400x difference.

---

## Why This Matters for GenAI

Traditional RAG pipelines require:
- Vector database (Pinecone, Weaviate, etc.)
- Graph database (Neo4j, etc.)
- Search index (Elasticsearch, etc.)
- Orchestration layer to merge results

**IRIS Vector Graph consolidates all three:**

| Capability | Traditional Stack | IRIS Vector Graph |
|------------|------------------|-------------------|
| Vector search | Separate service | Native HNSW |
| Graph queries | Separate service | SQL + Cypher |
| Text search | Separate service | BM25 via iFind |
| Cross-modal joins | Application code | Single SQL query |
| Transactional consistency | Complex | ACID guaranteed |

For an LLM doing fraud investigation, this means richer context: not just similar transaction patterns, but also the network structure, connected entities, and alert history—all in one query.

---

## Try It Yourself

```bash
# Clone the repository
git clone https://github.com/intersystems-community/iris-vector-graph.git
cd iris-vector-graph

# Start IRIS
docker-compose up -d

# Install Python dependencies
uv sync

# Run the fraud detection demo
python examples/demo_fraud_detection.py
```

The demo validates:
- Ring pattern detection (money laundering)
- Mule account detection (high-degree nodes)
- Vector anomaly detection (behavioral outliers)
- Alert summary (by severity and status)

---

## Extending to Your Domain

The fraud detection example demonstrates patterns that apply broadly:

| Domain | Ring Pattern | Star Pattern | Anomaly Detection |
|--------|-------------|--------------|-------------------|
| **Fraud** | Money laundering cycles | Mule accounts | Unusual transactions |
| **Healthcare** | Referral loops | High-volume prescribers | Outlier diagnoses |
| **Supply Chain** | Circular dependencies | Hub suppliers | Demand anomalies |
| **Social Networks** | Bot rings | Influencer accounts | Fake engagement |

The schema (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`) is domain-agnostic. Change the labels and properties, and the same queries work.

---

## Conclusion

Hybrid retrieval—combining vectors, graphs, and text—dramatically improves fraud detection by surfacing patterns invisible to any single method. With `iris-vector-graph`, you can run this entire pipeline inside InterSystems IRIS:

- **No external dependencies** — Everything in one database
- **Sub-millisecond graph queries** — HNSW indexing + SQL joins
- **Multiple query interfaces** — GraphQL, Cypher, SQL
- **Production patterns included** — Ring, star, and velocity detection ready to test

The 150+ entity fraud dataset in the repo provides a concrete starting point. Clone it, run the demo, and see hybrid retrieval in action.

---

## Resources

- **Repository:** https://github.com/intersystems-community/iris-vector-graph
- **Schema:** `sql/schema.sql` - core graph tables
- **Operators:** `sql/operators.sql` - hybrid search SQL procedures  
- **Sample Data:** `sql/fraud_sample_data.sql` - 150+ entity fraud network
- **Demo Script:** `examples/demo_fraud_detection.py` - interactive demo
- **Python API:** `iris_vector_graph/engine.py` - IRISGraphEngine class

---

## Discussion

What other patterns could hybrid graph+vector search reveal in your domain? I'm particularly interested in:

1. **Healthcare:** Could similar patterns detect insurance fraud or care network anomalies?
2. **Supply Chain:** Ring patterns for detecting circular invoicing?
3. **Security:** Combining threat intel embeddings with attack graph traversal?

Share your thoughts in the comments!

---

*Note: This article was drafted with assistance from Claude for structure and clarity. All code examples, data patterns, and technical claims were verified against the actual repository implementation.*
