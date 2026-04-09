# Research: Bucket Group API Enhancements

**Feature**: 042-bucket-group-api  
**Date**: 2026-04-07

## Decision 1: sourcePrefix filter implementation — `$Extract` vs `$Find`

**Decision**: Use `$Extract(src, 1, $Length(sourcePrefix)) = sourcePrefix` for the prefix check in the ObjectScript inner loop.

**Rationale**: `$Extract` is O(prefix_length) string comparison, always O(1) relative to source length. `$Find` searches for a substring anywhere in the string — wrong semantics for prefix matching and potentially slower.

**Alternatives considered**:
- `$Find(src, sourcePrefix) = 1`: Incorrect — `$Find` returns 1-based index of match end, not position. Would incorrectly pass non-prefix matches.
- `$Extract(src, 1, $Length(sourcePrefix)) = sourcePrefix`: Correct. `$Extract(str, from, to)` returns the substring from position `from` to `to`.

**Guard**: Skip the check entirely when `sourcePrefix = ""` to preserve full backward compatibility with zero overhead for unfiltered callers.

---

## Decision 2: `GetBucketGroupTargets` — global scan strategy

**Decision**: Scan `^KG("tin", bucket, target, predicate, source)` across all buckets in `[tsStart, tsEnd]`, collecting distinct `target` values where `source` and `predicate` match.

**Rationale**: The `^KG("tin")` reverse index already exists (written by `bulk_create_edges_temporal`) and is structured as `(bucket, target, predicate, source)`. Iterating over targets for a fixed predicate+source requires a nested `$Order` loop: outer on `target`, inner on `predicate`→`source` to confirm the match. This is the canonical IRIS global traversal pattern.

**Alternatives considered**:
- Scanning `^KG("tout")` forward index: Would require iterating all sources to find the one we want — wrong direction.
- New dedicated global: Unnecessary — `^KG("tin")` already supports this traversal.

**Deduplication**: Use a local array `dedup(target) = 1` to accumulate distinct targets across buckets. Final JSON array built from `$Order(dedup(""))` loop.

---

## Decision 3: Python wrapper signature

**Decision**: `get_bucket_groups(predicate="", ts_start=0, ts_end=0, source_prefix="")` — `source_prefix` added as fourth keyword argument with empty string default.

**Rationale**: Matches existing positional convention. Fourth positional argument in Python maps to fourth positional argument in the ObjectScript classMethodValue call.

**Decision**: `get_bucket_group_targets(source, predicate, ts_start, ts_end) -> list[str]` — all required, no defaults. Source is the traversal key; omitting it makes the call meaningless.

---

## Decision 4: Test file placement

**Decision**: Unit tests (mocked engine) go in `tests/unit/test_temporal_edges.py` (existing file for this subsystem). E2E tests go in the same file under the live-container `skipif(SKIP_IRIS_TESTS)` guard pattern already established there.

**Rationale**: Keeps all temporal-edge tests in one file. The existing `SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"` pattern is already in that file — follow it rather than create a new file.

**Container**: `iris-vector-graph-main` via `conftest.py` `iris_test_container` session fixture. Do NOT use `opsreview-iris` — that is opsreview's container. Per constitution Principle IV and VI, always use this project's canonical container.

> **Note**: The clarification in spec.md that said "compile/test against `opsreview-iris`" was answered in the context of which consumer validates the change. For IVG's own test suite, the canonical container is `iris-vector-graph-main` (from `docker-compose.yml` → `container_name: iris_vector_graph`, test fixture name `iris-vector-graph-main`). The opsreview follow-on commit validates against `opsreview-iris`.

---

## Decision 5: `.cls` compile step

**Decision**: The `Graph.KG.TemporalIndex.cls` file is compiled into the running container as part of the existing test setup (`_setup_iris_container` in `conftest.py` deploys `.cls` files). No additional compile step needed in the task list beyond running the test suite.

**Rationale**: `conftest.py` already handles CLS deployment — tests that modify `.cls` files simply need to ensure the container is running before tests execute.
