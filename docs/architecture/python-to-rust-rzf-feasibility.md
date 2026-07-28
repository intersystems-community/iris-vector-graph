# Migrating iris-vector-graph from Python to Rust via rzf — Feasibility Investigation

*Status: investigation only. No code changes proposed here are implemented.*

---

## Question

What would be required to switch all Python in this repository to Rust using **rzf**?

## Short answer

A literal "all Python → Rust via rzf" migration is not achievable as stated, for three
independent reasons:

1. **Direction mismatch.** rzf generates `$ZF(-5)` callout entry points — it lets *IRIS call
   into Rust*. The overwhelming majority of Python here is a *client* that calls *into IRIS*
   (DB-API SQL + Native API). rzf cannot host that code; it is the wrong tool for ~80% of the
   surface area.
2. **No Rust IRIS client exists.** 467 cursor/execute call sites and 102 Native API call sites
   (`iris.createIRIS()`, `classMethodValue`, `gref`) have no Rust equivalent. The Rust→IRIS
   callin bridge (`rustcallin`) lives in the private `arno` workspace.
3. **License/IP boundary.** This repo is MIT and ships on PyPI + IPM. `rzf` and `arno-callout`
   are closed-source in a private repository, and the existing design documents
   *explicitly* list rzf under "does NOT transfer (arno IP, closed source)". Making rzf a
   build dependency of the whole product would make an MIT package unbuildable from public
   sources.

What *is* achievable, and worth doing, is a tiered migration where ~14k lines of pure-logic
Python move to Rust — some via rzf, most via PyO3 — while the client, semantic, and test
layers stay in Python. That plan is in [Recommended path](#recommended-path).

---

## 1. What rzf is, and what it can host

From `docs/architecture/rust-accelerator.md`:

> `arno/iris-integration/rzf/` is a Rust crate that makes writing IRIS `$ZF` callout functions
> feel native. A `#[rzf]` proc-macro on a Rust function auto-generates the C ABI entry point,
> type marshaling, and registration boilerplate that `$ZF(-5)` expects.

The control flow is fixed:

```
IRIS process
  └── $ZF(-4) dlopen  →  libarno_callout.so
        └── $ZF(-5, dllid, fnid, arg1, arg2, …)  →  #[rzf] fn(…) -> String
```

Properties that constrain what can be migrated:

| Property | Consequence for migration |
|---|---|
| Rust runs *inside* the IRIS process as a loaded `.so` | Cannot be a standalone process, HTTP server, or CLI |
| Entry points take scalar args, return a string | No streaming, no long-lived connections, no async |
| Invoked *by* ObjectScript/SQL | Cannot originate a workflow; something must call it |
| Reads data via global access (`^KG`, `^NKG`) | Works for graph compute; not for arbitrary SQL |

So rzf is a good fit for **stateless compute kernels invoked from inside IRIS**. It is not a
runtime for orchestration, HTTP APIs, protocol servers, or database clients.

The repo already uses this pattern correctly and deliberately: `iris_vector_graph/stores/arno_bridge.py`
wraps `$ZF(-4)`/`$ZF(-5)` behind `CREATE OR REPLACE FUNCTION … LANGUAGE OBJECTSCRIPT` shims,
`Graph.KG.ArnoAccel` wraps every call in try/catch with an ObjectScript fallback, and
`IVG_DISABLE_ARNO=1` forces the fallback path for tests. Rust is an *optional accelerator
behind a runtime-detected seam*, not a dependency.

---

## 2. Scope of the Python surface

508 Python files, **135,820 lines** total.

| Area | Files | Lines | Notes |
|---|---:|---:|---|
| `iris_vector_graph/` (the library) | 65 | 28,112 | The actual product |
| `tests/` | 361 | 90,187 | **66% of all Python** |
| `scripts/` | 22 | 5,747 | Ingest, ops, dev tooling |
| `examples/` | 19 | 3,899 | Documentation-by-example |
| `src/` (demo + fraud servers) | 17 | 3,844 | FastHTML/FastAPI demos |
| `api/` (FastAPI + Strawberry GraphQL) | 20 | 3,078 | HTTP surface |
| `benchmarks/` | 4 | 953 | |

Alongside it: **9,230 lines of ObjectScript** across 58 classes that the Python drives — and,
critically, **25 `Language=python` methods across 6 classes** that call *into* Python:

```
iris_src/src/PageRankEmbedded.cls
iris_src/src/Graph/KG/MCPTools.cls
iris_src/src/Graph/KG/PyOps.cls
iris_src/src/Graph/KG/Communities.cls
iris_src/src/Graph/KG/TraversalBFS.cls
iris_src/src/IVG/CypherEngine.cls
```

The ObjectScript layer depends on Python, not just the reverse. Removing Python breaks these
six classes.

### Largest single modules

| Module | Lines | Migratable via rzf? |
|---|---:|---|
| `cypher/translator.py` | 4,212 | No (rzf works, but PyO3 is the right seam) |
| `stores/iris_sql_store.py` | 1,750 | No — needs a Rust IRIS client |
| `cypher/parser.py` | 1,381 | No (same as translator) |
| `_engine/vector.py` | 1,110 | Partly — 24 Native API sites |
| `_engine/nodes_edges.py` | 1,109 | No — pure I/O |
| `_engine/algorithms.py` | 1,093 | **Yes** — already partly done in arno |
| `schema.py` | 1,052 | No — DDL orchestration |
| `engine.py` | 977 | No — dispatch/orchestration |
| `_engine/query.py` | 974 | No — pure I/O |
| `bolt_server.py` | 949 | No — needs tokio, not rzf |

---

## 3. Blocking dependencies

### 3.1 IRIS connectivity — the hard blocker

Every data path goes through one of two Python-only seams:

- **DB-API** (`intersystems-iris`, `iris-embedded-python-wrapper`) — 467 `cursor()`/`.execute()`
  call sites in `iris_vector_graph/`.
- **Native API** (`iris.createIRIS(conn)` → `classMethodValue`, `gref`) — 102 call sites,
  concentrated in `_engine/vector.py` (24), `stores/iris_sql_store.py` (23), `engine.py` (11).

There is no InterSystems-supported Rust driver. The options, all bad:

| Option | Problem |
|---|---|
| ODBC via `odbc-api` + the IRIS ODBC driver | Gives you SQL only. Loses the Native API entirely — all 102 `classMethodValue`/`gref` sites have no replacement. `_detect_arno()` itself uses `classMethodValue`. |
| Private `rustcallin` (Rust → IRIS callin bridge) | Closed-source, in the arno workspace. Unreleased. Same IP problem as rzf. |
| Reimplement the superserver wire protocol | Undocumented, unsupported, multi-quarter, and a permanent maintenance liability. |
| Invert everything to run *under* IRIS via rzf | Requires rewriting the product as a set of callout functions with no client — breaks `pip install`, the CLI, the Bolt server, and the HTTP API. |

This alone bounds the migration: the store layer, schema management, bulk loader, and most of
`_engine/` cannot move until a Rust IRIS client exists.

### 3.2 No-Rust-equivalent libraries

| Python dep | Where | Rust status |
|---|---|---|
| `rdflib` + `pyshacl` (24 import sites) | `_engine/rdf_export.py`, `shacl.py`, `prov.py`, `_rdf_utils.py` — **957 lines** | RDF: `oxigraph`/`sophia` are viable. **SHACL: no production Rust validator.** Hard stop for the semantic layer. |
| `sentence_transformers` + `torch` | `engine.py`, `_engine/embeddings.py` (848 lines) | `candle` exists but model/tokenizer parity is a project of its own; embedded-Python is the current delivery vehicle (`EmbedQueue.cls`). |
| `python-igraph` + `leidenalg` | `_engine/algorithms.py` | Best case here — `kg_leiden_global` already exists in arno. |
| `strawberry-graphql` (19 import sites) | `api/gql/**`, `iris_vector_graph/gql/**` | `async-graphql` is a real replacement, but it's a full schema + resolver + dataloader rewrite. |
| `fastapi`/`uvicorn`/`pydantic` | `api/`, `src/` | `axum`/`serde`/`validator` — mechanical but large. |
| `pytest` + `iris-devtester` | 90,187 lines of tests | `iris-devtester` is a Python-only IRIS container harness with no Rust analog. |

### 3.3 License and distribution — likely the decisive blocker

- `LICENSE` / `pyproject.toml`: **MIT**. README carries an MIT badge and a PyPI badge.
- Distribution is `pip install iris-vector-graph` (PyPI) *and* IPM/ZPM (`module.xml`).
- `docs/architecture/rust-accelerator.md:112`: "The `rzf` crate and `arno-callout` library are
  in active development in a separate **private** `arno` repository."
- `docs/cypher-gap-recommendations.md` (§ "What Transfers vs What Doesn't") lists under
  **"Does NOT transfer (arno IP, closed source)"**: "The `rzf` crate and `$ZF(-6)` integration
  code" — and states "**IVG has zero arno code.** arno-callout is a separately deployed binary."

The current architecture is designed *specifically* to keep rzf out of the dependency graph:
runtime `$ZF` lookup by function name, string-typed results, graceful fallback, and an env
var to disable it. Making rzf a compile-time dependency of the entire product inverts that
deliberate boundary. That is a licensing/governance decision, not an engineering one, and it
must be settled before any of the work below is worth starting.

### 3.4 Consumer API break

`iris_vector_graph/__init__.py` exports **55 public names**. `pip install iris-vector-graph`
is the documented path in the README, and `EmbeddedConnection` is the documented seam for
ObjectScript `Language=python` methods. A Rust rewrite that preserved this contract would
need a PyO3 shim exposing all 55 names — meaning Python remains in the distribution anyway,
which defeats "switch *all* Python".

---

## 4. Tiered assessment

| Tier | Content | Lines | rzf applicable? | Verdict |
|---|---|---:|---|---|
| **A** | Graph algorithms, traversal, adjacency: `_engine/algorithms.py`, `cypher/algorithms/paths.py`, `NKGAccel*.cls` | ~4,000 | **Yes — ideal** | Largely already done in arno. Remaining gap named in the docs: `kg_bfs_global`. |
| **B** | Cypher stack (`cypher/**` = 7,721), `fusion.py`, `vector_utils.py`, `_validate.py` | ~8,600 | Works, but PyO3 is the better seam | Pure string→string, zero I/O. **The best actual Rust opportunity.** |
| **C** | Store + I/O: `iris_sql_store.py`, `schema.py`, `bulk_loader.py`, `dbapi_utils.py`, most of `_engine/` | ~9,000 | No | **Blocked** on a Rust IRIS client. |
| **D** | Services: `api/`, `src/`, `bolt_server.py`, `cli.py`, `sdk.py` | ~8,800 | No (rzf can't host a server) | Portable to axum/tokio/async-graphql. Mechanical, large, low payoff. |
| **E** | Semantic layer (rdflib/pyshacl, 957), embeddings (848), `tests/` (90,187), `scripts/`, `examples/` | ~100,000 | No | **No Rust path.** Keep in Python or drop features. |

### Effort, for the literal request

Assuming the licensing question is resolved *and* a Rust IRIS client materialises:

| Tier | Estimate (1 engineer) |
|---|---|
| A — close arno gaps | 1–1.5 months |
| B — Cypher parity against ~38k lines of unit tests | 3–5 months |
| C — store layer, *after* a driver exists | 3–4 months (driver itself: multi-quarter, unsupported) |
| D — services port | 2–3 months |
| E — test suite port | 6–12 months, or never |

**Total: roughly 18–30 engineer-months**, contingent on unreleased private crates, and paid
for by losing SHACL validation, the PyPI distribution model, and the embedded-Python
integration path. I do not recommend it.

---

## Recommended path

Do the parts that are actually good ideas, in this order. None of them require relicensing
or an IRIS Rust driver.

1. **Settle the licensing question first.** If rzf cannot be an MIT build dependency — and the
   existing docs say it cannot — then Tier A stays exactly where it is: an optional,
   separately-deployed binary behind the `$ZF` + fallback seam. Everything else follows from
   this answer.

2. **Tier B as a PyO3 extension crate** (e.g. `ivg-cypher`). The Cypher lexer, parser, AST,
   translator, and AQL variant are 7,721 lines of pure logic with no I/O. Compile to a native
   Python module; the existing `iris_vector_graph` package imports it unchanged; the existing
   test suite validates it unchanged. This is real Rust, delivers real speedup on the hottest
   non-I/O path, keeps MIT, keeps `pip install`, and breaks no consumer. The same crate can
   *additionally* expose `#[rzf]` entry points so `IVG.CypherEngine` calls Rust directly
   inside IRIS — one implementation, two bindings.

3. **Continue Tier A through the existing seam.** Add `kg_bfs_global` (already scoped in
   `docs/cypher-gap-recommendations.md`) and an Arno path for `KHop2Count`/`KHop2NeighborIds`
   and `edge_vector_search`, each behind the `ArnoAccel` try/catch fallback. No architectural
   change; this is the pattern the repo already ships.

4. **Leave Tiers C, D, E in Python.** The store layer is blocked, the services port has a poor
   effort-to-benefit ratio, and the semantic layer has no Rust story at all. Keep pytest as
   the harness for everything, including the Rust crates.

This gets most of the available performance for ~10% of the cost of the literal migration,
and it preserves every property the project currently depends on: MIT licensing, PyPI and IPM
distribution, embedded-Python integration, graceful degradation when the Rust binary is
absent, and a 90k-line test suite that keeps working.
