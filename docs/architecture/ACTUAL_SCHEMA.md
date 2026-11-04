<!-- markdownlint-disable MD013 -->

# Schema reference

For contributors and curious operators: the tables, indexes, functions and procedures `initialize_schema()` creates,
as written in `iris_vector_graph/schema.py` (`GraphSchema.get_base_schema_sql`, `get_procedures_sql_list`) and
`iris_vector_graph/_engine/schema.py`, checked against that code for 4.6.0. The globals are described in
[Architecture](ARCHITECTURE.md#globals).

Everything here is in the namespace's **IVG Schema**, shown as `Graph_KG`, its default. On a namespace that records
another name (`schema_prefix=`, `IVG_SCHEMA_PREFIX`), the same DDL runs with that name in place of `Graph_KG`
([migrating to 4.6.0](../migration/v4.6.0.md)).

## The graph tables

Since 4.0.0 a node ID is unique **per graph**, not per namespace, so every table that points at a node carries
`graph_id` and references the composite key `(graph_id, node_id)`, in that column order, which is the referenced
constraint's own (the other order is SQLCODE -121). The default graph's `graph_id` is `''`. `%EXACT` keeps a value's
case: IRIS's default collation for `VARCHAR` compares upper-cased.

### nodes

```sql
CREATE TABLE Graph_KG.nodes(
  node_id    VARCHAR(256) %EXACT NOT NULL,
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id),
  CONSTRAINT uq_nodes_graph_node UNIQUE (graph_id, node_id)
)
```

### rdf_labels

```sql
CREATE TABLE Graph_KG.rdf_labels(
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s          VARCHAR(256) %EXACT NOT NULL,
  label      VARCHAR(128) %EXACT NOT NULL,
  CONSTRAINT pk_labels PRIMARY KEY (graph_id, s, label),
  CONSTRAINT fk_labels_node FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id)
)
```

### rdf_props

```sql
CREATE TABLE Graph_KG.rdf_props(
  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s        VARCHAR(256) %EXACT NOT NULL,
  key      VARCHAR(128) %EXACT NOT NULL,
  val      VARCHAR(64000) %EXACT,
  val_key  VARCHAR(128) %EXACT COMPUTECODE {set {*}=$extract({val},1,128)} COMPUTEONCHANGE (val),
  CONSTRAINT pk_props PRIMARY KEY (graph_id, s, key)
)
```

Every value is stored as text (booleans as `true`/`false`, lists and maps as JSON). `val_key` is the first 128
characters of `val`, kept by IRIS, so `idx_props_key_valkey (key, val_key)` can look up a property value that `val`'s
own length makes too long to index.

### rdf_edges

```sql
CREATE TABLE Graph_KG.rdf_edges(
  edge_id    BIGINT IDENTITY PRIMARY KEY,
  graph_id   VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  s          VARCHAR(256) %EXACT NOT NULL,
  p          VARCHAR(128) %EXACT NOT NULL,
  o_id       VARCHAR(256) %EXACT NOT NULL,
  qualifiers %Library.DynamicObject,
  ekey       INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT fk_edges_source FOREIGN KEY (graph_id, s) REFERENCES Graph_KG.nodes (graph_id, node_id),
  CONSTRAINT fk_edges_dest FOREIGN KEY (graph_id, o_id) REFERENCES Graph_KG.nodes (graph_id, node_id),
  CONSTRAINT u_spo_graph_ekey UNIQUE (s, p, o_id, graph_id, ekey)
)
```

`ekey` tells parallel edges apart (4.1.0, spec 234): a graph in multigraph mode can hold the same triple more than
once, each with its own `ekey`; everywhere else `ekey` is 0 and the triple is unique per graph. `qualifiers` holds the
relationship's properties as JSON, `weight` among them when it is not 1.0.

### Indexes on the graph tables

| Index                                                    | On                                                                        |
| -------------------------------------------------------- | ------------------------------------------------------------------------- |
| `idx_labels_s`, `idx_labels_label`, `idx_labels_s_label` | `rdf_labels (s)`, `(label)`, `(s, label)`                                 |
| `idx_props_s`, `idx_props_key`, `idx_props_s_key`        | `rdf_props (s)`, `(key)`, `(s, key)`                                      |
| `idx_props_key_valkey`                                   | `rdf_props (key, val_key)`                                                |
| `idx_props_val_ifind`                                    | `rdf_props (val)`, `%iFind.Index.Basic`                                   |
| `idx_edges_s`, `idx_edges_oid`, `idx_edges_p`            | `rdf_edges (s)`, `(o_id)`, `(p)`                                          |
| `idx_edges_s_p`, `idx_edges_p_oid`                       | `rdf_edges (s, p)`, `(p, o_id)`                                           |
| `idx_edges_graph_id`, `idx_edges_graph_oid`              | `rdf_edges (graph_id)`, `(graph_id, o_id)` (the second new in 4.6.0)      |
| `idx_edges_confidence`                                   | `JSON_VALUE(qualifiers, '$.confidence' RETURNING INTEGER)`                |
| `idx_edges_adj`                                          | `rdf_edges (graph_id, s, p, o_id, qualifiers, ekey) AS Graph.KG.AdjIndex` |
| `idx_labels_adj`                                         | `rdf_labels (graph_id, s, label) AS Graph.KG.LabelIndex`                  |

The last two are the **Adjacency Index** (spec 252): functional indexes whose callbacks write `^KG("out")`, `("in")`
and `("label")` in the row's own transaction.

## Embeddings

### kg_NodeEmbeddings and kg_NodeEmbeddings_optimized: the default route

```sql
CREATE TABLE Graph_KG.kg_NodeEmbeddings (
    emb_rowid BIGINT IDENTITY PRIMARY KEY,
    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
    node_id VARCHAR(256) %EXACT NOT NULL,
    emb VECTOR(DOUBLE, 768),
    metadata %Library.DynamicObject,
    CONSTRAINT uq_emb_graph_node UNIQUE (graph_id, node_id),
    CONSTRAINT fk_emb_node FOREIGN KEY (graph_id, node_id) REFERENCES Graph_KG.nodes (graph_id, node_id)
)
```

The width is the engine's `embedding_dimension` (768 here). `_optimized` is the same table with the constraint names
`uq_emb_opt_graph_node` and `fk_emb_node_opt`. The single-integer primary key is there because IRIS accepts an HNSW
index only on a table shaped that way; `initialize_schema()` creates none on these two. 3.2.0's `id` column is gone,
but `SELECT id` still parses, as the implicit row ID, so a statement carried over from 3.2.0 answers integers rather
than failing.

### `kg_emb_<hash>`: one table per (graph, model)

Created on demand by `resolve_route(..., create=True)` (and by the first write to a new pair), named `kg_emb_` and the
first 16 hex digits of `sha256(graph_id || 0x00 || model_key)`, recorded in `embedding_registry`. The same columns as
above at the route's own width and dtype, constraints `uq_<table>` and `fk_<table>`, and an ANN index attempted when
the table is made:

```sql
CREATE INDEX idx_<table>_ann ON Graph_KG.<table> (emb) AS HNSW(Distance='Cosine')
```

A refusal is recorded in `embedding_registry.index_error` and the route scans.

### kg*EdgeEmbeddings and `kg_eemb*<hash>`

```sql
CREATE TABLE IF NOT EXISTS Graph_KG.kg_EdgeEmbeddings (
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

Edge vectors, keyed by graph since 4.0.0. A routed edge table is `kg_eemb_` and the same hash; the registry's `kind`
column (`node` or `edge`) tells the two kinds of route apart.

### embedding_registry and embedding_quarantine

```sql
CREATE TABLE Graph_KG.embedding_registry (
    table_name      VARCHAR(128)  NOT NULL,
    graph_id        VARCHAR(256)  NOT NULL DEFAULT '',
    kind            VARCHAR(16)   DEFAULT 'node',
    mechanism       VARCHAR(32),
    model_key       VARCHAR(256),
    declared_config VARCHAR(512),
    dimension       INTEGER,
    dtype           VARCHAR(16)   NOT NULL DEFAULT 'DOUBLE',
    set_at          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    set_by          VARCHAR(128),
    index_state     VARCHAR(16),
    index_error     VARCHAR(4000),
    recall_measured DOUBLE,
    recall_measured_at TIMESTAMP,
    CONSTRAINT pk_embedding_registry PRIMARY KEY (table_name, graph_id)
)
```

One row per embedding table and graph: the identity (width, dtype, model) every write is checked against (spec 226,
ADR-0006), and for a route its index state and last measured recall. `idx_registry_route` serves the
`(graph, model) -> table` lookup. `embedding_quarantine` holds vectors the 4.0.0 upgrade could not place in a graph
(`reason` is `ambiguous_graph`, `no_node` or `resolver_declined`); nothing searches it.

## Text search

### docs

```sql
CREATE TABLE Graph_KG.docs(
  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
  id       VARCHAR(256) %EXACT NOT NULL,
  text     VARCHAR(4000) %EXACT,
  CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)
)
CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)
CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic
```

`id` is a node ID, the same key space as `nodes.node_id`, which is what lets `kg_RRF_FUSE` join its vector and text
legs. There is no foreign key to `nodes`, so a document may be written before its node. BM25 indexes keep no table;
they are in `^BM25Idx`.

## Mappings, crosswalks and RDF

| Table                   | Key                                                     | Holds                                                                                                       |
| ----------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `table_mappings`        | `label`                                                 | an existing SQL table read as nodes of a label (`sql_table`, `id_column`, `prop_columns`)                   |
| `relationship_mappings` | `(source_label, predicate, target_label)`               | a foreign key or join table read as edges (`target_fk`, or `via_table`, `via_source`, `via_target`)         |
| `rdf_reifications`      | `reifier_id`                                            | a reifier node for an edge (`edge_id`); `idx_reif_edge` on `edge_id`                                        |
| `rdf_namespaces`        | `prefix`                                                | RDF prefixes for export (`uri`)                                                                             |
| `fhir_bridges`          | `(fhir_code, kg_node_id)`                               | the 4.0-era code-to-node bridge (`fhir_code_system`, `bridge_type`, `confidence`, `source_cui`), deprecated |
| `code_crosswalk`        | `(target_graph, target_node_id, code_system_uri, code)` | a code in a code system mapped to a node in a graph (`relation`, `source`, `source_version`, `confidence`)  |

The FHIR graph's own tables (`fhir_graphs`, `fhir_unresolved`, `fhir_definitions`, `fhir_canonical_refs`) are created
by the first `fhir_graph_register`, so a namespace that never registers a FHIR repository has none of them; their
columns are in `GraphSchema.get_fhir_graph_schema_sql` and their meaning in [FHIR graph](../FHIR_GRAPH.md).

## The ledger's tables

`ledger_revisions` and `ledger_stats` are not created by DDL: they are the SQL projections of the classes
`Graph.KG.LedgerRevision` and `Graph.KG.LedgerStats`, compiled by `initialize_schema()` into the IVG Schema.

- **`ledger_revisions`**: one immutable row per committed revision. `seq` (contiguous from 1), `revision_id`
  (unique), `parent_id`, `kind` (`genesis`, `adoption`, `changeset`, `redaction`), `actor`, `actor_type`, `conn_user`,
  `committed_ms`, `message`, `source` (JSON), `correlation_id`, `idempotency_key`, `op_count`, `stmt_first`,
  `stmt_last`. Triggers refuse `UPDATE` and `DELETE`.
- **`ledger_stats`**: one row, `ledger = 'default'`: `state`, `strict`, `head_seq`, `commits_ok` (every revision
  written, genesis included), `replays_idem`, the refusal counters `rej_stale_head`, `rej_unknown_head`,
  `rej_idem_conflict`, `rej_failed_op`, `rej_strict_block`, `rej_size_limit`, `rej_disabled`, `rej_lock_timeout`, and
  `last_verify_ms`, `last_verify_result`, `enabled_ms`, `disabled_ms`.

The mutation records themselves are in `^IVG.Ledger`, read in pages by the table function
`Graph_KG.ledger_records(seq_from, seq_to, ordinal_from, page_size)`.

## Functions and procedures

Created by `initialize_schema()`, every one in the IVG Schema (before 4.6.0 the functions were in `SQLUser`).

| Kind                              | Names                                                                                                                                                                                                                                                                                                |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Retrieval procedures              | `kg_KNN_VEC(query_vector, k, label, graph_id)`, `kg_TXT`, `kg_RRF_FUSE(k, k1, k2, c, vector, text, graph_id)`                                                                                                                                                                                        |
| Algorithm functions (answer JSON) | `kg_PPR`, `kg_DegreeCentrality`, `kg_Betweenness`, `kg_Closeness`, `kg_Eigenvector`, `kg_Leiden`, `kg_TriangleCount`, `kg_SCC`, `kg_KCore`                                                                                                                                                           |
| Class procedures                  | `kg_BM25(name, query, k)` (`Graph.KG.BM25Index.SearchProc`), `kg_IVF` (`Graph.KG.IVFIndex.SearchProc`), `ledger_records`, `rebuild_edge_indices`, `backfill_temporal_rows`, `MatchEdges`, `MatchEdgesAllGraphs`, `DijkstraPath`, `JSON_KEYS`                                                         |
| Cypher helper functions           | `CY_CMP`, `CY_DURATION_FIELD`, `CY_EXP_ORDKEY`, `CY_EXP_ORDVAL`, `CY_PROPS_MAP`, `CY_RETYPE_MAP`, `CY_REVERSE`, `CY_SORT_KEY`, `CY_TEMPORAL_ARITH`, `CY_TEMPORAL_FIELD`, `CY_VLP_CHAIN`, `CY_VLP_DISJOINT`, `CY_VLP_ENDS`, `CY_VLP_HAS`, `CY_VLP_OPT`, `CY_VLP_PATHS`, the `CypherFn_IVG*` functions |
| General functions                 | `JSON_ARRAYGET`, `JSON_ARRAYLENGTH`, `JSON_VALUE`, `LIST_CONCAT`, `LIST_REVERSE`, `LIST_TAIL`, `NEWID`, `RAND`, `REGEX_MATCH`, `STR_SPLIT`                                                                                                                                                           |

`kg_KNN_VEC` searches the default route of one graph and converts the query with `TO_VECTOR(:queryInput, DOUBLE)`,
with no length on purpose: a query of the wrong width is refused (SQLCODE -257) rather than padded or cut to fit
(ADR-0005). `kg_BM25` takes no graph: an index is built over one graph's documents, so it answers only with them.
The first Arno use also creates the `ivg_arno_*` functions the library is called through.
