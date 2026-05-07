# Implementation Plan: IVGResult for execute_cypher

**Branch**: `151-ivgresult-execute-cypher` | **Date**: 2026-05-06 | **Spec**: [spec.md](./spec.md)

## Summary

Introduce `IVGResult` — a Pydantic `BaseModel` that replaces `Dict[str, Any]` as the
return type of `execute_cypher`. Backward-compatible via `__getitem__`, `__contains__`,
and `.get()` overrides. Consistent with `SQLQuery`, `QueryMetadata`, `IndexHandle` pattern
already in the codebase. 189 existing call sites require zero changes.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `pydantic>=2.11.9` (already declared in pyproject.toml)
**Testing**: `pytest`, IRIS container `iris_vector_graph` port `1972`
**Target Platform**: Python 3.11+, all callers of `execute_cypher`
**Performance Goals**: Zero overhead — `IVGResult` construction is O(1) field assignment
**Constraints**: Fully backward-compatible; no breaking changes; no ObjectScript changes
**Scale/Scope**: 1 new class, ~200 `return {...}` sites in engine.py updated, 0 test changes

## Constitution Check

**Principle II (Compatibility-First)**: ✅ All 189 test call sites work unchanged via
`__getitem__`, `__contains__`, `.get()` overrides. `"error" in result` returns `False`
on success (no error key) matching old dict behavior.

**Principle III (Test-First)**: ✅ Unit tests for `IVGResult` written before implementation.
Integration tests verify all 189 call sites still pass.

**Principle IV (IRIS e2e)**:
- [x] Container: `iris_vector_graph` (verified from `docker-compose.yml:4`)
- [x] Port: `1972` (verified from `docker-compose.yml:5`)
- [x] e2e regression test phase non-optional

**Principle V (Simplicity)**: `IVGResult` is a thin Pydantic model + 4 method overrides.
No new abstractions, no factory patterns, no metaclasses.

**Principle VI (Grounding Rule)**:
- Container: `iris_vector_graph` ← `docker-compose.yml:4`
- Pydantic: `pydantic>=2.11.9` ← `pyproject.toml:57`
- Pattern: follows `SQLQuery`/`QueryMetadata`/`IndexHandle` pattern ← `translator.py:109`, `index_protocol.py:49`

## Root Cause / Motivation

`execute_cypher` currently returns `Dict[str, Any]`. Key problems:
1. No IDE autocomplete — callers type `result[""]` and get no suggestions
2. No static type checking — `result["typo"]` silently returns `KeyError` at runtime
3. 6 distinct return shapes — callers don't know which keys are always present
4. `error` key absent on success, present on failure — asymmetric structure

## Project Structure

```text
iris_vector_graph/
├── result.py           # NEW — IVGResult model
├── engine.py           # MODIFIED — ~200 return sites updated; return type annotation
├── __init__.py         # MODIFIED — export IVGResult

tests/unit/
└── test_ivgresult.py   # NEW — IVGResult unit tests (dict compat, bool, fields)
```

## Phase 0: Research

### Decision Log

**D-001: Pydantic BaseModel not TypedDict**
- Decision: Pydantic `BaseModel` with `__getitem__`/`get`/`__contains__` overrides
- Rationale: Consistent with `SQLQuery`, `QueryMetadata`, `IndexHandle` — same pattern,
  same validation, same serialization. TypedDict has no runtime validation or `.get()`.
- Alternative rejected: `TypedDict` — no runtime construction, no field defaults.

**D-002: `"error" in result` returns `False` on success**
- Decision: `__contains__` returns `False` for `"error"` when `self.error is None`
- Rationale: Matches old dict behavior where error key was absent on success paths.
  13 existing sites use `result.get("error")` — all safe. 0 use `result["error"]`.

**D-003: `bool(result)` = `not result.error`**
- Decision: `__bool__` returns `True` when `error is None`, `False` when error is set
- Rationale: Enables `if result:` pattern for error checking. Empty `rows` is a valid
  success (e.g. `CREATE` or `DELETE` with no RETURN clause).

**D-004: `sql` and `params` are `Optional`**
- Decision: Some paths (system procedures, SHOW commands) don't generate SQL; `sql=None`
- Rationale: 32 existing return paths set only `columns`/`rows`. Making `sql` required
  would force fake SQL strings onto these paths.

**D-005: `_bolt_column_types` stays out of IVGResult**
- Decision: `bolt_server.py` adds this key after receiving the result; not in the model
- Rationale: It's a transport-layer concern, not a query result concern. Bolt server
  will convert `IVGResult` to dict before adding the key.

## Phase 1: Design

### IVGResult Model

```python
from pydantic import BaseModel, Field
from typing import Optional, Any
from iris_vector_graph.cypher.translator import QueryMetadata

class IVGResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list = Field(default_factory=list)
    error: Optional[str] = None
    metadata: QueryMetadata = Field(default_factory=QueryMetadata)
    sql: Optional[str] = None
    params: Optional[list] = None

    model_config = {"arbitrary_types_allowed": True}

    def __bool__(self) -> bool:
        return self.error is None

    def __getitem__(self, key: str) -> Any:
        _map = {"columns": self.columns, "rows": self.rows, "sql": self.sql,
                "params": self.params, "metadata": self.metadata, "error": self.error}
        if key not in _map or (key == "error" and self.error is None):
            raise KeyError(key)
        return _map[key]

    def __contains__(self, key: object) -> bool:
        if key == "error":
            return self.error is not None
        return key in {"columns", "rows", "sql", "params", "metadata"}

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
```

### bolt_server.py adaptation (minimal)

`bolt_server.py` calls `result["columns"]` and `result["rows"]` — both work unchanged.
It also sets `result["_bolt_column_types"] = ...` — this will fail on `IVGResult`.
Fix: `bolt_server.py` converts to dict first: `d = dict(result.model_dump()); d["_bolt_column_types"] = ...`

### engine.py return site migration

Every `return {"columns": ..., "rows": ..., ...}` in `execute_cypher` and `_execute_parsed`
replaced with `return IVGResult(columns=..., rows=..., ...)`.

~200 sites — many are short `return {"columns": [], "rows": []}` patterns.

## Implementation Task Groups

### A. IVGResult class + unit tests (test-first)
1. Write failing unit tests for IVGResult
2. Create `iris_vector_graph/result.py` with `IVGResult`
3. Export from `__init__.py`

### B. engine.py migration
1. Update `execute_cypher` return type annotation
2. Update all `return {...}` in `execute_cypher` and `_execute_parsed`
3. Update `_execute_var_length_cypher` and other BFS path returns

### C. bolt_server.py adaptation
1. Convert IVGResult to dict before adding `_bolt_column_types`

### D. Regression validation
1. Run all unit tests — zero regressions
2. Run e2e tests — zero regressions
3. Verify isinstance(result, IVGResult) on live query

### Execution Order

A (test-first) → B → C → D
