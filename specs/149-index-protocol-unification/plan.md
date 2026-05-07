# Implementation Plan: Index Protocol Unification

**Branch**: `149-index-protocol-unification` | **Date**: 2026-05-06 | **Spec**: [spec.md](./spec.md)

## Summary

Introduce a unified `engine.index(name)` entry point that dispatches to any registered index
type (IVF, BM25, VecIndex, PLAID) without callers needing to know the prefix. Add a
`typing.Protocol` (`IVGIndex`) as the type contract. Rename `PLAIDSearch.cls` public methods
to `Build`/`Search`/`Drop`/`Insert`/`Info` matching every other index class, and add full
PLAID e2e test coverage. Zero breaking changes to existing `vec_*`, `ivf_*`, `bm25_*`,
`plaid_*` prefixed methods.

## Technical Context

**Language/Version**: Python 3.11, ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (tests only)
**Storage**: `^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID` globals — read on engine init for registry
**Testing**: `pytest`, `iris-devtester`, IRIS container `iris_vector_graph` port `1972`
**Target Platform**: InterSystems IRIS 2025.1+, Python 3.11
**Project Type**: Single library project
**Performance Goals**: `engine.index(name)` dispatch overhead < 1ms (in-process dict lookup)
**Constraints**: No breaking changes to existing public API surface
**Scale/Scope**: 4 index types, ~30 affected engine methods, 1 new ObjectScript class method group

## Constitution Check

**Principle II (Compatibility-First)**: ✅ All existing `vec_*`, `ivf_*`, `bm25_*`, `plaid_*`
methods remain unchanged. `engine.index()` is additive.

**Principle III (Test-First)**: ✅ e2e tests for PLAID + `engine.index()` written before
implementation in each task group.

**Principle IV (IRIS e2e testing)**:
- [x] Container: `iris_vector_graph` (verified from `docker-compose.yml`)
- [x] Port: `1972` (verified from `docker-compose.yml`)
- [x] e2e test phase is non-optional (Task Group C + D)
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test files
- [x] No hardcoded ports — all via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`

**Principle V (Simplicity)**: `IndexHandle` is a lightweight dataclass wrapping name + type +
engine ref. `IVGIndex` is a `Protocol` (structural subtyping) — no inheritance required.

**Principle VI (Grounding Rule)**:
- Container name: `iris_vector_graph` ← verified from `docker-compose.yml:4`
- Port: `1972` ← verified from `docker-compose.yml:5`
- Schema prefix: `Graph_KG` ← verified from `engine.py` `set_schema_prefix` call
- PLAID class name: `Graph.KG.PLAIDSearch` ← verified from `engine.py:5824`

## Project Structure

### Documentation (this feature)

```text
specs/149-index-protocol-unification/
├── plan.md              ← this file
├── research.md          ← Phase 0
├── data-model.md        ← Phase 1
└── tasks.md             ← /speckit.tasks output
```

### Source Code

```text
iris_vector_graph/
├── index_protocol.py           # NEW — IVGIndex Protocol + IndexHandle
├── engine.py                   # MODIFIED — _build_index_registry(), index()
iris_src/src/Graph/KG/
└── PLAIDSearch.cls             # MODIFIED — add Build(), rename helpers to Private

tests/e2e/
├── test_plaid.py               # NEW — full PLAID lifecycle
└── test_index_protocol.py      # NEW — engine.index() dispatch + registry
```

## Phase 0: Research

### Decision Log

**D-001: IVGIndex as typing.Protocol (not ABC)**
- Decision: `typing.Protocol` with `@runtime_checkable`
- Rationale: Structural subtyping — existing index engine methods already satisfy the
  contract without any class changes. No inheritance required. Simpler than ABC.
- Alternatives rejected: `abc.ABC` would require all 4 index types to explicitly inherit.

**D-002: IndexRegistry auto-populated from globals on __init__**
- Decision: On `IRISGraphEngine.__init__`, probe `^IVF`, `^VecIdx`, `^BM25Idx`, `^PLAID`
  via ObjectScript to find registered index names, build `{name: type}` dict.
- Rationale: `engine.index(name)` works immediately after reconnecting without requiring
  the caller to re-run `*_build`. Consistent with IRIS-as-source-of-truth principle.
- Implementation: `_build_index_registry()` called at end of `__init__`. Each global
  probed via `$Order(^IVF(""))`, `$Order(^VecIdx(""))`, etc. O(N) where N = index count.
- Alternatives rejected: Strict in-process only would break reconnect scenarios.

**D-003: PLAIDSearch.Build wraps StoreCentroids + BuildInvertedIndex**
- Decision: New `Build` ClassMethod calls existing private helpers in sequence.
- Rationale: Minimal ObjectScript diff, zero regression risk. Internal structure preserved.
- Implementation: `StoreCentroids`, `StoreDocTokens`, `StoreDocTokensBatch`,
  `BuildInvertedIndex` moved to `[ Private ]` ClassMethods. `Build` orchestrates them.

**D-004: IndexHandle dispatch table**
- Decision: `IndexHandle` holds `engine` ref + `name` + `type_str`. Dispatch via
  `_DISPATCH = {"ivf": (engine.ivf_search, engine.ivf_insert, ...), ...}`.
- Rationale: Simple dict lookup, no dynamic attribute resolution, easy to extend.

## Phase 1: Design & Contracts

### Data Model (`data-model.md`)

**IVGIndex Protocol**
```python
@runtime_checkable
class IVGIndex(Protocol):
    def search(self, query: Any, k: int = 10, **kwargs) -> list: ...
    def insert(self, id: str, vector: Any) -> None: ...
    def drop(self) -> None: ...
    def info(self) -> dict: ...
```

**IndexHandle**
```python
@dataclass
class IndexHandle:
    name: str
    type: str        # "ivf" | "bm25" | "vec" | "plaid"
    _engine: Any     # IRISGraphEngine ref

    def search(self, query, k=10, **kwargs) -> list: ...
    def insert(self, id: str, vector) -> None: ...
    def drop(self) -> None: ...
    def info(self) -> dict: ...
```

**IndexRegistry** (in `IRISGraphEngine`)
```python
_index_registry: Dict[str, str]  # {name: type_str}
# Populated by _build_index_registry() on __init__
# Updated by ivf_build(), bm25_build(), vec_create_index(), plaid_build()
```

**PLAIDSearch.cls method visibility changes**

| Before | After | Visibility |
|---|---|---|
| `StoreCentroids` | `StoreCentroids` | Private |
| `StoreDocTokens` | `StoreDocTokens` | Private |
| `StoreDocTokensBatch` | `StoreDocTokensBatch` | Private |
| `BuildInvertedIndex` | `BuildInvertedIndex` | Private |
| `Search` | `Search` | Public (unchanged) |
| `Insert` | `Insert` | Public (unchanged) |
| `Info` | `Info` | Public (unchanged) |
| `Drop` | `Drop` | Public (unchanged) |
| *(new)* | `Build` | Public |

### API Contracts (`contracts/`)

**`engine.index(name: str) → IndexHandle`**
- Raises `ValueError` if name not in `_index_registry`
- Registry probed from: `^IVF`, `^VecIdx(name, "cfg")`, `^BM25Idx(name, "cfg")`, `^PLAID(name, "cfg")`

**`IndexHandle.search(query, k=10, **kwargs)`**
- Dispatches to: `ivf_search` | `vec_search` | `bm25_search` | `plaid_search`
- `query` type depends on index: `list[float]` for vector indexes, `str` for BM25, `list[list[float]]` for PLAID

**`IndexHandle.info() → dict`**
- Always includes `"type"` key
- Delegates to underlying `*_info` method

**`IndexHandle.insert(id, vector)`**
- Dispatches to `ivf_insert` | `vec_insert` | `bm25_insert` | `plaid_insert`

**`IndexHandle.drop()`**
- Dispatches to `ivf_drop` | `vec_drop` | `bm25_drop` | `plaid_drop`

**`PLAIDSearch.Build(name, docs_json, n_clusters, dim) → info_json`**
- Calls: `StoreCentroids`, `StoreDocTokensBatch`, `BuildInvertedIndex` in sequence
- Returns same JSON as current `Info` method

**`_build_index_registry() → Dict[str, str]`**
ObjectScript probe logic:
```
$Order(^IVF(""))       → type "ivf"
$Order(^VecIdx(""))    → type "vec"
$Order(^BM25Idx(""))   → type "bm25"
$Order(^PLAID(""))     → type "plaid"
```

## Implementation Task Groups

### A. IVGIndex Protocol + IndexHandle (no IRIS dependency)

1. Create `iris_vector_graph/index_protocol.py` with `IVGIndex` Protocol and `IndexHandle` dataclass
2. Export `IVGIndex` and `IndexHandle` from `iris_vector_graph/__init__.py`
3. Write unit tests for `IndexHandle` dispatch (mock engine)

### B. PLAIDSearch.cls refactor

1. Add `Build` ClassMethod to `PLAIDSearch.cls` that calls existing helpers
2. Mark `StoreCentroids`, `StoreDocTokens`, `StoreDocTokensBatch`, `BuildInvertedIndex` as `[ Private ]`
3. Update `plaid_build` Python wrapper to call `PLAIDSearch.Build`
4. Update `plaid_info` to include `"type": "plaid"` in returned dict
5. Compile on all containers; verify no regressions

### C. PLAID e2e tests

1. Write `tests/e2e/test_plaid.py` covering build/search/insert/info/drop
2. Run and verify all pass (test-first: write before implementation in Group B)

### D. IndexRegistry + `engine.index()` wiring

1. Add `_build_index_registry()` to `IRISGraphEngine.__init__` — probes all 4 globals
2. Update `ivf_build`, `bm25_build`, `vec_create_index`, `plaid_build` to register name in `_index_registry`
3. Add `engine.index(name)` method returning `IndexHandle`
4. Write e2e tests in `tests/e2e/test_index_protocol.py`

### E. `*_info` methods add `"type"` key

1. Update `ivf_info`, `bm25_info`, `vec_info`, `plaid_info` to include `"type"` in returned dict
2. Verify existing tests still pass (additive change, no breakage expected)

### Execution Order

A → C → B → D → E (test-first: C before B, D tests before D impl)
