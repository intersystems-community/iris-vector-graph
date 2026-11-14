<!-- markdownlint-disable MD013 -->

# IVG 4.7.1 plan (hotfix)

Tom, 2026-11-14: "yes on 4.7.1", for the four findings of the 4.7.0 code review that were confirmed or checked
(review scripts in `~/.cache/ivg-bench/rel-470/review/`). Branch `release-4.7.1` from `81a02bd9` (4.7.0's tag
`fabf4fae` plus the checklist's release-notes links). Scope is these four; the rest of the review goes to the
4.7.1 queue in `specs/release-4.7.0` (unchanged by this hotfix).

| # | Finding | Known answer | Worker |
| - | ------- | ------------ | ------ |
| H1 | `MERGE (n:M {v: 6})` with the string `'6'` stored answers no rows and writes nothing (existence check keeps 4.6.5's spelling rule through alias `_mp…`; the binding join uses row 24's typed rule) | a new node `{v: 6}`, one row; a second identical MERGE answers that node and writes nothing; stored number 6 still merges onto itself | w471-merge, IVG470H1 |
| H2 | `kg_PERSONALIZED_PAGERANK(['a'], return_top_k=2000)` on a star `a -> 3000 leaves` gives 999 leaves their score and 1000 leaves 0.0 (`_ppr_every_node` pads past the route's 1000-row cap); `fhir_bridge.unified_clinical_pipeline` counts the 0.0 rows | `a` plus 1999 leaves, each with the leaf score networkx gives; 0.0 only for a node the walk never reaches | w471-ppr, IVG470H2 |
| H3 | With the driver's autocommit off, every migration's `commit_step` refuses the migration's own open writes (`CallerTransactionOpenError`, "Nothing was done") | autocommit off, nothing written by the caller: the migration completes and its writes are committed; a caller's open write before the call: refused at entry, caller's row still uncommitted | w471-mig, IVG470H3 |
| H4 | CHANGELOG 4.7.0 says "A SET whose value is null removes the property"; a literal `SET n.p = null` leaves `p` in `keys(n)` (KNOWN_ISSUES is right) | the 4.7.0 line says computed nulls; 4.7.1 notes the correction | lead |

## Gates (on the release commit)

- Unit: `scripts/ci-parity.sh` exit 0 (Studio).
- E2E: the full suite on the Studio, as at 4.7.0's final commit (H3 touches every migration), with IVG470OLD rebuilt from 4.6.5 first.
- TCK enterprise 2026.3 and Community 2026.1, drivers 5.4.0 and 5.4.1: 3896 / 3896 each (translator changed).
- Quickstart 5 / 5.
- Each H case run live at the release commit: the known answer.
- Sign-off in PRE_RELEASE_CHECKLIST.md; publish only on Tom's "push it".
