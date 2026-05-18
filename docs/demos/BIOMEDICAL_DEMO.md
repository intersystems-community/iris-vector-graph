# Biomedical Research Demo

The biomedical demo shows how IVG combines vector similarity search with graph traversal to navigate protein interaction networks — a pattern applicable to drug target discovery, pathway analysis, and literature mining.

## Running the Demo

```bash
docker compose up -d
pip install "iris-vector-graph[full]"
python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1 --app-dir src
open http://localhost:8200/bio
```

## What It Demonstrates

### Three Pre-Built Scenarios

| Scenario | Query | Purpose |
|----------|-------|---------|
| Cancer protein | TP53 (tumor suppressor) | Find structurally similar proteins across species |
| Metabolic pathway | GAPDH → LDHA (2 hops) | Trace glycolysis pathway connections |
| Drug target | Kinase inhibitor search | Find proteins targetable by a drug class |

### Vector Similarity Search

Proteins are embedded using sequence and structural features. The demo finds the `top_k` proteins most similar to a query protein:

```cypher
CALL ivg.vector.search('Protein', 'emb', $query_vector, 10)
YIELD node, score
RETURN node.id, node.name, node.organism, score
ORDER BY score DESC
```

Similarity thresholds:
- **Very High** (≥ 0.9) — likely same protein family
- **High** (≥ 0.75) — related function or structural homolog  
- **Moderate** (≥ 0.5) — distant relationship, worth investigating
- **Low** (< 0.5) — weak signal

### Graph Traversal: Protein Interaction Networks

From any protein, traverse interaction edges to find pathway neighbors:

```cypher
MATCH (p:Protein {id: $protein_id})-[r:INTERACTS_WITH|ACTIVATES|INHIBITS*1..2]->(neighbor)
RETURN neighbor.id, neighbor.name, type(r) LIMIT 25
```

Edge types and their visual encoding in the D3 force graph:
- `ACTIVATES` → green edges (stimulatory)
- `INHIBITS` → red edges (inhibitory)  
- `BINDS` → blue edges (physical binding)
- `INTERACTS_WITH` → grey edges (general)

### Hybrid Search: Vector + Graph

The power of IVG is combining both modalities in one query: find similar proteins *and* their network neighborhood.

```cypher
CALL ivg.vector.search('Protein', 'emb', $query_vector, 5) YIELD node AS seed, score
MATCH (seed)-[:INTERACTS_WITH*1..2]->(neighbor)
RETURN seed.id, neighbor.id, score
ORDER BY score DESC
```

This is how you find drug targets: start from a known protein, find structurally similar proteins (potential off-targets), then traverse to see what pathways they're wired into.

## Interactive Network Visualization

The D3.js force-directed graph is interactive:
- **Click** a node to expand its 1-hop neighborhood (up to 500 total nodes)
- **Drag** nodes to rearrange the layout
- **Zoom** with scroll wheel
- **Double-click** to reset the view

Node colors indicate organism: teal = *Homo sapiens*, purple = *Mus musculus*, orange = others.

## Data Model

```
Protein (id, name, organism, sequence, embedding)
  -[:INTERACTS_WITH]-> Protein
  -[:ACTIVATES]-> Protein
  -[:INHIBITS]-> Protein
  -[:BINDS]-> Protein
  -[:IN_PATHWAY]-> Pathway
  -[:ENCODED_BY]-> Gene
```

Protein embeddings are computed offline from UniProt sequence data and stored in `Graph_KG.kg_NodeEmbeddings` using an HNSW index for fast approximate nearest-neighbor search.

## Architecture

```
Browser (HTMX + D3.js force graph)
    ↓ POST /api/bio/search
    ↓ GET  /api/bio/network/{protein_id}
FastHTML route (src/iris_demo_server/routes/biomedical.py)
    ↓ engine.kg_KNN_VEC() / engine.execute_cypher()
IRISGraphEngine + IRISGraphStore
    ↓ VECTOR_COSINE + HNSW index / SQL JOIN
IRIS (Graph_KG schema + kg_NodeEmbeddings)
```

## Loading Demo Data

The demo works best with real protein interaction data. The examples directory includes a loader:

```bash
python examples/demo_biomedical.py --load-data
```

Or load from UniProt / STRING database exports using the bulk loader:

```python
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.bulk_loader import BulkLoader

engine = IRISGraphEngine(conn)
engine.initialize_schema(embedding_dimension=1024)

loader = BulkLoader(conn)
loader.load_nodes(protein_nodes, label_attr="type")
loader.load_edges(interaction_edges)
```
