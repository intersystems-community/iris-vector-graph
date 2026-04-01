# Data Model: Edge Properties + NDJSON

**Feature**: 037-edgeprop-ndjson-ingest | **Date**: 2026-04-01

## New ^KG Subscript Key

```
^KG("edgeprop", timestamp, source, predicate, target, key) = value
```

- Identifies edge by (ts, s, p, o) — same as `^KG("tout",...)`
- `key` selects individual attribute (e.g., "latency_ms", "error", "trace_id")
- `value` is always stored as string

## NDJSON Event Schema

### Node event
```json
{"kind":"node","id":"service:checkout","labels":["Service"],"properties":{"name":"checkout"}}
```

### Non-temporal edge event
```json
{"kind":"edge","source":"span:def456","predicate":"BELONGS_TO","target":"trace:abc123","weight":1.0,"attrs":{}}
```

### Temporal edge event
```json
{"kind":"temporal_edge","source":"service:checkout","predicate":"CALLS_AT","target":"service:payment","timestamp":1712000000,"weight":1.0,"source_labels":["Service"],"target_labels":["Service"],"attrs":{"latency_ms":"237","status_code":"500","error":"true"}}
```

## Cleanup

`TemporalIndex.Purge()` now also kills `^KG("edgeprop")`.
