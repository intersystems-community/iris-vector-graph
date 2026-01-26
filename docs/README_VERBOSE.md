# IRIS Vector Graph

A general-purpose graph utility built on InterSystems IRIS that supports and demonstrates knowledge graph construction and query techniques. Combines graph traversal, vector similarity search, and full-text search in a single database. Features a robust **recursive-descent Cypher parser** with multi-stage query support (`WITH`), aggregations, and transactional DML (`CREATE`, `DELETE`, `MERGE`).

## What is InterSystems IRIS?

IRIS is a multi-model database that supports SQL, objects, documents, and key-value storage. This project uses IRIS's embedded Python, SQL procedures, and native vector search capabilities to implement graph operations without external dependencies.

## What This Does

Stores graph data (nodes, edges, properties) in IRIS SQL tables and provides:
- **Transactional Cypher Engine** - `CREATE`, `DELETE`, `MERGE`, `SET`, `REMOVE` with ACID consistency
- **Vector similarity search** - Find semantically similar entities using embeddings
- **Multi-stage graph traversal** - Chained logic using `WITH` and recursive paths
- **Hybrid search** - Combine vector similarity with keyword search using RRF (Reciprocal Rank Fusion)
- **Referential integrity** - Foreign key constraints ensure data consistency across all graph entities
- **Graph analytics** - PageRank, Connected Components, and other algorithms optimized with embedded Python
- **Advanced API** - openCypher, GraphQL, and SQL interfaces

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
│  - Core engine (iris_vector_graph)         │
│  - NetworkX integration                         │
│  - Vector utilities                             │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  IRIS Tables                                    │
│  - nodes: Explicit node identity (PRIMARY KEY)  │
│  - rdf_edges: Relationships (FK to nodes)       │
│  - rdf_labels: Node types (FK to nodes)         │
│  - rdf_props: Properties (FK to nodes)          │
│  - kg_NodeEmbeddings: Vector embeddings (FK)    │
└─────────────────────────────────────────────────┘
```

Data is stored in RDF-style tables with explicit node identity and foreign key constraints for referential integrity. Graph operations are implemented via SQL procedures and embedded Python. HNSW vector indexing provides fast similarity search (requires IRIS 2025.3+ or ACORN-1 pre-release build).

## Repository Structure

```
sql/
  schema.sql              # Table definitions
  operators.sql           # SQL procedures (requires IRIS 2025.3+)
  operators_fixed.sql     # Compatibility version for older IRIS
  migrations/             # Schema migrations (NodePK)
    001_add_nodepk_table.sql
    002_add_fk_constraints.sql

iris_vector_graph/   # Python engine
  engine.py               # Core search/traversal logic
  fusion.py               # RRF hybrid search
  vector_utils.py         # Vector operations

iris_src/src/                 # ObjectScript components
  Graph/KG/               # REST API and Python integration
    Service.cls           # REST API endpoints
    PyOps.cls             # Python integration
    Traversal.cls         # Graph operations
  PageRankEmbedded.cls    # Embedded Python graph analytics

scripts/
  ingest/networkx_loader.py         # Load data from files
  migrations/migrate_to_nodepk.py   # NodePK migration utility
  performance/                      # Benchmarking tools

docs/
  architecture/           # Design documentation
  performance/            # Performance benchmarks
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
\i sql/migrations/001_add_nodepk_table.sql
\i sql/migrations/002_add_fk_constraints.sql
\i scripts/sample_data_768.sql
```

**Note**: If you have existing data, use the migration utility instead:
```bash
uv run python scripts/migrations/migrate_to_nodepk.py --validate-only  # Dry run
uv run python scripts/migrations/migrate_to_nodepk.py --execute         # Execute
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

## Data Integrity & Graph Analytics

### Referential Integrity with NodePK

The system enforces data consistency through foreign key constraints on an explicit `nodes` table:

**Benefits:**
- **Zero orphaned references** - Cannot create edges, labels, properties, or embeddings for non-existent nodes
- **Data validation** - FK constraints validated on every insert/update operation
- **Performance** - FK constraints actually IMPROVE performance by 64% (query optimizer benefits)
- **Migration support** - Utility to safely migrate existing data to explicit node identity

**Schema:**
```sql
-- Central node registry
CREATE TABLE nodes (
    node_id VARCHAR(256) PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- All graph tables reference nodes
ALTER TABLE rdf_edges ADD FOREIGN KEY (s) REFERENCES nodes(node_id);
ALTER TABLE rdf_edges ADD FOREIGN KEY (o_id) REFERENCES nodes(node_id);
ALTER TABLE rdf_labels ADD FOREIGN KEY (s) REFERENCES nodes(node_id);
ALTER TABLE rdf_props ADD FOREIGN KEY (s) REFERENCES nodes(node_id);
ALTER TABLE kg_NodeEmbeddings ADD FOREIGN KEY (id) REFERENCES nodes(node_id);
```

**Migration:**
```bash
# Validate existing data (dry run)
uv run python scripts/migrations/migrate_to_nodepk.py --validate-only

# Execute migration
uv run python scripts/migrations/migrate_to_nodepk.py --execute

# See detailed logs
uv run python scripts/migrations/migrate_to_nodepk.py --execute --verbose
```

### Graph Analytics with Embedded Python

High-performance graph algorithms using IRIS embedded Python (runs in-process, 10-50x faster than client-side).

**Setup Requirements:**
- InterSystems IRIS 2024.1+ with embedded Python support
- Container Python Framework (CPF) configuration - see [iris-embedded-python-template](https://github.com/intersystems-community/iris-embedded-python-template)
- Python packages installed in IRIS runtime environment (separate from client)
- PageRankEmbedded.cls compiled in IRIS

**Compilation (one-time setup):**
```objectscript
// IRIS terminal - compile PageRankEmbedded class
do $system.OBJ.Load("/path/to/iris_src/src/PageRankEmbedded.cls", "ck")
```

**PageRank Usage:**
```objectscript
// IRIS terminal - compute PageRank on graph subset
set results = ##class(PageRankEmbedded).ComputePageRank("PROTEIN:%", 10, 0.85)
do results.%ToJSON()

// With convergence tracking
set results = ##class(PageRankEmbedded).ComputePageRankWithMetrics("PROTEIN:%", 10, 0.85, 0.0001)
do results.%ToJSON()
```

**Python client:**
```python
# PageRank via embedded Python (in-process execution)
cursor.execute("""
    SELECT ##class(PageRankEmbedded).ComputePageRank('PROTEIN:%', 10, 0.85)
""")
pagerank_json = cursor.fetchone()[0]

import json
results = json.loads(pagerank_json)
for node in results[:10]:  # Top 10 by PageRank
    print(f"{node['nodeId']}: {node['pagerank']:.6f}")
```

**Performance (1K nodes, 8863 edges):**
- PageRank (10 iterations): 5.31ms
- Connected Components: 4.70ms
- Shortest Path (BFS): 0.045ms per level
- Degree Centrality: 72-89ms

**Expected performance (100K nodes, 500K edges):**
- PageRank: 1-5 seconds (vs 50-60s client-side Python baseline)
- Graph traversal: <2ms for 3-hop queries
- Concurrent workload: ≥700 queries/second

See [`docs/performance/graph_analytics_roadmap.md`](docs/performance/graph_analytics_roadmap.md) for optimization roadmap and [`docs/performance/nodepk_benchmark_results.md`](docs/performance/nodepk_benchmark_results.md) for detailed benchmarks.

## Performance

The system has been tested with biomedical datasets (STRING protein interactions, PubMed literature). Performance metrics:

**With ACORN-1 (pre-release build with HNSW indexing):**
- Vector search: ~1.7ms (HNSW-optimized)
- Node lookup: 0.292ms (PRIMARY KEY index)
- Graph queries: ~0.09ms per hop
- Bulk node insertion: 6,496 nodes/second
- PageRank (1K nodes): 5.31ms
- Concurrent queries: 702 queries/second
- Handles 50K+ nodes, 500K+ edges tested

**With standard IRIS Community Edition:**
- Vector search: ~5.8s (no HNSW optimization)
- Graph queries: ~1ms average
- Data ingestion: ~29 proteins/second
- Still functional for development and moderate-scale datasets

**NodePK Performance:**
- FK constraint overhead: -64% (IMPROVED performance, query optimizer benefits)
- Node lookup: <1ms with PRIMARY KEY index
- Graph traversal: 0.09ms per hop with FK validation
- See [`docs/performance/nodepk_benchmark_results.md`](docs/performance/nodepk_benchmark_results.md)

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
- [`docs/architecture/embedded_python_architecture.md`](docs/architecture/embedded_python_architecture.md) - Embedded Python constraints for hybrid queries
- [`docs/api/REST_API.md`](docs/api/REST_API.md) - REST endpoint reference
- [`docs/setup/QUICKSTART.md`](docs/setup/QUICKSTART.md) - Detailed setup guide
- [`docs/performance/`](docs/performance/) - Performance analysis and benchmarks
  - [`nodepk_benchmark_results.md`](docs/performance/nodepk_benchmark_results.md) - NodePK comprehensive benchmarks
  - [`graph_analytics_roadmap.md`](docs/performance/graph_analytics_roadmap.md) - PageRank optimization phases
- [`specs/001-add-explicit-nodepk/`](specs/001-add-explicit-nodepk/) - NodePK feature specification and implementation plan

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