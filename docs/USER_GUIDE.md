<!-- markdownlint-disable MD013 -->

# IVG User Guide

For Python developers building applications on IVG. Each section is a task, in the order you are likely to meet them.
Method signatures are in the [API Reference](API_REFERENCE.md); running the IRIS side (installation, privileges,
backup) is in the [Admin Guide](ADMIN_GUIDE.md); open problems are in [Known issues](KNOWN_ISSUES.md).

The examples run in order, in one Python session, and build one small graph. Each shows its answer on a `# →` line,
worked out from the graph drawn in section 2.

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

A query that does not parse raises `CypherParseError`. A query that parses but fails in IRIS comes back with
`result.error` set and no rows, unless you pass `raise_on_error=True`, which raises `RuntimeError` instead. Check
`result.error` (or pass `raise_on_error=True`) wherever an empty answer would be taken for "nothing found".

```python
from iris_vector_graph.cypher.parser import CypherParseError

try:
    engine.execute_cypher("MATCH (n RETURN n")
except CypherParseError:
    parse_failed = True
parse_failed
# → True
```

### Where a query runs

A read that IVG's native executor supports (a `MATCH` chain from a labelled node's ID, a property or a label; fixed
and variable-length hops; `WHERE` comparisons; `DISTINCT`, `ORDER BY`, `LIMIT`; `count`; returning properties, not
`node_id`) runs inside IRIS over `^KG`.
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
operators how to turn compiling off. The one known difference between the paths is listed
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
to one graph. A query without it reads every graph, and each node still meets only its own graph's edges:

```python
engine.execute_cypher("USE GRAPH 'staging' MATCH (a {node_id: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name").rows
# → [['Zed']]
engine.execute_cypher("MATCH (a {node_id: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name ORDER BY name").rows
# → [['Bob'], ['Carol'], ['Zed']]
```

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

`erase_all()` empties every graph. `delete_edge(s, p, o, graph=...)` deletes one edge (`all_graphs=True` from every
graph). Python methods that write or read take `graph=`; omitted, they mean the default graph, never every graph.

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

`get_node` reads the default graph unless you pass `graph=`, and returns `None` for a node that is not there. Set a
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

A relationship property comes back with its type: `2019` is an integer, as it was written.

---

## 6. Graph algorithms

The algorithms read the adjacency globals. `^KG` follows every write; the interned copy `^NKG`, which some algorithms
and the Arno library read, is rebuilt by `sync()`. Call it after loading and before running algorithms:

```python
engine.sync()
# → True
```

### PageRank

Global PageRank over the default graph, as `(node, score)` pairs, highest first. It is the textbook algorithm (damping
0.85, 20 iterations): a node with no out-edges spreads its rank over every node, and the scores sum to 1.

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

`graph=` keeps the walk inside one graph, and `exclude_predicates=` leaves edge types out of it.

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
hops, names = r.rows[0]
hops, json.loads(names)
# → (3, ['Alice', 'Carol', 'Dave', 'Erin'])
```

A list in a result row comes back as JSON text, hence `json.loads`. `RETURN p` gives the path itself, as node IDs and
relationship types:

```python
r = engine.execute_cypher(
    "MATCH p = shortestPath((a {node_id: 'alice'})-[:KNOWS*]->(b {node_id: 'erin'})) RETURN p"
)
json.loads(r.rows[0][0])
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

Degree centrality counts edges per node. Out-degree, top two:

```python
[(d["id"], d["degree"]) for d in engine.degree_centrality(direction="out", top_k=2)]
# → [('alice', 3), ('bob', 2)]
```

The others work the same way and return lists of dicts, highest first: `betweenness_centrality`, `closeness_centrality`,
`eigenvector_centrality`, `leiden_communities`, `kg_CDLP`, `strongly_connected_components`, `k_core` and
`triangle_count`. Their parameters are in the API Reference; [Communities](COMMUNITIES.md) explains the community
algorithms.

Betweenness runs Brandes from `sample_size` source nodes and scales by the node count over `sample_size`. The default,
0, uses every source on a graph of up to 200 nodes and 200 of them above that, an approximation. Any `sample_size` at
or above the node count uses every source, so `sample_size=engine.node_count()` is exact on any graph: networkx's
`betweenness_centrality(G, normalized=False)`, or with `direction="both"` that of the undirected graph. A smaller
`sample_size`, even the node count minus one, is an approximation. Here carol and dave each lie on four shortest
paths, and erin on two (carol and dave to acme):

```python
exact = engine.betweenness_centrality(sample_size=engine.node_count())
[(r["id"], r["score"]) for r in exact if r["score"]]
# → [('carol', 4.0), ('dave', 4.0), ('erin', 2.0)]
```

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
scores higher. `max_df_fraction=` skips query terms that occur in more than that fraction of the documents, and
`bm25_insert` / `bm25_delete` keep an index current. An index built before 4.2.0 needs `bm25_upgrade(name)` once
before it accepts writes.

### Vector search

A node's vector is stored in the IVG Schema's `kg_NodeEmbeddings`, at the engine's `embedding_dimension`. These test
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

`label_filter="Person"` keeps nodes with that label; `graph=` searches one named graph's vectors.

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
# → [('bob', 10, 1.0), ('bob', 70, 3.0)]
```

`get_edges_in_window` returns at most about 35,000 edges and no attributes. `iter_edges_in_window` reads a window of
any size page by page, each edge with its attributes:

```python
[(e["ts"] - T, e["attrs"]) for e in engine.iter_edges_in_window(T, T + 59, source="alice", predicates=["MESSAGED"])]
# → [(10, {'channel': 'chat'})]
```

Aggregates are kept per 5-minute bucket as the edges are written, so they cost the same however many edges a window
holds:

```python
engine.get_temporal_aggregate("alice", "MESSAGED", "count", T, T + 299)
# → 2
engine.get_temporal_aggregate("alice", "MESSAGED", "sum", T, T + 599)
# → 6.0
```

The metrics are `count`, `sum`, `avg`, `min` and `max`. `get_edge_velocity` and `find_burst_nodes` count recent
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
# → {'nodes': 2, 'edges': 1, 'temporal_edges': 0}
engine.list_graphs()
# → ['contractors']
```

A snapshot is a ZIP archive of every graph's rows and globals. `restore_snapshot` empties the namespace's graphs and
loads the archive; `merge=True` adds to what is there instead.

```python
saved = engine.save_snapshot("people.zip")
saved["tables"]["Graph_KG.nodes"]
# → 9
engine.restore_snapshot("people.zip")["failed_rows"]
# → {}
engine.node_count()
# → 9
```

The nine nodes are the six from section 2, Frank from the ledger, and Gina and Hank.
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
  answers traversals; `engine.status().report()` prints it.

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
