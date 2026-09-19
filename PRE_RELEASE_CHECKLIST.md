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

Re-measured 2026-09-18 on `225-upgrade-artifact-fidelity`, against
`ivg-iris-enterprise` (port 31972), after the embedding-dimension fixes. Supersedes
the 2026-09-16 measurement at `3c795e9` (kept below). §1–§8b are measured; §9
(PyPI publish, GitHub release) has not been run and no tag has been cut. Every
⚠️ below is written up in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

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

Date: **2026-09-18** Release: **3.1.0 prepared — not tagged, not published**

### Earlier sign-offs

| Date       | Release | Result                                                                                     |
| ---------- | ------- | ------------------------------------------------------------------------------------------ |
| 2026-09-16 | 3.1.0   | Void — unit gate ran against `irispython-dx-iris`, not `ivg-iris-enterprise`. Coverage 90% |
| 2026-09-06 | v2.17.0 | All gates passed; benchmarks waived for the `bench_utils.py` `SQLUser.*` issue             |
