<!-- markdownlint-disable MD013 -->

# Communities and components

For application developers who want to group a graph's nodes: Leiden communities, triangle counts with the local
clustering coefficient, strongly and weakly connected components, k-core decomposition and label propagation (CDLP).
Signatures are in the [API reference](API_REFERENCE.md); timings are in [Benchmarks](BENCHMARKS.md#graph-algorithms).

The Python methods read the default graph's adjacency (`^KG`). The Cypher procedures and IRIS's own Leiden read `^NKG`,
which holds every graph's edges together (it has no graph dimension) and is rebuilt by `engine.sync()`: call `sync()`
after writing and before running them, and expect them to see every graph of the namespace.

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
`gamma=1.0` it optimizes modularity; any other `gamma` uses the Constant Potts Model, where a lower `gamma` gives
fewer, larger communities. `random_seed` makes a run repeatable.

```python
communities = engine.leiden_communities(random_seed=42, top_k=0)
groups(communities, "community")
# → [['a', 'b', 'c'], ['x', 'y', 'z']]
```

Each row is `{"id", "community", "size"}`; community 0 is the largest, and `top_k=0` returns every node (the default
is the first 10,000 rows). The first path that answers is used:

1. `Graph.KG.Communities.LeidenJsonAuto` in IRIS: `leidenalg` in IRIS's embedded Python when `igraph` and `leidenalg`
   are installed there (`/usr/irissys/mgr/python`), otherwise a greedy modularity partition in ObjectScript over
   `^NKG`.
2. The Arno library's Leiden, when Arno is loaded.
3. In this Python process, over `^KG`: `leidenalg` from the `[communities]` extra, else networkx's Louvain, a different
   and weaker algorithm.

`IVG_DISABLE_ARNO=1` skips step 2. On a graph of more than 100,000 nodes, `top_k=0` warns that the answer may be a
large JSON string.

## Triangles: `engine.triangle_count()`

For each node, the triangles through it over its neighbours in either direction (self-loops and parallel edges count
once), and the local clustering coefficient: triangles divided by the pairs of neighbours, `k(k-1)/2`. Here `c` has
three neighbours (`a`, `b`, `x`) and one triangle, so its coefficient is 1/3; `a`, `b`, `y` and `z` have two
neighbours and one triangle:

```python
[(r["id"], r["triangles"], round(r["lcc"], 3)) for r in engine.triangle_count(top_k=0)]
# → [('a', 1, 1.0), ('b', 1, 1.0), ('c', 1, 0.333), ('x', 1, 0.333), ('y', 1, 1.0), ('z', 1, 1.0)]
```

Rows are sorted by triangle count, largest first, then by ID. The count follows `networkx.triangles` on the
undirected graph.

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

Rows are sorted by coreness, largest first, then by ID, and follow `networkx.core_number`.

Triangle count, components and k-core run in the Arno library when it is loaded, and otherwise in this Python
process over `^KG` (no extra package needed).

## Weakly connected components: `kg_WCC()`

Components that ignore direction. Each node is labelled with the smallest node ID of its component, in IRIS collation;
here one component, labelled `a`:

```python
engine.kg_WCC()
# → {'a': 'a', 'b': 'a', 'c': 'a', 'x': 'a', 'y': 'a', 'z': 'a'}
```

Labels spread until they stop changing, which takes at most the graph's diameter plus one rounds. Before 4.6.0 the
spreading stopped after 100 rounds, so on a graph whose diameter is over 100 one component came back as several.

`kg_WCC(max_iterations=n)` is a safety limit: it raises `WCC did not converge in n rounds` when the labels are still
changing after `n` rounds. The check comes before each round, so `n` must cover the round that finds nothing left to
change: on the chain `w1 -> w2 -> w3 -> w4 -> w5 -> w6`, round 5 is the last to change a label, so `max_iterations=5`
raises and `max_iterations=6` answers. With a limit the ObjectScript implementation answers even when the Arno library
is loaded, so the limit means the same either way.

## Label propagation (CDLP): `kg_CDLP()`

Each round, every node takes the label most common among its neighbours in either direction (its own label not
counted), all nodes at once; a tie goes to the label first in IRIS collation. It stops when no label changes, or after
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

The second community is labelled `c` because that label crossed the bridge in round 1. Because every node moves at
once, a pair of nodes can swap labels every round and never settle (two nodes joined in both directions, say); the
answer is then whatever round `max_iterations` ended on.

In 4.6.0 a tie between a label that is a number and one that is not is broken by collation, which puts numbers first
(`5` before `abc`); before, `abc` won. Ties between labels that are all numbers or all strings are unchanged.

## Global PageRank also changed in 4.6.0

`kg_PAGERANK()` is textbook PageRank now: every source and target is a node, a node with no out-edges spreads its rank
over all of them, and the scores sum to 1. On the example, `x` ranks first because `c -> x` sends rank into a triangle
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
(`Graph.KG.Communities`) over `^NKG`:

`node` comes back as a node: its ID, its labels and its properties, then the yielded columns. In 4.6.0 an
`ORDER BY node` after these procedures fails to prepare (SQLCODE -29), so sort in Python:

```python
rows = engine.execute_cypher("CALL ivg.kcore({topK: 100}) YIELD node, coreness RETURN node, coreness").rows
sorted((row[0], row[-1]) for row in rows)
# → [('a', 2), ('b', 2), ('c', 2), ('x', 2), ('y', 2), ('z', 2)]
```

| Procedure           | Options                                                          | Yields                      |
| ------------------- | ---------------------------------------------------------------- | --------------------------- |
| `ivg.leiden`        | `maxLevels`, `gamma`, `tol`, `topK`, `memBudgetMB`, `randomSeed` | `node`, `community`, `size` |
| `ivg.triangleCount` | `topK`                                                           | `node`, `triangles`, `lcc`  |
| `ivg.scc`           | `topK`                                                           | `node`, `component`, `size` |
| `ivg.kcore`         | `topK`                                                           | `node`, `coreness`          |

An option not in the list raises `ValueError`.

## When a method answers nothing

`leiden_communities`, `triangle_count`, `strongly_connected_components` and `k_core` return `[]` when the path that ran
reports an error, and raise `NotImplementedError` when the store cannot run them at all. An empty list on a graph that
has edges therefore means a failure, not an empty answer: check the log (logger `iris_vector_graph`) and that
`sync()` has run.
