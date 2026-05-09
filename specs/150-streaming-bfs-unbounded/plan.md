# Implementation Plan: Streaming BFS for Unbounded Variable-Length Path Queries

**Branch**: `150-streaming-bfs-unbounded` | **Date**: 2026-05-06 | **Spec**: [spec.md](./spec.md)

## Summary

Single targeted fix in `_execute_var_length_cypher` in `engine.py`: route unbounded VL path
queries (`max_results == 0`) to `_bfs_stream_pages` instead of `ReadBFSResults`. Bounded queries
(LIMIT present, `max_results > 0`) continue using `ReadBFSResults` unchanged.

The `ReadBFSPage` cursor initialization bug is **confirmed not present** — empty `cursorStep`
is handled correctly by the existing ObjectScript code (`$Order` with empty string).

## Technical Context

**Language/Version**: Python 3.11, ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: `intersystems-irispython>=3.2.0`, `iris-devtester>=1.8.1` (tests only)
**Storage**: `^ArnoKG("bfs_r", tag, step, o)` — written by `BFSFastJsonSorted`, read by `ReadBFSPage`
**Testing**: `pytest`, IRIS container `iris_vector_graph` port `1972`
**Target Platform**: InterSystems IRIS 2025.1+, Python 3.11
**Performance Goals**: Unbounded queries complete without crash; LIMIT queries within 20% of baseline
**Constraints**: No ObjectScript changes required. No breaking changes.
**Scale/Scope**: 1 method changed in `engine.py` (~8 lines), 2 new e2e tests

## Constitution Check

**Principle II (Compatibility-First)**: ✅ All existing bounded query behavior unchanged.

**Principle III (Test-First)**: ✅ e2e test for 90K+ result unbounded query written before fix.

**Principle IV (IRIS e2e testing)**:
- [x] Container: `iris_vector_graph` (verified from `docker-compose.yml:4`)
- [x] Port: `1972` (verified from `docker-compose.yml:5`)
- [x] e2e test phase non-optional
- [x] `SKIP_IRIS_TESTS` defaults to `"false"`

**Principle V (Simplicity)**: The fix is 4 lines in one method. No new abstractions.

**Principle VI (Grounding Rule)**:
- Container: `iris_vector_graph` ← `docker-compose.yml:4`
- Port: `1972` ← `docker-compose.yml:5`
- PLAID class: confirmed not relevant here
- `_bfs_stream_pages`: confirmed at `engine.py:32`
- `ReadBFSResults`: confirmed at `Traversal.cls:444`
- `ReadBFSPage`: confirmed at `Traversal.cls:468` — cursor init already correct

## Root Cause

`_execute_var_length_cypher` (engine.py ~line 1553):

```python
# Current (broken for large unbounded results):
results_str = str(_call_classmethod(conn, "Graph.KG.Traversal", "ReadBFSResults", tag))
bfs_results = _json.loads(results_str)  # <MAXSTRING> if results_str > 3.6MB

# Fallback only on exception — but <MAXSTRING> may not propagate as Python exception
except Exception:
    bfs_results = list(_bfs_stream_pages(self.conn, tag))
```

Fix: check `max_results` (already extracted from `vl` dict) before choosing path.

## Project Structure

### Source Code

```text
iris_vector_graph/
└── engine.py       # MODIFIED — _execute_var_length_cypher: routing logic

tests/e2e/
└── test_streaming_bfs.py   # NEW — unbounded query e2e tests
```

## Phase 0: Research

### Decision Log

**D-001: Route on max_results, not on exception**
- Decision: `if max_results == 0: use _bfs_stream_pages; else: use ReadBFSResults`
- Rationale: Exception-based routing is fragile — `<MAXSTRING>` in IRIS can surface as
  various Python exception types or not at all. Explicit routing on intent is deterministic.
- Alternative rejected: Catch `<MAXSTRING>` specifically — unreliable cross-IRIS-version.

**D-002: No ObjectScript changes needed**
- Decision: Pure Python engine fix.
- Rationale: `ReadBFSPage` cursor initialization already handles `cursorStep=""` correctly.
  `BFSFastJsonSorted` already writes to `^ArnoKG` correctly for all result sizes.
  The bug is entirely in which Python path the engine takes.

**D-003: LDBC SF10 for 90K+ test**
- Enterprise container (port 4972) has SF10 loaded: 62K persons, 3.87M KNOWS edges.
- Seed `p_28587302384882` (degree=1553) has 38K 2-hop neighbors — sufficient for the test.
- Alternatively: build a synthetic test graph on community IRIS.

## Phase 1: Design & Contracts

### The Fix (engine.py)

```python
# In _execute_var_length_cypher, after BFSFastJsonSorted returns tag:
if max_results == 0:
    # Unbounded: always stream — never risk <MAXSTRING>
    bfs_results = list(_bfs_stream_pages(self.conn, tag))
else:
    # Bounded (LIMIT present): single-call fast path
    results_str = str(_call_classmethod(conn, "Graph.KG.Traversal", "ReadBFSResults", tag))
    bfs_results = _json.loads(results_str)
```

### max_results source

`max_results` comes from `vl.get("max_results", 0)` where `vl = sql_query.var_length_paths[0]`.
The translator populates this from the LIMIT clause. Value is `0` when no LIMIT, `N` when LIMIT N.

## Implementation Task Groups

### A. Write failing e2e test (test-first)
Write `test_unbounded_bfs_completes` before fixing engine.

### B. Engine fix
Change routing logic in `_execute_var_length_cypher`.

### C. Validate bounded queries unaffected
Run existing VL path tests; add explicit regression test.

### D. Validate + publish
Full test suite, bump version.
