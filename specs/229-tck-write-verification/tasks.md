# Tasks: TCK Write Verification

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)
**Prerequisites**: `ivg-iris-enterprise` running on port 31972

Test-first throughout. Each phase's gate must pass before the next phase starts.

Run the harness with:

```bash
DOCKER_CONTEXT=orbstack IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m behave tests/tck/features
```

and its own unit tests with `SKIP_IRIS_TESTS=true .venv/bin/python -m pytest tests/unit`.

---

## Phase 1: Measure the mapping

**Goal**: no side-effect column is implemented on a guess.

- [x] T001 Count every distinct side-effect column used in `tests/tck/features/`, with a
      per-column scenario count, and write the table into `specs/229-tck-write-verification/research.md`
- [x] T002 For `+labels`/`-labels`, read the ten scenarios that use them most and record what
      the number means in each, in `research.md`
- [x] T003 Decide the `+labels`/`-labels` mapping — measured distinct-new-names, or deferral
      with a reason — and update plan.md's mapping table so no row reads "needs a decision"
- [x] T004 Record whether any scenario asserts a side-effect column IVG has no table for, and
      list those scenarios as candidate deferrals in `research.md`

**Gate**: plan.md's mapping table is complete; `research.md` carries the counts.

---

## Phase 2: The `SideEffects` module (Foundational)

**Goal**: one interface owns snapshot, delta and column mapping, tested without a container.

- [x] T005 [P] Write `tests/unit/test_tck_side_effects.py`: `delta_to` over two hand-built
      snapshots returns the expected per-column dict, including negative columns
- [x] T006 [P] Write the test that `unmapped(["+labels", "+bogus"])` names every column the
      mapping does not cover
- [x] T007 [P] Write the test that a delta of zero is distinguishable from an absent snapshot
- [x] T008 Implement `SideEffects` in `tests/tck/side_effects.py` — `capture(conn)`,
      `delta_to`, `unmapped`, and the column mapping settled in T003
- [x] T009 Write the test asserting the `+properties` ↔ one-`rdf_props`-row assumption
      explicitly, so a future map-valued property fails here rather than silently miscounting

**Gate**: T005–T009 pass; no step definition references `SideEffects` yet.

---

## Phase 3: A raising query fails its scenario (US2, P1)

**Goal**: an exception can no longer be scored as an empty result.

- [x] T010 [P] [US2] Write `tests/unit/test_tck_result_steps.py`: a context whose
      `last_error` is set fails `step_result_should_be_empty`, and the message carries the
      exception text
- [x] T011 [P] [US2] Write the test that a zero-row result still passes that step
- [x] T012 [P] [US2] Write the test that every result-table step fails on `last_error` rather
      than comparing against `None`
- [x] T013 [P] [US2] Write the test that an error-expecting step still passes on a raised
      exception — this path must not get stricter
- [x] T014 [US2] Remove the early return at `tests/tck/steps/results.py:57-58` and raise with
      the exception type and message instead
- [x] T015 [US2] Make `_assert_table` fail on `last_error` before it reads `last_result`
- [x] T016 [US2] Run the full corpus and record the new scenario tallies in `research.md`,
      separating "newly failing" from "errored out of the harness"

**Gate (E2E)**: T010–T013 pass; the full corpus run completes with no harness-level errors,
and the newly-failing count is recorded.

---

## Phase 4: Side effects are counted (US1, P1)

**Goal**: the assertion that should have caught defect 2 can fail.

- [x] T017 [P] [US1] Write the test that `step_side_effects_should_be` fails when the
      measured delta disagrees with the scenario table, naming column, expected and observed
- [x] T018 [P] [US1] Write the test that `step_no_side_effects` fails on any non-zero delta
      and names the table
- [x] T019 [P] [US1] Write the test that an unmapped column fails the scenario with an
      "unmapped side-effect column" message (FR-004)
- [x] T020 [P] [US1] Write the test that the error check runs before the count comparison, so
      a rolled-back query reports the error and not a count mismatch
- [x] T021 [US1] Capture a `SideEffects` snapshot before and after each
      `When executing query` / `executing control query` step in `tests/tck/steps/query.py`,
      on the scenario's own connection
- [x] T022 [US1] Wire both side-effect steps in `results.py` to the captured delta
- [x] T023 [US1] Add the `^KG`/`^NKG` unexpected-write check, off by default behind a flag
      until it is quiet (plan.md risk 4)
- [x] T024 [US1] Create a scratch worktree, revert `_create_match_gate`, and record which
      upstream scenarios fail — this is SC-001 (measured: none do, so SC-001 rests on
      T036; see research.md)
- [x] T025 [US1] Measure the wall-clock delta of the full run against the pre-Phase-4
      baseline; if over 2×, collapse the five counts into one statement and re-measure

**Gate (E2E)**: SC-001 proven in T024; T017–T020 pass; full corpus completes.

---

## Phase 5: Error kinds are matched, not merely counted (US3, P2)

- [x] T026 [P] [US3] Write the test that a `-119`/`-121` integrity error satisfies
      `ConstraintVerificationFailed`
- [x] T027 [P] [US3] Write the test that a prepare-time `-23` does **not** satisfy it, and
      that the message distinguishes the two
- [x] T028 [P] [US3] Write the test that a result object carrying an arbitrary non-empty
      `error` string no longer passes an error-expecting step without a kind check (FR-008)
- [x] T029 [P] [US3] Write the test that the runner refuses a deferral entry with no reason
- [x] T030 [US3] Replace the bare `(Exception,)` entries in `ERROR_TYPE_MAP` with mappings to
      the IVG/IRIS error surface, including SQLCODE ranges
- [x] T031 [US3] Remove the early return in `step_error_type_raised` that accepts any
      `result.error` string
- [x] T032 [US3] Add the required-reason format to `wip.txt` (or its replacement) and the
      runner check; migrate the 129 existing entries, adding a reason to each
- [x] T033 [US3] List every error kind IVG genuinely cannot distinguish, with its reason, in
      `docs/KNOWN_ISSUES.md`

**Gate (E2E)**: T026–T029 pass; SC-006 holds — the runner refuses a reasonless entry.

Status 2026-09-26: done. Tests are in `tests/unit/tck/test_error_kinds.py`, the
mapping is in `tests/tck/steps/errors.py`, and `tests/tck/wip.txt` holds 3
reasoned entries.

---

## Phase 6: The supplementary corpus (US4, P2)

- [ ] T034 [US4] Create `tests/tck/features_ivg/` with a README stating it is IVG-owned, not
      upstream TCK, and is counted separately
- [ ] T035 [P] [US4] Write the inline-property relationship `MERGE` scenario — one node pair
      and one relationship on the first run, nothing on the second
- [ ] T036 [P] [US4] Write the zero-row `MATCH` + `CREATE` scenario — no side effects at all
- [ ] T037 [P] [US4] Write the recorded-deviation scenario for row multiplicity: a `MATCH`
      binding N rows creates one node, marked as a deviation from openCypher with the
      measured behaviour (FR-015)
- [ ] T038 [P] [US4] Write the recorded-deviation scenario for `SQLCODE -119` on a
      re-`CREATE`d identical edge (FR-015)
- [ ] T039 [US4] Teach the runner to collect `features_ivg/` and count it apart from
      `features/` (FR-013)
- [ ] T040 [US4] In the scratch worktree, revert `_merge_literal_node_id` and confirm T035
      fails with the `-23` reported — this is SC-002

**Gate (E2E)**: SC-002 proven in T040; all four supplementary scenarios pass against the
current tree.

Status 2026-09-26: not started. `tests/tck/features_ivg/` does not exist.

---

## Phase 7: Report what is measured (US5, P3)

- [ ] T041 [P] [US5] Write the test that a scenario records two verdicts and that
      `behave`'s own pass/fail is their conjunction
- [ ] T042 [US5] Record the pair per scenario and write the two-number summary in
      `after_all` in `tests/tck/environment.py`
- [ ] T043 [US5] Publish the first honest write-conformance number, however low, in
      `research.md`
- [ ] T044 [US5] Update `docs/KNOWN_ISSUES.md`: restate 75.2% as result-shape conformance and
      give the write number beside it (FR-014, SC-007)
- [ ] T045 [US5] Update `CHANGELOG.md` with both numbers and a note that the previously
      published figure measured result shape only
- [x] T046 [US5] Run `markdownlint-cli2 --fix` and `prettier --write` on every `.md` touched

**Gate**: SC-005 and SC-007 hold.

Status 2026-09-26: superseded in part. Instead of two verdicts per scenario, one
run records a capture (`IVG_TCK_CAPTURE`), and `scripts/tck/rescore.py` scores
it offline in `default`, `typed` or `lenient` mode. The strict number (3896/3896
at `937d4b7`) is in `CHANGELOG.md` v4.1.0 and `docs/TCK.md`. The v2.6.0 75.2%
line is now labelled as lenient-harness. T041–T045 are left open because their
exact wording is not met.

---

## Dependencies

- Phase 1 blocks Phase 2 (T003 settles the mapping T008 implements).
- Phase 2 blocks Phase 4 (T008 is what T021/T022 wire in).
- Phase 3 blocks Phase 4 (plan.md: errors must be reported before counts are compared).
- Phase 4 blocks Phase 6 (a scenario added to a harness that cannot fail it proves nothing).
- Phase 5 is independent of Phases 4 and 6 and may run in parallel with either.
- Phase 7's mechanism (T041–T042) may be pulled forward into Phase 4 if the first honest run
  is otherwise illegible (plan.md risk 3).

## Parallel opportunities

- T005–T007 are one file each of independent assertions.
- T010–T013 likewise.
- T017–T020 likewise.
- T026–T029 likewise.
- T035–T038 are four independent feature files.
- Phase 5 in full, alongside Phase 4 or 6.

## MVP

Phases 1–4. At that point the harness can fail on a write defect, which is the entire reason
this spec exists. Phases 5–7 make the failures legible and the reported number honest.
