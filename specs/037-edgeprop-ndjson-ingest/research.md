# Research: Edge Properties + NDJSON Import/Export

**Feature**: 037-edgeprop-ndjson-ingest | **Date**: 2026-04-01

## R1: edgeprop Storage Design

**Decision**: `^KG("edgeprop", timestamp, source, predicate, target, key) = value`

**Rationale**: Matches the full temporal spec §6.1. The (ts, s, p, o) prefix identifies the edge; `key` selects the attribute. Retrieval is `$Order` on the key subscript for a given edge — O(attrs) per edge.

**Alternatives rejected**:
- JSON blob on `^KG("tout",...) = jsonAttrs`: breaks $vectorop suitability, can't query individual attrs
- SQL table for edge props: adds DDL complexity, not needed for global-only reads

## R2: NDJSON Format

**Decision**: One JSON object per line, discriminated by `kind` field: `"node"`, `"edge"`, `"temporal_edge"`.

**Rationale**: Canonical from full temporal spec §3.1. NDJSON is streaming-friendly (one line = one event), works with `jq`, easily parallelizable. Used by Neo4j bulk import, Apache Kafka Connect, and OpenTelemetry log export.

## R3: Import Batching Strategy

**Decision**: Read NDJSON line by line. Collect nodes first (upsert), then edges in batches of 10K via `BulkInsert`. Attrs included in the BulkInsert JSON payload.

**Rationale**: Same pattern as the existing `load_umls_bridges.py` — parse file, batch to ObjectScript. The `BulkInsert` classmethod handles all global writes server-side.

## R4: BulkInsert attrs Extension

**Decision**: Each item in the BulkInsert JSON array gains an optional `"attrs"` object:
```json
{"s":"service:checkout","p":"CALLS_AT","o":"service:payment","ts":1712000000,"w":1.0,"attrs":{"latency_ms":"237","error":"true"}}
```
ObjectScript iterates `attrs.%GetIterator()` and writes each key-value to `^KG("edgeprop",...)`.

**Rationale**: Minimal change to existing BulkInsert — add 5 lines for the attrs loop.
