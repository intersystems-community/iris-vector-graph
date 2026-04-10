# Quickstart: Bucket Group API Enhancements

**Feature**: 042-bucket-group-api

## Prerequisites

```bash
# Container must be running
docker ps | grep iris_vector_graph
# If not: start via conftest (pytest starts it automatically) or:
# docker-compose up -d
```

## Filter bucket groups by tenant prefix

```python
from iris_vector_graph.engine import IRISGraphEngine

# Before (fetches all tenants, filters in Python):
all_groups = engine.get_bucket_groups("CALLED_BY", ts_start, ts_end)
my_groups = [g for g in all_groups if g["source"].startswith("Routine:AcmeCorp:")]

# After (filter pushed to IRIS):
my_groups = engine.get_bucket_groups(
    "CALLED_BY", ts_start, ts_end,
    source_prefix="Routine:AcmeCorp:"
)
```

## Query distinct targets for a source

```python
# Which query groups did ProcessHL7 call into this week?
targets = engine.get_bucket_group_targets(
    source="Routine:AcmeCorp:PROD:ProcessHL7",
    predicate="CALLED_BY",
    ts_start=week_start,
    ts_end=week_end,
)
# Returns: ["QueryGroup:AcmeCorp:PROD:G1", "QueryGroup:AcmeCorp:PROD:G2"]
```

## Run tests

```bash
cd ~/ws/iris-vector-graph
# Unit tests only (no container required):
pytest tests/unit/test_temporal_edges.py -v -k "bucket_group"

# Full suite including live IRIS:
pytest tests/unit/test_temporal_edges.py -v
```
