# Data Formats: Loading Data into IRIS Vector Graph

## Overview

IVG supports several ingestion paths depending on data source and volume. All paths ultimately write to the same `Graph_KG` SQL tables and `^KG` global adjacency index.

---

## Ingestion Methods

### 1. Python API — individual writes

Best for: small datasets, incremental updates, application-level ingestion.

```python
# Single node
engine.create_node("gene:BRCA1", labels=["Gene"], properties={"name": "BRCA1"})

# Single edge
engine.create_edge("gene:BRCA1", "INTERACTS_WITH", "gene:TP53")

# With named graph context
engine.create_node("gene:BRCA1", labels=["Gene"], graph="brca_study")
engine.create_edge("gene:BRCA1", "TARGETS", "drug:Olaparib", graph="brca_study")
```

### 2. Bulk Python API — batch writes

Best for: initial data loads, 10K+ entities, ETL pipelines.

```python
# Bulk nodes — 5,000+ nodes/sec
created_ids = engine.bulk_create_nodes([
    {"id": "gene:BRCA1", "labels": ["Gene"], "properties": {"name": "BRCA1"}},
    {"id": "drug:Olaparib", "labels": ["Drug"], "properties": {"name": "Olaparib"}},
])

# Bulk edges — ~50K edges/sec (includes ^KG rebuild)
n = engine.bulk_create_edges([
    {"source_id": "gene:BRCA1", "predicate": "TARGETS", "target_id": "drug:Olaparib"},
])

# High-throughput direct ^KG write — 190-312K edges/sec
# NOTE: bypasses rdf_edges SQL table; call rebuild_nkg() afterward
n = engine.bulk_ingest_edges([
    {"s": "gene:BRCA1", "p": "TARGETS", "o": "drug:Olaparib"},
], predicate="TARGETS")
engine.rebuild_nkg()   # required before BFS/variable-length path queries
```

### 3. NDJSON import/export

Best for: graph snapshots, cross-system transfer, backup/restore of subgraphs.

Format: newline-delimited JSON, one record per line.

```jsonl
{"type":"node","id":"gene:BRCA1","labels":["Gene"],"props":{"name":"BRCA1"}}
{"type":"edge","s":"gene:BRCA1","p":"TARGETS","o":"drug:Olaparib","qualifiers":{"confidence":0.95}}
```

```python
# Export
engine.export_graph_ndjson("output.ndjson")
engine.export_temporal_edges_ndjson("temporal_edges.ndjson")

# Import
engine.import_graph_ndjson("output.ndjson")
```

### 4. OBO ontology files

Best for: Gene Ontology, NCI Thesaurus, ChEBI, MeSH, and any OBO-format ontology.

```python
engine.load_obo("path/to/ncit.obo")
# Nodes = ontology terms, edges = IS_A / part_of / relationship types
```

### 5. RDF import

Best for: existing RDF triples, Turtle/N-Triples format.

```python
engine.import_rdf("path/to/triples.ttl")
```

### 6. SQL table mapping

Best for: joining IVG graph queries with existing IRIS SQL tables without physically moving data.

```python
engine.map_sql_table(
    label="Patient",
    table="App.Patient",
    id_column="PatientID",
    property_columns=["Name", "DOB", "Diagnosis"]
)
engine.map_sql_relationship(
    predicate="TREATED_WITH",
    table="App.Treatment",
    source_column="PatientID",
    target_column="DrugCode"
)
# Now MATCH (p:Patient)-[:TREATED_WITH]->(d) works against live App.* tables
```

---

## Embedding Ingestion

Vector embeddings are stored in `kg_NodeEmbeddings` and indexed via HNSW (where available).

```python
# Embed nodes using a configured embedder
engine.embed_nodes(label="Gene")        # embeds all Gene nodes
engine.embed_nodes(node_ids=["gene:BRCA1", "gene:TP53"])   # specific nodes

# Store a pre-computed embedding
engine.store_embedding("gene:BRCA1", [0.1, -0.3, ...])   # list[float]

# Bulk store pre-computed embeddings
engine.store_embeddings([
    {"id": "gene:BRCA1", "embedding": [0.1, -0.3, ...]},
    {"id": "gene:TP53",  "embedding": [0.2,  0.1, ...]},
])
```

---

## Temporal Edges

Temporal edges are time-stamped and stored in `^KG("tout"/"tin")` globals alongside the structural graph.

```python
import time

engine.create_edge_temporal(
    "sensor:A", "READS", "sensor:B",
    timestamp=int(time.time()),
    weight=42.7
)

# Bulk temporal ingest
engine.bulk_create_edges_temporal([
    {"s": "sensor:A", "p": "READS", "o": "sensor:B", "ts": 1746000000, "w": 42.7},
])

# Query a time window
results = engine.get_edges_in_window("sensor:A", "READS", ts_start=1746000000, ts_end=1746003600)
```

---

## Identifier Conventions

IVG node IDs are arbitrary strings (up to 256 chars). Common patterns:

| Domain | Example IDs |
|---|---|
| Gene | `gene:BRCA1`, `ENSG00000012048` |
| Protein | `uniprot:P38398` |
| Drug | `drug:Olaparib`, `CHEMBL:CHEMBL2107776` |
| Disease | `MESH:D001943`, `OMIM:604370` |
| Ontology term | `NCIT:C84564`, `GO:0007049` |

IVG does not enforce a namespace — use whatever convention is consistent within your dataset. IDs are case-sensitive (`NCIT:C84564` ≠ `ncit:c84564`) and stored using IRIS `%EXACT` collation.

---

## Format Selection Guide

| Data Source | Recommended Method |
|---|---|
| OBO ontology (GO, NCI Thesaurus, ChEBI) | `engine.load_obo()` |
| RDF triples | `engine.import_rdf()` |
| CSV/TSV edge list | Parse in Python → `bulk_create_edges()` |
| JSONL graph snapshot | `engine.import_graph_ndjson()` |
| Existing IRIS SQL tables | `engine.map_sql_table()` / `map_sql_relationship()` |
| Pre-computed embeddings | `engine.store_embeddings()` |
| High-throughput streaming edges | `engine.bulk_ingest_edges()` + `rebuild_nkg()` |
| Temporal time-series edges | `engine.bulk_create_edges_temporal()` |
