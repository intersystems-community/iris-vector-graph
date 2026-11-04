<!-- markdownlint-disable MD013 -->

# Known issues

The issues open on IVG 4.6.0, checked on the 4.6.0 build. They come in two groups: for application developers, then
for IRIS operators. Within each group the issues you are most likely to hit come first. Each entry gives the symptom,
the versions affected, a workaround, and what to check.

Issues fixed in earlier releases, and older entries that could not be confirmed on 4.6.0, are kept in
[the known-issues history](dev/notes/known-issues-history.md). Fixes are listed in [the changelog](../CHANGELOG.md).

The examples run against an engine opened as in the README's first query:

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
```

## For application developers

### A Cypher `CREATE` of a relationship that already exists adds nothing

- **Symptom:** outside multigraph mode, `CREATE (a)-[:R]->(b)` between a pair that already has an `R` relationship
  returns no error and writes nothing. openCypher adds a second, parallel relationship.
- **Versions:** all 4.x. Early 4.0 builds raised `SQLCODE -119` here instead.
- **Workaround:** turn on multigraph mode for the graph (`engine.set_multigraph(graph, True)`) when you need parallel
  relationships, or use `MERGE` when you mean "at most one".
- **Check:** count the relationships after the write.

```python
engine.set_multigraph("trips", True)
engine.execute_cypher("USE GRAPH 'trips' CREATE (:City {id: 'bos'}), (:City {id: 'nyc'})")
for _ in range(2):
    engine.execute_cypher("USE GRAPH 'trips' MATCH (a {id: 'bos'}), (b {id: 'nyc'}) CREATE (a)-[:TRIP]->(b)")
engine.execute_cypher("USE GRAPH 'trips' MATCH (:City {id: 'bos'})-[r:TRIP]->(:City {id: 'nyc'}) RETURN count(r) AS n")["rows"]
# → [[2]]
```

### A `CALL { ... }` subquery's column comes first, whatever `RETURN` says

- **Symptom:** in `... CALL { WITH a ... RETURN count(q) AS deg } RETURN a.node_id AS id, deg` the result's columns are
  `['deg', 'id']`.
- **Versions:** all 4.x (a deliberate deviation).
- **Workaround:** read rows by column name, not by position.
- **Check:** `result["columns"]`.

```python
for n in ("co1", "co2", "co3"):
    engine.create_node(n, labels=["CO"])
engine.create_edge("co1", "R", "co2")
engine.create_edge("co1", "R", "co3")
r = engine.execute_cypher(
    "MATCH (a:CO {id: 'co1'}) CALL { WITH a MATCH (a)-[:R]->(q) RETURN count(q) AS deg } "
    "RETURN a.node_id AS id, deg"
)
[dict(zip(r["columns"], row)) for row in r["rows"]]
# → [{'id': 'co1', 'deg': 2}]
```

### Division by zero answers `null` instead of raising

- **Symptom:** `x / 0` and `x % 0` answer NaN (JSON `null`) with no error. openCypher raises an arithmetic error.
- **Versions:** all 4.x (by design).
- **Workaround:** test the divisor yourself when the difference matters: `CASE WHEN d = 0 THEN ... ELSE n / d END`.
- **Check:** a `null` or NaN where you expected a number.

### A temporal purge keeps the endpoint nodes of the edges it removes

- **Symptom:** `purge_raw_before` and `purge_before` delete the old temporal edges but keep every node at either end, so
  a workload that creates a new target node per event grows edgeless nodes without limit.
- **Versions:** 4.4.1 and later (the option exists; the default is unchanged).
- **Workaround:** pass `retire_bare_nodes=True`. For nodes left by earlier purges, run
  `engine.erase_bare_nodes(graph, dry_run=True)` to see what would go, then `engine.erase_bare_nodes(graph)`.
- **Check:** `nodes_retired` in the result.

```python
t0 = 1_700_000_000
for i in range(3):
    engine.create_edge_temporal("sensor", "EMITS", f"reading{i}", timestamp=t0 + i)
engine.purge_raw_before(t0 + 100, retire_bare_nodes=True).nodes_retired
# → 4
```

The sensor and its three readings have no edges left, so all four go.

### A `CREATE` after a `WITH` stage fails when more than one row reaches it

- **Symptom:** `MATCH (n:Gene) WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (c:Copy {src: i})` raises
  `IntegrityError ... SQLCODE -119` (constraint `pk_props`) and writes nothing, because every row gets the same
  generated node id. Giving the node a computed id (`{id: i + '-copy'}`) fails with `<ARGUMENT ERROR>`.
- **Versions:** 4.1.0 and later.
- **Workaround:** put the `CREATE` straight after the `MATCH`, with no `WITH` stage, or create the nodes from Python.
- **Check:** count the created nodes.

```python
for g in ("g1", "g2", "g3"):
    engine.create_node(g, labels=["Gene"])
engine.execute_cypher("MATCH (n:Gene) CREATE (c:Copy {src: n.node_id})")
engine.execute_cypher("MATCH (c:Copy) RETURN count(c) AS n")["rows"]
# → [[3]]
```

### A property cannot tell a number from a string that spells it

- **Symptom:** a number filter also matches a string that spells the number, and a read can answer the wrong one of
  the two types. With `score` the number 0.95 on one node or relationship and the string `".95"` or `"0.95"` on
  another:
  - `WHERE x.score = 0.95` matches both and `WHERE x.score <> 0.95` matches neither, for a node `n` and a relationship
    `r` alike. openCypher matches only the number, and its `<>` matches the string. The shapes are `= 0.95`, `= $v`,
    the inline `{score: 0.95}`, and for a relationship a filter on a `WITH r.score AS s` column. A string literal has
    the same problem in reverse: `WHERE r.score = '.95'` also matches the number 0.95 written by `create_edge`.
  - `RETURN r.score` and `properties(r)` read the string `"0.95"` as the number 0.95. The string `".95"` stays a
    string.
  - `RETURN n.score` and `get_node()` read the number 0.95 as the string `'0.95'` when `create_node`,
    `bulk_create_nodes` or `store_node` wrote it, and as the float 0.95 when a Cypher `CREATE` or `SET` wrote it, on
    both paths and under any version of the IRIS driver. Before 4.6.0 the SQL path and `get_node()` read the
    Cypher-written one as IRIS's text, `'.94999999999999995559'`, with intersystems-irispython 5.4.1 or later. A
    whole node (`RETURN n`, `collect(n)`, a node in a map or in a variable-length path) carries each property as
    `RETURN n.score` reads it; before 4.6.0 it carried every value as text. `properties(n)` reads both as 0.95, and
    reads the string `"0.95"` as 0.95 too.
- **Cause:** neither store records a type the readers can rely on. A node property is the text column `rdf_props.val`:
  the API writes a number as the text `0.95`, the same text as the string `"0.95"`, and a Cypher write stores the float
  as an IRIS double. A relationship property is a leaf of the `qualifiers` JSON: `create_edge` writes a JSON number,
  but a Cypher write stores a number as the JSON string `"0.95"`, the same as the string. Existing data cannot be
  re-typed without a stored marker telling the two apart, so the readers treat number-shaped text the way 4.5.x did.
- **Versions:** all 4.x for the reads. The filters match every form of the number from 4.6.0; before, an inline or
  literal number missed the API's `0.95` (and a node filter missed the Cypher-written double). From 4.6.0 a node
  value reads with the type it is stored with whatever the driver version; before, which type a Cypher-written number
  read as depended on the query's path and on the driver (5.4.0 read the number, 5.4.1 the text).
- **Workaround:** keep a property either numeric or text on every node or relationship that has it, and put
  number-like text under a property name of its own. Read a numeric property with `toFloat(x.score)`, which answers
  the float for every stored form. `toString(x.score)` answers a stored string as it is, and a stored number in
  openCypher's text (`'0.95'`, `'1.0'`, `'7'`) from 4.6.0. The string `"0.95"` and the number 0.95 both answer
  `'0.95'`. One form has lost its type before the read: `SET r.score = 1.0` on a relationship stores the JSON
  number `1`, so `toString(r.score)` answers `'1'`, as it does for the integer 1.
- **Check:** return the property next to the filter's answer and look at the types.

```python
engine.create_node("sc1", labels=["Scorer"])
for q in ("q1", "q2"):
    engine.create_node(q, labels=["Answer"])
engine.create_edge("sc1", "SCORED", "q1", qualifiers={"score": 0.95})
engine.create_edge("sc1", "SCORED", "q2", qualifiers={"score": ".95"})
engine.execute_cypher(
    "MATCH (:Scorer {id: 'sc1'})-[r:SCORED]->(q) WHERE r.score = 0.95 RETURN q.id ORDER BY q.id"
)["rows"]
# → [['q1'], ['q2']]
```

`q2`'s score is a string, so openCypher would answer `[['q1']]`. Reading the type back tells `".95"` apart, but not
the string `"0.95"`:

```python
engine.create_node("q3", labels=["Answer"])
engine.create_edge("sc1", "SCORED", "q3", qualifiers={"score": "0.95"})
engine.execute_cypher(
    "MATCH (:Scorer {id: 'sc1'})-[r:SCORED]->(q) RETURN q.id, r.score ORDER BY q.id"
)["rows"]
# → [['q1', 0.95], ['q2', '.95'], ['q3', 0.95]]
```

A node number written by the API reads back as text; `toFloat` reads every form as the number:

```python
engine.create_node("m1", labels=["Measure"], properties={"score": 0.95})
engine.execute_cypher("CREATE (:Measure {id: 'm2', score: 0.95})")
engine.execute_cypher(
    "MATCH (m:Measure) RETURN m.id, m.score, toFloat(m.score) ORDER BY m.id"
)["rows"]
# → [['m1', '0.95', 0.95], ['m2', 0.95, 0.95]]
```

`toString` spells both as openCypher does, and the relationship scores above too (`q2`'s is the string `".95"`):

```python
engine.execute_cypher("MATCH (m:Measure) RETURN m.id, toString(m.score) ORDER BY m.id")["rows"]
# → [['m1', '0.95'], ['m2', '0.95']]
engine.execute_cypher(
    "MATCH (:Scorer {id: 'sc1'})-[r:SCORED]->(q) RETURN q.id, toString(r.score) ORDER BY q.id"
)["rows"]
# → [['q1', '0.95'], ['q2', '.95'], ['q3', '0.95']]
```

### A variable-length read from a hub fails past IRIS's string limit

- **Symptom:** a variable-length read with no relationship variable and no `DISTINCT`, such as
  `MATCH (h {node_id: 'hub'})-[*1..2]->(x) RETURN x.node_id`, lists every path, as openCypher says it should. IVG
  builds each start node's paths as one JSON string (`CY_VLP_PATHS`), about 150 characters a path, and IRIS refuses a
  string longer than 3,641,144 characters. Past that the read answers no rows and an `IVGResult.error` ending
  `ObjectScript error: <MAXSTRING>zCYVLPPATHS+1^Graph.KG.funcCYVLPPATHS.1`; `raise_on_error=True` raises it as
  `RuntimeError`. Measured on a star with 12-character node IDs and no relationship properties: 23,798 leaves
  answer, 23,799 fail. Longer IDs or relationship properties lower the count. `count(x)`, `count(*)`, a relationship
  variable (`-[r*1..2]->`), and `DISTINCT` with `ORDER BY` or `LIMIT` read the same paths and fail the same way.
- **Versions:** 4.6.0. 4.5.x answered such a read with fewer rows than it has paths, some of them wrong.
- **Workaround:** ask for the nodes rather than the paths. `RETURN DISTINCT x.node_id` and `count(DISTINCT x)` take
  another route, which holds no path, and answer on any hub; sort or cut the `DISTINCT` rows in Python.
- **Check:** `"<MAXSTRING>" in result.error`.

A hub with 30,000 leaves: listing the paths fails, and the two alternatives answer.

```python
engine.bulk_create_edges(
    [{"source_id": "ki071hub", "predicate": "R", "target_id": f"ki071l{i:05d}"} for i in range(30000)]
)
r = engine.execute_cypher("MATCH (h {node_id: 'ki071hub'})-[*1..2]->(x) RETURN x.node_id")
(r.rows, "<MAXSTRING>" in r.error)
# → ([], True)
```

The nodes reached, each once:

```python
rows = engine.execute_cypher("MATCH (h {node_id: 'ki071hub'})-[*1..2]->(x) RETURN DISTINCT x.node_id").rows
(len(rows), sorted(rows)[0])
# → (30000, ['ki071l00000'])
```

How many:

```python
engine.execute_cypher("MATCH (h {node_id: 'ki071hub'})-[*1..2]->(x) RETURN count(DISTINCT x) AS n").rows
# → [[30000]]
```

### The first call of a query with undirected hops compiles slowly

- **Symptom:** a query with up to three anchored undirected hops (`(a {name: $n})-[:L]-(b)-[:L]-(c)`) is rewritten into
  up to 8 `UNION ALL` branches; its first call compiles all of them, which takes seconds for large query shapes. Later
  calls of the same shape are fast.
- **Versions:** 4.3.0 and later (by design).
- **Workaround:** run each such query shape once at startup if first-call latency matters.
- **Check:** time the first and the second call of the shape.

### Named graphs are not accelerated by Arno

- **Symptom:** Arno-accelerated BFS and personalized PageRank read the `^NKG` index, which has no graph dimension. Walks
  in a named graph, including a FHIR graph, always run in ObjectScript or Python: the answers are correct, but slower
  on large graphs.
- **Versions:** all 4.x.
- **Workaround:** none; expect the default graph to be the fast one.
- **Check:** `engine.status()` reports whether Arno is loaded; time the same walk in the default graph.

### The SQL vector procedures read only the default graph's vectors

- **Symptom:** `CALL`/`SELECT ... FROM Graph_KG.kg_KNN_VEC(...)` or `kg_RRF_FUSE(...)` with a named graph whose
  vectors were written through the API raises an error (`SQLCODE -149`). The error names the graph's route table and
  says to use `engine.kg_KNN_VEC`, `engine.kg_RRF_FUSE` or `CALL ivg.retrieve`. Both procedures read
  `kg_NodeEmbeddings`, and a named graph's vectors are in its own route (`kg_emb_<hash>`). Before 4.6.0 they answered
  no rows for that graph, or failed with `SQLCODE -257` on the route's width.
- **Versions:** all 4.x; the error is new in 4.6.0.
- **Workaround:** search a named graph through the engine (`engine.kg_KNN_VEC(..., graph=g)`,
  `engine.kg_RRF_FUSE(..., graph=g)`), through Cypher (`USE GRAPH 'g' CALL ivg.retrieve(...)`), or through the
  `/vectorSearch` and `/hybridSearch` routes, which read the route. The default graph is read by the procedures as
  before.
- **Check:** `engine.resolve_route(graph=g, model_key=None).table_name` names the table the graph's vectors are in.

### The RDF extra needs rdflib 7

- **Symptom:** IVG's SPARQL and SHACL export builds rdflib `ConjunctiveGraph`, which rdflib 8 removes, so the `[rdf]`
  extra pins `rdflib<8`. Another package that needs rdflib 8 conflicts with it.
- **Versions:** all 4.x.
- **Workaround:** keep rdflib 7 in the environment that runs IVG's RDF features.
- **Check:** `pip show rdflib`.

### Inside IRIS, a `SELECT` of a raw VECTOR column answers no rows

- **Symptom:** with embedded Python (`EmbeddedConnection`), a `SELECT` that returns a VECTOR column answers an empty
  list and no error. IRIS raises `<OBJECT DISPATCH>` while reading the row, and IVG's embedded cursor drops it.
- **Versions:** all 4.x, seen on IRIS 2026.3.
- **Workaround:** select `VECTOR_COSINE(...)` or a `CAST` of the column instead of the raw vector.
- **Check:** compare the row count with the same query over the external driver.

### The `^NKG` algorithm procedures raise once a named graph has edges

- **Symptom:** `CALL ivg.betweenness`, `ivg.closeness`, `ivg.eigenvector`, `ivg.leiden`, `ivg.triangleCount`,
  `ivg.scc` and `ivg.kcore` raise `ValueError` without `USE GRAPH` on a namespace where a named graph has an edge. A
  query without `USE GRAPH` answers the union of each graph's own answer, and these cannot answer per graph: they read
  `^NKG`, which has no graph key, and once a named graph has an edge it holds those edges merged with the default
  graph's (a node ID used in two graphs is one node with both graphs' edges). The message names the Python method that
  answers the default graph: `betweenness_centrality`, `closeness_centrality`, `eigenvector_centrality`,
  `leiden_communities`, `triangle_count`, `strongly_connected_components` or `k_core`. On such a namespace those
  methods read the default graph's own edges, through Arno when it is loaded (the community methods) or a walk from
  Python, which on a large graph is much slower than reading `^NKG`. `gds.pageRank.stream`, `gds.betweenness.stream`
  and `gds.shortestPath.dijkstra.stream`, which read the default graph alone, raise there too.
- **Versions:** 4.6.0. Before, the procedures, and `betweenness_centrality`, `closeness_centrality` and
  `leiden_communities`, answered every graph merged, while the other four methods answered the default graph, so a
  method and its Cypher form disagreed; the `gds.*` procedures answered the default graph as if it were every graph.
  Under `USE GRAPH` the `^NKG` procedures raise `ValueError` too.
- **Workaround:** call the Python method for the default graph's answer, or the `gds.*` procedure under
  `USE GRAPH ''`. For a named graph's answer, keep that graph in a namespace of its own (the namespace is IVG's
  isolation unit), or read its edges with `USE GRAPH` and run the algorithm on them yourself. `ivg.degreeCentrality`
  and `ivg.ppr` answer per graph: the `USE GRAPH` graph, or without it a row per graph and node.
- **Check:** `engine.list_graphs()` lists the named graphs that hold edges; while it is empty the procedures answer.

A 3-cycle in the default graph and another in graph `ki184`: the procedure raises, naming the method, and the method
finds the default graph's cycle.

```python
for graph, ids in ((None, "abc"), ("ki184", "xyz")):
    for i in ids:
        engine.create_node(f"ki184{i}", graph=graph)
    for s, o in zip(ids, ids[1:] + ids[:1]):
        engine.create_edge(f"ki184{s}", "R", f"ki184{o}", graph=graph)
engine.sync()
try:
    engine.execute_cypher("CALL ivg.scc() YIELD node RETURN node.node_id AS id")
except ValueError as e:
    message = str(e)
"engine.strongly_connected_components()" in message
# → True
sorted(d["id"] for d in engine.strongly_connected_components() if d["id"].startswith("ki184"))
# → ['ki184a', 'ki184b', 'ki184c']
```

One named graph's edges, to run an algorithm on yourself:

```python
engine.execute_cypher(
    "USE GRAPH 'ki184' MATCH (a)-[r]->(b) RETURN a.node_id AS s, b.node_id AS o ORDER BY s"
)["rows"]
# → [['ki184x', 'ki184y'], ['ki184y', 'ki184z'], ['ki184z', 'ki184x']]
```

## For IRIS operators

### Two processes with different embedding widths re-declare the vector column

- **Symptom:** two processes that call `initialize_schema()` on one namespace with different `embedding_dimension`
  values each succeed with no warning; the last one re-declares an empty vector column to its width. The other process's
  writes then fail with `EmbeddingIdentityConflict`.
- **Versions:** 3.2.0 and later.
- **Workaround:** use one embedding width per namespace, or per `(graph, model_key)` route.
- **Check:** `SELECT table_name, graph_id, model_key, dimension, set_by FROM Graph_KG.embedding_registry`.

### Snapshots made through the REST API land in the temporary directory

- **Symptom:** `POST /admin/snapshot` writes `ivg_snapshot_<time>.snapshot` to `IVG_SNAPSHOT_DIR`, and with the
  variable unset to the system temporary directory (`tempfile.gettempdir()`, `/tmp` on Linux), where a reboot can
  delete it.
- **Versions:** every release with the REST API, 4.x included.
- **Workaround:** set `IVG_SNAPSHOT_DIR` to a durable directory for the API process.
- **Check:** the `path` in the response.

### A snapshot restore does not bring back a FHIR graph's registration or crosswalk

- **Symptom:** restoring a snapshot brings back a FHIR graph's nodes and edges but not its `fhir_graphs` registration or
  its `code_crosswalk` rows.
- **Versions:** 4.1.0 and later.
- **Workaround:** after a restore, run `engine.fhir_graph_register()` and `engine.fhir_graph_rebuild(graph)`, and
  re-load the crosswalk; keep the crosswalk source under version control.
- **Check:** `SELECT COUNT(*) FROM Graph_KG.fhir_graphs` and `SELECT COUNT(*) FROM Graph_KG.code_crosswalk` before and
  after the restore.

### A temporal purge costs about 40 µs per purged edge

- **Symptom:** a purge takes about 35 to 45 µs per purged edge, against a design target of about 6 µs, because each
  temporal-only edge row is deleted with its index maintenance. The answer is correct.
- **Versions:** 4.3.0 and later.
- **Workaround:** size purge windows by that rate (about 25,000 edges a second).
- **Check:** time a purge and divide by `deleted` in its result.
