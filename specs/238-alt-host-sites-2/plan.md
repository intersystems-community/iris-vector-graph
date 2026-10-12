<!-- markdownlint-disable MD013 MD060 -->

# Implementation Plan: Native-handle sites go through the Host

**Branch**: `alt/host-sites-2` | **Spec**: [spec.md](spec.md)

## Summary

`host.py` (the Host interface, owned by another package) already exists on `native-handle-module`; this branch carries a
copy of `host.py` and `tests/unit/test_host.py` (commit 58ade9c3) so it can be built and tested. The engine and the
store each get a `_host()` factory that builds the dedicated connection exactly as `_iris_obj()` does and wraps it in
`host_for`. Migrated sites call `_host()` and Host operations. `_iris_obj()` stays as the raw-handle accessor for
`ledger/` and tests.

## Design decisions

1. **Factory split.** `_iris_obj()` (raw) and `_host()` (Host) share one connection-selection function. `_host()`
   builds `host_for(conn)` and touches `host.native` once, so a `createIRIS` failure surfaces at the same point it did
   before, inside the caller's `try`.
2. **Name check removed.** `_iris_obj` used `getattr(createIRIS, "__name__") == "_safe_createIRIS"` to skip the
   dedicated connection in tests. The Host's behaviour (fall back to the caller's connection when the dedicated one
   cannot be made) replaces it. Consequence: an engine built by `from_connect` in a test now opens the dedicated
   connection. Test-only; flagged in the report.
3. **Store** uses the same pattern with `self.conn` (no dedicated connection exists for the store today).
4. **Embedded.** `host_for(EmbeddedConnection)` is `InProcessHost`, which resolves `iris.cls` and `iris.gref` at call
   time, matching `EmbeddedIRIS`'s default (`py_names=False`) path.
5. **Out of scope, stays raw**: `_engine/class_deploy.py` (uses `%`-methods, `classMethodObject`, `%SYS.Python`; the Host
   refuses `%`); `_engine/schema.py` (not this package); `ledger/*` (not this package; they call `engine._iris_obj()`).
6. **Mapping of Native API calls to Host operations**:
   - `classMethodValue(c, m, *a)` and `classMethodVoid(c, m, *a)` to `call(c, m, *a)`
   - `kill`, `get`, `set` keep their names and argument order
   - `isDefined(name, *subs)` to `is_defined(name, *subs)` (returns int)
   - `nextSubscript(reverse, name, *subs)` to `next_subscript(name, *subs, reverse=reverse)`
   - Helpers that receive a handle (`_reload_arno_if_needed`, the algorithms reader) take the Host.
7. **Tests that patched `_iris_obj`** (about 70, in `tests/unit`, plus a few in `tests/integration`) patch
   `iris.createIRIS` with the same fake, because the migrated sites reach it through `_host()`.

## Static check (SC-003)

A unit test scans the migrated files for `classMethodValue|classMethodVoid|isDefined|nextSubscript|classMethodObject`
and for `_iris_obj()` calls outside the definition. `schema.py` and `class_deploy.py` are excluded by name.

## Test strategy

- Factory tests (FR-001, FR-004, User Story 2) with a fake `iris` module in `sys.modules`, as in
  `tests/unit/test_host.py`.
- Static check (User Story 1 acceptance 2).
- Behaviour tests for two representative migrated sites per file (fake createIRIS, recorded Native calls).
- Existing unit tests that touch these files, run before and after, with failures compared to the base branch.

## Not run here

Any test needing a live IRIS (`tests/e2e`, `tests/integration`) is for the lead's serial pass. ObjectScript is not
touched by this package.
