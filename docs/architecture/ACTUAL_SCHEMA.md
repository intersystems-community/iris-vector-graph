# Schema Reference

## SQL Tables (Graph_KG)

### nodes
```sql
CREATE TABLE Graph_KG.nodes (
    node_id VARCHAR(256) PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### rdf_labels
```sql
CREATE TABLE Graph_KG.rdf_labels (
    s VARCHAR(256) NOT NULL,
    label VARCHAR(128) NOT NULL,
    CONSTRAINT pk_labels PRIMARY KEY (s, label)
)
```

### rdf_props
```sql
CREATE TABLE Graph_KG.rdf_props (
    s VARCHAR(256) NOT NULL,
    "key" VARCHAR(128) NOT NULL,
    val VARCHAR(64000),
    CONSTRAINT pk_props PRIMARY KEY (s, "key")
)
```

### rdf_edges
```sql
CREATE TABLE Graph_KG.rdf_edges (
    edge_id BIGINT IDENTITY PRIMARY KEY,
    s VARCHAR(256) NOT NULL,
    p VARCHAR(128) NOT NULL,
    o_id VARCHAR(256) NOT NULL,
    qualifiers VARCHAR(4000)
)
```

### kg_NodeEmbeddings
```sql
CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    node_id INT IDENTITY,
    id VARCHAR(256),
    label VARCHAR(128),
    property_name VARCHAR(128),
    emb VECTOR(DOUBLE, 768)
)
-- HNSW index
CREATE INDEX kg_emb_hnsw ON Graph_KG.kg_NodeEmbeddings(emb)
    AS HNSW(M=16, efConstruction=200, Distance='COSINE')
```

### fhir_bridges
```sql
CREATE TABLE Graph_KG.fhir_bridges (
    fhir_code VARCHAR(64) %EXACT NOT NULL,
    kg_node_id VARCHAR(256) %EXACT NOT NULL,
    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
    bridge_type VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
    confidence FLOAT DEFAULT 1.0,
    source_cui VARCHAR(16),
    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
)
-- Indexes
CREATE INDEX idx_bridges_code_type ON Graph_KG.fhir_bridges (fhir_code, bridge_type)
CREATE INDEX idx_bridges_kg_node ON Graph_KG.fhir_bridges (kg_node_id)
CREATE INDEX idx_bridges_type ON Graph_KG.fhir_bridges (bridge_type)
```

Note: `%EXACT` on `fhir_code` and `kg_node_id` preserves case (IRIS VARCHAR uppercases by default).

## SQL Functions (Stored Procedures)

| Function | Signature | Description |
|----------|-----------|-------------|
| `kg_KNN_VEC` | `(query_vector, k, label, property)` | HNSW vector search, returns (id, score) |
| `kg_PPR` | `(seeds_json, damping, max_iter)` | Personalized PageRank via ObjectScript |
| `kg_NEIGHBORS` | `(sources, predicate, direction)` | 1-hop neighborhood expansion |
| `kg_SUBGRAPH` | `(seeds_json, k_hops, edge_types)` | Bounded k-hop subgraph extraction |
| `kg_RRF_FUSE` | `(k, k1, k2, c, vector, text)` | Reciprocal rank fusion (vector + text) |

## Global Structures

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed `^KG`, `^NKG`, `^VecIdx`, and `^PLAID` global documentation.
