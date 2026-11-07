<!-- markdownlint-disable MD013 -->

# IVG 4.6.0 release plan

**Date**: 2026-11-03 · **Decided by**: Tom, `/grill-with-docs` session 2026-11-03 (memory `project_release_460_plan`)
**Branch**: `release-4.6.0`, created from `262-ivg-owned-sql-schema` at `25ad7281`; every workstream lands there.
**Ships when**: every task in `tasks.md` here and in `specs/262-ivg-owned-sql-schema/tasks.md` is done, the gates in
workstream F pass on the final commit, and Tom says "push it" / "ship it". Nothing ships partially.

## What 4.6.0 contains

| Workstream | Scope | Where it is planned |
| --- | --- | --- |
| Already on the branch | `read_only` security fix (`060c3f59`, private report); global PageRank, WCC, CDLP, the Arno handle, `idx_edges_graph_oid`; retire-plan test fixes | CHANGELOG 4.5.2 section (retitled 4.6.0 in R001) |
| A. Six walkthrough fixes | `shortestPath` projection, `NKGAccelLoader.Load`, the "Acceleration" label, `kg_edge_count`, setup noise and the namespace warning, the IPM/ZPM claim | `tasks.md` workstream A |
| B. Spec 262 | One IVG Schema per namespace; `SQLUser`/`User` footprint gone; benchmark and `User.Exec` classes out of the wheel | `specs/262-ivg-owned-sql-schema/tasks.md` (68 tasks) |
| C. Docs restructure | Two audiences; ADMIN_GUIDE the operator doc (OPERATIONS folded in); generated API reference; contributor material to `CONTRIBUTING.md` + `docs/dev/`; scratch deleted; KNOWN_ISSUES split | `tasks.md` workstream C |
| D. Docs tests, layers 1–2 | Every doc code block runs and its shown answer is checked; every link, name, port and command agrees with the code | `tasks.md` workstream D |
| E. Docs tests, layer 3 | Two fresh newcomer walkthroughs (IRIS expert, Python developer) on the built wheel; no "blocker" or "wrong" item | `tasks.md` workstream E |
| F. Release | Version, CHANGELOG, sign-off, gates, publish on Tom's word, reporter reply, opsreview message | `tasks.md` workstream F |

## Order

```text
R001 branch + version ─┬─ A (fixes, small, independent) ──────────────┐
                       ├─ B (spec 262) ───────────────┐               │
                       │                              ├─ C (docs) ─ D ├─ E ─ F
                       └─ D's harness (R040–R046) ────┘               │
```

- A first: small, test-first, and C documents the fixed behaviour.
- B before C's inventory and footprint sections (C writes what B leaves in a namespace).
- D's harness can be built while B runs, against today's docs, which also measures how much of today's docs runs.
- E needs the built wheel with A, B, C and D in it. F needs E.

## Decisions that shape the tasks (all made 2026-11-03)

- `kg_edge_count` counts edges (the ADMIN_GUIDE promise), not sources.
- The status label reads `Acceleration: ObjectScript (Arno not loaded)` or `Acceleration: Arno <version>`.
- Setup prints one INFO summary (namespace, IVG Schema, classes deployed, skipped with reasons); optional pieces at INFO
  once; WARNING only for something an operator must act on. The namespace comes from the connection, so "Namespace
  mismatch … expected 'USER'" goes.
- IPM/ZPM: the README claim, `module.xml` and `module-core.xml` are removed (the manifests list three classes that no
  longer exist and version 2.0.0).
- Docs audiences: Python application developer; IRIS operator. Contributor material is not in their path.
- API reference generated from docstrings with a freshness test; `docs/python/PYTHON_SDK.md` deleted.
- KNOWN_ISSUES: a short list of issues verified open on the 4.6.0 build; the rest verbatim in
  `docs/dev/notes/known-issues-history.md`.
- One container recipe: `docker-compose.yml` with `${IVG_IRIS_PORT:-1972}` / `${IVG_WEB_PORT:-52773}` and no fixed
  `container_name`; every doc uses it.
- (2026-11-04) `/browser` stays behind `IVG_API_KEY` like every other path (R097 as built; ADMIN_API says so).
- (2026-11-04) Scope: 4.6.0 fixes everything found before release (FIX, SECURITY, PERF, DEV, DOC alike); nothing found
  during 4.6.0 is deferred to 4.6.1.
- (2026-11-05) R278's read-path grant includes `CY_VLP_*`: Tom kept them in after R280 made each check SELECT
  on `rdf_edges` (f101b60f), so a SELECT holder's variable-length Cypher works after the upgrade.
- (2026-11-05) Scope freeze at R303 (Tom), replacing the 2026-11-04 "nothing deferred" rule: R300-R303 finish;
  anything found later goes to KNOWN_ISSUES and a 4.6.1 list unless it is security, data loss, a wrong answer in a
  doc example, or a walkthrough blocker or wrong item.
- (2026-11-06) Walkthroughs end after round 3 (Tom): round 2's items are fixed, round 3 runs; a "wrong" round 3
  finds is fixed if it is a doc error, and otherwise goes to KNOWN_ISSUES (with a workaround) and the 4.6.1 list unless
  it is security or data loss. Then the gates and the release.
- (2026-11-06, later) Freeze lifted (Tom: "I can't stand this sub-par quality when I thought it was 5-star based on
  unit tests and TCK"). Replaces the R303 freeze and the round-3 cutoff: every defect found is fixed in 4.6.0; the
  4.6.1 list is back in scope; R306-R309 build the oracles and gate checks that should have caught them.
- (2026-11-06) The Cypher-literal SQL injection (4.0.0-4.5.1, fixed 5b03b8a8) is disclosed with 4.6.0 (Tom): a private
  GitHub security advisory and a heads-up to opsreview, both drafted for Tom to send when 4.6.0 publishes, plus the
  CHANGELOG Security entry. Nothing is sent without his word.
- (2026-11-06, evening) Result lists and maps are Python values in 4.6.0 (Tom): execute_cypher, Bolt and REST answer
  real lists and dicts, nested ones included, instead of the documented JSON text. A breaking change: CHANGELOG "Changed
  (breaking)" with the migration (drop `json.loads` on result values), docs and doc tests updated, and a line in the
  opsreview heads-up (fixes67). Stored property strings that spell a list or map stay KNOWN_ISSUES: data on disk keeps
  its stored form, so no storage marker in 4.6.0.
- (2026-11-06, late) Scope cut (Tom, "this stuff goes really slowly"): the four branches in flight finish
  (460-cmptypes, 460-fixes67, 460-fixes68, 460-sec-translator). After them a new finding goes into 4.6.0 only if it is
  security, data loss, or a wrong answer in a basic query; anything else goes to KNOWN_ISSUES (with a workaround) and the
  4.6.1 list. Merges are batched, with one shared gate per batch.
- (2026-11-06, late) Speed gate exemptions (Tom): `collect_node_property` (+~50%, R262's typed `collect()`) and
  uncached `pagerank` (+~37 ms at 20k nodes, R225/R306's edgeless-node scores) are allowed, with the measured cost and
  reason printed in the gate report and the CHANGELOG; an allowed path more than twice its documented cost still fails.
  Every other path meets FR-020a's 5%. 4.6.1: a typed aggregate for `collect()` and a fused loner pass in PageRank.
- (2026-11-07) Cached PageRank allowed too (Tom): R313's node-set token costs +1.3 ms per cached call (3.1-3.3 ms
  against 1.7-1.9 ms; it was 47-59 ms before R313); a cheaper token without a nodes delete hook missed Cypher `DETACH
  DELETE` (a wrong answer) and is parked on 460-speedgate-r313-token. 4.6.1: a delete hook on `nodes` for the token.
- (2026-11-07) `node_write_bulk` allowed too (Tom: "Allow it, R318 to 4.6.1"): R089's stored `id` property costs about
  +45-60% (3.0 s against 2.1 s for 10,000 nodes on dpgenai1, +61% on AWS); R318 (no existence SELECT for a node the
  batch creates, -8%, on 460-bulkperf a05d52e8) waits for 4.6.1 so the write path does not change at freeze.
- (2026-11-07) Walkthrough gate closed by a targeted round 5 (Tom: "Targeted recheck, fresh agents"): round 4 ran on
  `b5cf1ac4` (expert 0/0, python 0 blocker / 2 wrong, both fixed after it); two fresh agents re-run round 4's wrong and
  regrade items, every doc section changed since `b5cf1ac4`, and the two new behaviours (R319, fixes84) on the final
  wheel; the gate passes if both report 0 blocker and 0 wrong.

## Doc-test conventions (layer 1, workstream D)

- A fenced block runs unless it is preceded by `<!-- doc-test: skip reason="…" -->`; the harness counts skips by reason
  and fails on an unmarked block that cannot run.
- `python` blocks run in order in one interpreter per document, against a fresh namespace (`IVG460DOC`), with the
  connection the document's setup block makes (the harness supplies host and port through `IVG_IRIS_PORT`). The
  harness resets to the baseline taken when the namespace was built, so the release gate builds a new one from the
  final commit (`make_scratch_namespace.py IVG460DCn`, `IVG_DOCTEST_NAMESPACE=IVG460DCn`); on 2026-11-05 the old
  IVG460DOC baseline failed ADMIN_GUIDE and iris-sql-quirks blocks that pass in a new one.
- An expected answer is written in the doc beside the code: `# → [['Bob']]` on the line after an expression, or a
  following block fenced as `text` and preceded by `<!-- doc-test: output -->`. The harness compares; the doc author
  writes the value by hand, never from IVG's output.
- `sql` blocks run through the DB-API cursor; `objectscript` and terminal blocks (`USER>` prompts) run through
  `docker exec <container> iris session IRIS -U <NS>` with the prompts stripped and the output compared.
- `bash` blocks are checked, not run, unless marked `<!-- doc-test: run -->` (installs and `docker compose` run in E).

## Gates for 4.6.0 (workstream F)

Unit suite; full E2E, in two passes (the shared namespaces, then the 46 files that run only when `IVG_NAMESPACE`
names an `IVG460*` scratch namespace, with `IVG_DRY_RUN_NS`, `IVG_SPLIT_NAMESPACE` and `IVG_NS_IVG460FX299A/B` set;
they skip in the first pass); `scripts/ci-parity.sh`; `scripts/quickstart_e2e.py --isolated`; the reader gate; the full TCK with setup errors failing the scenario, on enterprise 2026.3 and Community 2026.1 under both drivers
(0 regressions); spec 262's speed gate; the inventory test; doc tests layers 1 and 2; both walkthroughs with no
"blocker" or "wrong" item; `markdownlint-cli2` and `prettier --check` over every changed `.md`.
