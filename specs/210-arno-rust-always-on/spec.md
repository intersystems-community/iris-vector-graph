# Spec 210: Arno Rust Kernel — Process-Isolation Fix

**Branch**: `210-arno-rust-always-on`
**Date**: 2026-09-01
**Status**: Draft

## Context

`ArnoAccel.Load()` stores the loaded DLL handle in `^||ArnoAccel("dllid")` — a
process-private global. IRIS routes `classMethodValue()` and SQL `LANGUAGE
OBJECTSCRIPT` function calls to arbitrary worker processes from a connection
pool. Any worker that did not execute `Load()` sees `^||ArnoAccel("dllid") = 0`,
so `IsAvailable()` returns false, `NKGAccel.Capabilities()` reports
`rust_callout: false`, and all Rust-accelerated paths (pagerank, wcc, cdlp,
bfs) are silently bypassed in favour of ObjectScript fallbacks.

Confirmed by probe (2026-09-01): calling `Load()` and `Capabilities()` on the
**same** native connection yields `rust_callout: true` and
`rust_algorithms: [pagerank, wcc, cdlp, bfs]`. Calling `Capabilities()` via a
fresh connection yields `rust_callout: false`.

This means `execute_pagerank`, `execute_wcc`, `execute_cdlp`, and BFS are
**never** using the Rust kernel in production — despite the `.so` being present
and loadable.

## User Stories

### US1 [P1] — Rust kernel fires on every analytics call

**As a** developer running graph analytics on a large IRIS graph,  
**I want** `execute_pagerank`, `execute_wcc`, `execute_cdlp`, and BFS to use
the Rust kernel whenever `libarno_callout.so` is loadable,  
**So that** I get the 10–100× speedup Arno was designed to deliver, not silent
ObjectScript fallback.

**Acceptance criteria**:

- After `Load()`, any subsequent `classMethodValue()` call to
  `ArnoAccel.IsAvailable()` returns `true` regardless of which IRIS worker
  handles it.
- `NKGAccel.Capabilities()` reports `rust_callout: true` and non-empty
  `rust_algorithms` on the next call after `Load()` completes.
- A PageRank call with Arno loaded returns results and the capabilities probe
  confirms `rust_callout: true`.

### US2 [P1] — Auto-reload on stale worker

**As a** long-running Python process,  
**I want** `_detect_arno()` and `_arno_call()` to automatically reload the
`.so` if the current worker has no dllid,  
**So that** Rust acceleration resumes after IRIS worker recycling without
requiring a process restart.

**Acceptance criteria**:

- If `IsAvailable()` returns false but a known `.so` path exists,
  `_detect_arno()` reloads before reporting capabilities.
- `_arno_call()` detects `IsAvailable() = false` and triggers reload before
  executing the Rust call.
- A test simulates stale-worker by clearing `^ArnoAccel("dllid")` and
  confirms the next call self-heals.

### US3 [P2] — Capabilities truthfully reflect Rust state

**As a** developer reading `engine._store._arno_capabilities`,  
**I want** `rust_callout` and `rust_algorithms` to reflect actual Rust
availability at the moment of the capabilities call,  
**So that** dispatch logic (`_engine/schema.py:505`,
`_engine/query.py:1573`) correctly routes to the Rust path.

**Acceptance criteria**:

- `Capabilities()` called immediately after `Load()` on any connection returns
  `rust_callout: true`.
- `Capabilities()` called before any `Load()` returns `rust_callout: false`.
- Integration test asserts `_arno_capabilities["rust_callout"] is True` after
  engine initialisation with Arno loaded.

## Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-001 | `^||ArnoAccel("dllid")` changed to `^ArnoAccel("dllid")` so the handle persists across worker processes (survives until IRIS restart or explicit kill). |
| FR-002 | `ArnoAccel.Load()` writes dllid to `^ArnoAccel`, not `^||ArnoAccel`. |
| FR-003 | `ArnoAccel.IsAvailable()` reads from `^ArnoAccel`. |
| FR-004 | `ArnoAccel.CallZF()` (and any internal `$ZF(-5)` calls) reads dllid from `^ArnoAccel`. |
| FR-005 | `_detect_arno()` in `iris_sql_store.py`: if `IsAvailable()` returns false but `IVG_ARNO_LIB` or default `.so` path exists, call `Load()` before `Capabilities()`. |
| FR-006 | `_arno_call()` in `iris_sql_store.py`: guard — if `IsAvailable()` false, attempt reload before `$ZF(-5)` dispatch. Raise `ArnoError` only if reload also fails. |
| FR-007 | `NKGAccelLoader.Capabilities()` adds no new logic — the fix to `^ArnoAccel` is sufficient for `IsAvailable()` to return correct value. |
| FR-008 | No behaviour change when `IVG_DISABLE_ARNO=1` — must still force false. |

## Edge Cases

- **IRIS restart**: `^ArnoAccel("dllid")` is cleared. First `_detect_arno()` call
  reloads. Expected: auto-heal.
- **`.so` deleted**: reload fails. Expected: `ArnoError`, fallback to
  ObjectScript, `rust_callout: false`.
- **Concurrent Load()**: two Python threads both probe simultaneously. Expected:
  idempotent — second `Load()` is a no-op (DLL already in address space).
- **Wrong arch `.so`**: `Load()` returns 0. Expected: `_arno_available = False`,
  no crash.
- **`IVG_DISABLE_ARNO=1`**: no probe, no load, returns false immediately.

## Non-Functional Requirements

- ObjectScript change is confined to `ArnoAccel.cls` — no other `.cls` files
  changed.
- Python change confined to `iris_sql_store.py` — no public API signature
  changes.
- No performance regression on the load path — `^ArnoAccel` lookup is O(1).
- Backward compatible: existing deployments that never call `Load()` are
  unaffected.

## Success Criteria

1. After spec 210 merge, `_arno_capabilities["rust_callout"]` is `True` in the
   enterprise container integration tests.
2. `rust_algorithms` contains `["pagerank", "wcc", "cdlp", "bfs"]`.
3. An end-to-end `execute_pagerank` call with Arno loaded returns results and
   a capabilities re-probe after that call still shows `rust_callout: True`.
4. Spec 208 regression: 26 pass, 2 skip, zero failures.
5. Spec 209 regression: 7 pass, zero failures.

## Clarifications

### Session 2026-09-01

- Q: Fix scope — ObjectScript only (`^||` → `^`), or also Python auto-reload?
  → A: Both. ObjectScript fix (FR-001–004) is the root cause. Python auto-reload
  (FR-005–006) is the resilience layer for post-IRIS-restart worker staleness.
- Q: Use `^ArnoAccel` (survives restart) or `^||ArnoAccel` in job-private
  namespace? → A: `^ArnoAccel` in USER namespace — persists until IRIS restart
  or explicit kill, visible to all worker processes.
