<!-- markdownlint-disable MD013 -->

# Schema Reference

## SQL Tables (Graph_KG)

### nodes

```sql
CREATE TABLE Graph_KG.nodes (
    node_id    VARCHAR(256) %EXACT NOT NULL,
    graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',  -- spec 214: empty string = default graph
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id),
    CONSTRAINT uq_nodes_nodeid UNIQUE (node_id)
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

### ledger_revisions (spec 213)

Projected by `Graph.KG.LedgerRevision`; one immutable row per committed revision. UPDATE and
DELETE are rejected by triggers; reset only via `Graph.KG.Ledger.PurgeAll()`.

```sql
seq BIGINT (IDKEY, contiguous from 1), revision_id VARCHAR(32) UNIQUE, parent_id VARCHAR(32),
kind VARCHAR(16) -- genesis | adoption | changeset
actor VARCHAR(256), actor_type VARCHAR(32), conn_user VARCHAR(128), committed_ms BIGINT (UTC ms),
message VARCHAR(4000), source VARCHAR(4000) (JSON), correlation_id VARCHAR(256),
idempotency_key VARCHAR(256), op_count INTEGER, stmt_first INTEGER, stmt_last INTEGER
```

### ledger_stats (spec 213)

Projected by `Graph.KG.LedgerStats`; single row keyed `ledger = 'default'`: `state`, `strict`,
`head_seq`, `commits_ok`, `replays_idem`, `rej_stale_head`, `rej_unknown_head`,
`rej_idem_conflict`, `rej_failed_op`, `rej_strict_block`, `rej_size_limit`, `rej_disabled`,
`rej_lock_timeout`, `last_verify_ms`, `last_verify_result`, `enabled_ms`, `disabled_ms`.

## SQL Stored Procedures (Graph_KG)

| Procedure              | Signature                                     | Description                                                                     |
| ---------------------- | --------------------------------------------- | ------------------------------------------------------------------------------- |
| `kg_KNN_VEC`           | `(query_vector, k, label, property)`          | HNSW vector search, returns JSON `[{"id","score"},...]`                         |
| `kg_PPR`               | `(seeds_json, damping, max_iter, ...)`        | Personalized PageRank, returns JSON `[{"id","score"},...]`                      |
| `kg_NEIGHBORS`         | `(sources, predicate, direction)`             | 1-hop neighborhood, returns JSON array                                          |
| `kg_BM25`              | `(name, query, k)`                            | BM25 lexical search, returns JSON `[{"id","score"},...]`                        |
| `kg_RRF_FUSE`          | `(k, k1, k2, c, vector, text)`                | Reciprocal rank fusion (vector + text)                                          |
| `ledger_records`       | `(seq_from, seq_to, ordinal_from, page_size)` | Paged mutation records (spec 213); `SELECT * FROM Graph_KG.ledger_records(...)` |
| `rebuild_edge_indices` | `(schema)`                                    | Rebuild structural-table indices without triggering the functional index purge  |

`kg_BM25` is a thin wrapper over `Graph.KG.BM25Index.Search()` exposed as an SQL stored procedure for Cypher translator Stage CTEs.

---

## Global Structures

### ^KG — Temporal + Structural Graph

```text
^KG("out", 0, s, p, o) = weight              — structural outbound edges (shard 0)
^KG("in",  0, o, p, s) = weight              — structural inbound edges (shard 0)
^KG("tout", ts, s, p, o) = weight            — temporal outbound (time-ordered)
^KG("tin",  ts, o, p, s) = weight            — temporal inbound
^KG("bucket", bucket, s) = count             — 5-min pre-aggregated edge count
^KG("tagg", bucket, s, p, key) = value       — COUNT/SUM/AVG/MIN/MAX/HLL per bucket
^KG("edgeprop", ts, s, p, o, key) = value    — rich edge attributes
^KG("deg", s) = total_degree                 — pre-aggregated total degree
^KG("degp", s, p) = degree_for_predicate    — pre-aggregated per-predicate degree (for KHopCount O(1))
```

Note: The `"out"` and `"in"` subscripts use shard 0 (i.e., `^KG("out", 0, s, p, o)`).
The shard subscript reserves space for future horizontal partitioning.
`^KG("degp", s, p)` powers the `KHopCount` fast path — O(1) lookup instead of `$Order` scan.

### ^IVG.Ledger — Revision Ledger (spec 213)

```text
^IVG.Ledger("meta","state"|"strict"|"maxOps"|"reconBound")
^IVG.Ledger("head") = seq                          — current head sequence
^IVG.Ledger("stmtNext") = next statement identity
^IVG.Ledger("revById", id) = seq / ("idBySeq", seq) = id
^IVG.Ledger("rec", seq, ordinal) = $LB(op, entityKind, entityId, attr, priorEnc, newEnc, graph, flags)
^IVG.Ledger("stmt", stmtId) = $LB(s, p, o, graph, createdSeq, deletedSeq)
^IVG.Ledger("tuple", s, p, o, graphKey) = stmtId    — live statements only ($C(1) = default graph)
^IVG.Ledger("idem", key) = $LB(seq, fingerprint)
```

Value encoding in records: `"N"` = null, `"S"` + value = string (exact stored representation).
Never placed under `^KG`, so `BuildKG`, temporal purges and the functional index purge cannot touch it.

### ^BM25Idx — BM25 Lexical Search

```text
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
