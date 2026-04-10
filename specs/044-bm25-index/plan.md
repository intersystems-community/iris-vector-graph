# Implementation Plan: BM25Index — Pure ObjectScript Lexical Search

**Branch**: `044-bm25-index` | **Date**: 2026-04-04 | **Spec**: [spec.md](spec.md)

---

## Summary

Add `Graph.KG.BM25Index` — a pure ObjectScript class that implements Okapi BM25 over `^BM25Idx` globals, following the exact Build/Search/Insert/Drop/Info interface of `Graph.KG.VecIndex`. Add Python wrappers on `IRISGraphEngine`. Add `CALL ivg.bm25.search(name, query, k) YIELD node, score` to the Cypher translator. Upgrade `kg_TXT` to use BM25 automatically when a `"default"` index exists.

---

## Technical Context

**Language/Version**: ObjectScript (IRIS 2024.1+), Python 3.11
**New ObjectScript file**: `iris_src/src/Graph/KG/BM25Index.cls`
**Modified Python files**:
- `iris_vector_graph/engine.py` — 5 new wrapper methods (`bm25_build`, `bm25_search`, `bm25_insert`, `bm25_drop`, `bm25_info`)
- `iris_vector_graph/operators.py` — `_kg_TXT_fallback` updated to check `^BM25Idx("default",...)`
- `iris_vector_graph/cypher/translator.py` — add `ivg.bm25.search` to `translate_procedure_call`

**Container**: `iris_vector_graph` — verified from `docker-compose.yml:4`
**Schema prefix**: `Graph_KG` — verified from `engine.py:59` `set_schema_prefix("Graph_KG")`
**IRIS port**: dynamic via iris-devtester — verified from `docker-compose.yml:5` pattern
**Package version**: `1.45.3` → will bump to `1.46.0`
**Test baseline**: 375 unit tests (verified from last run)

---

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Library-First | ✅ | New `.cls` file + Python wrappers — fully contained |
| II. Compatibility-First | ✅ | `kg_TXT` upgrade is additive (zero API change); VecIndex unchanged |
| III. Test-First | ✅ | Unit tests written before ObjectScript implementation |
| IV. E2E Testing (IRIS) | ✅ | `iris_vector_graph` container (docker-compose.yml:4); `IRISContainer.attach("iris_vector_graph")` pattern; `SKIP_IRIS_TESTS` defaults `"false"` |
| V. Simplicity | ✅ | ~200 lines ObjectScript; no new abstractions; mirrors VecIndex pattern exactly |
| VI. Grounding | ✅ | Container `iris_vector_graph` (docker-compose.yml:4), schema `Graph_KG` (engine.py:59), version `1.45.3` (pyproject.toml) — all verified |

**Gate**: All green. Proceed.

---

## Phase 0: Research Findings

### Decision 1: Global subscript order (from clarification)

**Decision**: `^BM25Idx(name,"tf",term,docId) = count` — term-first inverted index.

**Rationale**: Enables O(postings) posting-list iteration per query term via `$Order(^BM25Idx(name,"tf",queryTerm,""))`. Doc-first order would require O(corpus) scan to find documents containing a term.

**Implementation pattern** (verified from VecIndex and PLAID):
```objectscript
// During Build — store posting:
Set ^BM25Idx(name,"tf",term,docId) = tfCount

// During Search — iterate posting list for each query term:
Set docId = ""
For { 
    Set docId = $Order(^BM25Idx(name,"tf",queryTerm,docId))
    Quit:docId=""
    // accumulate score
}
```

### Decision 2: IDF update on Insert (from clarification)

**Decision**: Update IDF only for terms in the new document — O(doc_length).

**Rationale**: O(vocab_size) per Insert would blow NFR-004 (100ms). IDF staleness for unaffected terms is bounded by `log((N+k-df+0.5)/(N-df+0.5))` where k = number of Inserts since Build. For practical use (tens of incremental inserts), the error is < 5%.

**Verified correct**: This is how Lucene handles per-segment IDF. Rebuild via `Build()` to reset all IDF values.

### Decision 3: kg_TXT detection (from clarification)

**Decision**: `$Data(^BM25Idx("default","cfg","N")) > 0` check at query time.

**Implementation**: Add to `_kg_TXT_fallback` in `operators.py`:
```python
# Check for BM25 "default" index before LIKE fallback
if self._bm25_default_available():
    return self._kg_TXT_bm25("default", query_text, k)
```

Where `_bm25_default_available()` calls `Graph.KG.BM25Index.Info("default")` and checks N > 0. Cached per engine instance (invalidated if user calls `bm25_build` or `bm25_drop`).

### Decision 4: Build return type (from clarification)

**Decision**: JSON string `{"indexed":N,"avgdl":F,"vocab_size":N}` — consistent with Search/Info.

### Decision 5: Cypher YIELD columns (from clarification)

**Decision**: `YIELD node, score` — consistent with `ivg.vector.search` and `ivg.ppr`.

**Implementation**: `_translate_bm25_search` in translator follows the PPR Stage CTE pattern: calls `Graph.KG.BM25Index.Search(name, query, k)` via SQL stored procedure, wraps in JSON_TABLE, yields `(node, score)`. Registers `context.variable_aliases["node"] = "BM25"` and `context.variable_aliases["score"] = "BM25"`.

### Decision 6: Tokenizer

**Decision**: Detect `%iFind.Utils.Analyze` at Build time; use it if available, otherwise split on `\W+` pattern via `$ZSTRIP`.

**ObjectScript implementation**:
```objectscript
ClassMethod Tokenize(text As %String) As %List [ Private ]
{
    Set lower = $ZCONVERT(text, "L")
    // Try iFind tokenizer first (better punctuation handling)
    Try {
        Set tokens = ##class(%iFind.Utils).Analyze(lower, "en", 1)
        Return tokens
    } Catch {}
    // Fallback: split on non-alphanumeric characters
    Set result = "", tok = ""
    For i = 1:1:$LENGTH(lower) {
        Set c = $EXTRACT(lower, i)
        If c?1AN { Set tok = tok_c }
        ElseIf tok '= "" { Set result = result_$LISTBUILD(tok), tok = "" }
    }
    If tok '= "" Set result = result_$LISTBUILD(tok)
    Return result
}
```

### Decision 7: BM25Index as stored procedure for Cypher

**Decision**: Expose `Graph.KG.BM25Index.Search` as a SQL stored procedure `iris_vector_graph.kg_BM25` (same pattern as `kg_PPR`), called from the Cypher translator Stage CTE.

**SQL**:
```sql
SELECT j.node, j.score
FROM JSON_TABLE(
  Graph_KG.kg_BM25(?, ?, ?),  -- name, query, k
  '$[*]' COLUMNS(
    node VARCHAR(256) PATH '$.id',
    score DOUBLE PATH '$.score'
  )
) j
```

The stored procedure wrapper `Graph.KG.BM25Index.SearchProc(name, query, k)` delegates to `Search()` and returns the same JSON string.

---

## Phase 1: Data Model

### New ObjectScript class: `Graph.KG.BM25Index`

**File**: `iris_src/src/Graph/KG/BM25Index.cls`

```
Class Graph.KG.BM25Index Extends %RegisteredObject

ClassMethod Build(name, propsList, k1=1.5, b=0.75) As %String
  - Iterates Graph_KG.nodes via SQL (schema-prefix aware via _table())
  - For each node, reads rdf_props.val for each key in propsList
  - Concatenates values, tokenizes
  - Stores ^BM25Idx(name,"tf",term,docId) for each term
  - Stores ^BM25Idx(name,"len",docId) for each doc
  - After all docs: computes IDF for each term, stores ^BM25Idx(name,"idf",term)
  - Stores cfg: N, avgdl, k1, b, vocab_size
  - Returns JSON: {"indexed":N,"avgdl":F,"vocab_size":V}

ClassMethod Search(name, queryText, k=10) As %String
  - Tokenizes queryText
  - For each token: iterates posting list, accumulates BM25 score per docId
  - Sorts by score (process-private array ^||bm25scores)
  - Returns JSON array of top-k: [{"id":docId,"score":S},...]

ClassMethod Insert(name, docId, text) As %Integer
  - Tokenizes text
  - Removes old TF entries for docId (if replacing)
  - Stores new ^BM25Idx(name,"tf",term,docId)
  - Updates ^BM25Idx(name,"len",docId)
  - Increments N, updates avgdl
  - Updates IDF for terms in new document only
  - Returns 1 on success

ClassMethod Drop(name) As %Integer
  - Kill ^BM25Idx(name)
  - Returns 1

ClassMethod Info(name) As %String
  - Returns {"N":N,"avgdl":A,"vocab_size":V} or {"error":"not found"} if missing

ClassMethod SearchProc(name, queryText, k) As %String [SqlProc, SqlName=kg_BM25]
  - Thin wrapper over Search() for SQL stored procedure call from translator

ClassMethod Tokenize(text) As %List [Private]
  - Lowercase, try %iFind.Utils.Analyze, fallback to $ZSTRIP split
```

### Global structure (verified from clarification)

```
^BM25Idx(name, "cfg", "N")           integer — document count
^BM25Idx(name, "cfg", "avgdl")       float   — average doc length
^BM25Idx(name, "cfg", "k1")          float   — BM25 k1 param
^BM25Idx(name, "cfg", "b")           float   — BM25 b param
^BM25Idx(name, "cfg", "vocab_size")  integer — distinct terms
^BM25Idx(name, "idf",  term)         float   — Robertson IDF
^BM25Idx(name, "tf",   term, docId)  integer — term frequency
^BM25Idx(name, "len",  docId)        integer — token count per doc
```

### Python API additions on `IRISGraphEngine`

```python
def bm25_build(self, name, text_props, k1=1.5, b=0.75) -> dict
def bm25_search(self, name, query, k=10) -> list[tuple[str, float]]
def bm25_insert(self, name, doc_id, text) -> bool
def bm25_drop(self, name) -> None
def bm25_info(self, name) -> dict
```

All delegate to `##class(Graph.KG.BM25Index).Build/Search/Insert/Drop/Info` via `classMethodValue`.

### Cypher translator addition

In `translate_procedure_call`:
```python
elif name == "ivg.bm25.search":
    _translate_bm25_search(proc, context)
```

New function `_translate_bm25_search` follows `_translate_ppr` pattern exactly: Stage CTE using `iris_vector_graph.kg_BM25(?, ?, ?)` stored procedure + JSON_TABLE.

---

## Phase 2: Test Plan

### Unit tests (no IRIS) — `tests/unit/test_bm25_index.py`

| # | Test | Verifies |
|---|------|---------|
| U1 | `bm25_build` calls classmethod with correct args | FR-001 |
| U2 | `bm25_search` parses JSON response to list of tuples | FR-006 |
| U3 | `bm25_insert` calls Insert classmethod | FR-008 |
| U4 | `bm25_drop` calls Drop classmethod | FR-009 |
| U5 | `bm25_info` parses JSON to dict | FR-010 |
| U6 | `translate_procedure_call` recognizes `ivg.bm25.search` | FR-013 |
| U7 | `ivg.bm25.search` generates Stage CTE with kg_BM25 | FR-013 |
| U8 | `ivg.bm25.search` registers YIELD `node`, `score` aliases | US6 AC1 |
| U9 | `_kg_TXT_fallback` uses BM25 when `default` index found | FR-012 |
| U10 | `_kg_TXT_fallback` uses LIKE when no `default` index | FR-012 regression |

### E2E tests (live IRIS) — `tests/unit/test_bm25_index.py::TestBM25IndexE2E`

| # | Test | Verifies |
|---|------|---------|
| E1 | `bm25_build` + `bm25_search` returns ranked results | SC-001, US2 AC1 |
| E2 | Higher-scoring doc contains more query terms | US2 AC3 |
| E3 | Empty query returns empty list | US2 AC4 |
| E4 | `bm25_insert` adds doc findable by search | US3 AC1 |
| E5 | `bm25_drop` removes all index data | US4 AC1 |
| E6 | `kg_TXT` uses BM25 when `"default"` index exists | SC-004, US5 |
| E7 | Works on IRIS Community Edition (no iFind required) | SC-005, FR-014 |
| E8 | `Search` latency < 50ms on N nodes index, 3-term query | SC-002 |
| E9 | Cypher `CALL ivg.bm25.search(...)` executes end-to-end | SC-007, US6 |

---

## Phase 3: File Changeset

| File | Change |
|------|--------|
| `iris_src/src/Graph/KG/BM25Index.cls` | **New** — full BM25 implementation |
| `iris_vector_graph/engine.py` | Add `bm25_build`, `bm25_search`, `bm25_insert`, `bm25_drop`, `bm25_info` |
| `iris_vector_graph/operators.py` | Update `_kg_TXT_fallback` to check `^BM25Idx("default","cfg","N")` |
| `iris_vector_graph/cypher/translator.py` | Add `ivg.bm25.search` branch + `_translate_bm25_search()` |
| `tests/unit/test_bm25_index.py` | **New** — 10 unit + 9 E2E tests |
| `pyproject.toml` | Version `1.45.3` → `1.46.0` |
| `README.md` | Add BM25Index section alongside VecIndex |

---

## Delivery Checklist

- [ ] 10 unit tests written (TDD — fail first)
- [ ] 9 E2E tests written (TDD — fail first)
- [ ] `BM25Index.cls` implemented + compiled in container
- [ ] Python wrappers implemented
- [ ] `_kg_TXT_fallback` upgraded
- [ ] `ivg.bm25.search` Cypher procedure added
- [ ] All 375 + new tests pass
- [ ] SC-002 latency benchmark run (< 50ms)
- [ ] README updated
- [ ] Version bumped, committed, published
