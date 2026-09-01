# Data Model: TemporalIndex API Gaps

## Existing entities (unchanged)

### Raw edge

Stored in `^KG("tout", ts, s, p, o) = weight` and `^KG("tin", ts, o, p, s) = weight`.

Fields: `ts` (integer epoch, unit per TSUNIT), `s` (source node_id), `p` (predicate), `o` (target node_id), `weight` (double).

Subject to raw retention via `PurgeBefore` and new `PurgeRawBefore`.

### Aggregate bucket

Stored in `^KG("tagg", bucket, s, p, metric) = value` where `bucket = ts \ BUCKETDIV`.

Metrics: `count`, `sum`, `min`, `max`, `hll`. `bucket` index also in `^KG("bucket", bucket, s)`.

Never touched by `PurgeRawBefore`. Only `PurgeBefore` (and `Purge`) removes aggregates, and only for buckets strictly before the bucket containing `tsEnd`.

### Edge property

Stored in `^KG("edgeprop", ts, s, p, o, key) = val`. Deleted by `PurgeRawBefore` for the same `ts < tsEnd` edges.

### Adjacency shadow

`^KG("out", 0, s, p, o)` and `^KG("in", 0, o, p, s)` — never purged by either method (reachability must survive raw purge). Unchanged.

## New entities (this spec)

### Label set

```text
^KG("labelset", hash) = canonicalJSON
```

- `hash`: SHA1 hex string (40 chars lowercase) of the canonical JSON form
- `canonicalJSON`: JSON with keys sorted ascending case-sensitively, no extra whitespace
- Written exactly once (idempotent). Never purged by any purge method.
- Bounded by distinct label-set count, not sample count.

**State transitions**: Written by `InternLabelSet` if absent. Never updated. Never deleted.

**Validation rules**: Input JSON must be parseable as an object. Empty object `{}` is valid (produces a fixed hash). Invalid JSON returns `""` (no write).

## Parameter additions

### TSUNIT

```objectscript
Parameter TSUNIT As %String = ""
```

- `""` (default): second-precision. `BUCKETDIV = BUCKET = 300`.
- `"ms"`: millisecond-precision. `BUCKETDIV = BUCKETMS = BUCKET * 1000 = 300000`.

All bucket arithmetic uses `BUCKETDIV` when this parameter is read.

### BUCKETMS

```objectscript
Parameter BUCKETMS As %Integer = 300000
```

Derived constant: `BUCKET * 1000`. Used when `TSUNIT = "ms"`.

## Signature changes

### InsertEdge

```objectscript
ClassMethod InsertEdge(
    source As %String, predicate As %String, target As %String,
    timestamp As %Integer = "", weight As %Double = 1.0,
    attrsJSON As %String = "", upsert As %Boolean = 0,
    suppressReverseIndex As %Boolean = 0
) As %Status
```

New parameter: `suppressReverseIndex` — final positional, default `0`. When `1`, the `^KG("tin")` write is skipped.

### BulkInsert batch item

JSON item schema gains an optional boolean field `"suppress_reverse"` (default `0`). When `1`, same suppression applies for that item.

## Invariants

1. A `PurgeRawBefore(tsEnd)` call never changes the count of `^KG("tagg")` or `^KG("bucket")` entries.
2. A `PurgeBefore(tsEnd)` call never kills the aggregate bucket containing `tsEnd`.
3. `InternLabelSet` called N times with the same logical label set writes storage exactly once.
4. `suppressReverseIndex=1` writes `^KG("tout")` but skips `^KG("tin")` — no other global is affected.
