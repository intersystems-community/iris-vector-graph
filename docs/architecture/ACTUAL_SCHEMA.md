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
    id VARCHAR(256) PRIMARY KEY,
    emb VECTOR(DOUBLE, 768),
    metadata %Library.DynamicObject,
    CONSTRAINT fk_emb_node FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
)
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
```

Note: `%EXACT` preserves case (IRIS VARCHAR uppercases by default).

---

## SQL Stored Procedures (Graph_KG)

| Procedure | Signature | Description |
|-----------|-----------|-------------|
| `kg_KNN_VEC` | `(query_vector, k, label, property)` | HNSW vector search, returns JSON `[{"id","score"},...]` |
| `kg_PPR` | `(seeds_json, damping, max_iter, ...)` | Personalized PageRank, returns JSON `[{"id","score"},...]` |
| `kg_NEIGHBORS` | `(sources, predicate, direction)` | 1-hop neighborhood, returns JSON array |
| `kg_BM25` | `(name, query, k)` | BM25 lexical search, returns JSON `[{"id","score"},...]` |
| `kg_RRF_FUSE` | `(k, k1, k2, c, vector, text)` | Reciprocal rank fusion (vector + text) |

`kg_BM25` is a thin wrapper over `Graph.KG.BM25Index.Search()` exposed as an SQL stored procedure for Cypher translator Stage CTEs.

---

## Global Structures

### ^KG — Temporal + Structural Graph

```
^KG("out", s, p, o) = weight              — structural outbound edges
^KG("in",  o, p, s) = weight              — structural inbound edges
^KG("tout", ts, s, p, o) = weight         — temporal outbound (time-ordered)
^KG("tin",  ts, o, p, s) = weight         — temporal inbound
^KG("bucket", bucket, s) = count          — 5-min pre-aggregated edge count
^KG("tagg", bucket, s, p, key) = value    — COUNT/SUM/AVG/MIN/MAX/HLL per bucket
^KG("edgeprop", ts, s, p, o, key) = value — rich edge attributes
```

### ^BM25Idx — BM25 Lexical Search

```
^BM25Idx(name, "cfg", "N")           — document count
^BM25Idx(name, "cfg", "avgdl")       — average document length
^BM25Idx(name, "cfg", "k1")          — BM25 k1 parameter
^BM25Idx(name, "cfg", "b")           — BM25 b parameter
^BM25Idx(name, "cfg", "vocab_size")  — distinct token count
^BM25Idx(name, "idf",  term)         — Robertson IDF value
^BM25Idx(name, "tf",   term, docId)  — term frequency (term-first subscript order)
^BM25Idx(name, "len",  docId)        — document token count
```

Term-first `"tf"` subscript enables O(postings) iteration: `$Order(^BM25Idx(name,"tf",queryTerm,""))`.

### ^VecIdx, ^PLAID, ^NKG

See [ARCHITECTURE.md](ARCHITECTURE.md) for full global documentation.
