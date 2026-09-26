# Implementation Plan: TCK Write Verification

**Branch**: `229-tck-write-verification` | **Date**: 2026-09-20 | **Spec**:
[spec.md](./spec.md)

## Summary

Make the TCK harness's write assertions capable of failing. Three step definitions in
`tests/tck/steps/results.py` currently cannot fail — two side-effect steps that `pass`
unconditionally, and `the result should be empty`, which returns early when the query raised.
Together they cover 1424 corpus assertions and they are why two translator write defects
shipped through a 3897-scenario suite. Replace them with per-query row-count deltas, split
the reported figure into result conformance and write conformance, and add an IVG-owned
supplementary corpus for the write shapes the upstream TCK omits.

The deliverable is an honest number, not a high one. Expect the write-conformance figure to
land well below 75.2% on the first run.

## Technical Context

**Language/Version**: Python 3.10+ (3.11 in `.venv`, 3.13 verified)
**Primary Dependencies**: `behave` (Gherkin runner, already in the tree), `pytest>=7.4.0`,
`intersystems-irispython` (DB-API); no new runtime dependency
**Storage**: reads only — `Graph_KG.nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`,
`rdf_reifications`, and `^KG` adjacency for leak detection. No schema change.
**Testing**: `pytest` for the harness's own unit tests; `behave` for the corpus
**Target Platform**: `ivg-iris-enterprise` (port 31972). The community container's
`MaxServerConn=1` cannot host a second connection if counting ever needs one.
**Project Type**: test infrastructure — `tests/tck/` only, plus documentation
**Performance Goals**: five `COUNT(*)` reads per query step. At ~3897 scenarios the added
cost must stay under roughly 2× the current wall clock; if it does not, batch the five counts
into one statement before reaching for anything cleverer.
**Constraints**: the upstream feature tree stays a faithful copy — every IVG-specific
scenario goes in a separate directory so the corpus can be refreshed from upstream.
**Scale/Scope**: 1615 scenario definitions expanding to 3897 scenarios; 1424 currently-vacuous
assertions; 129 existing deferral entries.

## Constitution Check

| Principle                            | Status | Note                                                                                                                                                                          |
| ------------------------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| I — Tests before implementation      | PASS   | Every phase below writes the harness's own tests first. A step definition that cannot fail is the defect being fixed, so the test that proves it can fail is the deliverable. |
| II — Measure, don't infer            | PASS   | Every claim in the spec was measured in the tree (step bodies, corpus grep counts). Phase gates re-measure by reverting the 4.0.0 fixes.                                      |
| III — Graph scope is explicit        | N/A    | No graph-scoped read or write is added.                                                                                                                                       |
| IV — No silent failure               | PASS   | This is the principle the feature enforces: FR-004 makes an unmapped side-effect column fail rather than pass.                                                                |
| V — Honest reporting                 | PASS   | FR-010/FR-014/SC-005 require publishing the low number.                                                                                                                       |
| VI — Deviations recorded, not hidden | PASS   | FR-015 puts the two known IVG deviations in the corpus as deviations.                                                                                                         |

No violations. No complexity tracking entries.

## Design

### Where the seam goes

Counting belongs behind one interface, called by the step definitions:

```python
class SideEffects:
    """Row deltas across one query, per TCK side-effect column."""

    @classmethod
    def capture(cls, conn) -> "SideEffects": ...   # absolute snapshot
    def delta_to(self, later: "SideEffects") -> dict[str, int]: ...
    def unmapped(self, columns: Iterable[str]) -> list[str]: ...
```

Rationale: the step definitions stay three lines each, the mapping from TCK column names
(`+nodes`, `-relationships`, `+properties`, `+labels`) to IVG tables lives in one place, and
the harness's own unit tests exercise the mapping without a container by feeding it two
snapshots. One adapter today (IRIS via DB-API); the seam exists because the _mapping_ is the
part that needs testing, not the counting.

### Counting is a delta, never a total

`_inject_label` scopes reads to a per-scenario label, but writes land in shared tables. An
absolute total is polluted by any other connection and by leftover probe rows. Snapshot
before the query, snapshot after, subtract. This also handles the `-nodes` / `-relationships`
columns for free.

The snapshot shares the scenario's own connection (`context.engine.conn`) so it sees
uncommitted writes — a second connection would see nothing until commit and every scenario
would report zero.

### Ordering: errors before counts

US2 must fire before US1's comparison. If a query raised and rolled back, the delta is zero,
which is indistinguishable from "wrote nothing correctly". So the error check runs first and
reports the exception; only a non-raising query reaches the count comparison.

### The column mappings

| TCK column       | IVG measurement                                                                   | Confidence                                                                                                                     |
| ---------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `+nodes`         | `Graph_KG.nodes` delta                                                            | direct                                                                                                                         |
| `-nodes`         | negative `nodes` delta                                                            | direct                                                                                                                         |
| `+relationships` | `rdf_edges` delta                                                                 | direct                                                                                                                         |
| `-relationships` | negative `rdf_edges` delta                                                        | direct                                                                                                                         |
| `+properties`    | `rdf_props` delta                                                                 | holds while every property is one row; a map-valued property would break the identity — assert the assumption, don't assume it |
| `-properties`    | negative `rdf_props` delta                                                        | same                                                                                                                           |
| `+labels`        | set difference of `DISTINCT rdf_labels.label`, excluding `TCK_*` isolation labels | measured (research.md T002): the corpus counts label names new to the graph — `CREATE (:L), (:L)` is `+labels 1`               |
| `-labels`        | same, names no longer in use                                                      | measured, same                                                                                                                 |

`+labels`/`-labels` were the one place where a wrong mapping would manufacture failures.
Phase 1 settled them: the corpus uses them 79 times, so they are measured as
distinct-new-label-names rather than deferred (research.md T003). Properties are
`(entity, key, value)` triples, so an overwrite is `+1`/`-1`; relationship properties are
the keys of `rdf_edges.qualifiers`.

### Reporting two numbers

Each scenario ends with two booleans. `behave` reports one. Rather than fight the runner,
record the pair on the context and write a summary in `after_all` (`environment.py`), keeping
`behave`'s own pass/fail as the conjunction of the two so a write failure still fails the
scenario.

### The supplementary corpus

`tests/tck/features_ivg/` — a sibling of `features/`, never mixed into it. Two scenarios to
start, both derived from defects already fixed and already covered by unit and integration
tests; their job here is to make the _harness_ prove it can see them.

## Phases

### Phase 1 — Measure the mapping (no behaviour change)

Settle `+labels`/`-labels` by counting how the corpus uses every side-effect column, and
record the counts in the spec. Gate: the mapping table above has no "needs a decision" row.

### Phase 2 — `SideEffects`, tests first

Unit tests for the snapshot/delta/unmapped interface against two hand-built snapshots, no
container. Then the implementation. Gate: unit tests pass; the class is unused by any step.

### Phase 3 — Errors stop passing (US2)

Tests first: a raising query must fail `the result should be empty` and every result-table
step. Then change `results.py`. Gate: the new tests pass, and the full TCK run's scenario
count does not change (scenarios may flip to failing — that is the point — but none may
error out of the harness).

### Phase 4 — Side effects start counting (US1)

Tests first, including the decisive one: with `_create_match_gate` reverted in a scratch
checkout, a named upstream scenario fails. Then wire `SideEffects` into both steps. Gate:
SC-001.

### Phase 5 — Error kinds (US3)

Tests first for the `ERROR_TYPE_MAP` entries, including the `-23`-vs-`-119` distinction. Then
the mapping, then the deferral-reason requirement. Gate: SC-006 and the US3 acceptance
scenarios.

### Phase 6 — Supplementary corpus (US4)

Scenarios first — they must fail against reverted fixes. Gate: SC-002.

### Phase 7 — Report and document (US5)

Two-number summary, then `docs/KNOWN_ISSUES.md` and the release notes. Gate: SC-005, SC-007.

## Risks

- **The revert-and-prove gate needs a scratch checkout.** A `git stash` is blocked by the
  repo's guard hook, so Phase 4's and Phase 6's gates need a separate worktree or a manual
  edit-and-restore. Plan for the worktree; note that the three working-tree-only class
  deletions do not carry across.
- **Wall clock.** Five counts per query step across 3897 scenarios. If the run doubles,
  collapse the five into one `UNION ALL` statement before considering sampling — sampling
  would reintroduce exactly the blind spot this feature removes.
- **A flood of new failures.** Expected and desired, but it must not bury the signal. Phase 4
  lands with the two-number report (Phase 7's mechanism, pulled forward if needed) so the
  first honest run is legible.
- **`^KG` leak detection could be noisy.** A temporal edge deliberately keeps a structural
  shadow. Detect only _unexpected_ adjacency writes, and keep the adjacency check out of the
  phase gates until it is quiet.
