<!-- markdownlint-disable MD013 -->

# Architecture

For contributors and curious operators: how IVG is put together, where each piece lives in IRIS, and which code
answers which call. An application developer needs none of it to use IVG; the [User guide](../USER_GUIDE.md) and the
[API reference](../API_REFERENCE.md) cover that. The terms (Graph, Graph Key, IVG Schema, Native executor, Rebuilt
store…) are defined in [`CONTEXT.md`](../../CONTEXT.md).

## The pieces

```text
 Clients     Python application ── IRISGraphEngine          Neo4j drivers, Browser, LangChain, curl
                                        │                                │
 Python      iris_vector_graph          │              cypher_api (HTTP, Bolt WS)  bolt_server (Bolt TCP 7687)
             engine.py facade + _engine/*.py mixins ◀─────────────┘
             cypher/ parser → native plan or SQL translation
             stores/ IRISSQLStore (algorithm routing, the shared cache), LazyKG
                                        │  DB-API (SQL) and Native API (class methods, globals)
 IRIS        IVG Schema (SQL)           │  Graph.KG.* and IVG.* classes        IVGC.P<n> compiled plans
 namespace   tables, functions,         │  (ObjectScript; a few methods       (written at run time)
             procedures                 │   in embedded Python)
             globals: ^KG ^NKG ^IVG.* ^BM25Idx ^VecIdx ^PLAID ^ArnoAccel
                                        │  $ZF callouts, optional
 Optional    Arno library (Rust, not distributed with IVG)
```

- **The Python package** is the API. `IRISGraphEngine` (`iris_vector_graph/engine.py`) is a facade over mixins in
  `iris_vector_graph/_engine/` (nodes and edges, temporal, vectors, algorithms, ledger, FHIR, RDF, schema, admin).
  It talks to IRIS through the connection you give it: SQL through DB-API, and class methods and globals through the
  IRIS Native API on the same connection.
- **The ObjectScript classes** in the `Graph.KG` and `IVG` packages hold everything that walks globals: adjacency
  maintenance, traversal, the native Cypher executor, temporal storage, the ledger, the algorithms' Owners, BM25, IVF,
  PLAID and the FHIR projection. `initialize_schema()` compiles them into the namespace from the class sources the
  wheel carries (`iris_src/src` in a source checkout).
- **Compiled plans** are classes IVG writes itself: the first run of a native read's Plan shape queues a background
  job that compiles `IVGC.P<n>` for it (ADR-0014). `^IVG.CypherGen("mode")` turns this off for a namespace under
  change control.
- **The Arno library** is an optional Rust library that IVG calls through `$ZF` callouts for some traversals and
  algorithms. It is not part of IVG's distribution. IVG uses it for an algorithm only when the loaded build passes
  IVG's reader gate for that algorithm; otherwise the ObjectScript Owner answers ([rust accelerator](rust-accelerator.md)).

## What IVG puts in a namespace

**One namespace holds one IVG install.** Since 4.6.0 every SQL object IVG creates (tables, functions, procedures) is
in one schema, the **IVG Schema**: `Graph_KG` unless `schema_prefix=` (or `IVG_SCHEMA_PREFIX`) names another at the
first `initialize_schema()`, which records the name in `^IVG.Deploy("schema")`. An engine built for another schema is
refused on its first database call with `IVGSchemaError` (ADR-0016). The globals belong to the namespace, not the
schema, which is why one namespace cannot hold two IVG installs side by side.

| Where                                | What                                                                                                                                                                                                                                                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| IVG Schema, tables                   | `nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`, the embedding tables, `docs`, the mapping, crosswalk and FHIR tables ([schema reference](ACTUAL_SCHEMA.md))                                                                                                                                                       |
| IVG Schema, functions and procedures | the Cypher helpers (`CY_*`, `JSON_*`, `LIST_*`, `STR_SPLIT`, `RAND`, `NEWID`, `REGEX_MATCH`), the retrieval procedures (`kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE`), the algorithm functions (`kg_PPR`, `kg_Leiden`, `kg_KCore`, `kg_SCC`, `kg_TriangleCount`, the centralities), `kg_BM25`, `kg_IVF`, `ledger_records` |
| Classes                              | `Graph.KG.*`, `IVG.CypherEngine`, `IVG.Percentile`; `IVGC.P<n>` at run time. A class whose methods project as SQL is compiled so its SQL lands in the IVG Schema                                                                                                                                                   |
| Globals                              | `^KG`, `^NKG`, `^IVG.*`, `^BM25Idx`, `^VecIdx`, `^IVF`, `^PLAID`, `^ArnoAccel`, `^EmbedQueue`; the classes' own storage globals                                                                                                                                                                                    |
| `%SYS`                               | nothing                                                                                                                                                                                                                                                                                                            |

A namespace that 4.5.x set up also holds the functions, procedures, views and `User` classes 4.5.x created in
`SQLUser`; 4.6.0 leaves them working and unused, and `engine.remove_legacy_objects()` lists and removes them
([migrating to 4.6.0](../migration/v4.6.0.md)).

## Embedded Python

Most of IVG's ObjectScript is plain ObjectScript, which matters because the Python package calls class methods
through the Native API from outside IRIS. A few methods are `[ Language = python ]` and run in IRIS's embedded Python:

| Class                       | Embedded-Python methods                                                | Used for                                                                 |
| --------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `Graph.KG.Communities`      | `LeidenJsonPy`, `ClosenessJsonPy`                                      | `leidenalg` and `igraph` tiers, when those packages are in IRIS's Python |
| `Graph.KG.PageRankEmbedded` | `ComputePageRank`, `ComputePageRankWithMetrics`, `ComputePageRankCore` | an older PageRank, called by nothing else in IVG                         |
| `Graph.KG.PyOps`            | `VectorSearch`, `HybridSearch`, `MetaPath` and helpers                 | `Graph.KG.Service`'s vector, hybrid and meta-path requests               |
| `Graph.KG.MCPTools`         | `CypherQuery`, `LoadGraph`, `EvidenceSearch`                           | the MCP tool set (needs IRIS's `%AI` classes)                            |
| `IVG.CypherEngine`          | `QueryJson`, `Scalar`, `InitSchemaJson` and others                     | Cypher from ObjectScript, through the Python package installed in IRIS   |

`initialize_schema()` compiles a class with embedded-Python methods only where IRIS has embedded Python, and a class
that names `%AI.*` only where those classes exist; anything it skips is listed with the reason in the status it
returns. The Python tiers are optional: `LeidenJsonAuto` calls `LeidenJsonPy` and falls back to ObjectScript when it
answers that the packages are missing. The embedded-Python methods need their packages (and, for `IVG.CypherEngine`,
`iris-vector-graph` itself) installed in IRIS's own Python, `/usr/irissys/mgr/python`, not in the client's
environment. [Embedded Python in IVG](embedded_python_architecture.md) has the rules for code that crosses the two.

## Globals

### `^KG`: adjacency and the temporal index

Every subtree is keyed by the **Graph Key** right after its name: the integer `0` for the default graph, the graph's
name otherwise (`Graph.KG.GraphKey.ForIndex`; ADR-0001, ADR-0003).

```text
^KG("out", g, s, p, o) = weight            structural out-edges
^KG("in",  g, o, p, s) = weight            structural in-edges
^KG("label", g, label, s) = ""             nodes by label
^KG("gen", g, s), ^KG("degc", g, ...)      the degree cache: a per-source generation and the counts it validates (ADR-0012)
^KG("deg2p", g, s, p), ^KG("deg2p_exact", g, s, p)   two-hop statistics (a Rebuilt store)
^KG("tout", g, ts, s, p, o) = weight       temporal edges by time, outward
^KG("tin",  g, ts, o, p, s) = weight       temporal edges by time, inward
^KG("tlast", g, ...)                       each source's newest temporal event
^KG("bucket", g, bucket, s)                temporal edge counts per 5-minute bucket
^KG("tagg", g, bucket, s, p, key)          per-bucket COUNT, SUM, MIN, MAX and HLL sketches
^KG("edgeprop", g, ts, s, p, o, key)       temporal edge attributes
^KG("labelset", hash) = canonical          temporal label sets, append-only
```

`^KG("out")`, `("in")` and `("label")` are the **Adjacency Index**: since 4.3.0 a functional index on `rdf_edges` and
`rdf_labels` writes them in the same transaction as the row (spec 252, ADR-0008), so no write path can leave them
behind. `^KG("deg")`, `("degp")` and `("prop")` are retired: degree is counted, never stored (ADR-0010), and node
properties are read from `rdf_props`. The temporal stores are written only through `Graph.KG.TemporalIndex`
(ADR-0002).

### `^NKG`: the integer-keyed copy

`^NKG` holds the edges of every graph together, with node and label IDs interned to integers, for the Arno library
and the ObjectScript algorithms that want integer subscripts. It has **no graph dimension**: an edge of a named graph
and one of the default graph are indistinguishable there. It is a Rebuilt store:
`sync()` (or `Graph.KG.Traversal.BuildNKG`) rebuilds it, and it records the Graph-Version Token it was built from.

```text
^NKG("$NI", nodeId) = idx     ^NKG("$ND", idx) = nodeId      node dictionary
^NKG("$LI", label) = idx      ^NKG("$LS", idx) = label       predicate and label dictionary
^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight                     out-edges
^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight                     in-edges
^NKG(-3, ...)                                                degrees
^NKG("$meta", "nodeCount" | "edgeCount" | "version" | ...)   build metadata
```

Never `Kill ^NKG`: `Graph.KG.GraphIndex.DropNKG` removes it with its dictionary and metadata in step.

### `^IVG.*`: settings, records and caches

| Global                                          | Holds                                                                     |
| ----------------------------------------------- | ------------------------------------------------------------------------- |
| `^IVG.Deploy("schema")`, `("fingerprint")`      | the namespace's IVG Schema; the fingerprint of the class sources deployed |
| `^IVG.Ledger`                                   | the revision ledger (records, statements, head, idempotency keys)         |
| `^IVG.GraphMode(g)`                             | `"multi"` for a graph that allows parallel edges                          |
| `^IVG.TemporalStructural(g, predicate)`         | `0` for a temporal-only predicate (ADR-0015)                              |
| `^IVG.AlgoCache(g, algorithm, paramHash)`       | cached algorithm answers, each with the token it was computed at          |
| `^IVG.BuiltFrom(store)`                         | the token each Rebuilt store was built from                               |
| `^IVG.CypherGen("mode")`, `^IVG.CypherCompiled` | the compiled-plan mode, and the plan shapes compiled so far               |
| `^IVG.FHIRGraph(graph)`                         | the lock one FHIR sync or rebuild holds                                   |
| `^IVG.AdjW(graph)`, `^IVG.NKGBuild`             | locks: adjacency writers, one `^NKG` build at a time                      |
| `^IVG.warnings`                                 | warnings the ObjectScript algorithms record                               |

`^ArnoAccel("lib_path")` names the Arno library for the namespace; each process loads it on first use and keeps the
handle in its own `^||ArnoAccel` (4.6.0: a handle is valid only in the process that loaded it).

### Search indexes

```text
^BM25Idx(name, "cfg" | "idf" | "df" | "tf" | "len" | "doc" | "lock", ...)   BM25 (Index Schema 2: "df" and "doc")
^VecIdx(name, "cfg" | "vec" | "tree" | "meta", ...)                         VecIndex, random-projection trees
^PLAID(name, "centroid" | "docPacked" | "docCentroid" | "docNTok" | "meta", ...)   PLAID multi-vector
```

`^BM25Idx(name, "tf", term, docId)` is term-first, so a query walks only the posting lists of its terms. IVF indexes
(`Graph.KG.IVFIndex`) are in `^IVF`.

## How a Cypher query runs

1. `iris_vector_graph/cypher/` parses the query (a hand-written recursive-descent parser) into an AST.
2. A read-only query the **Native executor** supports becomes a native plan and runs inside IRIS:
   `Graph.KG.Cypher.Exec` walks `^KG` (and reads `rdf_props` and `rdf_labels`' storage directly), or the compiled
   `IVGC.P<n>` for its shape. On by default since 4.3.0.
3. Anything else, every write included, takes the **SQL path**: the translator writes one SQL statement (CTE stages
   for `WITH`, joins over `rdf_edges`, `rdf_labels`, `rdf_props` and `nodes`) and runs it on the connection. Some
   shapes the translator routes to an ObjectScript fast path instead (k-hop counts and IDs, shortest paths, BFS).
4. `ivg.*` procedure calls become stages over the IVG Schema's functions: `ivg.vector.search` (`kg_KNN_VEC`),
   `ivg.ppr` (`kg_PPR`), `ivg.bm25.search` (`kg_BM25`), `ivg.ivf.search` (`kg_IVF`), the centralities and
   communities (`kg_DegreeCentrality`, `kg_Betweenness`, `kg_Closeness`, `kg_Eigenvector`, `kg_Leiden`,
   `kg_TriangleCount`, `kg_SCC`, `kg_KCore`), plus `ivg.neighbors`, `ivg.retrieve` and `ivg.shortestPath.weighted`.

In **shadow mode** both paths answer a supported read and a difference raises. The translator's SQL can be read
without running it with `POST /admin/explain` ([HTTP server](../ADMIN_API.md)).

## Algorithms

An algorithm call goes through the store (`IRISSQLStore`), which answers from the shared cache (`^IVG.AlgoCache`) while
the graph's token is unchanged, from a proven Arno reader when one is loaded and passes the gate, and from the
ObjectScript **Owner** otherwise (`Graph.KG.PageRank`, `Graph.KG.Algorithms` for WCC and CDLP, `Graph.KG.Centrality`,
`Graph.KG.Communities`, `Graph.KG.Subgraph`). Some algorithms fall back to Python over `^KG` (`stores/lazy_kg.py`).
Which path answered is in the log. [Communities](../COMMUNITIES.md) gives the order for each community algorithm.

## The ObjectScript classes, by job

| Job                   | Classes                                                                                                                                                                           |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Adjacency             | `Adjacency`, `Adj` (degree cache), `AdjIndex` and `LabelIndex` (the functional indexes), `EdgeScan`, `GraphIndex` (`^NKG`), `GraphKey`, `GraphMode`, `GraphStores`, `GraphVerify` |
| Traversal             | `Traversal`, `TraversalBFS`, `TraversalBuild` (`BuildKG`, `BuildNKG`), `TraversalKHop`, `TraversalPaths`, `VarLength`, `Subgraph`                                                 |
| Cypher                | `Cypher.Exec` (native executor), `Cypher.Gen` (compiled plans), `Cypher.Props`, `Cypher.SortKey`, `CypherFn`, `JsonKeys`, `IVG.Percentile`                                        |
| Temporal              | `TemporalIndex`, `TemporalIndexMS`, `TemporalPolicy`, `TemporalWindow` (the paged window reader)                                                                                  |
| Ledger and erasure    | `Ledger`, `LedgerApply`, `LedgerGenesis`, `LedgerRedact`, `LedgerRevision` and `LedgerStats` (tables in the IVG Schema), `Eraser`                                                 |
| Algorithms            | `PageRank`, `PageRankEmbedded`, `Algorithms`, `Centrality`, `Communities`, `AlgoCache`, `ArnoAccel`, `NKGAccel*`                                                                  |
| Search                | `BM25Index`, `VecIndex`, `IVFIndex`, `PLAIDSearch`, `PyOps`                                                                                                                       |
| FHIR                  | `FHIRGraph`, `FHIRGraphSyncTask` (a Task Manager task per registered graph)                                                                                                       |
| Services and the rest | `Service`, `MCPService`, `MCPTools`, `MCPToolSet`, `EmbedQueue`, `Snapshot`, `Meta`, `SQL`, `IVG.CypherEngine`                                                                    |

All in `Graph.KG` unless named with `IVG.`. The class header comments are the reference for each.

## Further reading

- [Schema reference](ACTUAL_SCHEMA.md): every table, its columns and constraints.
- [Embedded Python in IVG](embedded_python_architecture.md) and [the Arno library](rust-accelerator.md).
- The decision records in [`docs/adr/`](../adr/), and [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for working on the code.
