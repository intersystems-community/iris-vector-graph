# Python Contracts: TemporalIndex API Gaps

## IRISGraphEngine (via TemporalMixin in _engine/temporal.py)

### New: purge_raw_before

```python
def purge_raw_before(self, ts_end: int) -> int:
    """Delete raw temporal edges with ts < ts_end. Preserves aggregates.

    Returns the number of edges deleted.

    Delegates to Graph.KG.TemporalIndex.PurgeRawBefore via _call_classmethod.
    """
```

### Modified: create_edge_temporal

```python
def create_edge_temporal(
    self,
    source: str,
    predicate: str,
    target: str,
    timestamp: int = None,
    weight: float = 1.0,
    attrs: dict = None,
    upsert: bool = False,
    suppress_reverse_index: bool = False,
    graph: str | None = None,
) -> bool:
    ...
```

New parameter: `suppress_reverse_index: bool = False`. Passed through to
`_store.write_temporal_edge(..., suppress_reverse_index=suppress_reverse_index)`.

### Modified: bulk_create_edges_temporal

```python
def bulk_create_edges_temporal(
    self,
    edges: list,
    upsert: bool = False,
    suppress_reverse_index: bool = False,
    graph: str | None = None,
) -> int:
    ...
```

New parameter: `suppress_reverse_index: bool = False`. Applied to all edges in
the batch. Individual per-edge suppression also supported via `attrs` dict field
`"suppress_reverse": 1` (passed through as `suppress_reverse` in the batch item).

### New: intern_label_set

```python
def intern_label_set(self, attrs: dict) -> str:
    """Canonicalize, hash, and intern an attribute dict.

    Returns SHA1 hex hash string. Returns "" on error.

    Delegates to Graph.KG.TemporalIndex.InternLabelSet via _call_classmethod.
    Input dict is serialized to JSON before passing.
    """
```

### New: resolve_label_set

```python
def resolve_label_set(self, hash_hex: str) -> str:
    """Return the canonical JSON string for a known label set hash.

    Returns "" if the hash is unknown.

    Delegates to Graph.KG.TemporalIndex.ResolveLabelSet via _call_classmethod.
    """
```

## IRISGraphStore (iris_vector_graph/stores/iris_sql_store.py)

### Modified: write_temporal_edge

```python
def write_temporal_edge(
    self, source_id: str, predicate: str, target_id: str,
    timestamp: int, weight: float = 1.0, attrs: dict | None = None,
    upsert: bool = False, suppress_reverse_index: bool = False
) -> IVGResult:
    ...
```

New parameter: `suppress_reverse_index: bool = False`. Passed as `str(int(suppress_reverse_index))` to `InsertEdge`.

### Modified: bulk_write_temporal_edges

```python
def bulk_write_temporal_edges(
    self, edges: list, upsert: bool = False, suppress_reverse_index: bool = False
) -> IVGResult:
    ...
```

New parameter: `suppress_reverse_index: bool = False`. Applied to all edges.

### New: purge_raw_before

```python
def purge_raw_before(self, ts_end: int) -> int:
    """Call PurgeRawBefore and return the edge count deleted."""
```

### New: intern_label_set

```python
def intern_label_set(self, attrs_json: str) -> str:
    """Call InternLabelSet and return the SHA1 hex hash."""
```

### New: resolve_label_set

```python
def resolve_label_set(self, hash_hex: str) -> str:
    """Call ResolveLabelSet and return the canonical JSON or ''."""
```

## GraphStore Protocol (iris_vector_graph/store_protocol.py)

```python
def purge_raw_before(self, ts_end: int) -> int: ...
def intern_label_set(self, attrs_json: str) -> str: ...
def resolve_label_set(self, hash_hex: str) -> str: ...
```
