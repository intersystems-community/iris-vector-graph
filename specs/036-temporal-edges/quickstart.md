# Quickstart: Temporal Edge Indexing

**Feature**: 036-temporal-edges

## Fraud Detection: Fan-out Burst Detection

```python
import time
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(conn)

# Ingest 100K transaction edges (bulk — single server-side call)
import time
edges = [
    {"s": f"account:{i%100}", "p": "SENDS", "o": f"account:{i+100}",
     "ts": int(time.time()) - (100000 - i), "w": float(i % 500)}
    for i in range(100000)
]
count = engine.bulk_create_edges_temporal(edges)
print(f"Inserted {count} edges")  # ~2 seconds

# Detect burst accounts (>50 outgoing SENDS in last 60 seconds)
bursts = engine.find_burst_nodes("Account", "SENDS", window_seconds=60, threshold=50)
print(f"Burst accounts: {bursts}")

# Time-window query: who did account:0 send to in last 5 minutes?
recent = engine.get_edges_in_window("account:0", "SENDS", time.time()-300, time.time())
print(f"Recent sends: {recent}")

# Velocity check
velocity = engine.get_edge_velocity("account:0", window_seconds=60)
print(f"Velocity: {velocity} edges/min")
```

## Cybersecurity: Lateral Movement Window

```python
# Find all network connections from host:compromised in last 10 minutes
edges = engine.get_edges_in_window(
    source="host:compromised",
    predicate="CONNECTS_TO",
    start=time.time() - 600,
    end=time.time()
)

# Check if any target hosts also had unusual velocity (port scan indicator)
for edge in edges:
    v = engine.get_edge_velocity(edge["o"], window_seconds=60)
    if v > 100:
        print(f"ALERT: {edge['o']} receiving {v} connections/min")
```

## ObjectScript Direct

```objectscript
// Bulk insert 10K edges
Set result = ##class(Graph.KG.TemporalIndex).BulkInsert(jsonBatch)
Write "Inserted: ", result, !

// Query window
Set edges = ##class(Graph.KG.TemporalIndex).QueryWindow("account:0", "SENDS", tsStart, tsEnd)

// Burst detection
Set bursts = ##class(Graph.KG.TemporalIndex).FindBursts("Account", "SENDS", 60, 50)
```

## Performance

| Operation | Target | Method |
|-----------|--------|--------|
| Bulk insert 100K edges | <2s | `BulkInsert` (ObjectScript) |
| Window query (1-min window) | <5ms | `$Order` range scan on `^KG("tout",...)` |
| Velocity check (1 node) | <1ms | Bucket index O(1) |
| Burst detection (10K nodes) | <100ms | Bucket scan |
