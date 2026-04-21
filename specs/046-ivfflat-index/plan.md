# Implementation Plan: IVFFlat Vector Index (spec 046)

**Branch**: `046-ivfflat-index` | **Date**: 2026-04-18 | **Spec**: `specs/046-ivfflat-index/spec.md`

## Summary

Add an IVFFlat index to iris-vector-graph: Python k-means build (sklearn) + pure ObjectScript
query-time search via `$vectorop`. Reuses PLAID centroid scoring + `VecIndex` distance
primitives. Exposes `ivf_build / ivf_search / ivf_drop / ivf_info` on `IRISGraphEngine` and
a `CALL ivg.ivf.search(...)` Cypher procedure.

## Technical Context

**Language/Version**: Python 3.11 (build) + ObjectScript (IRIS 2024.1+, query)  
**Primary Dependencies**: `sklearn.cluster.MiniBatchKMeans`, `numpy` (build only, same as PLAID); `intersystems-irispython`, `iris-devtester` (test)  
**Storage**: `^IVF` global (new, independent of `^KG`, `^VecIdx`, `^PLAID`, `^BM25Idx`)  
**Testing**: pytest (unit + E2E against `iris_vector_graph` container)  
**Target Platform**: IRIS 2024.1+ (any tier — pure ObjectScript query path)  
**Performance Goals**: build nlist=256 / 10K / 768-dim < 30s; search nprobe=8 < 10ms; recall@10 ≥ 0.90 at nprobe=32  
**Constraints**: query-time code is 100% ObjectScript + `$vectorop` (no Python at search time)  
**Scale/Scope**: single-node, single-namespace; `kg_NodeEmbeddings` as v1 vector source

## Constitution Check

- [x] IRIS container `iris_vector_graph` (from `docker-compose.yml`) — managed by `IRISContainer.attach("iris_vector_graph")`
- [x] E2E test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test file
- [x] No hardcoded IRIS ports — all via `IRISContainer.attach(...).get_exposed_port(1972)`

## Project Structure

### Source files touched / created

```text
iris_src/src/Graph/KG/
└── IVFIndex.cls            # NEW — Build, Search, Drop, Info, SearchProc

iris_vector_graph/
├── engine.py               # ADD ivf_build, ivf_search, ivf_drop, ivf_info
└── cypher/
    └── translator.py       # ADD _translate_ivf_search + wire into procedure dispatch

tests/unit/
└── test_ivf_index.py       # NEW — unit + E2E tests
```

### Global schema

```
^IVF(name, "cfg", "nlist"|"dim"|"metric"|"indexed") = value
^IVF(name, "centroid", k)         $vector — centroid k (768-dim float32)
^IVF(name, "list", k, node_id)    $vector — stored vector for node_id in cell k
```

## Implementation Strategy

**Phase 1 (US1 — Build)**: ObjectScript scaffold + Python `ivf_build()` wrapper.  
**Phase 2 (US2 — Search)**: ObjectScript `Search` ClassMethod + Python `ivf_search()` wrapper.  
**Phase 3 (US3 — Lifecycle)**: `Drop` + `Info` + `SearchProc` SQL stored proc.  
**Phase 4 (US4 — Cypher)**: Translator CTE for `CALL ivg.ivf.search(...)`.  
**Phase 5 (Polish)**: Recall benchmark, version bump, README update.

## Reuse Map

| Component | Reused from | Location |
|-----------|-------------|----------|
| k-means build | `plaid_build()` MiniBatchKMeans loop | `engine.py:2575` |
| Centroid scoring | `PLAIDSearch.Search` Stage 1 | `PLAIDSearch.cls` |
| `$vectorop` distance | `VecIndex.Distance` / `VecIndex.Cosine` | `VecIndex.cls` |
| Stage CTE | `_translate_bm25_search` pattern | `translator.py:458` |
| Engine wrapper pattern | `bm25_build/search/drop/info` | `engine.py:2874` |
| E2E test pattern | `TestBM25IndexE2E` | `tests/unit/test_bm25_index.py:270` |

## Complexity Tracking

No constitution violations. Feature is a direct extension of existing patterns.
