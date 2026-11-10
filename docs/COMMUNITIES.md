<!-- markdownlint-disable MD013 -->

# Communities and components

For application developers who want to group a graph's nodes: Leiden communities, triangle counts with the local
clustering coefficient, strongly and weakly connected components, k-core decomposition and label propagation (CDLP).
Signatures are in the [API reference](API_REFERENCE.md); timings are in [Benchmarks](BENCHMARKS.md#graph-algorithms).

The Python methods answer the default graph. A Cypher query without `USE GRAPH` answers the union of each graph's
own answer, and these procedures cannot give that: they, and the methods' fastest paths, read `^NKG`, which holds
every graph's edges together (it has no graph dimension) and is rebuilt by `engine.sync()`, so call `sync()` after
writing and before running them. While the default graph is the only one with edges, `^NKG` is exactly its edges and
the procedures answer. Once a named graph has an edge, the procedures raise `ValueError` naming the method to call
instead, and the methods read the default graph's own adjacency (`^KG`). `USE GRAPH` with a procedure always raises
`ValueError` ([Cypher procedures](#cypher-procedures)).

Every algorithm here answers every node of the graph. A node with no edge to another node (none at all, or only
self-loops) is a community and a component of its own, has coreness 0 and no triangles, and keeps its own CDLP label;
before 4.6.0 such a node was in no answer. The procedures give the same rows.

## The example graph

Two directed triangles joined by one edge, `c -> x`. Every answer on this page is worked from it by hand.

```text
a -> b -> c -> a        x -> y -> z -> x        c -> x
```

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

for s, t in [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")]:
    engine.create_edge(s, "LINKS", t)
engine.sync()
```

`groups` turns a list of `{"id": ..., <key>: ...}` rows into the sets of IDs that share a key, so an answer does not
depend on which group an algorithm numbered first:

```python
def groups(rows, key):
    by = {}
    for r in rows:
        by.setdefault(r[key], []).append(r["id"])
    return sorted(sorted(ids) for ids in by.values())
```

## Leiden: `engine.leiden_communities()`

Leiden (Traag, Waltman and van Eck, 2019) partitions the graph into communities and guarantees each is connected. At
`gamma=1.0` it optimizes modularity; any other `gamma` uses the Constant Potts Model (CPM): a community scores the
edges inside it less `gamma` times its number of pairs of members, so a lower `gamma` gives fewer, larger communities,
and on these unweighted graphs any `gamma` above 1 gives every node a community of its own. This is
`leidenalg.CPMVertexPartition` with `resolution_parameter=gamma`, and every path below means it. `random_seed` makes a
run repeatable.

Every path reads the graph as undirected and simple and ignores edge weights: two nodes linked in either direction,
by any number of edges, are one edge of weight 1, as in Neo4j GDS's Louvain without `relationshipWeightProperty`.
To compare a partition with networkx, score both on that graph without weights:
`nx.community.modularity(nx.Graph(G.to_undirected()), parts, weight=None)`. networkx's default, `weight="weight"`,
scores a different objective, and on a graph whose edges carry unequal weights an IVG partition can score below zero
there.

```python
communities = engine.leiden_communities(random_seed=42, top_k=0)
groups(communities, "community")
# → [['a', 'b', 'c'], ['x', 'y', 'z']]
```

Each row is `{"id", "community", "size"}`; community 0 is the largest, and `top_k=0` returns every node (the default
is the first 10,000 rows). The first path that answers is used:

1. `Graph.KG.Communities.LeidenJsonAuto` in IRIS: `leidenalg` in IRIS's embedded Python when `igraph` and `leidenalg`
   are installed there (`/usr/irissys/mgr/python`), otherwise Louvain in ObjectScript over `^NKG`. Louvain does not
   guarantee connected communities, and ignores `random_seed`: it visits nodes in `^NKG`'s order, so it answers the
   same each time. Before 4.6.0 this step ran Louvain's first phase only and stopped short (modularity 0.311 on
   Zachary's karate club, where networkx's Louvain reaches 0.415 to 0.419).
2. The Arno library's Leiden, when Arno is loaded.
3. In this Python process, over `^KG`: `leidenalg` from the `[communities]` extra, else networkx's Louvain, a different
   and weaker algorithm.

`IVG_DISABLE_ARNO=1` skips step 2. On a graph of more than 100,000 nodes, `top_k=0` warns that the answer may be a
large JSON string.

**What `top_k` (Cypher `topK`) cuts.** It counts rows, on every path: the first `top_k` rows, community 0's first,
which can end part-way through a community (each row's `size` still says the whole community's). On a graph of three
cliques of 4, 3 and 2 nodes, `top_k=1` answers one row of community 0 and `top_k=5` its 4 rows and one of
community 1. Before 4.7.0 step 1 kept a community whole: it added communities from the largest down and stopped after
the one that brought the rows to `top_k`, so on that graph `top_k=1` answered 4 rows and `top_k=5` 7. For the whole
partition pass `top_k=0`, and cut it yourself on `community`.

With `gamma` other than 1.0, step 2 is skipped (Arno's answer to the CPM has not been checked against leidenalg's),
and step 3 needs `leidenalg`: networkx's Louvain has no CPM, so without the extra the call raises and says so. The
ObjectScript Louvain in step 1 scores CPM moves itself; being Louvain, it can land below Leiden at a small `gamma`
(on the karate club at 0.1 its CPM quality is 74.8, leidenalg's 85.8; at 0.5 both are 26). Before 4.6.0 that
Louvain and the networkx fallback read `gamma` as modularity's resolution, a different objective, so the same call
gave different communities by path.

## Triangles: `engine.triangle_count()`

For each node, the triangles through it over its neighbours in either direction (self-loops and parallel edges count
once), and the local clustering coefficient: triangles divided by the pairs of neighbours, `k(k-1)/2`. Here `c` has
three neighbours (`a`, `b`, `x`) and one triangle, so its coefficient is 1/3; `a`, `b`, `y` and `z` have two
neighbours and one triangle:

```python
[(r["id"], r["triangles"], round(r["lcc"], 3)) for r in engine.triangle_count(top_k=0)]
# → [('a', 1, 1.0), ('b', 1, 1.0), ('c', 1, 0.333), ('x', 1, 0.333), ('y', 1, 1.0), ('z', 1, 1.0)]
```

Rows are sorted by triangle count, largest first, then by ID in IRIS collation (a number before text: `9`, `10`, `a`),
and `top_k` keeps the first rows of that order. The count follows `networkx.triangles` on the undirected graph.

## Strongly connected components: `engine.strongly_connected_components()`

Sets in which every node reaches every other along the edges' direction. `c -> x` goes one way only, so each triangle
is its own component:

```python
groups(engine.strongly_connected_components(top_k=0), "component")
# → [['a', 'b', 'c'], ['x', 'y', 'z']]
```

Rows are `{"id", "component", "size"}`, component 0 the largest. The algorithm is Tarjan's, iterative, so a deep graph
does not reach Python's recursion limit.

## K-core: `engine.k_core()`

A node's coreness is the largest `k` such that it belongs to a subgraph where every node has at least `k` neighbours
(either direction, self-loops removed). Every node here has at least two neighbours inside its triangle and no
3-core exists, so all six have coreness 2:

```python
[(r["id"], r["coreness"]) for r in engine.k_core(top_k=0)]
# → [('a', 2), ('b', 2), ('c', 2), ('x', 2), ('y', 2), ('z', 2)]
```

Rows are sorted by coreness, largest first, then by ID in IRIS collation, as `triangle_count()`'s are, and follow
`networkx.core_number`. Before 4.6.0 a tie went by Python string order (`10` before `9`).

Triangle count, components and k-core run in the Arno library when it is loaded, and otherwise in this Python
process over `^KG` (no extra package needed).

## Weakly connected components: `kg_WCC()`

Components that ignore direction. Each node is labelled with the smallest node ID of its component, in IRIS collation
(a node with no edge is a component of its own, labelled with itself; before 4.6.0 it had no entry); here one
component, labelled
`a`:

```python
engine.kg_WCC()
# → {'a': 'a', 'b': 'a', 'c': 'a', 'x': 'a', 'y': 'a', 'z': 'a'}
```

Labels spread until they stop changing, which takes at most the graph's diameter plus one rounds. Before 4.6.0 the
spreading stopped after 100 rounds, so on a graph whose diameter is over 100 one component came back as several.

`kg_WCC(max_iterations=n)` is a safety limit: it raises `WCC did not converge in n rounds` when the labels are still
changing after `n` rounds. The check comes before each round, so `n` must cover the round that finds nothing left to
change: on the chain `w1 -> w2 -> w3 -> w4 -> w5 -> w6`, round 5 is the last to change a label, so `max_iterations=5`
raises and `max_iterations=6` answers. With a limit, the ObjectScript implementation answers even when the Arno library
is loaded, so the limit means the same either way.

## Label propagation (CDLP): `kg_CDLP()`

Each round, every node takes the label most common among its neighbours in either direction, each edge one vote
(its own label not counted: a self-loop is no vote; before 4.6.0 it was two votes for the node's own label), all
nodes at once; a tie goes to the label first in IRIS collation. It stops when no label changes, or after
`max_iterations` rounds (default 10). Worked by hand on the example:

| Round | a   | b   | c   | x   | y   | z   |
| ----- | --- | --- | --- | --- | --- | --- |
| start | a   | b   | c   | x   | y   | z   |
| 1     | b   | a   | a   | c   | x   | x   |
| 2     | a   | a   | a   | x   | c   | c   |
| 3     | a   | a   | a   | c   | c   | c   |
| 4     | a   | a   | a   | c   | c   | c   |

```python
engine.kg_CDLP()
# → {'a': 'a', 'b': 'a', 'c': 'a', 'x': 'c', 'y': 'c', 'z': 'c'}
```

The second community is labelled `c` because that label crossed the bridge in round 1.

Because every node moves at once, the labels can swap every round and never settle, and this is common, not a corner
case: no edge in both directions is needed. On the path a -> b -> c, each node's neighbours (either direction) are a
and c for b, b alone for a and c:

| Round | a   | b   | c   |
| ----- | --- | --- | --- |
| start | a   | b   | c   |
| 1     | b   | a   | b   |
| 2     | a   | b   | a   |
| 3     | b   | a   | b   |

In round 1 b's neighbours have the labels a and c, a tie that a wins; from then on the two ends hold one label and
b the other, and they trade places every round. Any part of a graph whose nodes split into two sides with edges only
between them (people and the places or items they link to, an even cycle) can flip between two states this way, and
a 13-node graph of people, films and cities did, every node, from round 3 on. The answer is then whatever round
`max_iterations` ended on, and `kg_CDLP()` gives no sign of it. To tell, compare `kg_CDLP(max_iterations=k)` with
`kg_CDLP(max_iterations=k + 1)`: an answer that settled by round k is the same in both.

In 4.6.0 a tie between a label that is a number and one that is not is broken by collation, which puts numbers first
(`5` before `abc`); before, `abc` won. Ties between labels that are all numbers or all strings are unchanged.

## Global PageRank also changed in 4.6.0

`kg_PAGERANK()` is textbook PageRank now over every node of the graph: a node with no out-edges (or no edge at all)
spreads its rank over all of them, and their scores sum to 1. On the example, `x` ranks first because `c -> x` sends rank into a triangle
that never sends it back:

```python
ranks = engine.kg_PAGERANK()
[node for node, _ in sorted(ranks, key=lambda t: -t[1])]
# → ['x', 'y', 'z', 'c', 'b', 'a']
```

```python
round(sum(score for _, score in ranks), 6)
# → 1.0
```

Scores change from 4.5.x wherever a graph has a node with no out-edges or a parallel edge. PageRank and personalized
PageRank are covered in the [User guide](USER_GUIDE.md).

## Cypher procedures

Leiden, triangle count, strongly connected components and k-core are also Cypher procedures. They run in IRIS
(`Graph.KG.Communities`) over `^NKG`. `ivg.triangleCount` and `ivg.kcore` rank their rows as `triangle_count()` and
`k_core()` do, and `topK` keeps the same rows; before 4.6.0 a tie at the cut went by the order the nodes were written.

`node` comes back as a node: its ID, its labels and its properties, then the yielded columns. `ORDER BY node` sorts by
ID (before 4.6.0 it failed to prepare, SQLCODE -29):

```python
rows = engine.execute_cypher(
    "CALL ivg.kcore({topK: 100}) YIELD node, coreness RETURN node, coreness ORDER BY node"
).rows
[(row[0], row[-1]) for row in rows]
# → [('a', 2), ('b', 2), ('c', 2), ('x', 2), ('y', 2), ('z', 2)]
```

| Procedure           | Options                                                          | Yields                      |
| ------------------- | ---------------------------------------------------------------- | --------------------------- |
| `ivg.leiden`        | `maxLevels`, `gamma`, `tol`, `topK`, `memBudgetMB`, `randomSeed` | `node`, `community`, `size` |
| `ivg.triangleCount` | `topK`                                                           | `node`, `triangles`, `lcc`  |
| `ivg.scc`           | `topK`                                                           | `node`, `component`, `size` |
| `ivg.kcore`         | `topK`                                                           | `node`, `coreness`          |

An option not in the list raises `ValueError`. So does `USE GRAPH` with any of them, `USE GRAPH ''` included:
`^NKG` has no graph dimension, so they cannot answer one graph. A query without `USE GRAPH` answers the union of
each graph's own answer, which they cannot give either once a named graph has an edge, as `^NKG` then holds its edges
merged with the default graph's: they raise `ValueError` there, naming the Python method that answers the default
graph. While the default graph is the only one with edges they answer, `^NKG` then being exactly its edges. The
methods take no graph and answer the default graph. Before 4.6.0 the procedures answered every graph's edges merged,
as if they were the named graph's or the default graph's.

## When a method fails

`leiden_communities`, `triangle_count`, `strongly_connected_components` and `k_core` raise `RuntimeError`, naming the
method and the error, when the path that ran reports one, and `NotImplementedError` when the store cannot run them at
all. An empty list is an empty answer. Before 4.6.0 a failure came back as `[]`.
