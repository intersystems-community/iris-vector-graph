# Migrating iris-vector-graph from Python to Rust via rzf — Feasibility Investigation

*Status: investigation only. Nothing here is implemented.*

---

## Question

What would be required to switch all Python in this repository to Rust using **rzf**?

## Short answer

rzf can technically reach every layer of this codebase — it has two modes, and between them
they cover both "IRIS calls Rust" and "Rust calls IRIS". So the migration is not blocked on a
missing capability. It is bounded by three things instead:

1. **Deployment topology.** Call-in mode links `libirisdb` in-process against a local IRIS
   install — explicitly no TCP. This repo's entire client model is TCP to a container
   (`IRIS_HOST=localhost`, `IRIS_PORT=1972`, docker-compose publishing 1972). Every Rust
   client would have to run *inside* the IRIS container, or co-located with the install.
2. **One session per process**, versus a codebase built around many logical connections in one
   process (`AsyncConnectionPool(max_size=5)`, Bolt's `asyncio.start_server`). The `threads`
   feature relaxes this to one session per thread — but is Linux/Windows only, and primary
   development here is macOS/arm64.
3. **License and distribution.** rzf and arno-callout are closed-source in a private repo;
   this package is MIT on PyPI + IPM, and its own design docs list rzf under "does NOT
   transfer (arno IP, closed source)". This is the decisive constraint, and it is a governance
   decision, not an engineering one.

Plus the unavoidable long pole: **90,187 lines of tests**, 66% of all Python here.

---

## 1. rzf's two modes, and what each can host

Mutually exclusive at build time — `rzf_core/build.rs` panics if both or neither feature is
set. This matters architecturally and is addressed in §5.

### Call-out — IRIS calls Rust (`features = ["as_library"]`)

Compiles to `cdylib`. IRIS `dlopen`s it and dispatches via `$ZF(-3)` by name or `$ZF(-6)` by
ordinal. `#[rzf::rzf]` wraps a function in an `extern "C"` shim that unpacks the IRIS arg
stack, folds `Result<T, E>` into an IRIS status code, and registers in the table emitted by
`rzf::init!()`. Variadic entry points take `IRISArgs<'a> -> IRISData<'a>`.

Constraints: no unwinding out of the shim (return `Err`, never `panic!`); already inside the
IRIS process, so `NameSpace::default()` works with no session setup; XDEV is callout-only.

**Hosts:** stateless compute kernels invoked from ObjectScript or SQL. This is the pattern the
repo already ships via `Graph.KG.ArnoAccel` + `arno_bridge.py`.

### Call-in — Rust calls IRIS (`features = ["as_binary"]`)

Links `libirisdb`, needs `IRIS_INSTALL_DIR` at build time (linker search path) and at run
time, plus `LD_LIBRARY_PATH=$IRIS_INSTALL_DIR/bin`. `IrisInstanceConfig::builder()` →
`try_open()` → `try_namespace("USER")`. `#[rzf::rzf]` and `init!()` don't exist in this mode —
`rzf_codegen_macros` only compiles under `as_library`.

Constraints:
- **One session per process.** rzf's own integration suite runs `--test-threads=1`.
- Multithreaded call-in requires the `threads` feature (links `libirisdbt`), **Linux/Windows
  only, not macOS**; each thread opens and closes its own session from a cloned config.
- **No TCP.** In-process linking against the install; `IRIS_HOST`/`IRIS_PORT` are unused.
- IRIS-side prerequisites: `%Service_CallIn` enabled, user's `ChangePassword` flag cleared.

**Hosts:** everything the Python client currently does — this is the piece I need to correct
from an earlier read of this question. It is a genuine IRIS client.

### Shared surface (either mode, once you hold a `NameSpace`)

`globals.rs` (get/set/data/kill, iter/keys, `with_transaction`), `methods.rs` (class and
instance method calls), `xecute.rs`, `data.rs` conversions (`IRISData`, `try_extract`,
`IrisList`, `ShortString` vs `String`). All C status codes route through `iris_call!` →
`Result<_, rzf::Error>`.

Modes diverge only at the setup module (`callin_setup.rs` vs `callout_setup.rs`), the C++ shim
(`no_zf_dll.cpp` vs `zf_dll.cpp`), and macro availability.

---

## 2. Mapping the repo onto those two modes

508 Python files, **135,820 lines**.

| Area | Files | Lines | Mode that fits |
|---|---:|---:|---|
| `tests/` | 361 | 90,187 | Neither — see §4 |
| `iris_vector_graph/` (the library) | 65 | 28,112 | Both, split by layer |
| `scripts/` | 22 | 5,747 | Call-in |
| `examples/` | 19 | 3,899 | Call-in (but they exist to document the *Python* API) |
| `src/` (demo + fraud servers) | 17 | 3,844 | Neither — HTTP servers |
| `api/` (FastAPI + Strawberry) | 20 | 3,078 | Neither — HTTP servers |
| `benchmarks/` | 4 | 953 | Call-in |

Plus **9,230 lines of ObjectScript** across 58 classes, and — running the other direction —
**25 `Language=python` methods across 6 classes** that call *into* Python:
`PageRankEmbedded.cls`, `Graph.KG.{MCPTools,PyOps,Communities,TraversalBFS}.cls`,
`IVG.CypherEngine.cls`. Those six are the natural call-out targets, since they are already
ObjectScript entry points that happen to be implemented in Python.

### The API-surface question, resolved

| Current Python seam | Sites | rzf equivalent |
|---|---:|---|
| Native API — `createIRIS()`, `classMethodValue`, `gref` | 102 | `methods.rs` + `globals.rs`. **Direct match.** |
| DB-API — `cursor()` / `.execute()` | 467, across 26 modules | **No SQL layer in the shared surface.** Route through `%SQL.Statement` via `methods.rs`, or `xecute.rs`. |

The SQL gap is real but has a proven template *in this repo*: `embedded.py:124-174` already
implements exactly this fallback — `iris.cls("%SQL.Statement")._New()` → `_Prepare` →
`_Execute` → iterate `_Next()`/`_GetData(i)` — because embedded Python needs it when
`iris.sql.prepare` hits `<UNIMPLEMENTED>`/ddtab. Nine ObjectScript classes use the same
pattern natively. So the port is mechanical per call site, but it is a rewrite of result-set
and `description`/`rowcount` handling across 26 modules, not a driver swap.

---

## 3. The constraints that actually bound this

### 3.1 Topology — call-in requires co-location

This is the substantive architectural cost. Today:

```
Python client (anywhere)  ──TCP 1972──▶  IRIS in Docker
```

Under call-in there is no TCP path. The Rust binary must link `libirisdb` from a local
`IRIS_INSTALL_DIR`, which means it runs inside the IRIS container or on the install host. That
invalidates:

- `.env.sample`'s `IRIS_HOST`/`IRIS_PORT` contract;
- `docker-compose.yml` publishing 1972 for "the iris_vector_graph Python SDK";
- `IVGClient`/`AsyncIVGClient` in `sdk.py` as remote clients;
- the Bolt server (`bolt_server.py`, 949 lines) as a network-facing service on a separate host;
- `scripts/enterprise-container.sh`'s host→container workflow.

Container images under `docker/` and `deploy/` would additionally need `%Service_CallIn`
enabled and `ChangePassword` cleared. Workable, but it converts a client library into a
component that ships inside the database image.

### 3.2 Session model vs. the concurrency the code assumes

One session per process collides with:

- `gql/pooling.py` — `AsyncConnectionPool(engine, max_size=5)`, semaphore-bounded, sized to
  Community Edition license limits;
- `bolt_server.py:947` — `asyncio.start_server(handle, host, port)`, one coroutine per client;
- `api/` — FastAPI request concurrency on a single process.

Options: a process-per-session worker pool (external supervision, IPC, per-process session
startup cost), or the `threads` feature for thread-per-session. The latter is **unavailable on
macOS**, and this project develops and benchmarks on Apple Silicon — README:80 records "M3
Ultra, Community IRIS 2026.1, ARM64 Docker", `scripts/setup_iris.py:28` picks an image for
Apple Silicon, `docs/coverage.md:57` documents a macOS/arm64 test crash. CI is `ubuntu-latest`
only, so CI would be fine and local dev would be degraded — the inverse of what you want.

### 3.3 License and distribution — still decisive, and mode-independent

- `LICENSE` / `pyproject.toml`: **MIT**. README carries MIT + PyPI badges.
- Distribution: `pip install iris-vector-graph` *and* IPM/ZPM (`module.xml`).
- `docs/architecture/rust-accelerator.md:112` — rzf and arno-callout are "in active
  development in a separate **private** `arno` repository".
- `docs/cypher-gap-recommendations.md`, § "What Transfers vs What Doesn't", under **"Does NOT
  transfer (arno IP, closed source)"**: "The `rzf` crate and `$ZF(-6)` integration code" —
  followed by "**IVG has zero arno code.**"

Both modes are the same crate, so this applies to call-in exactly as much as to call-out. The
present architecture is built to keep rzf out of the dependency graph: runtime `$ZF` lookup by
name, string-typed results, `ArnoAccel` try/catch fallback, `IVG_DISABLE_ARNO=1` for tests.
Making rzf a build dependency inverts that on purpose, and an MIT package that can't be built
from public sources is a different product. **Settle this before costing anything else.**

### 3.4 Libraries with no Rust path

| Dep | Where | Status |
|---|---|---|
| `rdflib` + `pyshacl` (24 import sites) | `_engine/{rdf_export,shacl,prov,_rdf_utils}.py` — **957 lines** | RDF is fine (`oxigraph`/`sophia`). **SHACL has no production Rust validator.** Hard stop. |
| `sentence_transformers` + `torch` | `engine.py`, `_engine/embeddings.py` (848) | `candle` exists; model/tokenizer parity is its own project. Current vehicle is embedded Python (`EmbedQueue.cls`). |
| `python-igraph` + `leidenalg` | `_engine/algorithms.py` | Best case — `kg_leiden_global` already exists in arno. |
| `strawberry-graphql` (19 sites) | `api/gql/**`, `iris_vector_graph/gql/**` | `async-graphql` is a real replacement; full schema + resolver + dataloader rewrite. |
| `fastapi`/`uvicorn`/`pydantic` | `api/`, `src/` | `axum`/`serde`/`validator`. Mechanical, large. |
| `pytest` + `iris-devtester` | `tests/` | No Rust analog for the IRIS container harness. |

### 3.5 Consumer contract

`__init__.py` exports **55 public names**; `pip install` is the documented entry point;
`EmbeddedConnection` is the documented seam for `Language=python` methods. Preserving that
contract requires a PyO3 shim exposing all 55 — so Python stays in the distribution regardless,
which is in tension with "switch *all* Python".

---

## 4. Tiered assessment

| Tier | Content | Lines | Mode | Verdict |
|---|---|---:|---|---|
| **A** | Graph algorithms, traversal, adjacency: `_engine/algorithms.py` (1,093), `cypher/algorithms/paths.py`, `NKGAccel*.cls` | ~4,000 | Call-out | Largely done in arno. Named gap: `kg_bfs_global`. |
| **B** | Cypher stack (`cypher/**` = 7,721), `fusion.py`, `vector_utils.py`, `_validate.py` | ~8,600 | **Neither needed** — pure logic | Zero I/O, string→string. **The best Rust opportunity here.** |
| **C** | Store + I/O: `iris_sql_store.py` (1,750), `schema.py` (1,052), `_engine/{nodes_edges,query,vector}.py`, `bulk_loader.py`, `dbapi_utils.py` | ~9,000 | Call-in | **Unblocked** by `as_binary` — but pays §3.1 topology and §3.2 session costs, and rewrites 467 SQL sites through `%SQL.Statement`. |
| **D** | Services: `api/`, `src/`, `bolt_server.py`, `cli.py`, `sdk.py` | ~8,800 | Neither | axum/tokio/async-graphql. Large, mechanical, low payoff — and §3.1 breaks the remote-client premise these exist to serve. |
| **E** | Semantic layer (957), embeddings (848), `tests/` (90,187), `scripts/`, `examples/` | ~100,000 | — | **No path** for SHACL; test port is the long pole. |

### Effort, assuming licensing is resolved

| Tier | Estimate (1 engineer) |
|---|---|
| A — close arno gaps | 1–1.5 months |
| B — Cypher parity against ~38k lines of unit tests | 3–5 months |
| C — call-in store layer + topology/session rework | 4–6 months |
| D — services port | 2–3 months |
| E — test suite port | 6–12 months, or never |

**~16–28 engineer-months** for the literal request, paid for by losing SHACL validation, the
remote-client deployment model, the PyPI distribution story, and multithreaded local dev on
macOS. Still not recommended as a wholesale switch.

---

## 5. Recommended path

The mutual-exclusivity constraint (`as_library` XOR `as_binary`) is the useful design forcing
function: you cannot ship one crate that is both accelerator and client. That points at a
three-crate layout, which is also the shape that lets you start small.

```
ivg-core/       no rzf dependency at all — pure logic (Tier B)
ivg-callout/    features = ["as_library"]  → libivg_callout.so, #[rzf] + init!()
ivg-client/     features = ["as_binary"]   → binary linking libirisdb
ivg-py/         PyO3 cdylib wrapping ivg-core, imported by iris_vector_graph
```

Note that `ivg-py` must be a *separate* cdylib from `ivg-callout`: both want `crate-type =
["cdylib"]`, and the rzf one carries its own C++ shim and `$ZF` function table. Keeping the
logic in `ivg-core` and the bindings thin is what makes all three viable at once.

Then, in order:

1. **Settle the licensing question.** If rzf cannot be an MIT build dependency, Tiers A and C
   stay behind the runtime `$ZF` seam and only `ivg-core` + `ivg-py` are in scope — which is
   fine, because that is where most of the value is.

2. **Build `ivg-core` + `ivg-py` (Tier B).** 7,721 lines of Cypher lexer/parser/AST/translator
   plus AQL, with no I/O. The existing Python package imports the extension module; the
   existing 38k lines of unit tests validate it unchanged. Real Rust, real speedup on the
   hottest non-I/O path, MIT intact, no consumer break, no topology change. `ivg-core` then
   feeds `ivg-callout` for free, so `IVG.CypherEngine` can call the same implementation
   in-process instead of via embedded Python.

3. **Continue Tier A through the existing seam.** `kg_bfs_global` (already scoped in
   `docs/cypher-gap-recommendations.md`), then Arno paths for `KHop2Count`/`KHop2NeighborIds`
   and `edge_vector_search`, each behind `ArnoAccel`'s try/catch fallback.

4. **Treat Tier C as an opt-in deployment profile, not a replacement.** If an in-container
   Rust component is wanted — bulk load, `^NKG` rebuild, batch embed — `as_binary` is now the
   right tool and those are the workloads that tolerate co-location and one session per
   process. Prove the pattern on `bulk_loader.py` (476 lines, batch, no concurrency
   requirement) before touching `iris_sql_store.py`. Keep the TCP Python client as the
   supported path for everything else.

5. **Leave Tiers D and E in Python.** Keep pytest as the harness for the Rust crates too.

This captures most of the available performance for roughly a tenth of the cost, and preserves
every property currently depended on: MIT licensing, PyPI and IPM distribution, remote TCP
clients, embedded-Python integration, graceful degradation when the Rust binary is absent, and
a 90k-line test suite that keeps working.
