# IVG: High-Performance Bulk Loader + Missing Index Fix + RDF 1.2 Reification

**From**: arno team
**Date**: 2026-03-31

---

## 1. BulkLoader — New General-Purpose Graph Loader

### What

New `iris_vector_graph/bulk_loader.py` (447 lines) — a `BulkLoader` class that
loads any NetworkX graph into IVG at **46,000+ rows/s**, replacing the existing
`engine.load_networkx()` path which tops out at ~100 rows/s on large graphs.

### Why the existing path is slow

The DDL-created `rdf_edges` table has 3 B-tree indexes plus a bitmap extent
index. At high cardinality (200K+ distinct nodes), every INSERT must probe all
index trees. The existing `schema.get_bulk_insert_sql()` makes this worse by
adding a `WHERE NOT EXISTS` correlated subquery per row.

| Method | Speed | Notes |
|--------|-------|-------|
| `engine.create_edge()` per-row | ~18 rows/s | FK check + index + commit |
| `get_bulk_insert_sql()` | ~100 rows/s | Correlated subquery per row |
| **BulkLoader** | **46,000 rows/s** | `%NOINDEX` + batch `%BuildIndices` |

### How it works

6-phase pipeline:

1. **Nodes** — `INSERT %NOINDEX %NOCHECK` into `nodes`
2. **Labels** — `INSERT %NOINDEX %NOCHECK` into `rdf_labels`
3. **Properties** — `INSERT %NOINDEX %NOCHECK` into `rdf_props`
4. **Edges** — `INSERT %NOINDEX %NOCHECK` into `rdf_edges`
5. **Index Rebuild** — `%BuildIndices` on all 4 classes (single-pass)
6. **Globals** — `Graph.KG.Traversal.BuildKG()` for `^KG`/`^NKG`

### Full NCIT benchmark (from-scratch load)

```
Graph: 204,296 nodes, 297,525 edges, 1,384,117 properties

Phase               Rows         Time      Throughput
─────────────────────────────────────────────────────
Nodes (204K)        204,296      2.0s      103,147 rows/s
Properties (1.38M)  1,384,117    24.6s     56,349 rows/s
Edges (297K)        297,492      6.4s      46,366 rows/s
%BuildIndices       all 4 tbls   2.6s      —
BuildKG()           ^KG + ^NKG   3.3s      —
─────────────────────────────────────────────────────
TOTAL               1,885,905    40.9s     45,893 rows/s
```

### Usage

```python
from iris_vector_graph.bulk_loader import BulkLoader

loader = BulkLoader(conn)
stats = loader.load_networkx(G, label_attr="namespace")
```

CLI:

```bash
python -m iris_vector_graph.bulk_loader /tmp/graph.pkl \
    --host localhost --port 1972 --namespace USER
```

### Post-load verification (all passed)

- UNIQUE constraint enforced (duplicate insert rejected)
- COUNT(*) == COUNT(1) on all 4 tables (bitmap extent intact)
- Indexed point lookups: 0.2-0.5ms
- Cross-index consistency: 20 random edges found identically via all indexes
- BFS/Subgraph/PPR traversals: 0.15-0.4ms
- `^NKG` metadata matches SQL edge count exactly

### Why NOT BenchSeeder.SeedRandom()

`BenchSeeder` writes **only** to `^KG`/`^NKG` globals — it does **not** write
SQL rows. This creates split-brain: the Python API queries SQL, so none of the
data would be visible. `BulkLoader` writes to SQL first, then rebuilds globals,
keeping both layers in sync.

### Bug in get_bulk_insert_sql()

`schema.py:269` — `get_bulk_insert_sql()` docstring says "Get INSERT statement
with %NOINDEX hint" but the actual SQL uses `WHERE NOT EXISTS` subqueries with
no `%NOINDEX`. Should be either renamed to `get_dedup_insert_sql()` or actually
use `%NOINDEX`.

---

## 2. Missing Indexes on rdf_edges

### The problem

`schema.py` defines 6 indexes for `rdf_edges` via `ensure_indexes()`, but the
live container only has 3:

| Index | Status | Columns |
|-------|--------|---------|
| `uspo` | present | `(s, p, oid)` UNIQUE |
| `idxedgesoid` | present | `(oid)` |
| `DDLBEIndex` | present | bitmap extent |
| `idx_edges_s` | **missing** | `(s)` |
| `idx_edges_p` | **missing** | `(p)` |
| `idx_edges_s_p` | **missing** | `(s, p)` |
| `idx_edges_p_oid` | **missing** | `(p, o_id)` |

### Root cause

Two classes map to the same `rdf_edges` SQL table:

- `Graph.KG.rdfedges` — DDL-created (owns storage, auto-generated hash global)
- `Graph.KG.Edge` — ObjectScript class with `SqlTableName = rdf_edges`

When `CREATE INDEX ... ON Graph_KG.rdf_edges(...)` runs, IRIS DDL resolves to
`Graph.KG.Edge` and fails:

```
[SQLCODE: <-400>:<Fatal error occurred>]
[Error: <<CLASS DOES NOT EXIST>Open+50^%apiDDL *Graph.KG.Edge>]
```

The DDL ALTER path cannot modify the ObjectScript-defined `Edge` class.

### Impact at current scale (297K edges): negligible

The `uspo` UNIQUE index on `(s, p, oid)` already serves lookups by `s` and by
`(s, p)` as prefix matches. Measured query times:

| Query | Min time | Index used |
|-------|----------|-----------|
| `WHERE s = ?` | 0.30ms | `uspo` prefix |
| `WHERE s = ? AND p = ?` | 0.28ms | `uspo` prefix |
| `WHERE o_id = ?` | 0.29ms | `idxedgesoid` |
| `WHERE p = ?` (TOP 10) | 0.52ms | skip-scan on `uspo` |
| `WHERE p = ? AND o_id = ?` | 0.29ms | skip-scan |
| `SELECT DISTINCT p` | 92ms | full index scan (inherent) |
| `COUNT(*)` | 0.25ms | `DDLBEIndex` bitmap |

All point queries are sub-millisecond. The missing indexes would start mattering
at 1M+ edges where skip-scan cost grows.

### Fix (for IVG team)

Add the indexes as ObjectScript `Index` definitions in `Graph.KG.Edge` instead
of via DDL. The DDL path will always fail because both `Edge` and `rdfedges`
claim `SqlTableName = rdf_edges`. Example:

```objectscript
/// In Graph.KG.Edge:
Index idxS On s;
Index idxP On p;
Index idxSP On (s, p);
Index idxPOid On (p, oId);
```

Then compile `Edge.cls` and run `do ##class(Graph.KG.Edge).%BuildIndices()`.

Alternatively, resolve the dual-class collision: either delete `Graph.KG.Edge`
(and keep the DDL class) or migrate fully to the ObjectScript class and drop the
DDL-created `Graph.KG.rdfedges`.

---

## 3. Other Changes in This Branch

| File | Change |
|------|--------|
| `iris_src/Graph/KG/Meta.cls` | Added `GetKG()` / `GetNKG()` for Python-side global reads |
| `iris_src/Graph/KG/ArnoAccel.cls` | `BumpVersion()` increments `^NKG("$meta","version")` |
| `iris_src/Graph/KG/BenchFormat.cls` | `Kill ^NKG` alongside `Kill ^KG` |
| `iris_src/Graph/KG/Loader.cls` | `Kill ^NKG` alongside `Kill ^KG` |
| `iris_src/Graph/KG/Subgraph.cls` | Em-dash → `--` in comments (encoding safety) |
| `iris_src/Graph/KG/Traversal.cls` | Em-dash → `--` in comments (encoding safety) |
| `iris_vector_graph/schema.py` | Added `nkg_built` capability detection |
| `iris_vector_graph/capabilities.py` | Minor cleanup |

---

## 4. RDF 1.2 Reification Enhancement — Separate Request

A separate, detailed implementation guide for **RDF 1.2 reification support**
has been prepared. This is the foundation for **KBAC (Knowledge-Based Access
Control)** — making authorization, provenance, confidence, and audit metadata
graph-traversable by attaching them to specific edges.

**Full document**: `arno/specs/034-niche-knowledge-graph/ivg-reification-request.md`
(975 lines, self-contained)

### Quick summary

- Adds **one new SQL table** (`rdf_reifications`) and **one new ObjectScript class**
- **Zero changes** to existing tables, `Edge.cls`, or `GraphIndex`
- Each reification row = a triple `(edge_id, predicate, value)` that makes a
  statement *about* an edge
- Enables KBAC: `(:e1, auth:readableBy, "analyst")`, `(:e1, prov:source, "PMID:12345")`
- W3C RDF 1.2 compliant (Working Draft, 28 March 2026)
- ~3 days estimated implementation effort
- Includes schema DDL, ObjectScript class, Python API additions, migration path,
  and test plan

### Why this matters

Today edge metadata lives in the `qualifiers` JSON column — opaque to SQL,
not indexable, not traversable. Reification promotes metadata to first-class
triples that can be queried, filtered, and traversed just like regular edges.
This is what makes KBAC possible: an agent's access to edge `e1` is determined
by traversing `(:e1, auth:readableBy, ?)` — a standard graph query, not a
special-case permission check.
