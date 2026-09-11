# ADR-0001 — Graph key is always the first subscript in every ^KG global

**Status**: Accepted  
**Date**: 2026-09-11  
**Specs**: 214 (structural adjacency), 223 (temporal index)

## Decision

In every `^KG` global, the graph key is the first subscript after the key name:

```
^KG("out",   graphKey, source, predicate, target)
^KG("in",    graphKey, target, predicate, source)
^KG("deg",   graphKey, node)
^KG("degp",  graphKey, node, predicate)
^KG("tout",  graphKey, timestamp, source, predicate, target)
^KG("tin",   graphKey, timestamp, target, predicate, source)
^KG("bucket",graphKey, bucket, node)
^KG("tagg",  graphKey, bucket, source, predicate, metric)
^KG("edgeprop", graphKey, timestamp, source, predicate, target, attrKey)
```

The default graph uses integer `0` as its key (never the empty string, which IRIS
does not support as an intermediate subscript).

## Context

IRIS global subscripts form a hierarchical key tree. Placing the graph key first means
every tenant-scoped operation (`QueryWindow`, `PurgeRawBefore`, `BuildKG`, backup,
restore) is naturally bounded by the first subscript. A `$Order` or `Kill` anchored at
`^KG("tout", graphKey)` touches only that graph's data — no filter needed, no risk of
forgetting one.

## Alternatives considered

**Graph key last (e.g. `^KG("tout", ts, s, p, o, graphKey)`)**: Window queries would
require scanning all timestamps across all graphs to filter by graph. Purge becomes
expensive. Rejected.

**No graph key in temporal globals (flat layout)**: The layout used before spec-223.
Causes silent cross-tenant data leakage when multiple graphs share a namespace.
Rejected as incompatible with multi-tenant isolation requirements.

## Consequences

- A flat-layout migration utility (`TemporalIndex.MigrateToGraphScoped`) must be
  provided for any deployment with pre-spec-223 temporal data.
- Every module that reads or writes `^KG` globals must use this subscript ordering.
  Direct global access outside `TemporalIndex.cls` and `EdgeScan.cls` is forbidden.
- This is a storage-layout contract. Changing it requires a new major version.
