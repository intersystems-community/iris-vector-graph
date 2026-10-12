<!-- markdownlint-disable MD013 -->

# Implementation Plan: Host sites (package host-sites-1)

**Spec**: `specs/237-alt-host-sites-1/spec.md`

## Summary

Four modules speak the Native API directly: `migrations/kg_node_stores.py`, `stores/lazy_kg.py`,
`stores/arno_bridge.py` (two sites), and `schema.py` (`_call_classmethod`, `_call_classmethod_large`). Each moves to
`host.host_for(conn)`. `host.py` gains the `conn.iris` precedence and an optional prebuilt native on `ClientHost`.

## Paths (deviations from the package text)

The package text names `iris_vector_graph/lazy_kg.py`, `iris_vector_graph/arno_bridge.py` and
`iris_vector_graph/_engine/schema.py`. The real files are `iris_vector_graph/stores/lazy_kg.py`,
`iris_vector_graph/stores/arno_bridge.py` and `iris_vector_graph/schema.py` (`_engine/schema.py` has no native
calls). This plan uses the real paths.

## Technical Context

- Host module: `iris_vector_graph/host.py`, cherry-picked from `native-handle-module` (b6471848) because the branch
  base did not contain it.
- Native callers outside the owned files (`_engine/nodes_edges.py`, `_engine/algorithms.py`) pass native objects
  into `_call_classmethod_large`; the helper keeps accepting them by wrapping in `ClientHost(None, native=...)`.
- `stores/iris_sql_store.py` and `engine.py` are owned by another package and are not touched.

## Design decisions

1. `host_for(conn)`: `conn.iris` (not None) → `ClientHost(conn, native=conn.iris)`; `EmbeddedConnection` →
   `InProcessHost`; otherwise `ClientHost(conn)`.
2. `ClientHost.__init__(conn, native=None)`: a prebuilt native is used as is; otherwise `iris.createIRIS` is looked up
   lazily, as now.
3. `kg_node_stores`: `_native` is removed; `host_for(conn)` replaces it. `_value_at` checks `is_defined(...) & 1`
   before `get`, which keeps the "value, not node" rule that `getString` gave (`None` when undefined). Existence is
   `host.class_exists`. The recompile keeps `%SYSTEM.OBJ.CompileList` through `host.call`: it is a method of a
   system class, not a `%`-named method, so the host guard passes it. Flagged in the report.
4. `lazy_kg` and `arno_bridge`: `nextSubscript`/`get` calls go through the host with the same arguments.
5. `schema._call_classmethod`: `host_for(conn).call(...)`. `_call_classmethod_large(iris_obj, ...)`: wrap a non-Host
   in `ClientHost(None, native=iris_obj)` and return `.call_large(...)`. `_read_large_chunks`, `_read_sorted_marker`
   and the reader constants are deleted from `schema.py`.

## Test strategy

Unit only (`PYTHONPATH=<worktree> .venv/bin/python -m pytest`). Site tests use a `conn` carrying a fake native on
`conn.iris` and a `sys.modules["iris"]` whose `createIRIS` raises, so any site still calling it fails. No IRIS, no
e2e run by this package.

## Risks

- `get` on a `ClientHost` (Native API) versus `getString` is not checked against a live server; the `is_defined`
  check narrows it. Lead's live pass should cover the rekey dry run.
- Honouring `conn.iris` changes behaviour for any connection object that already carries an `iris` attribute.
