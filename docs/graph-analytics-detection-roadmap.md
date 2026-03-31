# IVG Graph Analytics: High-Speed Detection Use Cases

**Date**: 2026-04-01  
**Status**: Strategic design — not yet specced for implementation

---

## The Core Insight

Fraud, lateral movement, anomaly detection, and time series all reduce to the same problem:

> **Find nodes whose graph neighborhood changed in a way that deviates from a learned baseline.**

IRIS + IVG already has the graph. The missing primitive is **time** — and once you have time, everything else follows.

---

## Current IVG Capabilities (what we have)

| Primitive | How | Speed |
|-----------|-----|-------|
| k-hop ego graph extraction | `BFSFastJson`, `kg_SUBGRAPH` | 1-20ms |
| PPR-guided subgraph | `PPRGuidedJson` | 62ms |
| Community detection | `WCCJson`, `CDLPJson` | batch |
| Node similarity | `kg_KNN_VEC` (HNSW) | 1.7ms |
| Multi-vector search | PLAIDSearch | 9ms |
| Pattern matching | Cypher `[*1..3]`, EXISTS, UNION | varies |
| Edge reification (metadata) | `rdf_reifications` | 5ms |

---

## The Missing Primitives

### 1. Temporal Edges (CRITICAL — unlocks everything)

**Current**: `^KG("out", s, p, o) = weight` — no timestamp  
**Needed**: `^KG("tout", ts, s, p, o) = weight` — time-indexed

This single change enables:
- Sliding window queries: "who connected to what in the last 5 minutes?"
- Velocity features: "how fast is this node's degree growing?"
- Burst detection: "10 transactions in 30 seconds from one account"
- Temporal path queries: "find attack chains where each hop is within 60s of the previous"

**Implementation**:
```objectscript
^KG("tout", timestamp, source, predicate, target) = weight
^KG("tin",  timestamp, target, predicate, source) = weight
^KG("twindow", floor(timestamp/300), source) = ""  // 5-min bucket index
```

`$Order(^KG("tout", ts_start), 1, ts)` range scan for time windows. Sub-millisecond.

---

### 2. Ego Graph Serialization (for GNN training)

**Current**: BFSFastJson returns JSON edge list  
**Needed**: PyTorch Geometric / DGL-compatible adjacency + feature matrices

```python
def serialize_ego_graph(node_id, k=2, include_features=True) -> dict:
    # Returns: {"nodes": [...], "edges": [...], "features": {node_id: [f1,f2,...]}}
    # Compatible with torch_geometric.data.Data

def batch_ego_graphs(node_ids, k=2) -> list:
    # Single ObjectScript call via BatchBFSJson (new classmethod)
    # Returns list of ego graphs for GNN batch training
```

**ObjectScript side**: `Graph.KG.Traversal.BatchBFSJson(seedsJson, maxHops)` — runs BFS for N seeds in one call, returns array of edge lists. Eliminates N round-trips.

---

### 3. Graph Feature Extraction (for anomaly baselines)

Node-level features needed for anomaly detection:
- **Degree** — already in `^KG("deg", node)`
- **Clustering coefficient** — NEW: fraction of neighbors that are connected to each other
- **Triangle count** — NEW: `^KG("tri", node) = count`
- **Betweenness centrality approximation** — via PPR sampling
- **Temporal velocity** — rate of new edge creation (needs temporal edges)

```objectscript
ClassMethod ComputeLocalFeatures(nodeId As %String) As %String
// Returns JSON: {degree, clustering, triangles, pagerank, degree_velocity}
```

---

### 4. Pattern Library (for threat hunting)

Known attack patterns stored as graph templates, matched via our Cypher engine:

```cypher
// Lateral movement: process chain with external connection
MATCH (p1:Process)-[:SPAWNS*1..3]->(p2:Process)-[:CONNECTS]->(e:External)
WHERE timestamp > $start
RETURN p1, p2, e

// Fan-out fraud: one account sending to many new accounts
MATCH (src:Account)-[:SENDS]->(dst:Account)
WHERE src.age < 7 AND dst.first_seen > $yesterday
WITH src, COUNT(DISTINCT dst) AS fan_out
WHERE fan_out > 50
RETURN src, fan_out
```

These work today with Cypher `[*1..3]` (Sprint 3) + temporal edges (needed).

---

## The Integrated Pipeline: Three Use Cases

### Use Case A: Fraud Detection (Kumo/Coinbase pattern)

```
Ingest transactions → Build ^KG → Extract ego graphs → 
Train GNN (offline) → Store embeddings in kg_NodeEmbeddings →
Real-time: new transaction → extract ego graph → embed → 
KNN search against known fraud embeddings → score
```

**IVG role**: Graph storage, ego graph extraction, embedding store, KNN search  
**Python role**: GNN training, embedding generation  
**Gap**: Batch ego graph export, temporal edges for velocity features

### Use Case B: Cybersecurity / Lateral Movement

```
Ingest audit logs as events → Build temporal ^KG →
Pattern match against MITRE ATT&CK graph patterns (Cypher) →
Score suspicious paths by PPR from known IOCs →
Alert on anomalous subgraphs (KNN against baseline embeddings)
```

**IVG role**: Temporal graph, Cypher pattern matching, PPR scoring, anomaly search  
**Python role**: IOC feed ingestion, alert routing  
**Gap**: Temporal edges (critical), streaming ingest, pattern library

### Use Case C: Time Series Anomaly Detection

```
Ingest metrics as nodes + edges (metric → host, metric → service) →
Build temporal neighborhoods per time bucket →
Compute local features (degree velocity, clustering coefficient) →
Score deviations from rolling baseline (PPR distance) →
Alert when neighborhood structure changes beyond threshold
```

**IVG role**: Temporal graph, neighborhood features, baseline PPR  
**Python role**: Threshold tuning, alert routing  
**Gap**: Temporal edges, local feature computation, rolling baseline API

---

## Recommended Implementation Sequence

### Phase 1: Foundation (2-3 days)
1. **Temporal edges** — add `^KG("tout", ts, s, p, o)` in `GraphIndex.InsertIndex`
2. **Temporal Cypher** — `WHERE timestamp > $t` support in translator
3. **Batch ego graph export** — `BatchBFSJson` ObjectScript + Python `batch_ego_graphs()`

### Phase 2: Features (2-3 days)  
4. **Local graph features** — `ComputeLocalFeatures()` ObjectScript (degree, clustering, triangles)
5. **Ego graph serializer** — `serialize_ego_graph()` Python, PyG/DGL compatible output

### Phase 3: Detection (3-4 days)
6. **Baseline builder** — `build_anomaly_baseline(label, k, n_samples)` — samples ego graphs, embeds, stores in VecIndex
7. **Anomaly scorer** — `score_anomaly(node_id)` — extracts current ego graph, embeds, KNN distance from baseline
8. **Pattern library** — Cypher templates for MITRE ATT&CK T1059 (command execution), T1021 (lateral movement), TA0011 (C2)

---

## What This Gives Us (vs. Kumo / Neo4j GDS)

| Capability | Kumo | Neo4j GDS | IVG after Phase 3 |
|-----------|------|-----------|-------------------|
| k-hop ego graphs | ✅ | ✅ | ✅ (now) |
| GNN training data export | ✅ | ❌ | Phase 1 |
| Temporal graph | ❌ | ❌ | Phase 1 |
| Real-time anomaly scoring | ❌ | ❌ | Phase 3 |
| Cypher pattern matching | ❌ | ✅ | ✅ (now) |
| Multi-vector (PLAID) | ❌ | ❌ | ✅ (now) |
| FHIR/clinical bridge | ❌ | ❌ | ✅ (now) |
| Single engine (graph+vector) | ❌ (needs Qdrant) | ❌ (needs separate vector DB) | ✅ |

**The IVG differentiator**: Everything in one engine — temporal graph, pattern matching, vector similarity, multi-vector retrieval. No Neo4j + Qdrant + separate time series DB. IRIS is the substrate.

---

## Demo Story (for READY talk / Dirk conversation)

> "Traditional fraud detection systems split the problem across 3 tools: a graph DB for relationships, a vector DB for embeddings, and a time series DB for event streams. With IRIS + IVG, it's one engine. We store the transaction graph, compute the ego graph neighborhoods, run the GNN embeddings back in, and score new transactions against known fraud patterns — all in IRIS. The query that takes 4 cross-system hops in a Neo4j + Qdrant + InfluxDB architecture is a single Cypher query here."

---

## Open Questions for Dirk Conversation

1. Is the target deployment dpgenai1 (IRIS + FHIR data) or a separate security/fraud instance?
2. Does the Coinbase use case require streaming ingest (Kafka/Pulsar) or batch? If streaming, that's the TrustGraph integration (spec 004-trustgraph).
3. Is the primary audience for READY talk: (a) healthcare fraud, (b) financial fraud, (c) IT security, or (d) industrial anomaly? Affects which demo dataset to use.
4. Should the anomaly baseline use HNSW (existing) or VecIndex (new)? HNSW is higher recall; VecIndex is more controllable.
