# Data Model: Bucket Group API Enhancements

**Feature**: 042-bucket-group-api  
**Date**: 2026-04-07

## Existing Globals (unchanged)

### `^KG("tagg", bucket, source, predicate, metric)`
Forward pre-aggregation index. Written by `bulk_create_edges_temporal`. Keys:
- `bucket`: integer (`timestamp \ BUCKET_SIZE`)
- `source`: node ID string
- `predicate`: edge type string
- `metric`: one of `count`, `sum`, `min`, `max`

### `^KG("tin", bucket, target, predicate, source, weight)`  
Reverse temporal index. Written by `bulk_create_edges_temporal`. Used by `GetBucketGroupTargets`.

## API Surface Changes

### Modified: `GetBucketGroups(predicate, tsStart, tsEnd, sourcePrefix="")`

**New parameter**: `sourcePrefix As %String = ""`  
**Behavior change**: When non-empty, skip any `src` that does not satisfy `$Extract(src, 1, $Length(sourcePrefix)) = sourcePrefix`.  
**Return shape** (unchanged): JSON array of objects — `[{"source":..., "predicate":..., "count":N, "sum":F, "avg":F, "min":F, "max":F}, ...]`

### New: `GetBucketGroupTargets(source, predicate, tsStart, tsEnd)`

**Returns**: JSON array of distinct target node ID strings — `["NodeID1", "NodeID2", ...]`  
**Scans**: `^KG("tin")` buckets in `[tsStart \ BUCKET, tsEnd \ BUCKET]`  
**Deduplication**: local array `dedup(target)=1` across all matching buckets

## Python Engine Surface Changes

### Modified: `engine.get_bucket_groups(predicate, ts_start, ts_end, source_prefix="")`

New `source_prefix: str = ""` keyword argument. Passes to `GetBucketGroups` as fourth positional arg.  
**Docstring added**: documents all return dict keys.

### New: `engine.get_bucket_group_targets(source, predicate, ts_start, ts_end) -> list[str]`

Calls `GetBucketGroupTargets` via `classMethodValue`. Parses JSON response. Returns `list[str]`.
