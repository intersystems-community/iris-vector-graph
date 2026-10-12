# Implementation Plan: Temporal edges own a structural row (opt-in)

**Branch**: `alt/temporal-structural-row` | **Spec**: `specs/246-alt-temporal-structural-row/spec.md`

## Summary

A flag on `IRISGraphEngine` makes the temporal write path also own one `rdf_edges` row per
triple, written by calling `create_edge` with `qualifiers={"temporal": True}`. The delete path
removes that row when it is marked and no temporal edge survives. A migration module backfills
rows for existing temporal-only triples. No ObjectScript change.

## Technical Context

**Language/Version**: Python 3.10+ (`.venv` 3.11)
**Primary Dependencies**: `intersystems-irispython` (unit tests use MagicMock; no IRIS)
**Storage**: existing `Graph_KG.rdf_edges` (`qualifiers` JSON column); `^KG("tout")` read via
`get_edges_in_window`
**Testing**: pytest unit tests with fakes (`tests/unit/test_temporal_structural_rows.py`);
live check in `tests/e2e/test_temporal_structural_row_live.py` (lead runs it)
**Project Type**: single library
**Constraints**: `iris_src` not editable here; default behaviour byte-for-byte the same SQL.

## Design decisions

1. **Flag**: `IRISGraphEngine(..., temporal_structural_rows: bool = False)`, stored as
   `self._temporal_structural_rows`. Read with `getattr(..., False)` in the mixin so fakes
   that skip `__init__` keep today's behaviour.
2. **Marker**: `qualifiers` JSON `{"temporal": true}`. The column already exists; nothing new.
   Its weight key is not used, so `WeightOf` is unaffected.
3. **Write path**: when the flag is on and the write succeeded, not strict, and no row exists
   (`SELECT COUNT(*)` on the triple and graph, COALESCE on graph_id), call `create_edge`. This
   replaces the legacy unmarked mirror for that call only. Flag off keeps the legacy code verbatim.
4. **Delete path**: when the flag is on, read the row's qualifiers first. After the store delete
   returns True, read the window for `(source, predicate)` through the store; if the read errors,
   or any row for `target` survives, keep the row. Otherwise call `delete_edge(graph=...)`.
5. **Backfill**: `iris_vector_graph/migrations/temporal_structural_rows.py`. For each graph in
   `graphs`, read temporal triples through `engine.get_edges_in_window` and existing triples in one
   SQL read, then insert the missing ones via `engine.create_edge(..., qualifiers={"temporal": True})`.
   Dry run only computes the plan. Row counts before and after are reported.

## Files

- `iris_vector_graph/engine.py` (constructor keyword only)
- `iris_vector_graph/_engine/temporal.py` (write and delete paths)
- `iris_vector_graph/migrations/temporal_structural_rows.py` (new)
- `tests/unit/test_temporal_structural_rows.py` (new)
- `tests/e2e/test_temporal_structural_row_live.py` (new, not run here)

## Not in this plan

Clearing the marker on an explicit `create_edge` (ADR-0009) and reverting marked rows when the
flag is turned off. Both need a decision and are reported.
