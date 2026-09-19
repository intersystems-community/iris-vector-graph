<!-- markdownlint-disable MD013 -->

# IVG Pre-Release Checklist

Run this checklist before every merge to main, version bump, and PyPI publish.
Check items off as completed (`[x]`). Items marked **GATE** must pass before proceeding.

---

## 1. Branch & History

- [ ] Feature branch is up to date with main (`git fetch && git merge main --ff-only`)
- [ ] No unintended files staged (secrets, `.env`, large binaries)
- [ ] Commit messages are clean (no "WIP", no "fixup", no AI attribution)

---

## 2. Test Suite — **GATE**

Run against `ivg-iris-enterprise` (port 31972). **Do not use `ivg-iris` (community, port 21972) — MaxServerConn=1 causes spurious license failures under concurrent test processes.**

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest --tb=short -q
```

- [ ] Zero failures in `tests/unit/`
- [ ] Zero unexpected failures in `tests/integration/` (pre-existing skips documented in `KNOWN_ISSUES.md` are OK)
- [ ] Zero regressions vs. the prior release baseline (`tests/benchmarks/results/`)
- [ ] Arno/enterprise tests pass on `ivg-iris-enterprise` (port 31972) — `TestBFSArnoE2E` and related

---

## 3. Test Coverage — **GATE: ≥ 89%**

Coverage is measured across **unit + integration** together against `ivg-iris-enterprise`
(port 31972). Run in two phases with `--append` so the `.coverage` file accumulates both.
Never use the community container — see §2 note.

```bash
# Phase 1: unit tests
.venv/bin/python -m coverage run --source=iris_vector_graph \
    -m pytest tests/unit/ \
    --ignore=tests/unit/test_api_security.py \
    --ignore=tests/unit/test_bolt_relationship_encoding.py \
    --ignore=tests/unit/test_bolt_server.py \
    --ignore=tests/unit/test_fhir_event_sidecar.py \
    --ignore=tests/unit/test_module_coverage.py \
    --ignore=tests/unit/tck/ \
    -q -p no:warnings

# Phase 2: integration tests (appends to same .coverage file)
.venv/bin/python -m coverage run --append --source=iris_vector_graph \
    -m pytest tests/integration/ \
    -q -p no:warnings

.venv/bin/python -m coverage report --fail-under=89 --sort=cover
```

- [ ] Overall coverage ≥ 89% (`coverage report --fail-under=89`)
- [ ] No public-API module below 80%: `engine.py`, `_engine/query.py`, `_engine/nodes_edges.py`, `sdk.py`, `cypher_api.py`
- [ ] New code in this release has ≥ 90% coverage in its own test file

**Baseline (2026-09-03, unit+integration):** 89% combined — `gql/engine.py` (59%) and
`text_search.py` (79%) are the known gaps below 80%; Bolt/FastAPI modules excluded from
suite (missing deps). `cypher/algorithms/paths.py` 97%.

---

## 4. Performance Benchmarks — **GATE**

Run the full benchmark suite before merge. Compare p50 to the prior release baseline.
No regression > 10% p50 on any Q1–Q6 query at dataset M.

```bash
cd tests/benchmarks
# Community container
IRIS_PORT=21972 python bench.py --datasets S M --runs 20 --warmup 5

# Enterprise container (Arno acceleration)
IRIS_PORT=31972 python bench.py --datasets S M --runs 20 --warmup 5

# Neo4j comparison (if neo4j-ivg-bench container is running)
python benchmark_neo4j.py --uri bolt://localhost:7688 --user neo4j --password password
```

- [ ] Q1 (1-hop COUNT) p50 within 10% of baseline
- [ ] Q2 (2-hop BFS) p50 within 10% of baseline
- [ ] Q3 (3-hop BFS) p50 within 10% of baseline
- [ ] Q4 (4-hop BFS) p50 within 10% of baseline
- [ ] Q5 (shortest path) p50 within 10% of baseline
- [ ] Q6 (weighted shortest path) p50 within 10% of baseline
- [ ] Arno speedup ratios consistent with `docs/performance/BENCHMARKS.md`
- [ ] `BFSFastJsonDirect` p50 ≤ `BFSFastJson` p50 at hops 1–3 (Spec 193)
- [ ] NKG fast-path (`[*1..N]` Cypher) ≥ 2x speedup vs SQL path when `^NKG` populated (Spec 193)
- [ ] Results written to `tests/benchmarks/results/bench_<timestamp>.json`

---

## 5. Linting & Type Checks

```bash
ruff check .
```

- [ ] `ruff check .` — zero errors
- [ ] No new `type: ignore` comments added without justification

---

## 6. ObjectScript Compilation

- [ ] All `.cls` files in `iris_src/src/` compile cleanly on `ivg-iris-enterprise` (port 31972)
- [ ] Enterprise-only classes compile without errors if changed
- [ ] No compilation errors or warnings in `Graph.KG.*` namespace

---

## 7. KNOWN_ISSUES.md

- [ ] Any pre-existing test failures are documented in `KNOWN_ISSUES.md`
- [ ] No new unexpected failures are silently accepted

---

## 8. Version Bump (for PyPI publishes only)

- [ ] `pyproject.toml` version incremented (semver: patch for bugfix, minor for feature, major for breaking)
- [ ] `CHANGELOG.md` updated with release notes (if it exists)
- [ ] Git tag created: `git tag v<version>`

---

## 8b. Documentation Parity — **GATE** (blocks §9)

Every spec delivered since the last release MUST have corresponding user-facing documentation
before publishing. A PyPI release without docs leaves consumers on their own.

- [ ] `README.md` version string matches `pyproject.toml`
- [ ] Every new public API introduced since the last tag has a usage example in `README.md`
      or `docs/USER_GUIDE.md`
- [ ] `docs/demos/` has at least one runnable demo for any new major feature
- [ ] `grep -r "v[0-9]\+\.[0-9]\+" README.md` — no hardcoded version older than current

```bash
# Quick check — should print current version only
grep -E "v[0-9]+\.[0-9]+\.[0-9]+" README.md | grep -v "$(grep '^version' pyproject.toml | grep -o '[0-9.]*')"
```

---

## 9. PyPI Publish + GitHub Release (explicit permission required)

**Do not run without explicit "publish it" / "push to PyPI" instruction.**

```bash
python -m build
twine upload dist/*
git push && git push --tags
# `head -n -1` is GNU-only and fails on macOS BSD head; `sed '$d'` drops the
# trailing `---` portably.
gh release create v<version> \
  --title "v<version>" \
  --notes "$(awk '/^### v<version>/,/^---$/' CHANGELOG.md | sed '$d')"
```

- [ ] Explicit publish instruction received from Tom
- [ ] `dist/` contains only the intended release artifacts
- [ ] Test install from PyPI in a clean venv: `pip install iris-vector-graph==<version>`
- [ ] `git push && git push --tags` pushed to origin
- [ ] GitHub release created with CHANGELOG section as notes

---

## Sign-off

### 3.2.0 — embedding identity contract (2026-09-19)

Spec 226 plus the packaging fix that was prepared as `3.1.1` and never published, so
`3.1.1` has no tag and no PyPI release. Measured on `226-embedding-identity-contract`
against `ivg-iris-enterprise` (port 31972), `docker ps` confirmed healthy first. Status
words are ASCII for the reason given in the 3.1.1 row.

| Gate                     | Status | Notes                                                                                                                                         |
| ------------------------ | ------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Branch and history       | pass   | Clean linear history on the feature branch; `main` fast-forwarded, no merge commit                                                            |
| Unit tests               | pass   | 8777 passed / 0 failed / 20 skipped / 13 deselected. k-hop fast path passed this run — container `^NKG` state, still not a gate               |
| Integration tests        | known  | Run as chunks (five files segfault alone and give no result). Every failure was replayed at HEAD; see the paragraph below for the single diff |
| Arno (enterprise)        | known  | Unchanged from 3.1.0 — the `_detect_arno` probe failures are in the chunk totals and reproduce at HEAD                                        |
| Coverage >= 89%          | pass   | 91% (22907 statements, 2173 missed). Unit phase then integration chunks with `--append`; `--fail-under=89` exit 0                             |
| No benchmark regressions | waived | Same waiver as v2.17.0: `bench_utils.py` writes `SQLUser.*` (lines 11, 16, 22)                                                                |
| Lint clean               | known  | `ruff check .` = 2048 findings, byte-identical to the 3.1.0 count; the new modules contribute none                                            |
| ObjectScript compiles    | 55/56  | `tcp-deploy` compiled 55 classes clean; `User.PageRankEmbedded` still fails #5559 (pre-existing)                                              |
| Known issues logged      | pass   | Added: a recorded width can go stale against its column; `test_embeddings_api.py` depends on leftover column width                            |
| Version bump             | pass   | `pyproject.toml` at `3.2.0`; CHANGELOG `### v3.2.0 (2026-09-19)`, trailing `---`; the 3.1.1 section folded in, not left dangling              |
| Documentation parity     | pass   | USER_GUIDE section 8 now documents `get_embedding_identity` / `set_embedding_identity` / `embedding_registry`; README has no version string   |
| Artifact metadata        | pass   | `twine check dist/*` PASSED for wheel and sdist under twine 7.0.0. The 3.1.1 artifacts were moved to `/tmp/ivg-dist-archive/`, not deleted    |
| Bare-install E2E         | pass   | Wheel into an empty 3.13 venv, no extras, `import iris_vector_graph` from a neutral cwd: imports. `rdflib`/`pyshacl`/`fastapi` still absent   |

Coverage detail — no public-API module under 80%: `engine.py` 92, `_engine/query.py` 91,
`_engine/nodes_edges.py` 95, `sdk.py` 95, `cypher_api.py` 96. New code in this release:
`embedding_identity.py` 100%, `exceptions.py` 100%, `_engine/schema.py` 89%. Under 80%
and unchanged: `api_auth.py` 70% (in this checklist's own ignore list) and
`text_search.py` 79%.

The integration diff is the number that matters, because the chunk totals move with
chunk boundaries and leftover container state. The 25 files not covered by the chunk
lists were run on the branch and again at HEAD with the tree stashed, and the failure
sets differ by exactly three cases. Two fail at HEAD and pass here —
`test_embed_queue_e2e.py::test_enqueue_process_search_roundtrip` and
`::test_pending_count_and_clear_done`, both of which 226 fixes. One passes at HEAD and
failed here: `test_embeddings_api.py::test_store_embedding_and_knn`, raising
`EmbeddingIdentityConflict: recorded dimension 384 but this writer declares 768`. That
is container state, not a regression: its fixture declares 768 and can only widen a
column whose table is empty, and a prior file had left five rows behind. From a cleared
state both tests in the file pass. Written up in KNOWN_ISSUES
§`test_embeddings_api.py` depends on the embedding column's leftover width.

Two measurement lessons from this round, both recorded in `specs/226-.../tasks.md`.
Chunk `ad` returned `6 passed, 547 errors` when it ran straight after chunk `ac`, and
`2 failed, 547 passed, 2 skipped, 2 errors` when run alone — chunk `ac`'s namespace
tests leave state behind, so a chunk total is only meaningful re-run in isolation. And
`git stash push -u` removes untracked test files, so a HEAD baseline that passes the
branch's file list to pytest reports a bare `collected 0 items` instead of erroring;
filter to files that exist at HEAD first.

Date: **2026-09-19** Release: **3.2.0**

### 3.1.1 — packaging fix (2026-09-19)

One-line dependency change plus its test. The gates that a `pyproject.toml`
`dependencies` entry cannot affect were not re-measured; they still stand at the
3.1.0 numbers below. Status words here are ASCII on purpose: `prettier` pads table
cells by display width and `markdownlint` MD060 checks them by code point, so a ✅ in
a column whose width it sets makes the two tools disagree permanently.

| Gate                 | Status  | Notes                                                                                                                    |
| -------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| Unit tests           | pass    | 8709 passed / 0 failed / 20 skipped — the 8705 baseline plus 4 new in `test_core_dependencies_cover_eager_imports.py`    |
| Bare-install E2E     | pass    | `pip install dist/*.whl` into an empty 3.13 venv, no extras, then `import iris_vector_graph` from a neutral cwd: imports |
| Extras stay optional | pass    | That venv has `requests`, `numpy`, `pydantic` and none of `rdflib`, `pyshacl`, `fastapi` — core did not swallow a group  |
| Lint clean           | pass    | `ruff check` on the new test file: all checks passed. Repo-wide count unchanged, see the 3.1.0 row                       |
| Artifact metadata    | pass    | `twine check dist/*` PASSED for both wheel and sdist, under twine 7.0.0                                                  |
| Version bump         | pass    | `pyproject.toml` at `3.1.1`; CHANGELOG `### v3.1.1 (2026-09-19)`, trailing `---`                                         |
| Known issues logged  | pass    | KNOWN_ISSUES §Packaging now records the defect as fixed in 3.1.1 rather than open                                        |
| §9 publish           | not run | Artifacts built and checked; no tag cut, nothing pushed, nothing uploaded                                                |

The first unit run of this session returned 184 errors, and the cause was the
documented `conftest.py` fall-through, not the change under test:
`ivg-iris-enterprise` was `Exited (255)` and the suite silently dialled
`localhost:1972`. Starting the container took the same tree to 8709/0. That defect
has now cost two measurements — see KNOWN_ISSUES §`conftest.py` cannot tell a stopped
container from a missing one, and run `docker ps` first.

Startup also reported `Detected 2 errors during load` and aborted its own
compile-all, but the deployment is complete: 67 `Graph.KG.*` classes compiled, and the
only class defined-but-not-compiled is the stale `Graph.KG.TestEdge` leftover already
in §Loose ends. `User.PageRankEmbedded` is the other, per the 3.1.0 row.

### 3.1.0 gates

Re-measured 2026-09-18 on `225-upgrade-artifact-fidelity`, against
`ivg-iris-enterprise` (port 31972), after the embedding-dimension fixes. Supersedes
the 2026-09-16 measurement at `3c795e9` (kept below). §1–§8b are measured; §9 was
run on 2026-09-19. Every ⚠️ below is written up in
[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

| Gate                     | Status    | Notes                                                                                                                                          |
| ------------------------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Branch and history       | ✅        | `225-upgrade-artifact-fidelity`, clean linear history                                                                                          |
| Unit tests               | ✅        | 8705 passed / 0 failed / 20 skipped (8672 baseline + 33 new). k-hop fast path passed this run: container `^NKG` state, still not a gate        |
| Integration tests        | ⚠️        | Five files segfault alone on their first test and give no result. Remaining chunks: 70 failed / 26 errors, all pre-existing                    |
| Arno (enterprise)        | ⚠️        | 9 failed / 71 passed / 4 skipped. All 9 trace to `_detect_arno`'s probe using a node that does not exist                                       |
| Coverage ≥ 89%           | ✅ 91%    | `coverage run` for unit, `--append` per integration chunk, so a crashed chunk loses only its own data. `--fail-under=89` → 0                   |
| No benchmark regressions | ⬜ Waived | Same waiver as v2.17.0: `bench_utils.py` writes `SQLUser.*` (lines 11, 16, 22)                                                                 |
| Lint clean               | ⚠️        | `ruff check .` = 2048 findings, all pre-existing; no `[tool.ruff]` section exists, so this gate has never been meaningful                      |
| ObjectScript compiles    | ⚠️ 55/56  | Every `Graph.KG.*` class clean; `User.PageRankEmbedded` fails with #5559 (pre-existing, and only visible since the `%Status` fix)              |
| Known issues logged      | ✅        | Added: `conftest.py` cannot tell a stopped container from a missing one; no per-graph embedding model; inert `get_procedures_sql_list` param   |
| Version bump             | ✅        | `pyproject.toml` at `3.1.0`; CHANGELOG `### v3.1.0 (2026-09-19)`, trailing `---`. §9's `head -n -1` is GNU-only — use `sed '$d'` on macOS      |
| Documentation parity     | ✅        | `erase_graph`, `erase_all`, `verify_graph`, `delete_edge_temporal` now have usage examples; README and USER_GUIDE no longer teach `drop_graph` |

Coverage detail — no public-API module under 80%: `engine.py` 92, `_engine/query.py`
91, `nodes_edges.py` 94, `sdk.py` 95, `cypher_api.py` 96, `schema.py` 89,
`_engine/schema.py` 93, `constants.py` 100, and `admin.py` 95, `temporal.py` 94,
`_validate.py` 98. Under 80%: `api_auth.py` 70% (its test file is in this
checklist's own ignore list) and `text_search.py` 79% (known baseline gap).

Two notes on the deltas from 2026-09-16. The integration count moved from ~66 to 70
failures at the same 26 errors; the additional cases are chunk-boundary dependent and
all are pre-existing API drift — the only dimension-adjacent one,
`test_vector_engine_deep.py::TestValidateVectorTable::test_validate_kg_node_embeddings`,
fails on `ImportError: cannot import name '_table'`, and `_table` is absent from
`_engine/vector.py` at `3c795e9` too. And `ruff` reports the same 21 findings across
the six touched modules as it does at `3c795e9`, so the new code adds none.

The 2026-09-16 run of the unit gate was measured against the wrong instance and is
void, not merely different: `ivg-iris-enterprise` had been `Exited` for 12 hours,
`IRISContainer.attach` succeeded on it anyway, and the suite silently ran against
`irispython-dx-iris` on `localhost:1972`. See KNOWN_ISSUES §Test and build
environment. Confirm `docker ps` before trusting any number here.

§9 ran 2026-09-19 with Tom's explicit instruction: `main` fast-forwarded to `20725bd`
and pushed, annotated tag `v3.1.0` pushed, both artifacts uploaded to PyPI, GitHub
release `v3.1.0` created from the CHANGELOG section. `twine check` needed twine 7.0.0
— 6.2.0 with packaging 25.0 rejects the `Metadata-Version: 2.5` that the isolated
build env's hatchling emits, and the published 3.0.1 wheel is also 2.5, so PyPI
accepts it. The 3.0.1 artifacts were moved to `/tmp/ivg-dist-archive/`, not deleted.

The clean-venv install check is the one §9 item that failed, and it failed for a
reason that predates this release: `pip install iris-vector-graph==3.1.0` followed by
`import iris_vector_graph` raises `ModuleNotFoundError: No module named 'requests'`.
3.0.1 fails identically. With `requests` present the 3.1.0 wheel imports and the
embedding-dimension fix behaves as designed — `get_embedding_dimension(cursor,
table_name='Graph_KG.kg_NodeEmbeddings')`, `DEFAULT_EMBEDDING_DIMENSION == 768`,
`derive_class_name('Graph_KG.kg_EdgeEmbeddings') == 'Graph.KG.kgEdgeEmbeddings'`. See
KNOWN_ISSUES §Packaging; fixing it needs a `3.1.1`.

Date: **2026-09-18** (gates) / **2026-09-19** (§9) Release: **3.1.0 published**

### Earlier sign-offs

| Date       | Release | Result                                                                                     |
| ---------- | ------- | ------------------------------------------------------------------------------------------ |
| 2026-09-16 | 3.1.0   | Void — unit gate ran against `irispython-dx-iris`, not `ivg-iris-enterprise`. Coverage 90% |
| 2026-09-06 | v2.17.0 | All gates passed; benchmarks waived for the `bench_utils.py` `SQLUser.*` issue             |
