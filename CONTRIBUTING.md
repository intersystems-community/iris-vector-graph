<!-- markdownlint-disable MD013 -->

# Contributing to iris-vector-graph

This page is for people who change IVG itself. If you use IVG from an application, start with the
[README](README.md); if you run it on an IRIS instance, start with the [Admin Guide](docs/ADMIN_GUIDE.md).

Everything a contributor needs beyond this page lives under [`docs/dev/`](docs/dev/):

| Document                                                                 | What it covers                                                                  |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| [Testing policy](docs/dev/TESTING_POLICY.md)                             | The rules every test follows, and the "Coverage That Counts" checklist          |
| [openCypher TCK](docs/dev/TCK.md)                                        | How the TCK figure is produced, what a pass checks, how to rerun it             |
| [Coverage](docs/dev/coverage.md)                                         | Python and ObjectScript coverage gates and how to measure them                  |
| [uv setup](docs/dev/UV_SETUP.md)                                         | Setting up the development environment with `uv`                                |
| [Design and research notes](docs/dev/notes/)                             | Benchmarks harness, spikes, enhancement proposals, the known-issues history     |
| [ObjectScript coverage floor](docs/dev/objectscript-coverage-floor.json) | The floor `scripts/line_coverage/check_gate.py` enforces                        |
| [Arno reader proof](docs/dev/arno-reader-proof.json)                     | Which Arno readers passed `scripts/arno_reader_gate.py`, on which library build |

## Development setup

Python 3.10 or later (3.11 and 3.13 are checked). From a clone:

```bash
uv sync --extra dev          # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

The `dev` extra carries everything the test suite imports, including `iris-devtester` (its `idt` CLI drives the test
containers). An optional package a test needs belongs in the `dev` extra, never behind a silent `importorskip`.

CI installs with pip from `pyproject.toml` and does not read `uv.lock`, which is what `uv sync` installs. After
changing a dependency, run `uv lock`: `tests/unit/test_460_r124_uv_lock_current.py` fails until the lock records it.
Every dependency must be on the package index, since `uv lock` resolves every extra.
[docs/dev/UV_SETUP.md](docs/dev/UV_SETUP.md) has the details.

Lint before you commit:

```bash
ruff check . && black . && isort .
```

## Test containers

The test suite runs against a persistent, named IRIS container, never an ephemeral one (`IRISContainer.start()`
creates containers that vanish when the process exits). Two scripts manage them:

```bash
scripts/enterprise-container.sh up       # ivg-iris-enterprise: the suite's default, with the Arno library
scripts/enterprise-container.sh status   # health, and whether Arno is loaded
scripts/enterprise-container.sh deploy   # copy iris_src/src into the container (/tmp/src); loads nothing
scripts/enterprise-container.sh compile-all   # load and compile it into USER through irispython
scripts/enterprise-container.sh tcp-deploy    # load and compile it over TCP (what make_scratch_namespace.py runs)
scripts/enterprise-container.sh down     # remove it (destroys its data)

scripts/test-container.sh up             # ivg-iris: Community Edition
scripts/test-container.sh status
scripts/test-container.sh deploy         # copy iris_src/src into the container; compiles nothing
scripts/test-container.sh compile Graph.KG.NKGAccel   # compile one class
scripts/test-container.sh compile-all    # compile everything
scripts/test-container.sh down
```

With `IVG_NAMESPACE` set, the enterprise script's `deploy`, `compile`, `compile-all` and `tcp-deploy`, and the
Community script's `deploy`, `compile` and `compile-all`, work in that namespace instead of USER (`deploy` and the
compiles stage in `/tmp/src_<namespace>`). Those that load refuse a namespace recorded under another IVG Schema before
loading anything: the sources name `Graph_KG`. Set it up with `IRISGraphEngine(conn, schema_prefix=...).initialize_schema()`
instead. `tcp-deploy` skips the classes `initialize_schema` skips (on Community, the three MCP classes, which need
`%AI`) and prints each with the reason; only a class it loads and cannot compile is an error.

| Container             | Image                         | Host port | Notes                                                                                                                        |
| --------------------- | ----------------------------- | --------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `ivg-iris-enterprise` | `irishealth:2026.3.0AI.113.0` | 31972     | The default. Built from an InterSystems-internal image; mounts a license key and the Arno library, neither in the repository |
| `ivg-iris`            | IRIS Community                | 21972     | `IVG_CONTAINER_PORT` changes the port when starting it                                                                       |

Outside InterSystems, run the suite against `ivg-iris` with `IVG_TEST_CONTAINER=ivg-iris IVG_PORT=21972`. Community
Edition allows few connections (`MaxServerConn`), so run tests in one process there, and on a machine with many cores
(16 or more) Community Edition may refuse to start; the enterprise container is the primary one where it is available.

A test module that defaults to a port this repository does not own fails the build
(`tests/unit/test_230_test_port_defaults.py`): connecting to an unpublished port fails with
`<COMMUNICATION LINK ERROR>`, which reads like a driver or licensing fault and is not one.

The containers persist across machine restarts while Docker runs. Data survives a container restart only if IRIS was
stopped gracefully first (`docker exec <container> iris stop IRIS quietly`).

Tests that need a namespace of their own use a scratch namespace with its own database, made by
`scripts/dev/make_scratch_namespace.py` (names starting `IVG262` or `IVG460`), never `USER` on a shared container.

### The FHIR namespace (IVGFHIR)

The FHIR E2E files (`test_231_fhir_*`, `test_232_fhir_*`, `test_233_*`, most of `test_235_*`, `test_fhir_demo_e2e.py`,
`test_bio_demo_e2e.py`, `test_demo_browser_e2e.py`, `test_fhirgraph_*` and `test_411_rebuild_deleted_history_e2e.py`)
run in the namespace `IVGFHIR`, against a FHIR R4 endpoint at `/csp/healthshare/ivgfhir/fhir/r4`. A container made by
`scripts/enterprise-container.sh up` has neither, and those files skip with a reason that names this section. No
script makes them. To make a container FHIR-capable, run this once (it needs the `irishealth` image: IRIS Community has
no FHIR server):

```bash
docker exec -i ivg-iris-enterprise iris session IRIS -U HSLIB <<'EOF'
Set tSC=##class(HS.Util.Installer.Foundation).Install("IVGFHIR") Write "foundation ",$System.Status.IsOK(tSC),!
ZNspace "IVGFHIR"
Do ##class(HS.FHIRServer.Installer).InstallNamespace()
Do ##class(HS.FHIRServer.Installer).InstallInstance("/csp/healthshare/ivgfhir/fhir/r4","HS.FHIRServer.Storage.JsonAdvSQL.InteractionsStrategy","hl7.fhir.r4.core@4.0.1")
Halt
EOF
```

Use a container name other than `ivg-iris-enterprise` for a second container. The namespace must be new: its first
FHIR endpoint gets the repository package `X0001`, and the fixtures name it (graph `fhir:IVGFHIR:X0001`, table
`HSFHIR_X0001_R.Rsrc`). Nothing else is installed by hand. The first test session runs `initialize_schema()` in the new
namespace (it has no IVG Schema record yet), deploys every `Graph.KG` class and the test loader `IVGTest.FHIRLoad`,
and empties the repository.
The enterprise image has no web server, so resources are loaded through `IVGTest.FHIRLoad`, not over HTTP.

Check it with one file, on the container's port:

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest tests/e2e/test_231_fhir_graph_build_e2e.py
```

IVGFHIR is scratch: every session empties the repository and erases its FHIR graphs. When the session cannot start,
the reason is given once. If no IRIS answers on the port, the files on `fhir_conn` skip, and those on
`fhir_conn_required` (`test_233_*`, most of `test_235_*`, `test_411_rebuild_deleted_history_e2e.py`) fail unless
`SKIP_IRIS_TESTS=true`. If IVGFHIR or its endpoint is missing, every FHIR file skips. If IVGFHIR is there but its
setup raises (a compile error, a schema refusal), the first FHIR test fails with the traceback, and the rest skip with
the same reason.

### Environment variables for tests

| Variable                   | Default               | Purpose                                                                                       |
| -------------------------- | --------------------- | --------------------------------------------------------------------------------------------- |
| `IVG_TEST_CONTAINER`       | `ivg-iris-enterprise` | The container the suite and the demos connect to, by name                                     |
| `IVG_PORT`                 | `31972`               | IRIS SuperServer port of that container                                                       |
| `IVG_ARNO_PORT`            | `31971`               | The same container's second mapping, for Arno fixtures (`31972` when the proxy is not used)   |
| `IVG_NAMESPACE`            | `USER`                | Namespace the integration and E2E fixtures connect to                                         |
| `IVG_SECONDARY_NAMESPACE`  | unset                 | A second scratch namespace for the cross-namespace tests                                      |
| `IVG_KEEP_CONTAINER`       | unset                 | `1` keeps a container the fixtures started                                                    |
| `IVG_AUTO_START_CONTAINER` | `0`                   | `1` lets the fixtures start a Community container when none is running                        |
| `SKIP_IRIS_TESTS`          | `false`               | `true` skips tests that need IRIS (CI's unit job); it does not hide a running container       |
| `IVG_DOCTEST_NAMESPACE`    | `IVG460DOC`           | Scratch namespace the doc-run test empties and runs the docs in                               |
| `IVG_DOCTEST_JSON`         | unset                 | Also write each document's doc-run summary to this JSON file                                  |
| `IVG_GATE`                 | unset                 | `1`: a skipped E2E test fails unless `tests/e2e/gate_skips.txt` allows it (ci-parity sets it) |
| `IVG_GATE_NAMESPACES`      | `1`                   | `0` stops the suite making the per-run scratch namespaces the gated E2E files need            |
| `IVG_GATE_KEEP_NAMESPACES` | unset                 | `1` leaves this run's scratch namespaces in place at the end                                  |

## Test layout

| Directory            | What runs there                                                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------- |
| `tests/unit/`        | Fast tests; some classes in a file need IRIS and skip with `SKIP_IRIS_TESTS=true`                        |
| `tests/integration/` | Tests against a live IRIS through the fixtures in `tests/conftest.py`                                    |
| `tests/e2e/`         | End-to-end tests of a user story against a live IRIS; run them a file at a time on a shared container    |
| `tests/docs/`        | The docs tests: every doc code block runs and its stated answer is checked; every link and name resolves |
| `tests/tck/`         | The openCypher TCK harness ([docs/dev/TCK.md](docs/dev/TCK.md))                                          |
| `tests/contract/`    | GraphQL contract tests                                                                                   |
| `tests/regression/`  | Size and complexity budgets                                                                              |
| `tests/benchmarks/`  | Benchmarks, not run by default                                                                           |

A typical run:

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest tests/unit
pytest tests/docs/test_docs_consistent.py
```

Write the test before the code, and assert the answer, not the shape: a test of a query shows a known question getting
its known answer, worked out by hand. The full rules are in [the testing policy](docs/dev/TESTING_POLICY.md).

### The docs tests

`tests/docs/test_docs_run.py` runs every code block of the documents listed in `tests/docs/docs.toml`, in a freshly
emptied scratch namespace, and compares the answers the doc states (`# →` lines and `<!-- doc-test: output -->`
blocks). `tests/docs/test_docs_consistent.py` checks that every link, path, class, method, global, environment
variable, port and container name a doc gives exists. Both are ratchets on a known-failures list
(`tests/docs/run_known_failures.txt`, `tests/docs/consistency_known_failures.txt`): a new failure fails, and a fixed
one fails until its line is deleted. Never add a line to make a new failure pass.

A block that cannot run in the harness is marked `<!-- doc-test: skip reason="…" -->`; a `bash` block is only
syntax-checked unless marked `<!-- doc-test: run -->`.

## CI parity

Run `scripts/ci-parity.sh` before any push. It copies exactly what a commit would carry into a scratch directory,
builds fresh virtual environments from `pyproject.toml`, and runs the unit suite with warnings as errors, the docs
tests, and a wheel build installed into a clean venv. A green run in your `.venv` proves nothing about CI: the `.venv`
is not a clean install, and a test that reads a gitignored file passes there and fails in CI.

ci-parity runs in gate mode (`IVG_GATE=1`) and under both drivers: the first Python version's unit suite once with
intersystems-irispython 5.4.0 and once with 5.4.1, and, with the test container running,
`scripts/dev/driver_matrix.sh` over the property-reading E2E files in a namespace made for the run.

The E2E files that need a scratch namespace of their own get one without any setting: `tests/e2e/gate.py` makes it
for the run from this checkout, sets `IVG_NAMESPACE` (or the file's own variable) for that file only, and drops it at
the end. In gate mode a skipped E2E test fails unless `tests/e2e/gate_skips.txt` lists it with a reason; the testing
policy, "Why 4.6.0's defects passed the suite", says why.

## TCK

The openCypher TCK runs through `tests/tck/` and `scripts/tck/`; [docs/dev/TCK.md](docs/dev/TCK.md) says how the
published figure is produced and how to rerun it. A change to the Cypher translator or the native path runs the TCK
areas it touches, and the full TCK before a release (0 regressions).

## Coverage gates

Python coverage must stay at or above 89%; ObjectScript line coverage (adjusted) at or above 91%, measured against
[the floor file](docs/dev/objectscript-coverage-floor.json). [docs/dev/coverage.md](docs/dev/coverage.md) says how to
measure both.

## Releases

A release follows [PRE_RELEASE_CHECKLIST.md](PRE_RELEASE_CHECKLIST.md) step by step, and its sign-off is recorded
there. Release notes live in [CHANGELOG.md](CHANGELOG.md). Nothing is pushed, tagged or published without the
maintainer's explicit word.
