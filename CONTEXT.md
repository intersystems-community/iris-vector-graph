<!-- markdownlint-disable MD013 -->

# iris-vector-graph Domain Glossary

A platform graph database on InterSystems IRIS. Core abstraction: a **Graph** containing
**Structural Edges** (schema-free relationships between nodes) and **Temporal Edges**
(event-time metric/trace/span samples). Both kinds live in the same named graph, share
the same revision history, and can be written atomically.

---

## Core concepts

**Namespace**
The IRIS namespace IVG is installed in: its isolation unit. Everything IVG keeps outside
SQL (adjacency, ledger, settings) belongs to the namespace, so one namespace holds one
install, under one **IVG Schema**. Two tenants that must not share an index or a ledger
get two namespaces (ADR-0016).

**IVG Schema**
The one SQL schema in a namespace that holds every table, function, procedure and view
IVG creates; `Graph_KG` unless chosen otherwise at install, and fixed for the namespace
after that: the first setup records it, and an engine or deploy that asks for another is
refused (ADR-0016). Not a Graph: graphs partition the rows inside it.
_Avoid_: schema prefix (the setting that names it), SQLUser

**Graph**
A named partition of the database. Every node, structural edge, temporal edge,
aggregate, and adjacency entry belongs to exactly one graph. The **default graph** is the
one named by the empty string, the graph a write or read gets when it names none. Graphs
are not schemas — they carry no type constraints. They are isolation units.

**Named Graph**
A graph other than the default graph. Identified by a non-empty string (e.g.
`"acme-health"`). Provides storage-level isolation: two named graphs' content never
shares an index entry.

**All-graphs read**
A read that names no graph (a Cypher query without `USE GRAPH`). It sees every graph,
and its answer is the union of each graph's own answer: a node is still its own graph's
node (the same node ID in two graphs is two nodes), its labels and properties are that
graph's, and an edge meets only nodes of its own graph. (Decided 2026-10-20.)

**Graph Key**
The value that identifies a graph inside IVG's native storage, derived from its name.
The default graph's key is distinct from every name (the empty string cannot be used),
and it comes first in every graph-scoped index entry (ADR-0001). The ledger has a second,
incompatible key for the default graph. Both are derived from the graph's name, never
converted from each other: a caller holding a name asks for the key belonging to the
store it is about to touch. Using one store's key on the other reads nothing and writes
where no reader looks (ADR-0003). A scoped lookup never falls through to the default
graph: a wrong key reads nothing, never another graph's data.

**Structural Edge**
A typed, directed relationship between two nodes (`source -[predicate]→ target`),
optionally carrying qualifier key-value pairs. Lives in the edge table and the
**Adjacency Index**. Subject to the revision ledger.

**Temporal Edge**
A timestamped measurement or event attached to a `(source, predicate, target)` triple.
Every Temporal Edge implies a **Structural Edge** for the same triple in the same graph
(unless its predicate is a **Temporal-only predicate**):
the triple exists structurally whether or not it has timestamps, and is marked as having
come from a temporal write so counts and exports can tell them apart. Removing the
triple's last timestamp removes that Structural Edge only while it is still marked; an
explicit structural create of the same triple clears the mark. The marked Structural Edge
is not itself a ledger-governed edge (ADR-0009).
Carries a `weight` (float) and optional attributes. Lives in the **Temporal Index**.
Subject to the revision ledger (via a Changeset temporal op). The timestamp is event-time
(when the event occurred), not ingestion-time.

**Temporal-only predicate**
A predicate, per graph, whose temporal edges are written to the Temporal Index and
nowhere else: they imply no Structural Edge, which trades the structural view of those
triples for writes several times cheaper (ADR-0015).

**Multigraph mode**
A per-graph setting under which two nodes may hold several relationships of one type, as
openCypher's `CREATE` expects; outside it a second identical relationship adds nothing.
Parallel edges each count towards **Degree**.

**Adjacency Index**
The native index that gives constant-time neighbour lookup and breadth-first walks: each
graph's out- and in-edges and labels, and the **Degree** cache. Always graph-scoped. Where
it is **declared** on the edge and label tables, it is derived from the rows: every row
written, changed or deleted by any path (engine, SQL, Cypher DML, ledger, bulk load)
changes it in the same transaction, and it holds no entry without a row behind it. Where it
is not declared (a namespace without the deployed classes), only the engine's own writers
and a **rebuild** bring it current, and any other write leaves it **stale**.

**Interned snapshot**
`^NKG` in the guides: a **Rebuilt store** holding every graph's edges together, with each
node ID numbered once, which the fastest algorithm paths read. It has no graph dimension:
a node ID used in two graphs is one node there with both graphs' edges, so it gives the
default graph's answer only while no named graph has an edge.

**Degree**
The number of outgoing edges of a node, or of one predicate on it. Counted from the
Adjacency Index when asked; never stored (decided in the 2026-10-12 architecture review,
because a stored counter cannot be kept exact inside a transaction and no reader needs
one). Parallel edges in a multigraph each count.

**Rebuilt store**
A derived store that is rebuilt from the authoritative tables instead of being kept
current on every write (the **Interned snapshot**, the two-hop statistics, the Arno
snapshot, cached algorithm results). It records the **Graph-Version Token** it was built
from. It is **current** while that equals the live token and **stale** otherwise, so a
failed rebuild leaves it stale and every process gets the same answer.

**Graph-Version Token**
A namespace-wide counter that every write path able to change what an algorithm answers
increments. It never falls and is never reused. A cached algorithm result is valid only
while the token it was computed under is current.

**Drift**
Disagreement between two stores about the same graph content — most often SQL rows
that exist without their adjacency entries, or adjacency entries that outlive their
rows. Drift makes traversal return nodes that no longer exist. Any write path that
touches one store without the other is a **bypass** and produces drift. A declared
Adjacency Index cannot drift from the rows; declaring it on a drifted install repairs it.

---

## Search

**BM25 Index**
A named lexical-search index over the text properties of one graph's nodes. It records,
per term, which documents contain it and how often, and per document, its length.
Searched with Okapi BM25. Independent of the Adjacency Index.

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

**Embedding Registry**
The record of each graph's embedding identity: which model (its **model key**) and which
vector width the graph's node vectors were made with. A write at another width or model
is refused rather than mixed in (ADR-0006).

**Quarantine**
Where vectors go that an upgrade could not place in a graph. No search reaches them
until an operator places each one, naming its graph.

---

## Time

**Temporal Index**
The native index that stores event-time edges and their pre-aggregated statistics, and
each source's newest event. Always graph-scoped. Read by a paged window reader (a time
window of any size read page by page, each event with its attributes) and related
analytics.

**Collision**
A temporal write in mode `"insert"` at coordinates (graph, source, predicate, target,
timestamp) that already hold an edge. It changes nothing (the first write wins, and the
aggregates do not count it twice) and is counted, so the caller can see it. An upsert at
the same coordinates replaces the edge and is not a collision.

---

## History

**Revision**
An immutable, named snapshot of a set of mutations committed atomically. Identified by
a UUID and a monotonically increasing sequence number. A revision can include any mix of
structural ops and temporal ops.

**Changeset**
The client-side accumulator of operations to be committed as one revision: structural
ones (nodes, relationships, properties) and temporal ones. Submitted to the ledger
atomically.

**Ledger**
The append-only revision history of a graph (the guides' "revision ledger"). Enables
diff, reconstruct, and verify across any time range. The ledger owns the transaction
boundary of a commit.

**Erasure**
The removal of graph content from every store that holds it — relational rows, native
adjacency, temporal index, and live statements — as one atomic operation. Erasure
removes content, not history: the **Revision** records of an erased graph survive,
because a single revision may span several graphs and pruning it would destroy history
the caller did not ask to erase. What erasure guarantees is that no live statement
outlives its content. Erasure either completes or leaves the graph untouched; a partial
erasure is a defect, not an outcome. It owns its own transaction boundary. The erased
target's Revision records survive unless they are **redacted**, which turns them into
**Tombstones** (ADR-0004, ADR-0013).

**Redaction**
The removal of an erased target's content from the Ledger's history, keeping the
history's shape: every Revision and record position stays, only its content goes.
Refused while any of the target's content is live, so it always follows an **Erasure**.
It is itself recorded as a Revision that names nothing from the target (ADR-0013).

**Tombstone**
What a redacted Revision record becomes: it keeps its position and the kind of operation,
and a digest of the record it replaced, but no entity, value or graph. Replay, diff and
reconstruction skip it.

---

## Query execution

**Native executor**
The path that answers a read-only Cypher query inside IRIS, walking the Adjacency Index
directly (the README's "native Cypher"). It takes only the reads it supports; every other
query, and every write, takes the **SQL path**. On by default.

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
compiled, or that failed to compile, is interpreted. A deploy discards every Compiled plan (ADR-0014).

**Execution Context** _(planned)_
A session-scoped, immutable graph identity derived from an authenticated principal.
Prevents accidental cross-graph writes. Not yet implemented.

---

## Algorithms

**Owner**
IVG's own implementation of a graph algorithm (PageRank, PPR, WCC, CDLP, subgraph,
betweenness), running in IRIS. It is the reference answer and the fallback whenever a
**Native Reader** is unavailable or has not been proven.

**Arno**
An optional native library, loaded into IRIS processes, that holds the **Native Readers**
and some accelerated graph walks. IVG answers the same without it, more slowly; it is
loaded per process.

**Native Reader**
The same algorithm implemented in **Arno**, reading the Adjacency Index directly. Used
only for an algorithm whose **Reader Revision** has met the minimum IVG has proven
against the Owner.

**Reader Revision**
An integer Arno reports per Native Reader, bumped on any behaviour fix to that reader.
An absent reader means the build has none. Unrelated to a **Revision** (ledger). IVG
routes to a Native Reader by revision, never by library version string.

**Proof Record**
The record, shipped with IVG, of the minimum Reader Revision per algorithm whose Native
Reader matched its Owner on the proof gate's graphs, with the library build it was proven
on. Written by the gate, never by hand. An algorithm absent from it answers from its
Owner.

---

## Upgrades

**Legacy objects**
The SQL functions, views and classes a 4.5.x install created outside the IVG Schema.
4.6.0 leaves them in place, frozen and unused by IVG, so SQL that calls them keeps
working; they are listed from a fixed manifest of what 4.5.x created, never by name
pattern, and removed only when asked (ADR-0016).

---

## What is NOT in this glossary

Implementation details (globals and their subscripts, class names, SQL schemas, Python
method signatures, files) belong in specs, ADRs and code, not here. The globals' layout
is in `docs/architecture/ARCHITECTURE.md` and ADR-0001.
