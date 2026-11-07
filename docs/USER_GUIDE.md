<!-- markdownlint-disable MD013 -->

# IVG User Guide

For Python developers building applications on IVG. Each section is a task, in the order you are likely to meet them.
Method signatures are in the [API Reference](API_REFERENCE.md); running the IRIS side (installation, privileges,
backup) is in the [Admin Guide](ADMIN_GUIDE.md); open problems are in [Known issues](KNOWN_ISSUES.md).

The examples run in order, in one Python session, and build one small graph. Each shows its answer on a `# →` line,
worked out from the graph drawn in section 2. Start in a namespace with no graph in it: in a namespace that already
holds one (the Quickstart's, say), `engine.erase_all()` empties it first, or the counts differ.

---

## 1. Connect

```python
import json

import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()["schema"]
# → 'Graph_KG'
```

The engine works in the namespace its connection is in. `initialize_schema()` compiles IVG's ObjectScript classes into
that namespace and creates its tables, functions and procedures in the **IVG Schema**, `Graph_KG` unless you name
another with `schema_prefix=` (or `IVG_SCHEMA_PREFIX`;
[choosing a name](ADMIN_GUIDE.md#one-ivg-schema-per-namespace)). It is safe to run again; run it after each upgrade of
the package. `embedding_dimension` is the width of the vectors you will store (section 7); setup needs it.
The [README](../README.md#where-ivg-lives-in-a-namespace) explains what goes where.

An engine refuses a namespace that 4.6.0's `initialize_schema()` has not set up: its first database call raises
`IVGSchemaError`, naming what to run. To upgrade a namespace from 4.5 or earlier, read
[Migrating to 4.6.0](migration/v4.6.0.md).

`IRISGraphEngine.from_connect(hostname, port, namespace, username, password, embedding_dimension=768)` opens the
connection for you.

**One namespace per tenant.** Each namespace has its own tables and its own `^KG`, so two tenants in two namespaces
cannot read each other's graph, whatever a query says. Create the namespaces in IRIS first (an operator's task), then
connect to each and set it up:

<!-- doc-test: skip reason="needs two more namespaces, ACME_HEALTH and METRO_HOSPITAL, created by an operator" -->

```python
acme = IRISGraphEngine(iris.connect("localhost", 1972, "ACME_HEALTH", "_SYSTEM", "SYS"), embedding_dimension=768)
metro = IRISGraphEngine(iris.connect("localhost", 1972, "METRO_HOSPITAL", "_SYSTEM", "SYS"), embedding_dimension=768)
acme.initialize_schema()
metro.initialize_schema()
```

Derive the namespace from the caller's verified identity (a token, a client certificate), never from a field in the
request.

**Inside IRIS.** Code that runs in IRIS (`irispython`, or a `[ Language = python ]` method) uses the process's own
connection:

<!-- doc-test: skip reason="runs only inside IRIS (irispython or a Language = python method)" -->

```python
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(EmbeddedConnection(), embedding_dimension=768)
engine.initialize_schema()
```

Outside IRIS, `EmbeddedConnection()` raises an error that names `iris.connect` instead. Inside IRIS, a SQL `NULL`
comes back as `''`, and a `DELETE` that matches no row raises `SQLError` with SQLCODE 100 from raw `iris.sql.exec`
(the engine's own methods handle it).

---

## 2. Load a graph

The examples use five people and one company:

```text
alice -KNOWS-> bob -KNOWS-> carol -KNOWS-> dave -KNOWS-> erin
alice -KNOWS-> carol
alice, bob and erin -WORKS_AT-> acme
```

```python
people = [
    {"id": "alice", "labels": ["Person"], "properties": {"name": "Alice", "bio": "Builds graph databases and vector search"}},
    {"id": "bob",   "labels": ["Person"], "properties": {"name": "Bob",   "bio": "Vector search at scale"}},
    {"id": "carol", "labels": ["Person"], "properties": {"name": "Carol", "bio": "Temporal analytics"}},
    {"id": "dave",  "labels": ["Person"], "properties": {"name": "Dave",  "bio": "Graph algorithms"}},
    {"id": "erin",  "labels": ["Person"], "properties": {"name": "Erin",  "bio": "Search relevance"}},
]
engine.bulk_create_nodes(people)
# → ['alice', 'bob', 'carol', 'dave', 'erin']
engine.create_node("acme", labels=["Company"], properties={"name": "Acme"})
# → True
engine.create_edge("alice", "KNOWS", "bob", qualifiers={"since": 2019})
# → True
engine.bulk_create_edges([
    {"source_id": "bob",   "predicate": "KNOWS",    "target_id": "carol"},
    {"source_id": "alice", "predicate": "KNOWS",    "target_id": "carol"},
    {"source_id": "carol", "predicate": "KNOWS",    "target_id": "dave"},
    {"source_id": "dave",  "predicate": "KNOWS",    "target_id": "erin"},
    {"source_id": "alice", "predicate": "WORKS_AT", "target_id": "acme"},
    {"source_id": "bob",   "predicate": "WORKS_AT", "target_id": "acme"},
    {"source_id": "erin",  "predicate": "WORKS_AT", "target_id": "acme"},
])
# → 7
engine.node_count(), engine.edge_count()
# → (6, 8)
```

A node has an ID you choose (`alice`), labels, and properties. An edge has a source, a type (the predicate) and a
target, and properties of its own (`qualifiers`). `create_node` returns `False` for a node that is already there, and
`create_edge` for an edge that is; a write IRIS refuses raises `GraphWriteError` and leaves nothing behind. Both write
the `^KG` adjacency global in the same transaction as the rows, so a query sees them at once.

Other sources (NDJSON files, RDF, OBO ontologies, networkx graphs, existing SQL tables, millions of edges) are in
[Data formats](setup/DATA_FORMATS.md).

---

## 3. Query with Cypher

`execute_cypher` runs an openCypher query and returns an `IVGResult`: `rows` (a list of lists) and `columns`.

```python
r = engine.execute_cypher("MATCH (p:Person) RETURN p.name AS name ORDER BY name")
r.columns
# → ['name']
r.rows
# → [['Alice'], ['Bob'], ['Carol'], ['Dave'], ['Erin']]
```

`node_id` is the ID you gave the node. In a pattern, `{id: 'x'}` names the node x too, as `CREATE ({id: 'x'})` makes
it, whether or not the node has an `id` property (a node `bulk_create_edges` created has none); `n.id` in an
expression reads the property. Pass values as parameters (`$id`) rather than pasting them into the text:

```python
engine.execute_cypher(
    "MATCH (a:Person {node_id: $id})-[:KNOWS]->(b) RETURN b.name AS friend ORDER BY friend", {"id": "alice"}
).rows
# → [['Bob'], ['Carol']]
```

A column without `AS` is named after its expression:

```python
engine.execute_cypher("MATCH (a {node_id: 'alice'})-[:KNOWS]->(b) RETURN b.name ORDER BY b.name").columns
# → ['b.name']
```

Filters and aggregates:

```python
engine.execute_cypher("MATCH (p:Person) WHERE p.bio CONTAINS 'search' RETURN p.name AS name ORDER BY name").rows
# → [['Alice'], ['Bob']]
engine.execute_cypher("MATCH (p:Person)-[:WORKS_AT]->(c:Company) RETURN c.name AS company, count(p) AS staff").rows
# → [['Acme', 3]]
```

String comparison is case-sensitive: Erin's bio says `Search`, so `CONTAINS 'search'` leaves her out.

A whole node in `RETURN` comes back as three columns, `<name>_id`, `<name>_labels` and `<name>_props`: the labels as
JSON text, and the properties as JSON text of a list of `{"key", "value"}` objects, each itself JSON text. To read
the properties as one map, return `properties(n)`, which answers the map:

```python
r = engine.execute_cypher("MATCH (n {node_id: 'acme'}) RETURN n")
r.columns
# → ['n_id', 'n_labels', 'n_props']
node_id, labels, props = r.rows[0]
node_id, json.loads(labels), sorted((p["key"], p["value"]) for p in map(json.loads, json.loads(props)))
# → ('acme', ['Company'], [('id', 'acme'), ('name', 'Acme')])
engine.execute_cypher("MATCH (n {node_id: 'acme'}) RETURN properties(n) AS p").rows[0][0]
# → {'id': 'acme', 'name': 'Acme'}
```

A node yielded by a `CALL` (`YIELD node`) comes back the same way. The `id` property is there because `create_node`,
`bulk_create_nodes` and `store_node` store one equal to the node ID, so that `{id: 'acme'}` finds the node.

### Variable-length paths

`-[:KNOWS*1..2]->` matches paths of one or two `KNOWS` edges, and a query returns one row per path. From Alice there
are four: to Bob, to Carol, to Carol through Bob, and to Dave through Carol.

```python
engine.execute_cypher(
    "MATCH (a {node_id: 'alice'})-[:KNOWS*1..2]->(b) RETURN b.name AS name, count(*) AS paths ORDER BY name"
).rows
# → [['Bob', 1], ['Carol', 2], ['Dave', 1]]
engine.execute_cypher(
    "MATCH (a {node_id: 'alice'})-[:KNOWS*1..2]->(b) RETURN DISTINCT b.name AS name ORDER BY name"
).rows
# → [['Bob'], ['Carol'], ['Dave']]
```

A path uses each relationship at most once. Undirected, Erin reaches Dave in one hop and Carol in two; the walk does not
come back to Erin over the edge it left by:

```python
engine.execute_cypher("MATCH (e {node_id: 'erin'})-[:KNOWS*1..2]-(x) RETURN count(DISTINCT x) AS n").rows
# → [[2]]
```

Shortest paths are in [section 6](#6-graph-algorithms).

### Writes

`CREATE`, `MERGE`, `SET`, `DELETE` and `REMOVE` change the graph. `read_only=True` refuses any of them with
`PermissionError` before anything runs, wherever the write appears in the query:

```python
try:
    engine.execute_cypher("MATCH (n {node_id: 'alice'}) SET n.city = 'Boston'", read_only=True)
except PermissionError:
    refused = True
refused
# → True
```

### Errors

A query that does not parse raises `CypherParseError`. One that parses but is not valid Cypher (an undefined
variable, two columns with one name, an aggregate where none is allowed) raises `SyntaxError`, its message starting
with the openCypher error's name. A read that fails in IRIS comes back with `result.error` set and no rows, unless you
pass `raise_on_error=True`, which raises `RuntimeError` instead. Check `result.error` (or pass `raise_on_error=True`)
wherever an empty answer would be taken for "nothing found". A write that IRIS or IVG refuses raises `GraphWriteError`,
a `RuntimeError`, whatever `raise_on_error` says, after rolling the statement back; `read_only=True` refuses any write
with `PermissionError` before it runs.

```python
from iris_vector_graph.cypher.parser import CypherParseError

try:
    engine.execute_cypher("MATCH (n RETURN n")
except CypherParseError:
    parse_failed = True
parse_failed
# → True
```

A `DELETE` of a node that still has relationships is refused, and nothing is deleted (`DETACH DELETE` removes the
relationships with it):

```python
from iris_vector_graph import GraphWriteError

try:
    engine.execute_cypher("MATCH (a {node_id: 'alice'}) DELETE a")
except GraphWriteError as e:
    refused = str(e)
refused.split(".")[0]
# → 'ConstraintVerificationFailed: Cannot delete node with existing relationships'
engine.execute_cypher("MATCH (a {node_id: 'alice'}) RETURN count(a)").rows
# → [[1]]
```

### Where a query runs

A read that IVG's native executor supports (a `MATCH` chain from a node's ID, with or without a label, a property or a
label; fixed and variable-length hops; `WHERE` comparisons; `DISTINCT`, `ORDER BY`, `LIMIT`; `count`; returning
properties and `node_id`) runs inside IRIS over `^KG`.
Everything else, and every write, is translated to IRIS SQL. Both give the same answer, with the same column names:

```python
sql_only = IRISGraphEngine(conn, embedding_dimension=768, native_cypher=False)
sql_only.execute_cypher("MATCH (p:Person) RETURN count(p) AS n").rows
# → [[5]]
```

`native_cypher=False` keeps one engine on the SQL path; `IVG_NATIVE_CYPHER=0` does it for every engine, and
`IVG_CYPHER_SHADOW=1` runs both paths and raises `NativeMismatch` where they differ. A native read with hops is also
compiled, in the background, into a class for its shape (`IVGC.P<n>`), which later reads of that shape use; a read on
the SQL path is not. The [Admin Guide](ADMIN_GUIDE.md#compiled-native-plans-ivgc) lists examples of each and tells
operators how to turn compiling off. The paths also agree on each value's type, under any version of the IRIS driver: a number a Cypher write stored
reads as that number on both (before 4.6.0 the SQL path read it as IRIS's text, `'.94999999999999995559'` for 0.95,
with intersystems-irispython 5.4.1). The one known difference between the paths is listed
in [Known issues](KNOWN_ISSUES.md).

### AQL

`execute_aql` translates an ArangoDB AQL query to Cypher and runs it:

```python
r = engine.execute_aql("FOR v IN 1..2 OUTBOUND @s KNOWS RETURN DISTINCT v._key", bind_vars={"s": "alice"})
sorted(row[0] for row in r.rows)
# → ['bob', 'carol', 'dave']
```

---

## 4. Named graphs

Every node and edge belongs to one graph. The **default graph** is the one you get when you name none: `graph=None`
or `graph=""` in Python, no `USE GRAPH` in a Cypher write. A **named graph** is any non-empty string. The same ID in two
graphs is two nodes, each with its own labels, properties and edges.

```python
engine.create_node("alice", labels=["Person"], properties={"name": "Alice (staging)"}, graph="staging")
# → True
engine.execute_cypher("USE GRAPH 'staging' CREATE (:Person {id: 'zed', name: 'Zed'})")
engine.create_edge("alice", "KNOWS", "zed", graph="staging")
# → True
engine.list_graphs()
# → ['staging']
engine.get_node("alice", graph="staging")["name"]
# → 'Alice (staging)'
```

`list_graphs()` lists the named graphs that hold edges; the default graph is never in it. `USE GRAPH` scopes a query
to one graph. A query without it reads every graph, and each node still meets only its own graph's edges; a `MATCH`
of two patterns (`MATCH (a:Person), (b:Person)`) pairs each graph's nodes with that graph's only:

```python
engine.execute_cypher("USE GRAPH 'staging' MATCH (a {node_id: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name").rows
# → [['Zed']]
engine.execute_cypher("MATCH (a {node_id: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name ORDER BY name").rows
# → [['Bob'], ['Carol'], ['Zed']]
```

A write without `USE GRAPH` changes what its `MATCH` bound, each node in its own graph: `MATCH (n {node_id: 'alice'})
SET n.seen = true` sets `seen` on both Alices, and `MATCH (n:Person {node_id: 'alice'}) SET n.seen = true` only on
each graph's Alice that is a `Person` there; a graph whose node of that ID the pattern does not match is not touched.
`SET`, `REMOVE` and `DELETE` work this way. `CREATE` and `MERGE` write the default graph, and a `MERGE` without
`USE GRAPH` also matches there only, so its `ON MATCH SET` changes the default graph's node. Under `USE GRAPH 'g'`
every write reads and writes graph g alone.

**The default graph's key.** In the `^KG` global a graph is the first subscript after the tree's name. A named graph's
key is its name; the default graph's key is `0`, because an IRIS subscript cannot be the empty string
(`^KG("out","","alice")` raises `<SUBSCRIPT>`). In an IRIS terminal in the same namespace, each edge's entry holds its
weight:

```objectscript
USER>write ^KG("out",0,"alice","KNOWS","bob")
1
USER>write ^KG("out","staging","alice","KNOWS","zed")
1
```

A delete that names no graph refuses an ID that more than one graph holds, and deletes nothing:

```python
try:
    engine.delete_node("alice")
except ValueError as e:
    refusal = str(e)
"staging" in refusal
# → True
```

Pass `graph=` to say which one. `erase_graph` removes one graph's content from every store (rows, `^KG`, the temporal
index) and returns the number of node and edge rows it removed; `verify_graph` checks that a graph's `^KG` entries
and rows agree:

```python
bool(engine.verify_graph("staging")["ok"])
# → True
engine.erase_graph("staging")
# → 3
engine.list_graphs()
# → []
```

After an erase, the next graph algorithm that reads `^NKG` (strongly connected components, k-core, triangle count,
Leiden, closeness, and the other `^NKG` centralities) first rebuilds `^NKG` once, because the erased graph's nodes are
still in its dictionary. That rebuild walks the whole namespace, not just the erased graph: 167 s at 1.9M edges in the
lab. On a large graph, call `engine.sync()` right after the erase to pay that cost when you choose
([ADMIN_GUIDE, The adjacency indexes](ADMIN_GUIDE.md#the-adjacency-indexes-kg-and-nkg)).

`erase_all()` empties every graph. `delete_edge(s, p, o, graph=...)` deletes one edge (`all_graphs=True` from every
graph). Python methods that write or read take `graph=`; omitted, they mean the default graph, never every graph.
The bulk writers `bulk_create_nodes` and `bulk_create_edges` take it too, and a node's or edge's own `graph` key wins
over it; `bulk_ingest_edges` and `bulk_load_session` write the default graph only.

A graph ID keeps two datasets from colliding; it is not a security boundary, because any caller can name any graph.
To keep tenants apart, give each a namespace (section 1).

**Parallel edges.** A graph holds at most one edge per `(source, type, target)`: writing the same edge twice leaves
one. `engine.set_multigraph(graph, True)` lets a graph hold several, as openCypher's `CREATE` expects; see the first
entry of [Known issues](KNOWN_ISSUES.md) for an example, and the API Reference for what changes.

---

## 5. Properties

```python
node = engine.get_node("alice")
node["labels"], node["name"]
# → (['Person'], 'Alice')
```

`get_node` reads the default graph unless you pass `graph=`, and returns `None` for a node that is not there. Its
dict holds `id` (the node ID), `labels` and the properties; a property named `id` or `labels` is returned as `p_id` or
`p_labels`, so the `id` property the write methods store comes back as `p_id`, equal to the node ID. Set a
property with Cypher (or `store_node`, which replaces a node's properties):

```python
engine.execute_cypher("MATCH (n {node_id: 'alice'}) SET n.city = 'Boston'")
engine.get_node("alice")["city"]
# → 'Boston'
```

A relationship's properties are read in Cypher as `r.<name>`. An edge without the property reads `None`:

```python
engine.execute_cypher(
    "MATCH (a {node_id: 'alice'})-[r:KNOWS]->(b) RETURN b.name AS friend, r.since AS since ORDER BY friend"
).rows
# → [['Bob', 2019], ['Carol', None]]
engine.execute_cypher(
    "MATCH (a {node_id: 'alice'})-[r:KNOWS]->(b {node_id: 'carol'}) SET r.since = 2022 RETURN r.since AS since"
).rows
# → [[2022]]
```

A relationship property comes back with its type: `2019` is an integer, as it was written. A node property comes back
with the type it is stored with, which depends on the writer: a Cypher `CREATE` or `SET` stores a number as a number,
and `create_node`, `bulk_create_nodes` and `store_node` store it as its text, so `{"age": 34}` reads back as the
string `'34'`. Filters and `ORDER BY` treat both as the number; read one with `toFloat()` or `toInteger()` to get a
number whichever wrote it (see [Known issues](KNOWN_ISSUES.md), "A property cannot tell a number from a string that
spells it").

A list or map property reads back as the list or map, from every writer and on every read: `RETURN n.p`, `WITH`,
`DISTINCT`, `collect()`, a map projection, `properties(n)` and a whole node carry `['a', 'b']`, not the text
`'["a", "b"]'` it is stored as, on nodes and relationships alike:

```python
engine.create_node("dana", labels=["Person"], properties={"tags": ["a", "b"], "address": {"city": "Oslo"}})
# → True
engine.execute_cypher("MATCH (n {node_id: 'dana'}) RETURN n.tags AS tags, n.address AS address").rows
# → [[['a', 'b'], {'city': 'Oslo'}]]
engine.delete_node("dana")
# → True
```

A string that spells a JSON list or map reads as one too (see [Known issues](KNOWN_ISSUES.md), "A string that spells
a boolean, a list or a map reads as one").

---

## 6. Graph algorithms

The algorithms read the adjacency globals. `^KG` follows every write; the interned copy `^NKG`, which some algorithms
and the Arno library read, is rebuilt by `sync()`. Call it after loading and before running algorithms:

```python
engine.sync()
# → True
```

Every node of the graph is in every answer, a node with no edge too: it has a PageRank score, a degree and closeness
row (0), a component and a community of its own, coreness 0 and no triangles, and the `n` of the degree and
closeness formulas below is `node_count()`. So an answer is networkx's on the graph with every node, edgeless ones
included. Before 4.6.0 a node with no edge was in no answer and `n` counted only the nodes with an edge. Ranked
answers go highest first and break a tie by node ID in IRIS collation (numbers first, by value, then text: `9`,
`10`, `07`, `a`); two scores equal to 12 decimal places are a tie, so float noise between two nodes in the same
position does not order them.

### PageRank

Global PageRank over the default graph, as `(node, score)` pairs, highest first. It is the textbook algorithm (damping
0.85, 20 iterations) over every node: a node with no out-edges (or no edge at all) spreads its rank over all of them,
and their scores sum to 1. The 20 are a fixed count, not a convergence test, so on some graphs the scores are still a
few hundredths from converged; pass `max_iterations=200` to compare them with networkx's `pagerank(G, weight=None)`.
With the Arno library loaded, in the `USER` namespace, the walk also stops once the scores have converged, within
about 2e-6 of the fixed count's.

```python
[(node, round(score, 3)) for node, score in engine.kg_PAGERANK()]
# → [('acme', 0.315), ('erin', 0.221), ('dave', 0.178), ('carol', 0.127), ('bob', 0.089), ('alice', 0.07)]
```

Acme ranks first: three people work there. Alice ranks last: nothing points to her.

Personalized PageRank ranks the graph around seed nodes. From Alice, Alice comes first (every restart returns to her)
and Acme second:

```python
scores = engine.kg_PERSONALIZED_PAGERANK(["alice"])
sorted(scores, key=scores.get, reverse=True)[:2]
# → ['alice', 'acme']
```

`kg_PAGERANK(seed_entities=[...])` is the same walk stopped after `kg_PAGERANK`'s 20 iterations, where
`kg_PERSONALIZED_PAGERANK` allows 100 and stops once converged; call `kg_PERSONALIZED_PAGERANK` for it.
`kg_PAGERANK`'s `bidirectional` and `reverse_weight` are accepted and ignored:

```python
engine.kg_PAGERANK(seed_entities=["alice"]) == engine.kg_PERSONALIZED_PAGERANK(["alice"], max_iterations=20)
# → True
```

`graph=` keeps the walk inside one graph, and `exclude_predicates=` leaves edge types out of it. The walk restarts
at the seeds with probability 0.15 and otherwise follows an out-edge. A node with no out-edges (Acme here) hands its
score back to the seeds, so the scores sum to 1 and are networkx's
`pagerank(G, alpha=damping_factor, personalization=seeds, tol=tolerance)`, with G as a multigraph. The walk stops
after `max_iterations`, or once the scores change by less than N × `tolerance` in total in one iteration (N: the
nodes holding a score), networkx's rule. Every route gives this answer, whether or not the Arno library is loaded.

### Shortest path

`shortestPath` in Cypher finds one shortest path. From Alice to Erin along `KNOWS` it is Alice, Carol, Dave, Erin
(three edges; the way through Bob takes four):

```python
r = engine.execute_cypher(
    "MATCH p = shortestPath((a {node_id: 'alice'})-[:KNOWS*]->(b {node_id: 'erin'})) "
    "RETURN length(p) AS hops, [n IN nodes(p) | n.name] AS names"
)
r.columns
# → ['hops', 'names']
r.rows[0]
# → [3, ['Alice', 'Carol', 'Dave', 'Erin']]
```

A list, a map or a path in a result row comes back as the Python list or dict, through `execute_cypher`, REST and
Bolt alike; a string is a string, even one that spells JSON (`RETURN '[1]'` answers `'[1]'`). Before 4.6.0 a list came
back as its JSON text, which callers read with `json.loads`. `RETURN p` gives the path itself, as node IDs and
relationship types:

```python
r = engine.execute_cypher(
    "MATCH p = shortestPath((a {node_id: 'alice'})-[:KNOWS*]->(b {node_id: 'erin'})) RETURN p"
)
r.rows[0][0]
# → {'nodes': ['alice', 'carol', 'dave', 'erin'], 'rels': ['KNOWS', 'KNOWS', 'KNOWS']}
```

`CALL ivg.shortestPath.weighted(from, to [, weightProp, maxCost, maxHops, direction, types]) YIELD path, totalCost`
finds the cheapest path by edge weight, in the `USE GRAPH` graph; without one, in each graph with edges, a row per
graph that has a path. The defaults are
`'weight'`, 9999, 10, `'out'` and every relationship type; `maxHops` bounds the path's relationships (0 for no bound).
An edge's weight is its `weight` property (`create_edge(..., weight=...)`, or `{weight: 2.5}` in Cypher), the one
property the adjacency index stores, so `weightProp` must be `'weight'`: any other name is an error, not a search by
that property. An edge without one costs 1.0, and `r.weight` reads it as null; `r.weight` reads a weight given to
`create_edge` back as the number it was (1.0 as 1.0, 1 as 1).

### Breadth-first search

The nodes within two hops of Alice, over any edge type, are Bob, Carol and Acme (one hop) and Dave (two). In Cypher:

```python
engine.execute_cypher(
    "MATCH (a {node_id: 'alice'})-[*1..2]->(b) RETURN DISTINCT b.node_id AS id ORDER BY id"
).rows
# → [['acme'], ['bob'], ['carol'], ['dave']]
```

`khop` walks the same neighbourhood breadth-first and returns `{"nodes", "edges"}`: the node IDs, the start first and
then in the order the walk reached them, and the edges it walked, each `{"s", "p", "o", "w", "step"}`. The answer is
the same whether or not the Arno library is loaded (before 4.6.0 it had another shape with the library):

```python
sorted(engine.khop("alice", hops=2)["nodes"])
# → ['acme', 'alice', 'bob', 'carol', 'dave']
```

### Components and centrality

Weakly connected components label each node with its component; every node here is in one, labelled by its smallest
ID:

```python
components = engine.kg_WCC()
len(set(components.values())), components["erin"]
# → (1, 'acme')
```

Degree centrality counts edges per node: `degree` is the count, and `score` is the degree / (n - 1), n being the
number of the graph's nodes (here six). Out-degree, top two: alice has three out-edges and bob two,
and the other nodes number five, so their scores are 3/5 and 2/5:

```python
[(d["id"], d["degree"], d["score"]) for d in engine.degree_centrality(direction="out", top_k=2)]
# → [('alice', 3, 0.6), ('bob', 2, 0.4)]
```

`CALL ivg.degreeCentrality` yields the same `score` and `degree` (Cypher procedures, below), so compare `score` with
`score`: the procedure's 0.6 is the method's `degree` 3 over 5.

The others work the same way and return lists of dicts, highest first: `betweenness_centrality`, `closeness_centrality`,
`eigenvector_centrality`, `leiden_communities`, `kg_CDLP`, `strongly_connected_components`, `k_core` and
`triangle_count`. Their parameters are in the API Reference; [Communities](COMMUNITIES.md) explains the community
algorithms. Two definitions to know: `closeness_centrality()` is harmonic by default, the sum of 1/d over the nodes a
node reaches (not normalised), and `formula="classical"` is (n - 1) / (sum of d) for a node that reaches every other
node, else 0, n counted as for degree (so once a node has no edge, every classical score is 0). `direction="in"`
counts the hops from the nodes that reach a node, and `"both"` reads the graph as undirected (before 4.6.0 `"both"`
answered `"out"`'s scores). Closeness and degree list every node, zeros included; betweenness lists only the nodes
that lie on some shortest path between two others, so a node scoring 0 has no row (from the method,
`CALL ivg.betweenness` and `gds.betweenness.stream` alike): for the lowest betweenness, count each node with no row
as 0. `eigenvector_centrality()` scores a node by the nodes pointing to it, each once however many edges it has to the
node, and stops after `max_iter` rounds
(30 by default, at most 1000), which can be before it converges; `max_iter=1000` matches networkx's
`eigenvector_centrality(G)`.

Betweenness runs Brandes from `sample_size` source nodes and scales by the node count over `sample_size`. The default,
0, uses every source on a graph of up to 200 nodes and 200 of them above that, an approximation. Any `sample_size` at
or above the node count uses every source, so `sample_size=engine.node_count()` is exact on any graph: networkx's
`betweenness_centrality(G, normalized=False)`, or with `direction="both"` that of the undirected graph. A smaller
`sample_size`, even the node count minus one, is an approximation. Its sources are a fixed pseudo-random sample: the
node IDs that come first by the SHA-1 of their UTF-8 bytes, so every call, from Python or `CALL ivg.betweenness`,
takes the same ones. There is no seed to change them. Before 4.6.0 the sample was the first node IDs in ID order. Here carol and dave each lie on four shortest
paths, and erin on two (carol and dave to acme):

```python
exact = engine.betweenness_centrality(sample_size=engine.node_count())
[(r["id"], r["score"]) for r in exact if r["score"]]
# → [('carol', 4.0), ('dave', 4.0), ('erin', 2.0)]
```

`direction="out"` and `"in"` give the same scores, those of the directed graph; `"both"` gives the undirected graph's.
`max_hops=k` counts only the pairs of nodes at most k hops apart (0, the default, counts every pair). `top_k` keeps
the highest scores. `CALL ivg.betweenness({direction: "both", maxHops: 2, topK: 10})` means the same; before 4.6.0
the procedure ignored `direction` and `maxHops` and answered the directed, unbounded scores, and its `topK` kept the
first scoring nodes by ID rather than the highest.

Each has a Cypher form too, for example `CALL ivg.degreeCentrality({direction: "out", topK: 20}) YIELD node, score`.
A query without `USE GRAPH` answers the union of each graph's own answer, as every read does (section 4):
`ivg.degreeCentrality` and `ivg.ppr` run once in each graph with edges and return a row per graph and node, each with
that graph's own score, and whatever the query reads of the node after the `CALL` (`node.name`, a `MATCH` from it) is
that graph's. A node ID used in two graphs is two rows. Under `USE GRAPH 'g'` they read graph g. Betweenness,
closeness, eigenvector and the community procedures read `^NKG`, which holds every graph's edges with no graph key, so
they cannot answer per graph: `USE GRAPH` with them raises `ValueError`, and so does a query without it once a named
graph has an edge, the message naming the Python method that answers the default graph. While the default graph is
the only one with edges, `^NKG` is exactly its edges, and they answer. `gds.pageRank.stream`, `gds.betweenness.stream`
and `gds.shortestPath.dijkstra.stream` read the default graph alone, so without `USE GRAPH` they raise too once a
named graph has an edge; `USE GRAPH ''` asks for the default graph's answer. Before 4.6.0 the procedures answered the
default graph, or every graph's edges merged, whatever `USE GRAPH` said.

The Python methods answer one graph, never every graph: `degree_centrality(graph=...)` and
`kg_PERSONALIZED_PAGERANK(graph=...)` the graph named, and the default graph when `graph` is omitted (`None`); the
others take no graph and answer the default graph. Where a named graph has an edge, the methods whose fast path reads
`^NKG` read the default graph's own edges instead: through Arno when it is loaded (`leiden_communities`,
`triangle_count`, `strongly_connected_components`, `k_core`), otherwise by a walk from Python that is much slower on a
large graph. Before 4.6.0 `betweenness_centrality`, `closeness_centrality` and `leiden_communities` answered every
graph merged. For a named graph's answer from one of these, keep that graph in a namespace of its own (the namespace
is IVG's isolation unit), or read its edges with `USE GRAPH` (`MATCH (a)-[r]->(b) RETURN a.node_id, b.node_id`) and run
the algorithm on them yourself.
[Known issues](KNOWN_ISSUES.md#the-nkg-algorithm-procedures-raise-once-a-named-graph-has-edges) has an example.

With the optional Arno library loaded, some algorithms answer from it instead of ObjectScript, with the same results
(the [README](../README.md#where-ivg-lives-in-a-namespace) says what it is). `engine.status().report()` shows which one
answers.

### Cypher procedures

The procedures this section names, with what each takes. Options go in one map, as in
`CALL ivg.closeness({formula: 'classical', topK: 10})`, and an option not listed raises `ValueError` naming the ones
allowed. A `gds.*` procedure takes a graph name first, which IVG accepts and does not read (it has no graph
projections), then a configuration map: `CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'alice',
targetNode: 'erin'})`. The defaults are in parentheses. The four `ivg.*` centrality procedures answer highest score first,
ties by node ID, and `topK` keeps the highest; before 4.6.0 `ivg.closeness` kept the first nodes by ID and
`ivg.eigenvector` ignored `topK`. The community procedures are in [Communities](COMMUNITIES.md#cypher-procedures).
The search procedures (`ivg.vector.search`, `ivg.bm25.search`, `ivg.ivf.search`, `ivg.retrieve`, `ivg.neighbors`)
are not described in this guide yet; [section 7](#7-text-and-vector-search) describes the Python search methods.

| Procedure                          | Arguments                                                                                                          | Options                                                                                                        | Yields                                                               |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `ivg.degreeCentrality`             | none                                                                                                               | `direction` ("out"; "in", "both"), `predicate` (every type), `topK` (10000)                                    | `node`, `score`, `degree`                                            |
| `ivg.betweenness`                  | none                                                                                                               | `sampleSize` (0), `direction` ("out"), `maxHops` (0: no limit), `topK` (10000), `memBudgetMB` (256)            | `node`, `score`                                                      |
| `ivg.closeness`                    | none                                                                                                               | `formula` ("harmonic"; "classical"), `direction` ("out"), `maxHops` (0: no limit), `topK` (10000)              | `node`, `score`                                                      |
| `ivg.eigenvector`                  | none                                                                                                               | `maxIter` (30), `tol` (1e-6), `topK` (10000)                                                                   | `node`, `score`                                                      |
| `ivg.ppr`                          | `seeds` (a node ID or a list), `alpha` (0.85), `maxIter` (20)                                                      | none                                                                                                           | `node`, `score`                                                      |
| `ivg.shortestPath.weighted`        | `from`, `to`, `weightProp` ("weight"), `maxCost` (9999), `maxHops` (10), `direction` ("out"), `types` (every type) | none                                                                                                           | `path`, `totalCost`                                                  |
| `gds.pageRank.stream`              | `graphName` (not read)                                                                                             | `dampingFactor` (0.85), `maxIterations` (20)                                                                   | `nodeId`, `score`                                                    |
| `gds.betweenness.stream`           | `graphName` (not read)                                                                                             | `sampleSize`, `direction`, `maxHops`, `topK` (0: every node)                                                   | `nodeId`, `score`                                                    |
| `gds.shortestPath.dijkstra.stream` | `graphName` (not read)                                                                                             | `sourceNode`, `targetNode` (both needed), `relationshipWeightProperty` (none: unweighted), `relationshipTypes` | `index`, `sourceNode`, `targetNode`, `totalCost`, `nodeIds`, `costs` |

`gds.pageRank.stream` is the global PageRank of `kg_PAGERANK()`, and `gds.betweenness.stream` that of
`betweenness_centrality()`. A `gds.*` call answers `YIELD` and an optional `RETURN` of the yielded names, and nothing
after: sort or cut its rows in Python. An `ivg.*` call takes no `WHERE` straight after `YIELD`; filter in a `WITH`
(`YIELD node, score WITH node, score WHERE score > 5 RETURN ...`), as
[Known issues](KNOWN_ISSUES.md#call--yield--where-is-a-parse-error) shows. The out-degrees here, from the default graph (`USE GRAPH ''`; without it, the
`staging` graph of section 4 adds rows of its own):

```python
engine.execute_cypher(
    "USE GRAPH '' CALL ivg.degreeCentrality({direction: 'out', topK: 2}) YIELD node, score, degree "
    "RETURN node.node_id AS id, score, degree"
).rows
# → [['alice', 0.6, 3], ['bob', 0.4, 2]]
```

The `score` is the degree over the five other nodes, as for `degree_centrality()` above.

---

## 7. Text and vector search

### BM25 text search

A BM25 index covers one graph's nodes, over the properties you name:

```python
engine.bm25_build("bios", ["bio"])["indexed"]
# → 5
[node for node, score in engine.bm25_search("bios", "vector", k=3)]
# → ['bob', 'alice']
```

Five people have a bio (Acme has none). Both Bob's and Alice's bios contain "vector" once; Bob's is shorter, so it
scores higher. Text is lower-cased and split into words, and 77 common English words ("a", "and", "at", "for",
"the" and so on) are left out of documents and queries, so they count in neither a document's length nor a score. `max_df_fraction=` skips query terms that occur in more than that fraction of the documents, and
`bm25_insert` / `bm25_delete` keep an index current. An index built before 4.2.0 needs `bm25_upgrade(name)` once
before it accepts writes.

### Vector search

A node's vector is stored in the IVG Schema's `kg_NodeEmbeddings`, at the engine's `embedding_dimension`. Choose it
before you store vectors: `initialize_schema()` with another width changes an empty vector table to it without a
word, and leaves a table holding vectors at its width, logging an error and listing it in the status's `warnings`, so
every write at the new width is then refused. `initialize_schema(dry_run=True)` shows which it would be
(`embedding_dimension` in its answer). These test
vectors are 768 wide, with only their first two dimensions set:

```python
def vec(*head):
    return list(head) + [0.0] * (768 - len(head))

engine.store_embeddings([
    {"node_id": "alice", "embedding": vec(1.0, 0.0)},
    {"node_id": "bob",   "embedding": vec(0.8, 0.6)},
    {"node_id": "carol", "embedding": vec(0.0, 1.0)},
    {"node_id": "dave",  "embedding": vec(0.6, 0.8)},
])
# → True
[(node, round(score, 3)) for node, score in engine.search_nodes_by_vector(vec(1.0, 0.0), k=3)]
# → [('alice', 1.0), ('bob', 0.8), ('dave', 0.6)]
```

The score is the cosine similarity. `kg_KNN_VEC` takes the query as a JSON array, or as a node ID, whose own vector is
the query and which is left out of the answer:

```python
[(node, round(score, 3)) for node, score in engine.kg_KNN_VEC("alice", k=2)]
# → [('bob', 0.8), ('dave', 0.6)]
```

`label_filter="Person"` keeps nodes with that label; `graph=` searches one named graph's vectors. A query vector of
another width than the stored ones raises `ValueError` naming both widths; it is never answered with no neighbours:

```python
try:
    engine.search_nodes_by_vector([1.0, 0.0, 0.0], k=3)
except ValueError as e:
    refused = str(e)
refused.split(",")[0]
# → 'query vector has 3 dimensions'
```

**Several models.** Vectors from another model, at another width, are stored under a `model_key`, which gives that
model (and graph) a table of its own:

```python
engine.store_embedding("carol", [0.0, 0.0, 1.0], model_key="toy3")
# → True
engine.embedding_count(model_key="toy3")
# → 1
[(node, round(score, 3)) for node, score in engine.search_nodes_by_vector([0.0, 0.0, 1.0], k=1, model_key="toy3")]
# → [('carol', 1.0)]
```

Each table records which model wrote it, and a writer that declares a different model at the same width is refused
with `EmbeddingIdentityConflict`, because vectors from two models at one width compare without error and rank
meaninglessly. `embed_nodes(model=...)` computes and stores vectors with a model you pass;
[Data formats](setup/DATA_FORMATS.md#7-embeddings) shows it.

---

## 8. Temporal edges

A temporal edge is an event between two nodes at a time (a message, a call, a metric sample), with a weight and
optional attributes. One pair can have any number of them. Timestamps are Unix seconds. The examples use
`T = 1_800_000_000` (2027-01-15 08:00 UTC), the start of one of the 5-minute buckets the aggregates are kept in.

```python
T = 1_800_000_000
engine.create_edge_temporal("alice", "MESSAGED", "bob", timestamp=T + 10, weight=1.0, attrs={"channel": "chat"})
# → True
written = engine.bulk_create_edges_temporal([
    {"s": "alice", "p": "MESSAGED", "o": "bob",   "ts": T + 70,  "w": 3.0},
    {"s": "alice", "p": "MESSAGED", "o": "carol", "ts": T + 400, "w": 2.0},
])
int(written), written.collisions
# → (2, 0)
```

Writing the same `(source, type, target, timestamp)` again changes nothing and is counted in `collisions`;
`mode="update"` overwrites instead.

A window, inclusive at both ends:

```python
[(e["o"], e["ts"] - T, e["w"]) for e in engine.get_edges_in_window("alice", "MESSAGED", T, T + 299)]
# → [('bob', 10, 1), ('bob', 70, 3)]
```

The weights written as `1.0` and `3.0` come back as the integers `1` and `3`: the temporal index keeps a weight as an
IRIS number, and IRIS keeps a whole number as an integer, so the `.0` is not stored. They compare equal to the floats
(`1 == 1.0`); call `float()` on `e["w"]` where the type matters. `iter_edges_in_window` answers the same. A plain
edge's `r.weight` is different: it reads back as written (§6, Shortest path).

```python
[type(e["w"]).__name__ for e in engine.get_edges_in_window("alice", "MESSAGED", T, T + 299)]
# → ['int', 'int']
```

`get_edges_in_window` returns at most about 35,000 edges and no attributes. `iter_edges_in_window` reads a window of
any size page by page, each edge with its attributes:

```python
[(e["ts"] - T, e["attrs"]) for e in engine.iter_edges_in_window(T, T + 59, source="alice", predicates=["MESSAGED"])]
# → [(10, {'channel': 'chat'})]
```

Aggregates are kept per 5-minute bucket as the edges are written, so a window of whole buckets costs the same however
many edges it holds. The answer is always exactly the window `[ts_start, ts_end]`: a bucket the window cuts is read
edge by edge, so an edge outside the window is never counted (before 4.6.0 every bucket the window touched counted
whole):

```python
engine.get_temporal_aggregate("alice", "MESSAGED", "count", T, T + 299)
# → 2
engine.get_temporal_aggregate("alice", "MESSAGED", "sum", T, T + 599)
# → 6.0
```

The metrics are `count`, `sum`, `avg`, `min` and `max`. `avg` is rounded to six decimal places on the server: the
mean of the weights 1, 2 and 5 is `2.666667`, not `8 / 3`. For the exact mean, divide `sum` by `count`. `get_bucket_groups(predicate, ts_start, ts_end)` answers
them for every source at once, over exactly the window too (from 4.6.0). `get_edge_velocity` and `find_burst_nodes` count recent
edges relative to now; pass `now_ts=` to anchor them to your data's time.

**Retention.** `purge_raw_before(ts)` deletes the raw edges older than `ts` and keeps the aggregates:

```python
int(engine.purge_raw_before(T + 300))
# → 2
[(e["o"], e["ts"] - T) for e in engine.get_edges_in_window("alice", "MESSAGED", T, T + 599)]
# → [('carol', 400)]
engine.get_temporal_aggregate("alice", "MESSAGED", "count", T, T + 299)
# → 2
```

A purged bucket keeps only its aggregate, so a window that cuts it counts nothing of it; a window of whole buckets
still reads the aggregates.

A temporal write also asserts the plain edge for its `(source, type, target)`, so Cypher and the algorithms see that
the two nodes are connected; the purge removes that edge once none of its timestamps is left, unless an ordinary
write (`create_edge`, Cypher `CREATE`) asserted it too. `delete_edge_temporal(s, p, o, timestamp=...)` deletes one
event. Purging large volumes, and predicates that should not create plain edges at all, are covered in the
[Admin Guide](ADMIN_GUIDE.md).

---

## 9. The revision ledger

The ledger records the graph's history as numbered revisions. It is off until you enable it. A revision is made by
committing a `Changeset`, which applies completely or not at all.

```python
from iris_vector_graph.ledger import Changeset

genesis = engine.ledger.enable()
genesis.seq, genesis.kind
# → (1, 'genesis')

cs = Changeset(actor="app:onboarding", actor_type="ingest", message="new hire")
cs.create_node("frank", labels=["Person"], properties={"name": "Frank"})
cs.create_relationship("frank", "WORKS_AT", "acme")
result = engine.ledger.commit(cs)
result.revision.seq, result.replayed
# → (2, False)
engine.execute_cypher("MATCH (p:Person)-[:WORKS_AT]->(:Company) RETURN count(p) AS staff").rows
# → [[4]]
```

The genesis revision records the graph as it was when the ledger was enabled. History, differences, and a check that
replaying the revisions gives the current tables:

```python
[rev.kind for rev in engine.ledger.history(limit=10).revisions]
# → ['genesis', 'changeset']
changes = engine.ledger.diff(genesis.revision_id, result.revision.revision_id)
sorted({c.entity_kind for c in changes.entries})
# → ['node', 'rel']
```

`engine.ledger.verify()` replays the revisions and compares the result with the current tables; `.result` is
`'equal'` when they agree. The plain edge a temporal write asserts (section 8) is derived from the temporal store and
is not governed by the ledger: neither the genesis nor `verify()` counts it (before 4.6.0 the genesis recorded it, and
`verify()` reported `'diverges'`):

```python
engine.ledger.verify().result
# → 'equal'
```

- **Concurrency.** `Changeset(expected_head=engine.ledger.head().revision_id, ...)` makes `commit` raise
  `StaleHeadError`, writing nothing, if another writer committed first.
- **Retries.** Committing the same actor and operations again returns the first revision with `replayed=True`.
- **Missing endpoints.** A relationship whose endpoint does not exist fails the commit with `NodeNotFoundError`;
  `Changeset(auto_stub_missing_nodes=True)` creates the missing nodes instead.
- **Reading the past.** `ledger.reconstruct(revision_id)` returns the graph as of a revision, and
  `ledger.export_reconstruction(revision_id, path)` writes it as NDJSON for `import_graph_ndjson`.
- **Writes outside the ledger.** `create_node`, `create_edge` and the other methods still work with the ledger on.
  Their writes are not recorded, and `verify()` reports them (`verify(adopt=True)` records them). Strict mode refuses
  them instead:

```python
from iris_vector_graph.ledger import LedgerStrictModeError

engine.ledger.set_strict(True)
try:
    engine.create_node("bypass")
except LedgerStrictModeError:
    bypass_refused = True
engine.ledger.set_strict(False)
bypass_refused
# → True
```

- **Erased tenants.** After a tenant's rows are erased, `ledger.redact(...)` removes their content from the history
  and keeps the history verifiable ([ADR-0013](adr/0013-redaction-removes-content-from-history.md)).
- **Counters.** `engine.ledger.stats()`, `engine.status().ledger`, and a metrics hook for Prometheus
  ([ledger-prometheus-hook.md](ledger-prometheus-hook.md)).

A temporal edge can be part of a changeset too (`cs.create_temporal_edge(...)`), committed in the same transaction as
the structural operations.

---

## 10. Export and snapshots

`import_graph_ndjson` reads IVG's NDJSON format (the format is in [Data formats](setup/DATA_FORMATS.md#3-ndjson)).
With `graph=` it imports into one named graph:

```python
with open("contractors.ndjson", "w") as f:
    f.write(json.dumps({"kind": "node", "id": "gina", "labels": ["Person"], "properties": {"name": "Gina"}}) + "\n")
    f.write(json.dumps({"kind": "node", "id": "hank", "labels": ["Person"], "properties": {"name": "Hank"}}) + "\n")
    f.write(json.dumps({"kind": "edge", "source": "gina", "predicate": "KNOWS", "target": "hank"}) + "\n")

engine.import_graph_ndjson("contractors.ndjson", graph="contractors")
# → {'nodes': 2, 'edges': 1, 'temporal_edges': 0, 'skipped': 0}
engine.list_graphs()
# → ['contractors']
```

A snapshot is a ZIP archive of every graph's rows and globals. `restore_snapshot` empties the namespace's graphs and
loads the archive; `merge=True` adds to what is there instead. `save_snapshot` reads every IVG table and global, so a
login that cannot read one of them gets an error naming it, and no archive.

```python
saved = engine.save_snapshot("people.zip")
saved["tables"]["Graph_KG.nodes"]
# → 9
engine.restore_snapshot("people.zip")["failed_rows"]
# → {}
engine.node_count(), engine.node_count(graph="contractors")
# → (7, 2)
```

The nine nodes are the six from section 2, Frank from the ledger, and Gina and Hank. `node_count()` and
`edge_count()` count one graph, the default graph unless you pass `graph=`, so Gina and Hank count in
`"contractors"`.
`IRISGraphEngine.snapshot_info(path)` reads an archive's contents without a connection.

`export_graph_ndjson` writes the default graph as NDJSON, its nodes and then its edges, in the format
`import_graph_ndjson` reads:

```python
engine.export_graph_ndjson("graph.ndjson")
# → {'nodes': 7, 'edges': 9}
```

The seven nodes are the six from section 2 and Frank; the nine edges are the eight from section 2 and Frank's
`WORKS_AT`. It writes neither the `contractors` graph, where Gina and Hank are, nor temporal edges;
`export_temporal_edges_ndjson(path)` writes those. RDF export (`export_rdf`, Turtle, N-Triples,
N-Quads, JSON-LD) is in the [Semantic Layer guide](SEMANTIC_LAYER.md).

---

## 11. More

**FHIR.** In a namespace that holds an InterSystems FHIR repository, `fhir_graph_register()` reads the repository as
a named graph, one node per resource and one edge per reference, and `fhir_concept_ppr` ranks patients around a
concept. In a namespace without a repository it raises.

<!-- doc-test: skip reason="needs an InterSystems FHIR repository in the namespace" -->

```python
reg = engine.fhir_graph_register()
engine.fhir_graph_rebuild(reg["graph_id"])
```

The model, the sync task and the reports are in [FHIR graph](FHIR_GRAPH.md).

- **RDF, SHACL and PROV-O.** Export to RDF, validate against SHACL shapes, temporal edges as PROV-O:
  [Semantic Layer](SEMANTIC_LAYER.md).
- **Bolt and HTTP.** A neo4j-driver compatible Bolt server and a REST API: [Admin API](ADMIN_API.md).
- **Status.** `engine.status()` reports the namespace's IVG Schema, row counts, the adjacency globals and what
  answers traversals; `engine.status().report()` prints it. Its counts are every graph's together (the report's
  `Graph:` line says `in every graph`), while `node_count()` and `edge_count()` count the default graph, or the one
  named by `graph=`; with a named graph that has nodes the two differ.

---

## Quick reference

| Task                  | Code                                                                  |
| --------------------- | --------------------------------------------------------------------- |
| Set up a namespace    | `engine.initialize_schema()`                                          |
| Add a node            | `engine.create_node("id", labels=[...], properties={...})`            |
| Add an edge           | `engine.create_edge("src", "TYPE", "dst", qualifiers={...})`          |
| Add many              | `engine.bulk_create_nodes([...])`, `engine.bulk_create_edges([...])`  |
| Query                 | `engine.execute_cypher("MATCH ... RETURN ...", {"param": value})`     |
| Read a node           | `engine.get_node("id")`                                               |
| Named graph           | `graph="name"` on writes; `USE GRAPH 'name'` in Cypher                |
| Erase a graph         | `engine.erase_graph("name")`                                          |
| Rebuild `^NKG`        | `engine.sync()`                                                       |
| PageRank              | `engine.kg_PAGERANK()`                                                |
| Personalized PageRank | `engine.kg_PERSONALIZED_PAGERANK(["seed"], graph=None)`               |
| Shortest path         | `MATCH p = shortestPath((a {node_id: $a})-[*]->(b {node_id: $b}))`    |
| Text search           | `engine.bm25_build(name, ["prop"])`, `engine.bm25_search(name, text)` |
| Vector search         | `engine.store_embeddings([...])`, `engine.search_nodes_by_vector(v)`  |
| Temporal edge         | `engine.create_edge_temporal(s, p, o, timestamp=ts, weight=w)`        |
| Temporal window       | `engine.get_edges_in_window(s, p, start, end)`                        |
| Ledger commit         | `engine.ledger.enable()`, `engine.ledger.commit(changeset)`           |
| Snapshot              | `engine.save_snapshot(path)`, `engine.restore_snapshot(path)`         |
| Status                | `engine.status().report()`                                            |
