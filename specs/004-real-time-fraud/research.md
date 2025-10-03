# Research & Technical Decisions: Real-Time Fraud Scoring MVP

**Feature**: 004-real-time-fraud
**Date**: 2025-10-02

## Overview

This document consolidates research findings for implementing real-time fraud scoring on IRIS Vector Graph. All technical decisions are driven by performance requirements (< 20ms p95 latency at 200 QPS) and constitutional principles (IRIS-native, SQL-first, test-first).

---

## R001: TorchScript Model Loading in IRIS Embedded Python (MVP) → GraphStorm Upgrade Path

### Decision (MVP)
Use `torch.jit.load()` in IRIS Embedded Python via `iris.cls` Python gateway for TorchScript MLP model loading.

### Rationale
- **IRIS Embedded Python Support**: IRIS 2025.1+ includes embedded Python runtime that can load PyTorch libraries
- **TorchScript Performance**: TorchScript provides optimized CPU inference (~5-8ms for small MLPs)
- **Model Portability**: `.torchscript` files are self-contained (include architecture + weights)
- **Zero-Copy Integration**: Embedded Python runs in-process with IRIS, avoiding IPC overhead
- **MVP Simplicity**: Faster implementation (2-3 weeks) for learning GNN concepts before scaling

### AWS GraphStorm Pattern (Future Upgrade Path)
**Reference**: [AWS GraphStorm v0.5 Real-Time Fraud Detection](https://aws.amazon.com/blogs/machine-learning/modernize-fraud-prevention-graphstorm-v0-5-for-real-time-inference/)

The AWS production pattern separates concerns across specialized services:
1. **OLTP Graph Database** (Neptune/IRIS): Live transaction streams, quick subgraph extraction
2. **Offline Training** (GraphStorm on SageMaker): Distributed RGCN/HGT training on billions of nodes
3. **Real-Time Endpoint** (SageMaker BYOC): Sub-second inference with standardized payloads
4. **Client Integration**: Subgraph sampling → payload preparation → endpoint invocation

**Upgrade Path** (6-8 weeks):
- Keep IRIS as OLTP graph database (replace Neptune)
- Export graph to S3 in GraphStorm format using `GConstruct` command
- Train full GNN (RGCN/HGT) on SageMaker with GraphStorm distributed training
- Deploy to SageMaker endpoint with one-command deployment (`launch_realtime_endpoint.py`)
- Implement GraphStorm JSON payload specification for client integration

**Why Not GraphStorm for MVP**:
- Requires multi-service orchestration (IRIS + S3 + SageMaker + ECR)
- BYOC Docker image packaging and deployment complexity
- GraphStorm GConstruct + distributed training setup (90s + 100s per epoch)
- Overkill for learning GNN fundamentals on 100K-1M node graphs

**When to Upgrade**:
- Graph scale exceeds 10M nodes or 100M edges
- Need distributed GNN training (RGCN, HGT, multi-layer message passing)
- Require enterprise MLOps (model versioning, A/B testing, gradual rollouts)
- SLOs demand horizontal scaling beyond single IRIS instance

### Implementation Pattern
```python
# In iris_vector_graph_core/fraud/model_loader.py
import torch

class TorchScriptModelLoader:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None

    def load_model(self):
        """Load TorchScript model from file"""
        try:
            self.model = torch.jit.load(self.model_path)
            self.model.eval()  # Set to inference mode
            return {"status": "success", "path": self.model_path}
        except Exception as e:
            raise ModelLoadError(f"Failed to load model from {self.model_path}: {str(e)}")

    def reload_model(self, new_model_path: str):
        """Hot-reload model without downtime"""
        old_model = self.model
        self.model_path = new_model_path
        try:
            self.load_model()
            del old_model  # Clean up old model
            return {"status": "reloaded", "path": new_model_path}
        except Exception as e:
            self.model = old_model  # Rollback on failure
            raise ModelLoadError(f"Reload failed, reverted to previous model: {str(e)}")
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **ONNX Runtime** | Adds external dependency (`onnxruntime`), requires model conversion step, minimal perf gain for small MLPs |
| **Pure Python Model** | Too slow (~50-100ms inference), no GPU support, doesn't meet <20ms target |
| **Separate Python Service** | Violates IRIS-native principle, adds network latency, requires service orchestration |

### Performance Characteristics
- Model load time: ~200-500ms (one-time on startup)
- Inference time: ~5-8ms for MLP with 768-dim input + 4 tabular features
- Memory footprint: ~10-20MB per model
- Hot-reload downtime: 0ms (in-place swap)

### Error Handling
- Missing model file → raise `ModelLoadError` with file path
- Corrupted .torchscript file → raise `ModelLoadError` with validation error
- PyTorch version mismatch → log warning, attempt load (TorchScript is forward-compatible)

---

## R001A: Subgraph Sampling Pattern for GNN Inference

### Decision
Implement k-hop neighborhood extraction with fanout limits for preparing GNN inference payloads, following AWS GraphStorm pattern.

### Rationale
- **GNN Requirement**: Graph neural networks require node neighborhoods for message passing
- **AWS Pattern**: GraphStorm inference uses 3-stage flow (sampling → payload prep → inference)
- **IRIS-Native**: Use LANGUAGE PYTHON stored procedure with `iris.sql.exec()` for k-hop sampling
- **Performance**: Fanout caps prevent unbounded traversal on high-degree nodes

### Sampling Strategy
**K-hop with Fanout Limits** (from AWS article):
- Hop 1: Top 10 neighbors by most recent edge
- Hop 2: Top 5 neighbors per hop-1 node
- Total edges: Max 10 + (10 × 5) = 60 edges

**Implementation Pattern** (LANGUAGE PYTHON):
```sql
-- sql/fraud/proc_subgraph_sample.sql
-- IRIS SQL does not support recursive CTEs (see CTE_DESIGN_VALIDATION.md)
-- Use LANGUAGE PYTHON with iris.sql.exec() following graph_path_globals.sql pattern

CREATE OR REPLACE PROCEDURE gs_SubgraphSample(
  IN target_tx_id VARCHAR(256),
  IN fanout1 INT DEFAULT 10,
  IN fanout2 INT DEFAULT 5
)
RETURNS TABLE (s VARCHAR(256), o_id VARCHAR(256), p VARCHAR(128), hop INT)
LANGUAGE PYTHON
BEGIN
import iris.sql as sql

# Hop 1: Top fanout1 neighbors by most recent edge
cursor = sql.exec("""
    SELECT TOP ? e.s, e.o_id, e.p, 1 AS hop
    FROM rdf_edges e
    WHERE e.s = ?
    ORDER BY e.created_at DESC
""", fanout1, target_tx_id)

hop1_results = cursor.fetchall()
hop1_nodes = [row[1] for row in hop1_results]  # Extract o_id values

# Hop 2: Top fanout2 neighbors per hop1 node (true per-node fanout)
hop2_results = []
for hop1_node in hop1_nodes:
    cursor = sql.exec("""
        SELECT TOP ? e.s, e.o_id, e.p, 2 AS hop
        FROM rdf_edges e
        WHERE e.s = ?
        ORDER BY e.created_at DESC
    """, fanout2, hop1_node)
    hop2_results.extend(cursor.fetchall())

# Combine results (max 60 edges: 10 + 50)
all_results = hop1_results + hop2_results
return all_results
END;
```

**Key Advantages over CTE Approach**:
- ✅ True per-node fanout limits (not global `LIMIT 50` approximation)
- ✅ Can extend to arbitrary depth (not hardcoded to 2 hops)
- ✅ Cycle detection possible (add `seen` set if needed)
- ✅ Follows proven `graph_path_globals.sql` pattern
- ✅ Avoids IRIS SQL CTE limitations (non-recursive only)

### Payload Preparation
After sampling subgraph, prepare JSON payload for model inference:

**Subgraph JSON Format** (simplified GraphStorm spec):
```json
{
  "target_nodes": ["tx:2991260"],
  "node_features": {
    "tx:2991260": {"amount": 150.0, "deg_24h": 1, "device_count": 1},
    "dev:laptop123": {"device_type": "desktop"},
    "ip:192.168.1.100": {"ip_reputation": 0.8}
  },
  "edges": [
    {"src": "tx:2991260", "dst": "dev:laptop123", "rel": "uses_device"},
    {"src": "tx:2991260", "dst": "ip:192.168.1.100", "rel": "uses_ip"}
  ]
}
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **Full Graph Export** | Too slow for real-time inference (multi-GB graphs), defeats purpose of sampling |
| **Random Sampling** | Loses temporal relevance (recent edges more important for fraud), no ordering |
| **Fixed Depth BFS** | Unbounded fanout causes performance issues on high-degree nodes (popular merchants) |

### Performance Characteristics
- Subgraph extraction: ~15-25ms (LANGUAGE PYTHON with indexed queries)
- Feature lookup: ~4ms (PK lookups for node properties)
- Payload serialization: ~2ms (JSON encoding)
- **Total sampling overhead**: ~21-31ms (acceptable for <50ms EGO mode target)

**Note**: LANGUAGE PYTHON is 10-50x faster than client-side Python (in-process execution, per `docs/architecture/embedded_python_architecture.md:283-304`)

### Error Handling
- Target node not found → Return empty subgraph, fallback to zero-vector embedding (R001)
- No neighbors found (isolated node) → Return target node only, cold-start reasoning
- Sampling timeout (>50ms) → Reduce fanout dynamically (10/5 → 5/2), log warning

---

## R002: SQL Trigger Design for Real-Time Rolling Features

### Decision
Implement AFTER INSERT trigger on `gs_events` table to update rolling 24h features (`deg_24h`, `tx_amt_sum_24h`) in the same transaction.

### Rationale
- **Consistency**: FR-008 requires feature updates in same transaction as event insert
- **Freshness**: Trigger-based updates provide sub-second freshness (no polling lag)
- **Simplicity**: No external consumer process or message queue required
- **IRIS-Native**: Leverages built-in SQL trigger capabilities

### Implementation Pattern
```sql
-- sql/fraud/trigger_update_features.sql
CREATE TRIGGER gs_UpdateRollingFeatures
AFTER INSERT ON gs_events
FOR EACH ROW
BEGIN
    -- Upsert into gs_features
    MERGE INTO gs_features AS target
    USING (SELECT :NEW.entity_id AS entity_id) AS source
    ON (target.entity_id = source.entity_id)
    WHEN MATCHED THEN
        UPDATE SET
            deg_24h = deg_24h + 1,
            tx_amt_sum_24h = tx_amt_sum_24h + COALESCE(:NEW.amount, 0),
            ts_updated = CURRENT_TIMESTAMP
    WHEN NOT MATCHED THEN
        INSERT (entity_id, deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop, ts_updated)
        VALUES (:NEW.entity_id, 1, COALESCE(:NEW.amount, 0), 0, 0, CURRENT_TIMESTAMP);
END;
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **Async Message Queue** | Adds architectural complexity (Kafka/RabbitMQ), violates SQL-first principle, introduces eventual consistency |
| **Materialized View** | IRIS doesn't support automatically refreshed materialized views with time windows |
| **Application-Layer Update** | Requires two SQL statements, risk of partial failure (event inserted but features not updated) |

### Performance Characteristics
- Trigger overhead: ~0.5-1ms per insert (MERGE is optimized for single-row updates)
- Contention risk: High-frequency updates to same entity_id may cause row lock contention
- Mitigation: Batch inserts in application layer (e.g., bulk insert 100 events → 100 row-level triggers)

### Limitations
- **Time-based expiry**: Trigger doesn't decrement counts after 24h (handled by hourly job)
- **Derived features**: `uniq_devices_7d` and `risk_neighbors_1hop` require JOINs (too expensive for trigger, computed by hourly job)

### Error Handling
- FK violation (invalid entity_id) → Transaction rolls back, INSERT fails with clear error
- NULL amount → Treat as 0.0 (COALESCE handles this)

---

## R003: Bounded CTE Patterns for Ego-Graph Extraction

### Decision
Use recursive CTE with explicit LIMIT at each hop level (LIMIT 10 at hop 1, LIMIT 5 at hop 2) to extract bounded ego-graphs for GraphSAGE mode.

### Rationale
- **Performance**: Fanout caps prevent unbounded traversal on high-degree nodes (meets NFR-003 <25ms p95 target)
- **IRIS-Native**: Pure SQL solution, no external graph database required
- **Deterministic**: Always returns same subgraph for same seed (stable for caching)

### Implementation Pattern
```sql
-- sql/fraud/proc_ego_graph.sql
CREATE PROCEDURE gs_EgoGraph(
    IN seed VARCHAR(256),
    IN fanout1 INT DEFAULT 10,
    IN fanout2 INT DEFAULT 5,
    IN depth INT DEFAULT 2
)
BEGIN
    -- Hop 1: Get top N neighbors by most recent edge
    WITH hop1 AS (
        SELECT e.s AS source, e.o_id AS target, e.p AS rel_type, e.created_at
        FROM rdf_edges e
        WHERE e.s = seed
        ORDER BY e.created_at DESC
        LIMIT fanout1
    ),
    -- Hop 2: Get top M neighbors for each hop1 node
    hop2 AS (
        SELECT e.s AS source, e.o_id AS target, e.p AS rel_type, e.created_at
        FROM rdf_edges e
        WHERE e.s IN (SELECT target FROM hop1)
        ORDER BY e.s, e.created_at DESC
        LIMIT fanout2 * fanout1  -- Approximation (may need partitioning for exact cap per seed)
    )
    -- Union hop1 and hop2
    SELECT source, target, rel_type FROM hop1
    UNION ALL
    SELECT source, target, rel_type FROM hop2;
END;
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **Breadth-First Search in Python** | Slower than SQL CTE (network round-trips), harder to index optimize |
| **Neo4j Cypher** | Requires separate graph database, violates IRIS-native principle, adds operational complexity |
| **Unbounded CTE** | High-degree nodes (e.g., popular merchant) would cause timeouts (>100ms for 1000+ edges) |

### Performance Characteristics
- Indexed query time: ~15-25ms for depth=2, fanout 10/5 (assumes index on `(s, p, created_at)`)
- Worst case (no index): ~100-200ms (violates SLO, INDEX REQUIRED)
- Result size: Max 10 + (10 × 5) = 60 edges

### Indexing Requirements
```sql
-- Critical index for ego-graph performance
CREATE INDEX rdf_edges_spc_idx ON rdf_edges(s, p, created_at DESC);
```

### Error Handling
- Seed node doesn't exist → Return empty result (0 edges)
- High fanout parameters (>100) → Log warning, enforce hard cap at 100 to prevent abuse

---

## R004: Feature Attribution for MLP Explainability

### Decision
Use gradient × input (simple saliency) for feature importance computation.

### Rationale
- **Fast**: <3ms computation for small MLP (meets NFR-004)
- **Interpretable**: Attribution weights directly show feature contribution
- **TorchScript Compatible**: Works with `.grad` attribute on tensors

### Implementation Pattern
```python
# iris_vector_graph_core/fraud/explainability.py
import torch

def compute_feature_attributions(model, input_tensor, feature_names):
    """
    Compute gradient × input for feature importance.

    Args:
        model: TorchScript MLP model
        input_tensor: Input features (shape: [1, num_features])
        feature_names: List of feature names

    Returns:
        List of (feature_name, value, weight) tuples
    """
    input_tensor.requires_grad = True
    output = model(input_tensor)
    fraud_score = output[0, 1]  # Assume output[:, 1] is fraud probability

    # Backward pass
    fraud_score.backward()

    # Gradient × input
    attributions = (input_tensor.grad * input_tensor).detach().numpy()[0]

    # Sort by absolute attribution
    results = [(feature_names[i], float(input_tensor[0, i]), float(attributions[i]))
               for i in range(len(feature_names))]
    results.sort(key=lambda x: abs(x[2]), reverse=True)

    return results[:3]  # Top 3 reasons
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **SHAP (SHapley Additive exPlanations)** | Too slow (~50-100ms for small model), overkill for MVP, requires shap library |
| **Integrated Gradients** | More accurate but 10x slower (~30ms), not worth complexity for MVP |
| **LIME (Local Interpretable Model-agnostic Explanations)** | Requires training surrogate model, ~100ms latency |

### Performance Characteristics
- Computation time: ~2-3ms (single backward pass)
- Memory overhead: ~1MB (gradient tensors)
- Accuracy: Approximation (not true Shapley values), good enough for top-3 features

### Error Handling
- Model doesn't support gradients → Fall back to zero weights (log warning)
- NaN gradients → Replace with 0.0, log error

---

## R005: Vector Proximity for Fraud Centroid

### Decision
Precompute fraud centroid offline (mean of all fraud-labeled embeddings) and use cosine similarity at inference time.

### Rationale
- **O(1) Lookup**: No HNSW search required for single centroid
- **Fast**: Cosine similarity is 1 dot product + 2 norms (~0.5ms for 768-dim vectors)
- **Simple**: No clustering algorithm required

### Implementation Pattern
```python
# Offline: Compute fraud centroid
import numpy as np

def compute_fraud_centroid(embeddings_df):
    """
    Compute mean embedding for all fraud-labeled entities.

    Args:
        embeddings_df: DataFrame with columns [entity_id, embedding, label]

    Returns:
        Fraud centroid vector (768-dim)
    """
    fraud_embeddings = embeddings_df[embeddings_df['label'] == 'fraud']['embedding']
    centroid = np.mean(fraud_embeddings, axis=0)
    return centroid / np.linalg.norm(centroid)  # Normalize

# Online: Cosine similarity
def cosine_similarity(vec1, vec2):
    """Cosine similarity between two normalized vectors"""
    return float(np.dot(vec1, vec2))
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **k-NN to Fraud Cluster** | Requires HNSW search (~5-10ms), slower than single centroid |
| **Multiple Centroids (Clustering)** | Adds complexity (k-means clustering), marginal accuracy gain for MVP |
| **HNSW Search** | Unnecessary for single centroid, would add ~5ms latency |

### Performance Characteristics
- Cosine similarity: ~0.5ms for 768-dim vectors
- Centroid update frequency: Daily (offline batch job)
- Memory: 768 floats × 4 bytes = 3KB per centroid

### SQL Storage
```sql
CREATE TABLE gs_fraud_centroid (
    centroid_id INT PRIMARY KEY,
    emb VECTOR[768],
    label VARCHAR(16) DEFAULT 'fraud',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert centroid (offline job)
INSERT INTO gs_fraud_centroid (centroid_id, emb, updated_at)
VALUES (1, ?, CURRENT_TIMESTAMP);
```

### Error Handling
- Missing centroid → Use zero-vector, log warning, mark reason as "no_centroid_available"
- Invalid embedding (NaN/Inf) → Fall back to feature-only scoring

---

## R006: Hot-Reload Pattern for TorchScript Models

### Decision
Implement admin endpoint `POST /admin/model/activate` that reloads TorchScript model in-place using Python reload pattern.

### Rationale
- **Zero Downtime**: FR-016 requires no service interruption
- **In-Place Swap**: Python allows replacing module-level variables atomically
- **Simple**: No process restart or complex versioning required for MVP

### Implementation Pattern
```python
# api/routers/fraud.py (admin endpoint)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

class ModelActivateRequest(BaseModel):
    model_path: str

router = APIRouter()

@router.post("/admin/model/activate")
async def activate_model(request: ModelActivateRequest):
    """
    Hot-reload TorchScript model without downtime.

    Steps:
    1. Load new model from file
    2. Validate model (test inference on dummy input)
    3. Atomic swap global model reference
    4. Return success
    """
    try:
        # Load and validate new model
        new_model = torch.jit.load(request.model_path)
        new_model.eval()

        # Test inference
        dummy_input = torch.randn(1, 772)  # 768 emb + 4 features
        output = new_model(dummy_input)
        assert output.shape == (1, 2), "Invalid model output shape"

        # Atomic swap
        global current_model
        old_model = current_model
        current_model = new_model

        # Cleanup
        del old_model

        return {"status": "success", "model_path": request.model_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model activation failed: {str(e)}")
```

### Alternatives Considered
| Alternative | Why Rejected |
|-------------|--------------|
| **Process Restart** | Requires downtime (~10-30 seconds), violates FR-016 |
| **Model Versioning in DB** | Out of MVP scope, adds complexity (version table, migration logic) |
| **Blue-Green Deployment** | Overkill for single model, requires load balancer and 2x resources |

### Performance Characteristics
- Model reload time: ~200-500ms (blocked during swap, but no request failure)
- Request handling during reload: In-flight requests use old model, new requests use new model (atomic swap)
- Rollback: Old model kept in memory temporarily (can revert on validation failure)

### Error Handling
- Invalid model file → Return 500 error, keep old model
- Model validation failure → Return 500 error, keep old model
- Concurrent reload requests → Use lock to prevent race conditions

### Security Considerations
- **Admin-only endpoint**: Must add authentication (deferred to JWT implementation phase)
- **Path validation**: Restrict model paths to `/models/` directory to prevent arbitrary file access
- **Rate limiting**: Max 1 reload per minute to prevent abuse

---

## Summary: Technical Decisions Matrix

| ID | Decision | Primary Driver | Performance Impact | Risk Level |
|----|----------|----------------|-------------------|------------|
| R001 | TorchScript in Embedded Python | IRIS-native, <20ms target | +5-8ms inference | Low |
| R002 | SQL Trigger for Rolling Features | Consistency, freshness | +0.5-1ms per insert | Medium (contention) |
| R003 | Bounded CTE for Ego-Graph | <25ms EGO target | +15-25ms (indexed) | Medium (requires index) |
| R004 | Gradient × Input Explainability | <3ms reason codes | +2-3ms | Low |
| R005 | Fraud Centroid Cosine Similarity | O(1) proximity | +0.5ms | Low |
| R006 | In-Place Model Hot-Reload | Zero downtime | +200-500ms (one-time) | Low |

**Overall Latency Budget** (MLP mode):
- PK lookups (embeddings + features): 4ms
- MLP inference: 8ms
- Feature attributions: 3ms
- Vector proximity: 0.5ms
- Overhead (JSON serialization, network): 3ms
- **Total**: ~18.5ms (meets <20ms p95 target)

---

## Dependencies & Prerequisites

### Python Libraries (add to pyproject.toml)
```toml
[project.dependencies]
torch = ">=2.0.0"  # TorchScript support
numpy = ">=1.24.0"  # Already present, used for vector ops
```

### SQL Extensions
- **IRIS Vector Type**: Requires IRIS 2025.1+ with Vector Search feature
- **Trigger Support**: Standard SQL (all IRIS versions)
- **Recursive CTE**: IRIS 2024.1+ (check compatibility)

### IRIS Configuration
```ini
[Startup]
; Enable Embedded Python
EmbeddedPython = 1

[Python]
; Python 3.11 runtime
PythonPath = /usr/local/bin/python3.11
```

### File Structure
```
models/
├── mlp_current.torchscript      # Main MLP model
├── graphsage_current.torchscript # Optional GraphSAGE model (EGO mode)
└── fraud_centroid.npy           # Fraud centroid vector (768-dim)
```

---

## Open Questions & Future Research

**NOTE**: User mentioned `../pluggable_iml` project which demos IntegratedML custom model upload feature. This could potentially simplify the TorchScript model loading pattern described in R001 by using IRIS's native ML model registration instead of embedded Python loader. **Action**: Evaluate during implementation phase (Phase 4) whether IntegratedML provides equivalent performance and hot-reload capabilities.

1. **Trigger Contention at Scale**: If single entity receives >1000 events/sec, row-level lock contention may degrade performance. Consider:
   - Partitioning gs_features by entity_id hash
   - Moving to eventual consistency with async aggregation

2. **Centroid Drift**: Fraud patterns may shift over time. Future work:
   - Implement daily centroid recomputation job
   - Track centroid drift metrics (alert if cosine similarity to old centroid <0.8)

3. **GraphSAGE Model Size**: If ego-graph includes node features (not just IDs), message-passing GNN may require >50ms. Benchmark with real model before committing to EGO mode.

4. **Index Maintenance**: As rdf_edges table grows (>10M rows), index on `(s, p, created_at)` may become fragmented. Monitor query plan and rebuild index quarterly.

---

**Status**: ✅ All research complete, ready for Phase 1 (Design & Contracts)
