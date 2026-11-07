<!-- markdownlint-disable MD013 -->

# iris-vector-graph

A property graph inside InterSystems IRIS, used from Python. IVG (iris-vector-graph) stores nodes, relationships, labels
and properties in IRIS tables, answers openCypher queries over them, and adds vector and text search, graph algorithms,
time-stamped edges and a revision history. The graph lives in one IRIS namespace, next to your other IRIS data, and is
reached through the ordinary IRIS Python driver.

[![PyPI](https://img.shields.io/pypi/v/iris-vector-graph)](https://pypi.org/project/iris-vector-graph/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![IRIS 2025.1+](https://img.shields.io/badge/IRIS-2025.1+-purple.svg)](https://www.intersystems.com/products/intersystems-iris/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Getting Started

### 1. Start IRIS

Any IRIS 2025.1 or later works. To try IVG on a laptop, start IRIS Community Edition (no license needed) with the
repository's compose file:

```bash
git clone https://github.com/intersystems-community/iris-vector-graph && cd iris-vector-graph
docker compose up -d
docker compose ps        # wait until the iris service shows (healthy), about a minute
```

IRIS listens on `localhost:1972` (the SuperServer, which Python connects to) and serves the Management Portal on port 52773. The user is `_SYSTEM`, password `SYS`. If another IRIS on your machine already holds either port, choose other
host ports, for example `IVG_IRIS_PORT=11972 IVG_WEB_PORT=15773 docker compose up -d`, and pass the SuperServer one to
`iris.connect` in step 3.

Compose names the container `<project>-iris-1`. `docker compose ps` shows its name, ports and health, and
`docker compose exec iris iris session IRIS -U USER` opens an IRIS terminal in it.

### 2. Install

```bash
pip install iris-vector-graph
```

That is all the quickstart needs. Some features need an extra: `[rdf]` for RDF import and export and SHACL,
`[communities]` for Leiden in this Python process when IRIS's own route cannot answer
([Communities](docs/COMMUNITIES.md)), `[plaid]` for the PLAID index, `[cli]` for the `ivg` command, and `[full]` for
the HTTP server and everything else (`pip install 'iris-vector-graph[full,cli]'`). OBO import needs `obonet`
(`pip install obonet`). A call that needs a missing package raises `ImportError` naming it.

### 3. Run your first query

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
engine.create_node("bob", labels=["Person"], properties={"name": "Bob"})
engine.create_edge("alice", "KNOWS", "bob")

result = engine.execute_cypher(
    "MATCH (a {node_id: $id})-[:KNOWS]->(b) RETURN b.name AS name",
    {"id": "alice"},
)
print(result.rows)  # [['Bob']]

# Leave USER as you found it: the Quickstart counts a namespace with no other graph in it
engine.delete_node("alice")
engine.delete_node("bob")
```

It prints:

<!-- doc-test: output -->

```text
[['Bob']]
```

`initialize_schema()` sets up IVG in the namespace the connection is in (here `USER`) and prints nothing; it logs one
summary line at INFO. With logging configured, `import iris` itself logs two warnings from the driver's
embedded-Python wrapper (`IRISINSTALLDIR or ISC_PACKAGE_INSTALLDIR environment variable is not set`,
`Embedded Python not configured`): they are about running inside IRIS, and a connection like this one does not use
it. Run it once per namespace, and again after you upgrade the package. `embedding_dimension` is the
width of the vectors you will store; 768 suits many sentence-embedding models.

---

## Where IVG lives in a namespace

IVG has no separate ObjectScript installation. `engine.initialize_schema()` sends IVG's ObjectScript classes from the
Python package over your connection and compiles them into the namespace that connection is in. Its SQL tables,
functions and procedures go into one SQL schema in that namespace, the **IVG Schema**: `Graph_KG` unless you choose
another name (`IRISGraphEngine(conn, schema_prefix="RAG")`, or `IVG_SCHEMA_PREFIX`; see
[One IVG Schema per namespace](docs/ADMIN_GUIDE.md#one-ivg-schema-per-namespace)). The graph's globals (`^KG`,
`^NKG`, `^IVG.*`) belong to that namespace too.

- Nothing is installed in `%SYS`, and nothing is shared between namespaces: each namespace you initialize has its own
  copy of the classes, its own tables and its own graph.
- One namespace holds one IVG Schema. To keep two graphs completely apart, use two namespaces.
- To check that a namespace has IVG, ask in that namespace. In an IRIS terminal opened in `USER` after step 3:

```objectscript
USER>write ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.NKGAccel")
1
USER>write ##class(Graph.KG.NKGAccel).IsLoaded()
0
```

The same check in `%SYS`, or in a namespace you have not initialized, prints `0`, which is expected.

`Graph.KG.NKGAccel` is ordinary ObjectScript and is always deployed. The optional part is the Arno library
(`libarno_callout.so`), a native library that speeds up some graph algorithms. It is not distributed with IVG, and
everything works without it: the algorithms then run in ObjectScript. When it is present, each IRIS process loads it on
first use, so `IsLoaded()` answers for the process that asks, and `0` in a new terminal is normal.
`engine.status().report()` shows which one answers your engine's calls (`Acceleration: ObjectScript (Arno not loaded)`
or `Acceleration: Arno <version>`).

If IVG 4.5 or an earlier release set up the namespace, read [Migrating to 4.6.0](docs/migration/v4.6.0.md) before
you upgrade. What setup creates, the privileges it needs, and how to back it up are in the [Admin Guide](docs/ADMIN_GUIDE.md).

---

## What It Does

| Feature                | What you get                                                                                                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **openCypher**         | `MATCH`, `CREATE`, `MERGE`, `SET`, `DELETE`, `WITH`, `UNWIND`, variable-length paths, `shortestPath`, subqueries; openCypher TCK 3896/3896 on IRIS 2026.3 and Community 2026.1, driver 5.4.0 and 5.4.1 ([how](docs/dev/TCK.md)) |
| **Native Cypher**      | Supported reads run inside IRIS over the `^KG` adjacency global, on by default; every other query is translated to IRIS SQL                                                                                                     |
| **Named graphs**       | Several graphs in one namespace, kept apart by a graph ID ([User Guide](docs/USER_GUIDE.md#4-named-graphs))                                                                                                                     |
| **Graph algorithms**   | PageRank and personalized PageRank, WCC, CDLP, Leiden, SCC, k-core, triangles, betweenness, closeness, eigenvector and degree centrality                                                                                        |
| **Shortest path**      | Unweighted `shortestPath` in Cypher; weighted Dijkstra (`CALL ivg.shortestPath.weighted`)                                                                                                                                       |
| **Vector search**      | Cosine search over IRIS `VECTOR` columns, one table per graph and embedding model; IVF and PLAID indexes                                                                                                                        |
| **Text search**        | BM25 indexes over node properties                                                                                                                                                                                               |
| **Temporal edges**     | Time-stamped edges with window queries and pre-aggregated 5-minute buckets                                                                                                                                                      |
| **Revision ledger**    | Opt-in, atomic changesets with history, diff, reconstruction and verification                                                                                                                                                   |
| **Bulk loading**       | Server-side batch loaders, 6–14K edges/s; NDJSON, RDF, OBO and networkx import ([formats](docs/setup/DATA_FORMATS.md))                                                                                                          |
| **RDF, SHACL, PROV-O** | Export to Turtle, N-Triples, N-Quads and JSON-LD; SHACL validation; temporal edges as PROV-O ([guide](docs/SEMANTIC_LAYER.md))                                                                                                  |
| **FHIR graph**         | An InterSystems FHIR repository read as a named graph ([guide](docs/FHIR_GRAPH.md))                                                                                                                                             |
| **Bolt protocol**      | A neo4j-driver compatible server (TCP and WebSocket)                                                                                                                                                                            |

---

## Performance

On the LDBC Social Network Benchmark at scale factor 10 (AWS `r7g.8xlarge`, 2026-10-23, native Cypher with compiled
plans), IVG's median time over 39 queries was 0.90x Neo4j's and 0.57x Memgraph's
([report](docs/dev/notes/ALT_VS_BASELINE.md#sf10-after-spec-257-aws-2026-10-23)).

Single-query latencies on an M3 Ultra, Community IRIS 2026.1, ARM64 Docker:

| Query                                                                             | Latency   | Notes                                   |
| --------------------------------------------------------------------------------- | --------- | --------------------------------------- |
| 1-hop neighbor lookup                                                             | ~0.4ms    | `$Order` on `^KG`                       |
| NKG fast-path: `[*1..N]` via `^NKG` BFS, hops 2–5 (2026-06, before native Cypher) | 1.4–2.0ms | 4.9–13.4x faster than the SQL path then |
| IC13 shortest path (LDBC SF10)                                                    | 2.1–3.2ms |                                         |
| BM25 full-text (174 nodes, 3-term)                                                | 0.3ms     | Posting-list `$Order`                   |
| Temporal window query                                                             | 0.1ms     | O(results), B-tree                      |

Methods and more numbers: [Benchmarks](docs/BENCHMARKS.md).

---

## Documentation

**If you write Python applications on IVG**, read in this order:

1. [Quickstart](docs/setup/QUICKSTART.md): start IRIS, load a small graph, query it, run an algorithm.
2. [User Guide](docs/USER_GUIDE.md): each task in turn, from connecting to exporting, with examples.
3. [API Reference](docs/API_REFERENCE.md): every public method's signature and summary.

Also: [Data formats](docs/setup/DATA_FORMATS.md), [Known issues](docs/KNOWN_ISSUES.md),
[Semantic layer](docs/SEMANTIC_LAYER.md), [FHIR graph](docs/FHIR_GRAPH.md), [Communities](docs/COMMUNITIES.md).

**If you run the IRIS instance IVG lives in**, read the [Admin Guide](docs/ADMIN_GUIDE.md): what IVG installs and
where, the privileges it needs, optional components, backup, upgrade and troubleshooting. Upgrading from 4.5:
[Migrating to 4.6.0](docs/migration/v4.6.0.md).

Changes in each release: [Changelog](CHANGELOG.md). Working on IVG itself: [CONTRIBUTING.md](CONTRIBUTING.md).
For coding agents, [`skills/iris-vector-graph/SKILL.md`](skills/iris-vector-graph/SKILL.md) summarises the API.

---

## License

MIT. See [LICENSE](LICENSE).
