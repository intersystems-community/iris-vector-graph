# Feature Specification: BM25Index — Pure ObjectScript Lexical Search

**Feature Branch**: `044-bm25-index`
**Created**: 2026-04-04
**Status**: Draft

---

## Overview

IVG currently provides two retrieval views: dense vector search (`Graph.KG.VecIndex` / `kg_NodeEmbeddings`) and structural graph traversal (Cypher over `rdf_edges`). The third view — lexical term-frequency search — is served by `kg_TXT` which calls an optional stored procedure backed by IRIS iFind. iFind is Enterprise-only, requires DDL class definitions, and fails gracefully to a LIKE-based fallback that scores all matches identically.

`Graph.KG.BM25Index` adds real BM25 (Okapi BM25) as a first-class retrieval view: pure ObjectScript globals (`^BM25Idx`), no SQL, no iFind, no Python, works on every IRIS tier. It follows the same Build/Search/Insert/Drop interface as `Graph.KG.VecIndex` and is callable from ObjectScript, Python, and Cypher (`CALL ivg.bm25.search(...)`).

When `kg_TXT` is called and iFind is unavailable, it falls back to BM25Index if one has been built — upgrading all existing callers (`kg_RRF_FUSE`, `HybridSearchFusion`) to real BM25 with zero API changes.

---

## Clarifications

### Session 2026-04-04

- Q: Which subscript order should `^BM25Idx` use for the TF posting list? → A: `^BM25Idx(name,"tf",term,docId) = count` — term-first (standard inverted index). Enables O(postings) sparse iteration per query term. FR-007 and Key Entities updated.
- Q: How should `Insert` handle IDF updates? → A: Update IDF only for terms present in the new document — O(doc_length) per insert, NFR-004 achievable. IDF for unaffected terms is slightly stale but bounded. FR-008 updated.
- Q: How does `kg_TXT` detect that a BM25 "default" index is available? → A: Check `$Data(^BM25Idx("default","cfg","N")) > 0` at query time — zero-config. FR-012 updated.
- Q: What should `Build` return? → A: JSON string `{"indexed":N,"avgdl":F,"vocab_size":N}` — richer and consistent with Info/Search. FR-001, class interface, and US1 example updated.
- Q: What should the YIELD column name be for `CALL ivg.bm25.search`? → A: `YIELD node, score` — consistent with `ivg.vector.search` and `ivg.ppr`. FR-013 and US6 updated.

---

## User Scenarios & Testing

### User Story 1 — Build a BM25 index over graph nodes (P1)

A developer loads NCIT (200K concepts) into the graph and wants term-frequency search over concept names and definitions without iFind or Python.

```objectscript
// Build index over all nodes using rdf_props["name"] + rdf_props["definition"]
Set result = ##class(Graph.KG.BM25Index).Build("ncit", $ListBuild("name","definition"))
Write result, !  // {"indexed":204000,"avgdl":12.4,"vocab_size":87000}
```

```python
# Python equivalent
result = engine.bm25_build("ncit", text_props=["name", "definition"])
# {"indexed": 204000, "avg_doc_length": 12.4, "vocab_size": 87000}
```

**Acceptance Scenarios**:
1. After `Build`, `^BM25Idx("ncit","cfg","N")` = total document count.
2. After `Build`, `^BM25Idx("ncit","cfg","avgdl")` = average document length in tokens.
3. After `Build`, `^BM25Idx("ncit","idf",term)` exists for every token that appears in at least one document.
4. `Build` on an already-built index replaces the previous index (idempotent).
5. `Build` with no nodes in the graph returns `{"indexed": 0}` without error.

---

### User Story 2 — Search a BM25 index (P1)

```objectscript
Set results = ##class(Graph.KG.BM25Index).Search("ncit", "ankylosing spondylitis HLA-B27", 10)
// returns JSON: [{"id":"NCIT:C34796","score":8.41},{"id":"NCIT:C62596","score":7.23},...]
```

```python
results = engine.bm25_search("ncit", "ankylosing spondylitis HLA-B27", k=10)
# [("NCIT:C34796", 8.41), ("NCIT:C62596", 7.23), ...]
```

**Acceptance Scenarios**:
1. Returns top-k results ordered by BM25 score descending.
2. Results contain only nodes whose text contains at least one query term.
3. A node containing all query terms scores higher than one containing only one term (given equal document lengths).
4. Empty query string returns empty list, no error.
5. Query with no matching terms returns empty list.
6. `k` larger than the matching set returns all matches (not fewer than available).

---

### User Story 3 — Add documents incrementally (P2)

```objectscript
// Add KG8 nodes to an existing NCIT index without rebuilding from scratch
Set sc = ##class(Graph.KG.BM25Index).Insert("ncit", "KG8:gene001", "EGFR epidermal growth factor receptor kinase")
```

**Acceptance Scenarios**:
1. After `Insert`, the new node is findable via `Search`.
2. `Insert` updates `avgdl` and `N` correctly.
3. `Insert` on an existing node_id replaces the previous entry.
4. IDF values are updated for new terms introduced by the inserted document.

---

### User Story 4 — Drop an index (P2)

```objectscript
Set sc = ##class(Graph.KG.BM25Index).Drop("ncit")
```

**Acceptance Scenarios**:
1. After `Drop`, all `^BM25Idx("ncit", ...)` subscripts are killed.
2. `Search` on a dropped index returns empty list with a clear warning.
3. `Drop` on a non-existent index returns success (idempotent).

---

### User Story 5 — Automatic kg_TXT upgrade (P2)

When `kg_TXT` is called and iFind is unavailable but a BM25 index named `"default"` exists, it uses BM25 instead of the LIKE fallback.

**Acceptance Scenarios**:
1. After `BM25Index.Build("default", $ListBuild("name"))`, calling `engine.kg_TXT("diabetes")` returns BM25-scored results, not LIKE-scored results.
2. Scores in the upgraded path are real BM25 floats (not 0.0 or 1.0).
3. No API change required for callers of `kg_TXT`.

---

### User Story 6 — Cypher procedure (P3)

```cypher
CALL ivg.bm25.search('ncit', 'ankylosing spondylitis', 10) YIELD node, score
RETURN node, score ORDER BY score DESC
```

**Acceptance Scenarios**:
1. `CALL ivg.bm25.search(name, query, k)` executes BM25 search and yields `(node, score)` rows — consistent with `ivg.vector.search` and `ivg.ppr` conventions.
2. Results can be used directly without a secondary MATCH.
3. Unknown index name returns empty result, not an error.

---

## Requirements

### Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-001 | `Graph.KG.BM25Index.Build(name, propsList, k1, b)` MUST tokenize all `rdf_props.val` for the given property keys, build `^BM25Idx` globals, and return a JSON string `{"indexed":N,"avgdl":F,"vocab_size":N}` |
| FR-002 | `Build` MUST store: `^BM25Idx(name,"cfg","N")`, `^BM25Idx(name,"cfg","avgdl")`, `^BM25Idx(name,"idf",term)`, `^BM25Idx(name,"tf",docId,term)`, `^BM25Idx(name,"len",docId)` |
| FR-003 | BM25 scoring formula MUST be: `score = Σ IDF(t) * TF(t,d) * (k1+1) / (TF(t,d) + k1*(1-b+b*dl/avgdl))` with defaults `k1=1.5, b=0.75` |
| FR-004 | IDF MUST be: `log((N - df + 0.5) / (df + 0.5) + 1)` (smoothed Robertson IDF) |
| FR-005 | Tokenizer MUST split on whitespace and punctuation; MUST normalize to lowercase; MUST use `%iFind.Utils.Analyze` if available, otherwise simple split |
| FR-006 | `Graph.KG.BM25Index.Search(name, queryText, k)` MUST return a JSON array `[{"id":..., "score":...}, ...]` sorted by score descending |
| FR-007 | `Search` MUST iterate only terms present in the query (sparse posting-list traversal via `$Order(^BM25Idx(name,"tf",queryTerm,docId))`) — O(postings per query term), never O(corpus) |
| FR-008 | `Graph.KG.BM25Index.Insert(name, docId, text)` MUST add/replace a single document, increment N, update avgdl, and update IDF for terms present in the new document only — O(doc_length), not O(vocab_size). IDF for terms not in the new document may be slightly stale; this is acceptable and bounded. |
| FR-009 | `Graph.KG.BM25Index.Drop(name)` MUST kill all `^BM25Idx(name,...)` subscripts |
| FR-010 | `Graph.KG.BM25Index.Info(name)` MUST return `{"N":int, "avgdl":float, "vocab_size":int}` |
| FR-011 | Python wrappers `bm25_build`, `bm25_search`, `bm25_insert`, `bm25_drop`, `bm25_info` MUST exist on `IRISGraphEngine` |
| FR-012 | When `kg_TXT` falls back to LIKE, it MUST first check `$Data(^BM25Idx("default","cfg","N")) > 0`; if true, it MUST call `BM25Index.Search("default", query, k)` instead — zero-config, no registration required |
| FR-013 | Cypher `CALL ivg.bm25.search(name, query, k) YIELD node, score` MUST be supported by the translator — consistent with `ivg.vector.search` and `ivg.ppr` YIELD conventions |
| FR-014 | All ObjectScript code MUST run on IRIS Community Edition — no iFind, no Enterprise-only classes required |

### Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | `Search` on a 200K-node index for a 3-term query MUST complete in under 50ms |
| NFR-002 | `Build` on 200K nodes MUST complete in under 5 minutes |
| NFR-003 | `^BM25Idx` global footprint for a 200K-node/100K-vocab index MUST be under 500MB |
| NFR-004 | `Insert` MUST complete in under 100ms regardless of index size |

---

## Key Entities

### Global Structure

```
^BM25Idx(name, "cfg", "N")          = total document count (integer)
^BM25Idx(name, "cfg", "avgdl")      = average document length in tokens (float)
^BM25Idx(name, "cfg", "k1")         = BM25 k1 parameter (default 1.5)
^BM25Idx(name, "cfg", "b")          = BM25 b parameter (default 0.75)
^BM25Idx(name, "idf", term)         = smoothed Robertson IDF value (float)
^BM25Idx(name, "tf", term, docId)   = raw term frequency count (integer)  ← term-first for posting-list iteration
^BM25Idx(name, "len", docId)        = document token count (integer)
```

The inverted index is term-first: to find all documents containing `term`, iterate `$Order(^BM25Idx(name,"tf",term,""))`. To score a query, for each query term walk the posting list `$Order(^BM25Idx(name,"tf",queryTerm,docId))` — O(postings), never O(corpus).

### ObjectScript Class Interface

```objectscript
Class Graph.KG.BM25Index Extends %RegisteredObject
{
    ClassMethod Build(name As %String, propsList As %List,
                      k1 As %Double = 1.5, b As %Double = 0.75) As %String  // JSON: {"indexed":N,"avgdl":F,"vocab_size":N}
    ClassMethod Search(name As %String, queryText As %String,
                       k As %Integer = 10) As %String  // JSON
    ClassMethod Insert(name As %String, docId As %String,
                       text As %String) As %Integer
    ClassMethod Drop(name As %String) As %Integer
    ClassMethod Info(name As %String) As %String  // JSON
    ClassMethod Tokenize(text As %String) As %List  // private
}
```

---

## Success Criteria

| ID | Criterion | Measurement |
|----|-----------|-------------|
| SC-001 | BM25 search returns ranked results on NCIT 204K nodes | US2 acceptance scenarios pass |
| SC-002 | `Search` latency < 50ms on 200K-node index, 3-term query | NFR-001 benchmark |
| SC-003 | `Build` completes in under 5 minutes on 200K nodes | NFR-002 benchmark |
| SC-004 | `kg_TXT` uses BM25 when `"default"` index exists | US5 acceptance scenarios pass |
| SC-005 | Works on IRIS Community Edition without iFind | Test on community container |
| SC-006 | No regression — all 375 existing unit tests pass | `pytest tests/unit/ -q` |
| SC-007 | Cypher `CALL ivg.bm25.search(...)` executes correctly | US6 acceptance scenarios pass |

---

## Edge Cases

- Node with empty text (all props null/missing) → skipped during Build, not indexed
- Query term not in vocabulary → contributes 0 to score (not an error)
- Single-document index → avgdl = that document's length; IDF of any term = `log(1.5)` ≈ 0.405
- `k1=0` collapses to binary presence scoring (TF ignored) — valid edge case, not an error
- Very long documents (>10K tokens) → indexed normally; no truncation
- Non-ASCII text → lowercased via `$ZCONVERT(token, "L")`; Unicode handled correctly
- Concurrent `Insert` calls — each updates `N` and `avgdl` via `$Increment` (atomic for integers); `avgdl` update is not atomic (racy), documented as acceptable for Phase 1
- Index name containing special characters → validated with `sanitize_identifier` before use as global subscript
- IDF staleness after many Inserts — after N incremental inserts, IDF values for terms not present in any inserted document reflect the Build-time N. The scoring error is bounded by `log((N_current - df + 0.5)/(N_build - df + 0.5))`. Rebuild via `Build()` to restore exact IDF values.

---

## Out of Scope

- Stop word removal (deferred — adds complexity, benefit depends on corpus)
- Stemming (deferred — language-specific; `%iFind.Utils` provides it if available)
- Persisting BM25 index across IRIS restarts to a durable location (globals are durable; IRISTEMP-mapped globals are not — use USER namespace)
- BM25+ variant (TF lower-bound) — standard BM25 is sufficient
- Phrase queries ("ankylosing spondylitis" as a phrase, not AND) — bag-of-words scoring only

---

## Assumptions

- Node text lives in `rdf_props.val` for the specified property keys (consistent with how `embed_nodes` works)
- Index name is a short alphanumeric string (validated by `sanitize_identifier`)
- IVG nodes are in `Graph_KG.nodes` / `Graph_KG.rdf_props` under the configured schema prefix (uses `_table()` for schema awareness, same as all other IVG queries)
- `%iFind.Utils.Analyze` availability is detected at runtime; graceful fallback to simple tokenizer if absent
- The `"default"` index name is the convention for the automatic `kg_TXT` upgrade; users can build differently-named indexes for domain-specific search without affecting `kg_TXT`
