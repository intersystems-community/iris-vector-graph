# Semantic Layer: RDF Export, SHACL Validation, PROV-O Provenance

IVG stores all data as W3C-aligned SPO triples internally (`rdf_edges`, `rdf_props`,
`rdf_labels`). The semantic layer surfaces that storage as standard RDF, adds data
quality validation via SHACL, and exposes temporal edge provenance in W3C PROV-O.

```bash
pip install 'iris-vector-graph[rdf]'
```

---

## Why This Matters

| Problem                                                              | Solution                         |
| -------------------------------------------------------------------- | -------------------------------- |
| Share a graph with a SPARQL endpoint (Jena Fuseki, GraphDB, Stardog) | `export_rdf()` → Turtle/N-Quads  |
| Integrate with rdflib pipelines, KGX/Biolink tooling                 | `export_rdf()` → any RDF format  |
| Validate FHIR Patient nodes against HL7 R5 shapes                    | `validate_shacl("shapes.ttl")`   |
| Enforce schema constraints on ingested data                          | `validate_shacl()` before commit |
| Track what data changed, when, and from where (agentic pipelines)    | `prov_export()` → PROV-O         |
| Feed provenance to downstream tools (PROV-AGENT, Provstore)          | `prov_export()` → Turtle/JSON-LD |

---

## Part 1: RDF Export

### Basic Export

```python
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(conn, embedding_dimension=768)

# Full graph → Turtle (format inferred from extension)
result = engine.export_rdf("knowledge_graph.ttl")
print(result)
# → {"triples": 87432, "nodes": 12001, "edges": 45230, "path": "knowledge_graph.ttl"}

# N-Triples — fastest serialization, one triple per line
engine.export_rdf("graph.nt")

# N-Quads — preserves named graphs (which graph each triple belongs to)
engine.export_rdf("graph.nq")

# JSON-LD — for web APIs and Linked Data
engine.export_rdf("graph.jsonld")
```

### Format Guide

| Format    | Extension | Best For                           | Notes                            |
| --------- | --------- | ---------------------------------- | -------------------------------- |
| Turtle    | `.ttl`    | Human-readable, namespace-friendly | Compact with prefix declarations |
| N-Triples | `.nt`     | Streaming, tool compatibility      | One triple per line; no prefixes |
| N-Quads   | `.nq`     | Named graph preservation           | Extends N-Triples with graph IRI |
| JSON-LD   | `.jsonld` | Web APIs, JavaScript consumers     | Verbose but machine-friendly     |

### Filtered Exports

```python
# Only nodes with specific labels (and edges between them)
engine.export_rdf("proteins.nt", label_filter=["Protein", "Disease"])

# Only a named graph
engine.export_rdf("fhir.nq", graph_id="http://fhir.example.org/Patient")

# Only specific nodes by ID
engine.export_rdf("subset.ttl", node_ids=["Patient/001", "Patient/002", "Encounter/enc1"])
```

### From Cypher Query

Export only the subgraph returned by a Cypher query:

```python
# Returns triples for nodes and relationships in the result
engine.export_rdf_from_cypher(
    "MATCH (p:Patient)-[r]->(e:Encounter) RETURN p, r, e LIMIT 1000",
    "patients_jan.ttl",
)

# With parameters
engine.export_rdf_from_cypher(
    "MATCH (p:Patient {cohort: $c})-[r]->(e) RETURN p, r, e",
    "cohort_a.ttl",
    parameters={"c": "HFrEF"},
)
```

**Column mapping for triples**: If the query returns columns named `s`, `p`, `o`, they
map directly to subject/predicate/object. With 3 unnamed columns, the first three are
used. Single-node columns emit `rdf:type` triples.

### Namespace Prefixes

Register short prefixes so Turtle output is readable:

```python
engine.register_namespace("fhir", "http://hl7.org/fhir/")
engine.register_namespace("ex", "http://example.org/")
engine.register_namespace("prov", "http://www.w3.org/ns/prov#")

# Subsequent exports use these prefixes in Turtle output:
# @prefix fhir: <http://hl7.org/fhir/> .
# fhir:Patient/001 a fhir:Patient .
```

Prefixes persist in `Graph_KG.rdf_namespaces` across sessions.

```python
# List registered namespaces
engine.list_namespaces()
# → {"fhir": "http://hl7.org/fhir/", "ex": "http://example.org/"}
```

### What Gets Exported

Each stored element maps to RDF triples as follows:

| IVG Storage                       | RDF Output                                            |
| --------------------------------- | ----------------------------------------------------- |
| `rdf_labels(s, "Protein")`        | `<s> rdf:type <Protein>`                              |
| `rdf_props(s, "name", "ALK")`     | `<s> ex:name "ALK"^^xsd:string`                       |
| `rdf_props(s, "score", "0.9")`    | `<s> ex:score "0.9"^^xsd:decimal`                     |
| `rdf_edges(s, p, o_id)`           | `<s> <p> <o_id>`                                      |
| `rdf_edges(s, p, o_id, graph_id)` | `<s> <p> <o_id> <graph_id>` (N-Quads)                 |
| Edge with qualifiers JSON         | reifier node via `rdf:reifies` + qualifier properties |

**Node IRI minting**: Bare string IDs (e.g., `"Patient/001"`) become
`urn:ivg:Patient/001`. Valid HTTP/URN IRIs pass through unchanged.

### Custom Base URI

```python
engine.export_rdf(
    "graph.ttl",
    base_uri="http://myorg.example.com/graph/",
)
# "Patient/001" → <http://myorg.example.com/graph/Patient/001>
```

### Round-Trip Import

Exported RDF can be re-imported:

```python
engine.export_rdf("/tmp/export.ttl")
engine.import_rdf("/tmp/export.ttl", format="turtle")
```

All nodes, edges, labels, and properties survive the round-trip.

---

## Part 2: SHACL Core Validation

SHACL (Shapes Constraint Language) validates graph data against declared constraints.
IVG uses [PySHACL](https://github.com/RDFLib/pySHACL) — no JVM required.

### Basic Validation

```python
report = engine.validate_shacl("shapes/patient.shacl.ttl")

print(report.conforms)      # True / False
print(len(report.violations))

for v in report.violations:
    print(f"  Node:    {v.focus_node}")
    print(f"  Shape:   {v.shape}")
    print(f"  Message: {v.message}")
    print(f"  Severity: {v.severity}")   # "Violation" or "Warning"
    if v.path:
        print(f"  Path:    {v.path}")
    if v.value:
        print(f"  Value:   {v.value}")
```

### Shapes Sources

`shapes_source` accepts four forms:

```python
# 1. File path (Turtle or JSON-LD)
report = engine.validate_shacl("shapes/patient.shacl.ttl")

# 2. HTTP/HTTPS URL (fetched at validation time)
report = engine.validate_shacl("https://hl7.org/fhir/r5/patient.shacl.ttl")

# 3. Turtle string inline
shapes_ttl = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix fhir: <http://hl7.org/fhir/> .

fhir:PatientShape a sh:NodeShape ;
    sh:targetClass fhir:Patient ;
    sh:property [ sh:path fhir:birthDate ; sh:minCount 1 ] .
"""
report = engine.validate_shacl(shapes_ttl)

# 4. rdflib Graph object
import rdflib
g = rdflib.Graph()
g.parse("shapes.ttl")
report = engine.validate_shacl(g)
```

### Scoped Validation

Validate only a specific set of nodes (memory-efficient for large graphs):

```python
# Validate only the nodes returned by a Cypher query
node_ids = [row[0] for row in engine.execute_cypher(
    "MATCH (p:Patient) WHERE p.admitted > 1700000000 RETURN p.id"
).rows]

report = engine.validate_shacl("shapes/patient.shacl.ttl", node_ids=node_ids)
```

### Working with the Report

```python
import json

# JSON-serializable report
d = report.to_dict()
print(json.dumps(d, indent=2))
# {
#   "conforms": false,
#   "violations": [
#     {
#       "focus_node": "urn:ivg:Patient/007",
#       "shape": "http://hl7.org/fhir/PatientShape",
#       "message": "Patient must have a birthDate",
#       "severity": "Violation",
#       "path": "http://hl7.org/fhir/birthDate",
#       "value": null
#     }
#   ]
# }

# Use as boolean
if not report:
    raise ValueError(f"Data quality check failed: {len(report.violations)} violations")
```

### Writing SHACL Shapes

A minimal SHACL shapes file:

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix fhir: <http://hl7.org/fhir/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

# Shape targeting all fhir:Patient nodes
fhir:PatientShape a sh:NodeShape ;
    sh:targetClass fhir:Patient ;

    # Required: birthDate must be present
    sh:property [
        sh:path     fhir:birthDate ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
        sh:message  "Patient must have a birthDate" ;
    ] ;

    # Warning: should have an identifier
    sh:property [
        sh:path     fhir:identifier ;
        sh:minCount 1 ;
        sh:severity sh:Warning ;
        sh:message  "Patient should have an identifier" ;
    ] .
```

**Supported SHACL Core constraint components** (via PySHACL):
`sh:minCount`, `sh:maxCount`, `sh:datatype`, `sh:nodeKind`, `sh:pattern`,
`sh:minLength`, `sh:maxLength`, `sh:minInclusive/Exclusive`, `sh:maxInclusive/Exclusive`,
`sh:in`, `sh:hasValue`, `sh:class`, `sh:node`, `sh:property`,
`sh:or`, `sh:and`, `sh:not`, `sh:xone`.

### Ingestion Validation Pattern

```python
def ingest_fhir_bundle(engine, bundle: dict, shapes_path: str):
    # 1. Import the bundle
    stats = engine.import_fhir_bundle(bundle)

    # 2. Validate freshly ingested nodes
    new_node_ids = stats.get("node_ids", [])
    report = engine.validate_shacl(shapes_path, node_ids=new_node_ids)

    if not report.conforms:
        # 3a. Reject: roll back (if transaction support available)
        violations_summary = [
            f"{v.focus_node}: {v.message}" for v in report.violations
        ]
        raise ValueError("FHIR bundle failed validation:\n" + "\n".join(violations_summary))

    # 3b. Accept: proceed
    return stats
```

---

## Part 3: PROV-O Temporal Provenance

IVG's temporal edge store (`^KG("tout"/"tin")` globals) records time-indexed
relationships. The PROV-O export maps these to W3C PROV-O vocabulary without
migrating the underlying storage.

### Vocabulary Mapping

| IVG temporal edge     | PROV-O                                             |
| --------------------- | -------------------------------------------------- |
| Each temporal edge    | `prov:Activity`                                    |
| Source node           | `prov:Entity` (via `prov:used`)                    |
| Target node           | `prov:Entity`                                      |
| `ts_start` (Unix int) | `prov:startedAtTime "..."^^xsd:dateTime`           |
| `ts_end` (Unix int)   | `prov:endedAtTime "..."^^xsd:dateTime`             |
| Edge predicate URI    | property on Activity                               |
| Edge `edge_id`        | Activity IRI: `urn:ivg:activity/{url-encoded-id}`  |
| Node `node_id`        | Entity IRI: `urn:ivg:entity/{node_id}` or bare IRI |

### Basic Export

```python
# Export all temporal edges as PROV-O Turtle
result = engine.prov_export("provenance.ttl")
print(result)
# → {"activities": 1203, "entities": 890, "path": "provenance.ttl"}

# JSON-LD output
engine.prov_export("provenance.jsonld", format="json-ld")
```

### Time-Windowed Export

```python
import time

# Last 24 hours
yesterday = int(time.time()) - 86400
engine.prov_export("recent.ttl", ts_start=yesterday)

# Specific window
engine.prov_export(
    "window.ttl",
    ts_start=1700000000,
    ts_end=1700086400,
)
```

### From Cypher

Export provenance only for temporal edges related to specific query results:

```python
engine.prov_export_from_cypher(
    "MATCH (p:Patient {cohort: 'HFrEF'})-[r]->(e:Encounter) RETURN p.id",
    "hfref_prov.ttl",
)
```

### Single Edge Lookup

```python
# Get PROV-O mapping as a dict without file I/O
prov = engine.prov_as_dict(edge_id="Patient/001|admitted|Encounter/001|1700000000")
print(prov)
# {
#   "activity": "urn:ivg:activity/Patient%2F001%7Cadmitted%7CEncounter%2F001%7C1700000000",
#   "type": "prov:Activity",
#   "startedAtTime": "2023-11-14T22:13:20Z",
#   "used": "urn:ivg:entity/Patient/001",
#   "predicate": "admitted",
#   "object": "urn:ivg:entity/Encounter/001"
# }
```

### Consuming PROV-O Output

Load the export into rdflib for downstream processing:

```python
import rdflib

PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")

g = rdflib.Graph()
g.parse("provenance.ttl", format="turtle")

# Query all activities
activities = list(g.subjects(rdflib.RDF.type, PROV.Activity))
print(f"{len(activities)} activities in provenance graph")

# Get timestamps for each activity
for act in activities:
    started = next(g.objects(act, PROV.startedAtTime), None)
    ended = next(g.objects(act, PROV.endedAtTime), None)
    print(f"  {act}: {started} → {ended}")
```

### Agentic Provenance Pattern

For agentic pipelines using [PROV-AGENT](https://arxiv.org/abs/2508.02866):

```python
# After an agent processes data, export provenance of what changed
engine.prov_export("agent_run_provenance.ttl",
    ts_start=agent_run_start_time)

# Feed to a provenance store or orchestrator
# The output is standard PROV-O — compatible with any W3C PROV consumer
```

---

## Error Handling

### Missing Dependencies

If `rdflib` or `pyshacl` are not installed, all semantic layer methods raise
`ImportError` with a clear install hint:

```python
try:
    engine.export_rdf("out.ttl")
except ImportError as e:
    print(e)
    # "rdflib is required for RDF export.
    #  Install with: pip install 'iris-vector-graph[rdf]'"
```

### Unreachable Shapes URL

```python
try:
    report = engine.validate_shacl("https://unreachable.example.test/shapes.ttl")
except IOError as e:
    print(e)
    # "Could not fetch shapes from https://...: HTTP 404"
```

### Empty Graph

All methods handle an empty graph gracefully — they return a valid but empty
output file rather than raising an exception.

---

## Integration Patterns

### Export → Apache Jena Fuseki

```python
import requests

engine.export_rdf("/tmp/kg.ttl")

# Upload to a Jena Fuseki dataset
with open("/tmp/kg.ttl", "rb") as f:
    requests.post(
        "http://localhost:3030/dataset/data",
        data=f,
        headers={"Content-Type": "text/turtle"},
    )
```

### Export → Oxigraph

```python
engine.export_rdf("/tmp/kg.nt")
# oxigraph_server --location ./db --file /tmp/kg.nt
```

### SHACL → CI/CD Gate

```python
# In a test or CI script:
report = engine.validate_shacl("shapes/schema.ttl")
assert report.conforms, (
    f"Graph failed SHACL validation: {len(report.violations)} violations\n"
    + "\n".join(f"  {v.focus_node}: {v.message}" for v in report.violations)
)
```

### PROV-O → Compliance Audit

```python
import json
from datetime import datetime

engine.prov_export("audit.jsonld", format="json-ld")

# audit.jsonld is standard W3C PROV-O — submit directly to
# any PROV-aware compliance system
```

---

## Quick Reference

| Method                                                                        | Description              | Returns                                 |
| ----------------------------------------------------------------------------- | ------------------------ | --------------------------------------- |
| `engine.export_rdf(path, format, label_filter, graph_id, node_ids, base_uri)` | Export graph to RDF file | `{"triples", "nodes", "edges", "path"}` |
| `engine.export_rdf_from_cypher(query, path, parameters, format, base_uri)`    | Cypher result as RDF     | `{"triples", "path"}`                   |
| `engine.register_namespace(prefix, uri)`                                      | Persist namespace prefix | `None`                                  |
| `engine.list_namespaces()`                                                    | All registered prefixes  | `{prefix: uri}`                         |
| `engine.validate_shacl(shapes_source, node_ids)`                              | SHACL Core validation    | `ValidationReport`                      |
| `engine.prov_export(path, format, ts_start, ts_end)`                          | Temporal edges as PROV-O | `{"activities", "entities", "path"}`    |
| `engine.prov_export_from_cypher(query, path, parameters, format)`             | Scoped PROV-O export     | `{"activities", "path"}`                |
| `engine.prov_as_dict(edge_id)`                                                | Single-edge PROV-O dict  | `dict`                                  |

---

**See also**:

- [User Guide](USER_GUIDE.md) — Cypher, algorithms, bulk operations
- [RDF Landscape Analysis](rdf-landscape-spike-2026.md) — spec maturity, vendor comparison, agentic patterns
- [Admin Guide](ADMIN_GUIDE.md) — deployment, containers, production setup
