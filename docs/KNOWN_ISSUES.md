<!-- markdownlint-disable MD013 -->

# Known Issues

Open findings carried into the next release, each verified against the working tree
on the date in its heading. A finding stays here until it is fixed or ruled out;
ruled-out entries move to [Investigated, not a defect](#investigated-not-a-defect)
with the evidence, so the same suspicion is not re-raised.

---

## Test and build environment (verified 2026-09-16, re-measured 2026-09-18)

### Five integration files segfault on their first test

`pytest tests/integration` dies with `Fatal Python error: Segmentation fault`
(exit 139). The earlier reading of this — "the driver gives out after ~1000
tests share one session-scoped connection" — is wrong. Run one file per
process and five of them crash **alone, on their first test**, with nothing
before them:

```text
tests/integration/test_cypher_multi_type.py
tests/integration/test_cypher_rd.py
tests/integration/test_cypher_rel_vars.py
tests/integration/test_cypher_single_type.py
tests/integration/test_cypher_untyped.py
```

Every crash lands at `tests/integration/conftest.py:139`, in `_execute`, inside
the driver's own `cursor.execute`. The shape they share is a
relationship-variable `MATCH` with a `LIMIT`:

```cypher
MATCH (t:Transaction)-[r]->(b) RETURN t.node_id, r, b.node_id LIMIT 10
```

That is the query shape already recorded in the `driver_segfault_query_shape`
and `qaqpre_fetch_first_join_crash` notes: a multi-table JOIN over VARCHAR keys
with a row limit, crashing in `%qaqpre` below the Python layer. It is an IRIS/driver
defect, not an endurance limit, and a 19-file run of the remaining chunk-1 files
completes without a crash.

Run the suite as separate processes, and expect the five files above to give no
result at all:

```bash
files=($(ls tests/integration/test_*.py)); third=$((${#files[@]}/3))
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m pytest "${files[@]:0:$third}" -q -p no:randomly
# ...repeat for the remaining two thirds
```

Until this is fixed, §2 of `PRE_RELEASE_CHECKLIST.md` cannot be satisfied by the
single command it prints.

### ~70 integration failures and 26 errors against the v3 API, all pre-existing

Run as chunks with the five crashing files excluded, the suite gives roughly 70
failures and 26 errors (2026-09-18: 70/26; 2026-09-16: ~66/26 — the count moves with
where the chunk boundaries fall, the errors do not). An earlier count of "51 failures
and 4 errors" was measured with a chunk that segfaulted early and lost its own
results; this is the fuller number.

None of it is a regression. The 36 node IDs newly counted here were replayed at
the `v3.0.1` tag in a worktree at `15f3d78`: zero passed there (`6 failed, 24
errors, 6 skipped`). The only six that differ are Arno cases that skip at
`v3.0.1` because no enterprise container was attached and fail here for the
`_detect_arno` reason below. What the rest have in common is a test written
against a pre-v3 API:

| Cause                                                        | Count | Example                                                       |
| ------------------------------------------------------------ | ----- | ------------------------------------------------------------- |
| `IVGResult.get('rows')` — removed, use `.rows` or `['rows']` | 20    | `test_query_engine_special.py`                                |
| `DeleteResult` compared to `int`                             | 6     | `test_nodes_edges_deep.py:413`                                |
| Reification `SQLCODE -29`, field not in the applicable table | 5     | `test_schema_and_engine_procs.py:158`                         |
| LazyKG results keyed by node ID, not label                   | 4     | `test_lazykg_algorithms.py:59`                                |
| `from iris_vector_graph._engine.vector import _table`        | 2     | `test_vector_engine_deep.py:190`                              |
| FK on `rdf_edges` not enforced — fixed in 4.0.0              | 3     | `test_nodepk_constraints.py:152`, `test_diabolical_qa.py:200` |
| Remaining one-offs (embed queue, PPR, chunked Arno, RRF)     | 15    | see the recipe below                                          |

The counts in that table classify the 55 cases catalogued on the first pass; the
36 added by the fuller run fall into the same groups and are not re-tallied here.

The `rdf_edges` row above is closed by 4.0.0. `rdf_edges` now declares
`fk_edges_source` and `fk_edges_dest`, both composite against
`Graph_KG.nodes (graph_id, node_id)`, and IRIS enforces them: a dangling edge
INSERT is refused with `SQLCODE -121` and a node holding edges cannot be deleted.
`test_nodepk_constraints.py` and `test_diabolical_qa.py` are 46 passed / 2
skipped live, and the `xfail` on `test_delete_node_blocked_by_edge` was removed
because it xpassed. The two remaining skips are not the same issue: `rdf_props`
still carries no foreign key on `s`, deliberately, so that RDF 1.2 quoted
triples can hold edge metadata whose subject is not a node.

Reproduce the list:

```bash
grep -hE "^(FAILED|ERROR) " chunk*.log | sed 's/^[A-Z]* //; s/ - .*//' | sort -u > failing.txt
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m pytest $(tr '\n' ' ' < failing.txt) -q -p no:randomly --tb=line
```

The FK group is the one worth reading as a product question rather than test
rot: `create_edge` to a nonexistent node returns `True`, and inserting an edge
with a missing endpoint raises nothing.

**Workaround:** do not gate a release on `tests/integration` reaching zero — the
baseline is this list, not a green run. Compare a fresh `failing.txt` against the
previous one and read only the difference; the causes in the table above each name
the current spelling (`.rows` for `IVGResult.get('rows')`, `DeleteResult.deleted`
for a bare `int` comparison), so a case that still fails for a listed reason is a
test to migrate, not a defect to chase. `tests/unit` is the suite that is expected
to be green.

### One unit test depends on container state: the k-hop fast path

```text
FAILED tests/unit/test_193_bfs_nkg_fast_path.py::TestKhopFastPathE2E::test_khop_fast_path_count_matches_bfs_count
```

Named a unit test, it talks to IRIS, so it reads whatever `^NKG` the container
currently holds. It fails on a pristine tree — proven earlier by stashing every
local change and redeploying — and it passes on a container whose `^NKG` was just
rebuilt, which is why the same suite reports `8672 passed` on one run and
`8671 passed, 1 failed` on the next. The suite total is stable at 8672; only
which side of the line this case falls on moves.

It belongs in `tests/integration/`, or it needs a fixture that rebuilds `^NKG`
before asserting. Until then, treat it as the one expected unit failure and check
it against `engine.status()` rather than re-running.

### Fixed in 4.0.0: `test_embeddings_api.py` depended on the column's leftover width

```text
FAILED tests/integration/test_embeddings_api.py::test_store_embedding_and_knn
E   iris_vector_graph.exceptions.EmbeddingIdentityConflict: dimension mismatch:
    recorded dimension 384 but this writer declares 768
```

The `engine` fixture declared `embedding_dimension=768` and then called
`initialize_schema`, which can only widen a vector column while its table is **empty**.
Any earlier test that left rows in `Graph_KG.kg_NodeEmbeddings` sent the ALTER to
`needs_manual_migration` — logged `CRITICAL: ... is VECTOR(DOUBLE, 128) but the engine
is configured for 768, and the table is not empty (5 rows)` — and the write was then
refused by the identity check. The fixture's teardown deleted the rows and restored
width 128, so the failure only appeared when a *previous* file's rows survived, and it
landed on the file that read the width rather than the one that set it.

`tests/integration/conftest.py` now calls `_clear_embedding_tables` **before**
`initialize_schema` as well as after. It empties the three `VECTOR_TABLE_NAMES` plus
every table named in `Graph_KG.embedding_registry`, so 4.0.0's routed tables are
covered too.

Two notes for anyone reproducing the old failure:

- Pollution has to be written at the width the column **actually holds**. An insert
  at some other width is rejected — `<Field 'Graph_KG.kg_NodeEmbeddings.emb' (value
'…@$vector') failed validation>` — nothing lands, and the ALTER is never blocked.
- In 4.0.0 `store_embedding` always writes a routed table, so
  `Graph_KG.kg_NodeEmbeddings` only holds rows written before the migration, or rows
  inserted by raw SQL.

### ~~`conftest.py` cannot tell a stopped container from a missing one~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-013). `tests/conftest.py` now refuses to hand back a
connection until two separate things are true: `container_state_is_running` (`:15`, read
from `docker inspect -f {{.State.Status}}`) says the container is `running`, and
`container_hostname_matches` (`:46`) says the instance on the other end of the connection
reports the hostname Docker gave *that* container. The second check is the one that
matters — a running container proves nothing about which instance answered on a port.
Both fail closed: an unreadable state, an unreadable hostname, or a hostname shorter than
the twelve characters Docker derives from the container ID is a failure, not a pass.
`probe_instance_hostname` (`:99`) asks over the Native API, because on
`irishealth:2026.3.0AI.113.0` every SQL route to the instance hostname either fails
`SQLCODE -12` or hangs past 120 seconds.

The original defect, for the record:

`tests/conftest.py:56` did `IRISContainer.attach("ivg-iris-enterprise")`, and
`attach` **succeeds on a container that is `Exited`** — it resolves the name, not a
live instance. So the `pytest.fail` guard at `:76` never fires, and the connection
chain behind it (OrbStack DNS → container IP → `localhost:$IVG_PORT` →
iris_devtester's default) keeps walking until something answers. On 2026-09-18 that
was `irispython-dx-iris` at `localhost:1972`: a whole suite ran against another
project's instance, reported `547 passed`, and the only signal was two
`<CLASS DOES NOT EXIST> Graph.KG.Traversal` failures in `test_engine_status.py`.

The passes were the dangerous part — a green run against the wrong instance is not a
measurement. Any gate number recorded before 4.0.0 carries that caveat: it was measured
by a harness that could not say which instance it measured. Treat a historical run that
reported unexplained `CLASS DOES NOT EXIST` failures as void rather than partial.

### `tests/python` had no fixtures, so `iris.cls` handed back a `MagicMock` (fixed in 4.0.0)

`tests/python/` shipped without a `conftest.py`, and two different things went wrong
for want of one.

`tests/python/test_python_operators.py` requested the `engine` fixture, which is
defined in `tests/integration/conftest.py` and therefore invisible from a sibling
directory. All six of its tests ended in `fixture 'engine' not found` — never a
failure, so the file read as "not currently exercised" rather than broken. (Its
fixture body also referenced a bare `iris_connection` that was not one of its
parameters, so it would have raised `NameError` even with `engine` in scope.)

The worse one: the installed `iris-embedded-python-wrapper` returns a
**`unittest.mock.MagicMock`** from `iris.cls(name)` when neither embedded Python nor
a bound Native API handle is available
(`_iris_ep/_runtime_facade.py:582-583`, mirrored at `:82-83` for module
`__getattr__`), after one `_logger.warning("No Embedded Python or Native API
connection available.")`. `test_pyops_vector_conversion.py` guarded itself with
`hasattr(iris, "cls")`, which **cannot** detect this — the attribute always exists —
so twelve tests called ObjectScript class methods, got a mock, and asserted against
it: `json.loads(<MagicMock>)`, `isinstance(<MagicMock>, int)`, `DID NOT RAISE`,
`'>' not supported between instances of 'MagicMock' and 'int'`. This is the same
defect class as `mock.patch(..., create=True)` fabricating an attribute that the
product does not have.

The honest guard is a probe, not an attribute check:

```python
probe = iris.cls("%SYSTEM.Version")
if isinstance(probe, MagicMock):
    pytest.skip("iris.cls has no runtime bound — it would return a MagicMock")
```

`tests/python/conftest.py` now defines `engine`, plus a session-scoped
`native_iris_runtime` that binds the test container
(`iris.connect(...)` → `iris.runtime.configure(native_connection=handle)`) so
`iris.cls(...)` returns a real `iris_utils._iris_native_proxy.NativeClassProxy`, and
`iris.runtime.reset()` on teardown. The directory went from 13 failed / 6 errors to
**71 passed, 33 skipped**.

What the Native API still cannot do is construct an OREF: over it
`iris.cls('%DynamicArray')._New()` marshals to a plain Python `list` with no `_Push`
or `_Size`, and `Graph.KG.PyOps.vectorToJson` refuses anything without `_Size`
("vector required"). Nine of the seventeen pyops tests are therefore genuinely
embedded-Python-only and skip saying so.

### The quickstart claims host port 1972, which belongs to `opsreview-iris`

`docker-compose.yml:14-16` starts `container_name: iris_vector_graph` and publishes
`"1972:1972"`. Host port 1972 is reserved for `opsreview-iris` by the workspace
container-exclusivity rule, so the published quickstart and `opsreview` cannot both
run: whichever starts second fails to bind, and a caller that pointed at
`localhost:1972` expecting one of them silently reaches the other.

The **name** half of this conflict is settled and is not a defect. Constitution
v1.4.0 gives the two containers two jobs: `tests/conftest.py` is authoritative for
the **test** container (`ivg-iris-enterprise` on 31972) and `docker-compose.yml` is
authoritative for the **quickstart** container that `README.md` and
`docs/setup/QUICKSTART.md` publish. A fixture, spec artifact or CI step must still
never read the compose name — that container is Community Edition
(`MaxServerConn=1`) and cannot serve a test run that opens several connections.
Gate 6 in `tests/unit/test_spec_hygiene_gates.py` enforces the one remaining
default.

What is left is the port. Moving the quickstart off 1972 changes a number printed in
user-facing install documentation, so it is a documentation change as much as a
compose change and is deliberately not bundled into spec 227. Until then, stop
`opsreview-iris` before `docker compose up`, or remap the port in compose. Note that
`IVG_PORT` is **not** an override for the test fixture — see the next entry.

### `IVG_PORT` is inert on the two paths the fixture actually takes

`tests/conftest.py`'s `iris_connection` tries four routes in order, and only the third
reads `IVG_PORT`:

| Order | Route                                   | Port used       |
| ----- | --------------------------------------- | --------------- |
| 1     | OrbStack DNS `{container}.orb.local`    | hard-wired 1972 |
| 2     | container IP from `docker inspect`      | hard-wired 1972 |
| 3     | `localhost` socat proxy                 | `IVG_PORT`      |
| 4     | `iris_devtester` `IRISContainer.attach` | its own         |

On an OrbStack host route 1 always answers, so the documented invocation
`IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest` selects its instance by
**container name** alone; the port is decoration. Measured 2026-09-22: a run declared
`IVG_TEST_CONTAINER=ivg-arno-bench-iris IVG_PORT=31972` and the log reported
`Connected to ivg-arno-bench-iris via OrbStack DNS ivg-arno-bench-iris.orb.local
(192.168.138.22):1972` — the bench container, not the enterprise one on 31972.

This is not a scope leak: the identity assertion at `tests/conftest.py:413-430` still
proved which instance answered, and it answered as the container it was told to. The
consequence is narrower and is about what a reader can conclude from an invocation.
Two of them:

- A port in the command line does not pin the target. Changing `IVG_PORT` while leaving
  `IVG_TEST_CONTAINER` alone changes nothing on an OrbStack host, so a run "moved" to
  another port by that variable did not move.
- SC-009's negative proof cannot be built by repointing the port. Because the name both
  resolves the connection and supplies the expected hostname, the two sides of
  `container_hostname_matches` cannot be made to disagree from the command line — the
  assertion fires only when something else occupies the name's address (an SSH tunnel,
  or a collision on the socat route). Its coverage is therefore the thirteen impostor
  cases in `tests/unit/test_230_container_identity.py`, which drive the comparison
  directly, plus the fail-closed behaviour of a foreign container: pointing the suite at
  `careconnect-ivg-iris` errors every test at connect
  (`<COMMUNICATION ERROR> … Unable to allocate a license`, Community Edition
  `MaxServerConn=1`), which is a refusal, not a silent pass.

### `irispython-dx-iris` holds a stale partial IVG deployment

The instance that answered above carries 22 of the 56 `Graph.KG.*` classes and a
`Graph_KG.nodes` table — enough to satisfy a connection and most schema reads, not
enough to run the suite. Its presence is why the fall-through above stays quiet
instead of erroring on the first query. It is not this project's container; do not
deploy to it and do not clean it up from here.

### ~~`_detect_arno`'s smoke probe disables a healthy Arno~~ (fixed in 4.0.0)

`stores/iris_sql_store.py:211` used to smoke the callout once before trusting it:

```python
iris_obj.classMethodValue("Graph.KG.NKGAccel", "BFSJson", "__ivg_arno_probe__", "[]", 1, 1)
```

`__ivg_arno_probe__` is a node that deliberately does not exist, and
`NKGAccelTraversal.cls:BFSJson` cannot return an empty result: the Rust callout
hands back no chunks, the reassembly loop at `:371` builds `json = ""`, and
`%DynamicArray.%FromJSON("")` throws `<THROW> *%Exception.General Parsing error 3
Line 1 Offset 1`. The probe treats any exception as "callout not runnable in this
process" and sets `_arno_available = False`, so Arno is disabled in every process
whose graph does not happen to contain that node — which is every process.

Verified over TCP against `ivg-iris-enterprise` with the callout loaded
(`rust_callout=true`, `rust_algorithms=["pagerank","wcc","cdlp","bfs"]`):

```text
BFSJson("__pa", ...)                -> SORTED:197013_bfs     # real node, works
BFSJson("__ivg_arno_probe__", ...)  -> Parsing error 3        # missing node, throws
```

This is what the 9 remaining Arno integration failures are —
`test_rust_algorithms_nonempty`, `test_pagerank_uses_rust_path`,
`TestPPRDispatch::test_ppr_arno_*`, `TestArnoDetection::test_detect_arno_via_store`,
`test_arno_capabilities_cached` and two more. Not container state: the container
reports Arno loaded and a real seed traverses through it.

The probe now seeds from `Graph.KG.NKGAccel.GetFirstNKGNode`, which is a node
`^NKG` actually holds; an empty `^NKG` is reported as empty rather than as a broken
library. Four unit tests were written first
(`tests/unit/test_arno_probe_seed.py`).

### ~~The Rust callout and the ObjectScript class disagree on what a scored node is called~~ (fixed in 4.0.0)

Enabling a working accelerator for the first time exposed every Python reader that
had only ever been exercised against the ObjectScript spelling. Two producers
answer the same question with different keys:

```text
Graph.KG.PageRank.RunJson / PageRankGlobalJson  ->  [{"id":"fix_0","score":0.15}, ...]
Graph.KG.ArnoAccel.PPRJson  (Rust callout)      ->  [{"node":"fix_0","score":0.15}, ...]
```

`execute_ppr` and `execute_pagerank` read `r.get("id", "")`, so every row on the
Arno path came back with a **blank** id and a correct score. Nothing raised and
`IVGResult.error` stayed `None`. Measured on `ivg-iris-enterprise`:

```python
store.execute_ppr(["fix_0"], 0.85, 20).rows
# [['', 0.15000000000000002], ['', 0.1275], ['', 0.108375], ...]
```

`kg_PERSONALIZED_PAGERANK` builds `{r[0]: r[1]}` from those rows and collapses them
to a single entry keyed `''` (`assert 'alg_0' in {'': 0.0549}`), and
`kg_PPR_GUIDED_SUBGRAPH` passes that `''` on as a seed to
`Graph.KG.Subgraph.SubgraphJson`, which throws — nine integration failures from one
key name. Both readers go through `_scored_node_rows` now, which accepts either key
and drops a row that names neither rather than manufacturing a blank id.

Three sibling mismatches are still live in the code and only latent because of what
`_arno_capabilities['algorithms']` happens to list:

| Path           | Arno (`ArnoAccel`)                               | ObjectScript                              |
| -------------- | ------------------------------------------------ | ----------------------------------------- |
| PPR / PageRank | `[{"node","score"}]`                             | `[{"id","score"}]`                        |
| WCC            | `{"components":1,"largest":[{"root","size"}]}`   | `{node: root}` map                        |
| CDLP           | `{"communities":2,"largest":[{"label","size"}]}` | `{node: label}` map                       |
| Subgraph       | `{"nodes":[…],"edges":[{"src","dst","type"}]}`   | `{"s","p","o"}` + `properties` + `labels` |

Only `ppr` routes to Arno today: the dispatch gates on `algorithms`, which is
`['khop','ppr','random_walk','export_adjacency']`, while `rust_algorithms`
separately lists `pagerank`, `wcc`, `cdlp` and `bfs`. The day `algorithms` gains
those names, `execute_wcc`, `execute_cdlp` and `execute_subgraph` break the same
way PPR did — silently, with plausible-looking output.

### ~~`User.PageRankEmbedded` does not compile~~ (fixed in 4.0.0)

`$system.OBJ.LoadDir` over `iris_src/src` used to report exactly one error:

```text
ERROR #5559: The class definition for class 'User.PageRankEmbedded' could not be parsed correctly
```

The cause was a method named with a leading underscore, which the class compiler
will not parse. It is `ComputePageRankCore` now, the class compiles, and
`scripts/deploy_objectscript.py:171` can call
`##class(PageRankEmbedded).ComputePageRank(...)` as it always claimed to.

### `ruff check .` reports 2048 findings

`pyproject.toml` has no `[tool.ruff]` section, so the run uses ruff's defaults:
690 `F401` unused-import, 394 `F405` star-import usage, 299 `F841` unused-variable,
196 `F541`, 186 `E702`, and 41 `F821` undefined-name. §5 of the pre-release
checklist asks for zero and has never been met. The 41 `F821` are worth triaging
first — an undefined name is a real defect wherever it is not a star-import artifact.
Pin a rule set before treating this gate as meaningful.

**Workaround:** the default rule set is not the project's, so `ruff check .`'s total is
not a signal — do not read it as 2048 defects, and do not gate anything on it. Ask for
the rules that catch real defects instead:

```bash
ruff check --select F821,F811,E711,E712 .
```

Nothing in CI runs `ruff check .`, so a red total does not block anything today either.

### The container drifts from the tree, and the deploy script used to hide it

Three separate pieces of drift were found in `ivg-iris-enterprise` while measuring
the gates, each of which read as a code regression until the container was
inspected:

| Symptom                                                                                           | Actual cause                                                                                                                                            |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 53 fixture errors, `Eraser::EraseAll` → `ERROR #5001: erase failed on rdf_edges with SQLCODE -30` | `Graph_KG.rdf_edges` did not exist in the USER namespace — class definition present, never projected. `nodes`, `rdf_labels`, `rdf_props` were all there |
| 12 Arno failures, `rust_algorithms == []`                                                         | `libarno_callout.so` was not loaded into the running instance                                                                                           |
| 68 `Graph.KG.*` classes in the container against 56 `.cls` files on disk                          | stale `TestEdge`, `BenchSeeder` and friends left from earlier specs                                                                                     |

Repair both of the first two before believing any Arno or erasure result:

```bash
bash scripts/enterprise-container.sh tcp-deploy     # write + compile each .cls over TCP
bash scripts/enterprise-container.sh tcp-load-arno  # stream the .so, then ArnoAccel::Load
```

`tcp-deploy` reported success on a class that failed to compile. An IRIS error
`%Status` is a non-empty string starting with `"0 "` — truthy in Python — so the
`if not result` test never fired. It now asks IRIS
(`%SYSTEM.Status::IsOK` / `GetOneErrorText`) and prints the error text, which is
how `User.PageRankEmbedded`'s `#5559` (now fixed, above) finally surfaced. Any other script
that checks a `%Status` for Python truth has the same defect.

### Four size/complexity gates from spec 186 have been red since spec 187

`tests/regression/test_complexity.py` and `test_module_size.py` were written as
spec 186 phase gates, flipped green at `681f6d3` ("spec 187 T025/T028/T029: flip
size guard green"), and have been failing ever since. They fail at the `v3.2.0`
tag as well as in the working tree, so this is not a 227 regression — but it does
mean four budgets are documented in the repo and enforced nowhere:

| Gate                                   | Budget | `v3.2.0` | 4.0.0 |
| -------------------------------------- | ------ | -------- | ----- |
| `cypher/translator.py` lines           | 4300   | 15924    | 16522 |
| `stores/iris_sql_store.py` lines       | 2000   | 2500     | 2915  |
| `_engine/schema.py` lines              | 2000   | 1495     | 2479  |
| `Graph/KG/TemporalIndex.cls` lines     | 800    | 1088     | 1088  |
| `translator.py` functions over `cc=25` | 0      | 45       | 45    |

Two readings matter here. `_engine/schema.py` is the one line 227 actually
crossed — it was inside budget at `v3.2.0` and the routing, quarantine and
recall code took it to 2340 — so that entry is a genuine new offender rather
than inherited debt. And `test_complexity.py`'s `ALLOWLIST` is `{}` against 45
offenders topping out at `cc=331` (`_build_temporal_from_variable_map`), which
is spec 186 Phase B never having landed, not drift.

Deciding these is a refactor spec, not a release task: splitting the translator
risks circular imports for no behavioural gain, which is the reason its file-size
budget was allowlisted in the first place. What must not happen is re-baselining
the budgets to whatever the tree currently measures — that converts a stated
design limit into a running tally and the gate stops meaning anything.

**Workaround:** a run over `tests/` fails four tests on a healthy tree, so read them
as four and no more:

```bash
SKIP_IRIS_TESTS=true .venv/bin/python -m pytest -q \
  tests/regression/test_complexity.py tests/regression/test_module_size.py
# 4 failed  — the two cc gates and the two size gates, with the numbers above
```

Any fifth failure, or a different set of four, is new. To measure anything else
without the noise, run `tests/unit` rather than `tests/` — that is the path every
gate number in the release notes was measured over, which is why those runs report
zero failures while this one reports four.

### Two fixtures reshape shared state, and the failure lands on another test

Both of these were chased as product defects during the 4.0.0 gate before the fixture
turned out to be the cause.

**A fixture that passes `embedding_dimension` and calls `initialize_schema()` reshapes the
column for the whole session.** `tests/e2e/test_stress_ingest.py` builds its engine as
`IRISGraphEngine(iris_connection, embedding_dimension=4)` and initializes, which alters
`Graph_KG.kg_NodeEmbeddings.emb` to `VECTOR(DOUBLE, 4)` in the live namespace. Every later
test in the run that expects the install's real width then reports

```text
Graph_KG.kg_NodeEmbeddings.emb is VECTOR(DOUBLE, 4) but the engine is configured for 768
```

and the file that fails is not the file that caused it. Run a suspect file alone before
believing a width complaint: a failure that disappears on its own is this, not a defect.

**A fixture that inserts nodes with raw SQL has to write the `id` property.** `.id` in
Cypher resolves through `rdf_props`, so a fixture that writes `Graph_KG.nodes` and
`rdf_labels` but no `id` row leaves every `WHERE n.id = …` and `RETURN n.id` matching
nothing — with the rows present and `n.node_id` answering correctly. `create_node` writes
the property; raw SQL does not.

### Operational facts the 4.0.0 gate established

- **`libarno_callout.so` does not survive a container rebuild.** The image does not carry it;
  it is streamed to `/usr/irissys/mgr/` by `tcp-load-arno`. A rebuilt container therefore
  comes back with no callout and `rust_algorithms == []`, which reads as an Arno regression.
  Re-run `bash scripts/enterprise-container.sh tcp-load-arno` after any rebuild.
- **Arno BFS has no fallback for an adjacency export too large for the callout.**
  `Graph.KG.NKGAccelTraversal.BFSJson` builds the whole adjacency into one string
  (`ExportAdjacencyWithPreds`) and passes it to `$ZF(-5)`. The three fallbacks around that
  call cover a library that will not load and an empty or `DEBUG:` reply; a payload over the
  callout's string limit is not one of them, so a large graph raises `<MAX $ZF STRING>` or
  `<OUT OF $ZF HEAP SPACE>BFSJson+23` out of `NKGAccelTraversal.cls:360-363` instead of
  falling back to `BFSFastJsonSorted`.
- **`tests/tck` and `tests/unit/tck` collide as packages.** Both carry an `__init__.py` and
  the same leaf name, so a single pytest invocation over both trees fails to import one of
  them. Every gate number in the release notes was measured with `--ignore=tests/tck`; the
  TCK harness runs on its own through `behave`.
- **`Graph.KG.EdgeScan.BulkIngestEdges` no longer exists.** The bulk entry points are
  `BulkIngestEdgesSQL`, `BulkIngestEdgesFile` and `BulkIngestNodesSQL`. A caller still
  naming the old method gets a `<METHOD DOES NOT EXIST>`, not a slow path.
- **~~`test_multiple_connections_same_db` fails with `<COMMUNICATION LINK ERROR>` on a healthy
  tree~~ — retracted, it was two defects (both fixed in 4.0.0).** This entry read the failure
  as a container refusing a concurrent connection, i.e. environmental. It was not. The
  `<COMMUNICATION LINK ERROR>` cluster in the gate had two causes, and both were in this
  tree:
  1. Test modules connecting to a **host port nothing publishes.** `test_stress_setup.py`,
     `test_stress_api.py` and `test_untested_methods.py` read `IRIS_PORT` — a name nothing
     here exports — defaulting to `1972`, and `test_large_output_chunked.py` /
     `test_lazy_node_resolution.py` read `IVG_TEST_PORT` defaulting to `2972`, which belongs
     to another project. Reading an unexported variable makes the default the only value
     that ever applies. A refused connection to an unpublished port reports as
     `<COMMUNICATION LINK ERROR> Failed to connect to server`, which reads like a driver or
     licensing fault. All five now read `IVG_PORT` (default `31972`), and
     `tests/unit/test_230_test_port_defaults.py` fails the build on a default this repo
     does not own.
  2. **`get_schema_visualization()` closing the caller's connection** — see the entry below.
     Once a predicate had more edges than one fetch buffer holds, this method killed the
     shared connection and every test that touched it afterwards raised
     `<COMMUNICATION LINK ERROR> Connection closed`.
     The `SQLUser.*` spelling in those files was also suspected and is fine:
     `SQLUser.rdf_edges` is a real view over `Graph_KG.rdf_edges` with identical contents,
     listed in `INFORMATION_SCHEMA.VIEWS`. The earlier
     `Table 'SQLUSER.RDF_EDGES' not found` came from querying the wrong container.
- **`tests/benchmarks/*` still default to `IRIS_PORT` 1972/4972.** Unlike the test modules
  above these are hand-run scripts, they take the port from an argument or environment in
  practice, and the documented variable for them is `IRIS_PORT`. They are not covered by the
  port guard, which scans `test_*.py` and `conftest.py` only. Export `IRIS_PORT=31972`
  before running one.
- **~~`get_schema_visualization()` occasionally hits "Message out of order"~~ — fixed in
  4.0.0.** The relationship-endpoint lookup ran `SELECT s, o_id FROM Graph_KG.rdf_edges WHERE
p = ?` uncapped, read one row with `fetchone()`, and then issued the next `execute()` on
  the same cursor with the rest of the result set still pending. That desynchronizes the IRIS
  wire protocol (`<COMMUNICATION ERROR> Message out of order; Invalid Message Sequence
Number: expected: 293 got: 291`) and IRIS closes the connection — so the **caller** lost a
  connection it still owned, and everything after it failed with
  `<COMMUNICATION LINK ERROR> Connection closed`, including work unrelated to this method. It
  hid on small fixtures, where the whole result set arrives in one buffer and leaves nothing
  pending. Fixed with `TOP 1`; guarded by `tests/unit/test_230_schema_visualization_row_cap.py`
  (every `SELECT` read with `fetchone()` must cap itself) and
  `tests/e2e/test_230_schema_visualization_connection.py` (the connection still answers
  afterwards). A `pytest.skip` for "Message out of order" had been carried in
  `test_untested_methods.py`; the symptom was known and read as transient. It was neither
  transient nor a driver fault.
- **~~`initialize_schema()` reports a clean setup over a table that rejects every
  write~~ — fixed in 4.0.0.** `_migrate_vector_dimensions` builds a
  `needs_manual_migration` list for each vector table whose declared width disagrees with
  the configured one while the table holds rows — the case the engine deliberately will not
  fix, because widening cannot invent the missing dimensions. `initialize_schema` called it
  and threw the return value away, so the only report was a `logger.error` and the returned
  status still read as success while every configured-width write to that table was rejected
  with SQLCODE -104. The status now carries `needs_manual_migration` and a matching
  `warnings` entry.
- **The shared test namespace's `kg_NodeEmbeddings` can be left at the wrong width.** One
  `emb VECTOR(DOUBLE, n)` declaration serves the whole namespace, and `initialize_schema()`
  alters it to the calling engine's width whenever the table is empty. Live tests
  legitimately build engines at 4, 8, 128, 384 and 1536, so any of them can narrow it; once
  rows exist at the narrow width the engine refuses to widen and every later run logs
  `CRITICAL: ... is VECTOR(DOUBLE, 4) but the engine is configured for 768`. The message
  reads as a complaint about the current run's data rather than as damage the previous run
  left behind, which is why it survived several sessions. `tests/conftest.py` now restores
  the width at session end and logs having done so, and
  `tests/e2e/test_230_embedding_width_isolation.py` asserts the invariant during the run.
- **~~`OPERATIONS.md` promised a `RuntimeWarning` for a stale `^NKG`~~ — corrected in
  4.0.0.** No code path emits one. The two var-length Cypher routes in `_engine/query.py`
  **raise `IndexNotSyncedError`** before any traversal runs, which is the stronger contract:
  an unsynced bulk load cannot produce a quietly incomplete path result. The engine's actual
  `RuntimeWarning`s are about the Arno accelerator not being loaded and about
  `degree_centrality(top_k=0)` on a large graph. Two tests asserted the documented shape
  rather than the real one: one expected a `RuntimeWarning` out of `bulk_ingest_edges` (which
  uses `logger.warning`, and emits nothing for staleness), and one asserted `_nkg_dirty is
True` after a default `bulk_ingest_edges`, which cannot hold — the default `auto_sync=True`
  calls `sync()`, which clears the flag by design.
- **`engine.is_ready` is a method, not a property.** Read as an attribute it yields a bound
  method, which is truthy, so `if engine.is_ready:` passes even on a dead connection. Three
  tests had it as a property (two asserting `is False` / `is True`, one hedging with
  `callable()` behind an `AttributeError` skip that could only ever have hidden its removal).
  It appears nowhere in `docs/`, `README.md` or `api/`, so no published example is affected.
- **An empty-string `^KG` subscript is illegal.** `^KG("out", "", node)` raises — which is
  why the default graph is written as the integer `0` in every `^KG` tree rather than as the
  `''` the SQL tables use.
- **`%Dictionary.CompiledMethod.Implementation` reads as `None` over SQL.** It is a stream,
  and a DB-API `SELECT` hands back nothing useful for it. `FormalSpec` is a string and is
  the reliable way to read a deployed method's signature from Python.

---

## Graph scope (verified 2026-09-16)

### ~~The default graph is falsy, so `''` reads as "every graph"~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-001). ADR-0003 spells the default graph `''`, and the
translator tested `context.graph_context` for *truth*, so the one graph whose name is
falsy took the same path as a query naming no graph: no predicate on any table. The
clause a caller writes to narrow a query was the one value that widened it.

All three rows are closed:

| Location                    | Original effect                                                                | Fix                                                                                                                                                  |
| --------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cypher/translator.py:2542` | `USE GRAPH ''` appended no `graph_id` predicate — the query spanned all graphs | `is not None` at seven sites: `add_dml`, `build_dml_subquery`, the read-scoping gate, two vector CTEs, the vector-distance site, the EdgeScan branch |
| `cypher/translator.py:6274` | the same value took the EdgeScan fast path, which merged graphs                | the fast path now picks a *method*: `MatchEdgesAllGraphs` for `None`, `MatchEdges` for `''`                                                          |
| `_engine/schema.py:745`     | `retract_inference(graph="")` deleted inferred edges in every graph            | `if graph is None or graph == "":` → `AND COALESCE(graph_id, '') = ''` (now `_engine/schema.py:2353`)                                                |

`_child_graph_sql` (`translator.py:162`) is deliberately *not* part of this: its falsy
branch omits the column and guards with `COALESCE(graph_id, '') = ''`, which is already
correct default-graph behaviour for both `''` and `None`. Switching it to `is not None`
would emit a bare `graph_id = ''` that misses rows written before the column acquired
its `DEFAULT ''`.

**`None` still means the whole namespace.** No `USE GRAPH` clause is
`graph_context is None`, and that is the pre-214 behaviour every existing caller
compiled against. Narrowing it to the default graph is a breaking change to every query
that never knew about graphs, and it belongs to a deprecation with its own release note.
The boundary is asserted by `test_no_use_graph_clause_still_spans_the_namespace`
(`tests/unit/test_230_default_graph_predicate.py`) so it cannot drift by accident — see
the fan-out entry below for what that default now costs.

### ~~`Graph.KG.EdgeScan.MatchEdges` overloads `0`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-002), by option **B**. `EdgeScan.cls:30` took
`graph = 0` and `:39` read `0` as "merged view, all graphs", while ADR-0001 makes
integer `0` the default graph's own key in `^KG("out", ...)` — so a caller asking for
the default graph and a caller asking for everything were indistinguishable.

`MatchEdges` is scoped-only now: `0` is the default graph and nothing else. The merged
all-graphs view is a separate method, `MatchEdgesAllGraphs`, so every cross-graph read
is greppable by name rather than hidden in an argument value. Option A was rejected
because a sentinel keeps the two intents in one call site, which is what made the
original ambiguous. The same split applies to the adjacency writers:
`DeleteAdjacencyAllGraphs` is the only spelling that reaches every graph.

One consequence worth stating, because it was a live divergence for the length of a
single commit: the Cypher translator's `^KG` fast path passes `MatchEdges(src, pred, 0)`,
where the third argument is `shard` and `graph` defaults to `0`. Once `0` meant the
default graph only, a query with no `USE GRAPH` clause taking the fast path answered
*narrower* than the same query on the SQL path. The branch now picks the method from the
graph, not the argument (`translator.py:6816`).

### A query with no `USE GRAPH` clause fans out across graphs, and the row count is wrong

New in 4.0.0, and a consequence of spec 227 rather than of spec 230: once
`Graph_KG.nodes` is keyed `(graph_id, node_id)`, two graphs may hold a row for the same
node ID — and a query that carries no `graph_id` predicate joins them all against each
other. `MATCH (n:Thing) RETURN n.node_id` with no `USE GRAPH` clause joins
`nodes ⋈ rdf_labels ON l.s = n.node_id`, which pairs *every* graph's node row against
*every* graph's label row for the same ID. Measured on `ivg-iris-enterprise` with one
node held by two graphs: **four rows, not two**
(`tests/e2e/test_230_scope_isolation.py::test_use_graph_with_the_default_graph_reads_one_graph`).

That is a wrong answer, not merely a wide one: the duplicates are a cartesian product of
graphs, so the count means nothing and a caller aggregating over it double-counts. Before
spec 227 the shape was impossible — `UNIQUE (node_id)` meant a node ID existed in exactly
one graph, so the unscoped join had at most one row per ID to pair.

It is documented rather than fixed because the fix is a breaking change to a different
thing. Making no clause mean the default graph would narrow every pre-214 caller's query,
and adding a `DISTINCT` would paper over the product while leaving every aggregate that
reads `nodes` unscoped still wrong. Both belong to a deprecation of the no-clause default,
with its own release note.

**Workaround:** name the graph. `USE GRAPH ''` is the default graph and scopes correctly
as of 4.0.0 (entry above); a named graph always did. An unscoped read is only safe in a
namespace with exactly one graph.

### `USE GRAPH` predicates are inlined against one spelling of `rdf_edges`

**Still open in 4.0.0.** `translator.py:3048` and `:3059` append
`f"{ea}.graph_id = '{safe_graph}'"` built from alias-prefix guesses
(`startswith("e")`, `not startswith("n")`, a `break` after the first match). It works
for the shapes under test and is not derived from the query's own edge set. Worth
reworking, and worth doing deliberately — it decides which rows a tenant-scoped query
sees.

Spec 230 FR-001 changed *when* this block runs (`graph_context is not None` rather than
truthiness, so `USE GRAPH ''` reaches it at all) and left *how* it picks aliases alone.
Two things now constrain any rework, both learned the hard way: the guesses must exclude
the stage names (`_defined_stage_names`), because a fused `Retrieve` stage projects a
node ID and a score and no `graph_id` — naming it out here makes IRIS refuse the whole
statement at Prepare with `SQLCODE -29 Field 'RETRIEVE.GRAPH_ID' not found`, which the
driver's error path turns into zero rows and a scoped retrieval that reads as an empty
graph. And a predicate on a LEFT JOIN belongs in the `ON` clause, not in `WHERE`: in
`WHERE` it is false for exactly the null row the outer join exists to keep, which turns
the join inner and shows up as missing data rather than as a wrong predicate.

Workaround until then: a query whose scope actually matters should name its graph and be
checked against `tests/unit/test_230_default_graph_predicate.py`'s shapes (anchor table,
label and property joins, edge traversal, vector subquery, `SET`, `DELETE`), which are
the shapes the alias guesses are known to get right.

### Nodes cannot sit in the default graph while their edge sits in a named graph

The original reading of this — `Graph_KG.nodes` carrying `UNIQUE (node_id)`, so one
node reachable from several named graphs had no representation — is closed by 4.0.0:
the constraint is `uq_nodes_graph_node UNIQUE (graph_id, node_id)` and the primary
key is `(node_id, graph_id)`, so the same node ID can exist once per graph.

The heading is still accurate for a different reason, and it is now enforced rather
than latent. `rdf_edges` declares `fk_edges_source (graph_id, s)` and
`fk_edges_dest (graph_id, o_id)` against `nodes (graph_id, node_id)`, so an edge in
graph `g` requires **its endpoints to be registered in `g`**. A node row in the
default graph does not satisfy an edge in a named graph: the insert is refused with
`SQLCODE -121`. Writers that create an edge without first registering its endpoints
in the same graph fail now where they used to write a dangling row; the audit of the
remaining `rdf_edges` insert sites is tracked with the writers section below.

**Workaround:** register both endpoints in the edge's own graph before writing the
edge, and pass the same `graph` to all three calls:

```python
engine.create_node(src, labels=["Thing"], graph=g)
engine.create_node(dst, labels=["Thing"], graph=g)
engine.create_edge(src, "REL", dst, graph=g)
```

A node in the default graph is a different row from the same node in `g`, so
sharing an entity across graphs means one `create_node` per graph, not one for all
of them. Cypher's `CREATE (a)-[:R]->(b)` under `USE GRAPH '<g>'` already does this
— it writes the endpoints into `g` in the same statement list.

---

## ~~`graph_id` writers and the scan that is supposed to catch them~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-003 and FR-004). `tests/unit/test_graph_id_tightening.py`
exists so that no writer can insert an edge without naming `graph_id`, and it was reading
a third of the writers: it resolved one spelling of the interpolated table name, found
thirteen INSERTs in the `_engine/*` mixins, found `graph_id` in all thirteen, and reported
that every writer names the column. The two modules that had actually omitted it were
never read.

Four separate blind spots, each closed and each now asserted:

| Blind spot                                                                                                                                  | Fix                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_python_text` resolved `{self._t(` only, so `bulk_loader.py`'s `{self._table(` and `cypher/translator.py`'s bare `{_table(` were invisible | all three accessors resolved                                                                                                                                                                                                                         |
| the regex demanded a literal adjacent `INSERT INTO`, and the bulk loader interpolates `%NOINDEX %NOCHECK` between them                      | `INSERT[^()]{0,40}?INTO`, bounded so it cannot swallow a second statement                                                                                                                                                                            |
| it demanded `rdf_edges\s*\(`, and the same writer breaks the statement across two adjacent string literals                                  | `rdf_edges[\s"']*\(`                                                                                                                                                                                                                                 |
| nothing asserted the scan's own coverage                                                                                                    | `MUST_BE_SCANNED = ("bulk_loader.py", "translator.py")` and `test_the_scan_reaches_every_module_that_writes_an_edge` — named rather than counted, because a scan that stops covering a module looks identical to a module that stopped writing edges |

One finding turned out to be a false positive and is skipped deliberately: a column list
that is a single `{…}` interpolation names no column of its own. `MERGE` re-issues the
`rdf_edges` INSERT this translation already added as `SELECT ... WHERE NOT EXISTS`
(`translator.py:4958-4967`), copying the column list across verbatim, so reading it as an
omission flags the rewrite for a column the original is asserted to name (`_REEMITTED`).

The writers themselves: the three `rdf_edges` INSERTs in `cypher/translator.py` and
`bulk_loader.py`'s `(s, p, o_id, qualifiers)` now name `graph_id` unconditionally and bind
`_graph_of(context)`, which returns `''` for the default graph rather than `None` — the
column is `NOT NULL DEFAULT ''` since spec 227, so a bound NULL is rejected outright and
an omitted column is the other spelling of the default graph this whole section exists to
stop. One spelling, one branch.

### `graph_id` predicates: one reconciled, one still asymmetric

- **Fixed in 4.0.0:** the `rdf_edges_with_graph` bulk template deduped with
  `(graph_id = ? OR (graph_id IS NULL AND ? IS NULL))`, treating `NULL` and `''` as
  different graphs. It is `COALESCE(graph_id, '') = COALESCE(?, '')` now
  (`schema.py:923`), the spelling every other reader uses.
- **Still asymmetric, now harmless.** `add_graph_id_column` (`schema.py:447`) still adds
  `graph_id VARCHAR(256) %EXACT NULL` to `rdf_edges` while a fresh install declares
  `NOT NULL DEFAULT ''`, and relies on `tighten_graph_id_column` to repair it afterwards.
  The `rdf_labels`/`rdf_props` upgrade path no longer does: it emits `NOT NULL` and
  `SET DEFAULT ''` in the same statement batch (`schema.py:622-628`). Emitting the
  constraint up front on `rdf_edges` too would mean the two construction paths never
  disagree; until then the repair is what closes the gap, and it runs from
  `ensure_indexes` (asserted by `test_the_migration_runs_as_part_of_ensure_indexes`).

**Fixed in 4.0.0:** `nodes_with_graph` probed `WHERE node_id = ?` without
`graph_id`, so the same `node_id` in a second graph read as already present and
the insert was skipped with nothing written and no error. Spec 227 broke
`UNIQUE (node_id)`, which made that a live defect rather than a latent one, and
the guard is scoped now (`schema.py:815`). The same fix landed on
`rdf_labels_with_graph` and `rdf_props_with_graph`, which the re-key gave a
`graph_id` to.

---

## ~~`^KG` layout leftovers~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-015). `Graph/KG/TraversalBuild.cls` read
`$Order(^KG("out", gg, 0, mid, pred, o2))` — the `0` was a shard subscript spec 214
removed, one subscript more than the writer sets. Nothing lived there, so
`Build2HopExactStats`' merged fallback returned `exact = 0` for every node in every graph
rather than a count. The walk is `^KG("out", g, mid, pred, o2)` now
(`TraversalBuild.cls:307-316`), and the second hop stays inside graph `g`.

One deliberate loss to record: the scoped `deg2p_exact` path no longer uses the Arno
acceleration. `^KG("deg2p_exact_merged")` is Arno's graph-blind sketch, and a count that
spans graphs is the wrong answer to a scoped question — a slower correct count beats a
fast one that includes another graph's nodes.

### ~~`Graph.KG.Eraser` leaves the flat `^KG` subscripts behind~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-005). `^KG("out")`, `^KG("in")`, `^KG("deg")` and
`^KG("degp")` were graph-scoped and the Eraser killed them per graph. `^KG("prop")`,
`^KG("label")` and `^KG("deg2p")` were flat, with no graph subscript, and nothing erased
them at all: measured on `ivg-iris-enterprise`, after `Graph.KG.Eraser.EraseAll` returned
32 and reported the database empty, `^KG` still held `__version` and a `deg2p` subtree for
all 15 nodes of the previous test's ring.

The three, plus `^KG("deg2p_exact")`, now carry a graph subscript
(`^KG("prop", g, nodeId)`), so a per-graph erase can scope them: `EraseGraph` kills
`^KG("prop", tKey)` and its siblings at `Eraser.cls:217-221`, and `EraseAll` kills the
subtrees outright at `:370-381`. Before the re-key, killing `^KG("prop", nodeId)` for one
graph took both graphs' entries for a node ID two graphs held — which spec 227's
`UNIQUE (graph_id, node_id)` made a reachable shape rather than a hypothetical one.

Existing installations need the rebuild, not just the new code: the re-key is a storage
layout change, so the four stores are killed and rebuilt from graph-scoped SQL rows
(`BuildKG` → `Build2HopStats` → `Build2HopExactStats`). A 3.2.0 database's entries sit at
subscripts no 4.0.0 reader walks until that runs — see `docs/migration/v4.0.0.md`.

Three stores are deliberately still flat, because a graph subscript would be wrong for
them: `^KG("labelset")` is content-addressed interning (the hash *is* the identity, and
two graphs interning the same label set should share the entry),
`^KG("deg2p_exact_merged")` is Arno's cross-graph sketch by definition, and `^KG("__version")`
describes the layout itself.

### A `%NOINDEX` bulk load left rows no reader could see (fixed in 4.0.0)

`BulkLoader` inserts with `INSERT %NOINDEX %NOCHECK` — 450× faster, and no index
is maintained on the way in — so phase 5 exists to put the indices back. It
never ran: `_rebuild_indices` asked IRIS for
`SELECT %SYSTEM_SQL.BuildIndices('<class>')`, which is not a function IRIS has
(`SQLCODE -359 SQL Function (function stored procedure) not found`). The `except`
logged a warning, `rebuild_all_indices` recorded `False` in a stats dict nobody
read, and `load_networkx` returned as if the load had worked.

What that leaves is worse than a missing index. Measured with one bulk-loaded
edge (`lni_a KNOWS lni_b`) alongside 17 ring edges:

| Query                                                  | Result  |
| ------------------------------------------------------ | ------- |
| `SELECT COUNT(*) FROM Graph_KG.rdf_edges`              | 17      |
| `SELECT s FROM Graph_KG.rdf_edges WHERE s LIKE 'lni%'` | no rows |
| `SELECT s FROM Graph_KG.rdf_edges WHERE %NOINDEX …`    | `lni_a` |

The row is in the database and invisible to every index-driven reader: `COUNT(*)`,
any `WHERE` on an indexed column, the `DELETE`s `Graph.KG.Eraser` runs, the joins
`kg_KNN_VEC` and the Cypher translator emit. `Graph.KG.TraversalBuild.BuildKG`
reads with an embedded cursor and no predicate, so it *does* see the row and
writes `^KG("out", 0, "lni_a", …)`, and `BuildNKG` puts both endpoints into
`^NKG`. That is how a bulk-loaded node nobody could select kept reappearing in
`^NKG` and made the Arno WCC ring test report two components long after
`EraseAll` had reported the database empty — and no cleanup in the suite could
remove it.

`_rebuild_indices` calls `##class(<class>).%BuildIndices()` through
`schema._call_classmethod` now, treats a falsy `%Status` or an exception as a
failure, and `load_networkx` raises rather than returning stats a caller would
read as success. The same four classes returned 1 and the counts moved to 18
edges and 17 nodes with the `lni` rows selectable.

The pre-existing unit tests could not have caught it:
`tests/unit/test_bulk_loader_unit.py` and `test_bulk_loader_coverage.py` mocked
`cursor.execute` and asserted `True`, which passes whatever SQL the code sends.
`tests/unit/test_bulk_loader_index_rebuild.py` asserts the call instead.

---

## Installing the SQL routines (verified 2026-09-20)

### ~~`kg_Betweenness` never existed, because one `$SELECT` will not go through the DDL parser~~ (fixed in 4.0.0)

A `LANGUAGE OBJECTSCRIPT` body cannot contain a comma-separated colon list. Measured:

| Body                             | Result                                                                     |
| -------------------------------- | -------------------------------------------------------------------------- |
| `{ quit $SELECT(x>0:x, 1:200) }` | `<PARAMETER ERROR> Parameter Name error, First value cannot be a digit: 2` |
| `{ quit $S(x>0:x, 1:200) }`      | same                                                                       |
| `{ quit $CASE(x, 0:200, :x) }`   | same                                                                       |
| `{ quit $SELECT(x>0:x) }`        | installs, returns `5`                                                      |
| `{ quit:x>0 x  quit 200 }`       | installs, returns `5`                                                      |
| `{ if x>0 { quit x } quit 200 }` | installs, returns `5`                                                      |

So the colon is fine and the **comma inside the clause list** is not — the parser reads
`, 1:200` as a parameter assignment and objects to a name beginning with a digit.

`kg_Betweenness` shipped exactly that expression, which made it the one algorithm
function that did not exist after `initialize_schema`. Calling it returned
`SQLCODE -359 ... User defined SQL function 'GRAPH_KG.KG_BETWEENNESS' does not exist`.
Nothing reported a problem, because `_install_procedures`
(`iris_vector_graph/_engine/schema.py:457`) treats every statement except
`kg_KNN_VEC` as optional and sends the error to
`logger.debug("Optional procedure DDL skipped (non-fatal)")`. That branch exists for a
reason — `kg_TXT` and `kg_RRF_FUSE` genuinely depend on the full-text feature — but it
also swallows a syntax error in a shipped body.

The body computes its sample-size default with an `if` now, and two tests pin it:
`tests/unit/test_schema_procedures.py::TestObjectScriptBodiesTheDdlParserAccepts`
rejects a comma-separated clause list in **any** shipped body, and
`tests/integration/test_shipped_routines_exist.py` reads the routine names out of
`get_procedures_sql_list` and requires each one to appear in
`INFORMATION_SCHEMA.ROUTINES` — asserting the outcome rather than the install, which is
the only way past the debug-level `except`. `kg_KNN_VEC`'s documented pre-migration
deferral is the single allowed exception.

### `SQLUser.STR_SPLIT`'s `SQLCODE -300` is harmless duplication, not a failure

`initialize_schema` logs a failure for one UDF on every run:

```text
[SQLCODE: <-300>:<DDL not allowed on this table definition>]
[%msg: <DDL not enabled for class 'User.funcSTRSPLIT'>]
```

It is not a defect, and the function works — `SELECT SQLUser.STR_SPLIT('a,b,c', ',')`
returns `["a","b","c"]`. `STR_SPLIT` is defined **twice**: as
`iris_src/src/User.funcSTRSPLIT.cls` (a class, because `STR_SPLIT` is a reserved word
in IRIS 2026.3 and cannot be a method name — hence the `SqlName` projection), and as a
`CREATE OR REPLACE FUNCTION` in `iris_vector_graph/schema.py:1120`. The class-loaded
version compiles without `DdlAllowed`, so once it is deployed the DDL statement can
never replace it, and `-300` is IRIS saying so.

Keeping both is deliberate: a DDL-only namespace has no `User.funcSTRSPLIT.cls` and
gets the function from the DDL instead. The two bodies were compared on the live
container and agree, including the empty-string case — SQL `''` reaches ObjectScript as
`$CHAR(0)`, so **both** return `["\u0000"]` rather than `[]`. `User.funcJSONKEYS` is the
other class-defined UDF and has no DDL twin, so it never produces this message.

---

## Cypher procedure calls (verified 2026-09-20)

### ~~Four of the `ivg.*` procedures emitted SQL this build cannot run~~ (fixed in 4.0.0)

`CALL ivg.vector.search`, `CALL ivg.bm25.search`, `CALL ivg.ppr` and `CALL ivg.retrieve`
each failed on `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`), at four unrelated
causes. Every existing unit test asserted on the CTE text, and the text looked right —
what the four had in common is that nothing had handed the finished statement to IRIS.

| Symptom                                                            | Stage          | Cause                                                          | Fix                                   |
| ------------------------------------------------------------------ | -------------- | -------------------------------------------------------------- | ------------------------------------- |
| `SQLCODE -400 <UNDEFINED>%C0o+NN^%sqlcq…`                          | Query Open     | a CTE ordering by a bare `VECTOR_COSINE(...) AS score` alias   | `CAST(... AS DOUBLE) AS score`        |
| `<ARGUMENT ERROR> Incorrect number of parameters`                  | driver Prepare | `?` inside `JSON_TABLE(fn(...))`'s **source** argument         | inline through `_sql_arg`             |
| `SQLCODE -29 Field 'RRF_SCORE' not found in the applicable tables` | Prepare        | the outer `FROM` came from `stages[0]`, which was the BM25 arm | `context.result_stage`                |
| `SQLCODE -1 ) expected, IDENTIFIER (ORDER) found`                  | Prepare        | `ORDER BY` inside a CTE without `TOP`                          | `SELECT TOP k`, `FETCH FIRST` dropped |

Narrowing measurements for the first two, one variable at a time:

- the `-400` body standalone works; inside a CTE it fails; removing the JOIN does not
  help; a literal vector instead of `?` does not help; `ORDER BY 2 DESC` does not help;
  ordering by a **non**-vector alias (`1 AS score`) works. So it is the server's code
  generation for ordering a CTE by a vector-valued alias, and the cast is what avoids it
  while keeping `TOP` — and therefore the limit — inside the CTE.
- `SELECT kg_BM25(?, ?, 10)` binds fine; `JSON_TABLE(kg_BM25(?, ?, 10), …)` does not,
  even with the placeholder count matching the parameter count. IRIS does not treat that
  `?` as a parameter marker at all and then finds one argument too many.
  `JSON_TABLE(kg_BM25('default', 'aspirin', 10), …)` works in a plain `SELECT`, in a CTE,
  and in a CTE with an outer bind. `ivg.ivf.search` already knew this; the other three
  never got the same treatment.

Because binding is not available inside `JSON_TABLE`, the injection defence at those
sites is escaping: `_sql_arg` doubles every quote, and
`tests/unit/test_api_security.py::TestSqlParameterization` pins that a break-out attempt
stays inside the literal with the statement's quotes balanced.
`tests/integration/test_227_procedure_statements_execute.py` is the live gate — it
executes each statement and fails on any error of the four codes above.

Two more defects fell out of the same sweep, both invisible until the statements reached
the server. First, `rrf_score` was projected as a **node** variable
(`rrf_score AS rrf_score_id` plus `JSON_ARRAYAGG` label and property subqueries keyed on a
float), because the scalar guard tested for `"score"` and the procedure yields
`rrf_score`. Second, the `%Embedding.Config` demand described next.

### ~~`ivg.retrieve` demanded an `%Embedding.Config` that no namespace had~~ (fixed in 4.0.0)

The vector arm embedded the query text with IRIS's native `EMBEDDING(text, config)` and
took the config name from `ivg.retrieve`'s 6th argument, which defaults to `''`. So the
ordinary two-argument call asked the server to embed with a config named `' '`:

```text
[SQLCODE: <-280>:<Embedding configuration error>] [Location: <ServerLoop - Query Open()>]
[%msg: <%Embedding.Config ' ' does not exist. Create a configuration by adding an entry
to the %Embedding.Config table…>]
```

Nothing about that was recoverable on `ivg-iris-enterprise`. It ships the classes
(`%Embedding.Config`, `%Embedding.SentenceTransformers`, `%Embedding.OpenAI`), but
`SELECT * FROM %Embedding.Config` returns no rows and `sentence_transformers` is not
importable inside the instance, so no configuration can be created there. Native
embedding is unavailable, not merely unconfigured.

The engine already owned this decision and documented the order it tries —
`IRISGraphEngine.embed_text`: native `EMBEDDING()` when an `embedding_config` is set,
otherwise the configured Python embedder, otherwise a default SentenceTransformer. The
translator bypassed that chain; `_retrieve_query_vector` now defers to it:

1. a config named by the caller (6th argument), or failing that the engine's own
   `embedding_config` — native `EMBEDDING(?, ?)`
2. an engine with no config — `engine.embed_text(query)` supplies the vector and the arm
   binds it through `TO_VECTOR(?, DOUBLE)`, the shape `ivg.vector.search` already used
   for a list argument
3. no engine — the native call is left in place. `translate_to_sql()` without an engine
   only produces text, and nothing executes it.

So a bare `CALL ivg.retrieve('aspirin', 5) YIELD node, rrf_score RETURN node, rrf_score`
now runs wherever the engine can embed, and naming a config still selects the native path:

```cypher
CALL ivg.retrieve('aspirin', 5, 'default', '*', 60, 'my-embedding-config')
YIELD node, rrf_score RETURN node, rrf_score
```

An engine with neither a config nor an embedder (and no `sentence-transformers` installed)
now raises at translate time naming both remedies, instead of emitting SQL that the server
rejects. `-280` is a failure in the live gate, which no longer skips the retrieve cases.
`tests/unit/test_227_retrieve_embedding_source.py` pins the resolution order.

Note for callers: the query vector's width must match the embedding column's declared
width, because IRIS checks it at Query Open even against an empty table (`SQLCODE -257`,
zero rows). An embedder of the wrong dimension fails there, not silently.

---

## Constraint errors after an index build (verified 2026-09-20)

### Building a class's indices permanently breaks constraint-error decoding on other connections

Measured on `ivg-iris-enterprise`. Once any connection runs `%BuildIndices` on a
class — which is exactly what a `%NOINDEX` bulk load does in phase 5, so
`load_networkx` in `test_engine_misc_paths.py` triggers it — every **other** open
connection in the process stops being able to decode that class's constraint errors.
A violation still happens and still raises, but the text arrives as:

```text
<LIST ERROR> Incorrect list format, offset: 1 type detected : 0
```

Four properties make this nastier than a cosmetic message:

- **It covers every constraint code, not just one.** -119 (unique), -104 (field
  validation) and -121 (foreign key) all arrive as the same `<LIST ERROR>`. Nothing
  in the text distinguishes a duplicate from a missing endpoint.
- **It is connection-wide and permanent.** Every later statement on that connection
  behaves the same way for the rest of its life.
- **Nothing clears it.** `rollback()`, `$SYSTEM.SQL.PurgeForTable`, and issuing
  `BUILD INDEX FOR TABLE` were each tried and none of them restores decoding — and
  `BUILD INDEX FOR TABLE` poisons a connection the same way `%BuildIndices` does.
- **A connection opened afterwards decodes correctly.** A fresh connection is the
  only known remedy.

Two consequences, both already handled:

*Product.* `iris_vector_graph/_engine/nodes_edges.py:41` `_swallow_duplicate` cannot
rely on the text. It returns on the matchable cases (`-119`, "duplicate", "unique"),
re-raises anything that is not a `<LIST ERROR>`, and for a `<LIST ERROR>` **asks the
database whether the row is actually there** — if it is not, the error was something
else and is re-raised. Treating every `<LIST ERROR>` as a duplicate would swallow
real failures. Pinned by `tests/unit/test_swallow_duplicate.py`.

*Tests.* Any test whose assertion **is** the error text needs its own connection.
`tests/integration/test_nodepk_constraints.py:45` `constraint_conn` opens one per
test and closes it afterwards; the eight tests that read constraint messages use it
instead of the shared session connection. A test that asserts on message text
against the session connection passes or fails depending on whether an unrelated
file ran a bulk load first.

---

## Embeddings and vector width (verified 2026-09-18)

### ~~A named graph cannot carry its own embedding model or dimension~~ (fixed in 4.0.0)

Through 3.2.0 there was no per-graph embedding anywhere in the schema, and it was a
design fact rather than a missing filter: `kg_NodeEmbeddings` was
`id VARCHAR(256) %EXACT PRIMARY KEY, emb VECTOR(DOUBLE, N), metadata` with no
`graph_id` column, `Graph_KG.nodes` carried `UNIQUE (node_id)` so the same node could
not exist twice to hold two vectors, and `kg_KNN_VEC`'s `IN embeddingConfig
VARCHAR(128)` was accepted and never read. Two graphs in one namespace shared one
vector space and one width, so a 384-wide vector for a node in graph A and a 768-wide
one for the same node in graph B was one row being overwritten. Multiple models meant
one namespace per model.

**Spec 227 lifted all of it.** `nodes` is `UNIQUE (graph_id, node_id)`, the embedding
tables are re-keyed on `(graph_id, node_id)` with `emb_rowid` as the identity, and a
`(graph, model_key)` pair routes to its own physical table so two widths can coexist.
The fourth `kg_KNN_VEC` argument is the graph now. See
[`docs/migration/v4.0.0.md`](migration/v4.0.0.md).

**Spec 230 lifted the two parts 227 left behind.** Both were live through 3.2.0 and
neither is now:

- `kg_EdgeEmbeddings` is keyed `(graph_id, s, p, o_id)` with `emb_rowid` as the
  identity, and it is the *default edge route* — shaped like a generated edge route so
  one INSERT serves both. Before 4.0.0 the key was the triple alone, so two graphs
  asserting the same edge shared one row and the second write replaced the first.
- The BM25 leg is scoped by the corpus rather than by a procedure argument:
  `Graph_KG.docs` is keyed `(graph_id, id)` and `kg_TXT` takes the graph, so
  `kg_RRF_FUSE` narrows both of its legs. `kg_BM25` still takes no `graph_id`, which is
  correct rather than an omission — see the note under its own heading below.

What remains graph-blind is the IVF leg, and it refuses rather than guessing: see the
`search_nodes_by_vector` entry immediately below.

### ~~Callers that still cannot pass a graph~~, and one that read a column that does not exist

`kg_KNN_VEC` takes `graph` and `model_key` (keyword-only) in 4.0.0. Three things that
route through the same storage did not, so each of them always meant the default graph.
**All three are fixed in 4.0.0** (spec 230, FR-009 and FR-014):

- `search_nodes_by_vector` (`_engine/vector.py:646`) takes `graph` and `model_key`,
  keyword-only for the reason `store_embedding`'s are — its fourth and fifth positional
  parameters are `ivf_name` and `nprobe`, so a 3.2.0 caller passing a graph positionally
  would have named an IVF index instead. The IVF fallback stays graph-blind, because
  `Graph.KG.IVFIndex` is keyed by index name and holds no graph; a lookup that names a
  graph refuses rather than silently searching every graph's vectors through the index.
- `operators.py:26` `kg_RRF_FUSE` takes `graph` and forwards it; `kg_KNN_VEC` and `kg_TXT`
  on the same facade forward `graph`/`model_key` too. A shim that dropped either argument
  searched the default route while reporting a scoped search, which is why the forwarding
  is pinned per method in `tests/unit/test_operators_coverage.py` rather than assumed.
- `Graph.KG.PyOps.getExpectedDimension` takes `(graph, modelKey)` (`PyOps.cls:88`) and
  reads the declared width from `Graph_KG.embedding_registry` for that pair, falling back
  to `getDefaultDimension()`. It used to read the literal `Graph_KG.kg_NodeEmbeddings` on
  every call, so once `(graph, model_key)` routing existed, a routed table narrower or
  wider than the legacy one was reported at the legacy width — and `vectorToJson`, which
  validates against that number, rejected every correct write to it.

`getExpectedDimension` also had a plain bug, **fixed in 4.0.0**. `getVectorColumn()` returned
`"embedding"`, and no shipped schema has ever had a column by that name — the vector
column is `emb` (`iris_vector_graph/schema.py:118`). So the
`INFORMATION_SCHEMA.COLUMNS` lookup inside `getExpectedDimension()` matched no row
and the method fell through to `getDefaultDimension()` on **every** call. The
fallback is what hid it: 768 is both the default and the usual declared width, so a
dead lookup and a working one returned the same number. Measured on
`Graph_KG.kg_NodeEmbeddings`, `emb` is `varchar` with `CHARACTER_MAXIMUM_LENGTH`
265727, and `round(265727 / 346) = 768` — the iris-vector-rag formula is sound once
the column name is right. Two tests in
`tests/python/test_pyops_vector_conversion.py` pin it: one that the looked-up column
exists at all, one that the reported dimension is the declared width.

Two smaller things in the same file, both fixed: three names
(`DEFAULT_EMBEDDING_DIMENSION`, `VECTOR_TABLE`, `VECTOR_COLUMN`) are **ClassMethods,
not Parameters** — `PyOps.cls:6` says why — so `_GetParameter(...)` returned `None`
and tests printed `None.None` or raised `TypeError: int() argument must be … not
'NoneType'`; and a test asserted the message `"doesn't match"` while the code emits
`"does not match"` (`PyOps.cls:73`), with an `or "doesn" in error` hedge that matches
neither. Against a `MagicMock` both passed anyway.

### ~~`get_procedures_sql_list(embedding_dimension=...)` is inert~~ (removed in 4.0.0)

The parameter was accepted and never interpolated; the generated `kg_KNN_VEC` declared
no length anywhere, which is why the pre-3.1.0 default of 1000 never produced a
procedure at the wrong width. It is **gone** in 4.0.0 — passing it is a `TypeError`,
pinned by
`tests/unit/test_embedding_dimension_default.py::test_get_procedures_sql_list_no_longer_takes_a_width_at_all`.
The unlengthened `TO_VECTOR` it generated is deliberate and stays.

**First resolved as a deprecation in 3.2.0.** Live measurement showed the
three-argument `TO_VECTOR(:q, DOUBLE, n)` form pads or truncates the *query* and then
scores the reshaped value — a 6-element query truncated to 4 returned a cosine of `1.0` —
while the unlengthed form makes IRIS compare widths and raise `SQLCODE -257`. Wiring the
width in would replace a loud refusal with a silently wrong score, so the parameter is now
documented as ignored, emits a `DeprecationWarning`, and is removed in 4.0.0. See
[ADR-0005](adr/0005-vector-width-is-not-declared-in-to-vector.md).

### The dimension migration cannot see a second writer

`_migrate_vector_dimensions` compares each vector column against the dimension the
**calling engine** was configured with. Two processes calling `initialize_schema` on
the same namespace at different widths will each believe the schema agrees with them:
the second one's ALTER is refused if rows exist (logged `CRITICAL`,
`needs_manual_migration`) and silently applied if they do not. Nothing records which
width wrote which rows. Detecting the disagreement itself needs a stored expectation, not
a wider comparison here.

**Addressed in 3.2.0 by spec 226**, and this write-up's original conclusion — "the ledger
is the natural place" — was wrong. The expectation lives in `Graph_KG.embedding_registry`;
see [ADR-0006](adr/0006-embedding-identity-lives-in-a-registry-not-the-ledger.md). The
ledger cannot hold it for four independent reasons: it is opt-in and off by default, so it
is absent exactly where an unguarded second writer is most likely; no embedding write
passes through a changeset, so there is no revision to attach identity to;
`ledger_stats` is a namespace-wide singleton and `ledger_revisions` has no `graph_id`, so
it cannot carry per-table identity at all; and it is a history, whereas this is a
current-state assertion read on every write, which is the wrong access pattern to satisfy
by folding a revision log.

The migration hole itself is **unchanged**: a column at the wrong width whose table is
non-empty still goes to `needs_manual_migration` with the declaration left alone. What
3.2.0 adds is that the disagreement is now *recorded* and the next write is *refused*
(`EmbeddingIdentityConflict`) instead of being attempted at a width the column cannot
take.

### ~~A recorded width can go stale against the column it describes~~ (fixed in 4.0.0)

**Was open in 3.2.0.** `Graph_KG.embedding_registry.dimension` is a snapshot taken when the
row was written. `_sync_recorded_dimension` carried it forward only for tables the current
`_migrate_vector_dimensions` call actually altered — `for name in altered_names`. A column
that reached its target width by any other route left the old number recorded, and the
refusal then blamed the writer for a width the column no longer declares:

```text
dimension mismatch: recorded dimension 768 but this writer declares 128
```

Measured on `ivg-iris-enterprise` while walking spec 226's quickstart: all three vector
columns read `LEN,128` while `kg_NodeEmbeddings` and `kg_NodeEmbeddings_optimized` still had
`dimension = 768, set_by = 'adopted'` left behind by an earlier 768 fixture. Every write at
the column's real width was refused, and the error named the wrong culprit.

Compare the two before believing the message — they are read from different places:

```python
GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")  # the column
```

```sql
SELECT dimension, set_by FROM Graph_KG.embedding_registry
 WHERE table_name = 'kg_NodeEmbeddings'                                    -- the record
```

When they disagree on 3.2.0, delete the registry row and re-run `initialize_schema`; adoption
re-reads the live column declaration.

**Fixed in 4.0.0 (FR-031)** by taking the behaviour change the 3.2.0 write-up declined:
`GraphSchema.reconcile_recorded_dimensions` reads every embedding column and writes the
registry unconditionally on each `initialize_schema`, not as a side effect of having altered
something. `altered_names` is empty exactly when the columns already hold the configured
width — which is the state a second writer finds after the first one migrated, and exactly
when its own stale row needs carrying forward. A column with no declared width is skipped:
the catalog has nothing to copy, and inventing a width would be worse than the
`SQLCODE -260` that already reports an undeclared column.

---

## Cypher writes (verified 2026-09-20)

### ~~`MERGE` with inline node IDs could not be prepared~~ (fixed in 4.0.0)

`MERGE (a:Person {id:'x'})-[:LIKES]->(b:Person {id:'y'})` failed in every graph:

```text
[SQLCODE: <-23>:<Label is not listed among the applicable tables>]
%msg: Label 'N0' is not listed among the applicable tables^INSERT INTO
      Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS
      (SELECT 1 FROM rdf_edges WHERE s = n0.node_id AND p = ? AND o_id = n1.node_id)
```

The idempotency guard is built two ways — from the SQL aliases of `MATCH`-bound variables,
or from the UUIDs of nodes the same query generates. A node written `{id: 'x'}` is neither,
so the UUID form fell back to the alias form and spliced `n0`/`n1` into a statement that
selects literals and joins nothing. `MERGE` over `MATCH`-bound variables always worked,
which is what kept this hidden: the failing shape is the one-liner.

Fixed by `_merge_literal_node_id` in `iris_vector_graph/cypher/translator.py`, which
resolves the literal so the guard has values to bind. A non-string `id` stays a user
property, not an identifier.

### ~~A `CREATE` after a zero-row `MATCH` wrote the node and not the edge~~ (fixed in 4.0.0)

```cypher
MATCH (a:Person {id:'missing'}) CREATE (a)-[:R]->(b:Person {id:'new'})
```

left `new` in `nodes`, its label in `rdf_labels`, and its `id` in `rdf_props` — with no edge
and no error. openCypher runs a `CREATE` once per incoming row, so zero rows must write
nothing. The edge insert always selected from the matched rows and so wrote nothing; only the
inline node, label, and property inserts were emitted as unconditional
`SELECT <literal> WHERE NOT EXISTS (...)`.

Fixed by `_create_match_gate`, which puts those three writes under
`FROM (<the matched rows>) AS _cg`. Parameters for that statement shape bind in text order
(projection, then the derived table, then the `NOT EXISTS` guard) — measured against the
enterprise container, not inferred; the comment near `translator.py:4241` documents the
opposite order for a different shape, so neither rule generalises.

### A `MATCH` returning N rows still creates one node, not N

`_create_match_gate` selects `DISTINCT`, so a `MATCH` binding several rows collapses to a
single inline node rather than one per row. openCypher would create N. This is the deviation
the zero-row fix above makes visible rather than introduces: the writes are now correlated
with the match, but not multiplied by it. Deliberately out of scope for 4.0.0 — fixing it
means abandoning the `NOT EXISTS` idempotency guard on these three statements, since N
identical inserts are exactly what that guard exists to suppress.

**Workaround:** do the multiplication in the caller. Read the match, then write one
statement per row, with the row's own values as parameters:

```python
rows = engine.execute_cypher("MATCH (n:Gene) RETURN n.node_id AS id").rows
for (node_id,) in rows:
    engine.execute_cypher(
        "CREATE (c:Copy {source: $src})", parameters={"src": node_id}
    )
```

Measured on `ivg-iris-enterprise` against three matched rows, the four spellings
diverge, and only the first two are safe to rely on:

| Spelling                                                   | Nodes created |
| ---------------------------------------------------------- | ------------- |
| one `CREATE` per row from the caller, as above             | 3             |
| `UNWIND ['p','q','r'] AS i CREATE (c:Copy {source: i})`    | 3             |
| `MATCH (n:Gene) CREATE (c:Copy {tag: 'x'})`                | 1             |
| `MATCH … WITH collect(n.node_id) AS ids UNWIND ids AS i …` | **0**         |

The last row is the one to avoid: routing the rows through `collect` and back out
through `UNWIND` is the obvious in-query workaround, and it creates nothing at all
and raises nothing. A literal `UNWIND` list is expanded into one statement per
element at translation time, which is why it multiplies correctly.

### `CREATE` of an identical edge raises `SQLCODE -119` instead of adding a parallel edge

`Graph_KG.rdf_edges` carries `u_spo_graph UNIQUE (s, p, o_id, graph_id)`, so re-running a
`CREATE` of the same triple is refused:

```text
[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check upon INSERT>]
%msg: Table 'Graph_KG.rdf_edges', Constraint 'u_spo_graph', Field(s) s="a",p="R",o_id="b",
      graph_id=$c(0); failed unique check
```

openCypher's `CREATE` would add a second relationship between the same pair. The storage
layout cannot represent one, so the two cannot be reconciled without a distinct edge key —
`MERGE` is the working spelling for "ensure this edge exists". Pre-existing; unchanged in
4.0.0.

---

## Variable-length traversal (verified 2026-09-21)

All five of these were found by the 4.0.0 release gate, in the same failure cluster, and
every one answered with real rows — which is why none of them reported anything. A
variable-length pattern is answered by one of three routes depending on how its source node
is bound, and none of the three runs the statement it was translated from, so every
guarantee the SQL carried had to be re-established in the route. That is the shape of all
five defects. The first two need the default graph with Arno loaded, which is what a
single-tenant install runs.

### ~~An inbound or undirected pattern came back with the source's successors~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-031). `Graph.KG.NKGAccel.BFSJson` — the Rust accelerator
behind `_ArnoBfsAdapter` — takes `(srcId, preds, maxHops, maxResults)`. It has no direction
argument and walks `^NKG` outbound, yet `_select_bfs_strategy` handed it every
default-graph BFS, so

```cypher
MATCH (x)-[r*1..1]-(y)  WHERE x.id = $id RETURN y.id   -- undirected
MATCH (x)<-[r*1..1]-(y) WHERE x.id = $id RETURN y.id   -- inbound
```

both answered with x's **outbound** neighbours. Nothing looks wrong: the query succeeds,
the IDs exist, and the edge it names is in the database — it just points the other way.
Probed on `ivg-iris-enterprise`, `Graph.KG.TraversalBFS.BFSFastJson` answers `out`, `in`
and `both` correctly on the same `^KG` rows, so the defect was entirely in which adapter
the store picked.

`_select_bfs_strategy` now takes the direction and returns `_ObjectScriptBfsAdapter` for
anything other than `out`/`outbound`; `execute_bfs` threads it. Same rule as spec 227
applied to `graph` — an accelerator that cannot be asked the question does not get it.
`BFSJson` carries neither argument.

### ~~A property-bound variable-length pattern ignored its `LIMIT`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-032). When the source is bound by a property
(`WHERE x.id = $id`) rather than by `node_id`, the query is answered by
`_execute_var_length_labeled` / `_execute_var_length_labeled_path_funcs`. Neither read the
translated statement's row cap — they never run that statement — so
`... RETURN y.id LIMIT 5` over a twenty-neighbour hub returned twenty rows. The ID-bound
route did read the cap and pass it into BFS, which is why only the property-bound spelling
was wrong.

One `_extract_sql_row_limit` now reads all three spellings the cap arrives in — IRIS SQL's
`FETCH FIRST n ROWS ONLY`, the `SELECT TOP n` the build-106 `%qaqpre` workaround emits
instead, and a bare `LIMIT n` — and `_route_var_length` applies it to both labeled routes.

A query that also carries an `ORDER BY` is deliberately **not** truncated: these routes
assemble their rows from BFS output, which carries no sort, so the first five rows are not
the five the sort asked for. Truncating would turn "too many rows" into "the wrong rows",
so the cap is skipped and a warning logged naming the gap.

### ~~`RETURN DISTINCT` over a variable-length path did not deduplicate~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230). `MATCH (a {node_id: $src})-[:KNOWS*1..2]-(b) RETURN DISTINCT
b.node_id LIMIT 20` translates to `SELECT DISTINCT n1.node_id ... FETCH FIRST 20 ROWS ONLY`,
and the ID-bound route never runs that statement: it calls BFS and hands the store's
`(id, hops, pred)` rows straight back. BFS reports one row per reached edge, so an
undirected walk reaches the same node at two hops and by two predicates and the caller sees
it twice — 16 rows for 11 nodes on a 15-node chain, with `DISTINCT` in the query as written.

The cap compounded it. `max_results` went to BFS, which truncates raw hits *before* anything
deduplicates, so `LIMIT 20` could answer 20 hits holding 11 nodes. Under `DISTINCT` the
route now sends no cap to BFS, dedupes on the id keeping the first row for each — BFS emits
in hop order, so the surviving row's `hops` is its shortest — and applies the cap after.

### ~~`RETURN b.node_id` came back as a column of `NULL`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230). `MATCH (a {node_id: 'chain:0'})-[:NEXT*1..2]->(b) RETURN
b.node_id` answered `[[None], [None]]` — the right number of rows, every value `NULL`. The
property-bound route (`_execute_var_length_labeled`, taken whenever the source is not bound
to a `?` parameter: a literal in the pattern, or a label) walked BFS correctly and then
projected the `RETURN` by asking the store for `node_id` as a property. `node_id` is the
node's identity, and there is no `rdf_props` row for it, so every lookup missed.

`RETURN b` and `RETURN count(b)` were unaffected — neither takes the property projection —
which is why the defect only showed on `b.node_id`. The route now projects `node_id` from
the id it already holds and asks the store only for real properties.

### ~~`approx_count_distinct` answered a silent zero~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230). `MATCH (a {node_id: 'star:c'})-[:SPOKE*1..2]-(b) RETURN
approx_count_distinct(b) AS c` answered `0` for a node with four neighbours, with
`warnings=[]` and `sql=''`. The route resolves its source by scanning the translated
statement's bound parameters for a string, and a node ID written as a literal in the pattern
is inlined into the SQL rather than bound — so nothing was found, and the unresolved case
returned `0` as though it were a count.

Two defects, both fixed: the shared `extract_vlp_source_ids` learned the literal spelling
that sits beside the `= ?` one it already knew, and a source that genuinely cannot be
resolved now says so in the result's warnings rather than reporting a zero. `0` from an
unresolved source is a different claim from "no distinct neighbours", and a caller has no
way to tell them apart from the number alone.

---

## Bulk ingest and table statistics (verified 2026-09-21)

Both found by the 4.0.0 release gate chasing one symptom — `BulkIngestEdgesSQL` at 20
edges/s — which turned out to have two independent causes.

### ~~IVG ran `TUNE TABLE` nowhere, so a graph-scoped lookup planned as a scan~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-033). `initialize_schema` created the tables and the
4.0.0 migration re-keyed three of them by copying their rows into a staging table and
dropping the source over it. Neither told IRIS what shape the result was. On a re-keyed
install, `WHERE node_id = ? AND graph_id = ?` — the shape every graph-scoped read issues,
and the shape both of `rdf_edges`' composite foreign keys check on every edge insert —
planned as

```text
Read master map Graph_KG.nodes.IDKEY, looping on ID
```

a scan of the extent: 5.3 ms per lookup against 46,343 rows, 10.2 ms per edge insert. One
`TUNE TABLE Graph_KG.nodes` moved the same lookup to
`Read index map Graph_KG.nodes.pk_nodes_graph, using the given node_id and graph_id` and
0.23 ms.

`TUNE TABLE` does two things a re-keyed install needs: it measures the rows that are
actually there, and it discards the plans prepared before the current indexes existed.
`GraphSchema.tune_tables` now runs it over the core tables from `initialize_schema`
(reported as `status["tuned"]`) and from `upgrade_to_4_0_0` (reported as
`UpgradeReport.tuned`), skipping a table this install does not have rather than stopping.

Two facts worth keeping if you probe this yourself:

- An untuned table announces itself: every plan for it carries
  `Warning: Table <schema>.<table> is not tuned.` That warning is the checkable signal.
- `%SYSTEM.SQL.Stats.Table.ClearTableStats` takes **one** qualified argument
  (`"Graph_KG.nodes"`; two raise `<PARAMETER>`), and it does **not** restore the
  master-map plan — the plan and its cost were unchanged after clearing, and after
  `SetFieldSelectivity` and `SetExtentSize` too. The bad state cannot be recreated on
  demand, so no test asserts it.

### ~~A failed `INSERT` was the bulk paths' upsert, at ~40 ms each~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-034). `Graph.KG.EdgeScan`'s bulk methods registered each
endpoint by inserting it and tolerating `SQLCODE -119`, the duplicate. Inside the
transaction they wrap a whole batch in, a *failed* statement is the most expensive thing
they do: IRIS rolls it back to its own implicit savepoint. Measured on
`ivg-iris-enterprise`, 200 chained edges in one transaction:

| idiom                      | ms per edge |
| -------------------------- | ----------- |
| the `-119` idiom           | 39.53       |
| seen-set + existence check | 0.16        |
| `INSERT OR UPDATE`         | 0.10        |
| existence check only       | 0.03        |

Edges arrive chained — an edge's target is the next edge's source — so about half of every
batch's registrations were duplicates. `BulkIngestEdgesSQL` ran at 25–26 edges/s whatever
the batch size, over DBAPI, over the Native API, and from an `iris session` terminal
alike; re-ingesting a batch that already landed, where *every* insert is a duplicate, ran
at 9.

`RegisterDefaultNode` now checks a seen-set, then asks the table, then inserts, tolerating
-119 only for the row a concurrent writer lands in between; `BulkIngestEdgesSQL` asks for
the edge row before inserting it too, because on a re-ingest every one of those inserts is
the duplicate `u_spo_graph` refuses. 300 chained edges went from 28 edges/s to over the
test's 500 floor, and the re-ingest from 9.

**A transaction's per-row cost grows with its size**, so the caller's chunk size still
matters. The same 10,000 edges, measured live:

| rows per transaction | edges/s |
| -------------------- | ------- |
| 250                  | 13,803  |
| 500                  | 13,123  |
| 1,000                | 10,989  |
| 2,000                | 11,093  |
| 5,000                | 7,885   |
| 10,000               | 6,426   |

`BulkIngestEdgesSQL` wraps exactly the batch it is handed in one transaction and does not
commit early, so the chunk size is yours to choose. A few hundred rows per call is the
fast shape.

### ~~`BulkIngestNodesSQL` wrote labels and properties with no `graph_id`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-035). It inserted into `rdf_labels (s, label)` and
`rdf_props (s, "key", val)` without naming the column, so those rows landed with
`graph_id` NULL while every other writer writes `''`. The default graph then had two
spellings in one table, and a reader matching one silently missed the rows written the
other way. Both writes now name `graph_id` as `''`; a property is written with
`INSERT OR UPDATE`, so a re-ingest carrying a changed value updates it instead of failing
on `pk_props`.

---

## Text search and fusion (verified 2026-09-20)

### ~~Every iFind index was declared with syntax IRIS rejects~~ (fixed in 4.0.0)

All five generated declarations read `CREATE INDEX <n> ON Graph_KG.docs(text) INDEXTYPE =
%iFind.Index.Basic`. IRIS rejects that at Prepare with `SQLCODE -25`. Because
`idx_docs_text_ifind` and `idx_props_val_ifind` are in `_OPTIONAL_INDEXES`, the rejection
was logged at DEBUG and the install reported success — so a namespace built cleanly had no
iFind index, and `SHOW INDEXES` had no row to contradict.

The accepted form, verified live on `ivg-iris-enterprise`:

```sql
CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic
```

### ~~`Graph_KG.kg_TXT` could not be created, and it took `kg_RRF_FUSE` with it~~ (fixed in 4.0.0)

`kg_TXT`'s body matched with `%FIND(d.text, :q)` and ranked with `%FIND.Rank`. Neither is
IRIS syntax: a bare `%FIND` resolves as a user function (`SQLUSER.%FIND`, `SQLCODE -359`)
because iFind is reached through the index, not the column. So the procedure never
installed, `kg_RRF_FUSE`'s `FROM ... kg_TXT` could not resolve (`SQLCODE -30`), and
SQL-side fusion was absent on every installation. `kg_RRF_FUSE` was independently broken
too: it read `id` from `kg_KNN_VEC`, which 227 re-keyed to `node_id` (`SQLCODE -29`).

**There is no iFind ranker in the IRIS AI image.** `%iFind.Rank` exists as a function but
every call fails `<CLASS DOES NOT EXIST>` resolving `$$$IFDEFAULTRANKER`:

```sql
SELECT ID FROM %Dictionary.CompiledClass WHERE ID %STARTSWITH '%iFind.Ranker'  -- no rows
```

`%iFind.Highlight` does work. So 4.0.0 matches through the index and scores by term
frequency, and the column is named `score` rather than `bm25` because nothing here computes
BM25:

```sql
WHERE %ID %FIND search_index(idx_docs_text_ifind, :q)
```

`search_index` wants the **SQL** index name; the class index name gives `SQLCODE -151`.

### ~~The Python text search had never used iFind either~~ (fixed in 4.0.0)

`VectorMixin.kg_TXT` carried the same invented predicate, so the `except` branch below it
ran on every call for the method's whole life and the LIKE fallback answered instead:
substring matching rather than word-aware, every score a flat `1.0`, and a DEBUG log as the
only sign. The fallback now logs at WARNING and also catches `-151` (the index named in
`search_index` is absent). Separately, `kg_VECTOR_GRAPH_SEARCH` passed
`int(min_confidence * 1000)` as kg_TXT's threshold — an edge-confidence fraction scaled
into an occurrence count, which asked for 500 occurrences of the search string once the
iFind path started working.

### ~~`kg_RRF_FUSE`'s text leg is namespace-wide, and its two legs use different key spaces~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-007). Two defects in one procedure, and the second was
the worse of them.

`kg_RRF_FUSE` took `graphId` and applied it to the vector leg only, because
`Graph_KG.docs` had no `graph_id` column and the text leg had nothing to filter on: a
scoped fusion returned its own graph's vectors plus **every** graph's documents. Proven
live, with a fusion scoped to one graph returning that graph's node plus unrelated
documents seeded outside it.

The ids also came from different key spaces — the vector leg answers node ids, the text leg
answered document ids — so `FULL OUTER JOIN V.id = K.id` could never match a row and RRF
degraded to two independent ranked lists concatenated. Every "fused" row carried one leg's
score and NULL for the other, which looks like a sparse result rather than like a broken
join.

`Graph_KG.docs` is re-keyed in 4.0.0: `graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''`,
`CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)`, and **`id` now means a node ID**. That is
what makes the fusion fuse. `:graphId` reaches both legs, and the join is on
`V.id = K.id AND COALESCE(V.graph_id, '') = COALESCE(K.graph_id, '')` — redundant today
because both legs are already restricted, and deliberately there because the next leg added
to this fusion will not be, and a join on id alone would then fuse one graph's vector score
with another graph's text score into a single row.

`graphId` is a required seventh argument, not an optional one: the pre-4.0.0 call passed
NULL into `kg_KNN_VEC`'s fourth slot to satisfy its arity, and leaving that NULL would now
mean "the default graph" by accident.

There is deliberately **no foreign key from `docs` to `nodes`**, so a caller may write a
document before the node exists. The migration therefore has orphan rows to deal with, and
it places or quarantines them — never deletes one, never places one by guess (spec 227
Story 5 treatment; see `docs/migration/v4.0.0.md`).

Note that `kg_BM25` still takes no `graph_id`. That is correct rather than an omission: its
corpus is `Graph_KG.docs`, which now carries the graph itself, and the scoping happens on
the rows it reads.

---

## Cypher expression semantics (verified 2026-09-22)

### Division by zero answers `null`, where openCypher raises

**Deliberate deviation, documented rather than changed.** openCypher treats `1/0` and
`1 % 0` as arithmetic errors. IVG answers NaN, which serializes to JSON `null`, so
`MATCH (n) RETURN 1/0 LIMIT 1` comes back `200` with `rows == [[null]]` through
`/api/cypher` and without an error through `/graphql`.

The translator emits a guard whenever the divisor is not a provably non-zero literal
(`iris_vector_graph/cypher/translator.py:8895` for `%`, `:9092-9093` for `/`):

```sql
CASE WHEN <rhs> = 0 AND <lhs> IS NOT NULL THEN CAST('NaN' AS DOUBLE) ELSE ... END
```

Without it IRIS fails the whole statement at Query Open with `SQLCODE -400 <MAXNUMBER>`,
which takes every other row of the result with it — a per-row null is the narrower failure.
A literal non-zero divisor emits `FLOOR(a / b)` with no guard, so Cypher's integer floor
division is unaffected, and `1/n.weight` is always guarded because the translator cannot
know what the column holds.

Callers that need the openCypher error must test for it themselves; a `null` here means
either "no value" or "divided by zero" and the two are not distinguishable in the result.
`tests/unit/test_230_division_by_zero_semantics.py` pins the emitted guard and
`tests/e2e/test_stress_api.py::test_division_by_zero_is_a_null_not_an_error` pins the
endpoint behaviour, so this changes only on purpose.

### `RETURN` column order is not preserved across `CALL { ... }`

**Deliberate deviation, documented rather than changed.** A correlated `CALL { ... }`
whose body returns a single aggregate contributes its column to the projection first,
whatever position `RETURN` gives it:

```cypher
MATCH (p:Protein)
CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(q) RETURN count(q) AS deg }
RETURN p.node_id, deg
```

answers `columns == ['deg', 'p.node_id']`. The subquery is translated into a scalar
subquery appended to the projection at `iris_vector_graph/cypher/translator.py:6050`,
which runs while the `CALL` clause is being handled — before the outer `RETURN` clause is
read at all. Reordering the projection afterwards would have to move the subquery's bound
parameters with it, and parameter position in IRIS follows the `?`'s position in the SQL
text, so the reorder is a change to how the projection and its parameters are assembled
rather than a one-line swap.

Nothing is dropped and nothing is renamed: both columns are present and `column_name_map`
still reports `p_node_id` as `p.node_id`. A caller that zips `columns` with `rows` — which
is what `execute_cypher` returns and what the Bolt server sends — reads the right answer.
A caller that indexes rows positionally against the order it wrote in `RETURN` does not.
`tests/unit/test_230_call_subquery_column_order.py` pins the current order, and
`tests/e2e/test_subquery_call_e2e.py::TestSubqueryCallE2E::test_correlated_subquery_degree`
reads its rows by column name for this reason.

---

## GraphQL API (verified 2026-09-20)

The three entries below came out of unskipping 19 contract tests in
`tests/contract/test_graphql_schema.py` and `test_graphql_queries.py`. Every one of them
either asserted `from api.graphql.schema import schema` raises `ImportError` — which it
does forever, since the shipped module is `api.gql` — or carried
`@pytest.mark.skip("Will be unskipped when schema is implemented")` while the schema
shipped underneath it. They now run against the real schema, which is how these surfaced.

### ~~The contract's `graphStats` ships as `stats`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** by correcting the contract, not the server. Through 3.2.0,
`specs/archive/003-add-graphql-endpoint/contracts/schema.graphql` named the root field
`graphStats` while `api.gql`'s `CoreQuery` published `stats`, so a client that followed the
published contract got a validation error on a field the server does in fact serve. The
`GraphStats` type matched field for field (`totalNodes`, `totalEdges`, `nodesByLabel`,
`edgesByType`) — only the root field name diverged.

The contract now names `stats` (`schema.graphql:191`) and says which resolver serves it.
Renaming the published field instead would have broken every existing client to satisfy a
document none of them could have been using. The contract test asserts the shipped
spelling.

### ~~There is no Subscription root type~~ (documented in 4.0.0)

**Still unimplemented, no longer mis-specified.** `contracts/example_subscriptions.graphql`
and `schema.graphql`'s `type Subscription` specify `proteinCreated`, `proteinUpdated`,
`interactionCreated` and four more. Nothing implements them and no transport is wired:
`strawberry.Schema(query=Query, mutation=...)` in `api/gql/schema.py` passes no
`subscription=`.

What changed in 4.0.0 is that the contract says so. Every field in the block is marked
`UNIMPLEMENTED`, as is the `subscription:` line of the schema definition
(`schema.graphql:205-235`), because a client needs to tell "not specified" from "specified,
not built" — and a contract that declares a root type the server has never had is a
documented API that is silently wrong. The block stays rather than being deleted so the
record of what was specified survives.

`tests/contract/test_graphql_schema.py::test_there_is_no_subscription_root` asserts the
absence, so it fails the day a Subscription root appears — which is the point at which the
contract's field names need checking and these markers need deleting.

### `api/gql/types.py` and `api/gql/resolvers/` are an unreachable duplicate

**Open.** The shipped schema is built from `api/gql/core/types.py` (the `Node` interface,
`JSON` and `DateTime` scalars, `GraphStats`) plus `examples/domains/biomedical/types.py`.
`api/gql/types.py` and `api/gql/resolvers/{query,mutation}.py` define a second, parallel
set of the same types and resolvers that nothing in the schema imports — the only
importers left are each other and `tests/unit/test_gql_protein_mutations.py`, which pulls
`CreateProteinInput` from the dead copy.

This is why the lowercase `datetime` scalar took two edits to fix: patching
`api/gql/types.py` changed nothing observable. Anything found in one copy has to be
checked in the other, including the unqualified table names and the hardcoded 768-dimension
check in `api/gql/resolvers/mutation.py`. Deleting the duplicate is the right end state but
it is an API-surface decision, not a cleanup.

**Workaround:** before believing a GraphQL fix, check which copy the schema actually
builds from and fix both:

```bash
grep -n "^from \|^    from " api/gql/schema.py   # the live import list (relative)
grep -rn "<the symbol you changed>" api/gql/     # every copy that defines it
```

A change confined to `api/gql/types.py` or `api/gql/resolvers/` changes nothing a
client can observe. The live path is `api/gql/core/types.py` plus
`examples/domains/biomedical/types.py`.

### ~~`/graphql` answered empty against a populated namespace~~ (fixed in 4.0.0)

**Fixed in 4.0.0.** Three defects stacked on the same request. Every generic resolver in
`api/gql/core/resolvers.py` opens with `engine = info.context.get("engine")` and returns
`[]`, `None` or zeros when the key is absent. Two app factories built that context —
`api.gql.create_app` and `api.main.create_app` — and only the first supplied `engine`. The
mounted `/graphql` route, the one `api/main.py`'s `__main__` serves and every test and
document uses, came from the second. So `nodes`, `node` and `stats` reported an empty graph
and **no GraphQL error**.

Behind that: `GenericNode` — what `node`/`nodes` return for every label no domain resolver
claims — was not in the schema, because the fields are typed as the `Node` interface and
nothing in the query graph named the implementer. Any result built from it was refused with
`Abstract type 'Node' was resolved to a type 'GenericNode' that does not exist inside the
schema`. And `nodes` finished by calling `await self.node(...)`, where `self` is the
Strawberry root value — `None` on a query root — so a `nodes` query that matched a row
raised `AttributeError: 'NoneType' object has no attribute 'node'`.

The context is now built in one place, `api/gql/context.py:build_graphql_context`, used by
both factories; `strawberry.Schema(..., types=[GenericNode])` registers the implementer; and
the load path is a module-level `_load_node` that both fields call.

What kept all three invisible was the assertion style in `tests/e2e/test_stress_api.py`:
`assert "data" in body`. Strawberry answers a refused query with `{"data": null, "errors":
[...]}`, so the key is present either way — three tests passed against queries GraphQL had
rejected, one of them naming a root field (`cypher`) that has never existed in any release.
Assert `body.get("errors") is None` and then assert on the payload.

### ~~`nodes` and `get_node` read every graph that shared a node ID~~ (fixed in 4.0.0)

**Fixed in 4.0.0.** Spec 227 replaced `UNIQUE (node_id)` with `UNIQUE (graph_id, node_id)`,
so one node ID can legitimately exist in several graphs. Three readers had no graph
predicate at all and therefore answered across all of them:

- `engine.get_nodes` (and `get_node` through it) selected from `rdf_labels` and `rdf_props`
  on `s IN (...)` alone, merging every graph's labels into one list and letting one graph's
  property values overwrite another's. Its existence check, `SELECT node_id FROM nodes WHERE
node_id IN (...)`, likewise reported a node as present when it only existed elsewhere.
  Both now take `graph=None` meaning the default graph, and scope every statement to it.
- `CoreQuery.nodes` — the published `/graphql` field — selected `FROM Graph_KG.nodes n JOIN
Graph_KG.rdf_labels l ON l.s = n.node_id` with no `graph_id` on either side, so it
  returned other graphs' nodes and matched them against other graphs' label rows. It now
  takes a `graph` argument (default graph when omitted), carries `n.graph_id = ?`, and joins
  `l.graph_id = n.graph_id` / `p.graph_id = n.graph_id`. `node(id:)` takes the same argument.

Two smaller defects in the same builder went with them. The property-order path emitted
`LEFT JOIN rdf_props order_p` with no schema prefix, and an unqualified name resolves
against the connection's default schema (`SQLUser`), so `nodes(orderBy: "<property>")` — a
documented argument — failed at Query Open instead of ordering. And its parameter was bound
before `where.key` although its `?` appears after that JOIN in the text, so a query using
`where` and `orderBy` together filtered on the sort key and sorted on the filter key.

The batch branch keyed on `info.context["node_loader"]` is gone: no `NodeLoader` exists in
`api/gql/loaders.py`, no factory ever supplied the key, and it read no graph, so it would
have reinstated the merge. `tests/e2e/test_230_graph_scoped_node_reads_e2e.py` holds the
decisive fixture — one node ID in two graphs, with each graph's labels, properties and
`orderBy` answered from its own graph and the default graph holding neither row.

---

## Packaging (verified 2026-09-19)

### `pip install iris-vector-graph` alone could not import the package

**Fixed in 3.2.0** by adding `requests>=2.28.0` to core `dependencies`. The fix was
prepared as `3.1.1`, which was never published. Affected
every release from 3.0.0 through 3.1.0; if you are on one of those, install an extra
or add `requests`. Kept here because the shape of the mistake is worth remembering:
the packaging metadata and the import graph disagreed, and no test compared them.
`tests/unit/test_core_dependencies_cover_eager_imports.py` now does.

The original report follows.

`import iris_vector_graph` raises
`ModuleNotFoundError: No module named 'requests'`. `__init__.py:45` imports
`fhir_bridge` eagerly, `fhir_bridge.py:20` imports `requests` unconditionally, and
`requests>=2.28.0` is declared only in the `[full]` extra (`pyproject.toml:67`), not
in core `dependencies`. Installing `requests` into the same venv makes the import
succeed with nothing else missing, so this is the only gap of its kind in the eager
import chain.

Not a 3.1.0 regression. Verified against both wheels installed from PyPI into clean
3.13 venvs on 2026-09-19 — 3.0.1 fails identically from its own `site-packages`. The
defect dates to `c46dc1b` (spec 027, FHIR-KG Clinical Bridge), which added the
`fhir_bridge` import to `__init__.py` without moving its dependency. It stayed
invisible because every development and CI path installs an extra.

Two candidate fixes were on the table, both one line: add `requests>=2.28.0` to core
`dependencies`, or guard the import in `fhir_bridge.py` and raise on first use. The
first was taken — it is what the eager `__init__` already promises, and it keeps
`get_kg_anchors` and friends working on a bare install rather than trading one failure
mode for another.

---

## Deployment and namespaces

A namespace has IVG's behaviour only once the `Graph.KG.*` classes are deployed
into it. A `Graph_KG` schema built by DDL alone — plain `CREATE TABLE`, or
`initialize_schema(auto_deploy_objectscript=False)` — is a shell: `graph_id`
nullable instead of required-with-default, primary key `edge_id` instead of `ID`,
no `Eraser`/`TemporalIndex`/`GraphStores`, and no schema migration ever reaches it.
Check with:

```sql
SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'Graph.KG.Eraser'
```

**Changed in 4.0.0:** this check used to name `Graph.KG.Edge`, which spec 227 deleted —
`rdf_edges` is DDL-owned, and a class declaring the same table was the third stale
declaration of one. Against a 4.0.0 deployment the old query returns 0, which reads as
"nothing is deployed" on a namespace that is fully deployed. `Graph.KG.Eraser` is the
substitute because it is behaviour no DDL path can produce and nothing else provides.

`tests/integration/test_namespace_isolation.py` needs a deployed secondary
namespace named in `IVG_SECONDARY_NAMESPACE`; without it, 11 of its 15 cases
skip by design. See `README.md` §Non-USER Namespace Deployment.

### ~~`deploy/` ships a v1.37.0 snapshot of `Graph.KG`~~ (fixed in 4.0.0)

**Fixed in 4.0.0** (spec 230, FR-016) by deleting the duplicate tree.
`deploy/Dockerfile:8` copied `deploy/projects/ObjectScript` into `/tmp/cls` and
`deploy/iris.script` loaded that directory — a second copy of the classes, last touched at
v1.37.0. It predated specs 214, 223, 226 and 227: `MCPTools.cls:178` in that copy still
read `kg_NodeEmbeddings` through an `id` column that no longer exists, so an image built
from it answered semantic search with the RowID and no graph predicate.

`deploy/Dockerfile`, `deploy/iris.script`, `deploy/projects/` and its eleven `.cls` files
are gone. `iris_src/src/` is the single source of truth; deploy with
`scripts/enterprise-container.sh deploy`. The rest of `deploy/` (`bolt-on/`, `certs/`,
`docker/`, `iris-build/`) is unaffected and still in use.

### Upgrading a real 3.2.0 install: six orderings, all fixed in 4.0.0

Found by rehearsing the upgrade against a namespace built by
`pip install iris-vector-graph==3.2.0` — none of them reproduce against a fresh
4.0.0 schema, and none reproduce against the committed
`tests/fixtures/snapshots/ivg-3.2.0.zip` either, which is why the rehearsal is the
proof and the fixture is the regression check. A snapshot restores **rows**; it
cannot restore a class-owned table declaration, so a fixture-built `rdf_edges` is
always DDL-owned and the first item below cannot happen in it.

1. **The edges were inside `Graph.KG.Edge`.** Deleting the class deleted the
   extent. Now staged to `rdf_edges__ivg400rescue`, restored after the DDL rebuild,
   and anything 4.0.0's composite foreign key rejects lands in
   `rdf_edges__ivg400unplaced` rather than being dropped. A failed restore raises
   instead of logging past the best-effort deploy handler.
2. **`Graph.KG.TraversalBuild` compiled with no methods.** The ObjectScript layer
   deploys before the 227 re-key adds `graph_id` to `rdf_labels`/`rdf_props`, so its
   embedded SQL failed `Field 'GRAPH_ID' not found in the applicable tables` and
   `BuildKG` became `ERROR #5123: Unable to find entry point` — after the `^KG`
   kill, leaving neither layout. The `kg_node_stores` step now recompiles
   `Graph.KG` and verifies every rebuild entry point in
   `%Dictionary.CompiledMethod` before the first `Kill`, dry runs included, and
   refuses by name with the tree still whole.
3. **`idx_docs_graph` reported `SQLCODE -31` as a schema error.** `Graph_KG.docs`
   gains `graph_id` in the migration's `docs` step, so the index cannot exist yet;
   `initialize_schema()` now skips it by shape and logs a deferral naming
   `upgrade_to_4_0_0(conn)`, matching `kg_KNN_VEC`.
4. **The rescue in (1) could not rebuild the table it had emptied.** Found by
   re-running the rehearsal after fixing (1)-(3): staging succeeded, the class went,
   and `CREATE TABLE Graph_KG.rdf_edges` was refused with `SQLCODE -314: Foreign Key
'FK_EDGES_SOURCE' references non-unique column(s)... 'GRAPH_ID,NODE_ID'`, because
   v3.2.0's `nodes` carries `UNIQUE (node_id)` alone and the composite key is added
   later by the embeddings migration. The -314 was an ordinary `ProgrammingError`, so
   the deploy call site logged it at DEBUG as "expected in Docker" and
   `initialize_schema` returned `tables_created: True, objectscript_deployed: True`
   over a namespace with **no edge table**, its only copy of the edges in
   `rdf_edges__ivg400rescue`. The `^KG` re-key then blamed the missing table for
   having "no `graph_id` column", advising `initialize_schema()` — the step that had
   deleted it. Now: the restore adds `uq_nodes_graph_node` itself before the rebuild,
   wraps every failure inside it in `RdfEdgesRescueError` (not only a short row
   count), and the re-key probes `INFORMATION_SCHEMA.TABLES` so an absent source is
   reported as absent and pointed at the staging table.
5. **The key that fixed (4) aborted the step after it.** Found by re-running the
   rehearsal again: the restore now adds `uq_nodes_graph_node`, so the embeddings
   migration's re-key reaches its own `ADD CONSTRAINT uq_nodes_graph_node` with the
   key already there, and IRIS refuses it by the index name it derives from the
   constraint — `SQLCODE -400`, `ERROR #5067: Index name conflict: uqnodesgraphnode`
   — wording that mentions nothing about "already". The tolerated set matched none
   of it, so `upgrade_to_4_0_0` raised with `rdf_labels` still unscoped and the `^KG`
   rebuild never reached, which then refused for the reason in (4). Now tolerated by
   name, and an unrelated fatal `-400` still raises.
6. **Compiling `Graph.KG` twice switched text search off.** Found by the release
   gate after (5): every text leg answered
   `SQLCODE -149 ... <CLASS DOES NOT EXIST> idxdocstextifindEmbedded+2^Graph.KG.docsivg400.1`.
   `%FIND search_index(...)` reaches an iFind index through a class the compiler
   **generates** while compiling the index's owner (`<owner>.<hash>`,
   `GeneratedBy = '<owner>.CLS'`, extending `%iFind.Find.Basic`), and a second
   `$SYSTEM.OBJ.CompilePackage("Graph.KG","ck-d")` deletes that compiled helper and
   writes no replacement, because the owner is already up to date. Pass 1 generates
   it, pass 2 removes it. Two compiles is the ordinary case — the deploy in
   `initialize_schema()` plus the `^KG` re-key's recompile, or a `LoadDir` deploy over
   an up-to-date tree on its own — so this reached every upgraded **and** every
   re-deployed install, on both `idx_docs_text_ifind` and `idx_props_val_ifind`.
   Nothing reported it: the index is still in `%Dictionary.CompiledIndex`, the class
   definition is intact, `SHOW INDEXES` lists the row, and `kg_TXT` caught the error
   and fell back to `LIKE` — substring matching, every hit scored 1.0 — so the leg
   kept answering with different semantics. Now both `initialize_schema()` and the
   re-key compile each affected owner on its own, which always regenerates the
   helper, and **re-read** to decide rather than trusting the compiler's status;
   `initialize_schema()` returns `ifind_indexes` and warns by name about any index
   still unsearchable. `kg_TXT`'s warning distinguishes this repairable failure from
   an index that was never created.

See `docs/migration/v4.0.0.md` for what to run and how to resolve a quarantined
edge.

### An upgraded install's embedding tables are owned by differently-named classes

**Inert by construction, documented rather than changed.** The embeddings migration
drains `kg_NodeEmbeddings` into a staging table named with `_STAGING_SUFFIX`
(`iris_vector_graph/migrations/graph_scoped_embeddings.py:74`) and renames the SQL table
into the source's place. A rename moves the **SQL table name**, not the IRIS class name,
so an upgraded namespace holds `Graph_KG.kg_NodeEmbeddings` projected from
`Graph.KG.kgNodeEmbeddingsivg400`, where a fresh install projects it from
`Graph.KG.kgNodeEmbeddings`. The same holds for `kg_NodeEmbeddings_optimized`.

The name is visible in two places. `INFORMATION_SCHEMA.ROUTINES` lists a `<Class>_Extent`
class query for every persistent class, so an upgraded install answers
`kgNodeEmbeddingsivg400_Extent` where a fresh one answers `kgNodeEmbeddings_Extent`; and
`GraphSchema.derive_class_name` (`iris_vector_graph/schema.py:1259-1275`) guesses a class
from a table name, so on an upgraded install its guess is wrong. That function is
documented as a guess and is only reached when the dictionary cannot be read: every
reader in the package — the index reports, the `_call_classmethod` bridges, the width
probe — resolves a table's class through `GraphSchema.resolve_table_class`, which reads
`%Dictionary.CompiledClass`. Renaming the class to match would mean copying every
embedding row a second time for a name no caller passes.

`tests/integration/test_227_fresh_vs_migrated.py::test_every_compared_table_resolves_a_class_on_both_installs`
pins the property that matters — both installs resolve a class for every compared table —
and `_procedures` in `tests/e2e/test_227_migration.py` excludes `*_Extent` from the
procedure comparison for this reason, so the two catalogs are still compared on
everything else.

---

## Loose ends

- `tests/e2e/test_betweenness_neighborhood_e2e.py:21-22` recompiles
  `Graph.KG.Traversal` and `Graph.KG.EdgeScan` with `cuk-d` from a module fixture, which
  rewrites class state the rest of the session shares. Gate it or drop it. (It named
  `Graph.KG.Edge` before spec 227 deleted that class.)
- The same fixture writes `^KG("deg", "k_<n>")` at `:39` — a flat subscript with no graph,
  which no 4.0.0 reader walks. Pre-existing, and it does not affect the test's assertions
  because Arno reads `^NKG`; it is left alone rather than quietly edited, because the
  `^KG` writes around it are what the test is exercising.
- spec-221 Phase 3 docs are unticked: T006 (`create_edge_temporal()` docstring —
  `upsert=True` updates weight, bucket aggregates are not adjusted) and T007
  (`docs/USER_GUIDE.md` §Temporal Graph, same note).

---

## Investigated, not a defect

- **`Graph/KG/Ledger.cls:217` `RebuildEdgeIndices`** — the five-index
  `%BuildIndices($ListBuild("uspo", "idxS", …), 1, 0)` call that used to be here named
  exactly the indices `Edge.cls` declared, and the `idx_edges_*` names that looked missing
  belonged to the DDL path. Both readings are moot in 4.0.0: spec 227 deleted
  `Graph.KG.Edge`, `rdf_edges` is declared by the DDL alone, and all four structural
  tables are rebuilt the same way, with `BUILD INDEX FOR TABLE`. The rebuild was once
  unsafe on `rdf_edges` because the functional index that class declared would have run
  `PurgeIndex` and killed `^KG` and `^NKG` — the spec 206 class of data loss. The index
  and the class are both gone, so the table now has plain B-tree indices and no purge
  hook.

---

## Resolved history

### IVF index tests — isolation marker

All 18 IVF tests (unit + E2E in `test_ivf_index.py`) carry
`@pytest.mark.requires_clean_isolation`: they need clean database state and must
not run straight after unit tests that pollute the session-scoped connection.

```bash
pytest -m 'requires_clean_isolation' tests/unit         # 18 pass
pytest -m 'not requires_clean_isolation' tests/unit     # rest pass
```

The `iris_connection` fixture is session-scoped and shared across the whole unit
suite; accumulated data defeats per-test cleanup, and IVF tests are the ones
sensitive to it. The marker records the constraint instead of hiding it. This is
a pytest architecture property, not a release regression.
