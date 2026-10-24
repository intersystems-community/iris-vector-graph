<!-- markdownlint-disable MD013 -->

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

**All-graphs read**
A read that names no graph (a Cypher query without `USE GRAPH`). It sees every graph,
and its answer is the union of each graph's own answer: a node is still its own graph's
node (the same node ID in two graphs is two nodes), its labels and properties are that
graph's, and an edge meets only nodes of its own graph. (Decided 2026-10-20.)

**Graph Key**
The IRIS global subscript used to identify a graph. Always an integer (`0`) for the
default graph or a non-empty string for named graphs. Never the empty string — IRIS
does not support empty intermediate subscripts. Derived via
`$Select(graphId="": 0, 1: $ZStrip(graphId,"*C"))`.

The ledger stores a second, incompatible form for the default graph (`$Char(1)` rather
than `0`). Both forms are derived from the `graphId`, never converted from each other:
a caller holding a `graphId` asks for the form belonging to the tree it is about to
touch. Using one tree's key on the other reads nothing and writes into a subtree no
reader scans.

**Structural Edge**
A typed, directed relationship between two nodes (`source -[predicate]→ target`),
optionally carrying qualifier key-value pairs. Lives in `rdf_edges` (SQL) and
`^KG("out"/"in")` (native adjacency). Subject to the revision ledger.

**Temporal Edge**
A timestamped measurement or event attached to a `(source, predicate, target)` triple.
Every Temporal Edge implies a **Structural Edge** for the same triple in the same graph:
the triple exists structurally whether or not it has timestamps, and is marked as having
come from a temporal write so counts and exports can tell them apart. Removing the
triple's last timestamp removes that Structural Edge only while it is still marked; an
explicit structural create of the same triple clears the mark. The marked Structural Edge
is not itself a ledger-governed edge (ADR-0009).
Carries a `weight` (float) and optional `attrs` dict. Lives in `^KG("tout"/"tin")` and
associated aggregate globals. Subject to the revision ledger (via a Changeset temporal
op). The timestamp is event-time (when the event occurred), not ingestion-time.

**Named Graph**
A graph other than the default graph. Identified by a non-empty string (e.g.
`"acme-health"`). Provides storage-level isolation: `^KG("tout", "acme-health", ...)`
and `^KG("tout", "metro-hospital", ...)` are disjoint subtrees in the IRIS global
hierarchy.

**Adjacency Index**
The native IRIS global structures (`^KG("out"/"in"/"label")`, the **Degree** cache
`^KG("gen"/"degc")`, and the interned `^NKG`) that enable constant-time neighbor lookup
and BFS. Always graph-scoped (`^NKG` excepted). Where it is **declared** on the edge and label tables, it is derived from the
rows: every row written, changed or deleted by any path (engine, SQL, Cypher DML, ledger,
bulk load) changes it in the same transaction, and it holds no entry without a row behind
it. Where it is not declared (a namespace without the deployed classes), only the engine's
own writers and a **rebuild** (`sync()`) bring it current, and any other write leaves it
**stale**.

**BM25 Index**
A named lexical-search index over the text properties of one graph's nodes, held in the
`^BM25Idx` global. It records, per term, which documents contain it and how often, and per
document, its length. Searched with Okapi BM25. Independent of the Adjacency Index.

**Document Frequency**
For a term, the number of documents in a BM25 Index that contain it. Kept incrementally,
so adding, replacing or removing a document costs in proportion to that document, never to
the index.

**Common-term cutoff**
An optional bound on a BM25 search: query terms whose Document Frequency is over a stated
fraction of the index are left out, because a term in nearly every document scores about zero
and costs a walk of its whole posting list. If every known term is over the bound the rarest is
kept, so a valid query never answers with nothing. The search reports which terms it left out.
Off unless asked for.

**Index Schema**
The version of a BM25 Index's stored layout. Schema 1 is the original; schema 2 adds Document
Frequency and a per-document token record. An index is **upgraded** from 1 to 2 by a single
pass; until then it can be searched but not written.

**Temporal Index**
The native IRIS global structure (`^KG("tout"/"tin"/"bucket"/"tagg"/"edgeprop")`, and
`^KG("tlast")`, each source's newest event) that stores event-time edges and their
pre-aggregated statistics. Always graph-scoped (post spec-223). Written via
`TemporalIndex.InsertEdge`. Read via `QueryWindow`, the paged window reader (a time
window of any size read page by page, each event with its attributes), and related
analytics methods.

**Collision**
A temporal write in mode `"insert"` at coordinates (graph, source, predicate, target,
timestamp) that already hold an edge. It changes nothing (the first write wins, and the
aggregates do not count it twice) and is counted, so the caller can see it. An upsert at
the same coordinates replaces the edge and is not a collision.

**Degree**
The number of outgoing edges of a node, or of one predicate on it. Counted from the
Adjacency Index when asked; never stored (decided in the 2026-10-12 architecture review,
because a stored counter cannot be kept exact inside a transaction and no reader needs
one). Parallel edges in a multigraph each count.

**Rebuilt store**
A derived store that is rebuilt from the authoritative tables instead of being kept
current on every write (the interned `^NKG` snapshot, the two-hop statistics, the Arno
snapshot, cached algorithm results). It records the Graph-Version Token it was built from.
It is **current** while that equals the live token and **stale** otherwise, so a failed
rebuild leaves it stale and every process gets the same answer.

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

**Erasure**
The removal of graph content from every store that holds it — relational rows, native
adjacency, temporal index, and live statements — as one atomic operation. Erasure
removes content, not history: the **Revision** records of an erased graph survive,
because a single revision may span several graphs and pruning it would destroy history
the caller did not ask to erase. What erasure guarantees is that no live statement
outlives its content. Erasure either completes or leaves the graph untouched; a partial
erasure is a defect, not an outcome. Implemented by the **Eraser**, which owns its own
`TSTART/TCOMMIT` boundary. The erased target's Revision records survive unless they are
**redacted**: `ledger.redact` turns them into **Tombstones** (ADR-0013).

**Redaction**
The removal of an erased target's content from the Ledger's history, keeping the
history's shape: every Revision and record position stays, only its content goes.
Refused while any of the target's content is live, so it always follows an **Erasure**.
It is itself recorded as a Revision that names nothing from the target (ADR-0013).

**Tombstone**
What a redacted Revision record becomes: it keeps its position and the kind of operation,
and a digest of the record it replaced, but no entity, value or graph. Replay, diff and
reconstruction skip it.

**Drift**
Disagreement between two stores about the same graph content — most often SQL rows
that exist without their adjacency entries, or adjacency entries that outlive their
rows. Drift makes traversal return nodes that no longer exist. Any write path that
touches one store without the other is a **bypass** and produces drift. A declared
Adjacency Index cannot drift from the rows; declaring it on a drifted install repairs it.

**Native executor**
The path that answers a read-only Cypher query inside IRIS, walking the Adjacency Index
directly. It takes only the reads it supports; every other query, and every write, takes
the **SQL path**. On by default.

**SQL path**
The path that translates a Cypher query to SQL over the graph tables. It answers
everything the native executor declines, and is the reference the native executor is
held to.

**Shadow mode**
A checking mode in which a query the native executor supports is answered by both paths
and their answers are compared; a difference is raised as an error, not returned.

**Plan shape**
A native plan with its parameter values taken out: its hops with their types and directions, each node's labels and
which properties it checks and how, and what is returned and grouped. Reads that differ only in the values they bind
have one shape.

**Compiled plan**
The walk of one Plan shape written out as code of its own and compiled in the namespace, so later reads of that shape
run it instead of the native executor's interpreter. It answers exactly as the interpreter does; a shape not yet
compiled, or that failed to compile, is interpreted. A deploy discards every Compiled plan.

**Execution Context** _(planned — spec-224+)_
A session-scoped, immutable graph identity derived from an authenticated principal.
Prevents accidental cross-graph writes. Not yet implemented.

**Owner**
The ObjectScript implementation of a graph algorithm (PageRank, PPR, WCC, CDLP,
subgraph, betweenness). It is the reference answer and the fallback whenever a Native
Reader is unavailable or has not been proven.

**Native Reader**
The Rust implementation of the same algorithm, exported by the Arno callout library and
reading the Adjacency Index directly. Used only for an algorithm whose Reader Revision
has met the minimum ivg has proven against the Owner.

**Reader Revision**
An integer the Arno callout library reports per Native Reader, bumped on any behaviour
fix to that reader. An absent reader means the build has none. Unrelated to a
**Revision** (ledger). ivg routes to a Native Reader by revision, never by library
version string.

**Proof Record**
`docs/arno-reader-proof.json`: per algorithm, the minimum Reader Revision whose Native
Reader matched its Owner on the gate's graphs, with the library build it was proven on.
Written by `scripts/arno_reader_gate.py`, never by hand. An algorithm absent from it
answers from its Owner.

**Graph-Version Token**
`^KG("__version")`: a namespace-wide counter incremented with `$Increment` by every
write path that can change what an algorithm answers. It never falls and is never reused.
A cached algorithm result is valid only while the token it was computed under is current.

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
