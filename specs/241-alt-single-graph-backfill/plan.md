<!-- markdownlint-disable MD013 -->

# Implementation Plan: Single-graph backfill shortcut

**Branch**: `alt/single-graph-backfill` | **Spec**: `spec.md`

## Where it lives

- `GraphSchema.get_graph_scope_migration_sql` (schema.py, not edited) emits, per child table, the
  correlated `UPDATE Graph_KG.<t> c SET c.graph_id = (...) WHERE c.graph_id IS NULL AND (...) = 1`.
- `_Migrator._rekey_children` (graph_scoped_embeddings.py) runs that list. The regex
  `_GRAPH_BACKFILL` matches that statement; `_backfill_graph_id` then either runs it unchanged or
  sends the constant UPDATE.
- `_single_graph` makes the decision with two reads: `SELECT DISTINCT n.graph_id FROM nodes` (must be
  one non-NULL value) and a NOT IN count of child rows whose s is absent from nodes (must be 0).

## Decisions

- The constant UPDATE inlines the graph literal (quote-escaped) instead of binding `?`. An empty
  string bound as a parameter may become NULL; the default graph is `''`, so the value must be exact.
- The statement is still sent through `_tolerate`, so a re-run keeps its existing semantics.
- The orphan check covers all child rows, not only NULL ones: stricter, so it can only fall back more.

## Test strategy (test first)

- Unit: `tests/unit/test_single_graph_backfill.py` on `RekeyDB`, with a cursor subclass that answers
  the two reads and the constant UPDATE. The shortcut-form tests were written and seen to fail first;
  the fallback and equivalence tests characterise current behaviour.
- Live: `tests/e2e/test_single_graph_backfill_equivalence.py` (IRIS, run by the lead).
