# Quickstart: NICHE Knowledge Graph Integer Index

**Feature**: 028-nkg-integer-index

## How It Works

When you insert an edge via SQL:

```sql
INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('MESH:D003920', 'binds', 'GENE:TP53')
```

The functional index fires and writes to **both** globals:

```
// ^KG (backward compatible, string subscripts)
^KG("out", "MESH:D003920", "binds", "GENE:TP53") = 1.0
^KG("in",  "GENE:TP53", "binds", "MESH:D003920") = 1.0

// ^NKG (integer-encoded, for arno acceleration)
^NKG(-1, 0, -6, 1) = 1.0     // out-edge: node 0 -[pred 5]-> node 1
^NKG(-2, 1, -6, 0) = 1.0     // in-edge:  node 1 <-[pred 5]- node 0
^NKG(-3, 0) = 1               // degree of node 0
```

## Batch Rebuild

For existing data, rebuild both globals:

```python
from iris_vector_graph.schema import _call_classmethod
_call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')
```

`BuildKG()` now populates `^KG` then does a second pass to encode `^NKG`.

## Verifying ^NKG

```python
irispy = createIRIS(conn)

# Check metadata
node_count = irispy.get("^NKG", "$meta", "nodeCount")
version = irispy.get("^NKG", "$meta", "version")
print(f"Nodes: {node_count}, Version: {version}")

# Look up a node's integer index
idx = irispy.get("^NKG", "$NI", "MESH:D003920")
print(f"MESH:D003920 → index {idx}")

# Look up the string ID from an integer index
string_id = irispy.get("^NKG", "$ND", str(idx))
print(f"Index {idx} → {string_id}")
```

## What arno Does With ^NKG

arno's `ExportAdjacency()` reads `^NKG` and builds a Rust CSR (Compressed Sparse Row) matrix in memory. This enables:
- PageRank at 5-10x speed (SIMD-friendly vectorized rank updates)
- BFS at k=5 matching Neo4j (O(1) adjacency list access)
- ASQ structural pruning (master label set enables hop rejection without string comparison)

IVG owns the write path (`^NKG`). arno owns the read path (`ExportAdjacency`).
