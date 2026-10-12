<!-- markdownlint-disable MD013 -->

# Feature Specification: Migration statements as data (docs and edge vectors)

**Feature Branch**: `alt/migration-as-data`
**Status**: Exploratory (alternative implementation; not adopted)
**Input**: The 4.0.0 docs and edge-vector upgrade step carries its SQL as strings inside Python control flow, so tests can only check the SQL that was sent.

## User Scenarios & Testing

### User Story 1 - Each statement is a named, checkable declaration (Priority: P1)

A maintainer can read the full list of statements the docs and edge-vector step runs, by name, with what each takes and what it is expected to do, without reading the control flow.

**Acceptance Scenarios**:

1. **Given** the statement declarations, **When** each is checked, **Then** its name equals its key, its `?` placeholder count equals its documented parameters, and it carries no trailing `;`.
2. **Given** a statement declared as `ddl`, `rows-changed` or `rows-selected`, **When** an unknown expectation is given, **Then** construction is refused.

### User Story 2 - The rendered SQL is unchanged (Priority: P1)

The SQL text the step produces after the refactor is byte-for-byte the text it produced before.

**Acceptance Scenarios**:

1. **Given** the pre-refactor text captured as golden values, **When** the public `docs_prepare_sql`, `docs_finish_sql`, `edge_vectors_prepare_sql` and `edge_vectors_finish_sql` are called, **Then** their output equals the golden text.
2. **Given** the placement of edge vectors, **When** it runs against a recording connection, **Then** each fixed statement has the golden text and the same parameters.

### User Story 3 - One place executes the step's SQL (Priority: P2)

Every statement the step looks up by name is run through `run_statement`, which strips a trailing `;`, passes no parameter list when there are none, refuses a parameter count that does not match its declaration, returns rows only for `rows-selected`, closes its cursor, and does not commit.

**Acceptance Scenarios**:

1. **Given** a fake cursor, **When** a statement with a trailing `;` is run, **Then** the executed text has none.
2. **Given** a statement with no parameters, **When** it is run, **Then** the cursor receives no parameter list.
3. **Given** a parameter count that differs from the declaration, **When** it is run, **Then** nothing is executed.

## Out of scope

* The docs placement's chunked `IN (...)` statements (variable placeholder counts). They stay inline; see plan.md.
* The graph-scoped embeddings migration and its backfill, which another package edits.
* Any change to the width-tolerance or already-done behaviour.

## Success Criteria

* `tests/unit/test_migration_statements.py` passes; the existing unit tests that import `docs_and_edge_vectors` still pass unchanged.
* No SQL text, parameter value or commit point changes.
