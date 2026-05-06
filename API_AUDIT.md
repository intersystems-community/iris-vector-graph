# IRISGraphEngine Public API Audit

## Summary
- **Total Public Methods**: 103
- **Methods with E2E/Unit Tests**: 66 (64%)
- **Undocumented/Untested Methods**: 37 (36%)

---

## 1. INITIALIZATION & CONNECTION (3 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `from_connect` | `(cls, hostname, port=1972, namespace="USER", username="_SYSTEM", password="SYS", embedding_dimension=None, **kwargs)` | `IRISGraphEngine` | ❌ | Create from remote IRIS connection |
| `__init__` | `(conn, embedding_dimension=None, embedder=None, embedding_config=None, embed_fn=None, use_iris_embedding=False)` | `None` | ✅ | Create with existing connection |
| `is_ready` | `()` | `bool` | ❌ | Test connection health |

---

## 2. SCHEMA & SETUP (1 method)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `initialize_schema` | `(auto_deploy_objectscript=True)` | `dict` | ✅ | DDL creation, index setup, ObjectScript deploy |

---

## 3. INTROSPECTION & METADATA (8 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `get_labels` | `()` | `List[str]` | ✅ | Get all node labels |
| `get_relationship_types` | `()` | `List[str]` | ✅ | Get all predicates |
| `get_label_distribution` | `()` | `Dict[str, int]` | ✅ | Node count per label |
| `get_property_keys` | `(label=None)` | `List[str]` | ✅ | Properties for label(s) |
| `get_node_count` | `(label=None)` | `int` | ✅ | Count nodes (optionally by label) |
| `get_edge_count` | `(predicate=None)` | `int` | ✅ | Count edges (optionally by predicate) |
| `node_exists` | `(node_id)` | `bool` | ✅ | Check node presence |
| `get_schema_visualization` | `()` | `dict` | ❌ | Neo4j-style schema response |

---

## 4. QUERY EXECUTION (1 method)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `execute_cypher` | `(cypher_query, parameters=None, read_only=False)` | `Dict[str, Any]` | ✅ | Parse & execute openCypher → SQL |

---

## 5. NODE CRUD (4 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `create_node` | `(node_id, labels=None, properties=None)` | `bool` | ✅ | Single node creation |
| `get_node` | `(node_id)` | `Optional[Dict]` | ❌ | Retrieve full node (labels + props) |
| `get_nodes` | `(node_ids)` | `List[Dict]` | ✅ | Batch node retrieval |
| `delete_node` | `(node_id)` | `bool` | ❌ | Delete node + edges |
| `count_nodes` | `(label=None)` | `int` | ❌ | Count nodes (alias for `get_node_count`) |
| `bulk_create_nodes` | `(nodes, disable_indexes=True)` | `List[str]` | ✅ | High-performance batch insert |

---

## 6. EDGE CRUD (4 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `create_edge` | `(source_id, predicate, target_id, qualifiers=None, graph=None)` | `bool` | ✅ | Single edge creation (sync to ^KG) |
| `delete_edge` | `(source_id, predicate, target_id)` | `bool` | ✅ | Delete edge + ^KG entry |
| `bulk_create_edges` | `(edges, disable_indexes=True, graph=None, auto_rebuild_kg=True)` | `int` | ✅ | Batch edge creation (lazy ^KG) |
| `rebuild_kg` | `()` | `bool` | ❌ | Rebuild `^KG` global from `rdf_edges` |

---

## 7. TEMPORAL EDGES (7 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `create_edge_temporal` | `(source, predicate, target, timestamp=None, weight=1.0, attrs=None, upsert=False, graph=None)` | `bool` | ✅ | Single time-stamped edge (→ `^KG("tout")`) |
| `bulk_create_edges_temporal` | `(edges, upsert=False, graph=None)` | `int` | ✅ | Batch temporal edges (134K+/sec) |
| `get_edges_in_window` | `(source="", predicate="", start=0, end=0, direction="out")` | `list` | ✅ | Query time-range (O(results)) |
| `get_edge_velocity` | `(node_id, window_seconds=300)` | `int` | ✅ | Edge count in window (O(1) pre-agg) |
| `find_burst_nodes` | `(predicate="", window_seconds=300, threshold=50)` | `list` | ✅ | Nodes exceeding edge threshold |
| `get_edge_attrs` | `(ts, source, predicate, target)` | `dict` | ✅ | Retrieve rich edge attributes |
| `purge_before` | `(ts)` | `None` | ✅ | Delete edges older than ts |

---

## 8. TEMPORAL ANALYTICS (4 methods - O(1) PRE-AGGREGATED)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `get_temporal_aggregate` | `(source, predicate, metric, ts_start, ts_end)` | `float\|int` | ✅ | COUNT/SUM/AVG/MIN/MAX per bucket |
| `get_bucket_groups` | `(predicate="", ts_start=0, ts_end=0, source_prefix="")` | `list` | ✅ | GROUP BY source per bucket |
| `get_bucket_group_targets` | `(source, predicate, ts_start, ts_end)` | `List[str]` | ✅ | Distinct targets in window |
| `get_distinct_count` | `(source, predicate, ts_start, ts_end)` | `int` | ✅ | HLL COUNT DISTINCT targets |

---

## 9. GRAPH TRAVERSAL & PATH ALGORITHMS (3 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `khop` | `(seed, hops=2, max_nodes=500)` | `dict` | ❌ | K-hop BFS expansion |
| `ppr` | `(seed, alpha=0.85, max_iter=20, top_k=20)` | `dict` | ❌ | Personalized PageRank (graph operators) |
| `random_walk` | `(seed, length=20, num_walks=10)` | `list` | ❌ | Random walk traces |

---

## 10. VECTOR SEARCH - VecIndex (7 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `vec_create_index` | `(name, dim, metric="cosine", num_trees=4, leaf_size=50)` | `dict` | ❌ | Create RP-tree index |
| `vec_insert` | `(index_name, doc_id, embedding)` | `None` | ❌ | Single vector insert |
| `vec_bulk_insert` | `(index_name, items)` | `int` | ❌ | Batch vector insert |
| `vec_build` | `(index_name)` | `dict` | ❌ | Finalize tree (run splitting) |
| `vec_search` | `(index_name, query_embedding, k=10, nprobe=8)` | `list` | ❌ | Query RP-tree |
| `vec_search_multi` | `(index_name, query_embeddings, k=10, nprobe=8)` | `list` | ❌ | Multi-query batch search |
| `vec_info` | `(index_name)` | `dict` | ❌ | Index metadata |
| `vec_drop` | `(index_name)` | `None` | ❌ | Delete index |
| `vec_expand` | `(index_name, seed_id, k=5)` | `list` | ❌ | Neighborhood expansion |

---

## 11. VECTOR SEARCH - IVFFlat (4 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `ivf_build` | `(name, nlist=256, metric="cosine", batch_size=10000)` | `dict` | ✅ | K-means build + inverted index |
| `ivf_search` | `(name, query, k=10, nprobe=8)` | `list` | ✅ | Approximate search (nprobe tuning) |
| `ivf_drop` | `(name)` | `None` | ✅ | Delete index |
| `ivf_info` | `(name)` | `dict` | ✅ | Index metadata |

---

## 12. VECTOR SEARCH - HNSW & TEXT (5 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `kg_KNN_VEC` | `(query_vector, k=50, label_filter=None)` | `List[Tuple[str, float]]` | ❌ | Native IRIS HNSW search |
| `kg_TXT` | `(query_text, k=50, min_confidence=0)` | `List[Tuple[str, float]]` | ❌ | iFind text search (DEPRECATED?) |
| `vector_search` | `(table, vector_col, query_embedding, top_k=10, id_col="id", return_cols=None, score_threshold=None)` | `List[dict]` | ❌ | Search any VECTOR column |
| `validate_vector_table` | `(table, vector_col)` | `dict` | ❌ | Get dimension + row count |
| `multi_vector_search` | `(sources, query_embedding, top_k=10, fusion="rrf", rrf_k=60)` | `List[dict]` | ❌ | Multi-table RRF fusion |

---

## 13. HYBRID SEARCH & FUSION (3 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `kg_RRF_FUSE` | `(k, k1, k2, c, query_vector, query_text)` | `List[Tuple[str, float, float, float]]` | ❌ | Reciprocal Rank Fusion |
| `kg_VECTOR_GRAPH_SEARCH` | `(query_vector, query_text=None, k=15, expansion_depth=1, min_confidence=0.5)` | `List[Dict]` | ❌ | Vector + graph expansion |
| `kg_NEIGHBORHOOD_EXPANSION` | `(entity_list, expansion_depth=1, confidence_threshold=500)` | `List[Dict]` | ❌ | Multi-entity k-hop expansion |

---

## 14. GRAPH ANALYTICS (1 method)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `kg_PERSONALIZED_PAGERANK` | `(seed_entities, damping_factor=0.85, max_iterations=100, tolerance=1e-6, return_top_k=None, bidirectional=False, reverse_edge_weight=1.0)` | `Dict[str, float]` | ❌ | PPR computation |

---

## 15. EMBEDDINGS - NODE EMBEDDINGS (7 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `embed_text` | `(text)` | `List[float]` | ❌ | Text → vector (IRIS EMBEDDING / Python fallback) |
| `store_embedding` | `(node_id, embedding, metadata=None)` | `bool` | ✅ | Single embedding storage |
| `store_embeddings` | `(items)` | `bool` | ✅ | Batch embedding storage |
| `embed_nodes` | `(model=None, where=None, text_fn=None, batch_size=500, force=False, progress_callback=None, label=None, node_ids=None)` | `dict` | ✅ | Auto-embed nodes (typed params) |
| `get_embedding` | `(node_id)` | `Optional[Dict]` | ❌ | Retrieve single embedding |
| `get_embeddings` | `(node_ids)` | `List[Dict]` | ❌ | Retrieve batch embeddings |
| `get_unembedded_nodes` | `()` | `List[str]` | ❌ | Find nodes without embeddings |

---

## 16. EMBEDDINGS - EDGE EMBEDDINGS (2 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `embed_edges` | `(model=None, text_fn=None, where=None, batch_size=500, force=False, progress_callback=None)` | `dict` | ✅ | Embed all `(s, p, o)` triples |
| `edge_vector_search` | `(query_embedding, top_k=10, score_threshold=None)` | `List[dict]` | ✅ | Cosine search on `kg_EdgeEmbeddings` |

---

## 17. PLAID MULTI-VECTOR (5 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `plaid_build` | `(name, docs, n_clusters=None, dim=128)` | `dict` | ✅ | Build PLAID index (K-means + inverted) |
| `plaid_search` | `(name, query_tokens, k=10, nprobe=4)` | `list` | ✅ | Search PLAID (centroid scoring → MaxSim) |
| `plaid_insert` | `(name, doc_id, token_embeddings)` | `None` | ❌ | Insert document into PLAID |
| `plaid_info` | `(name)` | `dict` | ❌ | PLAID index metadata |
| `plaid_drop` | `(name)` | `None` | ❌ | Delete PLAID index |

---

## 18. BM25 FULL-TEXT INDEX (5 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `bm25_build` | `(name, text_props, k1=1.5, b=0.75)` | `dict` | ✅ | Build BM25 index (0.3ms search) |
| `bm25_search` | `(name, query, k=10)` | `list` | ✅ | Query BM25 |
| `bm25_insert` | `(name, doc_id, text)` | `bool` | ✅ | Incremental document add |
| `bm25_drop` | `(name)` | `None` | ✅ | Delete BM25 index |
| `bm25_info` | `(name)` | `dict` | ✅ | BM25 index metadata |

---

## 19. SQL TABLE BRIDGE (6 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `map_sql_table` | `(table, id_column, label, property_columns=None)` | `dict` | ✅ | Register SQL table as nodes |
| `get_table_mapping` | `(label)` | `Optional[dict]` | ✅ | Get mapping for label |
| `map_sql_relationship` | `(source_label, predicate, target_label, target_fk=None, via_table=None, via_source=None, via_target=None)` | `dict` | ✅ | Define relationship mapping |
| `get_rel_mapping` | `(source_label, predicate, target_label)` | `Optional[dict]` | ❌ | Get rel mapping |
| `list_table_mappings` | `()` | `dict` | ✅ | List all table/rel mappings |
| `remove_table_mapping` | `(label)` | `None` | ✅ | Unregister table mapping |
| `reload_table_mappings` | `()` | `None` | ❌ | Refresh mapping cache |
| `attach_embeddings_to_table` | `(label, text_columns, batch_size=1000, force=False, progress_callback=None)` | `dict` | ✅ | Overlay embeddings on mapped table |

---

## 20. NAMED GRAPHS (2 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `list_graphs` | `()` | `List[str]` | ✅ | Get all graph IDs |
| `drop_graph` | `(graph_id)` | `int` | ✅ | Delete named graph |

---

## 21. DATA INGEST & EXPORT (5 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `load_networkx` | `(G, label_attr="type", skip_existing=True, progress_callback=None, auto_rebuild_kg=True)` | `dict` | ✅ | Ingest NetworkX graph |
| `load_obo` | `(path_or_url, prefix=None, encoding="utf-8", encoding_errors="replace", progress_callback=None)` | `dict` | ✅ | Ingest OBO ontology (NCIT, etc.) |
| `import_rdf` | `(path, format=None, batch_size=10000, progress=None, infer=False, graph=None)` | `Dict[str, int]` | ✅ | Import Turtle/N-Triples/N-Quads (auto-infer) |
| `import_graph_ndjson` | `(path, upsert_nodes=True, batch_size=10000)` | `dict` | ✅ | Load NDJSON graph dump |
| `export_graph_ndjson` | `(path)` | `dict` | ❌ | Export full graph as NDJSON |
| `export_temporal_edges_ndjson` | `(path, start=None, end=None, predicate=None)` | `dict` | ✅ | Export time-window edges |

---

## 22. SNAPSHOT & PERSISTENCE (3 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `save_snapshot` | `(path, layers=None)` | `Dict[str, Any]` | ✅ | Export portable `.ivg` ZIP (SQL + globals) |
| `restore_snapshot` | `(path, merge=False)` | `Dict[str, Any]` | ✅ | Import from `.ivg` (destructive/merge) |
| `snapshot_info` | `(path)` → `@staticmethod` | `Dict[str, Any]` | ✅ | Inspect snapshot metadata (no connection) |

---

## 23. INFERENCE & REIFICATION (4 methods)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `materialize_inference` | `(rules="rdfs", graph=None)` | `Dict[str, int]` | ❌ | Expand RDFS/OWL inference rules |
| `retract_inference` | `(graph=None)` | `int` | ❌ | Remove inferred triples |
| `reify_edge` | `(edge_id, reifier_id=None, label="Reification", props=None)` | `Optional[str]` | ✅ | RDF 1.2 edge reification |
| `get_reifications` | `(edge_id)` | `List[Dict]` | ✅ | Retrieve reifiers for edge |
| `delete_reification` | `(reifier_id)` | `bool` | ✅ | Remove reification |

---

## 24. FHIR BRIDGE (1 method)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `get_kg_anchors` | `(icd_codes, bridge_type="icd10_to_mesh")` | `List[str]` | ✅ | ICD-10 → MeSH mapping |

---

## 25. ENGINE STATUS (1 method)

| Method | Signature | Return | Tests | Status |
|--------|-----------|--------|-------|--------|
| `status` | `()` | `EngineStatus` | ✅ | Comprehensive runtime snapshot (SQL/globals/capabilities) |

---

## EXPOSED IN __init__.py (8 classes/modules)
- `IRISGraphEngine` ✅
- `GraphSchema` ✓ (14 static methods, all schema-related)
- `EngineStatus` ✓ (status snapshot)
- `IRISCapabilities` ✓ (capability flags)
- `VectorOptimizer` ✓ (vector utilities)
- `TextSearchEngine` ✓ (text utilities)
- `RRFFusion` ✓ (hybrid ranking)
- `EmbeddedConnection` / `EmbeddedCursor` ✓ (IRIS Language=python adapter)

---

## CRITICAL GAPS & OBSERVATIONS

### 1. UNDOCUMENTED METHODS (37/103 = 36%)
The following methods exist in code but appear untested and lack docstrings in many cases:
- Vector index management (`vec_*` - 9 methods)
- PLAID auxiliary ops (`plaid_insert/info/drop` - 3 methods)
- Graph algorithms (`khop`, `ppr`, `random_walk` - 3 methods)
- HNSW/text search (`kg_KNN_VEC`, `kg_TXT`, `kg_NEIGHBORHOOD_EXPANSION` - 3 methods)
- Hybrid fusion (`kg_RRF_FUSE`, `kg_VECTOR_GRAPH_SEARCH`, `multi_vector_search` - 3 methods)
- Node/edge retrieval (`get_node`, `get_embedding`, `delete_node` - 3 methods)
- Inference ops (`materialize_inference`, `retract_inference` - 2 methods)
- Misc utilities (`vector_search`, `validate_vector_table`, `is_ready`, `from_connect` - 4 methods)

### 2. METHODS THAT ARE ALIASES / REDUNDANT
- `count_nodes(label)` → Alias for `get_node_count(label)` (both exist, both public)
- `kg_PERSONALIZED_PAGERANK()` → Wrapper around `IRISGraphOperators.kg_PAGERANK()` (redundant public API)

### 3. METHODS WITH CONFLICTING SIGNATURES
- `embed_nodes()` has both old (`where=`) and new (`label=`, `node_ids=`) params with deprecation handling
- `embed_text()` tries 3 fallbacks (IRIS native → Python embedder → auto-load SentenceTransformer)

### 4. OBSERVABLE PATTERNS
- **Edge creation has dual API**: `create_edge()` (sync to `^KG` immediately) vs `bulk_create_edges()` (lazy, requires `BuildKG()`)
- **Vector indexes scattered**: `vec_*` (RP-tree), `ivf_*` (IVF flat), HNSW (native), BM25 (lexical), PLAID (multi-vector) — no unified interface
- **Graph algorithms use separate `IRISGraphOperators` class**: `khop/ppr/random_walk` are thin wrappers
- **Temporal API is comprehensive but sweet-spot-specific**: Good for ≤50-edge trajectories; large aggregations should use pre-agg methods

### 5. DOCUMENTATION QUALITY
- Core CRUD methods (create_node, create_edge, bulk_create_*) have docstrings ✅
- Vector search methods largely lack docstrings ❌
- Temporal methods well-documented ✅
- Graph algorithm wrappers underdocumented ❌

### 6. TESTING STRATEGY GAPS
- No E2E tests for `from_connect()` class method
- No tests for read_only=True mode in `execute_cypher()`
- RP-tree VecIndex untested (only IVFFlat tested)
- PLAID insertion/lifecycle untested
- Graph algorithms (khop/ppr/random_walk) untested
- Inference materialization untested
- SQL table bridge attachment untested

---

## RECOMMENDATIONS

### High Priority
1. **Document all 37 untested methods** — add docstrings explaining use case, parameters, return structure
2. **Add E2E tests for**:
   - `vec_*` full lifecycle (create → insert → build → search → drop)
   - `khop()` / `ppr()` / `random_walk()` with real graphs
   - `materialize_inference()` / `retract_inference()`
   - `from_connect()` classmethod
3. **Consolidate vector search API** — unify `vec_*`, `ivf_*`, `kg_KNN_VEC` under a single ergonomic interface
4. **Remove redundant methods** — `count_nodes()` is just an alias for `get_node_count()`

### Medium Priority
5. **Standardize error handling** — some methods return None, others raise, others return empty dict
6. **Add validation tests** for `validate_vector_table()` / `validate_schema()`
7. **Document readonly mode** of `execute_cypher()` with examples
8. **Test graph algorithm edge cases** (empty graphs, single-node, disconnected components)

### Low Priority
9. **Consider deprecating `count_nodes()`** in favor of `get_node_count()` for API simplification
10. **Add CLI tool** for common operations (`ivg-cli create-index`, `ivg-cli embed-nodes`, etc.)

