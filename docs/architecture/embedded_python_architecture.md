<!-- markdownlint-disable MD013 -->

# Embedded Python in IVG

For contributors and curious operators: where IVG's code runs in IRIS's embedded Python rather than in your Python
process, what that needs, and the rules the code follows because of it. Which classes have embedded-Python methods is
listed in [Architecture](ARCHITECTURE.md#embedded-python).

## Two Pythons

- **Your process.** An application imports `iris_vector_graph`, connects with `iris.connect(...)` (the
  `intersystems-irispython` driver) and runs SQL and Native API calls over the connection. Everything IVG does by
  default runs this way, from outside IRIS.
- **IRIS's embedded Python**, inside the IRIS process. It runs `[ Language = python ]` class methods, and Python code
  started by `irispython`. Its packages are installed in IRIS's own directory, `/usr/irissys/mgr/python` on the
  official images (`irispython -m pip install --target /usr/irissys/mgr/python <package>`), not in your environment.

A package installed in one is not visible in the other. IVG's optional embedded-Python tiers (`leidenalg` and
`igraph` for Leiden and closeness) are used only when those packages are in IRIS's Python; installing them in your
environment (the `[communities]` extra) helps the Python-side fallbacks instead.

## The rules IVG's code follows

1. **What the package calls from outside IRIS is ObjectScript.** The Python package calls class methods through the
   Native API (`classMethodValue`), so every method it calls directly is plain ObjectScript and works on any IRIS
   with the classes deployed. An embedded-Python method is reached through an ObjectScript method that falls back
   when Python, or the package it needs, is missing: `Graph.KG.Communities.LeidenJsonAuto` calls `LeidenJsonPy`,
   which answers `PYUNAVAIL` without `leidenalg`, and then runs the ObjectScript Leiden.
2. **The deploy does not assume embedded Python.** `initialize_schema()` compiles a class with
   `[ Language = python ]` methods only when the server has embedded Python, and a class naming `%AI.*` classes only
   when they exist. A skipped class is listed, with its reason, in the status `initialize_schema()` returns. Nothing
   else depends on a skipped class.
3. **Vectors are searched in SQL.** IRIS's vector functions and HNSW indexes are SQL features: a search reads the
   embedding table with `VECTOR_COSINE` (and the index, where one exists), never the table's storage global. Code in
   embedded Python that wants a vector search runs the SQL (`iris.sql.exec`) or calls IVG's procedures
   (`kg_KNN_VEC`); graph walks may read globals directly.

## Running IVG inside IRIS

Code that runs in IRIS's embedded Python can build an engine on an in-process connection:

<!-- doc-test: skip reason="runs only inside IRIS's embedded Python (a Language = python method or irispython)" -->

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.dbapi.connect(mode="embedded", namespace="USER")  # iris-embedded-python-wrapper
engine = IRISGraphEngine(conn, embedding_dimension=768)
```

`iris.dbapi.connect(mode="embedded")` comes from `iris-embedded-python-wrapper` (in the `[full]` extra). The older
`iris_vector_graph.embedded.EmbeddedConnection` does the same where the wrapper is not installed; use it only in code
that runs inside IRIS. IVG's HTTP server picks between them on its own ([HTTP server](../ADMIN_API.md)).

What differs inside IRIS:

- **No network round trip** per statement, so per-statement paths (the SQL BFS fallback, row-at-a-time loads) are
  much faster than from a client.
- **A `SELECT` of a raw `VECTOR` column raises** through `EmbeddedConnection`
  ([known issue](../KNOWN_ISSUES.md#inside-iris-a-select-of-a-raw-vector-column-raises)); select
  `VECTOR_COSINE(...)` or a `CAST` of the column.
- **The `iris` module can be shadowed.** `pip install iris-vector-graph` into IRIS's Python also installs the client
  driver's `iris` package, which lacks `iris.cls`, `iris.gref` and `iris.sql`. `iris_vector_graph.embedded` puts
  `/usr/irissys/lib/python` first on `sys.path` so the embedded module wins.
- **Long calls can hit a timeout.** A `[ Language = python ]` method reached through IRIS's web-gateway protocol (the
  REST and MCP services) runs under a timeout of about 30 seconds; loading a model or embedding in bulk belongs in a separate `irispython` process (the
  `^EmbedQueue` pattern, `Graph.KG.EmbedQueue`).

`IVG.CypherEngine` is the ObjectScript-facing form of this: its methods (`QueryJson`, `Scalar`, `InitSchemaJson`, …)
run the Python package in IRIS's Python, so `iris-vector-graph` must be installed there for them to work.
