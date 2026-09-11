# iris-vector-graph Domain Glossary

A platform graph database on InterSystems IRIS. Core abstraction: a **Graph** containing
**Structural Edges** (schema-free relationships between nodes) and **Temporal Edges**
(event-time metric/trace/span samples). Both kinds live in the same named graph, share
the same revision history, and can be written atomically.

---

## Core concepts

**Graph**
A named partition of the database. Every node, structural edge, temporal edge,
aggregate, and adjacency entry belongs to exactly one graph. The **default graph**
is identified by the empty string (`""`), stored internally as integer key `0`.
Graphs are not schemas — they carry no type constraints. They are isolation units.

**Graph Key**
The IRIS global subscript used to identify a graph. Always an integer (`0`) for the
default graph or a non-empty string for named graphs. Never the empty string — IRIS
does not support empty intermediate subscripts. Derived via
`$Select(graphId="": 0, 1: $ZStrip(graphId,"*C"))`.

**Structural Edge**
A typed, directed relationship between two nodes (`source -[predicate]→ target`),
optionally carrying qualifier key-value pairs. Lives in `rdf_edges` (SQL) and
`^KG("out"/"in")` (native adjacency). Subject to the revision ledger.

**Temporal Edge**
A timestamped measurement or event attached to a `(source, predicate, target)` triple.
Carries a `weight` (float) and optional `attrs` dict. Lives in `^KG("tout"/"tin")` and
associated aggregate globals. Subject to the revision ledger (via a Changeset temporal
op). The timestamp is event-time (when the event occurred), not ingestion-time.

**Named Graph**
A graph other than the default graph. Identified by a non-empty string (e.g.
`"acme-health"`). Provides storage-level isolation: `^KG("tout", "acme-health", ...)`
and `^KG("tout", "metro-hospital", ...)` are disjoint subtrees in the IRIS global
hierarchy.

**Adjacency Index**
The native IRIS global structure (`^KG("out"/"in"/"deg"/"degp")`) that enables
constant-time neighbor lookup and BFS. Always graph-scoped. Rebuilt from `rdf_edges`
SQL via `BuildKG`. Written live via `WriteAdjacency` on every structural edge write.

**Temporal Index**
The native IRIS global structure (`^KG("tout"/"tin"/"bucket"/"tagg"/"edgeprop")`) that
stores event-time edges and their pre-aggregated statistics. Always graph-scoped (post
spec-223). Written via `TemporalIndex.InsertEdge`. Read via `QueryWindow` and related
analytics methods.

**Revision**
An immutable, named snapshot of a set of mutations committed atomically. Identified by
a UUID `revision_id` and a monotonically increasing `seq`. Stored in
`^IVG.Ledger("rec", ...)` and projected to `Graph_KG.ledger_revisions` SQL. A revision
can include any mix of structural ops and temporal ops.

**Changeset**
The client-side accumulator of operations to be committed as one revision. Operations
include structural (`create_node`, `create_rel`, `set_prop`, etc.) and temporal
(`create_temporal_edge`). Submitted atomically to `engine.ledger.commit()`.

**Ledger**
The append-only revision history of a graph. Enables diff, reconstruct, and verify
across any time range. Implemented by `GraphLedger` (Python) and `Graph.KG.Ledger`
(ObjectScript). The ledger owns `TSTART/TCOMMIT` — it is the transaction boundary.

**Execution Context** _(planned — spec-224+)_
A session-scoped, immutable graph identity derived from an authenticated principal.
Prevents accidental cross-graph writes. Not yet implemented.

---

## Layout invariants

- **Graph key is always the first subscript** after the key name in every `^KG` global.
  `^KG("tout", graphKey, ts, s, p, o)` — not `^KG("tout", ts, graphKey, s, p, o)`.
- **`graphId=""` always maps to integer `0`** before use as a subscript.
- **No fallback**: a scoped lookup never falls through to the default graph. A missing
  or wrong graph key returns empty results, not cross-graph data.

---

## What is NOT in this glossary

Implementation details (subscript shapes, class names, SQL schemas, Python method
signatures) belong in specs and code, not here.
