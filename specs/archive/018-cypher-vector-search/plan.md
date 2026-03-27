# Implementation Plan: Cypher Vector Search

**Branch**: `018-cypher-vector-search` | **Date**: 2026-02-21 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/018-cypher-vector-search/spec.md`

---

## Summary

Implement the `CALL ivg.vector.search(label, property, query_input, limit [, options]) YIELD node, score` Cypher procedure, enabling developers to run vector similarity searches directly within Cypher queries — with IRIS HNSW-accelerated SQL — and compose the results with graph traversal (`MATCH`) in a single query.

The implementation extends the existing recursive-descent parser and CTE-based SQL translator with no new external dependencies. Two input modes: pre-computed `list[float]` (always available) and raw text string via IRIS native `EMBEDDING()` function (IRIS 2024.3+, detected lazily).

---

## Technical Context

**Language/Version**: Python 3.11 (same as existing library)  
**Primary Dependencies**: `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (dev/test only) — no new runtime dependencies  
**Storage**: InterSystems IRIS — `Graph_KG.kg_NodeEmbeddings`, `Graph_KG.nodes`, `Graph_KG.rdf_labels`, `Graph_KG.rdf_edges` (no schema changes)  
**Testing**: `pytest` — unit (no IRIS), integration (IRIS SQL layer), e2e (full round-trip via `IRISContainer.attach("iris_vector_graph")`)  
**Target Platform**: Any platform running IRIS 2024.1+ (Mode 1); IRIS 2024.3+ required for Mode 2  
**Performance Goals**: Top-10 cosine search < 100ms for datasets up to 1M nodes on recommended IRIS infrastructure (SC-001)  
**Constraints**: No new PyPI dependencies added to `[project.dependencies]`; `iris-vector-rag` MUST NOT become a dependency  
**Scale/Scope**: Single library package; 5 files modified + 3 new test files

---

## Constitution Check

*GATE: Must pass before implementation begins. Re-checked post-design.*

**Principle I (Library-First)**: ✅ All changes are within `iris_vector_graph/` package. No application-level workarounds.

**Principle II (Compatibility-First)**: ✅ `execute_cypher()` signature unchanged. New Cypher syntax is additive. Existing queries unaffected.

**Principle III (Test-First)**: ✅ Unit tests written and verified failing before implementation; integration and e2e tests written before implementation of e2e path.

**Principle IV (IRIS Integration & E2E — non-negotiable)**:
- [x] Dedicated named IRIS container `iris_vector_graph` managed by `iris-devtester`
- [x] Explicit e2e test phase (Phase 4 below — non-optional)
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in all new test files
- [x] No hardcoded IRIS ports — all resolved via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`

**Principle V (Simplicity)**: ✅ CTE-based translation reuses existing `context.stages` machinery. No new abstraction layers. Procedure dispatch is a single `if procedure_name == "ivg.vector.search"` check.

**Additional Constraint — no `iris-vector-rag` dependency**: ✅ Mode 2 uses IRIS native `EMBEDDING()` SQL function; `iris-vector-rag` is not referenced.

---

## Project Structure

### Documentation (this feature)

```text
specs/018-cypher-vector-search/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── procedure-call-contract.md  ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks — not yet created)
```

### Source Code

```text
iris_vector_graph/
├── cypher/
│   ├── lexer.py          # +2 token types: CALL, YIELD
│   ├── parser.py         # +parse_procedure_call(), CALL branch in parse()
│   ├── ast.py            # +options field on CypherProcedureCall
│   └── translator.py     # +translate_procedure_call(), VecSearch CTE logic
└── engine.py             # +_probe_embedding_support(), CALL dispatch in execute_cypher()

tests/
├── unit/
│   └── test_cypher_vector_search.py    # NEW — 10 unit tests, no IRIS
├── integration/
│   └── test_cypher_vector_search.py    # NEW — 6 integration tests, live IRIS SQL
└── e2e/
    └── test_cypher_vector_search.py    # NEW — 7 e2e tests, IRISContainer.attach("iris_vector_graph")
```

**Structure Decision**: Single project layout (existing). No new packages, modules, or directories beyond the 3 new test files and 5 modified source files.

---

## Phase 0: Research (Complete)

**Output**: `research.md`

All NEEDS CLARIFICATION items resolved:

| Item | Resolution |
|------|-----------|
| Lexer: CALL/YIELD tokens | Add two enum members; existing dispatch handles automatically |
| Parser: dotted name | Reconstruct from `IDENTIFIER DOT IDENTIFIER` tokens in parser |
| Parser: CALL branch location | Before first `parse_query_part()` in `parse()` |
| YIELD scope injection | Translator pre-populates `variable_aliases` from `yield_items` |
| CTE mechanism | Prepend `VecSearch AS (...)` to `context.stages` |
| Mode 1 SQL | `VECTOR_COSINE(e.emb, TO_VECTOR(?))` with `json.dumps(vector)` |
| Mode 2 SQL | `VECTOR_COSINE(e.emb, EMBEDDING(?, ?))` with `(text, config_name)` |
| EMBEDDING detection | Lazy SQL probe, cached on engine instance (mirrors `_ppr_sql_function_available`) |
| HNSW auto-use | Yes — `TOP N + ORDER BY VECTOR_COSINE(...) DESC` triggers it; no hint needed |
| Node hydration | Two-query via existing `get_nodes()` after SQL returns `(node_id, score)` |
| Security | `kg_NodeEmbeddings` in `VALID_GRAPH_TABLES`; `label` validated via `validate_table_name` |
| e2e container | `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)` |

---

## Phase 1: Design (Complete)

**Outputs**: `data-model.md`, `contracts/procedure-call-contract.md`, `quickstart.md`

### Key Design Decisions

**1. CTE name `VecSearch` (not `Stage{n}`)**
Using a named CTE avoids colliding with the existing numeric stage numbering (`Stage1`, `Stage2`, ...). The translator inserts `VecSearch` before any `Stage{n}` CTEs.

**2. Two-query node hydration**
The vector search CTE returns `(node_id AS node, score)`. The Python `execute_cypher()` post-processor calls `engine.get_nodes(ids)` on the returned node IDs to produce full `{"id", "labels", "properties"}` dicts. No change to the SQL CTE is needed — this is consistent with how `get_nodes()` already works.

**3. `options` map parsed as a Cypher map literal**
The 5th argument to `CALL ivg.vector.search(...)` is an optional Cypher map literal `{key: value}`. The parser already handles map literals (`parse_map_literal()` in `parser.py`). The `CypherProcedureCall.options` dict is populated from this parsed map.

**4. `UnsupportedOperationError` is a new exception class**
Added to `iris_vector_graph/exceptions.py` (or top-level `__init__.py`). Raised only for Mode 2 on IRIS < 2024.3.

**5. HNSW index not created by `initialize_schema()`**
This is a pre-existing gap. The plan includes a task to document and optionally add an `ensure_hnsw_index()` helper to `GraphSchema`. The e2e test fixture creates the index before tests run.

---

## Constitution Check (Post-Design)

All principles hold. No complexity violations. No abstraction layers added beyond what is strictly needed. No new external dependencies. E2e phase is mandatory and scheduled as Phase 4 (non-optional).

---

## Complexity Tracking

> No constitution violations requiring justification.

---

## Implementation Phases

### Phase 1: Lexer + AST (Blocking prerequisite — no parallelism)

**Files**: `iris_vector_graph/cypher/lexer.py`, `iris_vector_graph/cypher/ast.py`

- Add `CALL = "CALL"` and `YIELD = "YIELD"` to `TokenType` enum in `lexer.py`
- Add `options: Dict[str, Any]` field to `CypherProcedureCall` dataclass in `ast.py`

### Phase 2: Parser (depends on Phase 1)

**File**: `iris_vector_graph/cypher/parser.py`

- Implement `parse_procedure_call()` — dotted name, arg list, `YIELD` items, optional options map
- Insert CALL branch in `parse()` before first `parse_query_part()`

### Phase 3: Translator (depends on Phase 1)

**File**: `iris_vector_graph/cypher/translator.py`

- Implement `translate_procedure_call()` — emits `VecSearch` CTE, pre-populates `variable_aliases`
- Handle Mode 1 (`TO_VECTOR`) and Mode 2 (`EMBEDDING`) SQL emission
- Handle `similarity` option: `VECTOR_COSINE` vs `VECTOR_DOT_PRODUCT`
- Validation: label via `validate_table_name`, `similarity` value, `limit > 0`, `embedding_config` required for Mode 2

### Phase 3b: Engine (depends on Phase 3, parallel with Parser)

**File**: `iris_vector_graph/engine.py`

- Add `_embedding_function_available: Optional[bool] = None` instance attribute
- Implement `_probe_embedding_support()` — SQL probe, cached per instance
- Wire `procedure_call` dispatch in `execute_cypher()`: detect `CypherProcedureCall`, run vector search SQL, hydrate results via `get_nodes()`
- Add `UnsupportedOperationError` to exceptions

### Phase 4: Unit Tests (test-first; written before Phase 2/3 implementation)

**File**: `tests/unit/test_cypher_vector_search.py`

- 10 unit tests per contract — parser output shape, translator SQL correctness, error cases
- All pass without IRIS

### Phase 5: Integration Tests (after Phase 3b; requires live IRIS)

**File**: `tests/integration/test_cypher_vector_search.py`

- 6 integration tests — SQL executes, results ordered, label filter works, dot product works
- Uses existing integration conftest (note: conftest Principle IV violations are pre-existing; fix is a separate task)

### Phase 6: E2E Tests (mandatory, non-optional — Constitution Principle IV)

**File**: `tests/e2e/test_cypher_vector_search.py`

- 7 e2e tests — full `execute_cypher()` round-trip including node hydration, composability, Mode 2, error handling, benchmark
- Uses `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`
- `SKIP_IRIS_TESTS` defaults to `"false"`
- Test fixture creates HNSW index and seeds test data before tests run

### Phase 7: Documentation + HNSW helper

- `quickstart.md` already written (Phase 1)
- Optional: `GraphSchema.ensure_hnsw_index()` helper method to `schema.py`
- Update `AGENTS.md` auto-generated context via `update-agent-context.sh`

---

## Dependency Graph

```
Phase 1 (Lexer+AST)
    ↓
Phase 2 (Parser) ←──────────────── Phase 4 (Unit Tests, written first, fail first)
    ↓
Phase 3 (Translator) + Phase 3b (Engine) ← both depend on Phase 1
    ↓
Phase 5 (Integration Tests)
    ↓
Phase 6 (E2E Tests)  ← MANDATORY, BLOCKS RELEASE
    ↓
Phase 7 (Docs + helper)
```

Phases 2 and 3 can proceed in parallel after Phase 1. Phase 4 tests are written before Phase 2/3 implementation (test-first).

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `context.stages` CTE collision (`VecSearch` vs `Stage{n}`) | Low | Medium | Named `VecSearch` CTE avoids numeric collision; explicit integration test covers composed queries |
| HNSW index not present in test IRIS instance | Medium | High | E2e fixture creates index before tests; `ensure_hnsw_index()` helper added |
| IRIS `EMBEDDING()` probe gives false positive | Low | Medium | Probe distinguishes "function not found" from "config not found" errors explicitly |
| `translate_expression()` fails to resolve `VecSearch.node` in subsequent MATCH | Medium | High | Integration test covers composed `CALL ... MATCH` query end-to-end |
| Existing `tests/conftest.py` uses `MockContainer` with hardcoded port 1972 | Exists | Medium | New e2e tests bypass existing conftest entirely; pre-existing violation flagged for separate fix |
