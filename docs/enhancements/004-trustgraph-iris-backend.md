# Enhancement: TrustGraph IRIS Backend Adapter

**Date**: 2026-03-29
**Status**: Spec only — not scheduled for READY talk
**Affects**: `iris_vector_graph` library (new adapter module)
**Timeline**: Post-READY — Tech Exchange table demo or AIML71

---

## Problem

TrustGraph is an open-source GraphRAG framework (trustgraph.ai) that currently supports
Neo4j + Qdrant as its graph + vector backend. Adding IRIS as a backend would:

1. Position IRIS as a unified graph+vector store (vs the Neo4j+Qdrant split)
2. Get external validation (real OSS project choosing IRIS)
3. Enable 3D visualization of IRIS knowledge graphs via the TrustGraph Workbench

The IVG library already has the graph primitives (`create_node`, `create_edge`, `get_nodes`,
`kg_KNN_VEC`). The missing piece is the TrustGraph protocol adapter — translating TrustGraph's
Pulsar-based triple queries into IVG API calls.

---

## Architecture

```
TrustGraph Stack                        IRIS (via IVG)
┌──────────────┐                  ┌─────────────────────┐
│  Workbench    │ ← Pulsar ←──── │  trustgraph_write.py │ ← entities/triples
│  (3D viz)     │                 │  (Pulsar consumer)   │
├──────────────┤                  │                      │
│  Agents       │ → Pulsar ─────→│  trustgraph_query.py │ → SPO queries
│  (extract)    │                 │  (Pulsar responder)  │
└──────────────┘                  └──────────┬───────────┘
                                             │
                                    IRISGraphEngine
                                    create_node / create_edge
                                    get_nodes / kg_KNN_VEC
                                    Graph_KG schema
```

### Two Python services (~400 lines total)

**trustgraph_write.py** — Pulsar consumer that receives extracted triples and writes them
to IRIS via `IRISGraphEngine.create_node()` / `create_edge()`.

**trustgraph_query.py** — Pulsar responder that handles SPO (Subject-Predicate-Object)
triple queries against `Graph_KG.rdf_edges` with 8 wildcard combinations:

| Pattern | SQL |
|---------|-----|
| `(S, P, O)` | `WHERE s=? AND p=? AND o_id=?` |
| `(S, P, *)` | `WHERE s=? AND p=?` |
| `(S, *, O)` | `WHERE s=? AND o_id=?` |
| `(*, P, O)` | `WHERE p=? AND o_id=?` |
| `(S, *, *)` | `WHERE s=?` |
| `(*, P, *)` | `WHERE p=?` |
| `(*, *, O)` | `WHERE o_id=?` |
| `(*, *, *)` | `SELECT * FROM rdf_edges` |

### Dependencies

- **Apache Pulsar** — TrustGraph's message bus (docker run apache/pulsar)
- **trustgraph-base** pip package — Pulsar schema definitions
- No changes to IVG core library — adapter uses public API only

### Schema Mapping

TrustGraph uses a scoped property graph model:

| TrustGraph concept | IVG mapping |
|-------------------|-------------|
| Entity (user, collection, name, value) | `Graph_KG.nodes` with properties for user/collection |
| Triple (s, p, o) | `Graph_KG.rdf_edges` (s, p, o_id) |
| Entity class/type | `Graph_KG.rdf_labels` |
| Entity value/description | `Graph_KG.rdf_props` |
| Embeddings | `Graph_KG.kg_NodeEmbeddings` via `kg_KNN_VEC` |

The `user` and `collection` scoping from TrustGraph maps to node properties.
This enables multi-tenant KG storage in a single IRIS namespace.

---

## Implementation Estimate

| Component | Lines | Time |
|-----------|-------|------|
| trustgraph_write.py | ~150 | 1h |
| trustgraph_query.py (8 SPO patterns) | ~200 | 1.5h |
| Docker compose (Pulsar + IVG services) | ~50 | 30min |
| Testing (manual + integration) | — | 1h |
| **Total** | ~400 | **4h** |

---

## Where It Fits

| Venue | Fit | Notes |
|-------|-----|-------|
| AIML75 Beat 4 (READY demo) | No | 12 min too tight, dilutes the coordination story |
| Tech Exchange table | Yes | Short visual demo, 3D graph spinning, no time pressure |
| AIML71 (Petrocelli RAG session) | Yes | GraphRAG + visualization fits naturally |
| Community contribution | Yes | PR to trustgraph repo with IRIS backend |

---

## Not Implementing Now Because

1. The READY demo is already solid without it (Beat A/B/C all working)
2. Adds Pulsar dependency (heavy for a 12-minute demo)
3. The 3D visualization is impressive but doesn't answer the architects' question ("why trust this in production?")
4. Remaining READY work (KBAC Beat 5, post_finding wiring, screencasts) is higher priority

---

## Files to Create (when implemented)

```text
adapters/trustgraph/
├── __init__.py
├── write.py              # Pulsar consumer → IRISGraphEngine writes
├── query.py              # SPO triple query responder
├── config.py             # Pulsar/IRIS connection config
└── docker-compose.yml    # Pulsar + adapter services
```

These live in the IVG repo as an optional adapter — not a core dependency.
`trustgraph-base` and `pulsar-client` are optional extras, not in the main requirements.
