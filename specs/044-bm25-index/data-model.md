# Data Model: BM25Index (044)

## New ObjectScript Class

**`Graph.KG.BM25Index`** — `iris_src/src/Graph/KG/BM25Index.cls`

No new SQL tables. All state lives in `^BM25Idx` globals.

## Global Structure

```
^BM25Idx(name)                        — index root (implicit)
^BM25Idx(name, "cfg", "N")           integer — total document count
^BM25Idx(name, "cfg", "avgdl")       float   — average doc length in tokens
^BM25Idx(name, "cfg", "k1")          float   — BM25 k1 parameter
^BM25Idx(name, "cfg", "b")           float   — BM25 b parameter
^BM25Idx(name, "cfg", "vocab_size")  integer — distinct token count
^BM25Idx(name, "idf",  term)         float   — Robertson IDF value
^BM25Idx(name, "tf",   term, docId)  integer — term frequency (term-first!)
^BM25Idx(name, "len",  docId)        integer — document token count
```

**Key constraint**: `"tf"` subscripts are term-first (term at level 3, docId at level 4). This enables efficient posting-list iteration: `$Order(^BM25Idx(name,"tf",queryTerm,""))` walks all docs containing `queryTerm` in O(postings).

## Index Lifecycle States

```
[not built] → Build() → [built, clean]
                              ↓
                         Insert() → [built, with stale IDF for unaffected terms]
                              ↓
                         Build() → [built, clean]  (full rebuild resets IDF)
                              ↓
                          Drop() → [not built]
```

## Access Patterns

| Operation | Access pattern | Complexity |
|-----------|---------------|------------|
| Build | Write all tf, idf, len, cfg | O(corpus × avg_doc_len) |
| Search | Read idf[term], tf[term,*], len[*] for query terms | O(|query| × avg_postings) |
| Insert | Write tf[term,*], len[docId]; update N, avgdl, idf for new terms | O(doc_len) |
| Drop | Kill ^BM25Idx(name) | O(1) |
| Info | Read cfg[N], cfg[avgdl], cfg[vocab_size] | O(1) |

## Naming Constraints

- `name` MUST pass `sanitize_identifier` (alphanumeric + underscore + dot)
- `term` is lowercase, max ~100 chars (truncated silently if longer)
- `docId` is the `node_id` from `Graph_KG.nodes` — format `NCIT:C12345`, `KG8:gene001`, etc.

## Relationship to Existing Globals

| Global | Purpose | Relationship to BM25Index |
|--------|---------|--------------------------|
| `^VecIdx` | Dense vector ANN index | Independent — different access pattern, shared naming convention |
| `^PLAID` | Multi-vector ColBERT index | Independent |
| `^KG` | Temporal/structural graph edges | Source data for `docId` values |
| `^BM25Idx` | BM25 lexical index | New — this spec |

## Python API Contract

```python
# All methods on IRISGraphEngine

bm25_build(name: str, text_props: list[str], k1: float = 1.5, b: float = 0.75) -> dict
  # Returns: {"indexed": int, "avgdl": float, "vocab_size": int}
  # Raises: ValueError if name fails sanitize_identifier

bm25_search(name: str, query: str, k: int = 10) -> list[tuple[str, float]]
  # Returns: [(node_id, score), ...] sorted by score DESC
  # Returns: [] if index not found or query has no matching terms

bm25_insert(name: str, doc_id: str, text: str) -> bool
  # Returns: True on success

bm25_drop(name: str) -> None

bm25_info(name: str) -> dict
  # Returns: {"N": int, "avgdl": float, "vocab_size": int}
  # Returns: {} if index not found
```

## Cypher Procedure Contract

```
CALL ivg.bm25.search(name, query, k)
  Arguments:
    name  : string literal or $parameter — index name
    query : string literal or $parameter — query text
    k     : integer literal or $parameter — top-k results
  YIELD:
    node  : VARCHAR(256) — node_id
    score : DOUBLE — BM25 score
```

Generated SQL Stage CTE:
```sql
BM25 AS (
  SELECT j.node, j.score
  FROM JSON_TABLE(
    Graph_KG.kg_BM25(?, ?, ?),
    '$[*]' COLUMNS(
      node VARCHAR(256) PATH '$.id',
      score DOUBLE PATH '$.score'
    )
  ) j
)
```
