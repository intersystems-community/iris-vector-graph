<!-- markdownlint-disable MD013 -->

# RDF export, SHACL validation and PROV-O provenance

For application developers who need a graph in RDF tools: export it as Turtle, N-Triples, N-Quads or JSON-LD,
validate it against SHACL shapes, and publish temporal edges as W3C PROV-O. Signatures are in the
[API reference](API_REFERENCE.md); a runnable script is `examples/demo_rdf_semantic_layer.py`.

The three features read IVG's tables (`rdf_labels`, `rdf_props`, `rdf_edges`) and the temporal index; they write
files, not the graph. They need the `[rdf]` extra (rdflib 7 and pySHACL, no JVM):

```bash
pip install 'iris-vector-graph[rdf]'
```

rdflib 8 is not supported: the export builds an rdflib `ConjunctiveGraph`, which rdflib 8 removes
([known issue](KNOWN_ISSUES.md#the-rdf-extra-needs-rdflib-7)). Without the extra each method raises `ImportError`
naming the extra to install.

## The example graph

Every example on this page runs in order, on this graph:

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

engine.create_node("ALK", labels=["Gene"], properties={"name": "ALK"})
engine.create_node("crizotinib", labels=["Drug"], properties={"name": "crizotinib", "approved": 2011})
engine.create_edge("crizotinib", "INHIBITS", "ALK")
```

`create_node` also stores the `id` property, so `ALK` has two properties and `crizotinib` three.

## RDF export

### How IVG's rows become triples

| IVG row                                  | RDF triple                                                    |
| ---------------------------------------- | ------------------------------------------------------------- |
| label `Gene` on node `ALK`               | `<urn:ivg:ALK> rdf:type <urn:ivg:Gene>`                       |
| property `name = "ALK"`                  | `<urn:ivg:ALK> <urn:ivg:prop/name> "ALK"`                     |
| property `approved = 2011`               | `<urn:ivg:crizotinib> <urn:ivg:prop/approved> 2011` (integer) |
| edge `crizotinib -[INHIBITS]-> ALK`      | `<urn:ivg:crizotinib> <urn:ivg:INHIBITS> <urn:ivg:ALK>`       |
| any row of named graph `G` (N-Quads)     | the same triple, in the graph named `<G>`                     |
| edge with qualifiers (`{"source": "x"}`) | the triple, plus a reifier that carries each qualifier        |

- **IRIs.** A node ID, label or predicate becomes `base_uri` + the ID (`urn:ivg:` by default). One that already starts
  `http://`, `https://`, `urn:` or `ftp://` is used as it is. Property predicates are always `urn:ivg:prop/<key>`,
  whatever `base_uri` is; a key that is itself an IRI is used as it is.
- **Literals.** A value that reads as an integer is `xsd:integer`, one that Python reads as a float (`0.9`, `1e3`, and
  also `nan` and `inf`) is `xsd:decimal`, `true` and `false` are `xsd:boolean`, and anything else a plain string.
- **Qualifiers.** Each edge with qualifiers gets a blank node with `rdf:subject`, `rdf:predicate` and `rdf:object`, and
  a reifier blank node that points at it with `rdf:reifies` and carries `<urn:ivg:qualifier/<key>>` per qualifier.
  The `inferred` qualifier is left out.
- **Named graphs.** In N-Quads (or TriG) each row's triples are in its graph: a node's type and property triples, and
  an edge with its qualifier triples, are in the graph named by the graph ID as written, and the default graph's are in
  the default graph. `import_rdf` reads each quad back into its graph. A graph ID that is not an absolute IRI
  (`trials`) is written as the relative IRI `<trials>`, which N-Quads parsers, rdflib's included, refuse; give a graph
  you export to N-Quads an IRI as its ID (`http://example.org/trials`).

### Exporting

The format comes from the extension (`.ttl` Turtle, `.nt` N-Triples, `.nq` N-Quads, `.jsonld` JSON-LD, `.trig`,
`.n3`; anything else is Turtle) unless `format=` names one. The answer counts what was written: `triples`, `nodes`
(distinct subjects, blank nodes included) and `edges` (triples whose object is an IRI, `rdf:type` excepted, so a
qualified edge's `rdf:subject`, `rdf:predicate` and `rdf:object` count too):

```python
engine.export_rdf("graph.ttl")
# → {'triples': 8, 'nodes': 2, 'edges': 1, 'path': 'graph.ttl'}
```

Two labels, five properties and one edge. Read back with rdflib, `crizotinib`'s triples are:

```python
import rdflib

g = rdflib.Graph().parse("graph.ttl")
sorted((str(p), str(o)) for s, p, o in g if str(s) == "urn:ivg:crizotinib")
# → [('http://www.w3.org/1999/02/22-rdf-syntax-ns#type', 'urn:ivg:Drug'), ('urn:ivg:INHIBITS', 'urn:ivg:ALK'), ('urn:ivg:prop/approved', '2011'), ('urn:ivg:prop/id', 'crizotinib'), ('urn:ivg:prop/name', 'crizotinib')]
```

```python
str(g.value(rdflib.URIRef("urn:ivg:crizotinib"), rdflib.URIRef("urn:ivg:prop/approved")).datatype)
# → 'http://www.w3.org/2001/XMLSchema#integer'
```

### Narrowing the export

`graph_id=None` (the default) exports every graph, `graph_id=""` the default graph, and any other value that one
graph. `node_ids` keeps the labels, properties and outgoing edges of the nodes named:

```python
engine.export_rdf("crizotinib.nt", node_ids=["crizotinib"])
# → {'triples': 5, 'nodes': 1, 'edges': 1, 'path': 'crizotinib.nt'}
```

`label_filter` keeps the nodes with one of those labels: their label triples, their properties, and the edges they
start. Here that is `crizotinib`'s type, its three properties and its `INHIBITS` edge:

```python
engine.export_rdf("drugs.nt", label_filter=["Drug"])
# → {'triples': 5, 'nodes': 1, 'edges': 1, 'path': 'drugs.nt'}
```

### From a Cypher query

`export_rdf_from_cypher(query, path, parameters=None)` writes one triple per result row. Return three columns named
`s`, `p` and `o` (or any three columns, read in that order); each value becomes an IRI the way node IDs do:

```python
engine.export_rdf_from_cypher(
    "MATCH (d:Drug)-[r]->(t) RETURN d.id AS s, type(r) AS p, t.id AS o",
    "inhibits.nt",
)
# → {'triples': 1, 'path': 'inhibits.nt'}
```

A row with fewer than three columns adds `<value> rdf:type <urn:ivg:Node>` for each value that is already an IRI, and
nothing for any other value. Labels and properties are not looked up: the file holds what the query returned.

### Namespace prefixes

A registered prefix is bound in Turtle and JSON-LD output. Prefixes are rows of the IVG Schema's `rdf_namespaces`
table, so they last across sessions:

```python
engine.register_namespace("ex", "http://example.org/")
engine.list_namespaces()
# → {'ex': 'http://example.org/'}
```

### Another base IRI

`base_uri` replaces `urn:ivg:` for node IDs, labels and edge predicates (not for property predicates):

```python
engine.export_rdf("graph-ex.nt", base_uri="http://example.org/kg/")
sorted(str(o) for o in rdflib.Graph().parse("graph-ex.nt").objects(predicate=rdflib.RDF.type))
# → ['http://example.org/kg/Drug', 'http://example.org/kg/Gene']
```

### Importing RDF

`engine.import_rdf(path, graph=None)` loads any RDF file: a triple whose object is an IRI or blank node becomes an
edge, and a literal becomes a property of the subject, keyed by the predicate's local name. It is not the inverse of
`export_rdf`: re-importing an export gives nodes named by their IRIs (`urn:ivg:ALK`), and labels come back as
`rdf:type` edges, not labels.

## SHACL validation

`validate_shacl(shapes, node_ids=None)` validates the labels, properties and edges of every graph (or of the nodes in
`node_ids`) against SHACL Core shapes, with pySHACL and no inference. It reads only. Shapes are a file path, an
`http(s)` URL (fetched, 30 s timeout; an unreachable one raises `IOError`), Turtle or JSON-LD text, or an rdflib
`Graph`. Shapes name IVG's data by the IRIs above, so a shape for the label `Drug` targets `<urn:ivg:Drug>`:

```python
shapes = """
@prefix sh: <http://www.w3.org/ns/shacl#> .

<urn:ivg:shape/Drug> a sh:NodeShape ;
    sh:targetClass <urn:ivg:Drug> ;
    sh:property [
        sh:path <urn:ivg:prop/approved> ;
        sh:minCount 1 ;
        sh:message "a Drug needs an approval year" ;
    ] .
"""
engine.validate_shacl(shapes).conforms
# → True
```

A drug without `approved` breaks the shape:

```python
engine.create_node("imatinib", labels=["Drug"], properties={"name": "imatinib"})
report = engine.validate_shacl(shapes)
[(v.focus_node, v.message, v.severity) for v in report.violations]
# → [('urn:ivg:imatinib', 'a Drug needs an approval year', 'Violation')]
```

```python
(report.conforms, bool(report))
# → (False, False)
```

Each violation also has `shape`, `path` and `value`, and `report.to_dict()` gives the whole report as plain data for JSON.
`severity` is `Violation`, `Warning` or `Info`, from the shape's `sh:severity`. Validating only some nodes reads only
their triples:

```python
engine.validate_shacl(shapes, node_ids=["crizotinib"]).conforms
# → True
```

A pattern for an ingestion step that refuses bad data (IVG has no FHIR bundle importer; `load_bundle` is yours and
returns the node IDs it wrote):

<!-- doc-test: skip reason="a pattern: load_bundle and the shapes file are the reader's" -->

```python
def ingest(engine, bundle, shapes_path, load_bundle):
    new_ids = load_bundle(engine, bundle)
    report = engine.validate_shacl(shapes_path, node_ids=new_ids)
    if not report.conforms:
        raise ValueError("\n".join(f"{v.focus_node}: {v.message}" for v in report.violations))
    return new_ids
```

The validation runs after the write, so a refusal here does not undo it; roll back or erase what `load_bundle` wrote.
To read a FHIR repository as a graph without copying it, see [FHIR graph](FHIR_GRAPH.md).

## PROV-O provenance from temporal edges

`prov_export` writes the default graph's temporal edges as PROV-O, one `prov:Activity` per edge:

| Temporal edge                     | PROV-O                                                                 |
| --------------------------------- | ---------------------------------------------------------------------- |
| the edge `s -[p]-> o` at time `t` | activity `urn:ivg:activity/<s\|p\|o\|t, percent-encoded>`              |
| `t` (Unix seconds)                | `prov:startedAtTime` as `xsd:dateTime`, UTC                            |
| source `s`                        | `prov:Entity`, linked by `prov:used`; IRI `urn:ivg:entity/<s>`         |
| target `o`                        | `prov:Entity`, linked by `<urn:ivg:pred/<p>>` (or `p` if it is an IRI) |

A temporal edge has one timestamp, so no activity has `prov:endedAtTime`. Two measurements:

```python
engine.create_edge_temporal("sensor:1", "MEASURED", "room:A", timestamp=1700000000, weight=21.5)
engine.create_edge_temporal("sensor:1", "MEASURED", "room:B", timestamp=1700003600, weight=19.0)
engine.prov_export("prov.ttl")
# → {'activities': 2, 'entities': 3, 'path': 'prov.ttl'}
```

`ts_start` and `ts_end` bound the window, both inclusive; `format="json-ld"` writes JSON-LD:

```python
engine.prov_export("recent.jsonld", format="json-ld", ts_start=1700001000)
# → {'activities': 1, 'entities': 2, 'path': 'recent.jsonld'}
```

One edge as a dictionary, by its ID `source|predicate|target|timestamp`. It raises `KeyError` for an ID that is not
there:

```python
engine.prov_as_dict("sensor:1|MEASURED|room:A|1700000000")
# → {'activity': 'urn:ivg:activity/sensor%3A1%7CMEASURED%7Croom%3AA%7C1700000000', 'type': 'prov:Activity', 'startedAtTime': '2023-11-14T22:13:20Z', 'used': 'urn:ivg:entity/sensor:1', 'predicate': 'MEASURED', 'object': 'urn:ivg:entity/room:A'}
```

`prov_export_from_cypher(query, path, parameters=None)` takes every string value in the query's rows as a node ID and
exports the temporal edges that start or end at one of them:

```python
engine.prov_export_from_cypher(
    "MATCH (n {node_id: $id}) RETURN n.node_id", "room-b.ttl", parameters={"id": "room:B"}
)
# → {'activities': 1, 'path': 'room-b.ttl'}
```

Limits of the PROV-O export in 4.6.0:

- It reads the default graph only; a named graph's temporal edges are not exported.
- It reads the whole window in one call (`TemporalIndex.QueryWindow`), whose answer is one IRIS string. A window
  larger than that string can hold is logged as a warning and exported as an empty file, not raised. Export a large
  history window by window with `ts_start` and `ts_end`.
- `prov_as_dict` and `prov_export_from_cypher` read every temporal edge of the default graph to find theirs.

## Using the files

Load an export into a SPARQL store, for example Apache Jena Fuseki's upload endpoint:

<!-- doc-test: skip reason="needs an Apache Jena Fuseki server on port 3030" -->

```python
import requests

with open("graph.ttl", "rb") as f:
    requests.post("http://localhost:3030/dataset/data", data=f, headers={"Content-Type": "text/turtle"})
```

Or read the PROV-O file with rdflib:

```python
prov = rdflib.Graph().parse("prov.ttl")
PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
sorted(str(t) for t in prov.objects(predicate=PROV.startedAtTime))
# → ['2023-11-14T22:13:20+00:00', '2023-11-14T23:13:20+00:00']
```

rdflib normalizes an `xsd:dateTime` it parses, so the `Z` IVG writes reads back as `+00:00`.

## Quick reference

| Method                                                                 | Answer                                        |
| ---------------------------------------------------------------------- | --------------------------------------------- |
| `export_rdf(path, format, label_filter, graph_id, node_ids, base_uri)` | `{"triples", "nodes", "edges", "path"}`       |
| `export_rdf_from_cypher(query, path, parameters, format, base_uri)`    | `{"triples", "path"}`                         |
| `register_namespace(prefix, uri)`, `list_namespaces()`                 | `None`, `{prefix: uri}`                       |
| `import_rdf(path, format, batch_size, progress, infer, graph)`         | counts of triples, nodes, edges, props        |
| `validate_shacl(shapes, node_ids)`                                     | `ValidationReport` (`conforms`, `violations`) |
| `prov_export(path, format, ts_start, ts_end)`                          | `{"activities", "entities", "path"}`          |
| `prov_export_from_cypher(query, path, parameters, format)`             | `{"activities", "path"}`                      |
| `prov_as_dict(edge_id)`                                                | one activity as a `dict`                      |

See also the [User guide](USER_GUIDE.md) and, for contributors, the
[RDF landscape notes](dev/notes/rdf-landscape-spike-2026.md).
