<!-- markdownlint-disable MD013 MD060 -->

# Feature Specification: Native-handle sites in the engine and store go through the Host

**Feature Branch**: `alt/host-sites-2`
**Created**: 2026-10-12
**Status**: Draft (exploratory alternative implementation, not adopted)
**Input**: Package host-sites-2 of docs/research/alt-impl/BRIEF.md: the engine and store native-handle sites use
`iris_vector_graph/host.py` instead of calling Native API methods on a raw handle.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Engine and store operations go through the Host (Priority: P1)

An IVG caller (or a test) runs any engine or store operation that reaches ObjectScript or a global. Today each of
about 130 call sites in `engine.py`, `_engine/*.py` and `stores/iris_sql_store.py` calls `classMethodValue`,
`classMethodVoid`, `isDefined` or `nextSubscript` on a raw Native API handle. After the change those sites call the
Host operations (`call`, `kill`, `get`, `set`, `is_defined`, `next_subscript`) on a handle built by `host_for`.

**Why this priority**: This is the change the package exists for: one module owns the host-sensitive calls.

**Independent Test**: Unit tests with a fake `iris.createIRIS` and a recording Host double show every migrated site
reaches the Host, and a static check finds no Native API method name left in the migrated files.

**Acceptance Scenarios**:

1. **Given** an engine built on an ordinary connection, **When** any migrated operation runs, **Then** the Native API
   call is made on the handle that `host_for` wraps, and the caller's behaviour (return value, exceptions) is the same.
2. **Given** the migrated files, **When** they are searched for `classMethodValue`, `classMethodVoid`, `isDefined` or
   `nextSubscript`, **Then** no match remains except in `_engine/schema.py` and `_engine/class_deploy.py`, which are
   out of this package (schema.py) or use `%` methods the Host refuses (class_deploy.py).

### User Story 2 - The dedicated native connection is chosen as before (Priority: P1)

The engine keeps a second connection for Native API calls, so a long SQL statement does not block them. When that
connection cannot be made the engine falls back to the caller's connection, as it does today.

**Why this priority**: Behaviour must not change in production; this is the part that could silently change it.

**Independent Test**: Unit tests on the engine's host factory with a fake `iris.createIRIS` and a fake
`iris.dbapi.connect` cover: no parameters (caller's connection), parameters and connect succeeds (dedicated
connection), parameters and connect fails (caller's connection), an embedded connection (in-process host).

**Acceptance Scenarios**:

1. **Given** no stored connection parameters, **When** a host is requested, **Then** `iris.createIRIS` is called with
   the caller's connection.
2. **Given** stored parameters and a dedicated connect that succeeds, **When** a host is requested, **Then**
   `iris.createIRIS` is called with the dedicated connection, and the same dedicated connection is reused.
3. **Given** stored parameters and a dedicated connect that raises, **When** a host is requested, **Then**
   `iris.createIRIS` is called with the caller's connection.
4. **Given** an embedded connection, **When** a host is requested, **Then** the host is the in-process host.
5. **Given** `iris.createIRIS` raises, **When** a host is requested, **Then** the error is raised at that point (not on
   first use), as the raw handle did.

### Edge Cases

- The name check on `_safe_createIRIS` is removed. Test engines built with `from_connect` (which store connection
  parameters) now open the dedicated connection; before, the test-only monkeypatch short-circuited it. This is a
  deliberate change and is recorded in the plan.
- Unit tests that patched `engine._iris_obj` to return a fake native handle now patch `iris.createIRIS`, because the
  migrated sites no longer call `_iris_obj`. Their assertions do not change.

## Requirements *(mandatory)*

- **FR-001**: Engine and store MUST provide a host factory that wraps the dedicated (or fallback) connection with
  `host_for`.
- **FR-002**: `_iris_obj()` on the engine and store MUST keep returning a raw Native API handle, because
  `ledger/` and tests call it.
- **FR-003**: Migrated sites MUST use Host operations; `%`-prefixed methods are not routed through the Host.
- **FR-004**: The engine MUST NOT use the `_safe_createIRIS` name check.
- **FR-005**: Behaviour (return values, error paths) MUST be unchanged; the existing unit tests MUST pass, with only
  the patch targets changed where they patched `_iris_obj`.

## Success Criteria *(mandatory)*

- **SC-001**: All new unit tests pass; each was written and seen failing before the code changed.
- **SC-002**: The unit tests in the touched test files pass, or every failure is shown to also fail on the base branch.
- **SC-003**: The static check finds no Native API method name in the migrated source files (see plan).
