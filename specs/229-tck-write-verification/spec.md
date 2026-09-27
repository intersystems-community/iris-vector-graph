# Feature Specification: TCK Write Verification

**Feature Branch**: `229-tck-write-verification`
**Created**: 2026-09-20
**Status**: Partially implemented (2026-09-26). US1–US3 done; US4
(`features_ivg/`) not started; US5 superseded by capture/rescore.
**Input**: The openCypher TCK harness reports 2930/3897 scenarios passing (75.2%) and did not
catch either translator write defect fixed in 4.0.0. Investigation shows why: the harness's
entire write-verification surface is a no-op, and a raising query is scored as an empty
result.

## Why this exists

Two defects shipped through a 3897-scenario conformance suite:

1. `MERGE (a:P {id:'x'})-[:LIKES]->(b:P {id:'y'})` could not be prepared in any graph
   (`SQLCODE -23 Label 'N0' is not listed among the applicable tables`).
2. `MATCH (a:P {id:'missing'}) CREATE (a)-[:R]->(b:P {id:'new'})` wrote the node, its label,
   and its properties with no edge and no error.

Neither is exotic. Both were found by hand-reading the translator, months after a TCK harness
was built to find exactly this class of bug. The harness is not wrong about what it measures —
it is wrong about what it claims to measure. 75.2% is a **result-shape** conformance number.
It says nothing about whether a write wrote the right rows, and nothing about whether a write
that should have been refused was refused.

Three independent false-pass channels, each measured in the current tree:

**(a) Side-effect assertions do nothing.** `tests/tck/steps/results.py:69-76`:

```python
@then("no side effects")
def step_no_side_effects(context):
    pass  # IVG doesn't expose side-effect counters; pass if no crash

@then("the side effects should be:")
def step_side_effects_should_be(context):
    pass  # Side-effect counting not implemented; pass silently
```

The corpus contains **1180** `no side effects` steps and **244** `the side effects should be:`
tables — 1424 assertions that cannot fail. This is the only place the TCK ever checks what a
write wrote, and defect 2 above is purely a side-effect defect: the result set is empty
whether or not a stray node landed.

**(b) An error is scored as an empty result.** `results.py:54-58`:

```python
@then("the result should be empty")
def step_result_should_be_empty(context):
    result = context.last_result
    # An error counts as empty result for this check
    if context.last_error is not None:
        return
```

`query.py:32-34` swallows every exception into `context.last_error`. So the canonical write
scenario shape —

```gherkin
When executing query: MATCH (x:X), (y:Y) CREATE (x)-[:R]->(y)
Then the result should be empty
And the side effects should be: | +relationships | 1 |
```

— passes on a hard prepare failure. `features/clauses/create/Create2.feature` alone has seven
MATCH-then-CREATE scenarios in exactly that shape. Defect 1 raised at prepare time and would
have been invisible to every one of them.

**(c) Error-expecting scenarios accept any exception.** `results.py:17-27` maps
`SemanticError`, `ProcedureError` and `ConstraintVerificationFailed` to bare `(Exception,)`,
and `step_error_type_raised` returns early if a result carries any non-empty `error` string.
A scenario expecting a semantic rejection passes on an unrelated `SQLCODE -23`, so a
regression that breaks a statement's _shape_ is indistinguishable from correct behaviour.

A fourth, narrower fact worth recording: the broken MERGE shape is **not in the corpus at
all**. Searched all 12 feature files containing `MERGE`; scenarios matching
`MERGE (<var> {props})-[` number **zero**. Every TCK relationship-MERGE is
`MERGE (a)-[r:TYPE]->(b)` over `MATCH`-bound variables — the form that always worked. The
v2.6.0 release notes credit "`MERGE` relationship idempotency: `WHERE NOT EXISTS` check
before INSERT" as a TCK-driven fix; that is the code that introduced the `-23`. The TCK
motivated a change and then could not see the shape it broke. Closing (a)–(c) does not close
this; only a supplementary corpus does.

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Side effects are counted, so a write defect fails a scenario (Priority: P1)

A maintainer runs the TCK. A scenario declaring `| +nodes | 1 |` fails when the engine wrote
two nodes, zero nodes, or one node plus a label row the scenario did not ask for. A scenario
declaring `no side effects` fails when any row landed anywhere.

**Why this priority**: it is the whole point of the feature. 1424 vacuous assertions become
live, and the two defects already fixed in 4.0.0 become regression-proof.

**Independent Test**: revert `_create_match_gate` in a scratch checkout, run the
`clauses/create` features, and observe scenarios failing that pass today.

**Acceptance Scenarios**:

1. **Given** a scenario with `And the side effects should be: | +nodes | 1 |`, **When** the
   engine creates one node, **Then** the scenario passes.
2. **Given** the same scenario, **When** the engine creates two nodes, **Then** it fails and
   the message names the observed and expected counts.
3. **Given** a scenario with `Then no side effects`, **When** the engine writes any row to
   `nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`, `rdf_reifications` or `^KG`, **Then** it
   fails and names the table.
4. **Given** a zero-row `MATCH` followed by a `CREATE`, **When** the engine writes an inline
   node anyway, **Then** the scenario fails — this is defect 2, caught by the harness.

### User Story 2 - A raising query fails its scenario (Priority: P1)

A maintainer runs the TCK. A query that raises where the scenario expected a result — empty
or otherwise — fails, and the message carries the SQLCODE.

**Why this priority**: same rank as US1 because `the result should be empty` is the most
common assertion on write scenarios, and its current early-return is what hid defect 1.
Without this, US1's counting still can't see a prepare failure: nothing was written, so the
counts match "no side effects".

**Independent Test**: revert `_merge_literal_node_id`, run `clauses/merge`, and observe that
the harness now reports the `-23` instead of scoring it as a pass. (Requires a corpus
scenario in the failing shape — see US4.)

**Acceptance Scenarios**:

1. **Given** a scenario ending `Then the result should be empty`, **When** the query raises,
   **Then** the scenario fails and the message includes the exception text.
2. **Given** a scenario ending `Then the result should be empty`, **When** the query returns
   zero rows, **Then** it passes.
3. **Given** a scenario that expects an error at any time, **When** the query raises, **Then**
   it still passes — this path must not become stricter than the corpus.

### User Story 3 - An expected error is matched by kind, not merely by existence (Priority: P2)

A scenario expecting `ConstraintVerificationFailed` fails when the engine raises an unrelated
translator or prepare error.

**Why this priority**: lower than US1/US2 because it converts silent passes into honest
failures rather than revealing unknown data loss, and because the mapping work is the larger
share of the effort. Independently valuable: it is the channel that would let a future
translator regression hide inside an error-expecting scenario.

**Independent Test**: point one such scenario at a query that raises `SQLCODE -23` and confirm
the scenario fails.

**Acceptance Scenarios**:

1. **Given** a scenario expecting `ConstraintVerificationFailed`, **When** the engine raises
   an IRIS `-119`/`-121` integrity error, **Then** it passes.
2. **Given** the same scenario, **When** the engine raises a prepare-time `-23`, **Then** it
   fails and the message distinguishes the two.
3. **Given** a scenario whose expected error kind IVG genuinely cannot distinguish, **When**
   the suite runs, **Then** it is listed in the deferral file with a stated reason rather than
   passing on `(Exception,)`.

### User Story 4 - The corpus covers the shapes IVG's translator actually generates (Priority: P2)

A supplementary feature directory, owned by this repo and clearly marked as non-TCK, holds
scenarios for write shapes the upstream corpus omits — starting with inline-property nodes in
a relationship `MERGE`.

**Why this priority**: US1–US3 fix the _scoring_; only this fixes the _coverage_. Kept
separate from P1 because it depends on US1/US2 being real first: adding scenarios to a harness
that cannot fail them changes nothing.

**Independent Test**: the new scenarios must fail against a checkout with the two 4.0.0
translator fixes reverted, and pass with them.

**Acceptance Scenarios**:

1. **Given** `MERGE (a:P {id:'x'})-[:R]->(b:P {id:'y'})` run twice, **Then** the side effects
   are one node pair and one relationship on the first run and nothing on the second.
2. **Given** a `MATCH` binding N rows followed by a `CREATE` of one inline node, **Then** the
   scenario records the count IVG actually produces and is marked as a known deviation from
   openCypher, not silently expected to be N.
3. **Given** the supplementary directory, **When** the suite reports its pass rate, **Then**
   supplementary and upstream scenarios are counted separately.

### User Story 5 - The reported number says what it measures (Priority: P3)

The pass rate is reported as two numbers — result conformance and write conformance — so no
reader can mistake one for the other.

**Why this priority**: documentation follows behaviour; worthless before US1.

**Independent Test**: run the suite and confirm both numbers appear, with the count of
deferred scenarios.

**Acceptance Scenarios**:

1. **Given** a completed run, **Then** the summary reports scenarios passing result assertions
   and scenarios passing write assertions as separate counts.
2. **Given** the 4.0.0 release notes, **Then** the TCK figure is restated with both numbers
   and the previous single figure is marked as result-shape only.

### Edge Cases

- **A scenario that writes and then reads.** Counting must happen at the point the scenario
  asserts, not at scenario teardown, or a later read clause changes the observed totals.
- **Scenario isolation.** `_inject_label` scopes reads to a per-scenario label, but writes go
  to shared tables. A count taken as a global table total is polluted by any other connection.
  Counting must be a delta across the query, not an absolute.
- **`^KG` and `^NKG`.** A `CREATE` writes adjacency globals as well as SQL rows. A side-effect
  count that reads only SQL will miss an adjacency-only leak; one that reads both must
  tolerate the documented cases where a temporal edge deliberately has a structural shadow.
- **Deleted rows.** TCK side-effect tables use `-nodes`, `-relationships`, `-properties`,
  `-labels`. A delta-based count handles these for free; an insert-only counter does not.
- **Properties are not rows.** TCK counts `+properties` per property, IVG stores one
  `rdf_props` row per property — these agree today, but a future map-valued property would
  break the identity, and the mapping has to be stated rather than assumed.
- **Labels.** TCK's `+labels` counts _distinct label names newly introduced to the graph_, not
  label assignments. IVG has no such concept; this mapping must be decided explicitly or the
  column deferred with a reason.
- **A query that raises mid-transaction.** If the harness counts a delta and the engine rolled
  back, the delta is zero while the scenario expected writes. US2 must fire first, so the
  failure is reported as the error and not as a count mismatch.
- **Scenarios already deferred.** `wip.txt` holds 129 entries. Making assertions real will
  move scenarios into failure; the deferral list must grow deliberately, with a reason per
  entry, not by bulk append.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: The harness MUST count rows in `Graph_KG.nodes`, `rdf_labels`, `rdf_props`,
  `rdf_edges` and `rdf_reifications` immediately before and immediately after each
  `When executing query` step, and expose the deltas to the side-effect steps.
- **FR-002**: `Then the side effects should be:` MUST compare every column in the scenario's
  table against the measured delta and fail on any mismatch, naming the column, the expected
  value and the observed value.
- **FR-003**: `Then no side effects` MUST fail when any measured delta is non-zero.
- **FR-004**: A side-effect column the harness cannot yet map to a measurable delta MUST fail
  the scenario with an explicit "unmapped side-effect column" message, or route the scenario
  to the deferral list. It MUST NOT pass silently.
- **FR-005**: `Then the result should be empty` MUST fail when the query raised, reporting the
  exception type and message. It MUST keep passing on a genuinely empty result set.
- **FR-006**: Every result-table assertion MUST fail on a raised exception rather than compare
  against an absent result.
- **FR-007**: `ERROR_TYPE_MAP` MUST map each TCK error kind the corpus uses to the IVG/IRIS
  error surface that represents it, including SQLCODE ranges where the distinction is at the
  SQL layer. Kinds that genuinely cannot be distinguished MUST be listed with a reason.
- **FR-008**: An error-expecting step MUST NOT pass on a result object carrying an arbitrary
  non-empty `error` string without checking its kind.
- **FR-009**: Side-effect counting MUST be a delta measured per query, not an absolute table
  total, so a concurrently-used namespace cannot make a scenario pass or fail spuriously.
- **FR-010**: The harness MUST record whether each scenario's _result_ assertions and its
  _write_ assertions passed, and report the two counts separately.
- **FR-011**: The deferral file MUST carry a reason per entry, and the runner MUST reject an
  entry without one.
- **FR-012**: A supplementary, clearly non-upstream feature directory MUST hold scenarios for
  write shapes the upstream corpus omits, beginning with a relationship `MERGE` whose
  endpoints are inline-property nodes and a `CREATE` after a zero-row `MATCH`.
- **FR-013**: Supplementary scenarios MUST be counted and reported separately from upstream
  TCK scenarios, so the conformance figure stays a conformance figure.
- **FR-014**: Documentation quoting a TCK pass rate MUST state which of the two numbers it is.
  The existing 75.2% MUST be restated as result-shape conformance.
- **FR-015**: The known IVG deviations already recorded in `docs/KNOWN_ISSUES.md` — one node
  per `MATCH` row set rather than N, and `SQLCODE -119` on a re-`CREATE`d identical edge —
  MUST be represented in the corpus as recorded deviations with their measured behaviour, not
  as passing openCypher conformance.

### Key Entities

- **Side-effect delta**: per-query row counts for the five SQL tables, plus any adjacency
  globals in scope, taken as before/after pairs.
- **Scenario verdict**: two independent booleans — result assertions passed, write assertions
  passed — replacing today's single pass/fail.
- **Deferral entry**: a scenario identifier plus a required reason.
- **Supplementary corpus**: IVG-owned feature files, disjoint from the upstream TCK tree and
  counted apart from it.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: With `_create_match_gate` reverted, at least one upstream TCK scenario fails.
  (It cannot today — the assertion that would catch it is a no-op.)
- **SC-002**: With `_merge_literal_node_id` reverted, at least one supplementary scenario
  fails with the `-23` reported, not absorbed.
- **SC-003**: Zero side-effect steps in the harness are unconditional passes. Measured by
  grepping the step definitions; today the count is 2, covering 1424 corpus assertions.
- **SC-004**: `the result should be empty` no longer returns early on `last_error`. Measured
  by reading `results.py`; a test asserts a raising query fails that step.
- **SC-005**: The suite reports a result-conformance count and a write-conformance count. The
  write number is published even if it is far below 75.2% — an honest low number is the
  deliverable, not a high one.
- **SC-006**: Every deferral entry has a reason. Measured by the runner refusing to start
  otherwise.
- **SC-007**: `docs/KNOWN_ISSUES.md` and the release notes state which number 75.2% was.

## Out of scope

- Raising the pass rate. This feature will almost certainly lower the number that gets
  reported, because it starts measuring something that was never measured. Fixing whatever it
  reveals is separate work, spec'd from the results.
- Making IVG's write semantics match openCypher where they currently don't (row multiplicity,
  parallel edges). Those are recorded deviations; this feature makes them visible, and
  FR-015 requires the corpus to state them honestly rather than expect conformance.
- Side-effect counting for `^KG`/`^NKG` beyond detecting an unexpected write. Full adjacency
  conformance is its own problem.
- Running the TCK in CI. Worth doing, but it needs a container and belongs with the container
  work, not here.

## Dependencies and assumptions

- Assumes the harness keeps a live connection it can query for counts. `context.engine` has
  `.conn`, so the counting can share the scenario's connection and see uncommitted writes.
- Assumes the enterprise container (`ivg-iris-enterprise`, port 31972) is the target. The
  community container's `MaxServerConn=1` cannot serve a counting connection alongside the
  query connection if they ever need to be separate.
- Assumes the upstream corpus is not edited. Every IVG-specific scenario goes in the
  supplementary directory, so the upstream tree stays a faithful copy and can be refreshed.
