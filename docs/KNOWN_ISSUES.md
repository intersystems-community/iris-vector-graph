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

### A relationship-property filter cannot tell a number from a string that spells it

- **Symptom:** a filter that compares a relationship property with a number also matches relationships where that
  property is a string spelling the same number, and `<>` leaves those relationships out. With `score` set to the number
  0.95 on one relationship and to the string `".95"` (or `"0.95"`) on another, `WHERE r.score = 0.95` matches both and
  `WHERE r.score <> 0.95` matches neither. openCypher matches only the first, and its `<>` matches the string. The
  shapes are `WHERE r.p = 0.95`, `WHERE r.p = $v`, the inline `-[r {p: 0.95}]->`, and a filter on a `WITH r.p AS p`
  column. A string literal has the same problem in reverse: `WHERE r.p = '.95'` also matches the number 0.95 written
  by `create_edge`.
- **Cause:** relationship properties are stored as JSON and compared as the text `JSON_VALUE` reads. That text is
  `.95` for the number 0.95 written by `create_edge`, `0.95` when a Cypher `CREATE` wrote it, and `.95` or `0.95` for
  the strings. So a number filter compares against every spelling of the number, which is what makes API-written and
  Cypher-written numbers match each other.
- **Versions:** 4.6.0. Before 4.6.0, `= $v`, the inline form, and filters after `WITH` matched no number at all.
- **Workaround:** keep a property either numeric or text on every relationship that has it. Put number-like text
  under a property name of its own. When one property already holds both, return it and check the type in Python: a
  `RETURN r.p` keeps `".95"` a string. The string `"0.95"` reads back as the number 0.95, so even this check cannot
  tell that spelling apart.
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

`q2`'s score is a string, so openCypher would answer `[['q1']]`. The workaround reads the type back:

```python
rows = engine.execute_cypher(
    "MATCH (:Scorer {id: 'sc1'})-[r:SCORED]->(q) RETURN q.id, r.score ORDER BY q.id"
)["rows"]
[q for q, score in rows if isinstance(score, float) and score == 0.95]
# → ['q1']
```

### `count(DISTINCT m)` over graphs that share a node id depends on the execution path

- **Symptom:** with no `USE GRAPH` clause, when one node id exists in two graphs, `count(DISTINCT m)` answers 1 on the
  SQL path (it counts ids) and 2 on the native path (it counts graph-and-node pairs).
- **Versions:** 4.3.0 and later.
- **Workaround:** scope the query with `USE GRAPH '<graph>'`.
- **Check:** run the count scoped to each graph and compare.

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
