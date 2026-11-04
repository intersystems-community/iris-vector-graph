<!-- markdownlint-disable MD013 -->

# Quickstart

From nothing to a small graph you can query, in about ten minutes, on IRIS Community Edition (free, no license). Each
step shows what it prints, so you can tell that it worked.

You need Docker, Python 3.10 or later, and a clone of this repository (for its `docker-compose.yml`).

---

## Step 1: Start IRIS

From the repository root:

```bash
docker compose up -d
docker compose ps       # wait until the iris service shows (healthy), about a minute
```

IRIS now listens on two ports on your machine:

- **1972**, the SuperServer, which the Python driver connects to;
- **52773**, the Management Portal, at <http://localhost:52773/csp/sys/UtilHome.csp>.

The user is `_SYSTEM`, password `SYS`.

If an IRIS on your machine already holds either port, choose other host ports when you start it:
`IVG_IRIS_PORT=11972 IVG_WEB_PORT=15773 docker compose up -d` (the variables default to 1972 and 52773). Then give the
SuperServer port you chose to `iris.connect` in the steps below.

Compose names the container itself (`<project>-iris-1`, for example `iris-vector-graph-iris-1`). `docker compose ps`
shows the name, the ports and the health; `docker compose exec iris iris session IRIS -U USER` opens an IRIS terminal
in it through the service name, `iris`, so you never need the container's name.

---

## Step 2: Install the library

In a virtual environment:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install iris-vector-graph
```

---

## Step 3: Connect and set up the namespace

```python
import json

import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
status = engine.initialize_schema()
print(status["schema"])
```

Output:

<!-- doc-test: output -->

```text
Graph_KG
```

`initialize_schema()` compiles IVG's ObjectScript classes into the namespace the connection is in (`USER`) and creates
its tables in the IVG Schema, `Graph_KG`. It is safe to run again; run it after every upgrade of the package. The
[README](../../README.md#where-ivg-lives-in-a-namespace) explains where everything goes.

---

## Step 4: Load a small graph

Five biomedical entities and five relationships: TP53 interacts with BRCA1, BRCA1 targets the drug Olaparib and is
associated with breast cancer, and Olaparib treats breast and ovarian cancer.

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
    {"source_id": "gene:BRCA1",    "predicate": "TARGETS",         "target_id": "drug:Olaparib"},
    {"source_id": "drug:Olaparib", "predicate": "TREATS",          "target_id": "disease:BRCA"},
    {"source_id": "drug:Olaparib", "predicate": "TREATS",          "target_id": "disease:Ovarian"},
    {"source_id": "gene:BRCA1",    "predicate": "ASSOCIATED_WITH", "target_id": "disease:BRCA"},
    {"source_id": "gene:TP53",     "predicate": "INTERACTS_WITH",  "target_id": "gene:BRCA1"},
]
engine.bulk_create_edges(edges)
print(engine.node_count(), "nodes,", engine.edge_count(), "edges")
```

Output:

<!-- doc-test: output -->

```text
5 nodes, 5 edges
```

Each node has an ID you choose (`gene:BRCA1`), any number of labels, and properties. The edges name their endpoints by
ID. Other ways to load data (NDJSON, RDF, OBO, networkx) are in [Data formats](DATA_FORMATS.md).

---

## Step 5: Query the graph with Cypher

What does Olaparib treat?

```python
result = engine.execute_cypher(
    "MATCH (d:Drug {node_id: $id})-[:TREATS]->(dis) RETURN d.name AS drug, dis.name AS disease ORDER BY disease",
    {"id": "drug:Olaparib"},
)
for drug, disease in result.rows:
    print(f"{drug} → {disease}")
```

Output:

<!-- doc-test: output -->

```text
Olaparib → Breast cancer
Olaparib → Ovarian cancer
```

`node_id` is the ID you gave the node. `$id` is a parameter, passed in the dict. `result.rows` is a list of rows, and
`result.columns` names them (`['drug', 'disease']`).

Which genes target a drug?

```python
result = engine.execute_cypher("MATCH (g:Gene)-[:TARGETS]->(d:Drug) RETURN g.name AS gene, d.name AS drug")
print(result.rows)
```

Output:

<!-- doc-test: output -->

```text
[['BRCA1', 'Olaparib']]
```

What can BRCA1 reach in one or two hops?

```python
result = engine.execute_cypher(
    "MATCH (g {node_id: $id})-[*1..2]->(target) RETURN DISTINCT target.name AS name ORDER BY name",
    {"id": "gene:BRCA1"},
)
print([row[0] for row in result.rows])
```

Output:

<!-- doc-test: output -->

```text
['Breast cancer', 'Olaparib', 'Ovarian cancer']
```

One hop reaches Olaparib and Breast cancer; two hops reach Breast cancer again (through Olaparib) and Ovarian cancer.
`DISTINCT` lists each once.

---

## Step 6: Run graph algorithms

The shortest path from TP53 to ovarian cancer:

```python
result = engine.execute_cypher(
    "MATCH p = shortestPath((a {node_id: 'gene:TP53'})-[*]->(b {node_id: 'disease:Ovarian'})) "
    "RETURN length(p) AS hops, [n IN nodes(p) | n.name] AS names"
)
hops, names = result.rows[0]
print(hops, json.loads(names))
```

Output:

<!-- doc-test: output -->

```text
3 ['TP53', 'BRCA1', 'Olaparib', 'Ovarian cancer']
```

A list in a result row comes back as JSON text, hence `json.loads`.

PageRank over the whole graph, highest first:

```python
print([(node, round(score, 3)) for node, score in engine.kg_PAGERANK()])
```

Output:

<!-- doc-test: output -->

```text
[('disease:BRCA', 0.285), ('gene:BRCA1', 0.207), ('drug:Olaparib', 0.2), ('disease:Ovarian', 0.197), ('gene:TP53', 0.112)]
```

Breast cancer ranks first: both BRCA1 and Olaparib point to it. TP53 ranks last because nothing points to it. The
scores sum to 1 (damping 0.85, 20 iterations).

---

## Next steps

| To do this                                       | Read                                                                |
| ------------------------------------------------ | ------------------------------------------------------------------- |
| Every task with examples, from loading to export | [User Guide](../USER_GUIDE.md)                                      |
| More Cypher: properties, paths, named graphs     | [User Guide: Cypher](../USER_GUIDE.md#3-query-with-cypher)          |
| Other algorithms: PPR, WCC, centrality, Leiden   | [User Guide: graph algorithms](../USER_GUIDE.md#6-graph-algorithms) |
| Vector and text search                           | [User Guide: search](../USER_GUIDE.md#7-text-and-vector-search)     |
| Time-stamped edges                               | [User Guide: temporal edges](../USER_GUIDE.md#8-temporal-edges)     |
| Every method's signature                         | [API Reference](../API_REFERENCE.md)                                |
| Run IVG in production                            | [Admin Guide](../ADMIN_GUIDE.md)                                    |

---

## Stopping IRIS

```bash
docker compose down        # stop the container, keep the data
docker compose down -v     # stop it and delete the data
```
