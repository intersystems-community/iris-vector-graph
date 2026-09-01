# ObjectScript Contracts: TemporalIndex API Gaps

Class: `Graph.KG.TemporalIndex`

## New parameters

```objectscript
/// Timestamp precision unit. "ms" = milliseconds. Default "" = seconds.
Parameter TSUNIT As %String = "";

/// Bucket width in milliseconds when TSUNIT="ms". Derived: BUCKET*1000.
Parameter BUCKETMS As %Integer = 300000;
```

## Modified: InsertEdge

```objectscript
/// Insert a temporal edge. suppressReverseIndex=1 skips ^KG("tin") write.
ClassMethod InsertEdge(
    source As %String,
    predicate As %String,
    target As %String,
    timestamp As %Integer = "",
    weight As %Double = 1.0,
    attrsJSON As %String = "",
    upsert As %Boolean = 0,
    suppressReverseIndex As %Boolean = 0
) As %Status
```

**Behavior change**: when `suppressReverseIndex=1`, line `Set ^KG("tin", ...)` is skipped.
All other writes unchanged (tout, bucket, out, in, deg, tagg, HLL, edgeprop).

## Modified: BulkInsert

```objectscript
/// Bulk insert from JSON array. Each item may include "suppress_reverse": 1.
ClassMethod BulkInsert(batchJSON As %String, upsert As %Boolean = 0) As %Integer
```

**Behavior change**: each item object now checked for `suppress_reverse` field (default 0).
When 1, that item's `^KG("tin")` write is skipped.

## New: PurgeRawBefore

```objectscript
/// Delete raw temporal edges with ts < tsEnd. Preserves aggregates.
/// Returns edge count deleted. Strict < boundary (same as PurgeBefore).
ClassMethod PurgeRawBefore(tsEnd As %Integer) As %Integer
```

**Deletes**: `^KG("tout", ts, ...)`, `^KG("tin", ts, ...)`, `^KG("edgeprop", ts, ...)`
for all `ts < tsEnd`.

**Does NOT touch**: `^KG("tagg")`, `^KG("bucket")`, `^KG("out")`, `^KG("in")`, `^KG("deg")`,
`^KG("labelset")`.

**Returns**: integer count of raw edges deleted.

## Modified: PurgeBefore (bucket-boundary fix)

```objectscript
ClassMethod PurgeBefore(tsEnd As %Integer)
```

**Behavior change**: aggregate buckets are only killed for buckets strictly less than
`tsEnd \ BUCKETDIV`. The bucket containing `tsEnd` is NOT killed.

This fixes the latent bug where mid-bucket `tsEnd` destroyed aggregates for edges
newer than `tsEnd`.

## New: InternLabelSet

```objectscript
/// Canonicalize, hash, and intern an attribute JSON object.
/// Returns SHA1 hex hash. Returns "" on invalid JSON input.
/// Idempotent: same logical label set always returns same hash.
ClassMethod InternLabelSet(attrsJSON As %String) As %String
```

**Algorithm**:

1. Parse `attrsJSON` as `%DynamicObject`. Return `""` on parse error.
2. Sort keys ascending case-sensitively.
3. Emit compact JSON (no whitespace).
4. Compute `SHA1Hash(canonicalJSON)` → lowercase hex string.
5. If `$Data(^KG("labelset", hash)) = 0`, write `^KG("labelset", hash) = canonicalJSON`.
6. Return hex hash.

## New: ResolveLabelSet

```objectscript
/// Return the canonical JSON for a known hash. Returns "" if unknown.
ClassMethod ResolveLabelSet(hash As %String) As %String
```

**Returns**: `$Get(^KG("labelset", hash), "")`.
