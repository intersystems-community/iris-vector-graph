<!-- markdownlint-disable MD013 -->

# Tasks: Single-graph backfill shortcut

Test tasks come first. Ticked when done.

## Tests first

- [x] T001 Unit: one-graph install sends one constant UPDATE per child table
- [x] T002 Unit: default graph `''` is written as `''`; quote is escaped
- [x] T003 Unit: two graphs fall back to the correlated statement
- [x] T004 Unit: an orphan child row falls back
- [x] T005 Unit: empty nodes and a NULL node graph fall back
- [x] T006 Unit: shortcut and forced-off paths leave identical state
- [ ] T007 E2E (IRIS, lead runs): tests/e2e/test_single_graph_backfill_equivalence.py

## Implementation

- [x] T008 Regex, `_backfill_graph_id` and `_single_graph` in graph_scoped_embeddings.py
- [x] T009 Run the new unit tests and the existing migration unit tests
- [ ] T010 Commit (spec files added with `git add -f`)
