<!-- markdownlint-disable MD013 MD060 -->

# Audit: who reads `^KG("deg")` and `^KG("degp")`

Step 4 of the plan in [RISKS.md](RISKS.md): before the adjacency becomes engine-maintained, find every reader of
the degree counters and decide whether it needs them exact. Counters kept with `$Increment` inside a
transaction drift when the transaction rolls back (RISKS R1), and a lock-based exact counter deadlocks (R2),
so the question is what can stop reading them.

Scope: every `.cls` under `iris_src/src` and every `.py` under `iris_vector_graph`, comments excluded. 20 test
files also mention the stores (listed at the end). Numbers below are from 2026-10-12.

## What a drifted counter does to a reader

There are two directions, and they hurt differently:

- **Too high** (a rolled-back insert left its `+1`): a key can exist for a node that has no edges. A reader that
  enumerates `deg` gets a node with no outgoing edges; a reader that divides by `deg` under-weights.
- **Too low** (a rolled-back delete left its `-1`): the counter can reach zero while edges remain, and
  `DeleteIndex` kills the key at zero. A reader that enumerates `deg`, or tests `$Data(degp)`, then **misses a
  node or a predicate that has edges**. This is the dangerous one: results go missing silently.

## The readers, by what they need

### E. Existence or enumeration: "which nodes have an outgoing edge" (10 sites)

| Site                       | Use                                                                                                                                                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Algorithms.cls` 12, 92    | iterate source nodes                                                                                                                                                                                             |
| `ArnoAccel.cls` 176        | iterate source nodes for the snapshot                                                                                                                                                                            |
| `Centrality.cls` 49        | iterate source nodes                                                                                                                                                                                             |
| `NKGAccelAdjacency.cls` 79 | the `^KG` fallback of the adjacency export; reads the legacy layout with no graph subscript and says so in its header: dead in production (`UseNKG()` is true after any `sync()`). Delete or fix, do not migrate |
| `PageRank.cls` 282         | iterate source nodes                                                                                                                                                                                             |
| `Subgraph.cls` 41          | `$Data(deg)` to decide a node exists                                                                                                                                                                             |
| `lazy_kg.py` 116, 121      | `nextSubscript` over `deg` to list nodes                                                                                                                                                                         |
| `iris_sql_store.py` 463    | `$Data(^KG("deg"))`: is the index populated at all                                                                                                                                                               |

Needs: the set of sources with at least one `out` entry. Nothing about the count.

Replacement: iterate the first level under `^KG("out", graph)` instead. It is the same set when the counters are
right, exact when they are not, and costs the same (one `$Order` per source). The populated check becomes
`$Data(^KG("out"))`.

### P. Predicate pruning in the BFS (2 sites)

`TraversalBFS.cls` 434 and 474: `'$Data(^KG("degp", g, o, nextP))` skips a node that has no edge of the next
predicate. With a too-low counter this prunes a real path.

Replacement: `'$Data(^KG("out", g, o, nextP))`. Exact, and measured the same cost as the `degp` probe
(0.069 µs against 0.063 µs per call, 100,000 calls each).

### V. The value matters (14 sites)

| Site                      | Use                                                                                      | Exactness needed                            |
| ------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------- |
| `PageRank.cls` 111, 304   | the divisor of each node's outgoing share                                                | yes: wrong rank                             |
| `Subgraph.cls` 198        | the same divisor in the subgraph PageRank                                                | yes                                         |
| `Centrality.cls` 81, 97   | degree centrality, direction `out` and `both`                                            | yes                                         |
| `Centrality.cls` 113, 123 | the same with a predicate (`degp`)                                                       | yes                                         |
| `TraversalKHop.cls` 8, 11 | `KHopCount`: the answer _is_ the counter, with a counting fallback already in the method | yes                                         |
| `TraversalBuild.cls` 270  | `^NKG(-3, s)` for the Arno readers: the merged degree over all graphs                    | yes                                         |
| `TraversalBuild.cls` 346  | `deg2p` (two-hop count) built from `degp` of the middle nodes                            | yes; every later `deg2p` answer inherits it |
| `lazy_kg.py` 177-186      | `degree()` and `degree_by_predicate()`, public Python API                                | yes                                         |

Needs: the true number of outgoing edges, per node or per node and predicate.

Replacement: count the `out` entries where the number is used.

- `PageRank` (both variants) and `Subgraph`: build a local `deg(node)` once before the iteration loop. That is
  O(E) once, against O(iterations x E) for the loop itself. `PageRank.cls` already counts kept out-edges by hand
  on its exclusion path (`hasExcl`), so the pattern exists.
- `Centrality`: count at the point of use; the `in` direction already does.
- `KHopCount`: delete the stored-counter shortcut and keep the counting branch that is already there.
- `TraversalBuild`: `^NKG(-3)` and `deg2p` are computed during the rebuild, where the loop over `out` already
  visits every edge, so the count is free.
- `lazy_kg.degree()`: count entries. For a hub it is a walk (below); document it.

Cost, measured on the scratch namespace:

| Operation                             | Time     |
| ------------------------------------- | -------- |
| `$Get` of a stored `deg`              | 0.063 µs |
| count a 200-entry node by `$Order`    | 27 µs    |
| count a 100,000-entry hub by `$Order` | 12.9 ms  |

So a counted degree is about 400 times slower per call than a stored one for an ordinary node, and a hub costs
13 ms. That is irrelevant for the loop readers (PageRank, centrality, the build: they pay O(E) once) and only
matters for a caller that asks `degree()` of one hub in a tight loop.

### V2. Semantics to keep

`deg` today counts edges, not distinct targets, and in multigraph mode the keyed writes increment it per
parallel edge while the `out` leaf holds a multiplicity (`"#"`). A count of `out` entries is the number of
distinct triples. Readers that must see parallel edges (PageRank on a multigraph) have to read the leaf
multiplicity, not just count subscripts. Needs a decision before the replacement, and a multigraph test.

### C. Checking and bookkeeping (6 sites, no change of behaviour)

| Site                           | What it is                                                | Under the index                                 |
| ------------------------------ | --------------------------------------------------------- | ----------------------------------------------- |
| `GraphVerify.cls` 131-153, 188 | compares the stored `deg` with the rows and reports drift | becomes the reconciler for the advisory counter |
| `GraphStores.cls` 67           | lists `^KG("deg")` among the stores                       | relabel as advisory or drop                     |
| `snapshot.py` 98-99            | exports and restores `deg`/`degp` as globals              | stop exporting; rebuild after restore           |
| `TraversalBuild.cls` 48        | `Kill` of the derived stores before a rebuild             | unchanged                                       |

## Result

| Class of reader       | Sites | Can stop reading the counter?                               |
| --------------------- | ----- | ----------------------------------------------------------- |
| E existence / list    | 10    | yes, iterate `out`                                          |
| P predicate pruning   | 2     | yes, `$Data(out ...)`                                       |
| V value               | 14    | yes, count where used; one hub-walk cost for the Python API |
| C check / bookkeeping | 6     | no change in behaviour                                      |

Nothing in the repository needs `deg` or `degp` to be stored. Every reader can be served from the `out` entries
the index keeps exact. So the counters can be dropped from the index entirely (RISKS option 2), or kept only as an
advisory number for `GraphVerify` and the Python API. The recommendation is to drop them from the write path: the
two directions of drift (above) produce silent wrong answers in E and P, and V already pays an O(E) walk
elsewhere.

Work: about 25 edit sites in 11 ObjectScript classes and one Python module, one helper pair
(`Adj.Sources(g)` and `Adj.Degree(g, s[, p])`) to put the replacement in one place, plus the 20 test files.

## Tests that mention the stores

`test_adjacency_counter_ownership.py` (13), `test_lazy_kg_coverage.py` (11), `test_delete_adjacency.py` (7),
`test_upgrade_migrate_globals_e2e.py` (5), `test_lazykg_graph_scope.py` (4), `test_lazy_kg.py` (4),
`test_old_release_fixtures.py` (4), `test_236_token_write_paths.py` (4), `test_edge_class_compiles.py` (4),
`test_no_functional_index_declaration.py` (3), `test_ledger_adjacency_graph_key.py` (3),
`test_234_multigraph_storage_e2e.py` (3), and eight others with one or two.

Two of them are guards that this design reverses and must be rewritten on purpose, not worked around:

- `test_no_functional_index_declaration.py` forbids any class from declaring a functional index against
  `Graph.KG.GraphIndex`. It records why the first attempt was removed: the callbacks wrote the default graph's key
  for every graph and `deg` at the wrong depth (both ADR-0001), `PurgeIndex` killed the whole `^KG` root, and the
  declaration made the table class uncompilable under `%SYSTEM.OBJ.LoadDir`, which compiles in name order and
  reaches the table before the index class (ERROR #5289). The new design needs a new index class (not
  `GraphIndex`), a root of its own, the graph key mapped, and the deploy order that `class_deploy` already gives
  functional-index types (it compiles them first).
- `test_adjacency_counter_ownership.py` pins who may write the counters: `EdgeScan` owns `^KG` adjacency including the counters, `GraphIndex` owns `^NKG` (the outcome of an earlier design review, "grilling Q13"). The new rule (the index is the only writer of `out`/`in`; the counters are advisory or gone) replaces it, and that decision should be taken on purpose.
