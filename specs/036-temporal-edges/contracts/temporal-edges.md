# Contracts: Temporal Edge Indexing

**Feature**: 036-temporal-edges | **Date**: 2026-04-01

## ObjectScript API (Graph.KG.TemporalIndex)

### InsertEdge
```objectscript
ClassMethod InsertEdge(source, predicate, target, timestamp, weight = 1.0)
// Writes: ^KG("tout",ts,s,p,o), ^KG("tin",ts,o,p,s), ^KG("bucket",bucket,s)
// Also writes: ^KG("out",s,p,o), ^KG("in",o,p,s), ^KG("deg",s) for compat
```

### BulkInsert
```objectscript
ClassMethod BulkInsert(batchJSON As %String) As %Integer
// Input: '[{"s":"A","p":"SENDS","o":"B","ts":1712000000,"w":1.0}, ...]'
// Returns: count of edges inserted
// Target: ≥50K edges/sec
```

### QueryWindow
```objectscript
ClassMethod QueryWindow(source, predicate, tsStart, tsEnd) As %String
// Returns: '[{"s":"A","p":"SENDS","o":"B","ts":1712000000,"w":1.0}, ...]'
// O(results), not O(total edges)
```

### GetVelocity
```objectscript
ClassMethod GetVelocity(nodeId As %String, windowSec As %Integer = 300) As %Integer
// Returns: edge count in most recent windowSec seconds
// Uses bucket index — O(windowSec/300) bucket reads
```

### FindBursts
```objectscript
ClassMethod FindBursts(label, predicate, windowSec, threshold) As %String
// Returns JSON array of node_ids where velocity >= threshold
```

### Purge
```objectscript
ClassMethod Purge()
// Kill ^KG("tout"), ^KG("tin"), ^KG("bucket")
// Does NOT touch ^KG("out"), ^KG("in"), etc.
```

## Python API (IRISGraphEngine)

```python
def create_edge_temporal(source, predicate, target, timestamp=None, weight=1.0) -> bool
def bulk_create_edges_temporal(edges: list[dict]) -> int
    # edges = [{"s":"A","p":"SENDS","o":"B","ts":1712000000,"w":1.0}, ...]
    # Calls TemporalIndex.BulkInsert via single classMethodValue
def get_edges_in_window(source, predicate, start, end) -> list[dict]
def get_edge_velocity(node_id, window_seconds=300) -> int
def find_burst_nodes(label, predicate, window_seconds, threshold) -> list[str]
```
