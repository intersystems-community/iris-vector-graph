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
they skip in the first pass); `scripts/ci-parity.sh`; `scripts/quickstart_e2e.py --isolated`; the reader gate; the full TCK
(0 regressions); spec 262's speed gate; the inventory test; doc tests layers 1 and 2; both walkthroughs with no
"blocker" or "wrong" item; `markdownlint-cli2` and `prettier --check` over every changed `.md`.
