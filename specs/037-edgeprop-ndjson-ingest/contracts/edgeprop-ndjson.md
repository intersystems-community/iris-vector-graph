# Contracts: Edge Properties + NDJSON

## ObjectScript

### InsertEdge (modified)
```objectscript
ClassMethod InsertEdge(source, predicate, target, timestamp, weight, attrs As %DynamicObject = "")
// If attrs provided, iterate and write ^KG("edgeprop", ts, s, p, o, key) = value
```

### BulkInsert (modified)
```objectscript
ClassMethod BulkInsert(batchJSON As %String) As %Integer
// Each item may have optional "attrs" object; writes to ^KG("edgeprop",...)
```

### GetEdgeAttrs (new)
```objectscript
ClassMethod GetEdgeAttrs(ts, source, predicate, target) As %String
// Returns JSON: {"latency_ms":"237","error":"true",...}
```

## Python

```python
def create_edge_temporal(source, predicate, target, timestamp=None, weight=1.0, attrs=None) -> bool
def get_edge_attrs(ts, source, predicate, target) -> dict
def import_graph_ndjson(path, upsert_nodes=True, batch_size=10000) -> dict
def export_graph_ndjson(path) -> dict
def export_temporal_edges_ndjson(path, start=None, end=None, predicate=None) -> dict
```
