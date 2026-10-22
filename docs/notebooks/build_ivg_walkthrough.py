"""Build ``ivg_walkthrough.ipynb``: a guided tour of what a graph database is and how iris-vector-graph builds one.

    .venv/bin/python docs/notebooks/build_ivg_walkthrough.py            # write the notebook
    .venv/bin/python docs/notebooks/build_ivg_walkthrough.py --execute  # write it and run every cell, outputs saved

The notebook runs against its own namespace (``IVGDEMO`` by default) and erases that namespace's graph at the start,
so never point ``IVG_NAMESPACE`` at a namespace whose data matters.
"""

from __future__ import annotations

import sys
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
OUT = HERE / "ivg_walkthrough.ipynb"

cells = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip("\n")))


md(r"""
# iris-vector-graph: a walkthrough

What a graph database is, how iris-vector-graph (IVG) builds one on InterSystems IRIS, and the three parts opsreview
uses: Cypher, temporal edges and the ledger. Every cell runs against a live IRIS and prints what IRIS holds afterwards.

| Part | What it shows |
| ---- | ------------- |
| 1 | Nodes, labels, properties and relationships: a small opsreview-shaped graph |
| 2 | Asking it questions in Cypher, and the SQL the same question needs |
| 3 | How IVG stores the graph: four SQL tables, and the `^KG` adjacency global derived from them |
| 4 | How a query runs: the native executor over `^KG`, or the SQL path, and why the first is faster |
| 5 | Time: temporal edges, the event store pattern opsreview uses (window reads, buckets, retention) |
| 6 | The ledger: every governed write as a revision you can list, diff and verify |
| 7 | Vectors, briefly |
| 8 | A map of the code, for reading it next |

**Before running**

```bash
scripts/enterprise-container.sh up          # the ivg-iris-enterprise container (SQL port 31972)
.venv/bin/jupyter lab docs/notebooks/ivg_walkthrough.ipynb
```

The notebook uses the `IVGDEMO` namespace, which has its own database and its own copy of IVG's ObjectScript classes.
To make it on a fresh container, see `docs/notebooks/build_ivg_walkthrough.py` (the `IVGDEMO` recipe at the end of this
notebook). It erases `IVGDEMO`'s graph first, so it can be run again from the top.
""")

code(r"""
import os, json, time, random, logging
import iris
import pandas as pd
from iris_vector_graph.engine import IRISGraphEngine

# IVG warns about two things that do not apply here: a namespace other than USER (IVGDEMO has its own ^KG), and the
# optional Rust accelerator (Arno), which this container does not load
logging.getLogger("iris_vector_graph").setLevel(logging.ERROR)

HOST = os.environ.get("IVG_HOST", "localhost")
PORT = int(os.environ.get("IVG_PORT", "31972"))
NAMESPACE = os.environ.get("IVG_NAMESPACE", "IVGDEMO")

conn = iris.connect(HOST, PORT, NAMESPACE, "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=8)
native = iris.createIRIS(conn)          # the Native API: globals and class methods, alongside SQL on the same connection
# a clean start, so the notebook can run again from the top: the ledger's history (part 6), then the graph
native.classMethodValue("Graph.KG.Ledger", "PurgeAll")
print("erased", engine.erase_all(), "rows from", NAMESPACE)

def q(cypher, **params):
    # Run a Cypher query and show its rows as a table.
    r = engine.execute_cypher(cypher, params)
    if r.error:
        raise RuntimeError(r.error)
    return pd.DataFrame(r.rows, columns=r.columns)

def sql(text, *args):
    cur = conn.cursor()
    cur.execute(text, list(args))
    cols = [d[0] for d in cur.description]
    return pd.DataFrame([list(row) for row in cur.fetchall()], columns=cols)
""")

md(r"""
## 1. What is a graph database?

A graph database stores **things** and the **connections between them**, and treats the connections as data in their
own right.

- A **node** is a thing: a customer, an IRIS instance, a finding. Its **id** names it.
- A **label** says what kind of thing it is (`Customer`, `Instance`, `Finding`). A node can have several.
- **Properties** are its key/value facts (`version: "2025.1"`, `severity: "high"`).
- A **relationship** (an edge) connects two nodes. It has a **type** (`RUNS`, `ABOUT`) and a **direction**, and it can
  carry properties of its own.

```
(acme:Customer) -[:RUNS]-> (prod1:Instance) <-[:ABOUT]- (f1:Finding {severity: "high"})
                                  |
                              [:MIRRORS]
                                  v
                           (prod2:Instance)
```

Why bother, when a relational table can hold the same facts? Because the questions are about **paths**: "which
customers have an instance whose mirror partner has a high-severity finding?" In SQL every hop is another join, and you
write the joins yourself. In a graph database you draw the path, and the engine follows each node's connections
directly instead of searching a table for them.

Below is a small opsreview-shaped graph: two customers, their instances, the components on them, mirror partnerships,
and findings about the instances.
""")

code(r"""
customers = {"acme": "Acme Health", "globex": "Globex Labs"}
instances = {  # id: (customer, version, role)
    "acme-prod-1": ("acme", "2025.1", "primary"),
    "acme-prod-2": ("acme", "2025.1", "backup"),
    "acme-test-1": ("acme", "2026.1", "test"),
    "globex-prod-1": ("globex", "2024.3", "primary"),
    "globex-prod-2": ("globex", "2024.3", "backup"),
    "acme-dr-1": ("acme", "2025.1", "disaster recovery"),
}
components = ["IRIS", "WebGateway", "Mirror", "Interoperability"]
findings = [  # (id, about, severity, title)
    ("f1", "acme-prod-1", "high", "Journal space below 10%"),
    ("f2", "acme-prod-1", "medium", "License use above 80%"),
    ("f3", "acme-prod-2", "high", "Mirror member behind by 40 s"),
    ("f4", "globex-prod-1", "low", "Old TLS cipher enabled"),
    ("f5", "globex-prod-2", "high", "Database expansion failed"),
]

for cid, name in customers.items():
    engine.create_node(f"customer:{cid}", labels=["Customer"], properties={"name": name})
for iid, (cid, version, role) in instances.items():
    engine.create_node(f"instance:{iid}", labels=["Instance"], properties={"name": iid, "version": version, "role": role})
    engine.create_edge(f"customer:{cid}", "RUNS", f"instance:{iid}")
for comp in components:
    engine.create_node(f"component:{comp}", labels=["Component"], properties={"name": comp})
for iid in instances:
    for comp in (components if "prod" in iid else components[:2]):
        engine.create_edge(f"instance:{iid}", "HAS_COMPONENT", f"component:{comp}")
engine.create_edge("instance:acme-prod-1", "MIRRORS", "instance:acme-prod-2")
engine.create_edge("instance:acme-prod-2", "MIRRORS", "instance:acme-dr-1")
engine.create_edge("instance:globex-prod-1", "MIRRORS", "instance:globex-prod-2")
for fid, about, severity, title in findings:
    engine.create_node(f"finding:{fid}", labels=["Finding"], properties={"severity": severity, "title": title})
    engine.create_edge(f"finding:{fid}", "ABOUT", f"instance:{about}")

q("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY label")
""")

md(r"""
Reading the graph back and drawing it. Each edge is one relationship, coloured by its type.
""")

code(r"""
import networkx as nx
import matplotlib.pyplot as plt

edges = q("MATCH (a)-[r]->(b) RETURN a.node_id AS a, type(r) AS t, b.node_id AS b")
G = nx.DiGraph()
for a, t, b in edges.itertuples(index=False):
    G.add_edge(a.split(":", 1)[1], b.split(":", 1)[1], t=t)
colours = {"RUNS": "tab:blue", "HAS_COMPONENT": "tab:gray", "MIRRORS": "tab:green", "ABOUT": "tab:red"}
plt.figure(figsize=(11, 7))
pos = nx.spring_layout(G, seed=7, k=0.9)
nx.draw_networkx(G, pos, node_color="#dde6f0", node_size=1700, font_size=8,
                 edge_color=[colours[G.edges[e]["t"]] for e in G.edges], arrowsize=14)
plt.legend(handles=[plt.Line2D([0], [0], color=c, label=t) for t, c in colours.items()], loc="lower left")
plt.axis("off"); plt.show()
""")

md(r"""
## 2. Asking questions in Cypher

Cypher describes the shape you are looking for with ASCII art: `(a)` is a node, `-[:TYPE]->` a relationship, and
`WHERE`, `RETURN`, `ORDER BY` work as in SQL. IVG speaks openCypher (it passes the openCypher TCK, 3,896 of 3,896
scenarios).

**One hop:** each customer and the instances it runs.
""")

code(r"""
q("MATCH (c:Customer)-[:RUNS]->(i:Instance) RETURN c.name AS customer, i.name AS instance, i.role AS role "
  "ORDER BY customer, instance")
""")

md(r"""
**An aggregate:** findings per instance, worst first. Grouping is implicit: every non-aggregate in `RETURN` is a key.
""")

code(r"""
q("MATCH (f:Finding)-[:ABOUT]->(i:Instance) "
  "RETURN i.name AS instance, count(f) AS findings, collect(f.severity) AS severities ORDER BY findings DESC, instance")
""")

md(r"""
**Two hops with a filter and a parameter:** the customers with a high-severity finding on any instance. `$severity` is
a parameter, passed separately like a SQL bind.
""")

code(r"""
q("MATCH (c:Customer)-[:RUNS]->(i:Instance)<-[:ABOUT]-(f:Finding) WHERE f.severity = $severity "
  "RETURN c.name AS customer, i.name AS instance, f.title AS finding ORDER BY customer, instance",
  severity="high")
""")

md(r"""
**A path of any length:** which instances does a failure of `acme-prod-1` reach through mirror partnerships, and how
far? `*1..3` means one to three `MIRRORS` hops. In SQL this is a recursive CTE.
""")

code(r"""
q("MATCH p = (a:Instance {name: $name})-[:MIRRORS*1..3]->(b:Instance) "
  "RETURN b.name AS reaches, length(p) AS hops ORDER BY hops", name="acme-prod-1")
""")

md(r"""
**The same question in SQL.** IVG can answer most Cypher with SQL over its tables (part 3). With the native executor
turned off, the result carries the SQL it ran: one join per hop, plus the label and property tables for every filter.
This is the query you would otherwise write by hand.
""")

code(r"""
engine.native_cypher = False
r = engine.execute_cypher(
    "MATCH (c:Customer)-[:RUNS]->(i:Instance)<-[:ABOUT]-(f:Finding) WHERE f.severity = 'high' "
    "RETURN c.name AS customer, count(f) AS high_findings")
engine.native_cypher = True
print(r.sql)
print(r.rows)
""")

md(r"""
## 3. How IVG stores a graph

IVG keeps the graph in **four ordinary SQL tables** in the `Graph_KG` schema. They are the source of truth: you can
query, back up and govern them as any other IRIS table.

| Table | One row per | Columns that matter |
| ----- | ----------- | ------------------- |
| `Graph_KG.nodes` | node | `node_id`, `graph_id` |
| `Graph_KG.rdf_labels` | label of a node | `s` (the node), `label` |
| `Graph_KG.rdf_props` | property of a node | `s`, `key`, `val` |
| `Graph_KG.rdf_edges` | relationship | `s` (source), `p` (type), `o_id` (target), `qualifiers` (its properties as JSON) |

The `rdf_` names come from RDF, where a fact is a triple (subject, predicate, object). `graph_id` lets one namespace
hold several named graphs; empty means the default graph. Here is `acme-prod-1` across the four tables:
""")

code(r"""
node = "instance:acme-prod-1"
display(sql("SELECT node_id, graph_id FROM Graph_KG.nodes WHERE node_id = ?", node))
display(sql("SELECT s, label FROM Graph_KG.rdf_labels WHERE s = ?", node))
display(sql('SELECT s, "key", val FROM Graph_KG.rdf_props WHERE s = ?', node))
display(sql("SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ? ORDER BY p", node, node))
""")

md(r"""
### The adjacency global `^KG`

Tables answer "find the rows where s = X" through an index: a B-tree search for every hop. A graph engine wants
something more direct: from a node, its neighbours, immediately. IVG keeps that in an IRIS **global**, `^KG`: a sorted,
persistent, multi-dimensional array, the storage layer IRIS itself is built on.

```
^KG("out", graph, source, type, target) = weight      every outgoing edge
^KG("in",  graph, target, type, source) = weight      the same edge, from the other end
^KG("label", graph, label, node)        = ""          members of a label
```

Following an edge is reading the next subscript: `$Order` over `^KG("out", g, "instance:acme-prod-1", "MIRRORS", "")`
lists that node's mirror partners without touching a table. This is the "index-free adjacency" graph databases are
known for.

`^KG` is **derived from the tables**: a functional index on `rdf_edges` and `rdf_labels` updates it inside the same
transaction as every insert, update and delete, whether it comes from IVG, SQL or anything else. So it is never stale
and never needs a rebuild after ordinary writes.

Reading it with the Native API:
""")

code(r"""
gkey = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", "")   # the subscript for the default graph

def out_edges(node):
    rows = []
    for ptype, _ in native.iterator("KG", "out", gkey, node).items():
        for target, weight in native.iterator("KG", "out", gkey, node, ptype).items():
            rows.append((node, ptype, target, weight))
    return pd.DataFrame(rows, columns=["source", "type", "target", "weight"])

out_edges("instance:acme-prod-1")
""")

md(r"""
Writing a row moves `^KG` with it, and deleting it removes the entry. Neither call mentions `^KG`:
""")

code(r"""
engine.create_edge("instance:acme-test-1", "MIRRORS", "instance:acme-prod-1")
print("after insert:", native.isDefined("KG", "out", gkey, "instance:acme-test-1", "MIRRORS", "instance:acme-prod-1") > 0)
cur = conn.cursor()
cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = ?", ["instance:acme-test-1", "MIRRORS"])
conn.commit()
print("after a plain SQL DELETE:", native.isDefined("KG", "out", gkey, "instance:acme-test-1", "MIRRORS", "instance:acme-prod-1") > 0)
""")

md(r"""
## 4. How a query runs: two engines

```
Cypher text
   |  iris_vector_graph/cypher/parser.py        -> an AST (ast.py)
   v
native_plan.py: can the native executor run it?
   |-- yes -> a JSON plan -> Graph.KG.Cypher.Exec (ObjectScript, inside IRIS) walks ^KG, reads properties, groups,
   |          sorts, applies LIMIT, and returns rows
   |-- no  -> translator.py -> SQL over the four tables -> IRIS SQL
```

- The **native executor** (`iris_src/src/Graph/KG/Cypher/Exec.cls`) answers the reads most applications ask: `MATCH`
  chains from an id or property lookup, fixed and variable-length hops, `WHERE` comparisons, `RETURN` with `DISTINCT`,
  `ORDER BY`, `LIMIT`, grouped counts. It runs next to the data, walking `^KG`, and splits big walks across worker
  processes.
- Everything else (writes, `OPTIONAL MATCH`, most functions, procedures) goes to the **SQL path**: `translator.py`
  turns the AST into SQL. Both paths name and type their answers with the same code, and shadow mode
  (`engine.cypher_shadow = True`) runs both and raises if they ever differ.

To see both paths answer, a slightly bigger synthetic graph: 300 instances, each with 1 to 4 findings and 2 to 4
network neighbours (a few seconds to load). It is far too small for the speed difference to matter; the benchmark
numbers below are for that.
""")

code(r"""
random.seed(1)
N = 300
nodes = [{"id": f"syn:i{k}", "labels": ["SynInstance"], "properties": {"name": f"i{k}", "region": random.choice("ABCD")}}
         for k in range(N)]
edges = []
for k in range(N):
    for j in random.sample(range(N), random.randint(2, 4)):
        edges.append({"source_id": f"syn:i{k}", "predicate": "LINKS", "target_id": f"syn:i{j}"})
    for m in range(random.randint(1, 4)):
        fid = f"syn:f{k}_{m}"
        nodes.append({"id": fid, "labels": ["SynFinding"], "properties": {"severity": random.choice(["low", "medium", "high"])}})
        edges.append({"source_id": fid, "predicate": "ABOUT", "target_id": f"syn:i{k}"})
t = time.perf_counter()
# disable_indexes=False: the bulk loaders' default turns indexes off and rebuilds them whole, which pays only for
# loads of millions of rows
engine.bulk_create_nodes(nodes, disable_indexes=False)
engine.bulk_create_edges(edges, disable_indexes=False)
print(f"{len(nodes):,} nodes and {len(edges):,} edges in {time.perf_counter() - t:.1f} s")
""")

code(r"""
# for every instance: the high-severity findings two network hops away, counted by the starting instance's region
QUESTION = ("MATCH (a:SynInstance)-[:LINKS]->(b:SynInstance)-[:LINKS]->(c:SynInstance)<-[:ABOUT]-(f:SynFinding) "
            "WHERE f.severity = 'high' RETURN a.region AS region, count(*) AS paths ORDER BY region")

def timed(native_on, runs=5):
    engine.native_cypher = native_on
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter(); r = engine.execute_cypher(QUESTION); best = min(best, time.perf_counter() - t)
    engine.native_cypher = True
    return best * 1000, r.rows

native_ms, native_rows = timed(True)
sql_ms, sql_rows = timed(False)
print(f"native executor {native_ms:6.1f} ms   SQL path {sql_ms:6.1f} ms   same answer: {sorted(native_rows) == sorted(sql_rows)}")
pd.DataFrame(native_rows, columns=["region", "paths"])
""")

md(r"""
Both paths give the same answer; the native one reads `^KG` instead of joining tables, and on a graph this small
both take a few milliseconds. The gap opens with size: on the LDBC Social Network Benchmark at scale factor 1 (3.7 M nodes, 21 M edges, 40 queries) the native path's median time is 1.2x Neo4j's and
0.9x Memgraph's; see `docs/performance/alt-bench/REPORT_TEMPLATE.md`.

## 5. Time: temporal edges

opsreview records **events**: an action on an instance, a configuration change, a metric sample. IVG stores these as
**temporal edges**: an edge with a timestamp, `(ts, source, type, target)`. Many edges can join the same two nodes at
different times.

The pattern opsreview uses: each event is its own node, with an id taken from its `event_id`, and the edge is
`(ts, instance, ACTION_AT, action:<event_id>)`. A replay rewrites the same edge; two events in the same millisecond
cannot collide; the event's details are the edge's attributes.

The temporal store is a set of globals, kept by `Graph.KG.TemporalIndex`:

```
^KG("tout", graph, ts, source, type, target) = weight     by time, then source
^KG("tin",  graph, ts, target, type, source) = weight     by time, then target
^KG("bucket", ...), ^KG("tagg", ...)                      per-bucket counts, sums and distinct counts (pre-aggregated)
^KG("tlast", graph, source, type) = ts                    each source's newest event
```

Two days of actions on three instances, one an hour:
""")

code(r"""
T0 = 1_760_000_000            # a fixed start (seconds) so the output is the same every run
HOUR = 3600
kinds = ["backup", "restart", "config_change", "license_check"]
events, eid = [], 0
for iid in ["acme-prod-1", "acme-prod-2", "globex-prod-1"]:
    for step in range(0, 48 * HOUR, HOUR):
        eid += 1
        events.append({"s": f"instance:{iid}", "p": "ACTION_AT", "o": f"action:{eid}", "ts": T0 + step,
                       "w": random.randint(1, 100), "attrs": {"kind": random.choice(kinds), "event_id": str(eid)}})
written = engine.bulk_create_edges_temporal(events)
print(f"written {int(written):,}  collisions {written.collisions}")

# writing the same coordinates again in mode "insert" changes nothing, and says so (spec 254)
again = engine.bulk_create_edges_temporal(events[:10], mode="insert")
print(f"replayed 10: written {int(again)}  collisions {again.collisions}")
""")

md(r"""
**A window:** one instance's actions in a 2-hour window, each with its attributes, read page by page from the server.
""")

code(r"""
cur_w = engine.iter_edges_in_window(T0 + 24 * HOUR, T0 + 26 * HOUR, source="instance:acme-prod-1", predicates=["ACTION_AT"])
pd.DataFrame([{"ts": e["ts"], "target": e["target"], **(e.get("attrs") or {})} for e in cur_w]).head(8)
""")

md(r"""
**Buckets:** counts and sums per source over a range, from the pre-aggregated buckets (no raw edge is read), and the
newest event per instance:
""")

code(r"""
display(pd.DataFrame(engine.get_bucket_groups("ACTION_AT", T0, T0 + 48 * HOUR, source_prefix="instance:")))
pd.DataFrame(engine.get_latest_edges(source_prefix="instance:", predicate="ACTION_AT"))[["source", "target", "timestamp", "attrs"]]
""")

md(r"""
**Retention.** opsreview keeps 48 hours of raw events; here, everything older than the first 24 hours goes. `purge_raw_before` removes every raw edge older than a cutoff,
and (spec 254) the structural row and `^KG("out")` entry each one held up, so one-node-per-event adjacency does not grow
forever. The buckets stay.
""")

code(r"""
def counts():
    return {"raw edges": sum(1 for _ in engine.iter_edges_in_window(T0, T0 + 49 * HOUR, source_prefix="instance:", attrs=False)),
            "rdf_edges rows": int(sql("SELECT COUNT(*) AS n FROM Graph_KG.rdf_edges WHERE p = 'ACTION_AT'")["n"][0])}

print("before:", counts())
res = engine.purge_raw_before(T0 + 24 * HOUR)
print("purged:", res.deleted, "edges")
print("after: ", counts())
""")

md(r"""
## 6. The ledger: governed writes

The ledger records writes as **revisions**: who made them (`actor`), why (`message`), and exactly what changed. You can
list them, look one up, and diff two of them. opsreview uses it for
writes that must be auditable.

Enabling the ledger snapshots the graph as its first revision ("genesis"). The synthetic graph from part 4 goes first,
so the ledger starts from the small opsreview graph. A write then goes through a **changeset**, committed as one
revision:
""")

code(r"""
from iris_vector_graph.ledger import Changeset

engine.delete_nodes_by_prefix("syn:")
engine.ledger.enable()
cs = Changeset(actor="luca", actor_type="human", message="record a new finding on acme-prod-2")
cs.create_node("finding:f6", labels=["Finding"], properties={"severity": "medium", "title": "CSP gateway timeout"})
cs.create_relationship("finding:f6", "ABOUT", "instance:acme-prod-2")
first = engine.ledger.commit(cs)

cs2 = Changeset(actor="luca", actor_type="human", message="raise it to high")
cs2.set_property("finding:f6", "severity", "high")
second = engine.ledger.commit(cs2)

page = engine.ledger.history(descending=True, limit=5)
pd.DataFrame([{"revision": r.revision_id[:12], "actor": r.actor, "message": r.message, "ops": r.op_count} for r in page.revisions])
""")

code(r"""
print(engine.ledger.diff(first.revision.revision_id, second.revision.revision_id))
q("MATCH (f:Finding {node_id: 'finding:f6'})-[:ABOUT]->(i) RETURN f.title AS title, f.severity AS severity, i.name AS instance")
""")

md(r"""
## 7. Vectors, briefly

The "vector" in the name: a node can carry an embedding, and IVG combines vector similarity with the graph (search by
meaning, then walk the neighbours). Toy 8-dimensional vectors here; real ones come from an embedding model.
""")

code(r"""
import math
def toy_vector(text):
    v = [0.0] * 8
    for i, ch in enumerate(text.lower()):
        v[(ord(ch) + i) % 8] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

for fid, _about, _sev, title in findings:
    engine.store_embedding(f"finding:{fid}", toy_vector(title))
hits = engine.kg_KNN_VEC(json.dumps(toy_vector("journal disk space")), k=3)
pd.DataFrame(hits, columns=["node", "score"])
""")

md(r"""
## 8. A map of the code

| Where | What it is | Read it for |
| ----- | ---------- | ----------- |
| `iris_vector_graph/engine.py` | `IRISGraphEngine`, the one class callers use | the public API; each method delegates to a mixin |
| `iris_vector_graph/_engine/nodes_edges.py` | node and edge writes, bulk loads, deletes | how a write reaches the four tables |
| `iris_vector_graph/_engine/temporal.py` | temporal edges, windows, buckets, purge | opsreview's event store |
| `iris_vector_graph/_engine/ledger.py`, `iris_vector_graph/ledger/` | the ledger: changesets, revisions, verify | governed writes |
| `iris_vector_graph/_engine/query.py` | `execute_cypher`: routing between the native and SQL paths | what runs a query |
| `iris_vector_graph/cypher/parser.py`, `ast.py` | Cypher text to an AST | the language |
| `iris_vector_graph/cypher/native_plan.py` | AST to a native plan, or "not supported" | what the native path accepts |
| `iris_vector_graph/cypher/translator.py` | AST to SQL (large: the whole of openCypher) | the SQL path |
| `iris_vector_graph/_engine/schema.py` | DDL, indexes, deploying the ObjectScript classes | installing IVG |
| `iris_src/src/Graph/KG/Cypher/Exec.cls` | the native executor | how a walk over `^KG` answers a query |
| `iris_src/src/Graph/KG/Adjacency.cls`, `Adj.cls` | `^KG("out"/"in")` maintenance and reads | the adjacency global |
| `iris_src/src/Graph/KG/TemporalIndex.cls`, `TemporalWindow.cls` | the temporal globals | the event store, server side |
| `iris_src/src/Graph/KG/VarLength.cls` | variable-length walks (`*1..3`) | paths |
| `docs/adr/` | architecture decisions (ADR-0008/0009: `^KG` derived from the rows) | why it is built this way |
| `CONTEXT.md` | the project's glossary | the vocabulary |
| `tests/e2e/` | end-to-end tests against IRIS | worked examples of every feature |

**Suggested reading order:** `CONTEXT.md`, then `engine.py` (the API), `_engine/temporal.py` (what opsreview calls),
`_engine/query.py` (how a query is routed), and `Exec.cls` once the `^KG` layout in part 3 makes sense.

## The `IVGDEMO` namespace

Made once on `ivg-iris-enterprise` (2026-10-22). On a fresh container: in `%SYS`, create a database directory with
`SYS.Database`, then `Config.Databases.Create("IVGDEMO", ...)` and `Config.Namespaces.Create("IVGDEMO", ...)` with
`Globals` and `Routines` set to `IVGDEMO`. Then run `IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()`
connected to `IVGDEMO`, and
`IVG_PORT=31972 IVG_NAMESPACE=IVGDEMO scripts/enterprise-container.sh tcp-deploy`.
""")


def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "ivg", "display_name": "Python (iris-vector-graph)", "language": "python"}
    nb.metadata["language_info"] = {"name": "python"}
    if "--execute" in sys.argv:
        from nbclient import NotebookClient

        NotebookClient(nb, timeout=900, kernel_name="ivg", resources={"metadata": {"path": str(HERE.parents[1])}}).execute()
    nbf.write(nb, OUT)
    print(f"wrote {OUT} ({len(cells)} cells)")


if __name__ == "__main__":
    main()
