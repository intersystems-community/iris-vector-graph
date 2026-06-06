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

Run against the `ivg-iris` community container (port 21972).

```bash
pytest --tb=short -q
```

- [ ] Zero failures in `tests/unit/`
- [ ] Zero unexpected failures in `tests/integration/` (pre-existing skips documented in `KNOWN_ISSUES.md` are OK)
- [ ] Zero regressions vs. the prior release baseline (`tests/benchmarks/results/`)
- [ ] Arno/enterprise tests pass on `ivg-iris-enterprise` (port 31972) — `TestBFSArnoE2E` and related

---

## 3. Test Coverage — **GATE: ≥ 90%**

Coverage is measured across **unit + integration** together against the live `ivg-iris` container.

```bash
# From repo root with ivg-iris running
/Users/tdyar/ws/iris-vector-graph/.venv/bin/python -m coverage run \
    --source=iris_vector_graph \
    -m pytest tests/unit/ tests/integration/ \
    --ignore=tests/unit/test_ivf_index.py \
    -q -p no:warnings

/Users/tdyar/ws/iris-vector-graph/.venv/bin/python -m coverage report \
    --fail-under=90 --sort=cover
```

- [ ] Overall coverage ≥ 90% (`coverage report --fail-under=90`)
- [ ] No single public-API module below 70% (check `sdk.py`, `engine.py`, `cypher_api.py`, `_engine/query.py`)
- [ ] New code added in this release has ≥ 90% coverage in its own test file

**Baseline (2026-06-05, unit-only):** 67.2% — integration suite expected to push this significantly higher.
Lowest-coverage files to watch: `stores/iris_sql_store.py` (36%), `stores/arno_bridge.py` (27%), `sdk.py` (29%).

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

- [ ] All `.cls` files in `iris_src/src/` compile cleanly on `ivg-iris` (community)
- [ ] All `.cls` files compile on `ivg-iris-enterprise` if enterprise-only classes changed
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

## 9. PyPI Publish (explicit permission required)

**Do not run without explicit "publish it" / "push to PyPI" instruction.**

```bash
python -m build
twine upload dist/*
```

- [ ] Explicit publish instruction received from Tom
- [ ] `dist/` contains only the intended release artifacts
- [ ] Test install from PyPI in a clean venv: `pip install iris-vector-graph==<version>`

---

## Sign-off

| Gate | Status | Notes |
|------|--------|-------|
| Tests pass | | |
| Coverage ≥ 90% | | |
| No benchmark regressions | | |
| Lint clean | | |
| ObjectScript compiles | | |

Date: ___________  Release: v___________
