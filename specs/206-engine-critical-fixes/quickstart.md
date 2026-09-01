# Quickstart: IVG Engine Critical Fixes (206)

## A9 — sync() now safe with temporal data

```python
from iris_vector_graph import IRISGraphEngine

engine = IRISGraphEngine(conn)

# Write temporal edges
engine.create_edge_temporal("node_a", "METRIC_AT", "node_b", timestamp=1_700_000_000)

# Previously: sync() destroyed all temporal data silently
# Now: temporal edges survive
engine.sync()

edges = engine.get_edges_in_window("node_a", "METRIC_AT", 0, 9_999_999_999)
assert len(edges) == 1  # survives sync
```

## A12 — bulk_delete_nodes (no callsite change)

```python
# Same API — but now 100x faster on large tables
deleted = engine.bulk_delete_nodes(node_ids)
```

## A8.3 — PurgeBucketRange

```python
# Expire aggregate buckets by bucket number without touching raw edges.
# Bucket number = timestamp // BUCKET_SIZE
# BUCKET_SIZE = 300 (seconds mode) or 300000 (ms mode)

# Remove junk buckets written at ~6e9 (wrong ms-mode path)
n = engine.purge_bucket_range(bucket_start=0, bucket_end=5_999_999)
print(f"Removed {n} junk buckets")

# Raw edges at ts ~1.8e12 (ms) survive:
edges = engine.get_edges_in_window("", "METRIC_AT", 0, 9_999_999_999_999)
assert len(edges) > 0

# 13-month retention: compute cutoff bucket and purge
import time
cutoff_ts = int(time.time()) - (13 * 30 * 24 * 3600)
cutoff_bucket = cutoff_ts // 300
n = engine.purge_bucket_range(bucket_start=0, bucket_end=cutoff_bucket - 1)
```
