# Tasks: Spec 203 — Regressions and Ported Work

Covers the 2026-09-25 section of `spec.md`. Tests live in
`tests/unit/test_203_tck_regressions.py` (`T` below); the phase gate is a
same-namespace TCK run compared against `main`. Code changes are in
`iris_vector_graph/cypher/translator.py` (`X` below).

## Phase 1: Bisected regressions

- [x] T001 Write failing tests for WithOrderBy2/4, Precedence1 [23]/[25],
      Comparison1 [10], Temporal5 [1], Graph3 [9] in `T`
- [x] T002 Fix each regression in `X`
- [x] T003 Gate: 24-area TCK run, nothing that passes on main fails (`8ef268b`)

## Phase 2: Temporal port (a96a31b)

- [x] T004 Write failing tests `TestTemporalProjectionFromVariables` and
      `TestDurationKeepsHoursPastADay` in `T`
- [x] T005 Call `_build_temporal_from_variable_map` from map constructors and
      add the `time(var)` projection in `X`
- [x] T006 Remove the hours → days duration carry in `X`
- [x] T007 Update SQL-shape assertions in
      `tests/unit/test_translator_temporal_v7a.py` to the folded output
- [x] T008 Gate: Temporal3 183/183, Temporal7 17/18 under behave

## Phase 3: Precedence port (a634adb)

- [x] T009 Write failing tests `TestListMembershipIsOneReader` in `T`
- [x] T010 Emit JSON-list membership as one 1/0/NULL scalar subquery
      (`_json_list_membership`) in `X`
- [x] T011 Skip the `<=`/`>=` NaN decomposition for a membership operand in `X`
- [x] T012 Gate: Precedence1 72/72; 24-area TCK 2802/3079 vs main 2661, zero
      regressions

## Phase 4: Polish

- [x] T013 Full unit suite passes (`tests/unit`)
- [x] T014 Delete agent branches and worktrees a96a31b, a634adb; drop the
      scratch namespace
