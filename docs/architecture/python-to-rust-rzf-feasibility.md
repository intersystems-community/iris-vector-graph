# Feasibility: moving iris-vector-graph from Python to Rust with rzf

*Investigation only. Nothing here is implemented.*

---

## Question

What would it take to switch all Python in this repository to Rust using rzf?

## Answer

rzf reaches every layer of this codebase. It has two modes, and between them they cover both
"IRIS calls Rust" and "Rust calls IRIS", so nothing is blocked on a missing capability. Four
things bound the work instead:

- Call-in mode has no TCP path, so every Rust client must be co-located with the IRIS install
  (§3.1).
- Call-in gives one session per process, against a codebase built for many connections per
  process (§3.2).
- rzf is closed-source; this package is MIT on PyPI and IPM (§3.3). Decisive, and it needs a
  licensing decision before the engineering is worth costing.
- 90,187 lines of tests, 66% of all Python here (§4, tier E).

---

## 1. The two modes

Mutually exclusive at build time: `rzf_core/build.rs` panics if both features or neither are
set. §5 depends on this.

### Call-out, IRIS calls Rust (`features = ["as_library"]`)

Compiles to `cdylib`. IRIS `dlopen`s it and dispatches via `$ZF(-3)` by name or `$ZF(-6)` by
ordinal. `#[rzf::rzf]` wraps a function in an `extern "C"` shim that unpacks the IRIS arg stack,
folds `Result<T, E>` into an IRIS status code, and registers in the table emitted by
`rzf::init!()`. Variadic entry points take `IRISArgs<'a> -> IRISData<'a>`.

Constraints: nothing may unwind out of the shim, so return `Err` rather than `panic!`; you are
already inside the IRIS process, so `NameSpace::default()` works with no session setup; XDEV is
callout-only.

**Hosts:** stateless compute kernels invoked from ObjectScript or SQL. This is the pattern the
repo already ships, via `Graph.KG.ArnoAccel` and `arno_bridge.py`.

### Call-in, Rust calls IRIS (`features = ["as_binary"]`)

Links `libirisdb`. Needs `IRIS_INSTALL_DIR` at build time for the linker search path and again
at run time, plus `LD_LIBRARY_PATH=$IRIS_INSTALL_DIR/bin`. `IrisInstanceConfig::builder()` →
`try_open()` → `try_namespace("USER")`. `#[rzf::rzf]` and `init!()` do not exist in this mode,
since `rzf_codegen_macros` only compiles under `as_library`.

Constraints:
- **One session per process.** rzf's own integration suite runs `--test-threads=1`.
- Multithreaded call-in needs the `threads` feature, which links `libirisdbt` and is
  Linux/Windows only, not macOS. Each thread opens and closes its own session from a cloned
  config.
- **No TCP.** In-process linking against the install; `IRIS_HOST` and `IRIS_PORT` go unused.
- IRIS-side prerequisites: `%Service_CallIn` enabled, the user's `ChangePassword` flag cleared.

**Hosts:** everything the Python client does today. This is a full IRIS client.

### Shared surface, either mode, once you hold a `NameSpace`

`globals.rs` (get/set/data/kill, iter/keys, `with_transaction`), `methods.rs` (class and
instance method calls), `xecute.rs`, and `data.rs` conversions (`IRISData`, `try_extract`,
`IrisList`, `ShortString` vs `String`). All C status codes route through `iris_call!` into
`Result<_, rzf::Error>`.

The modes diverge only at the setup module (`callin_setup.rs` vs `callout_setup.rs`), the C++
shim (`no_zf_dll.cpp` vs `zf_dll.cpp`), and macro availability.

---

## 2. Mapping the repo onto the modes

508 Python files, 135,820 lines.

| Area | Files | Lines | Mode that fits |
|---|---:|---:|---|
| `tests/` | 361 | 90,187 | Neither (§4, tier E) |
| `iris_vector_graph/` (the library) | 65 | 28,112 | Both, split by layer |
| `scripts/` | 22 | 5,747 | Call-in |
| `examples/` | 19 | 3,899 | Call-in, though they exist to document the *Python* API |
| `src/` (demo + fraud servers) | 17 | 3,844 | Neither; HTTP servers |
| `api/` (FastAPI + Strawberry) | 20 | 3,078 | Neither; HTTP servers |
| `benchmarks/` | 4 | 953 | Call-in |

Alongside it, 9,230 lines of ObjectScript across 58 classes. Running the other direction,
25 `Language=python` methods across 6 classes call into Python: `PageRankEmbedded.cls`,
`Graph.KG.{MCPTools,PyOps,Communities,TraversalBFS}.cls`, and `IVG.CypherEngine.cls`. Those six
are the obvious call-out targets, being ObjectScript entry points already.

### The two client seams

| Current Python seam | Sites | rzf equivalent |
|---|---:|---|
| Native API: `createIRIS()`, `classMethodValue`, `gref` | 102 | `methods.rs` + `globals.rs`. Direct match. |
| DB-API: `cursor()` / `.execute()` | 467, across 26 modules | No SQL layer in the shared surface. Route through `%SQL.Statement` via `methods.rs`, or `xecute.rs`. |

The SQL gap has a working template in this repo already. `embedded.py:124-174` implements the
same path (`iris.cls("%SQL.Statement")._New()` → `_Prepare` → `_Execute` → iterate
`_Next()`/`_GetData(i)`) because embedded Python needs it when `iris.sql.prepare` hits
`<UNIMPLEMENTED>`/ddtab, and nine ObjectScript classes use the pattern natively. Each call site
is a mechanical port, but result-set handling plus `description` and `rowcount` get rewritten
across 26 modules.

---

## 3. What bounds the work

### 3.1 Call-in requires co-location

This is the largest architectural cost in the migration. The current model:

```
Python client (anywhere)  ──TCP 1972──▶  IRIS in Docker
```

Call-in has no TCP path. The Rust binary links `libirisdb` from a local `IRIS_INSTALL_DIR`,
so it runs inside the IRIS container or on the install host. That invalidates:

- the `IRIS_HOST`/`IRIS_PORT` contract in `.env.sample`;
- `docker-compose.yml` publishing 1972 for "the iris_vector_graph Python SDK";
- `IVGClient` and `AsyncIVGClient` in `sdk.py` as remote clients;
- the Bolt server (`bolt_server.py`, 949 lines) as a network service on a separate host;
- the host-to-container workflow in `scripts/enterprise-container.sh`.

Container images under `docker/` and `deploy/` would also need `%Service_CallIn` enabled and
`ChangePassword` cleared. All workable, but it turns a client library into a component that
ships inside the database image.

### 3.2 One session per process, against code that assumes otherwise

Collides with:

- `gql/pooling.py`: `AsyncConnectionPool(engine, max_size=5)`, semaphore-bounded, sized to
  Community Edition license limits;
- `bolt_server.py:947`: `asyncio.start_server(handle, host, port)`, one coroutine per client;
- `api/`: FastAPI request concurrency in a single process.

There are two ways out. A process-per-session worker pool costs external supervision, IPC, and
per-process session startup. The `threads` feature gives thread-per-session but is unavailable on
macOS, and this project develops and benchmarks on Apple Silicon: README:80 records "M3 Ultra,
Community IRIS 2026.1, ARM64 Docker", `scripts/setup_iris.py:28` picks an image for Apple Silicon,
`docs/coverage.md:57` documents a macOS/arm64 test crash. CI is `ubuntu-latest` only, so CI would
be unaffected while local development lost the option.

### 3.3 License and distribution, mode-independent

- `LICENSE` and `pyproject.toml`: MIT. README carries MIT and PyPI badges.
- Distribution is `pip install iris-vector-graph` *and* IPM/ZPM (`module.xml`).
- `docs/architecture/rust-accelerator.md:112`: rzf and arno-callout are "in active development
  in a separate private `arno` repository".
- `docs/cypher-gap-recommendations.md`, under "Does NOT transfer (arno IP, closed source)":
  "The `rzf` crate and `$ZF(-6)` integration code", followed by "IVG has zero arno code."

Both modes are the same crate, so this applies to call-in as much as to call-out. The current
architecture is built to keep rzf out of the dependency graph: runtime `$ZF` lookup by name,
string-typed results, `ArnoAccel` try/catch fallback, `IVG_DISABLE_ARNO=1` for tests. Making
rzf a build dependency inverts that deliberate boundary, and it would leave an MIT package that
cannot be built from public sources. Settle this before costing anything else.

### 3.4 Libraries with no Rust path

| Dep | Where | Status |
|---|---|---|
| `rdflib` + `pyshacl` (24 import sites) | `_engine/{rdf_export,shacl,prov,_rdf_utils}.py`, 957 lines | RDF is covered by `oxigraph`/`sophia`. SHACL has no production Rust validator. Hard stop. |
| `sentence_transformers` + `torch` | `engine.py`, `_engine/embeddings.py` (848) | `candle` exists; model and tokenizer parity is a project of its own. Current vehicle is embedded Python (`EmbedQueue.cls`). |
| `python-igraph` + `leidenalg` | `_engine/algorithms.py` | Cheapest of these: `kg_leiden_global` already exists in arno. |
| `strawberry-graphql` (19 sites) | `api/gql/**`, `iris_vector_graph/gql/**` | `async-graphql` is a replacement, at the cost of a full schema, resolver, and dataloader rewrite. |
| `fastapi`/`uvicorn`/`pydantic` | `api/`, `src/` | `axum`/`serde`/`validator`. Mechanical and large. |
| `pytest` + `iris-devtester` | `tests/` | No Rust analog for the IRIS container harness. |

### 3.5 Consumer contract

`__init__.py` exports 55 public names, `pip install` is the documented entry point, and
`EmbeddedConnection` is the documented seam for `Language=python` methods. Holding that contract
means a PyO3 shim over all 55, so Python stays in the distribution either way. That sits awkwardly
against "switch *all* Python".

---

## 4. Tiered assessment

| Tier | Content | Lines | Mode | Notes |
|---|---|---:|---|---|
| **A** | Graph algorithms, traversal, adjacency: `_engine/algorithms.py` (1,093), `cypher/algorithms/paths.py`, `NKGAccel*.cls` | ~4,000 | Call-out | Largely done in arno. Named gap: `kg_bfs_global`. |
| **B** | Cypher stack (`cypher/**`, 7,721), `fusion.py`, `vector_utils.py`, `_validate.py` | ~8,600 | Neither needed; pure logic | Zero I/O, string to string. The strongest candidate. |
| **C** | Store and I/O: `iris_sql_store.py` (1,750), `schema.py` (1,052), `_engine/{nodes_edges,query,vector}.py`, `bulk_loader.py`, `dbapi_utils.py` | ~9,000 | Call-in | Reachable via `as_binary`, at the cost of §3.1 and §3.2, plus 467 SQL sites rewritten through `%SQL.Statement`. |
| **D** | Services: `api/`, `src/`, `bolt_server.py`, `cli.py`, `sdk.py` | ~8,800 | Neither | axum/tokio/async-graphql. Large, mechanical, low payoff, and §3.1 removes the remote-client premise these exist to serve. |
| **E** | Semantic layer (957), embeddings (848), `tests/` (90,187), `scripts/`, `examples/` | ~100,000 | — | No path for SHACL. The test port is the long pole. |

### Effort, assuming licensing is resolved

| Tier | Estimate, one engineer |
|---|---|
| A, close arno gaps | 1–1.5 months |
| B, Cypher parity against ~38k lines of unit tests | 3–5 months |
| C, call-in store layer plus topology and session rework | 4–6 months |
| D, services port | 2–3 months |
| E, test suite port | 6–12 months, or never |

Roughly 16–28 engineer-months for the literal request. The bill includes SHACL validation,
the remote-client deployment model, the PyPI distribution story, and multithreaded local
development on macOS. I would not do it as a wholesale switch.

---

## 5. Recommended path

Because `as_library` and `as_binary` are mutually exclusive, one crate cannot be both
accelerator and client. That forces a core-plus-bindings layout, which is also what lets the
work start small:

```
ivg-core/       no rzf dependency at all; pure logic (tier B)
ivg-callout/    features = ["as_library"]  → libivg_callout.so, #[rzf] + init!()
ivg-client/     features = ["as_binary"]   → binary linking libirisdb
ivg-py/         PyO3 cdylib wrapping ivg-core, imported by iris_vector_graph
```

`ivg-py` has to be a separate cdylib from `ivg-callout`: both claim `crate-type = ["cdylib"]`,
and the rzf one carries its own C++ shim and `$ZF` function table. Thin bindings over a shared
`ivg-core` is what makes all three buildable at once.

Then, in order:

1. **Settle the licensing question.** If rzf cannot be an MIT build dependency, tiers A and C
   stay behind the runtime `$ZF` seam and the scope narrows to `ivg-core` and `ivg-py`, which is
   where most of the value sits anyway.

2. **Build `ivg-core` and `ivg-py` (tier B).** 7,721 lines of Cypher lexer, parser, AST, and
   translator, plus AQL, with no I/O. The Python package imports the extension module and the
   existing 38k lines of unit tests validate it unchanged: no consumer break, no topology
   change, MIT intact. `ivg-core` then feeds `ivg-callout` at no extra cost, letting
   `IVG.CypherEngine` call the same implementation in-process instead of through embedded
   Python.

3. **Continue tier A through the existing seam.** `kg_bfs_global` first, since it is already
   scoped in `docs/cypher-gap-recommendations.md`, then Arno paths for
   `KHop2Count`/`KHop2NeighborIds` and `edge_vector_search`, each behind `ArnoAccel`'s
   try/catch fallback.

4. **Treat tier C as an opt-in deployment profile.** For an in-container Rust component (bulk
   load, `^NKG` rebuild, batch embed), `as_binary` is the right tool, and those workloads
   tolerate co-location and one session per process. Prove it on `bulk_loader.py` (476 lines,
   batch, no concurrency requirement) before touching `iris_sql_store.py`. The TCP Python client
   stays the supported path for everything else.

5. **Leave tiers D and E in Python**, with pytest as the harness for the Rust crates too.

Step 2 on its own covers the hottest non-I/O path and costs none of §3.1 through §3.3.
