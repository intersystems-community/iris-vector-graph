# Feature Specification: IVGResult — Typed Return Model for execute_cypher

**Feature Branch**: `151-ivgresult-execute-cypher`
**Created**: 2026-05-06
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 — IDE autocomplete and static analysis on query results (Priority: P1)

A developer calls `engine.execute_cypher(...)` and types `result.` — today nothing useful
appears because the return type is `Dict[str, Any]`. With this change, the IDE shows
`columns`, `rows`, `error`, `metadata`, `sql` with their types and docstrings. mypy
and pyright catch `result["typo"]` before runtime.

**Why this priority**: This is the highest-value increment of the Pydantic roadmap.
`execute_cypher` is the most-called method in the entire codebase — 189 test call sites,
3 production API callers. Typed contracts here benefit every IVG user immediately.

**Independent Test**: `mypy iris_vector_graph/engine.py` passes without `Dict[str, Any]`
suppression. Callers can access `result.columns`, `result.rows`, `result.error` with
full type checking.

**Acceptance Scenarios**:

1. **Given** a successful query, **When** `result = engine.execute_cypher(q, p)` is called, **Then** `result.columns` is `list[str]`, `result.rows` is `list[tuple]`, and `result.error` is `None`.
2. **Given** a failed query (bad SQL), **When** the engine catches the error, **Then** `result.error` is a non-empty string, `result.columns` is `[]`, `result.rows` is `[]`.
3. **Given** any result, **When** `result.metadata` is accessed, **Then** it is a `QueryMetadata` instance (never `None`).
4. **Given** a result from a normal query, **When** `bool(result)` is evaluated, **Then** it is `True` on success (rows may be empty) and `False` on error — callers can use `if result:` pattern.

---

### User Story 2 — Backward-compatible dict access still works (Priority: P1)

Existing code uses `result["columns"]`, `result.get("rows", [])`, `"error" in result`.
None of this should break. `IVGResult` must be subscriptable and support `.get()`.

**Why this priority**: 189 call sites + 3 API callers. Cannot break them in a minor version.

**Acceptance Scenarios**:

1. **Given** existing code using `result["rows"]`, **When** the new `IVGResult` is returned, **Then** `result["rows"]` works identically to `result.rows`.
2. **Given** existing error check `result.get("error")`, **When** the result has no error, **Then** `.get("error")` returns `None` (not missing key).
3. **Given** `"error" in result`, **When** the result is a success, **Then** it returns `False` (no error key present — consistent with old Dict behavior).

---

### User Story 3 — ObjectScript surface unchanged (Priority: P1)

`IVG.CypherEngine.Execute` calls `execute_cypher` via the Python/IRIS bridge. The bridge
serializes return values — a `Dict` and a Pydantic model serialize identically to JSON.
ObjectScript callers must not be affected.

**Acceptance Scenarios**:

1. **Given** ObjectScript calls `##class(IVG.CypherEngine).Execute(cypher)`, **Then** the returned `%DynamicObject` has the same keys as before.

---

### Edge Cases

- Empty result set (0 rows) on a valid query: `bool(result)` should be `True` (success, not error).
- Query that returns only `columns` with no rows.
- Error path: `sql` and `params` may or may not be present.
- Calling `.get("nonexistent_key")` returns `None` without raising `KeyError`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `IVGResult` MUST be a Pydantic `BaseModel` subclass with fields: `columns: list[str]`, `rows: list[tuple]`, `error: Optional[str]`, `metadata: QueryMetadata`, `sql: Optional[str]`, `params: Optional[list]`.
- **FR-002**: `IVGResult.error` MUST default to `None`. When set, it MUST be a non-empty string.
- **FR-003**: `IVGResult.metadata` MUST default to `QueryMetadata()` — never `None`.
- **FR-004**: `IVGResult` MUST support subscript access (`result["columns"]`) identical to `Dict` access via `__getitem__` and `__contains__` overrides.
- **FR-005**: `IVGResult` MUST support `.get(key, default=None)` returning `None` for missing optional keys (not `KeyError`), and the correct value for present keys.
- **FR-006**: `bool(IVGResult)` MUST return `False` if and only if `error` is set — success with empty rows is still truthy.
- **FR-007**: `execute_cypher` return type annotation MUST change from `Dict[str, Any]` to `IVGResult`.
- **FR-008**: All internal paths in `execute_cypher` and `_execute_parsed` that return a `dict` MUST be updated to return `IVGResult(...)` instead.
- **FR-009**: `IVGResult` MUST be exported from `iris_vector_graph.__init__` alongside `IVGIndex`, `IndexHandle`.
- **FR-010**: Existing tests using `result["columns"]`, `result.get("error")`, `"error" in result` MUST continue to pass without modification.

### Key Entities

- **IVGResult**: Pydantic `BaseModel`. Fields: `columns`, `rows`, `error`, `metadata`, `sql`, `params`. Overrides `__getitem__`, `__contains__`, `get`, `__bool__`. Consistent with `SQLQuery`, `QueryMetadata`, `IndexHandle` Pydantic pattern.
- **QueryMetadata**: Already Pydantic `BaseModel` — no changes needed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `execute_cypher` return type is `IVGResult` — verified by `isinstance(result, IVGResult)`.
- **SC-002**: All 189+ existing test `execute_cypher` call sites pass without modification.
- **SC-003**: `result.columns`, `result.rows`, `result.error` all work with dot notation.
- **SC-004**: `result["columns"]`, `result.get("error")`, `"error" in result` all work identically to the old `Dict` behavior.
- **SC-005**: `bool(result)` is `False` when `error` is set, `True` when `error` is `None`.
- **SC-006**: `IVGResult` is importable from `iris_vector_graph`.
- **SC-007**: `IVG.CypherEngine` ObjectScript tests pass unchanged.

## Assumptions

- `"error" in result` on a success path returns `False` — this matches the old `Dict` behavior where `error` key was absent on success.
- `result.get("sql")` returns `None` when `sql` is not populated — same as `dict.get()` on a missing key.
- The `_bolt_column_types` custom key used by `bolt_server.py` is NOT part of `IVGResult` — bolt server will receive an `IVGResult` and add its custom key by mutation or separate handling.
- ObjectScript bridge serializes Pydantic models as dicts automatically — no ObjectScript changes needed.
- Breaking change to `execute_cypher` return type is a **minor** version bump (backward-compatible via FR-004/FR-005), not major.
