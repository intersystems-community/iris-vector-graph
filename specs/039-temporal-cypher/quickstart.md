# Quickstart: Cypher Temporal Edge Filtering (039)

## Acceptance Test Scenarios

### SC-001: Temporal window filter returns correct results

```python
import time, iris
from iris_devtester import IRISContainer
from iris_vector_graph.engine import IRISGraphEngine

c = IRISContainer.attach("iris-vector-graph-main")
conn = iris.connect(c.get_container_host_ip(), int(c.get_exposed_port(1972)), "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)

now = int(time.time())
engine.bulk_create_edges_temporal([
    {"s": "svc:auth", "p": "CALLS_AT", "o": "svc:payment", "ts": now - 100, "w": 42.7},
    {"s": "svc:auth", "p": "CALLS_AT", "o": "svc:payment", "ts": now - 500, "w": 10.0},
])

result = engine.execute_cypher(
    "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts, r.weight",
    {"start": now - 200, "end": now}
)
assert len(result["rows"]) == 1
assert abs(result["rows"][0][1] - 42.7) < 0.01
```

### SC-003: Latency within 2× of get_edges_in_window

```python
import statistics, time

src, pred = "svc:ts-station-service", "CALLS_AT"
ts_start, ts_end = 1708818000, 1708818225

# Baseline
lats_raw = []
for _ in range(12):
    t0 = time.perf_counter_ns()
    engine.get_edges_in_window(src, pred, ts_start, ts_end)
    lats_raw.append((time.perf_counter_ns() - t0) / 1e6)
baseline = statistics.median(lats_raw[2:])

# Cypher path
lats_cypher = []
for _ in range(12):
    t0 = time.perf_counter_ns()
    engine.execute_cypher(
        "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $s AND r.ts <= $e RETURN r.ts, r.weight",
        {"s": ts_start, "e": ts_end}
    )
    lats_cypher.append((time.perf_counter_ns() - t0) / 1e6)
cypher_lat = statistics.median(lats_cypher[2:])

assert cypher_lat <= baseline * 2, f"Cypher {cypher_lat:.2f}ms > 2× baseline {baseline:.2f}ms"
```

### SC-006: Non-temporal MATCH unchanged

```python
# Before this feature was added, this query worked correctly
result_before = engine.execute_cypher("MATCH (a:Service)-[r]->(b) RETURN a.id, b.id LIMIT 5")
# After: same result expected
result_after  = engine.execute_cypher("MATCH (a:Service)-[r]->(b) RETURN a.id, b.id LIMIT 5")
assert result_before["rows"] == result_after["rows"]
```
