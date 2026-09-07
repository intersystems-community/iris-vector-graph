<!-- markdownlint-disable MD013 -->

# iris-vector-graph

**Turn InterSystems IRIS into a graph + vector database** — openCypher queries, HNSW/BM25 hybrid search, temporal property graph, RDF/SHACL/PROV-O semantic layer, and Neo4j Bolt protocol compatibility.

[![PyPI](https://img.shields.io/pypi/v/iris-vector-graph)](https://pypi.org/project/iris-vector-graph/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![IRIS 2024.1+](https://img.shields.io/badge/IRIS-2024.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Getting Started

Five minutes from zero to running graph queries.

### 1. Start IRIS

```bash
docker compose up -d
```

Starts IRIS Community Edition on `localhost:1972`. No license required.
Default credentials: `_SYSTEM` / `SYS`.

### 2. Install

```bash
pip install iris-vector-graph
```

### 3. Run your first query

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
engine.create_node("bob",   labels=["Person"], properties={"name": "Bob"})
engine.create_edge("alice", "KNOWS", "bob")

result = engine.execute_cypher(
    "MATCH (a {node_id:$id})-[:KNOWS]->(b) RETURN b.name AS name",
    {"id": "alice"}
)
print(result["rows"])  # [('Bob',)]
```

> **Note:** `initialize_schema()` prints compile warnings on Community Edition — safe to ignore.
> Enterprise-only classes (`Graph.KG.MCPService`, `Graph.KG.MCPToolSet`) are not required.

---

## What It Does

| Feature                     | Notes                                                                                                |
| --------------------------- | ---------------------------------------------------------------------------------------------------- |
| **openCypher**              | `MATCH`, `CREATE`, `MERGE`, `DELETE`, `WITH`, `UNWIND`, variable-length paths, subqueries            |
| **Temporal property graph** | Time-windowed edges, pre-aggregated bucket analytics, O(1) window queries                            |
| **Vector search**           | HNSW (native IRIS VECTOR), IVFFlat, PLAID multi-vector, BM25 full-text                               |
| **Graph analytics**         | Betweenness, closeness, eigenvector, degree centrality; Leiden community detection; SCC; k-core; PPR |
| **Shortest path**           | Unweighted BFS (`shortestPath`), weighted Dijkstra (`ivg.shortestPath.weighted`)                     |
| **NKG fast-path**           | `[*1..N]` Cypher patterns route to integer-keyed `^NKG` index, bypassing SQL translation             |
| **Bulk loader**             | 190–312K edges/s direct `^KG` write; incremental `^NKG` rebuild                                      |
| **FHIR bridge**             | ICD-10 → knowledge graph mapping via FHIR R4                                                         |
| **Bolt protocol**           | neo4j-driver compatible wire protocol (TCP + WebSocket)                                              |
| **Embedded Python**         | Graph algorithms run server-side via IRIS embedded Python (igraph, leidenalg)                        |
| **IPM / ZPM**               | ObjectScript-only install via InterSystems Package Manager                                           |
| **RDF export**              | `export_rdf()` — full or filtered graph to Turtle/NT/NQuads/JSON-LD                                  |
| **SHACL validation**        | `validate_shacl()` — SHACL Core via PySHACL; `ValidationReport` dataclass                            |
| **PROV-O**                  | `prov_export()` — temporal edges as W3C PROV-O provenance graph                                      |

---

## Performance

Hardware: M3 Ultra, Community IRIS 2026.1, ARM64 Docker.

### Query latency

| Query                                    | Latency   | Notes                                  |
| ---------------------------------------- | --------- | -------------------------------------- |
| 1-hop neighbor lookup                    | ~0.4ms    | `$Order` on `^KG`                      |
| NKG fast-path `[*1..N]`, hops 2–5        | 1.4–2.0ms | **4.9–13.4x faster than SQL path**     |
| IC3 2-hop with LIMIT (LDBC SF10)         | 1.2ms     | 3.5x faster than GES/GraphScope        |
| IC13 shortest path (LDBC SF10)           | 2.1–3.2ms | Comparable to GES at SF1000 on cluster |
| HNSW vector search (768-dim)             | 1.7ms     | Native IRIS VECTOR index               |
| BM25 full-text (174 nodes, 3-term)       | 0.3ms     | Posting-list `$Order`                  |
| Temporal window query                    | 0.1ms     | O(results), B-tree                     |
| Pre-aggregated bucket (24hr/288 buckets) | 0.16ms    | O(buckets), not O(edges)               |

### Algorithm comparison (vs Neo4j GDS and networkx)

IVG is competitive with or faster than Neo4j GDS on degree centrality, betweenness, and Leiden
community detection, producing results identical to networkx (Pearson r = 1.0).
Validated on DRKG biomedical KG (~97K nodes / ~5.9M edges).

Full methodology and numbers: [docs/performance/BENCHMARKS.md](docs/performance/BENCHMARKS.md)
and [docs/performance/GRAPH_ALGORITHMS.md](docs/performance/GRAPH_ALGORITHMS.md).

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    iris-vector-graph  v2.18.0                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌───────────────┐   ┌───────────────┐   ┌───────────────────┐    │
│   │  Python SDK   │   │  Cypher/AQL   │   │   Bolt (wire)     │    │
│   │  IRISGraph    │   │  translator   │   │   neo4j-driver    │    │
│   │  Engine       │   │  + executor   │   │   compatible      │    │
│   └───────┬───────┘   └───────┬───────┘   └────────┬──────────┘    │
│           └──────────────┬────┘                    │               │
│                          ▼                          │               │
│             ┌────────────────────────┐              │               │
│             │   GraphStore protocol  │◄─────────────┘               │
│             │   (pluggable backend)  │                              │
│             └───────────┬────────────┘                              │
│                         │                                           │
│          ┌──────────────┼──────────────┐                           │
│          ▼              ▼              ▼                            │
│   ┌─────────────┐ ┌──────────┐ ┌───────────────┐                  │
│   │  SQL layer  │ │  ^KG     │ │  ^NKG         │                  │
│   │  Graph_KG.* │ │  globals │ │  integer adj  │                  │
│   │  (nodes,    │ │  (edges, │ │  index        │                  │
│   │   edges,    │ │   temp,  │ └───────┬───────┘                  │
│   │   vectors)  │ │   PPR)   │         │                          │
│   └─────────────┘ └──────────┘         │                          │
│                                         ▼                          │
│                              ┌────────────────────┐               │
│                              │  Algorithm tiers   │               │
│                              ├────────────────────┤               │
│                              │ 1. Rust accelerator│ ← fastest     │
│                              │    (rayon parallel)│               │
│                              │ 2. ObjectScript    │               │
│                              │    parallel 8×     │               │
│                              │ 3. Python LazyKG   │ ← always works│
│                              └────────────────────┘               │
│                                                                     │
│   Centrality:  betweenness (Brandes) · closeness · eigenvector     │
│                degree                                              │
│   Community:   Leiden · triangle count · SCC · k-core             │
│   Search:      vector (HNSW/IVF/PLAID) · BM25 · temporal · PPR   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

Full schema and ObjectScript class reference: [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

---

## Semantic Layer (RDF / SHACL / PROV-O)

```bash
pip install 'iris-vector-graph[rdf]'
```

```python
# Export graph as Turtle (full or filtered)
engine.export_rdf("kg.ttl")
engine.export_rdf("proteins.nt", format="nt", label_filter=["Protein", "Disease"])
engine.export_rdf_from_cypher("MATCH (p:Patient)-[r]->(e) RETURN p,r,e", "patients.ttl")

# Persistent namespace prefixes
engine.register_namespace("fhir", "http://hl7.org/fhir/")

# SHACL Core validation
report = engine.validate_shacl("shapes/patient.ttl")
if not report.conforms:
    for v in report.violations:
        print(f"{v.focus_node}: {v.message} [{v.severity}]")

# PROV-O temporal provenance
engine.prov_export("provenance.ttl", ts_start=1700000000)
prov = engine.prov_as_dict(edge_id=42)
```

Every write to ivg is stored as W3C-aligned SPO triples (`rdf_edges`, `rdf_props`, `rdf_labels`)
with OWL 2 RL inference, named graph support, and RDF-star style edge qualifiers.
See [docs/SEMANTIC_LAYER.md](docs/SEMANTIC_LAYER.md) for the full guide: format
reference, SHACL shape writing, PROV-O vocabulary mapping, and integration patterns.

---

## Named Graphs

Scope nodes and edges to a named graph — useful for multi-tenant data, staging
vs. production snapshots, or materializing a ledger reconstruction into an
isolated subgraph.

```python
# Create a node in the "umls" named graph
engine.create_node("C0027051", labels=["Concept"], graph="umls")

# Create an edge in the same named graph
engine.create_edge("C0027051", "ISA", "C0085580", graph="umls")

# Cypher: USE GRAPH targets the context for CREATE and MERGE
engine.execute_cypher("USE GRAPH umls CREATE (n:Concept {id: 'C0001234'})")

# Import an NDJSON file into a named graph
engine.import_graph_ndjson("export.ndjson", graph="staging")

# Drop everything scoped to a named graph (nodes, edges, labels, props)
engine.drop_graph("staging")

# delete_edge defaults to the default graph; pass all_graphs=True to sweep all
engine.delete_edge("C0027051", "ISA", "C0085580", graph="umls")
engine.delete_edge("C0027051", "ISA", "C0085580", all_graphs=True)
```

Nodes in the default graph use `graph=""` (empty-string sentinel); the BFS
adjacency index (`^KG`) partitions by graph key so traversals stay within
their graph. Existing code that never passes `graph=` is unaffected.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md#named-graphs) for the full guide.

---

## Revision Ledger (Transaction-Time History)

The structural graph (nodes, labels, properties, relationships, qualifiers) can
be versioned with an opt-in, immutable revision ledger. A changeset applies
atomically and produces exactly one revision; a failed operation applies
nothing. This is **transaction time** (when the ledger recorded a change) and is
independent of the temporal property graph's **event time** (`ts` on temporal
edges), which is unchanged and unversioned.

```python
from iris_vector_graph.ledger import Changeset

genesis = engine.ledger.enable()              # idempotent; captures the existing graph
head = engine.ledger.head()

cs = Changeset(actor="ingest:etl-42", actor_type="ingest",
               message="equipment sync",
               expected_head=head.revision_id,      # optimistic concurrency
               idempotency_key="etl-42-2026-09-05")  # safe retry
cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
cs.create_node("tank-2")
r = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"weight": "1.0"})
cs.set_qualifier(r, "capacity", "100")
result = engine.ledger.commit(cs)              # StaleHeadError if another writer won

engine.ledger.history(limit=20)                # deterministic, pageable, filterable
engine.ledger.get_revision(result.revision.revision_id).records
engine.ledger.diff(genesis.revision_id, result.revision.revision_id)
engine.ledger.reconstruct(genesis.revision_id) # read-only graph as of a revision
engine.ledger.export_reconstruction(result.revision.revision_id, "at-rev.ndjson")
engine.ledger.verify()                         # replay from genesis == current tables?
engine.ledger.verify(adopt=True)               # record any unrecorded (legacy) writes
```

Existing APIs keep working with a ledger enabled; writes made through them are
"unrecorded" and are surfaced by `verify()`. `enable(strict=True)` (or
`set_strict(True)`) rejects non-ledger structural writes with
`LedgerStrictModeError`; reads, temporal writes, and index maintenance are
unaffected. Relationships get a stable **statement identity** that survives
qualifier changes and snapshot restore. Counters are in
`engine.status().ledger`, the `Graph_KG.ledger_stats` table, and a metrics hook
(`engine.ledger.register_metrics_hook`, see
[docs/ledger-prometheus-hook.md](docs/ledger-prometheus-hook.md)).

---

## Non-USER Namespace Deployment

By default IVG connects to the `USER` namespace. When deploying against a
HealthShare, Ensemble, or multi-tenant IRIS instance where data lives in a
different namespace, pass `namespace=` at engine construction:

```python
from iris_vector_graph import IRISGraphEngine
import iris.dbapi as dbapi

conn = dbapi.connect(hostname="...", port=1972, namespace="HSCUSTOM", ...)
engine = IRISGraphEngine(conn, namespace="HSCUSTOM")
```

On first use, the engine probes `$Data(^KG("deg"))` to verify the `^KG`
globals are accessible. If they are absent a `WARNING` is logged naming the
namespace and the fix. Set `IVG_STRICT_NAMESPACE=1` to upgrade the warning
to a raised `NamespaceMismatchWarning`.

### CPF global mapping

If your graph data lives in a separate database, map the `^KG` global in the
IRIS CPF file so the probe passes without copying data:

```ini
[Map.HSCUSTOM]
Global=^KG,Directory=/db/IRISLOCALDATA/
```

After mapping, `$Data(^KG("deg"))` returns non-zero in `HSCUSTOM` and no
warning is emitted.

### Env var controls

| Env var                        | Effect                                                        |
| ------------------------------ | ------------------------------------------------------------- |
| _(neither)_                    | `logging.warning` on mismatch, execution continues            |
| `IVG_STRICT_NAMESPACE=1`       | `logging.warning` + raises `NamespaceMismatchWarning`         |
| `IVG_IGNORE_NAMESPACE_CHECK=1` | Skip probe entirely (HealthShare platform-managed namespaces) |

`IVG_IGNORE_NAMESPACE_CHECK=1` takes precedence over `IVG_STRICT_NAMESPACE=1`.

---

## Documentation

| Document                                                 | Contents                                                             |
| -------------------------------------------------------- | -------------------------------------------------------------------- |
| [User Guide](docs/USER_GUIDE.md)                         | Cypher examples, named graphs, ledger, temporal edges, vector search |
| [Admin Guide](docs/ADMIN_GUIDE.md)                       | Container setup, schema management, index rebuilding                 |
| [Admin API](docs/ADMIN_API.md)                           | Python API reference for engine administration                       |
| [Benchmarks](docs/performance/BENCHMARKS.md)             | Full methodology, LDBC SNB results, ingestion throughput             |
| [Graph Algorithms](docs/performance/GRAPH_ALGORITHMS.md) | Centrality and community detection benchmark details                 |
| [Semantic Layer](docs/SEMANTIC_LAYER.md)                 | RDF export, SHACL validation, PROV-O provenance                      |
| [Changelog](CHANGELOG.md)                                | Full version history                                                 |

---

## AI Agent Development

When building agents that use IVG as a knowledge graph backend, install the
[iris-agentic-dev](https://github.com/intersystems-community/iris-agentic-dev) MCP
toolkit for ObjectScript compilation, global inspection, and IRIS method invocation
from your agent's tool loop:

```bash
pip install iris-agentic-dev
```

This gives your agent `iris_compile`, `iris_execute`, `iris_global`, and related tools
that work alongside IVG's Python API.

Skill files with agent-oriented quickstarts:

- [`skills/iris-vector-graph/SKILL.md`](skills/iris-vector-graph/SKILL.md) — IVG API,
  temporal edges, SHACL, globals, container setup, common gotchas
- [`skills/ivg-arno/SKILL.md`](skills/ivg-arno/SKILL.md) — Arno Rust acceleration,
  BFS at scale, enterprise container, ASQ query syntax

---

## License

MIT. See [LICENSE](LICENSE).
