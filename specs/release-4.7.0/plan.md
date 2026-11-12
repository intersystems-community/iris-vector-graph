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

## Deferred to 4.7.1

Found during 4.7.0 and not in its cut (the list is frozen).

- **A MATCH with many property equalities can take minutes to prepare** (4.6.5 and 4.7.0, docs/KNOWN_ISSUES.md): five
  or more `n.p = v` on one node do not prepare in 60 s on 2026.3. `test_stress_api::test_very_long_query_string`
  skips until then (tests/e2e/gate_skips.txt).
- **`kg_RRF_FUSE` answers `[]` when an index search fails** (4.6.5 and 4.7.0; silent wrong answer): its
  `except Exception: logger.error(...)` around the IVF / HNSW and BM25 legs turns any failure (a shape mismatch, a
  missing index, IRIS refusing the query) into an empty fusion, which reads as "nothing matched". It should raise
  naming the leg, as the centrality methods do since 4.6.0 R105 / R127.
- **`create_edge` and `store_node` check no widths** (4.6.5 and 4.7.0; loud): an over-wide id, predicate, label or key
  reaches IRIS and fails there (SQLCODE -400 or -104) instead of a `ValueError` before anything is sent, as
  `create_node` and the bulk writers do since 4.7.0. Found by the integration gate's audit (f2c6c2a6).
- **`RETURN r.p` of a relationship number past 64-bit integers reads an int** (4.6.5 and 4.7.0): `SET r.p = 1e20`
  returns 100000000000000000000 where UNWIND and `collect` read the float 1e+20 (d4ac7523).
- **A map's boolean in `CASE WHEN` reads as false** (4.6.5 and 4.7.0; silent wrong answer): `CASE WHEN input.fixed`
  over `UNWIND [{fixed: true, ...}] AS input` translates to `JSON_VALUE(..) = 'true'`, and `Graph_KG.JSON_VALUE` answers
  `'1'` for a JSON true, so the branch never runs (docs/KNOWN_ISSUES.md; strict xfail in
  tests/e2e/test_470_tck_unwind_shapes_e2e.py). The TCK's quantifier invariants compare the wrong value with itself.
- **A subscript of a collected boolean expression answers 1 / 0** (4.7.0, as 4.6.5): `collect(a OR b)[0]` and
  `head(...)` answer `0` for false; the list itself answers booleans (e521a35a, docs/KNOWN_ISSUES.md).
