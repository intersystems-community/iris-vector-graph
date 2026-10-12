<!-- markdownlint-disable MD013 -->

# Feature Specification: Host sites (package host-sites-1)

**Feature Branch**: `alt/host-sites-1`
**Status**: Draft (exploratory; not adopted)
**Input**: Move the native-handle call sites onto `iris_vector_graph.host` (ADR-0008 step, architecture review candidate 2).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Migration code talks to the host, not the Native API (Priority: P1)

A maintainer reading `migrations/kg_node_stores.py` sees one way of reaching IRIS: `host_for(conn)` and its
operations. The migration no longer builds its own handle, no longer calls `%ExistsId` through `classMethodValue`,
and no longer calls `getString`.

**Why this priority**: the migration is the only caller that calls `%` methods natively; it is the risky one.

**Independent Test**: `tests/unit/test_host_sites.py::test_recompile_*` and `::test_count_entries_*` pass with a
host whose native object is a fake and whose `createIRIS` raises.

**Acceptance Scenarios**:

1. **Given** a connection with a native object on `conn.iris`, **When** the migration counts `^KG` entries,
   **Then** it reads through the host and `iris.createIRIS` is never called.
2. **Given** the shipped classes are defined, **When** the migration recompiles, **Then** existence is asked in SQL
   (no `%Dictionary.ClassDefinition.%ExistsId` call) and `%SYSTEM.OBJ.CompileList` is still called with the same spec.

### User Story 2 - LazyKG and the arno bridge read `^KG` through the host (Priority: P1)

**Independent Test**: `test_host_sites.py::test_lazy_kg_*`, `::test_build_kg_adjacency_*`.

**Acceptance Scenarios**:

1. **Given** a connection carrying a native object, **When** `LazyKG` reads neighbours, **Then** the result is the same
   as before and `createIRIS` is not called.
2. **Given** `IVG_DISABLE_ARNO=1`, **When** `build_kg_adjacency_chunked` walks `^KG`, **Then** the walk uses the host
   and the function reaches its existing "libarno not available" refusal.

### User Story 3 - The schema helpers delegate large replies to the host (Priority: P2)

`_call_classmethod` runs its class method through `host_for`. `_call_classmethod_large` delegates `CHUNKED:` and
`SORTED:` reassembly to `Host.call_large`; the hand-written copies in `schema.py` are deleted. Callers that pass a
native object keep working.

**Independent Test**: `test_host_sites.py::test_call_classmethod_*`, plus the existing `test_230_sorted_marker_decode.py`
and `test_schema_snapshot_coverage.py`, which must still pass unchanged.

### User Story 4 - A native object already on the connection is honoured (Priority: P1)

`host_for(conn)` uses `conn.iris` when present, instead of calling `iris.createIRIS(conn)`.

**Independent Test**: `tests/unit/test_host.py::test_a_native_object_already_on_the_connection_is_used`.

### Edge Cases

- A `%`-named method through the host is refused (existing `HostError`), so no migrated site can call one.
- An interior `^KG` node with no value is not counted as an entry (`is_defined` bit 1 is checked, not `get`).
- A `_call_classmethod_large` caller passing a `MagicMock` native still gets the raw reply for plain strings.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `host_for(conn)` MUST use `conn.iris` when it is not None, before the embedded and createIRIS choices.
- **FR-002**: Migrated sites MUST NOT call `iris.createIRIS`, `EmbeddedIRIS`, `getString`, or any `%`-named method
  directly.
- **FR-003**: `schema._call_classmethod_large` MUST reassemble through `Host.call_large` and keep accepting a native object.
- **FR-004**: Behaviour MUST NOT change for the existing unit tests in the touched modules.

### Key Entities

- **Host**: `iris_vector_graph/host.py`; `ClientHost` gains an optional prebuilt `native` argument.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each migrated site has a unit test that fails on the parent commit and passes after the change.
- **SC-002**: The existing unit tests for the touched modules pass unchanged (350 pass at baseline across the listed files).
- **SC-003**: No `createIRIS`, `getString`, `%ExistsId` or `CHUNKED:`/`SORTED:` reassembly remains in the four files,
  except the `createIRIS` fallback inside `host.py`.
