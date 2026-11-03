# Index-Maintenance Architecture Review

**Date:** 2026-06-19
**Scope:** How `Graph_KG.rdf_edges` (SQL, source of truth) stays consistent with
the `^KG` / `^NKG` acceleration globals (BFS, centrality, var-length Cypher).
**Trigger:** Revisiting the original "functional index keeps globals in sync
automatically" design, which was believed abandoned because functional indexes
can't run Python.

---

## TL;DR

- The functional index (`Graph.KG.GraphIndex`, declared on `Graph.KG.Edge`)
  **was never deployed and is dead code.** `rdf_edges` is a plain DDL table with
  ordinary SQL indexes — no functional index, no triggers. Verified on both the
  community (`iris_vector_graph`) and enterprise (`ivg-iris-enterprise`)
  containers: `%Dictionary.CompiledClass.%ExistsId("Graph.KG.Edge") = 0`.
- It also had a **layout bug**: it wrote `^KG("out", s, p, o)` (no shard) while
  every live reader uses `^KG("out", 0, s, p, o)` (shard-0). Fixed in this review
  so the source reflects the real layout, but the index remains undeployed.
- **Globals are maintained explicitly in Python/ObjectScript**, three ways:
  per-row (`create_edge` → `WriteAdjacency`), per-batch
  (`BulkIngestEdgesSQL`), and full rebuild (`sync()` → `BuildKG`/`BuildNKG`).
- **The deferred (rebuild-once) strategy is correct and was measured to be
  6.5×–14.9× faster than per-row maintenance**, with the gap widening at scale.
  A live functional index would impose exactly the per-row cost. **Do not adopt
  one for the bulk path.**
- The real risk is not coverage but **silent drift from BYPASS write paths**
  (raw SQL, `drop_graph`, `delete_node`, the SQL-table bridge, temporal edges).
  This review adds `engine.verify_sync()` + dirty-flagging to detect/repair it.
- **Testing gap:** the existing suite asserted that `sync()` *calls* its
  sub-methods (mock plumbing) but never that globals actually match SQL after a
  write. New E2E tests now exercise the real invariant.

---

## 1. What's actually wired up

| Component                                                     | Source says                                      | Live database says                                                                                                   |
| ------------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `Graph.KG.Edge` persistent class (`SqlTableName = rdf_edges`) | defines table + `GraphIdx` functional index      | **not compiled** — table is DDL-created auto-class                                                                   |
| `GraphIdx ... As Graph.KG.GraphIndex`                         | functional index maintaining `^KG`/`^NKG`        | **absent** from the compiled table; only B-tree/bitmap indexes + a UNIQUE(s,p,o,graph_id) + a FOREIGN KEY to `nodes` |
| `^KG("out", 0, s, p, o)`                                      | written by `WriteAdjacency`, `BuildKG`           | **populated** (the layout all readers use)                                                                           |
| `^KG("out", s, p, o)` (no shard)                              | written by `GraphIndex`, `Loader`, bench classes | **empty** on live DB                                                                                                 |

So the picture the user remembered is correct: the functional-index strategy was
dropped, and the live system maintains globals by explicit calls.

### The three real maintenance mechanisms

1. **Per-row (incremental).** `create_edge` / `delete_edge` /
   `set_edge_weight` call `EdgeScan.WriteAdjacency` / `DeleteAdjacency` right
   after the SQL write.
2. **Per-batch (incremental).** `EdgeScan.BulkIngestEdgesSQL` does SQL insert +
   `WriteAdjacency` in one ObjectScript loop and sets `_nkg_dirty`.
3. **Full rebuild (batch reconciliation).** `engine.sync()` →
   `TraversalBuild.BuildKG` does `Kill ^KG` + full cursor rebuild from SQL, then
   `BuildNKG`. `bulk_load_session` defers to this once at the end (with a drift
   check) instead of paying per-row cost.

The "optional batch mode so incremental updates don't slow things down" the user
wanted **already exists**: `bulk_load_session(incremental=…)`.

---

## 2. The drift gaps (this is the real problem)

Every *documented* write path maintains the globals. The exposure is paths that
write `rdf_edges` (or delete from it) **without** touching `^KG`/`^NKG`:

| Path                                | Class                           | Risk                                                                                                                                                                                                         |
| ----------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Raw `INSERT/DELETE` on `rdf_edges`  | external ETL, SQL bridge        | **BYPASS** — globals never updated                                                                                                                                                                           |
| `drop_graph(graph_id)`              | `nodes_edges.py`                | deletes SQL rows, left globals stale → *fixed: now sets `_nkg_dirty`*                                                                                                                                        |
| `delete_node` / `bulk_delete_nodes` | `nodes_edges.py`                | deleted edges remained in globals → *fixed: now sets `_nkg_dirty`*                                                                                                                                           |
| `create_edge_temporal`              | `temporal.py` / `TemporalIndex` | writes `^KG("tout"/"tin")` **and** a shadow `^KG("out",0,…)`, but **not** `^NKG(-1,…)` — temporal edges are visible to `^KG` readers but invisible to the NKG-accelerated (arno/Rust) BFS until a `BuildNKG` |

The FOREIGN KEY on `rdf_edges`→`nodes` is the *only* built-in SQL guard: it
prevents orphan edges, but does nothing for `^KG`/`^NKG` consistency.

### Mitigation added in this review

`engine.verify_sync(heal=False)` → `SyncReport`:

- Compares `COUNT(*) FROM rdf_edges` against the `^NKG` edge count.
- Treats `_nkg_dirty` as the authoritative "writes happened without sync"
  signal (BYPASS deletes now set it).
- The count comparison is **one-directional** (flags only SQL > globals): `^NKG`'s
  meta `edgeCount` over-counts because `InternNode`/`InternLabel` are append-only
  (never decremented on delete) and NKG interning ignores `graph_id` while the
  SQL UNIQUE includes it. We do not flag globals > SQL to avoid false positives.
- `heal=True` runs `sync()` to rebuild.

Usage: `if not engine.verify_sync(): engine.sync()` — or call it on a schedule
after external-ETL windows.

> **A documented contract followed from this:** raw SQL writers and the SQL
> table bridge must call `engine.sync()` (or `verify_sync(heal=True)`) after
> writing. There is no automatic propagation.

---

## 3. Should we adopt a live functional index? — Measured answer: no

A functional index fires its `InsertIndex` callback **once per inserted row** —
identical in cost to the per-row `WriteAdjacency` that `create_edge` already
pays. We benchmarked the two strategies the engine already supports as a faithful
proxy (`benchmarks/bench_index_maintenance_strategy.py`, enterprise container):

| Edges   | Deferred (bulk insert + 1 rebuild) | Per-row (== functional index) | Ratio            |
| ------- | ---------------------------------- | ----------------------------- | ---------------- |
| 20,000  | 0.98 s (20,429 edges/s)            | ~6 s (3,128 edges/s)          | **6.5× slower**  |
| 100,000 | 2.16 s (46,306 edges/s)            | ~32 s (3,105 edges/s)         | **14.9× slower** |

The ratio **widens with scale**: deferred is O(1) global rebuilds amortized over
a single bulk insert; per-row is O(n) server round-trips. This decisively
falsifies any case for a live functional index on the bulk-load path and
quantifies why the original deferral instinct was right.

A functional index *could* make sense for the pure-OLTP path (occasional single
`create_edge`) since it would remove the "forgot to sync" footgun — but:

1. `create_edge` already maintains globals inline, so there's no correctness gain.
2. It would require migrating `rdf_edges` from a DDL table to the
   `Graph.KG.Edge` persistent class (or `ALTER TABLE ADD INDEX … AS …`), a real
   migration with bulk-load implications.
3. The bulk path would need `%NOINDEX`-style deferral anyway (the
   `SortBegin`/`SortEnd` stubs in `GraphIndex` are no-ops today).

**Recommendation: keep explicit maintenance + the new drift detector. Don't
deploy the functional index.** The shard-layout fix is retained only so the
source is honest and a future experiment isn't starting from a broken baseline.

---

## 4. Fundamental redesign options (beyond the immediate fixes)

Ordered by value-to-risk. These address maintainability and the design smells the
review surfaced, not just the bugs.

### 4.1 — Collapse the `^KG` layout fork (low risk, high clarity)

Three coexisting layouts (`^KG("out",0,…)` shard-0, `^KG("out",…)` no-shard,
`^NKG(-1,…)` interned) with no single documented owner. The no-shard layout is
used only by benchmark/loader classes and the (dead) functional index.

**Action:** pick shard-0 as the canonical adjacency layout; migrate
`BenchSeeder`/`BenchFormat`/`ArnoAccel`/`Loader` to it (or delete the bench-only
ones); make `GraphIndex` and `WriteAdjacency` provably identical (the layout fix
in this review is step one). Add a single doc-comment "layout of record" in
`EdgeScan.cls`. Removes a whole class of "why is my edge invisible" bugs.

### 4.2 — One maintenance entry point (medium risk, high maintainability)

Today the "write an edge to SQL + globals" logic is duplicated across
`create_edge` (Python), `BulkIngestEdgesSQL` (ObjectScript), `bulk_ingest_edges`
(Python fallback), and `TemporalIndex.InsertEdge`. They drift (e.g. temporal
writes the `^KG` shadow but not `^NKG`; the fallback skips globals entirely).

**Action:** funnel all edge writes through a single ObjectScript
`EdgeWriter.Write(s,p,o,opts)` that owns *both* the SQL insert and the chosen
global-maintenance policy (inline vs deferred), with one place that decides
whether `^NKG` is touched. Python becomes a thin caller. This is the
"strategy pattern" the system is implicitly missing.

### 4.3 — Unify temporal and adjacency index families (larger)

Temporal edges live in a parallel universe (`^KG("tout"/"tin")`, buckets,
aggregates) and only partially bridge to adjacency. A graph algorithm cannot
"see" a temporal edge in `^NKG` without a rebuild.

**Action (design choice, not yet warranted):** either (a) make temporal a
*view* over the same canonical adjacency plus a time index, so there's one edge
store with a temporal facet; or (b) formally document temporal as a separate
store with an explicit `materialize_temporal_to_adjacency()` step. Today it's
ambiguously in-between, which is the worst place to be.

### 4.4 — Make the drift detector persistent + cheap (incremental)

`_nkg_dirty` is in-memory and per-process: it's lost on reconnect, and a second
client never learns the first one's writes bypassed sync. The count walk is the
durable fallback but `^NKG` meta over-counts.

**Action:** persist a monotonic "last synced edge version" in `^NKG("$meta")`
and compare against a SQL `MAX(edge_id)` / row version on connect; add a true
`KGEdgeCountExact` that walks s→p→o (the current `KGEdgeCount` counts subjects,
not edges). Then `verify_sync` has an exact oracle instead of a heuristic.

### 4.5 — Address the append-only interning leak (correctness hygiene)

`InternNode`/`InternLabel` never shrink on delete, so `$ND`/`$NI` and the
`nodeCount`/`edgeCount` meta grow unbounded across a delete-heavy workload, which
is *why* the count oracle is unreliable. A full `BuildNKG` resets it, but nothing
incremental does.

**Action:** either accept it and rely on periodic rebuild (document it), or add
tombstone/compaction to the interning maps. Low urgency, but it's the root cause
of the count-drift noise.

---

## 5. Is our testing sufficient? — No, and here's the specific gap

The existing index tests (`test_sync_model.py`) are **mock-only**: they assert
`sync()` *calls* `_sync_kg`/`_sync_nkg` (both mocked to return `True`), that
signatures carry an `auto_sync` param, and that deprecated methods warn. **None
assert the actual invariant** — that after a write the globals match SQL. That's
exactly why the layout fork, the undeployed functional index, and the live
110-vs-108 drift all went unnoticed: nothing tested the property, only the
plumbing.

This is a structural pattern worth correcting project-wide: heavy mock coverage
of *call wiring* with thin coverage of *behavioral invariants on a live engine*.
The new `tests/integration/test_index_consistency_e2e.py` is the template — seed,
mutate via a bypass, assert the detector catches it, heal, assert convergence. A
mock test could never have found the drift; the live test does in 12 seconds.

**Recommendation:** for every "index/global is maintained" claim, require one
live E2E asserting the invariant, not just a mock asserting the method fired.

---

## Artifacts produced by this review

- `iris_src/src/Graph/KG/GraphIndex.cls` — shard-layout fix (Insert/DeleteIndex).
- `iris_vector_graph/status.py` — `SyncReport` dataclass.
- `iris_vector_graph/_engine/admin.py` — `engine.verify_sync(heal=…)`.
- `iris_vector_graph/_engine/nodes_edges.py` — `_nkg_dirty` flagging on
  `drop_graph` / `delete_node` / `bulk_delete_nodes` (BYPASS paths).
- `tests/unit/test_drift_detector.py` — 13 unit tests.
- `tests/integration/test_index_consistency_e2e.py` — 4 live-invariant E2E tests.
- `benchmarks/bench_index_maintenance_strategy.py` + JSON results.
