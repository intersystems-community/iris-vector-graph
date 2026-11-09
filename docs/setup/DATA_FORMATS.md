<!-- markdownlint-disable MD013 -->

# Loading data into IVG

Every loader below writes the same rows: nodes, labels and properties in the IVG Schema's tables (`Graph_KG` by
default), relationships in `rdf_edges`. The `^KG` adjacency global follows those rows inside the same transaction, so
a query sees a write as soon as the call returns. Pick a loader by where your data comes from (the table at the end).
Signatures are in the [API Reference](../API_REFERENCE.md).

The examples run in order against one engine, opened as in the [Quickstart](QUICKSTART.md):

```python
import json

import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()["schema"]
# → 'Graph_KG'
```

---

## 1. One node or edge at a time

For application writes and small updates.

```python
engine.create_node("gene:BRCA1", labels=["Gene"], properties={"name": "BRCA1"})
# → True
engine.create_node("gene:TP53", labels=["Gene"], properties={"name": "TP53"})
# → True
engine.create_edge("gene:TP53", "INTERACTS_WITH", "gene:BRCA1", qualifiers={"source": "STRING"})
# → True
```

`create_node` returns `False`, and writes nothing, when the node is already there; `create_edge` returns `False` when
the edge is. A refused edge (for example a source and target ID pair too long for the edge index) raises
`GraphWriteError` and leaves nothing behind.

```python
engine.create_edge("gene:TP53", "INTERACTS_WITH", "gene:BRCA1")
# → False
```

`graph=` writes into a named graph. The same ID in another graph is another node:

```python
engine.create_node("gene:BRCA1", labels=["Gene"], graph="brca_study")
# → True
```

---

## 2. Batches

For initial loads and ETL. `bulk_create_nodes` returns the IDs it wrote; `bulk_create_edges` returns the number of
edges in the batch, counting any that were already there. An edge's endpoints are created if they are missing.

```python
engine.bulk_create_nodes([
    {"id": "drug:Olaparib", "labels": ["Drug"], "properties": {"name": "Olaparib", "approved": 2014}},
    {"id": "disease:BRCA", "labels": ["Disease"], "properties": {"name": "Breast cancer"}},
])
# → ['drug:Olaparib', 'disease:BRCA']
engine.get_node("drug:Olaparib")["approved"]
# → 2014
engine.bulk_create_edges([
    {"source_id": "gene:BRCA1", "predicate": "TARGETS", "target_id": "drug:Olaparib"},
    {"source_id": "drug:Olaparib", "predicate": "TREATS", "target_id": "disease:BRCA"},
])
# → 2
```

`bulk_ingest_edges` takes `(source, target)` or `(source, target, predicate)` tuples, or dicts with `s`, `o` and
optionally `p`; `predicate=` is the default type. It runs on the server, writes the same `rdf_edges` rows, and returns
the number of edges written:

```python
engine.bulk_ingest_edges(
    [("gene:TP53", "disease:BRCA"), {"s": "gene:BRCA1", "o": "disease:BRCA"}],
    predicate="ASSOCIATED_WITH",
)
# → 2
```

**Numbers come back as numbers.** `bulk_create_nodes` (like `create_node`) stores a node number as a Cypher `CREATE`
does, so the integer `2014` above reads back from `get_node()` and Cypher's `RETURN` as 2014, and a string as the
string. Before 4.6.4 the Python writers stored a number as its text, and a number they stored then still reads back
as the string `'2014'`. Filters, `ORDER BY` and arithmetic treat the string as the number
(`WHERE d.approved > 2010` matches it), and `toInteger()` or `toFloat()` in Cypher, or `int()` in Python, gives the
number back. Python's `csv` module reads every column as a string, so convert numeric columns to `int` or `float`
before the call to store numbers.
[Known issues](../KNOWN_ISSUES.md#a-property-cannot-tell-a-number-from-a-string-that-spells-it) has the details.

**Lists and maps come back as lists and maps.** A list or map property (`{"aliases": ["ERBB1", "HER1"]}`) is stored
as its JSON text, and every read decodes it: `get_node()`, Cypher's `RETURN n.aliases`, `WITH`, `collect()` and a
whole node all answer the list `['ERBB1', 'HER1']`. A string that spells a JSON list or map reads as one too
([Known issues](../KNOWN_ISSUES.md), "A stored string that spells a boolean, a list or a map reads as one").

For millions of rows, `engine.bulk_load_session()` disables and rebuilds the indexes once for the whole load instead
of once per batch (see its entry in the API Reference).

---

## 3. NDJSON

For graph snapshots and moving a graph between systems. One JSON object per line, each with a `kind`:

```jsonl
{"kind": "node", "id": "gene:EGFR", "labels": ["Gene"], "properties": {"name": "EGFR"}}
{"kind": "edge", "source": "drug:Erlotinib", "predicate": "TARGETS", "target": "gene:EGFR", "qualifiers": {"confidence": 0.95}}
{"kind": "temporal_edge", "source": "gene:EGFR", "predicate": "MEASURED", "target": "drug:Erlotinib", "timestamp": 1800000000, "weight": 0.4}
```

An edge line may carry its own `"graph"`. The ledger's reconstruction export (`{"type": "node"|"rel", ...}`) is read
too. An edge line names its endpoints `source` and `target` (or `source_id` and `target_id`). Lines that cannot be
parsed, lines of an unknown kind, and node or edge lines missing an id, source, predicate or target are logged with
their line number and skipped, and counted in `skipped`.

```python
lines = [
    {"kind": "node", "id": "gene:EGFR", "labels": ["Gene"], "properties": {"name": "EGFR"}},
    {"kind": "node", "id": "drug:Erlotinib", "labels": ["Drug"], "properties": {"name": "Erlotinib"}},
    {"kind": "edge", "source": "drug:Erlotinib", "predicate": "TARGETS", "target": "gene:EGFR",
     "qualifiers": {"confidence": 0.95}},
    {"kind": "temporal_edge", "source": "gene:EGFR", "predicate": "MEASURED", "target": "drug:Erlotinib",
     "timestamp": 1800000000, "weight": 0.4},
]
with open("egfr.ndjson", "w") as f:
    f.writelines(json.dumps(line) + "\n" for line in lines)

engine.import_graph_ndjson("egfr.ndjson")
# → {'nodes': 2, 'edges': 1, 'temporal_edges': 1, 'skipped': 0}
engine.execute_cypher("MATCH (d {node_id: 'drug:Erlotinib'})-[:TARGETS]->(g) RETURN g.name AS gene").rows
# → [['EGFR']]
```

The edge's `confidence` is a relationship property, and reads back as the number it was written as:

```python
engine.execute_cypher("MATCH ({node_id: 'drug:Erlotinib'})-[r:TARGETS]->() RETURN r.confidence AS confidence").rows
# → [[0.95]]
```

The counts are lines read, not rows created. `graph="staging"` imports every node and edge into one named graph.

Temporal edges export to the same format, and import back with `import_graph_ndjson`:

```python
engine.export_temporal_edges_ndjson("measurements.ndjson")
# → {'temporal_edges': 1}
```

`export_graph_ndjson` writes the default graph, a line per node and then a line per edge, with its qualifiers:

```python
engine.export_graph_ndjson("graph.ndjson")
# → {'nodes': 6, 'edges': 6}
with open("graph.ndjson") as f:
    lines = [json.loads(line) for line in f]
[line for line in lines if line["kind"] == "edge" and line["source"] == "drug:Erlotinib"]
# → [{'kind': 'edge', 'source': 'drug:Erlotinib', 'predicate': 'TARGETS', 'target': 'gene:EGFR', 'qualifiers': {'confidence': 0.95}}]
```

The six nodes are the four from sections 1 and 2 and the two this section imported; the six edges are the five from
sections 1 and 2 and `TARGETS`. It does not write named graphs (`brca_study`'s BRCA1) or temporal edges;
`export_temporal_edges_ndjson` writes those. To copy every graph, use `save_snapshot()` and `restore_snapshot()` (the
[User Guide](../USER_GUIDE.md#10-export-and-snapshots) shows both).

---

## 4. OBO ontologies

For the Gene Ontology, NCI Thesaurus, ChEBI and other OBO files. Needs `obonet` (`pip install obonet`). Each term is a
node, labelled by its `namespace`; each `is_a` line is an `is_a` edge from the term to its parent.

```python
obo = """format-version: 1.2

[Term]
id: GO:0005575
name: cellular_component
namespace: cellular_component

[Term]
id: GO:0005623
name: cell
namespace: cellular_component
is_a: GO:0005575

[Term]
id: GO:0005737
name: cytoplasm
namespace: cellular_component
is_a: GO:0005575
"""
with open("tiny.obo", "w") as f:
    f.write(obo)

engine.load_obo("tiny.obo")
# → {'nodes': 3, 'edges': 2, 'skipped_nodes': 0, 'skipped_edges': 0}
engine.execute_cypher(
    "MATCH (t:cellular_component)-[:is_a]->(p {node_id: 'GO:0005575'}) RETURN t.name AS term ORDER BY term"
).rows
# → [['cell'], ['cytoplasm']]
```

`prefix="go"` would store the IDs as `go:GO:0005575`. With logging configured, obonet itself logs
`WARNING:root:name and ontology keys are both missing` for a file whose header names neither, as this one; it is
about the header only, and every term is loaded.

A `relationship:` line (`part_of`, `regulates`) becomes an edge of that type. Loading the file again with one more
term skips the three terms and two edges already there:

```python
with open("part_of.obo", "w") as f:
    f.write(obo + "\n[Term]\nid: GO:0005739\nname: mitochondrion\nnamespace: cellular_component\n"
            "relationship: part_of GO:0005737\n")
engine.load_obo("part_of.obo")
# → {'nodes': 1, 'edges': 1, 'skipped_nodes': 3, 'skipped_edges': 2}
engine.execute_cypher("MATCH ({node_id: 'GO:0005739'})-[r]->(p) RETURN type(r) AS rel, p.name AS parent").rows
# → [['part_of', 'cytoplasm']]
```

---

## 5. RDF

For existing RDF in Turtle, N-Triples, N-Quads, TriG or JSON-LD. Needs the RDF extra
(`pip install 'iris-vector-graph[rdf]'`). An IRI or blank-node object becomes an edge whose type is the predicate's
IRI; a literal becomes a property of the subject, keyed by the predicate's local name. Node IDs are the full IRIs.

```python
with open("drugs.ttl", "w") as f:
    f.write("""@prefix ex: <http://example.org/> .
ex:aspirin ex:treats ex:headache .
ex:aspirin ex:name "Aspirin" .
""")

engine.import_rdf("drugs.ttl")
# → {'triples': 2, 'nodes': 2, 'edges': 1, 'properties': 1}
engine.execute_cypher(
    "MATCH (d {node_id: 'http://example.org/aspirin'})-[r]->(x) RETURN d.name AS drug, type(r) AS rel, x.node_id AS target"
).rows
# → [['Aspirin', 'http://example.org/treats', 'http://example.org/headache']]
```

The format comes from the file's extension. `graph=` imports into a named graph; in N-Quads and TriG each quad's own
graph wins. Exporting RDF is in the [Semantic Layer guide](../SEMANTIC_LAYER.md).

---

## 6. Existing SQL tables

To query rows you already have in IRIS SQL as graph nodes, without copying them. A mapping is read-only: Cypher reads
the table each time, and IVG writes nothing to its own tables for mapped labels. The tables here are made in SQL:
run the statements in the Management Portal's SQL page, or one at a time through the connection with
`conn.cursor().execute(statement)`, leaving out the trailing `;` (the Python driver refuses it).

```sql
CREATE TABLE App.Patient (PatientID VARCHAR(20) PRIMARY KEY, Name VARCHAR(100), MRN VARCHAR(20));
INSERT INTO App.Patient VALUES ('P001', 'Jane Doe', 'MRN-001');
INSERT INTO App.Patient VALUES ('P002', 'John Smith', 'MRN-002');
CREATE TABLE App.Encounter (EncounterID VARCHAR(20) PRIMARY KEY, PatientID VARCHAR(20), AdmitDate VARCHAR(20));
INSERT INTO App.Encounter VALUES ('E001', 'P001', '2026-01-15');
```

```python
engine.map_sql_table("App.Patient", "PatientID", "Patient", property_columns=["Name", "MRN"])
engine.map_sql_table("App.Encounter", "EncounterID", "Encounter")
engine.map_sql_relationship("Patient", "HAS_ENCOUNTER", "Encounter", target_fk="PatientID")

engine.execute_cypher("MATCH (p:Patient) RETURN count(p) AS n").rows
# → [[2]]
engine.execute_cypher(
    "MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) WHERE p.MRN = $mrn RETURN p.Name AS name, e.AdmitDate AS admitted",
    {"mrn": "MRN-001"},
).rows
# → [['Jane Doe', '2026-01-15']]
```

`target_fk` names the column of the target table that holds the source's ID. A many-to-many relationship goes through
a join table: `map_sql_relationship(src, pred, dst, via_table=..., via_source=..., via_target=...)`.

---

## 7. Embeddings

Vectors live in `kg_NodeEmbeddings` at the engine's `embedding_dimension` (768 here). The node must exist first.
Store a vector you computed yourself with `store_embedding`, or many at once with `store_embeddings` (keys `node_id`
and `embedding`):

```python
def vec(*head):  # a 768-wide test vector that starts with `head`, zeros after
    return list(head) + [0.0] * (768 - len(head))

engine.store_embedding("gene:BRCA1", vec(1.0, 0.0))
# → True
engine.store_embeddings([
    {"node_id": "gene:TP53", "embedding": vec(0.6, 0.8)},
    {"node_id": "drug:Olaparib", "embedding": vec(0.0, 1.0)},
])
# → True
engine.embedding_count()
# → 3
```

A second model at another width goes in its own table: pass `model_key="minilm"` to the same calls (and `graph=` for a
named graph). The [User Guide](../USER_GUIDE.md#7-text-and-vector-search) searches these vectors.

`embed_nodes` computes the vectors with a model you pass and stores them:

<!-- doc-test: skip reason="needs an embedding model (sentence-transformers downloads one on first use)" -->

```python
from sentence_transformers import SentenceTransformer

engine.embed_nodes(model=SentenceTransformer("all-mpnet-base-v2"), label="Gene")
```

---

## 8. Temporal edges

Time-stamped events between two nodes (a metric sample, a call, a measurement), many per pair. Each has a weight and
is kept in the temporal index (`^KG("tout")` and its aggregates); timestamps are Unix seconds.

```python
written = engine.bulk_create_edges_temporal([
    {"s": "gene:TP53", "p": "EXPRESSED", "o": "gene:BRCA1", "ts": 1_800_000_000, "w": 2.5},
    {"s": "gene:TP53", "p": "EXPRESSED", "o": "gene:BRCA1", "ts": 1_800_000_060, "w": 3.5},
])
int(written)
# → 2
[(e["ts"], e["w"]) for e in engine.get_edges_in_window("gene:TP53", "EXPRESSED", 1_800_000_000, 1_800_000_299)]
# → [(1800000000, 2.5), (1800000060, 3.5)]
```

`create_edge_temporal(source, predicate, target, timestamp=..., weight=..., attrs={...})` writes one. The
[User Guide](../USER_GUIDE.md#8-temporal-edges) covers windows, aggregates and retention.

---

## Node IDs

A node ID is any string up to 256 characters, compared exactly (`NCIT:C84564` and `ncit:c84564` are two nodes). IVG
imposes no scheme; a prefix per source is a common one:

| Domain        | Example IDs                             |
| ------------- | --------------------------------------- |
| Gene          | `gene:BRCA1`, `ENSG00000012048`         |
| Protein       | `uniprot:P38398`                        |
| Drug          | `drug:Olaparib`, `CHEMBL:CHEMBL2107776` |
| Disease       | `MESH:D001943`, `OMIM:604370`           |
| Ontology term | `NCIT:C84564`, `GO:0007049`             |

---

## Which loader

| Data source                             | Use                                                                             |
| --------------------------------------- | ------------------------------------------------------------------------------- |
| Application writes                      | `create_node()`, `create_edge()`                                                |
| CSV or TSV files                        | Parse in Python, then `bulk_create_nodes()` and `bulk_create_edges()`           |
| An edge list of millions of rows        | `bulk_ingest_edges()`, or `bulk_load_session()`                                 |
| An IVG NDJSON file                      | `import_graph_ndjson()`                                                         |
| OBO ontology (GO, NCI Thesaurus, ChEBI) | `load_obo()`                                                                    |
| RDF triples                             | `import_rdf()`                                                                  |
| A networkx graph                        | `load_networkx()`: labels from a node's `type`, an edge's type from `predicate` |
| Existing IRIS SQL tables                | `map_sql_table()`, `map_sql_relationship()`                                     |
| Vectors you computed                    | `store_embedding()`, `store_embeddings()`                                       |
| Time-stamped events                     | `bulk_create_edges_temporal()`                                                  |
