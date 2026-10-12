<!-- markdownlint-disable MD013 -->

# Implementation Plan: Migration statements as data (docs and edge vectors)

**Branch**: `alt/migration-as-data` | **Spec**: [spec.md](spec.md)

## Summary

Move the statements of `iris_vector_graph/migrations/docs_and_edge_vectors.py` into
`iris_vector_graph/migrations/statements.py` as frozen `Statement` templates keyed by
name. The step binds them (schema, 4.0.0 staging suffix, vector width and type, full
table name) and runs them through one helper, `run_statement`.

## Technical Context

* Language: Python 3.10+ (the worktree's `.venv` is 3.11).
* Dependencies: none new. `GraphSchema.get_embedding_dimension` is still called with a cursor.
* Testing: unit tests only, using a recording fake connection. No IRIS: the placement
  statements are exercised against fakes, and the IRIS-side behaviour is unchanged.

## Design decisions

* **Templates, bound at use.** The SQL depends on schema and vector width, so
  `STATEMENTS` holds templates and `bind(name, ...)` returns the rendered `Statement`.
  Width is rendered through `int()`, so it cannot carry SQL.
* **`run_statement(conn, stmt, params)`** is the only place this step executes SQL. It
  opens and closes its own cursor and does not commit. The step still commits at the
  points it did before.
* **Max bound parameters.** The rule (`MAX_BOUND_PARAMS = 100` in
  `iris_vector_graph/embedded.py`, which also inlines empty-string parameters) lives in
  the embedded connection's `execute`, not in the step. `run_statement` passes its
  parameters through unchanged, so the rule applies exactly as before. No change there.
* **Tolerance stays in the step.** `_ALREADY_DONE` and the duplicate-row handling are
  step policy and remain in `docs_and_edge_vectors.py`; `run_statement` only executes.
* **Deviation: no empty parameter list.** The step used to pass `[]`; `run_statement`
  passes nothing when there are none. For a DB-API cursor the two are equivalent, and
  the brief asks for this shape.
* **Left inline:** the docs placement's `UPDATE`/`INSERT`/`DELETE` statements take a
  variable number of `?` (one per chunk of ids), and the placement's read queries are
  chosen by whether a column exists. They are not fixed statements, so a single
  declaration would be a lie. The edge-vector placement's fixed statements are declared.

## Project Structure

```text
iris_vector_graph/migrations/statements.py          (new)
iris_vector_graph/migrations/docs_and_edge_vectors.py (refactored to use it)
tests/unit/test_migration_statements.py              (new)
specs/240-alt-migration-as-data/                     (this spec, gitignored, force-added)
```
