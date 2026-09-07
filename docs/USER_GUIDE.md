# iris-vector-graph User Guide

**For developers building applications with IVG. Deployment docs live in the Admin Guide.**

---

## 1. Connection & Setup

### Connect to IRIS

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
```

### Inside IRIS (Embedded Python)

```python
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(EmbeddedConnection(), embedding_dimension=768)
engine.initialize_schema()
```

### When to Rebuild

```python
# Call rebuild_nkg() after bulk_ingest_edges() to rebuild the ^NKG integer index
engine.bulk_ingest_edges([...])
engine.rebuild_nkg()

# Check index status anytime
status = engine.status()
if not status.ready_for_bfs and status.tables.edges > 0:
    engine.rebuild_nkg()
```

**`rebuild_kg()`**: rebuilds SQL-backed `^KG` globals (used by graph algorithms). Required after large edge bulk ingests.

**`rebuild_nkg()`**: rebuilds the integer-indexed `^NKG` adjacency for algorithm acceleration. Call after `bulk_ingest_edges()` to activate Rust accelerator paths.

---

## 2. Graph Mutation

### Create Nodes

```python
engine.create_node("gene:TP53", labels=["Gene"], properties={"name": "TP53", "type": "tumor_suppressor"})
engine.create_node("MESH:D003924", labels=["Disease"], properties={"name": "Diabetes"})
```

### Create Edges

```python
# Structural edge (immediate write to ^KG)
engine.create_edge(
    source_id="gene:TP53",
    predicate="ASSOCIATED_WITH",
    target_id="MESH:D009101",
    qualifiers={"confidence": 0.92}
)

# Temporal edge (event log)
import time
engine.create_edge_temporal(
    source="service:auth",
    predicate="CALLS",
    target="service:payment",
    timestamp=int(time.time()),
    weight=42.7  # latency_ms, metric value, etc
)
```

### Bulk Operations

```python
# Structural edges — requires rebuild_nkg() after
edges = [
    {"s": "gene:TP53", "p": "BINDS", "o": "drug:doxorubicin", "qualifiers": {"Kd": 1e-9}},
    {"s": "gene:TP53", "p": "BINDS", "o": "drug:paclitaxel", "qualifiers": {"Kd": 2e-8}},
]
engine.bulk_ingest_edges(edges)
engine.rebuild_nkg()

# Temporal edges — writes ^KG immediately
temporal_edges = [
    {"s": "svc:auth", "p": "CALLS_AT", "o": "svc:pay", "ts": 1712000000, "w": 42.7},
    {"s": "svc:pay", "p": "CALLS_AT", "o": "svc:db", "ts": 1712000001, "w": 8.1},
]
engine.bulk_create_edges_temporal(temporal_edges)
```

### Delete

```python
engine.delete_edge("service:auth", "CALLS", "service:payment")
```

### Named Graphs

Scope nodes and edges to a named graph for multi-tenant data, staging
snapshots, or materializing a ledger reconstruction into an isolated subgraph.
The default graph uses `graph=""` (empty-string sentinel); named graphs use any
non-empty string.

```python
# Nodes
engine.create_node("C0027051", labels=["Concept"], graph="umls")

# Structural edges
engine.create_edge("C0027051", "ISA", "C0085580", graph="umls")

# Cypher CREATE/MERGE respects USE GRAPH context
engine.execute_cypher("USE GRAPH umls CREATE (n:Concept {id: 'C0001234'})")

# Import an NDJSON snapshot into a named graph
engine.import_graph_ndjson("export.ndjson", graph="staging")

# Delete an edge scoped to a named graph
engine.delete_edge("C0027051", "ISA", "C0085580", graph="umls")

# Delete an edge regardless of which graph it belongs to
engine.delete_edge("C0027051", "ISA", "C0085580", all_graphs=True)

# Drop all nodes and edges in a named graph (FK-safe order)
engine.drop_graph("staging")
```

The `^KG` adjacency index partitions by graph key so BFS and variable-length
paths stay within their graph. Existing callers that never pass `graph=` are
unaffected — their data lands in the default graph.

---

## 3. Cypher Queries

### Basic Pattern Matching

```python
result = engine.execute_cypher(
    "MATCH (a:Gene)-[:ASSOCIATED_WITH]->(d:Disease) RETURN a.name, d.name LIMIT 10"
)
print(result.columns)  # ["a.name", "d.name"]
print(result.rows)     # [("TP53", "Lung Cancer"), ...]
```

### Parameters

```python
result = engine.execute_cypher(
    "MATCH (a {node_id: $id})-[:BINDS]->(d) RETURN d.name AS drug",
    {"id": "gene:TP53"}
)
```

### Variable-Length Paths

```python
# 1–3 hops from source to target
result = engine.execute_cypher(
    "MATCH p = (a {node_id: 'gene:TP53'})-[:*1..3]-(b:Drug) RETURN b.node_id, length(p) AS hops"
)
```

### Temporal Filtering

```python
now = int(time.time())
result = engine.execute_cypher(
    "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN a.node_id, b.node_id, r.weight ORDER BY r.ts DESC",
    {"start": now - 300, "end": now}
)
```

### AQL (ArangoDB Query Language)

```python
result = engine.execute_aql(
    "FOR v IN 1..2 OUTBOUND @s g RETURN v._key",
    bind_vars={"s": "gene:TP53"}
)
```

---

## 4. Centrality Algorithms

### Degree Centrality

**What it does**: Counts edges per node. Fast baseline for hub identification.

```python
scores = engine.degree_centrality(direction="out", top_k=20)
# [{"id": "hub-gene", "score": 0.847, "degree": 12}, ...]
```

**Return format**:

| Key      | Type  | Description                       |
| -------- | ----- | --------------------------------- |
| `id`     | str   | Node identifier                   |
| `score`  | float | Normalized degree (value / (n-1)) |
| `degree` | int   | Raw edge count                    |

**Cypher**:

```cypher
CALL ivg.degreeCentrality({direction: "out", topK: 20}) YIELD node, score, degree
```

### Betweenness Centrality

**What it does**: Identifies bottleneck nodes that control information flow (Brandes 2001).

```python
scores = engine.betweenness_centrality(sample_size=200, top_k=20)
# [{"id": "hub-gene", "score": 4821.3}, ...]

# Exact computation (slower)
scores_exact = engine.betweenness_centrality(sample_size=0, top_k=20)

# Neighborhood betweenness (biomedical use case)
scores = engine.betweenness_centrality_neighborhood(
    seed="MESH:D009101",  # Multiple Myeloma
    hops=2,               # 2-hop neighborhood
    sample_size=200,
    top_k=20
)
# [{"id": "TP53", "score": 1234.5}, ...]
```

**Return format**:

| Key     | Type  | Description                                              |
| ------- | ----- | -------------------------------------------------------- |
| `id`    | str   | Node identifier                                          |
| `score` | float | Betweenness score (scaled by sampling factor if sampled) |

**Cypher**:

```cypher
CALL ivg.betweenness({sampleSize: 200, topK: 20}) YIELD node, score
```

### Betweenness Neighborhood (Biomedical)

**Sweet spot**: 10M-node graph with a 5K-node disease neighborhood runs in ~10ms. Scales to neighborhood size, not total KG size.

```python
# Find bottleneck genes between Multiple Myeloma and its drug targets
bottlenecks = engine.betweenness_centrality_neighborhood(
    seed="MESH:D009101",
    hops=2,
    top_k=10
)

# Returns nodes within the neighborhood, ranked by influence in that subgraph
for node in bottlenecks:
    print(f"{node['id']}: {node['score']}")  # TP53, KRAS, etc.
```

### Closeness Centrality

**What it does**: How quickly can a node reach others via shortest paths?

```python
scores = engine.closeness_centrality(formula="harmonic", top_k=20)
# [{"id": "central-node", "score": 0.823}, ...]

# Classical formula (undefined for disconnected graphs)
scores = engine.closeness_centrality(formula="classical", top_k=20)
```

**Return format**:

| Key     | Type  | Description                                    |
| ------- | ----- | ---------------------------------------------- |
| `id`    | str   | Node identifier                                |
| `score` | float | Closeness (harmonic or classical, per formula) |

**Cypher**:

```cypher
CALL ivg.closeness({formula: "harmonic", topK: 20}) YIELD node, score
```

### Eigenvector Centrality

**What it does**: Prestige: a node is influential if connected to other influential nodes.

```python
scores = engine.eigenvector_centrality(max_iter=30, top_k=20)
# [{"id": "prestigious-gene", "score": 0.894}, ...]
```

**Return format**:

| Key     | Type  | Description                               |
| ------- | ----- | ----------------------------------------- |
| `id`    | str   | Node identifier                           |
| `score` | float | L2-normalized eigenvector component (0–1) |

**Cypher**:

```cypher
CALL ivg.eigenvector({maxIter: 50, topK: 20}) YIELD node, score
```

---

## 5. Community Algorithms

### Leiden Community Detection

```python
communities = engine.leiden_communities(gamma=1.0, top_k=100)
# [{"id": "gene1", "community": 0, "size": 45}, ...]

# Smaller communities (resolution parameter)
small_comms = engine.leiden_communities(gamma=0.5, top_k=100)
```

### Triangle Count

```python
triangles = engine.triangle_count(top_k=100)
# [{"id": "hub", "triangles": 45, "lcc": 0.73}, ...]
```

### Strongly Connected Components

```python
sccs = engine.strongly_connected_components(top_k=100)
# [{"id": "gene", "component": 0, "size": 8}, ...]
```

### K-Core Decomposition

```python
cores = engine.k_core_decomposition(top_k=100)
# [{"id": "dense-hub", "coreness": 5}, ...]
```

---

## 6. Error Handling

### NKG Not Built

When `^NKG` hasn't been built:

```python
result = engine.betweenness_centrality(sample_size=200)
# Returns [] if ^NKG missing, emits warning
# Falls back to Python LazyKG (slow)
```

**Solution**: Call `engine.rebuild_nkg()` after data loads.

### Seed Not Found

```python
scores = engine.betweenness_centrality_neighborhood(seed="MISSING_NODE", hops=2)
# Returns []
```

### Connection Drops

```python
try:
    result = engine.execute_cypher("MATCH (n) RETURN count(n)")
except Exception as e:
    logger.error(f"Connection lost: {e}")
    conn = iris.connect(...)
    engine = IRISGraphEngine(conn, embedding_dimension=768)
```

---

## 7. Performance Tiers

Three-tier dispatch for all graph algorithms:

| Tier | Backend                                              | Latency (ER 2000) |
| ---- | ---------------------------------------------------- | ----------------- |
| 1    | **Rust accelerator** (if deployed + `^NKG` built)    | ~8ms              |
| 2    | **ObjectScript parallel** (8× workers, `^NKG` built) | ~500ms            |
| 3    | **Python LazyKG** (always works, `^NKG` not needed)  | slow              |

Dispatch is automatic and transparent. See [performance/GRAPH_ALGORITHMS.md](performance/GRAPH_ALGORITHMS.md) for detailed benchmarks.

---

## 8. Vector & Text Search

### Vector Search

```python
# Find 10 nearest neighbors to a gene embedding
results = engine.vector_search(
    table="kg_NodeEmbeddings",
    vector_col="embedding",
    query_embedding=my_vector,
    top_k=10,
    id_col="node_id"
)
# [{"id": "gene:BRCA1", "score": 0.95}, ...]
```

### BM25 Lexical Search

```python
# Build index on node names
engine.bm25_build("drug_index", props="name,description")

# Search
results = engine.bm25_search("drug_index", "insulin resistance", k=10)
# [{"id": "drug:metformin", "score": 8.43}, ...]
```

### Cypher Integration

```cypher
-- Vector search in MATCH
CALL ivg.ivf.search('kg_idx', $query_vec, 10, 32) YIELD node, score
RETURN node, score ORDER BY score DESC

-- BM25 in MATCH
CALL ivg.bm25.search('drug_index', 'insulin resistance', 10) YIELD node, score
RETURN node, score ORDER BY score DESC LIMIT 5
```

---

## 9. Semantic Layer (RDF / SHACL / PROV-O)

IVG stores all data as W3C-aligned SPO triples. The semantic layer lets you get
that data back out as standard RDF, validate it against SHACL shapes, and export
temporal edge provenance in W3C PROV-O.

```bash
pip install 'iris-vector-graph[rdf]'
```

```python
# Export graph as Turtle (full or filtered)
engine.export_rdf("graph.ttl")
engine.export_rdf("proteins.nt", label_filter=["Protein", "Disease"])
engine.export_rdf_from_cypher("MATCH (p:Patient)-[r]->(e) RETURN p,r,e", "sub.ttl")

# Register namespace prefixes for readable Turtle output
engine.register_namespace("fhir", "http://hl7.org/fhir/")

# Validate with SHACL shapes
report = engine.validate_shacl("shapes/patient.shacl.ttl")
if not report.conforms:
    for v in report.violations:
        print(f"{v.focus_node}: {v.message} [{v.severity}]")

# Export temporal edge provenance as PROV-O
engine.prov_export("provenance.ttl", ts_start=1700000000)
prov = engine.prov_as_dict(edge_id=42)
```

**Full documentation**: [SEMANTIC_LAYER.md](SEMANTIC_LAYER.md) — includes format guide,
SHACL shape writing, PROV-O vocabulary mapping, and integration patterns.

---

## 10. Revision Ledger

The ledger provides opt-in, immutable transaction-time history. Each `commit`
applies atomically or not at all — no partial writes, no silent overwrites.

### Enable and commit

```python
from iris_vector_graph.ledger import Changeset

# Enable once; idempotent — captures existing graph as genesis revision
genesis = engine.ledger.enable()
print(genesis.revision_id)  # "a3f9..." — stable across restarts

# Build a changeset
head = engine.ledger.head()
cs = Changeset(
    actor="ingest:etl-42",
    actor_type="ingest",
    message="equipment sync — batch 2026-09-07",
    expected_head=head.revision_id,      # optimistic concurrency lock
    idempotency_key="etl-42-2026-09-07", # safe to retry on network error
)
cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
cs.create_node("tank-2")
rel = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"weight": "1.0"})
cs.set_qualifier(rel, "capacity", "100")
cs.set_property("pump-7", "rated_kw", "15")

result = engine.ledger.commit(cs)
print(result.revision.seq)  # monotonically increasing sequence number
```

If another writer commits between `head()` and `commit()`, a `StaleHeadError`
is raised and nothing is written. Retry by re-reading `head()`.

### History and diffs

```python
# Page through revisions (newest first)
for rev in engine.ledger.history(limit=20):
    print(rev.seq, rev.actor, rev.message, rev.committed_ms)

# Full mutation records for one revision
rev = engine.ledger.get_revision(result.revision.revision_id)
for record in rev.records:
    print(record.op, record.entity_kind, record.entity_id)

# Diff between two revisions (what changed?)
changes = engine.ledger.diff(genesis.revision_id, result.revision.revision_id)
for change in changes:
    print(change.op, change.entity_id, change.prior, change.new)
```

### Historical reconstruction

```python
# Read-only graph as of a past revision — returns an IVGResult of NDJSON
snapshot = engine.ledger.reconstruct(genesis.revision_id)

# Export reconstruction to an NDJSON file (import into a named graph later)
engine.ledger.export_reconstruction(result.revision.revision_id, "at-rev.ndjson")

# Verify current tables match the replay from genesis
report = engine.ledger.verify()
print(report.consistent)          # True if tables == replay
print(report.unrecorded_writes)   # writes that bypassed the ledger

# Adopt unrecorded writes into the ledger history
engine.ledger.verify(adopt=True)
```

### Concurrent writers — branch conflict

```python
import threading
from iris_vector_graph.ledger import Changeset, StaleHeadError

def writer(name: str, conn):
    eng = IRISGraphEngine(conn)
    head = eng.ledger.head()
    cs = Changeset(actor=name, actor_type="test",
                   expected_head=head.revision_id)
    cs.create_node(f"node-{name}")
    try:
        r = eng.ledger.commit(cs)
        print(f"{name} won at seq={r.revision.seq}")
    except StaleHeadError:
        # Another writer committed first — re-read head and retry
        head = eng.ledger.head()
        cs2 = Changeset(actor=name, actor_type="test",
                        expected_head=head.revision_id)
        cs2.create_node(f"node-{name}")
        r = eng.ledger.commit(cs2)
        print(f"{name} retried at seq={r.revision.seq}")

threads = [threading.Thread(target=writer, args=(f"writer-{i}", conn)) for i in range(3)]
for t in threads: t.start()
for t in threads: t.join()
```

### Strict mode

Strict mode rejects structural writes that bypass the ledger (`create_node`,
`create_edge`, etc.) with `LedgerStrictModeError`. Reads, temporal writes, and
index maintenance are unaffected.

```python
engine.ledger.set_strict(True)   # or enable(strict=True) at first enable
try:
    engine.create_node("bypass")   # raises LedgerStrictModeError
except Exception as e:
    print(e)                        # must use ledger.commit(Changeset(...))
```

### Metrics

```python
stats = engine.ledger.stats()
print(stats.head_seq, stats.commits_ok, stats.rejections)

engine.status().ledger   # included in the engine status report
```

For Prometheus integration see
[docs/ledger-prometheus-hook.md](ledger-prometheus-hook.md).

---

## Quick Reference

| Task                     | Code                                                             |
| ------------------------ | ---------------------------------------------------------------- |
| Initialize               | `engine.initialize_schema()`                                     |
| Add node                 | `engine.create_node("id", labels=[...], properties={...})`       |
| Add node (named graph)   | `engine.create_node("id", graph="umls")`                         |
| Add edge                 | `engine.create_edge("src", "pred", "tgt", qualifiers={...})`     |
| Add edge (named graph)   | `engine.create_edge("src", "pred", "tgt", graph="umls")`         |
| Drop named graph         | `engine.drop_graph("staging")`                                   |
| Query                    | `engine.execute_cypher("MATCH (n) RETURN n.name LIMIT 10")`      |
| Enable ledger            | `engine.ledger.enable()`                                         |
| Commit changeset         | `engine.ledger.commit(cs)`                                       |
| Ledger history           | `engine.ledger.history(limit=20)`                                |
| Diff two revisions       | `engine.ledger.diff(rev_a, rev_b)`                               |
| Reconstruct at revision  | `engine.ledger.reconstruct(rev_id)`                              |
| Verify consistency       | `engine.ledger.verify()`                                         |
| Degree                   | `engine.degree_centrality(direction="out", top_k=20)`            |
| Betweenness              | `engine.betweenness_centrality(sample_size=200, top_k=20)`       |
| Betweenness neighborhood | `engine.betweenness_centrality_neighborhood(seed="...", hops=2)` |
| Closeness                | `engine.closeness_centrality(formula="harmonic", top_k=20)`      |
| Eigenvector              | `engine.eigenvector_centrality(max_iter=30, top_k=20)`           |
| Leiden                   | `engine.leiden_communities(gamma=1.0, top_k=100)`                |
| Rebuild index            | `engine.rebuild_nkg()`                                           |
| Check status             | `engine.status().report()`                                       |
| Export RDF               | `engine.export_rdf("out.ttl", label_filter=[...])`               |
| Validate SHACL           | `engine.validate_shacl("shapes.ttl")`                            |
| Export PROV-O            | `engine.prov_export("prov.ttl", ts_start=...)`                   |

---

**For deployment, security, and production setup, see [Admin Guide](ADMIN_GUIDE.md).**

**For schema reference and ObjectScript class details, see [Architecture](architecture/ARCHITECTURE.md).**

**For performance benchmarks and optimization, see [Performance](performance/GRAPH_ALGORITHMS.md).**

**For RDF export, SHACL validation, and PROV-O provenance, see [Semantic Layer](SEMANTIC_LAYER.md).**
