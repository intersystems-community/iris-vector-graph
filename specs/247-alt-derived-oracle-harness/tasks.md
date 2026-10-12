<!-- markdownlint-disable MD013 -->

# Tasks: Derived-store oracle

**Input**: [spec.md](spec.md), [plan.md](plan.md)

## Phase 1: Tests first (unit)

- [x] T001 Write `tests/unit/test_derived_oracle.py`: fake host (dict-backed, collation: numbers before strings) and fake connection.
- [x] T002 [P] Tests for consistent state: no violations, default graph spelled `None`, `''`, `$Char(0)` all map to key `0`.
- [x] T003 [P] One test per kind: `ghost_out`, `ghost_in`, `missing_out`, `missing_in`, `out_in_mismatch`.
- [x] T004 [P] R9 test: counter for a source with zero rows is `ghost_deg`, and it is not reported as `deg_mismatch`.
- [x] T005 [P] `deg_mismatch`, `degp_mismatch` (wrong value, and counter for a source with no rows).
- [x] T006 [P] Multigraph ekey children: consistent case clean, extra child is `ghost_out`, `#` is not an edge.
- [x] T007 Run the unit file; record which tests fail before the module exists (or note the deviation).

## Phase 2: Oracle module

- [x] T008 Implement `tests/support/__init__.py` and `tests/support/derived_oracle.py` (`Violation`, `Walk`, `graph_key`, `DerivedOracle.rows/walk/violations`, `KINDS`, `ADJACENCY_KINDS`, `ADVISORY_KINDS`).
- [x] T009 Run unit tests until green. Show the output.

## Phase 3: E2E property test (lead runs; needs IRIS)

- [x] T010 Write `tests/e2e/test_derived_oracle_property.py`: seeded `random.Random`, ops insert / delete / rollback-after-insert / failed-statement-mid-batch, oracle after every step, `allowed` kinds as a parameter (strict and lenient).
- [x] T011 Mark `@pytest.mark.e2e`. Not run by the worktree author (no container).

## Dependencies

- T001 before T002-T006. T008 after T007. T010 after T008.

## Notes

- Deviation: T008 was drafted before T001 in this session (the oracle module was written first, then the tests). The unit tests were then written against it. Reported in the package report.
