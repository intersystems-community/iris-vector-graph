<!-- markdownlint-disable MD013 -->

# IVG 4.7.0 release plan

**Date**: 2026-11-10 · **Decided by**: Tom, 2026-11-10 (memory `project_release_460_plan`)
**Branch**: `release-4.7.0`, from main `bc851871` (4.6.5 and its record); every wave lands there.
**Source list**: `~/.cache/ivg-bench/rel-470/ranked.md` (55 open defects on 4.6.5, ranked 2026-11-10, with the
reproduction scripts beside it). Row numbers below are that table's.
**Ships when**: every task in `tasks.md` is done, the gates below pass on the final commit, and Tom says "push it".

## Scope

Tom's ranking: named reporters first, then silent wrong answers and silent data loss, then regressions, then loud
errors. Tom's cut ("both"): rows 1–29 and rows 33–35, 32 of the 55. Rows 30–32 (speed regressions Tom allowed in
4.6.0) and rows 36–55 wait.

| Group | Rows |
| --- | --- |
| Named reporters | 1 (#13, ArnolfJp), 2–5 (python-devx), 6 (opsreview), 7 (Mindwalk) |
| Silent wrong answers and silent data loss | 8–29 |
| Loud errors on common queries | 33, 34, 35 |

Row 6: a temporal purge retires the endpoints it leaves bare by default (Tom, 2026-11-10). Waiting on Tom: row 27 (whether
`CREATE` of an existing relationship adds a parallel one or raises). Row 7 waits on the reporter's rerun on 4.6.5.

## Rules

- **The list is frozen.** Anything found during 4.7.0 goes to 4.7.1, unless it is a silent wrong answer.
- **Test first, with a known answer** (each task names it), never taken from IVG's own output.
- **Waves of at most 3 agents** (teammate-worker), each in its own worktree and scratch namespace (`IVG470<X>`, from
  `scripts/dev/make_scratch_namespace.py`), on ivg-iris-enterprise:31972. Agents never merge, push or run a full suite.
- **The lead** reviews each diff, runs `/simplify` before the code commit, cherry-picks onto `release-4.7.0`, waits
  for the full unit run before the next merge, and runs the affected E2E after each wave.
- **Each fix updates its KNOWN_ISSUES entry and CHANGELOG** in the same commit.
- **The record counts opened against closed**, so "half of the open defects" is a number that can be checked.

## Waves

| Wave | Agent | Rows |
| --- | --- | --- |
| 1 | Algorithms | 8 ppr ranges, 9 ppr loners, 12 VecIndex cosine, 28 leiden topK |
| 1 | Caller's transaction | 3 bulk_load_session/embed_nodes/setup, 4 ledger, 13 bulk_create_nodes dict |
| 1 | Cypher | 10 properties(n) strings, 33 CALL…YIELD…WHERE, 34 exists beside a column, 35 SET toInteger |
| 2 | Writers | 11 DETACH DELETE reification, 14 executemany refused row, 16 plain idx_edges_adj, 17 create_edge lock |
| 2 | Relationships and time | 15 tz, 18 collected temporals, 19 collect(r), 20 nodes(p)/relationships(p) |
| 2 | Reporters' environment | 1 #13 and 5 class compile on Community 2026.1, 2 load slowdown, 29 reader status() |
| 3 | Value reading | 21 unwound booleans and '', 22 nested lists, 23 strings spelling values |
| 3 | Number filters | 24 |
| 3 | Setup and snapshots | 25 embedding width, 26 FHIR snapshot |
| — | After Tom's decisions | 6, 27 |

## Gates (on the final commit)

| Gate | Pass |
| --- | --- |
| Unit suite | 0 failed |
| Full E2E (dpgenai1, one run at a time) | no product failure |
| `tests/integration` (no gate ran it: 9 tests had been stale since 4.6.0, found 2026-11-10) | 0 failed |
| `scripts/ci-parity.sh` | exit 0 |
| TCK, enterprise 2026.3 and Community 2026.1, drivers 5.4.0 and 5.4.1 | 3896 / 3896 each (row 15 removes the one listed failure) |
| Quickstart (`quickstart_e2e.py --isolated`) | 5 / 5 |
| Speed, the paths touched (bulk load, ppr, PageRank, Cypher writes), paired with 4.6.5 after `TUNE TABLE` | no slowdown over 10% unexplained; row 2 within 10% of 4.4.1 or the gap explained |
| Newcomer walkthroughs (expert, Python developer) on the built wheel, with the round-10 rule sheet | 0 blocker, 0 wrong |
| Opened against closed | at least 28 of the 55 closed |
