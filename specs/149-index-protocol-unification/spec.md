# Feature Specification: Index Protocol Unification

**Feature Branch**: `149-index-protocol-unification`
**Created**: 2026-05-06
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Use any index type through a single consistent API (Priority: P1)

A developer using IVG builds a feature that needs vector search. Today they must know whether
to call `vec_search`, `ivf_search`, `bm25_search`, or `plaid_search` and learn four different
calling conventions. With this feature, they call `engine.index("myidx").search(query, k=10)`
regardless of what index type was built.

**Why this priority**: Core DX improvement. All other work enables it.

**Independent Test**: Build any index type, retrieve it via `engine.index(name)`, call
`.search()`, `.insert()`, `.info()`, `.drop()` — all succeed without knowing the type.

**Acceptance Scenarios**:

1. **Given** an IVF index named `"emb"`, **When** `engine.index("emb").search(vec, k=5)` is called, **Then** it returns the same results as `engine.ivf_search("emb", vec, k=5)`.
2. **Given** a BM25 index named `"text"`, **When** `engine.index("text").search("query", k=5)` is called, **Then** it returns the same results as `engine.bm25_search("text", "query", k=5)`.
3. **Given** an index name that does not exist, **When** `engine.index("missing")` is called, **Then** a `ValueError` is raised with a clear message.

---

### User Story 2 — PLAID build/search/insert/drop matches the established naming pattern (Priority: P1)

A developer sees `bm25_build`, `bm25_search`, `bm25_drop` and tries `plaid_build`,
`plaid_search`, `plaid_drop`. Today `plaid_build` internally calls `StoreCentroids` and
`BuildInvertedIndex` which appear in error messages and logs. With this change, PLAID's
ObjectScript method names align: `Build`, `Search`, `Drop`, `Insert`, `Info`.

**Why this priority**: PLAID has zero test coverage. Fixing naming is prerequisite to reliable tests.

**Independent Test**: Full plaid lifecycle — build, search, insert, info, drop — all succeed,
ObjectScript errors reference `Build`/`Search` not `StoreCentroids`/`BuildInvertedIndex`.

**Acceptance Scenarios**:

1. **Given** documents with token embeddings, **When** `plaid_build(name, docs)` is called, **Then** `plaid_info(name)["indexed"] == len(docs)`.
2. **Given** a built PLAID index, **When** `plaid_search(name, query_tokens, k=5)` is called, **Then** up to 5 `(doc_id, score)` tuples are returned.
3. **Given** a built PLAID index, **When** `plaid_insert(name, doc_id, tokens)` is called, **Then** count increments and doc appears in search.
4. **Given** a built PLAID index, **When** `plaid_drop(name)` is called, **Then** all data is removed.

---

### User Story 3 — Index type is discoverable from a built index (Priority: P2)

`engine.index("myidx").info()` or `engine.ivf_info("myidx")` returns a dict including
`"type": "ivf"` so callers can identify the index without reading build code.

**Why this priority**: Required for `engine.index()` to dispatch correctly.

**Acceptance Scenarios**:

1. **Given** any built index, **When** `info()` is called, **Then** returned dict contains `"type"` matching `"ivf"`, `"bm25"`, `"vec"`, or `"plaid"`.
2. **Given** `engine.index(name)` with an unregistered name, **Then** a clear `ValueError` is raised.

---

### User Story 4 — PLAID has full e2e test coverage (Priority: P2)

`plaid_build`, `plaid_search`, `plaid_insert`, `plaid_info`, `plaid_drop` are covered by
passing e2e tests in `tests/e2e/test_plaid.py`.

**Acceptance Scenarios**:

1. **Given** synthetic token embeddings, **When** full PLAID lifecycle runs, **Then** all 5 methods pass.
2. **Given** a search query, **Then** results are ranked by MaxSim score.

---

### Edge Cases

- `plaid_build` called with zero documents.
- `plaid_search` query token count exceeds centroid dimension.
- `engine.index(name)` called before any index with that name is built.
- `plaid_insert` called with wrong-dimension token embeddings.
- Two different index types built with the same name.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `engine.index(name)` MUST return a handle supporting `.search()`, `.insert()`, `.info()`, `.drop()` for any registered index type. The registry MUST be auto-populated from existing globals (`^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID`) on `IRISGraphEngine.__init__`.
- **FR-002**: `engine.index(name).search(query, k)` MUST dispatch to the correct underlying method based on registered type.
- **FR-003**: `engine.index(name).info()` MUST return a dict including a `"type"` key (`"ivf"`, `"bm25"`, `"vec"`, `"plaid"`).
- **FR-004**: All existing `vec_*`, `ivf_*`, `bm25_*`, `plaid_*` prefixed methods MUST remain unchanged — no breaking changes.
- **FR-005**: `PLAIDSearch.cls` public ClassMethods MUST be renamed: `Build`, `Search`, `Insert`, `Drop`, `Info`. `StoreCentroids`, `BuildInvertedIndex`, `StoreDocTokens`, `StoreDocTokensBatch` become Private ClassMethods called internally by `Build`.
- **FR-006**: `plaid_build` Python wrapper MUST call `PLAIDSearch.Build`. Existing behavior preserved.
- **FR-007**: `plaid_info` MUST return `{"type": "plaid", "indexed": N, "dim": D, "nlist": L}`.
- **FR-008**: A `typing.Protocol` named `IVGIndex` MUST be defined in `iris_vector_graph/index_protocol.py` with `search`, `insert`, `drop`, `info` methods. `runtime_checkable` decorator required. `IndexHandle` MUST be a Pydantic `BaseModel` with `name: str` (non-empty), `type: Literal["ivf","bm25","vec","plaid"]`, consistent with `SQLQuery`/`QueryMetadata` pattern.
- **FR-009**: `tests/e2e/test_plaid.py` MUST cover build, search, insert, drop with passing assertions.
- **FR-010**: `engine.index(name)` MUST raise `ValueError` with descriptive message for names not found in any global.

### Key Entities

- **IVGIndex (Protocol)**: Structural type contract — `search(query, k) → list`, `insert(id, vec) → None`, `drop() → None`, `info() → dict`. Not a base class.
- **IndexHandle**: Returned by `engine.index(name)`. Pydantic `BaseModel` with validated `name: str` (non-empty), `type: Literal["ivf","bm25","vec","plaid"]`, `engine: Any`. Dispatches to correct `*_search`, `*_insert`, etc. Consistent with `SQLQuery`/`QueryMetadata` Pydantic pattern in `translator.py`.
- **IndexRegistry**: In-process dict `{name → type_string}` populated by `*_build`/`*_create_index` calls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `engine.index(name).search(query, k)` returns identical results to direct method call for all 4 index types.
- **SC-002**: All existing unit and e2e tests pass — zero regressions.
- **SC-003**: `pytest tests/e2e/test_plaid.py` passes with 4+ tests, 0 failures, 0 skips.
- **SC-004**: `StoreCentroids` and `BuildInvertedIndex` no longer appear in public error messages.
- **SC-005**: `plaid_info(name)` returns dict with `"type"`, `"indexed"`, `"dim"`, `"nlist"` keys.
- **SC-006**: `IVGIndex` protocol is importable from `iris_vector_graph` and `runtime_checkable`.

## Assumptions

- `engine.index(name)` registry is pre-populated on `IRISGraphEngine.__init__` by reading existing globals (`^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID`). `engine.index(name)` works immediately after reconnecting without requiring `*_build` to be called again.
- PLAID's new `Build` ClassMethod calls `StoreCentroids` + `BuildInvertedIndex` internally as private helpers — minimal ObjectScript change, lower regression risk.
- `kg_KNN_VEC` (native HNSW) integration with `engine.index()` is out of scope for this spec.
- `vec_expand` remains outside the `IVGIndex` protocol (VecIndex-specific, no equivalent in other types).

## Clarifications

### Session 2026-05-06

- Q: Should `IndexRegistry` auto-populate from globals on engine init, or be strictly in-process? → A: Auto-populate from globals on `IRISGraphEngine.__init__` (Option A).
- Q: Should PLAIDSearch `Build` call existing `StoreCentroids`+`BuildInvertedIndex` as private helpers, or merge into one method? → A: New `Build` calls existing helpers internally (Option A).
