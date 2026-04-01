# Quickstart: Edge Properties + NDJSON Import

## Store edge attributes

```python
engine.create_edge_temporal(
    "service:checkout", "CALLS_AT", "service:payment",
    timestamp=1712000000, weight=1.0,
    attrs={"latency_ms": "237", "status_code": "500", "error": "true", "trace_id": "abc123"}
)

attrs = engine.get_edge_attrs(1712000000, "service:checkout", "CALLS_AT", "service:payment")
# → {"latency_ms": "237", "status_code": "500", "error": "true", "trace_id": "abc123"}
```

## Import from NDJSON

```bash
# example.ndjson:
# {"kind":"node","id":"service:checkout","labels":["Service"],"properties":{"name":"checkout"}}
# {"kind":"node","id":"service:payment","labels":["Service"],"properties":{"name":"payment"}}
# {"kind":"temporal_edge","source":"service:checkout","predicate":"CALLS_AT","target":"service:payment","timestamp":1712000000,"weight":1.0,"attrs":{"latency_ms":"237"}}

python -c "
from iris_vector_graph.engine import IRISGraphEngine
engine = IRISGraphEngine(conn)
result = engine.import_graph_ndjson('example.ndjson')
print(result)  # {'nodes': 2, 'edges': 0, 'temporal_edges': 1}
"
```

## Export to NDJSON

```python
engine.export_graph_ndjson("output.ndjson")
engine.export_temporal_edges_ndjson("window.ndjson", start=1712000000, end=1712003600)
```
