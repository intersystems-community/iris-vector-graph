# IRIS Vector Graph

A knowledge graph system built on InterSystems IRIS that combines graph traversal, vector similarity search, and full-text search in a single database. Designed for biomedical research use cases like protein interaction networks and literature mining.

## What is InterSystems IRIS?

IRIS is a multi-model database that supports SQL, objects, documents, and key-value storage. This project uses IRIS's embedded Python, SQL procedures, and native vector search capabilities to implement graph operations without external dependencies.

## What This Does

Stores graph data (nodes, edges, properties) in IRIS SQL tables and provides:
- **Vector similarity search** - Find semantically similar entities using embeddings
- **Graph traversal** - Multi-hop path queries across relationships
- **Hybrid search** - Combine vector similarity with keyword search using RRF (Reciprocal Rank Fusion)
- **REST API** - Access all features via HTTP endpoints

Built with embedded Python for flexibility and IRIS SQL procedures for performance.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  REST API (Graph.KG.Service)                    │
│  - /kg/vectorSearch                             │
│  - /kg/hybridSearch                             │
│  - /kg/metaPath                                 │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  IRIS SQL Procedures (operators.sql)            │
│  - kg_KNN_VEC: Vector similarity                │
│  - kg_RRF_FUSE: Hybrid search                   │
│  - Text search with BM25                        │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  Embedded Python (Graph.KG.PyOps)               │
│  - Core engine (iris_vector_graph_core)         │
│  - NetworkX integration                         │
│  - Vector utilities                             │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  IRIS Tables                                    │
│  - rdf_edges: Relationships                     │
│  - rdf_labels: Node types                       │
│  - rdf_props: Properties                        │
│  - kg_NodeEmbeddings: Vector embeddings         │
└─────────────────────────────────────────────────┘
```

Data is stored in RDF-style tables. Graph operations are implemented via SQL procedures that call Python functions when needed. HNSW vector indexing provides fast similarity search (requires IRIS 2025.3+ or ACORN-1 pre-release build).

## Repository Structure

```
sql/
  schema.sql              # Table definitions
  operators.sql           # SQL procedures (requires IRIS 2025.3+)
  operators_fixed.sql     # Compatibility version for older IRIS

iris_vector_graph_core/   # Python engine
  engine.py               # Core search/traversal logic
  fusion.py               # RRF hybrid search
  vector_utils.py         # Vector operations

iris/src/Graph/KG/        # ObjectScript components
  Service.cls             # REST API endpoints
  PyOps.cls               # Python integration
  Traversal.cls           # Graph operations

scripts/
  ingest/networkx_loader.py    # Load data from files
  performance/                 # Benchmarking tools

docs/
  architecture/           # Design documentation
  setup/                  # Installation guides
  api/REST_API.md        # API reference
```

## Quick Start

### Prerequisites

- Docker (for running IRIS)
- Python 3.8+ (we use UV for package management)
- Basic familiarity with SQL

### Installation

**1. Install UV and dependencies:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repository-url>
cd iris-vector-graph
uv sync
source .venv/bin/activate
```

**2. Start IRIS:**
```bash
# Option A: ACORN-1 (pre-release build with HNSW optimization - fastest)
# Note: ACORN-1 is experimental and not yet in standard IRIS releases
docker-compose -f docker-compose.acorn.yml up -d

# Option B: Standard IRIS Community Edition (slower but stable)
# docker-compose up -d
```

**3. Load schema:**
```sql
# Connect to IRIS SQL terminal
docker exec -it iris-acorn-1 iris session iris

# In SQL prompt:
\i sql/schema.sql
\i sql/operators.sql  # Use operators_fixed.sql if this fails
\i scripts/sample_data_768.sql
```

**4. Create REST endpoints:**
```objectscript
# In IRIS terminal:
Do ##class(Graph.KG.Service).CreateWebApp("/kg")
```

**5. Configure and test:**
```bash
cp .env.sample .env
# Edit .env with connection details (defaults usually work)

# Run tests
uv run python tests/python/run_all_tests.py --quick
```

## Usage Examples

### Vector Similarity Search

Find entities with similar embeddings:

**REST API:**
```bash
curl -X POST http://localhost:52773/kg/vectorSearch \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [0.1, 0.2, 0.3, ...],  # 768-dimensional embedding
    "k": 10,
    "label": "protein"
  }'
```

**Python (using SQL procedure):**
```python
import iris, json, numpy as np
conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
c = conn.cursor()
qvec = np.random.rand(768).tolist()
c.execute("CALL kg_KNN_VEC(?, ?, ?)", [json.dumps(qvec), 10, 'protein'])
print(c.fetchall())
```

**Direct SQL (if VECTOR functions available):**
```sql
-- Use when VECTOR/TO_VECTOR functions are available
SELECT TOP 10 id,
       VECTOR_COSINE(emb, TO_VECTOR('[0.1,0.2,0.3,...]')) AS similarity
FROM kg_NodeEmbeddings
ORDER BY similarity DESC;
```

### Graph Traversal

Find multi-hop paths between entities:

**REST API:**
```bash
curl -X POST http://localhost:52773/kg/metaPath \
  -H "Content-Type: application/json" \
  -d '{
    "srcId": "DRUG:aspirin",
    "predicates": ["targets", "interacts_with", "associated_with"],
    "maxHops": 3,
    "dstLabel": "disease"
  }'
```

**Python:**
```python
# Multi-hop graph traversal
cursor.execute("""
    SELECT e1.s as drug, e2.o_id as protein, e3.o_id as disease
    FROM rdf_edges e1
    JOIN rdf_edges e2 ON e1.o_id = e2.s
    JOIN rdf_edges e3 ON e2.o_id = e3.s
    WHERE e1.s = ?
      AND e1.p = 'targets'
      AND e2.p = 'interacts_with'
      AND e3.p = 'associated_with'
""", ['DRUG:aspirin'])

pathways = cursor.fetchall()
for drug, protein, disease in pathways:
    print(f"{drug} → {protein} → {disease}")
```

**SQL (recursive CTE):**
```sql
-- Find shortest paths between drug and disease
WITH RECURSIVE pathway(source, target, path, hops) AS (
  SELECT s, o_id, CAST(s || ' -> ' || o_id AS VARCHAR(1000)), 1
  FROM rdf_edges
  WHERE s = 'DRUG:aspirin'

  UNION ALL

  SELECT p.source, e.o_id, p.path || ' -> ' || e.o_id, p.hops + 1
  FROM pathway p
  JOIN rdf_edges e ON p.target = e.s
  WHERE p.hops < 4
    AND e.o_id LIKE 'DISEASE:%'
)
SELECT path, hops FROM pathway
WHERE target LIKE 'DISEASE:%'
ORDER BY hops LIMIT 10;
```

### Hybrid Search

Combine vector similarity with keyword matching:

**REST API:**
```bash
curl -X POST http://localhost:52773/kg/hybridSearch \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [0.1, 0.2, ...],  # Cancer pathway embedding
    "text": "tumor suppressor DNA repair",
    "k": 15,
    "c": 60
  }'
```

**Python:**
```python
# Hybrid search using RRF fusion of vector and text results
import numpy as np

# Use the stored procedure for hybrid search
query_vector = np.random.rand(768).tolist()  # Replace with actual embedding
cursor.execute("CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)", [
    15,                              # k final results
    20,                             # k1 vector results
    20,                             # k2 text results
    60,                             # c parameter for RRF
    json.dumps(query_vector),       # query vector as JSON
    'tumor suppressor DNA repair'   # text query
])

results = cursor.fetchall()
for entity_id, rrf_score, vs_score, bm25_score in results:
    print(f"{entity_id}: RRF={rrf_score:.3f}, Vector={vs_score:.3f}, Text={bm25_score:.3f}")

# Alternative: Manual text search in qualifiers
cursor.execute("""
    SELECT s, qualifiers
    FROM rdf_edges
    WHERE qualifiers LIKE '%tumor%' OR qualifiers LIKE '%suppressor%'
    LIMIT 15
""")
text_results = cursor.fetchall()

for result in text_results:
    print(f"Entity: {result[0]}")
    print(f"  Qualifiers: {result[1]}")
```

### Network Analysis

Find highly connected nodes:

**Python:**
```python
# Find hub proteins (most connections)
cursor.execute("""
    SELECT s as protein, COUNT(*) as connections
    FROM rdf_edges
    WHERE p = 'interacts_with'
      AND s LIKE 'PROTEIN:%'
    GROUP BY s
    ORDER BY connections DESC
    LIMIT 20
""")

hubs = cursor.fetchall()
print("Top protein interaction hubs:")
for protein, connections in hubs:
    print(f"  {protein}: {connections} interactions")
```

**SQL:**
```sql
-- Network clustering coefficient
SELECT
    node,
    connections,
    triangles,
    CASE WHEN connections > 1
         THEN 2.0 * triangles / (connections * (connections - 1))
         ELSE 0 END as clustering_coefficient
FROM (
    SELECT
        e1.s as node,
        COUNT(DISTINCT e1.o_id) as connections,
        COUNT(DISTINCT e2.o_id) as triangles
    FROM rdf_edges e1
    LEFT JOIN rdf_edges e2 ON e1.o_id = e2.s AND e2.o_id IN (
        SELECT o_id FROM rdf_edges WHERE s = e1.s
    )
    WHERE e1.p = 'interacts_with'
    GROUP BY e1.s
) stats
ORDER BY clustering_coefficient DESC;
```

### Complete Workflow Example

Drug target discovery:

**Python:**
```python
def find_drug_targets(disease_name):
    """Find potential drug targets for a disease (working pattern)"""

    # 1. Find disease-associated proteins
    cursor.execute("""
        SELECT DISTINCT o_id as protein
        FROM rdf_edges
        WHERE s = ? AND p = 'associated_with'
    """, [f"DISEASE:{disease_name}"])

    disease_proteins = [row[0] for row in cursor.fetchall()]

    # 2. Find drugs targeting these proteins
    targets = []
    for protein in disease_proteins:
        cursor.execute("""
            SELECT s as drug, qualifiers
            FROM rdf_edges
            WHERE o_id = ? AND p = 'targets'
        """, [protein])

        targets.extend(cursor.fetchall())

    return targets

# Usage
cancer_drugs = find_drug_targets('cancer')
print(f"Found {len(cancer_drugs)} potential drug-target relationships")
```

**NetworkX integration:**
```python
import networkx as nx

# Export IRIS graph to NetworkX for analysis
G = nx.DiGraph()

cursor.execute("SELECT s, o_id, p FROM rdf_edges WHERE p = 'interacts_with'")
for source, target, relation in cursor.fetchall():
    G.add_edge(source, target, relation=relation)

# Network analysis
centrality = nx.betweenness_centrality(G)
communities = nx.community.greedy_modularity_communities(G)

print(f"Network has {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
print(f"Found {len(communities)} protein communities")
```

## Performance

The system has been tested with biomedical datasets (STRING protein interactions, PubMed literature). Performance metrics:

**With ACORN-1 (pre-release build with HNSW indexing):**
- Vector search: ~6ms
- Graph queries: ~0.25ms average
- Data ingestion: ~476 proteins/second
- Handles 10K+ nodes, 50K+ edges

**With standard IRIS Community Edition:**
- Vector search: ~5.8s (no HNSW optimization)
- Graph queries: ~1ms average
- Data ingestion: ~29 proteins/second
- Still functional for development and moderate-scale datasets

See [`docs/performance/`](docs/performance/) for detailed benchmarks.

## Use Cases

Designed for biomedical research but adaptable to other domains:

- Protein-protein interaction networks
- Drug-target relationship discovery
- Literature mining and knowledge extraction
- Multi-hop reasoning across heterogeneous data
- Semantic search over structured knowledge

The vector search supports any 768-dimensional embeddings (e.g., from BioBERT, SapBERT, or general-purpose models).

## Development

**Run tests:**
```bash
uv run python tests/python/run_all_tests.py --quick
uv run python tests/python/test_iris_rest_api.py
```

**Load your own data:**
```bash
# TSV format: source\ttarget\trelationship_type
uv run python scripts/ingest/networkx_loader.py load data.tsv --format tsv
```

**Performance testing:**
```bash
uv run python scripts/performance/string_db_scale_test.py --max-proteins 10000
```

## Documentation

- [`docs/architecture/ACTUAL_SCHEMA.md`](docs/architecture/ACTUAL_SCHEMA.md) - Schema details and working patterns
- [`docs/api/REST_API.md`](docs/api/REST_API.md) - REST endpoint reference
- [`docs/setup/QUICKSTART.md`](docs/setup/QUICKSTART.md) - Detailed setup guide
- [`docs/performance/`](docs/performance/) - Performance analysis and benchmarks

## Requirements

- **IRIS Database:**
  - IRIS 2025.3+ for VECTOR functions (recommended)
  - ACORN-1 pre-release build for HNSW optimization (fastest, but experimental)
  - Standard Community Edition works but without HNSW indexing (slower vector search)
- **Python:** 3.8+ (embedded in IRIS, also needed for client scripts)
- **Docker:** For running IRIS container

## Limitations

- Vector search requires IRIS with VECTOR support (2025.3+ or ACORN-1)
- HNSW indexing (major speedup) only available in ACORN-1 pre-release - not yet in standard IRIS
- ACORN-1 is experimental and not recommended for production deployments
- Graph traversal uses SQL recursive CTEs - performance degrades on very deep paths (>5 hops)
- Text search uses simple BM25 implementation (not production-grade full-text)

## License

See [LICENSE](LICENSE) file for details.