<!-- markdownlint-disable MD013 -->

# Tasks: Migration statements as data (docs and edge vectors)

Format: `[ID] Description`. Test tasks come first. `[x]` means done in this branch.

## Phase 1: Tests first

- [x] T001 Capture the pre-refactor SQL text of the docs and edge-vector step as golden values (from the original functions and from a recording run of `place_edge_vectors`).
- [x] T002 Write `tests/unit/test_migration_statements.py`: (a) declarations well formed, (b) golden text, (c) `run_statement` through a fake cursor.
- [x] T003 Confirm the new tests fail before the module exists (collection error: no `statements` module).

## Phase 2: Declarations

- [x] T004 Write `iris_vector_graph/migrations/statements.py`: `Statement`, `EXPECTS`, `STATEMENTS`, `bind`, `bind_all`, `run_statement`.
- [x] T005 Run `tests/unit/test_migration_statements.py` against the declarations alone; fix until (a) and (b) pass.

## Phase 3: The step uses them

- [x] T006 Refactor the public `*_sql` functions in `docs_and_edge_vectors.py` to render from `STATEMENTS`; signatures unchanged.
- [x] T007 Route `_run_tolerantly` through `run_statement`, keeping `_ALREADY_DONE`.
- [x] T008 Route `_reset_wrong_width_tables` through `edge_held_rows` and `edge_width_reset_drop`.
- [x] T009 Route the four fixed edge-vector placement statements through `run_statement`, keeping the duplicate-row tolerance.

## Phase 4: Verify

- [x] T010 Run `tests/unit/test_migration_statements.py`, then the existing unit tests that touch `docs_and_edge_vectors` (`test_230_*` placement, rekey, bookkeeping, `test_edge_vector_resume_width.py`, `test_upgrade_dry_run_read_only.py`), plus `tests/unit/test_host.py`.
- [x] T011 Compare the new output with the captured golden run for `place_edge_vectors` (parameters and order).

## Phase 5: Commit

- [ ] T012 `git add -f specs/240-alt-migration-as-data` and commit the code, tests and specs on `alt/migration-as-data`. No push.
