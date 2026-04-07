# Research: BM25Index (044)

## Decision 1: Global subscript order

**Decision**: `^BM25Idx(name,"tf",term,docId) = count` — term-first inverted index.
**Rationale**: Standard inverted index order. `$Order(^BM25Idx(name,"tf",queryTerm,""))` walks the posting list for a term in O(postings). Doc-first would require O(corpus) scan. Clarified in session.
**Verified**: VecIndex uses `^VecIdx(name,"vec",docId)` (doc-keyed for sequential access); PLAID uses `^PLAID(name,"docPacked",docId)` (doc-keyed). BM25's access pattern is term-keyed — needs different structure.

## Decision 2: IDF update on Insert

**Decision**: Update IDF only for terms in the new document.
**Rationale**: O(vocab_size) per Insert would require 100K global writes for a 100K-vocab index, blowing NFR-004 (100ms). O(doc_length) keeps it under 10ms. Staleness bounded by `log((N+k-df+0.5)/(N-df+0.5))` — small for typical incremental use. Clarified in session.
**Alternative rejected**: Full IDF rebuild on every Insert (too slow). Deferred IDF via `Reindex()` (complexity not needed).

## Decision 3: kg_TXT detection

**Decision**: `$Data(^BM25Idx("default","cfg","N")) > 0` check at query time. Zero-config.
**Implementation**: `_kg_TXT_fallback` in `operators.py` checks via `Graph.KG.BM25Index.Info("default")` (Python-side) before falling back to LIKE. Cache result on engine instance.

## Decision 4: Build return type

**Decision**: JSON string `{"indexed":N,"avgdl":F,"vocab_size":N}`.
**Rationale**: Consistent with `BM25Index.Info()`, `BM25Index.Search()`, `VecIndex.Build()` (which returns JSON). Integer 1/0 doesn't tell caller how many nodes were indexed.

## Decision 5: Cypher YIELD columns

**Decision**: `YIELD node, score` — matches `ivg.vector.search` and `ivg.ppr`.
**Verified**: `_translate_ppr` registers `context.variable_aliases["node"] = "PPR"` and `context.variable_aliases["score"] = "PPR"`. `_translate_bm25_search` follows the same pattern with alias `"BM25"`.

## Decision 6: Tokenizer

**Decision**: Detect `%iFind.Utils.Analyze` at runtime; fallback to `$ZCONVERT` + `$ZSTRIP` split.
**Rationale**: `%iFind.Utils` is part of the base IRIS class library (not Enterprise-only). `%iFind.Index.*` is Enterprise-only. The split is safe to use on all tiers.
**Verified**: FR-014 requires Community Edition compatibility. `%iFind.Utils.Analyze` is a utility class, not the indexing framework.

## Decision 7: Cypher integration pattern

**Decision**: SQL stored procedure `Graph_KG.kg_BM25(name, query, k)` + JSON_TABLE Stage CTE.
**Rationale**: Identical pattern to `kg_PPR` (verified in translator.py:414-450). The PPR CTE uses `iris_vector_graph.kg_PPR(?, ?, ?, 0, 1.0)` wrapped in JSON_TABLE. BM25 follows same shape.

## Infrastructure Verification (Constitution VI)

| Detail | Value | Source |
|--------|-------|--------|
| Container name | `iris_vector_graph` | `docker-compose.yml:4` |
| Schema prefix | `Graph_KG` | `engine.py:59` |
| Current version | `1.45.3` | `pyproject.toml` |
| Test baseline | 375 | last pytest run |
| VecIndex interface | Build/Search/Insert/Drop/Info | `iris_src/src/Graph/KG/VecIndex.cls` verified |
| kg_TXT fallback location | `operators.py:269` | verified |
| PPR Cypher translation | `translator.py:414` | verified — exact pattern to follow |
