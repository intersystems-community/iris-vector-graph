<!-- markdownlint-disable MD013 -->

# Known issues

These are the issues open on IVG 4.6.0, each checked on the 4.6.0 build. Application developers' issues are listed
before IRIS operators', and within each group the issues you are most likely to hit come first. Each entry gives the
symptom, the versions affected, a workaround, and what to check.

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

### A query over every graph cannot say which graph a row came from

- **Symptom:** without `USE GRAPH` a query reads every graph, and the same ID in two graphs is two nodes, but no
  function, column or property gives a row's graph: a node's `graph_id` and a relationship's `graph` read as null.
  Two rows for one ID can only be told apart by their other properties.
- **Versions:** 4.6.0.
- **Workaround:** run the query once per graph under `USE GRAPH` and tag the rows yourself: `USE GRAPH ''` for the
  default graph, and the names `engine.list_graphs()` returns, which are the named graphs that hold edges (a graph
  holding only nodes is not listed: keep its name yourself).
- **Check:** the number of rows per ID.

A `City` with ID `bos` in the default graph, beside the `trips` graph's `bos` above:

```python
engine.execute_cypher("CREATE (:City {id: 'bos'})")
engine.execute_cypher("MATCH (c:City {id: 'bos'}) RETURN c.id AS id, c.graph_id AS g")["rows"]
# → [['bos', None], ['bos', None]]
rows = []
for g in [""] + engine.list_graphs():
    r = engine.execute_cypher(f"USE GRAPH '{g}' MATCH (c:City {{id: 'bos'}}) RETURN c.id AS id")
    rows += [[g, *row] for row in r["rows"]]
rows
# → [['', 'bos'], ['trips', 'bos']]
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

### A relationship property computed from the row is refused between matched nodes

- **Symptom:** a `CREATE` or `MERGE` between nodes an earlier `MATCH` bound, whose relationship property is computed
  from the row, raises `GraphWriteError` and writes nothing:
  `MATCH (a:L {name: 'a'}), (b:L {name: 'b'}) CREATE (a)-[:T {w: a.name}]->(b)`. So does a list holding such a value
  (`{l: [a.name, 1]}`) or an expression (`{w: 1 + 2}`). A literal (a list or a map too), a parameter and a `WITH`'s
  column are stored.
- **Cause:** this writer builds the relationship's properties before the rows are read, so it stores only values it
  knows then or can read from a `WITH` stage's column.
- **Versions:** 4.6.0 refuses the write. 4.5.x and earlier 4.6.0 builds created the relationship with none of its
  properties, so the others beside the computed one were lost too.
- **Workaround:** compute the value in a `WITH` and use its column:
  `MATCH (a:L {name: 'a'}), (b:L {name: 'b'}) WITH a, b, a.name AS w CREATE (a)-[:T {w: w}]->(b)`.
- **Check:** `MATCH (:L)-[r:T]->() RETURN properties(r)` answers the stored properties.

### `MERGE ... ON CREATE SET n = {...}` and `ON MATCH SET n = {...}` on a node are refused

- **Symptom:** `MERGE (n:L {k: 1}) ON MATCH SET n = {k: 1, z: 2}` raises `SyntaxError` ("MERGE ... SET n = {...} on a
  node is not supported") and writes nothing; so does `ON CREATE SET n = {...}`. `ON MATCH SET n += {...}`, each
  property (`ON MATCH SET n.z = 2`) and a relationship's `ON MATCH SET r = {...}` are written.
- **Cause:** replacing every property of a node needs the ids of the nodes the MERGE matched or made, which each
  `ON CREATE` / `ON MATCH` branch finds its own way; only the key-by-key writes are built on them.
- **Versions:** 4.6.0 refuses it. 4.5.x and earlier 4.6.0 builds wrote a label named after the map (CHANGELOG, Fixed:
  Cypher writes) and no property.
- **Workaround:** a `SET` after the `MERGE`, which replaces the properties whether the node was made or matched:
  `MERGE (n:L {k: 1}) SET n = {k: 1, z: 2}`; for the matched case alone, a second query
  `MATCH (n:L {k: 1}) SET n = {k: 1, z: 2}` after the `MERGE`.
- **Check:** `MATCH (n:L {k: 1}) RETURN properties(n)` answers the stored properties.

### A property cannot tell a number from a string that spells it

- **Symptom:** a number filter also matches a string that spells the number, and a read can answer the wrong one of
  the two types. With `score` the number 0.95 on one node or relationship and the string `".95"` or `"0.95"` on
  another:
  - `WHERE x.score = 0.95` matches both and `WHERE x.score <> 0.95` matches neither, for a node `n` and a relationship
    `r` alike. openCypher matches only the number, and its `<>` matches the string. The shapes are `= 0.95`, `= $v`,
    the inline `{score: 0.95}`, and for a relationship a filter on a `WITH r.score AS s` column. A string literal has
    the same problem in reverse: `WHERE r.score = '.95'` also matches the number 0.95 written by `create_edge`.
  - `RETURN r.score`, `properties(r)` and a returned `r` read the string `"0.95"` as the number 0.95. The string
    `".95"` stays a string.
  - `ORDER BY` sorts a string that spells a number as that number, and a string that is an ISO 8601 duration
    (`'P1D'`) as a duration, whichever writer stored it, on nodes and relationships: `'10'`, `'9'` sort `'9'`, `'10'`,
    where openCypher sorts strings as text (`'10'` first).
  - Arithmetic reads a string that spells a number as the number: over the string `'7'`, `x.code + 1` answers 8 and
    `x.code * 2` 14, where openCypher answers `'71'` and raises TypeError. Any other string concatenates with `+`
    (`'a7' + 1` is `'a71'`) and raises TypeError with `-`, `*`, `/`, `%` and `^`, as openCypher does. A
    concatenation whose text spells a number reads back as the number: `'' + 1` answers `1`, not `'1'`.
  - A node number that `create_node`, `bulk_create_nodes`, `store_node` or another Python writer stored under
    4.6.3 or earlier is the text `'0.95'`, the same row as the string `"0.95"`, and `RETURN n.score` and `get_node()`
    read it as that string, on both paths and under any version of the IRIS driver. From 4.6.4 those writers store a
    number as a Cypher `CREATE` or `SET` does, and it reads as the float 0.95. Before 4.6.0 the SQL path and
    `get_node()` read the Cypher-written one as IRIS's text, `'.94999999999999995559'`, with intersystems-irispython
    5.4.1 or later. A whole node (`RETURN n`, `collect(n)`, a node in a map, a variable-length path's target) carries each property as
    `RETURN n.score` reads it; before 4.6.0 it carried every value as text. From 4.7.0 `properties(n)` reads each
    value as `RETURN n.score` does too: the earlier release's text and the string `"0.95"` as the string `'0.95'`,
    the stored number as 0.95. Before 4.7.0 it read any text that spells a number as the number, so a string `'30'`
    was 30 in `properties(n)` and `'30'` in `n.s`.
- **Cause:** neither store records a type the readers can rely on. A node property is the text column `rdf_props.val`:
  before 4.6.4 the Python API wrote a number as the text `0.95`, the same text as the string `"0.95"`, and a Cypher
  write (and from 4.6.4 the API) stores the float as an IRIS double. A relationship property is a leaf of the `qualifiers` JSON: `create_edge` writes a JSON number,
  but a Cypher write stores a number as the JSON string `"0.95"`, the same as the string. Existing data cannot be
  re-typed without a stored marker telling the two apart, so the readers treat number-shaped text the way 4.5.x did,
  and `initialize_schema()` leaves an earlier release's text as it is.
- **Versions:** all 4.x for the reads. The filters match every form of the number from 4.6.0; before, an inline or
  literal number missed the API's `0.95` (and a node filter missed the Cypher-written double). From 4.6.0 a node
  value reads with the type it is stored with whatever the driver version; before, which type a Cypher-written number
  read as depended on the query's path and on the driver (5.4.0 read the number, 5.4.1 the text). The Python writers
  store a node number as a number from 4.6.4.
- **Workaround:** keep a property either numeric or text on every node or relationship that has it, and put
  number-like text under a property name of its own. Read a numeric property with `toFloat(x.score)`, which answers
  the float for every stored form. `toString(x.score)` answers a stored string as it is, and a stored number in
  openCypher's text (`'0.95'`, `'1.0'`, `'7'`) from 4.6.0. The string `"0.95"` and the number 0.95 both answer
  `'0.95'`. One form loses its type before any read. `SET r.score = 1.0` on a relationship stores the JSON
  number `1`, so `toString(r.score)` answers `'1'`, as it does for the integer 1. A Cypher `CREATE` or `MERGE` stores
  a relationship's float as Python spells it, so one Python spells with an exponent (`1e-07`, `1e+20`) reads back as
  that string, and so does NaN or an infinity written by any Cypher write (`'nan'`, `'inf'`). To retype a node
  property an earlier release's Python writer stored, when you know it is numeric, read it and write the number back
  with `store_node` (pass `graph=` for a named graph), or from 4.7.0 with a Cypher `SET n.age = toInteger(n.age)`
  (`toFloat` for a float), which stores the number. Before 4.7.0 that SET failed with SQLCODE -23, and a Cypher write
  of a value with the stored text and another type left the string: `SET n.age = 30` over the string `'30'`, or
  `WITH n, toInteger(n.age) AS a SET n.age = a`. IRIS writes no new value whose text equals the old one's; IVG now
  writes such a value into the row itself (`Graph_KG.Prop_Set`).
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

A node number that 4.6.3 or earlier wrote through the API reads back as text (written here as the string it stored);
`toFloat` reads every form as the number:

```python
engine.create_node("m1", labels=["Measure"], properties={"score": "0.95"})
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

### A value written through the ledger is stored as its text

- **Symptom:** `Changeset.set_property` (and a changeset's `create_node` / `upsert_node` properties) sends every
  value as its text: 30 is the string `"30"`, `True` the string `"true"`. A number written through the ledger reads
  back as a string (`n.p` answers `'30'`; a boolean's `'true'` reads as true, as any stored `'true'` does). And the
  ledger cannot retype a value of the same text: setting 30 over a Cypher-stored number 30, or over a string `'30'`,
  is the identical value the ledger skips (no write, no record), so the stored type stays as it was.
- **Versions:** every release with the ledger (spec 213).
- **Workaround:** write a value whose type matters with Cypher (`SET n.p = 30`, or `SET n.p = toInteger(n.p)` to
  retype the text) or `store_node`, outside the changeset; read a ledger-written number with `toInteger()` or
  `toFloat()`. Typed changesets are planned for 4.8.0.
- **Check:** the ledger's record and the stored value are both the text.

```python
from iris_vector_graph.ledger.changeset import Changeset

engine.ledger.enable()
engine.execute_cypher("CREATE (:LedgerText {node_id: 'lt1', n: 30})")
result = engine.ledger.commit(
    Changeset(actor="docs", actor_type="example").set_property("lt1", "p", 30).set_property("lt1", "n", 30)
)
engine.execute_cypher("MATCH (x {node_id: 'lt1'}) RETURN x.p, x.n").rows
# → [['30', 30]]
[(m.attr, m.new) for m in engine.ledger.get_revision(result.revision.revision_id).records]
# → [('p', '30')]
```

### A stored string that spells a boolean, a list or a map reads as one, and `"\u0000"` as the empty string

- **Symptom:** a property holding the string `'true'` or `'false'` reads back as the boolean `True` or `False`, one
  holding JSON list or map text (`'[1, 2]'`, `'{"a": 1}'`) as the list or map, and one holding the one-character
  string `"\u0000"` reads back as `''`, whichever writer stored it (`create_node`,
  `create_edge`, `bulk_create_nodes`, NDJSON, Cypher `CREATE`, `SET` or `MERGE`) and on both paths: `RETURN x.p`,
  `get_node()`, `properties(x)`, a whole node or relationship, `collect()`, a `WITH` column. openCypher reads back the
  strings. Over a string that spells a list, `x.p + 1` answers the list (`[1, 2, 1]`). Only text that starts and ends
  with `[` and `]`, or `{` and `}`, and parses as JSON reads as a list or map: `'[a]'`, `' [1]'` and `'[1, 2'` stay
  strings, and so does a stored list holding NaN or an infinity (`[NaN]` is not JSON), which `get_node()` answered as
  the list before 4.6.0. Filters and `ORDER BY` read the stored text, as before. Only a stored property does this: a
  string the query builds (a literal, a parameter, a `WITH` of one, a string function, an `UNWIND` of a list the
  query holds) answers the string, on `execute_cypher`, REST and Bolt and inside a list or map (4.6.0). In `ORDER BY`,
  though, a string that spells a number or a list sorts as one, stored or not, for the same reason (the sort key reads
  the text): `UNWIND ['7', '10', 'a', '[1]'] AS v RETURN v ORDER BY v` answers `'[1]', 'a', '7', '10'` (openCypher:
  `'10', '7', '[1]', 'a'`), each still the string; `ORDER BY toString(v)` gives the text order. `size()` of a
  stored string answers as for what it spells: TypeError for `'true'`, the list's length for `'[1, 2]'`.
- **Cause:** neither store records a type the readers can rely on (see the entry above): a boolean is stored as the
  text `true` or `false` and a list or map as its JSON, the same as those strings, and IRIS SQL keeps the empty string
  as `$Char(0)`. Data on disk keeps its stored form, so 4.6.0 adds no type marker to it. A later release could mark
  the strings that spell another type when they are written (an idea for a 4.7 spec; a string stored before that
  would still read as it does now).
- **Versions:** all 4.x for booleans and `"\u0000"`. A list or map read back as its JSON text everywhere but
  `get_node()` before 4.6.0; from 4.6.0 every read answers the list or map, and so a string that spells one too.
- **Workaround:** keep such values under a property that holds only strings and read it with a prefix of your own, or
  store a flag as a boolean throughout.
- **Check:** `RETURN x.p` next to `toString(x.p)` shows the type.

```python
engine.create_node("note1", labels=["Note"], properties={"text": "[1, 2]", "tags": ["a", "b"]})
# → True
engine.execute_cypher("MATCH (n:Note {id: 'note1'}) RETURN n.text, n.tags")["rows"]
# → [[[1, 2], ['a', 'b']]]
```

### An unwound boolean or empty string of a list built at run time reads as `1`, `0` or null

- **Symptom:** `MATCH (n:Flag) WITH collect(n.on) AS l UNWIND l AS x RETURN x` answers `'1'` and `'0'` for `true`
  and `false`, and null for `''`; so does an `UNWIND` of any list the query builds as it runs (a `collect()`, a
  stored list property, a list a `WITH` computes). IRIS's `JSON_TABLE`, which the `UNWIND` reads such a list with,
  hands a JSON boolean on as the text `1` or `0` and an empty string as null, so neither can be told from the string
  `'1'` or from a null element. A list whose values the query holds before it runs (a literal, a parameter, a `WITH`
  bound to either) reads its booleans and `''` as themselves (4.6.0 R310), and numbers, strings, lists and maps read
  as their JSON values whatever the list. Also, `collect()` of an `UNWIND`'s booleans holds `1` and `0`.
- **Versions:** all 4.x.
- **Workaround:** keep the flag on the node and read it after the `UNWIND` (`UNWIND ns AS n RETURN n.flag`), or pass
  the list as a parameter.
- **Check:** `RETURN x IS NULL` on the row the empty string should give.

### `nodes(p)` of a variable-length path answers node ids

- **Symptom:** `MATCH p = (a)-[:R*2]->(b) RETURN nodes(p)` answers `['a', 'b', 'c']`, the node ids, where openCypher
  answers the nodes, and so do `tail(nodes(p))` and `reverse(nodes(p))` of such a path. Of a fixed-length path
  `RETURN nodes(p), relationships(p)` answers the nodes and the relationships as `RETURN [a, b]` and `RETURN [r]` spell
  them (`{"_id", "_labels", "_props"}` and `{"type", "props"}` each; before 4.7.0 the ids and the types), and so do
  `tail()` and `reverse()` of them; `relationships(p)` of a variable-length path answers the relationships.
- **Cause:** a path is built in SQL as its ids and types; the nodes are read back where a query reads their
  properties, and a `RETURN` of `nodes(p)` itself of a fixed-length path.
- **Versions:** all 4.x (a fixed-length path's, before 4.7.0).
- **Workaround:** read what you need through the path: `[x IN nodes(p) | x.name]`, `[x IN nodes(p) | labels(x)]`,
  or a fixed-length pattern and `RETURN a, b`.

### A list predicate over a list holding a stored NaN fails

- **Symptom:** over a list built from a node property holding NaN or an infinity a Cypher write stored
  (`any(e IN [n.p] WHERE ...)`, `all(...)`, `none(...)`, `single(...)`), the query fails with SQLCODE -400
  (`Parsing error`). So does an `UNWIND` of such a list (`WITH collect(n.p) AS c UNWIND c AS e RETURN e`).
- **Cause:** the predicates and the `UNWIND` read the list's elements through IRIS's `JSON_TABLE`, whose JSON has no
  NaN or infinity.
- **Versions:** all 4.x. The Python API (`create_node`, `bulk_create_nodes` and the other Python writers) stores NaN
  and the infinities as their text (`'nan'`), so a value it wrote is not affected. It reads back as that string, not
  as a number: for a NaN written by `create_node`, `RETURN n.v, n.v + 1, n.v = n.v, toFloat(n.v)` answers
  `['nan', 'nan1', True, None]`, where a NaN written by Cypher (`SET n.v = 0.0 / 0`) answers `[nan, nan, False, nan]`.
  To get the float back, call `float(value)` in Python on what the query or `get_node()` returned (`float('nan')`);
  Cypher's `toFloat` answers null for the text. To make the stored value a number, write it with Cypher (`SET n.v =
0.0 / 0`).
- **Workaround:** filter such values out first (`WHERE n.p = n.p`, which NaN fails, or `toString(n.p) <> 'NaN'`).
- **Check:** the error names `Parsing error`.

### Two `OPTIONAL MATCH` variable-length shapes are refused

- **Symptom:** an `OPTIONAL MATCH` over a variable-length hop raises `ValueError` naming the hop and the rewrite in
  two cases. The first is a new start walking to an end an earlier clause bound, when the query reads the hop's
  relationships or its path: `MATCH (x:L {name: 'c'}) OPTIONAL MATCH (a:L)-[r:R*1..2]->(x) RETURN a.name, size(r)`.
  The second is a hop from a bound node that walks more than 20,000 paths. The same hop without `r` or the path
  answers, and so does every hop from a bound start, with any `WHERE`, map or labels.
- **Cause:** IRIS cannot LEFT JOIN a `JSON_TABLE` that reads an outer row, so the engine walks such a hop from the
  bound node's rows and writes the paths into the statement. A walk from the bound end runs backwards, so its
  relationships and path would come out in the reverse of the pattern's order. A walk past 20,000 paths would make
  the statement too long.
- **Versions:** 4.6.0 refuses these. 4.5.x and earlier 4.6.0 builds answered them with rows of the wrong width and
  the start nulled, or raised `Undefined variable`.
- **Workaround:** match the hop alone and filter in a `WITH`:
  `OPTIONAL MATCH (a)-[r*1..2]->(b) WITH a, b, r WHERE ...`. That drops the row of nulls a node with no path keeps
  under `OPTIONAL MATCH`. For the bound end, write the pattern from it: `OPTIONAL MATCH (x)<-[r:R*1..2]-(a:L)`.
- **Check:** `"OPTIONAL MATCH" in str(error)` and `"WITH a, b WHERE" in str(error)`.

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

On a hub with 30,000 leaves, listing the paths fails and the two alternatives answer.

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

### A variable-length property map whose value is an expression raises

- **Symptom:** a variable-length relationship property map raises `ValueError` ("takes literals and parameters
  only") where a value is an expression: `MATCH (a:L {name: 'cat'})-[r:R*1..2 {k: a.kind}]->(b) RETURN b.name`. It
  also raises where more than 20,000 paths hold a map SQL cannot check (a float in a list, or a map). Literals and
  parameters answer, under `OPTIONAL MATCH` too.
- **Cause:** the engine checks such a map on each path before the query's SQL reads the paths, so it needs the
  map's values before the rows are read.
- **Versions:** 4.6.0 raises. 4.5.x and earlier 4.6.0 builds compared the values as text and answered for the far
  end's columns only.
- **Workaround:** write the map as a `WHERE` on the hop: `-[r:R*1..2]-> ... WHERE all(x IN r WHERE x.k = a.kind)`.
- **Check:** the query raises `ValueError`; it never answers part of the rows.

### On IRIS 2026.3 a list of many whole nodes unwound by position can fail at Prepare

- **Symptom:** a read that puts many whole nodes, relationships or paths in one list and unwinds its positions twice
  answers no rows and an `IVGResult.error` ending
  `ERROR #5002: ObjectScript error: <LIST>LoadTableFunction+10^%qTable` (SQLCODE -400 at Prepare). It never answers
  wrong rows. The shape is TCK Comparison2 [3]'s:
  `MATCH p = (n)-[r]->() WITH [n, r, p, ...] AS types UNWIND range(0, size(types) - 1) AS i`
  `UNWIND range(0, size(types) - 1) AS j WITH types[i] AS lhs, types[j] AS rhs ...`. Measured on IRIS 2026.3 with
  every cached query purged: lists of 10 elements (the TCK's) and 20 answer with one graph and with two; 40 failed
  for `<=` and `>=` from a labelled start with one graph, and lists of 16 paths or 32 nodes failed in other forms.
  The limit is not a fixed size: IRIS's plan for the statement decides.
- **Cause:** each whole node in the list is its JSON text, read with subqueries, and the stages after it read the
  list. Past a point IRIS 2026.3 fails to load the statement's table functions. 4.6.0 names the list once in each
  `UNWIND` bound and computes each bound as a column of the stage, which moved the limit from about 10 elements to
  past 20.
- **Versions:** 4.6.0 on IRIS 2026.3. 2026.1 was not measured.
- **Workaround:** unwind the list itself rather than its positions, `UNWIND types AS lhs UNWIND types AS rhs`, which
  answers the 40-element form (it also pairs each element with itself); or list the values you compare instead of the
  whole nodes.
- **Check:** `"LoadTableFunction" in result.error`.

### An UNWIND of a list built while the query runs can read a nested list or a boolean as a string

- **Symptom:** an `UNWIND` variable, returned or collected, reads a nested list or map as its JSON text in a string
  (`collect(e)` answers `["[1]"]`), and a boolean as `'1'` or `'0'`, where the unwound list was built while the
  query ran (a property, a `collect()`, a function's answer) and holds lists, maps or booleans beside other kinds.
  For a literal or parameter list it does the same only where a string is the same text as another element: the
  string `'[2]'` beside the list `[2]`, or `'1'` beside a boolean. Every other list known before the query runs
  answers each element as its type, and a list of one kind of scalar always does.
- **Cause:** IRIS's `JSON_TABLE` reads each element into one text column: a nested list as its JSON text and a
  boolean as `'1'` or `'0'`, the same text as the string `'[1]'` or `'1'`. For a list known before the query runs,
  IVG knows which texts are which; for one built at run time it cannot tell.
- **Versions:** 4.6.0 and earlier.
- **Workaround:** return the list itself (`RETURN l`) rather than its unwound elements: a list in a result row
  answers its elements as their types.
- **Check:** the answer holds a string where you expected a list or a boolean.

### A query that reads many properties prepares slowly

- **Symptom:** the first run of a query that reads properties many times on the SQL route takes seconds to prepare.
  Six comparisons of `x.v` with `y.v` (`RETURN x.v < y.v AS a, x.v <= y.v AS b, ...`) join 18 tables and took 41 s
  to prepare on 2026.3 (x86); written with one join per variable and key, the same query is 8 tables and 6.6 s, with
  the same rows. On 2026.3.0AI (ARM64, the laptop container) the query took 2.10 s the first time and 0.003 s the
  second. Bound in a `WITH` first, it took 0.21 s, and both answered `[[True, True, False, False, False, True]]`
  for `v` of 1 and 2.
- **Cause:** the SQL route joins `rdf_props` once for each use of a property, not once per variable and key, and IRIS
  prepares the plan over all of those joins.
- **Versions:** 4.6.0 and earlier; on the 4.6.1 list.
- **Workaround:** bind each property once in a `WITH`, then compare the bound values:
  `MATCH (x), (y) WITH x.v AS xv, y.v AS yv RETURN xv < yv AS a, xv <= yv AS b, ...`. IRIS caches the prepared
  plan, so only the first run of a query text pays the price; a query you run often pays it once.
- **Check:** the first run of the query is much slower than the second.

### The first call of a query with undirected hops compiles slowly

- **Symptom:** a query with up to three anchored undirected hops (`(a {name: $n})-[:L]-(b)-[:L]-(c)`) is rewritten into
  up to 8 `UNION ALL` branches; its first call compiles all of them, which takes seconds for large query shapes. Later
  calls of the same shape are fast.
- **Versions:** 4.3.0 and later (by design).
- **Workaround:** run each such query shape once at startup if first-call latency matters.
- **Check:** time the first and the second call of the shape.

### Named graphs are not accelerated by Arno

- **Symptom:** Arno-accelerated BFS and personalized PageRank read the `^NKG` index, which has no graph dimension. Walks
  in a named graph, including a FHIR graph, always run in ObjectScript or Python. The answers are correct, but slower
  on large graphs.
- **Versions:** all 4.x.
- **Workaround:** none; expect the default graph to be the fast one.
- **Check:** `engine.status()` reports whether Arno is loaded; time the same walk in the default graph.

### `CALL ivg.vector.search ... YIELD node` answers the node's ID, or its three columns

- **Symptom:** `CALL ivg.vector.search('Doc', 'emb', $vec, 5) YIELD node, score`, with a list vector (a literal or a
  parameter) and nothing after the `YIELD` but a `RETURN` of the yielded names, answers `node` as one column holding
  the node ID (`[['doc1', 0.98], ...]`; over Bolt a string, not a Node). The same call with a text query or a seed
  node ID, or with anything else after the `YIELD` (an `ORDER BY`, a `WHERE`, a `MATCH`), answers the node as
  `RETURN n` does, three columns `node_id`, `node_labels`, `node_props`, as USER_GUIDE §3 says of a node a `CALL`
  yields.
- **Cause:** two routes answer the call. The engine's handler sends the vector to each graph's own vector route and
  answers the IDs it finds; the translated SQL reads the node's labels and properties too.
- **Versions:** 4.x. A fix is planned for 4.6.1.
- **Workaround:** read the node from the ID (`engine.get_node(node_id)`), or add an `ORDER BY score DESC` after the
  `YIELD` for the three columns: `CALL ivg.vector.search('Doc', 'emb', $vec, 5) YIELD node, score RETURN node, score
ORDER BY score DESC`.

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

### Inside IRIS, a `SELECT` of a raw VECTOR column raises

- **Symptom:** with embedded Python (`EmbeddedConnection`), fetching the rows of a `SELECT` that returns a VECTOR
  column raises `RuntimeError: <OBJECT DISPATCH>next+9^%SYS.Python.SQLResultSet.1 *Invalid argument type`. IRIS
  raises it while reading the row.
- **Versions:** all 4.x, seen on IRIS 2026.3. Before 4.6.0 IVG's embedded cursor dropped the error and answered an
  empty list.
- **Workaround:** select `VECTOR_COSINE(...)` or a `CAST` of the column instead of the raw vector.
- **Check:** `<OBJECT DISPATCH>` in the error.

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

With a 3-cycle in the default graph and another in graph `ki184`, the procedure raises and names the method, and the
method finds the default graph's cycle.

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

### After an interrupted setup the engine accepts the namespace, and its edge writes fail

- **Symptom:** an `initialize_schema()` stopped part way (the process killed, the connection lost) leaves a namespace
  with some of IVG's tables, routines and classes and no IVG Schema record. Where it got far enough to create 4.6.0's
  SQL objects in the schema, the next engine is not refused: `status().schema` is `None` and its `errors` say
  "setup has not finished here ... Run initialize_schema() again". Where `Graph.KG.EdgeScan` is not yet compiled,
  `create_edge`, `set_edge_weight`, `delete_edge`, `bulk_ingest_edges` and `materialize_inference` raise
  `GraphWriteError` ("its ^KG adjacency write (Graph.KG.EdgeScan.WriteAdjacency) failed; the rows are rolled back,
  nothing written (IVG's classes are not compiled here: if setup did not finish, run initialize_schema() again)").
  Through 4.6.2 `create_edge` wrote the row, logged `create_edge ^KG write failed (BuildKG can recover)` at WARNING and
  answered `True`, so reads that use the adjacency index missed the edge. Where setup stopped earlier the first call
  raises `IVGSchemaError` ("setup has not finished here"), as the
  [errors](ADMIN_GUIDE.md#one-ivg-schema-per-namespace) describe. Seen by the expert walkthrough (Community 2026.1):
  16 of 18 tables left, accepted; 10 of 18, refused.
- **Cause:** the engine's first-call check refuses a namespace that lacks 4.6.0's SQL objects, and these were already
  there.
- **Versions:** 4.6.0 and later. Since 4.6.3 a write that cannot keep `^KG` raises and leaves no row; 4.6.0 to 4.6.2
  answered `True`.
- **Workaround:** run `initialize_schema()` again to the end before writing (it finishes the namespace and rebuilds
  `^KG` from the rows, so edges a 4.6.2 or earlier engine wrote meanwhile are indexed), and check `status().schema` is
  set and its `errors` empty before an application writes.
- **Check:** `engine.status().schema` is `None`, or a write raises `GraphWriteError` naming `Graph.KG.EdgeScan`.

### Upgrading a 2.x install quarantines a named-graph edge whose endpoints are only in the default graph

- **Symptom:** after `initialize_schema()` and `upgrade_to_4_0_0()` on a 2.x install, an edge a 2.x release wrote in a
  named graph between nodes it held only in the default graph is not in `rdf_edges`; it is in
  `Graph_KG.rdf_edges__ivg400unplaced`. The same data upgraded from 3.2.0 places the edge in its graph and gives its
  endpoints a node row there. Nothing is lost: the row is kept, not deleted.
- **Cause:** a 2.x install's edges are restored into the rebuilt `rdf_edges` during `initialize_schema()`, before the
  4.0.0 migration can register the endpoints in the edge's graph, so the rebuilt table's composite keys refuse the edge.
- **Versions:** 4.0.0 and later, upgrading from 2.x.
- **Workaround:** after the upgrade, give each endpoint a node row in the edge's graph and put the edge back:
  `INSERT INTO Graph_KG.nodes (node_id, graph_id) SELECT u.s, u.graph_id FROM Graph_KG.rdf_edges__ivg400unplaced u
WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes n WHERE n.node_id = u.s AND n.graph_id = u.graph_id)`, the same for
  `u.o_id`, then `INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) SELECT s, p, o_id, qualifiers,
graph_id FROM Graph_KG.rdf_edges__ivg400unplaced`, and drop the table (migration/v4.0.0.md, "Your 3.2.0 edges").
- **Check:** `SELECT graph_id, s, p, o_id FROM Graph_KG.rdf_edges__ivg400unplaced` lists the quarantined rows.

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

### On IRIS 2026.3, grouping over an `UNWIND` of a long list reads the list once per element

- **Symptom:** a read that groups over an `UNWIND` of a list a `WITH` holds, such as
  `MATCH (n:Gene) WITH n.family AS f, collect(n) AS ns UNWIND ns AS x RETURN f, count(x)` (also `DISTINCT`, an
  aggregate after the `UNWIND`, or a later aggregating `WITH`), is slower than the same read without the grouping
  once the list's JSON text passes 511 characters (a few nodes, or about 150 small integers). The answer is right, but
  for an empty-string element: read by position, a `''` element reads as null. IRIS
  2026.3 fails the plain statement with `SQLCODE -400 <SUBSCRIPT>` in a `^||%t` temporary keyed by the list's text;
  IVG then runs it once more reading the list by position, which reads the list once for each element, so the cost
  grows with the square of its length (about 0.75 s for two lists of 1,000 nodes). The first time a statement shape
  takes this route the engine logs at INFO "reading it by position instead". A write that groups this way is not
  retried and still raises the `<SUBSCRIPT>` error.
- **Versions:** 4.6.0, on IRIS 2026.3 (`irishealth:2026.3.0AI.113.0`); before 4.6.0 the read failed. IRIS 2026.1
  does not fail the plain statement, so there the read is never retried.
- **Workaround:** aggregate before collecting where the question allows it (`MATCH (n:Gene) RETURN n.family AS f,
count(n)`), or return the unwound rows and group them in Python.
- **Check:** the INFO line from `iris_vector_graph._engine.query`.

### A graph grown one edge at a time inserts its edges about 15 times slower until its tables are tuned

- **Symptom:** `create_edge`, Cypher `CREATE` of a relationship and ledger commits get slower as the graph grows, to
  about 6 ms an edge at 20,000 edges, against 0.4 ms. Setup tunes the tables while they are empty (it must: a table
  IRIS first tunes itself at about one row plans its point lookups as scans of the whole table, which only a
  `TUNE TABLE` at size undoes). With those statistics, each edge insert's existence check is planned as an
  intersection of three indexes: `EXPLAIN SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND
COALESCE(graph_id, '') = ?` shows cost 607 and `idx_edges_graph_oid INTERSECT idx_edges_p_oid INTERSECT
idx_edges_s_p`, where tuned statistics give cost 200 and `Read index map Graph_KG.rdf_edges.u_spo_graph_ekey`. The
  bulk loaders tune the tables after a load (`bulk_load_session`); the row-at-a-time writers never do. The answers
  are correct.
- **Versions:** 4.0.0 and later (setup tunes since spec 230), on any IVG Schema.
- **Workaround:** once a graph has grown by row-at-a-time writes, run `TUNE TABLE <schema>.rdf_edges` and
  `TUNE TABLE <schema>.nodes` (and again after it has grown by an order of magnitude), or load it with the bulk
  loaders. 4.6.1 is to make the plan independent of the table's size.
- **Check:** time 100 `create_edge` calls, or run the `EXPLAIN` above and look for `INTERSECT`.

### A temporal purge costs about 40 µs per purged edge

- **Symptom:** a purge takes about 35 to 45 µs per purged edge, against a design target of about 6 µs, because each
  temporal-only edge row is deleted with its index maintenance. The answer is correct.
- **Versions:** 4.3.0 and later.
- **Workaround:** size purge windows by that rate (about 25,000 edges a second).
- **Check:** time a purge and divide by `deleted` in its result.
