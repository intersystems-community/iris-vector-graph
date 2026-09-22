<!-- markdownlint-disable MD013 -->

# Schema Reference

## SQL Tables (Graph_KG)

Since 4.0.0 (spec 227) a node ID is unique **per graph**, not per namespace, so
every table that points at a node carries `graph_id` and references the composite
key `(graph_id, node_id)` — in that column order, which is the referenced
constraint's own order. The other order is `SQLCODE -121`.

### nodes

```sql
CREATE TABLE Graph_KG.nodes (
    node_id    VARCHAR(256) %EXACT NOT NULL,
    graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',  -- spec 214: empty string = default graph
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id),
    CONSTRAINT uq_nodes_graph_node UNIQUE (graph_id, node_id)
)
```

`uq_nodes_nodeid UNIQUE (node_id)` is gone — that is the 4.0.0 breaking change.

### rdf_labels

```sql
CREATE TABLE Graph_KG.rdf_labels (
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s        VARCHAR(256) %EXACT NOT NULL,
    label    VARCHAR(128) %EXACT NOT NULL,
    CONSTRAINT pk_labels PRIMARY KEY (graph_id, s, label),
    CONSTRAINT fk_labels_node FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id)
)
```

### rdf_props

```sql
CREATE TABLE Graph_KG.rdf_props (
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s        VARCHAR(256) %EXACT NOT NULL,
    key      VARCHAR(128) %EXACT NOT NULL,
    val      VARCHAR(64000) %EXACT,
    CONSTRAINT pk_props PRIMARY KEY (graph_id, s, key)
)
```

### rdf_edges

```sql
CREATE TABLE Graph_KG.rdf_edges (
    edge_id    BIGINT IDENTITY PRIMARY KEY,
    graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s          VARCHAR(256) %EXACT NOT NULL,
    p          VARCHAR(128) %EXACT NOT NULL,
    o_id       VARCHAR(256) %EXACT NOT NULL,
    qualifiers %Library.DynamicObject,
    CONSTRAINT fk_edges_source FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id),
    CONSTRAINT fk_edges_dest FOREIGN KEY (graph_id, o_id) REFERENCES Graph_KG.nodes (graph_id, node_id),
    CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)
)
```

### kg_NodeEmbeddings (and kg_NodeEmbeddings_optimized)

The two legacy tables are the **default route**: same shape a generated route
has, same constraint names 3.2.0 used, re-pointed at the composite key.
`_optimized` differs only in the constraint names (`uq_emb_opt_graph_node`,
`fk_emb_node_opt`).

```sql
CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id  VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    node_id   VARCHAR(256) %EXACT NOT NULL,
    emb       VECTOR(DOUBLE, 768),
    metadata  %Library.DynamicObject,
    CONSTRAINT uq_emb_graph_node UNIQUE (graph_id, node_id),
    CONSTRAINT fk_emb_node FOREIGN KEY (graph_id, node_id) REFERENCES Graph_KG.nodes (graph_id, node_id)
)
```

`id` is gone: the key is `(graph_id, node_id)`, and the primary key is a single
integer identity because IRIS only accepts an HNSW index on a table shaped that
way. A `SELECT id` still parses — `id` is the implicit RowID and returns
integers — so a statement carried over from 3.2.0 fails silently rather than
erroring.

### `kg_emb_<hash>` — routed embedding tables

One table per `(graph_id, model_key)`, named `kg_emb_` + the first 16 hex of
`sha256(graph_id || 0x00 || model_key)`, created on demand by
`resolve_route(..., create=True)` and recorded in `Graph_KG.embedding_registry`.
Same columns as above; constraints are `uq_{table}` and `fk_{table}`. Each one
gets its own ANN index:

```sql
CREATE INDEX idx_{table}_ann ON Graph_KG.{table} (emb) AS HNSW(Distance='Cosine')
```

The build is attempted, never required: a refusal is recorded verbatim in
`embedding_registry.index_error` and the route scans instead of erroring.

### kg*EdgeEmbeddings (and `kg_eemb*<hash>`)

Edge vectors, keyed by graph since 4.0.0 (spec 230, FR-006). `kg_EdgeEmbeddings`
is the **default edge route**, shaped like a generated one so a single INSERT
serves both.

```sql
CREATE TABLE Graph_KG.kg_EdgeEmbeddings (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    s    VARCHAR(256) %EXACT NOT NULL,
    p    VARCHAR(512) %EXACT NOT NULL,
    o_id VARCHAR(256) %EXACT NOT NULL,
    emb  VECTOR(DOUBLE, 768),
    metadata VARCHAR(4000),
    CONSTRAINT uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)
)
```

Through 3.2.0 the primary key was the triple alone, so two graphs asserting the
same edge shared one row and the second write replaced the first. The identity
primary key is the only shape IRIS accepts an HNSW index on, the same reason
`kg_NodeEmbeddings` has one.

Routing is the node tables' routing, with its own prefix: one table per
`(graph_id, model_key)` named `kg_eemb_` + the first 16 hex of
`sha256(graph_id || 0x00 || model_key)`, created on demand and recorded in
`Graph_KG.embedding_registry` alongside the node routes. `kg_emb_` and `kg_eemb_`
are distinct namespaces, and `security.py` validates each against its own
pattern — a name that is not `kg_emb_<16 hex>` or `kg_eemb_<16 hex>` never
reaches SQL.

### docs

The BM25 corpus, keyed by graph since 4.0.0, and `id` is a **node ID** — the same
key space as `nodes.node_id`, not a document ID chosen by the writer.

```sql
CREATE TABLE Graph_KG.docs (
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    id       VARCHAR(256) %EXACT NOT NULL,
    text     VARCHAR(4000) %EXACT,
    CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)
)

CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)
CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic
```

`id` meaning a node ID is what makes `kg_RRF_FUSE` fuse at all: its vector leg
answers node IDs, and through 3.2.0 the FULL OUTER JOIN between those and
free-form document IDs matched nothing, so every "fused" row carried one leg and
NULLs for the other.

No foreign key to `nodes`, deliberately: a caller may write a document before the
node exists, and the 4.0.0 migration has to be able to quarantine a row whose ID
names no node rather than be refused by a constraint.

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

| Procedure              | Signature                                     | Description                                                                      |
| ---------------------- | --------------------------------------------- | -------------------------------------------------------------------------------- |
| `kg_KNN_VEC`           | `(query_vector, k, label, graph_id)`          | HNSW vector search over the default route, one graph. Returns `node_id`, `score` |
| `kg_PPR`               | `(seeds_json, damping, max_iter, ...)`        | Personalized PageRank, returns JSON `[{"id","score"},...]`                       |
| `kg_NEIGHBORS`         | `(sources, predicate, direction)`             | 1-hop neighborhood, returns JSON array                                           |
| `kg_BM25`              | `(name, query, k)`                            | BM25 lexical search, returns JSON `[{"id","score"},...]`                         |
| `kg_RRF_FUSE`          | `(k, k1, k2, c, vector, text, graph_id)`      | Reciprocal rank fusion (vector + text), one graph. Seven arguments since 4.0.0   |
| `ledger_records`       | `(seq_from, seq_to, ordinal_from, page_size)` | Paged mutation records (spec 213); `SELECT * FROM Graph_KG.ledger_records(...)`  |
| `rebuild_edge_indices` | `(schema)`                                    | Rebuild structural-table indices without triggering the functional index purge   |

`kg_BM25` is a thin wrapper over `Graph.KG.BM25Index.Search()` exposed as an SQL stored procedure for Cypher translator Stage CTEs.
It takes no `graph_id`: its corpus is `Graph_KG.docs`, so an index built over one graph's rows only ever answers with them.

---

## Global Structures

### ^KG — Temporal + Structural Graph

Every subtree is keyed by graph immediately after the store name (`g` below).
Specs 214 and 223 put it there; a subscript list one short of these is the
pre-214/223 layout and reads nothing.

```text
^KG("out", g, s, p, o) = weight               — structural outbound edges
^KG("in",  g, o, p, s) = weight               — structural inbound edges
^KG("tout", g, ts, s, p, o) = weight          — temporal outbound (time-ordered within a graph)
^KG("tin",  g, ts, o, p, s) = weight          — temporal inbound
^KG("bucket", g, bucket, s) = count           — 5-min pre-aggregated edge count
^KG("tagg", g, bucket, s, p, key) = value     — COUNT/SUM/AVG/MIN/MAX/HLL per bucket
^KG("edgeprop", g, ts, s, p, o, key) = value  — rich edge attributes
^KG("deg", g, s) = total_degree               — pre-aggregated total degree
^KG("degp", g, s, p) = degree_for_predicate   — pre-aggregated per-predicate degree (for KHopCount O(1))
^KG("prop", g, s, key) = value                — node properties, mirrored from rdf_props
^KG("label", g, label, s) = ""                — label → node index, mirrored from rdf_labels
^KG("deg2p", g, s, p) = two_hop_degree        — 2-hop reach estimate per predicate
^KG("deg2p_exact", g, s, p) = two_hop_degree  — the same count, deduplicated
```

`g` is `Graph.KG.GraphKey.ForIndex(graph_id)`: the graph name, or the **integer
0** for the default graph — which is why the earlier reading of `^KG("out", 0,
...)` as a shard number was wrong. No caller re-derives it (ADR-0003); the
ledger spells the same default graph `$C(1)`, and that second derivation lives
in the same class. The name `"0"` is rejected outright, because IRIS
canonicalizes the subscript `"0"` to the integer that keys the default graph
(ADR-0001).

Graph before timestamp is deliberate: a window scan, purge, verify or erase is
normally one graph's work, and this order bounds it to one subtree.
`^KG("degp", g, s, p)` powers the `KHopCount` fast path — O(1) lookup instead of
a `$Order` scan.

The last four stores got `g` in 4.0.0 (spec 230, FR-010/FR-011); through 3.2.0
they were flat — `^KG("prop", s, key)`, `^KG("label", label, s)`,
`^KG("deg2p", s, p)`, `^KG("deg2p_exact", s, p)` — so two graphs holding the same
node ID shared one entry and the second writer overwrote the first. A 3.2.0
install therefore has entries a 4.0.0 reader looks straight past: the layouts
differ by a subscript, and a `$Order` over the wrong one answers nothing rather
than failing. `upgrade_to_4_0_0`'s `kg_node_stores` step kills all four and has
`Graph.KG.TraversalBuild` rebuild them from the graph-scoped SQL rows; see
[the migration guide](../migration/v4.0.0.md). The scoped `deg2p_exact` rebuild
is ObjectScript only — it no longer takes the Arno acceleration.

`Graph.KG.Loader` still writes the pre-214 shape (`^KG("out", s, p, o)`) and its
own header calls it legacy. Nothing in the engine calls it; data it loads is
invisible to every reader.

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
