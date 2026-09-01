# Quickstart: TemporalIndex API Gaps

## Prerequisites

Enterprise container running:

```bash
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
scripts/enterprise-container.sh up
```

Verify:

```bash
docker ps | grep ivg-iris-enterprise
```

## Run unit tests (no container needed)

```bash
pytest tests/unit/test_temporal_index_gaps.py -v
```

## Run integration tests (enterprise container required)

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  pytest tests/integration/test_temporal_index_gaps_e2e.py -v
```

## Run all existing tests (regression check)

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest
```

## Verify ObjectScript compile after edits

After editing `iris_src/src/Graph/KG/TemporalIndex.cls`, push and compile
via the iris-agentic-dev MCP:

```python
iris_doc(mode="put", file="iris_src/src/Graph/KG/TemporalIndex.cls", compile=True)
```

Or via devtester CLI:

```bash
idt deploy iris_src/src/Graph/KG/TemporalIndex.cls
```

## Verify SC-006 (opsreview zero direct ^KG after migration)

```bash
grep -rn '\\^KG' ~/ws/opsreview/iris/src/OpsReview/Monitor/
```

Expected output after opsreview migration: empty (or only OpsReview-owned globals like `^OpsReview.*`).

## Integration test scenario outline

### Gap 1 — PurgeRawBefore

1. Insert edges at ts=100, 200, 300 + run GetAggregate to confirm aggregates
2. `purge_raw_before(250)`
3. Assert: QueryWindow returns only ts=300 edge; GetAggregate returns same values

### Gap 2 — suppressReverseIndex

1. Insert edge with `suppress_reverse_index=True`
2. Assert: `QueryWindow(source, ...)` returns the edge
3. Assert: `QueryWindowInbound(target, ...)` returns empty

### Gap 3 — InternLabelSet

1. Call `intern_label_set({"b":2,"a":1})` → hash1
2. Call `intern_label_set({"a":1,"b":2})` → hash2
3. Assert hash1 == hash2
4. Call `resolve_label_set(hash1)` → assert returns `'{"a":1,"b":2}'`
5. Call `resolve_label_set("unknown")` → assert returns `""`

### Gap 4 — TSUNIT="ms"

1. Configure TSUNIT="ms" via class parameter (or subclass)
2. Insert edge at ts=1_000_000 (1000 s epoch in ms)
3. Assert: GetAggregate(tsStart=900_000, tsEnd=1_100_000) returns count=1
4. Assert: bucket stored = 1_000_000 // 300_000 = 3 (not 3333)
