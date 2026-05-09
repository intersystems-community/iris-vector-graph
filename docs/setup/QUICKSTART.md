# Quickstart Guide

This guide gets you from zero to a working graph in 5 minutes using IRIS Community Edition (free, no license required).

---

## Prerequisites

- Docker (any recent version)
- Python 3.10+
- `pip install iris-vector-graph`

---

## Step 1: Start IRIS

From the repository root:

```bash
docker compose up -d
```

Wait about 30 seconds for IRIS to be ready. You can check:

```bash
docker compose ps       # Should show "healthy"
```

IRIS is now running on:
- **Port 1972** — SuperServer (used by the Python SDK)
- **Port 52773** — Management Portal at http://localhost:52773/csp/sys/UtilHome.csp

Default credentials: `_SYSTEM` / `SYS`

---

## Step 2: Install the library

```bash
pip install iris-vector-graph
```

---

## Step 3: Initialize the schema

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)
engine.initialize_schema()
print("Schema ready.")
```

This creates all SQL tables and compiles the ObjectScript classes. It's idempotent — safe to run multiple times.

---

## Step 4: Load a demo graph

This demo builds a small knowledge graph of biomedical entities:

```python
nodes = [
    {"id": "gene:BRCA1",      "labels": ["Gene"],    "properties": {"name": "BRCA1",    "type": "tumor_suppressor"}},
    {"id": "gene:TP53",       "labels": ["Gene"],    "properties": {"name": "TP53",     "type": "tumor_suppressor"}},
    {"id": "drug:Olaparib",   "labels": ["Drug"],    "properties": {"name": "Olaparib", "mechanism": "PARP_inhibitor"}},
    {"id": "disease:BRCA",    "labels": ["Disease"], "properties": {"name": "Breast cancer"}},
    {"id": "disease:Ovarian", "labels": ["Disease"], "properties": {"name": "Ovarian cancer"}},
]
engine.bulk_create_nodes(nodes)

edges = [
    {"source_id": "gene:BRCA1",    "predicate": "TARGETS",      "target_id": "drug:Olaparib"},
    {"source_id": "drug:Olaparib", "predicate": "TREATS",        "target_id": "disease:BRCA"},
    {"source_id": "drug:Olaparib", "predicate": "TREATS",        "target_id": "disease:Ovarian"},
    {"source_id": "gene:BRCA1",    "predicate": "ASSOCIATED_WITH","target_id": "disease:BRCA"},
    {"source_id": "gene:TP53",     "predicate": "INTERACTS_WITH", "target_id": "gene:BRCA1"},
]
engine.bulk_create_edges(edges)
print(f"Loaded {len(nodes)} nodes and {len(edges)} edges.")
```

---

## Step 5: Query the graph

**Cypher — find what Olaparib treats:**
```python
result = engine.execute_cypher(
    "MATCH (d:Drug {node_id:$id})-[:TREATS]->(dis) RETURN d.name AS drug, dis.name AS disease",
    {"id": "drug:Olaparib"}
)
for row in result.rows:
    print(f"{row[0]} → {row[1]}")
```
Output:
```
Olaparib → Breast cancer
Olaparib → Ovarian cancer
```

**Find genes that target a drug:**
```python
result = engine.execute_cypher(
    "MATCH (g:Gene)-[:TARGETS]->(d:Drug) RETURN g.name AS gene, d.name AS drug"
)
print(result.rows)
```
Output:
```
[['BRCA1', 'Olaparib']]
```

**2-hop path — find diseases reachable from BRCA1:**
```python
result = engine.execute_cypher(
    "MATCH (g {node_id:$id})-[*1..2]->(target) RETURN DISTINCT target.name LIMIT 10",
    {"id": "gene:BRCA1"}
)
print([row[0] for row in result.rows])
```
Output:
```
['Olaparib', 'Breast cancer', 'Ovarian cancer']
```

---

## Step 6: Explore further

| Feature | Method | Guide |
|---|---|---|
| Vector search | `engine.ivf_build()`, `engine.search_nodes_by_vector()` | [Python SDK](../python/PYTHON_SDK.md) |
| Temporal edges | `engine.create_edge_temporal()`, `engine.get_edges_in_window()` | [Architecture](../architecture/ARCHITECTURE.md) |
| Graph analytics | `engine.run_pagerank()`, `engine.run_khop()` | [Python SDK](../python/PYTHON_SDK.md) |
| REST / Bolt API | `uvicorn api.main:app` | [Operations](../OPERATIONS.md) |
| IRIS Management Portal | http://localhost:52773 | (browser) |

---

## Stopping IRIS

```bash
docker compose down        # Stop container, keep data
docker compose down -v     # Stop and wipe all data
```
