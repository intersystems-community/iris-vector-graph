<!-- markdownlint-disable MD013 -->

# Known Issues

Open findings carried into the next release, each verified against the working tree
on the date in its heading. A finding stays here until it is fixed or ruled out;
ruled-out entries move to [Investigated, not a defect](#investigated-not-a-defect)
with the evidence, so the same suspicion is not re-raised.

---

## Test and build environment (verified 2026-09-16)

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

### ~66 integration failures and 26 errors against the v3 API, all pre-existing

Run as chunks with the five crashing files excluded, the suite gives roughly 66
failures and 26 errors. An earlier count of "51 failures and 4 errors" was
measured with a chunk that segfaulted early and lost its own results; this is the
fuller number.

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
| FK on `rdf_edges` not enforced, so no exception raised       | 3     | `test_nodepk_constraints.py:152`, `test_diabolical_qa.py:200` |
| Remaining one-offs (embed queue, PPR, chunked Arno, RRF)     | 15    | see the recipe below                                          |

The counts in that table classify the 55 cases catalogued on the first pass; the
36 added by the fuller run fall into the same groups and are not re-tallied here.

Reproduce the list:

```bash
grep -hE "^(FAILED|ERROR) " chunk*.log | sed 's/^[A-Z]* //; s/ - .*//' | sort -u > failing.txt
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m pytest $(tr '\n' ' ' < failing.txt) -q -p no:randomly --tb=line
```

The FK group is the one worth reading as a product question rather than test
rot: `create_edge` to a nonexistent node returns `True`, and inserting an edge
with a missing endpoint raises nothing.

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

### `_detect_arno`'s smoke probe disables a healthy Arno

`stores/iris_sql_store.py:211` smokes the callout once before trusting it:

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

Fix is in two places, tests first: guard the blank `raw` in `BFSJson` so an empty
result returns an empty array instead of throwing, and give the probe a seed that
exists (or accept "no such node" as a pass).

### `User.PageRankEmbedded` does not compile

`$system.OBJ.LoadDir` over `iris_src/src` reports exactly one error, and it is
always this one:

```text
ERROR #5559: The class definition for class 'User.PageRankEmbedded' could not be parsed correctly
```

`iris_src/src/PageRankEmbedded.cls` ships anyway, and
`scripts/deploy_objectscript.py:171` still calls
`##class(PageRankEmbedded).ComputePageRank(...)`. Either the class is repaired or
it and its caller go. Every `Graph.KG.*` class compiles clean.

### `ruff check .` reports 2048 findings

`pyproject.toml` has no `[tool.ruff]` section, so the run uses ruff's defaults:
690 `F401` unused-import, 394 `F405` star-import usage, 299 `F841` unused-variable,
196 `F541`, 186 `E702`, and 41 `F821` undefined-name. §5 of the pre-release
checklist asks for zero and has never been met. The 41 `F821` are worth triaging
first — an undefined name is a real defect wherever it is not a star-import artifact.
Pin a rule set before treating this gate as meaningful.

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
how `User.PageRankEmbedded`'s `#5559` below finally surfaced. Any other script
that checks a `%Status` for Python truth has the same defect.

---

## Graph scope (verified 2026-09-16)

### The default graph is falsy, so `''` reads as "every graph"

ADR-0003 spells the default graph `''`, and three call sites test that value for
truth instead of for `None`:

| Location                    | Effect                                                                           |
| --------------------------- | -------------------------------------------------------------------------------- |
| `cypher/translator.py:2542` | `USE GRAPH ''` appends no `graph_id` predicate — the query spans all graphs      |
| `cypher/translator.py:6274` | same value takes the EdgeScan path, which merges graphs (see `MatchEdges` below) |
| `_engine/schema.py:745`     | `retract_inference(graph="")` deletes inferred edges in every graph              |

Each is a one-line change to `is not None`, but the fix is only correct once
`MatchEdges`' `0` is settled, so they are logged together.

### `Graph.KG.EdgeScan.MatchEdges` overloads `0`

`EdgeScan.cls:30` takes `graph = 0` and `:39` reads `0` as "merged view, all
graphs", while ADR-0001 makes integer `0` the default graph's own key in
`^KG("out", ...)`. A caller asking for the default graph and a caller asking for
everything are indistinguishable. Two ways out, and this needs a decision rather
than a quiet edit:

- **A** — keep one method, change `0` to mean the default graph and add a separate
  sentinel for the merged view. One line in `EdgeScan.cls`, changes behaviour for
  anything already passing `0` deliberately.
- **B** — `MatchEdges` becomes scoped-only and a new `MatchEdgesAllGraphs` carries
  the merged view. No silent change of meaning, two methods to keep in step.

### `USE GRAPH` predicates are inlined against one spelling of `rdf_edges`

`translator.py:2550` and `:2560` append `f"{ea}.graph_id = '{safe_graph}'"` built
from alias-prefix guesses (`startswith("e")`, `not startswith("n")`, a `break`
after the first match). It works for the shapes under test and is not derived
from the query's own edge set. Worth reworking, and worth doing deliberately —
it decides which rows a tenant-scoped query sees.

### Nodes cannot sit in the default graph while their edge sits in a named graph

`Graph_KG.nodes` carries `UNIQUE (node_id)`, so the spec-214 case of one node
reachable from several named graphs has no representation. Recorded, not designed.

---

## `graph_id` writers and the scan that is supposed to catch them (verified 2026-09-16)

`tests/unit/test_graph_id_tightening.py` exists so that no writer can insert an
edge without naming `graph_id`. Its scan resolves one spelling of the table
name — `_edge_inserts()` at line 153 replaces `{self._t('rdf_edges')}` — and the
codebase has three:

| Spelling                     | Where                  | Seen by the scan |
| ---------------------------- | ---------------------- | ---------------- |
| `{self._t('rdf_edges')}`     | `_engine/*` mixins     | yes              |
| `{_table('rdf_edges')}`      | `cypher/translator.py` | **no**           |
| `{self._table('rdf_edges')}` | `bulk_loader.py`       | **no**           |

Writers currently inserting an edge with no `graph_id`, none of which the scan
can see: `cypher/translator.py:3789`, `:3800`, `:3850`, and `bulk_loader.py`'s
`INSERT INTO {self._table('rdf_edges')} (s, p, o_id, qualifiers)`. Rows they
write land in the NULL spelling of the default graph that no graph-aware reader
looks at.

Order of work, tests first: widen the substitution, add a case that fails if the
translator's spelling stops being covered, add a Python counterpart to
`test_no_objectscript_reader_matches_the_null_spelling_alone` (line 209) that
excludes the legitimate `IS NULL` in the migration at `schema.py:423` — then fix
the four writers.

### Two more `graph_id` predicates to reconcile

- `schema.py:696` (`rdf_edges_with_graph` bulk template) dedupes with
  `(graph_id = ? OR (graph_id IS NULL AND ? IS NULL))`, which treats `NULL` and
  `''` as different graphs. `COALESCE(graph_id, '') = COALESCE(?, '')` is the
  spelling every other reader uses.
- `schema.py:688` (`nodes_with_graph`) probes `WHERE node_id = ?` without
  `graph_id`, so the same `node_id` in a second graph is treated as already
  present. Harmless only while `UNIQUE (node_id)` stands — see above — so fix it
  with that decision, not before.
- `schema.py:383` adds `graph_id VARCHAR(256) %EXACT NULL` on the upgrade path
  while a fresh install creates `NOT NULL DEFAULT ''` (`schema.py:480` for
  `nodes`). `tighten_graph_id_column` repairs it afterwards; emitting the
  constraint up front would mean the two construction paths never disagree.

---

## `^KG` layout leftovers (verified 2026-09-16)

`Graph/KG/TraversalBuild.cls:291` reads
`$Order(^KG("out", gg, 0, mid, pred, o2))` — the `0` is a shard subscript that
spec-214 removed. Nothing lives at that subscript, so `Build2HopExactStats`'
merged fallback always returns `exact = 0` rather than a count. The class
header comment at `:2` and the note at `:51` also describe the pre-214 layout.

---

## Deployment and namespaces

A namespace has IVG's behaviour only once the `Graph.KG.*` classes are deployed
into it. A `Graph_KG` schema built by DDL alone — plain `CREATE TABLE`, or
`initialize_schema(auto_deploy_objectscript=False)` — is a shell: `graph_id`
nullable instead of required-with-default, primary key `edge_id` instead of `ID`,
no `Edge`/`Eraser`/`TemporalIndex`, and no schema migration ever reaches it.
Check with:

```sql
SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'Graph.KG.Edge'
```

`tests/integration/test_namespace_isolation.py` needs a deployed secondary
namespace named in `IVG_SECONDARY_NAMESPACE`; without it, 11 of its 15 cases
skip by design. See `README.md` §Non-USER Namespace Deployment.

---

## Loose ends

- `iris_vector_graph/schema.py:1157` still lists `Graph.KG.TestEdge` in
  `_SKIP_CLASSES`; there is no `iris_src/src/Graph/KG/TestEdge.cls` any more.
- `tests/e2e/test_betweenness_neighborhood_e2e.py:22` recompiles
  `Graph.KG.Edge` with `cuk-d` from a module fixture, which rewrites class state
  the rest of the session shares. Gate it or drop it.
- spec-221 Phase 3 docs are unticked: T006 (`create_edge_temporal()` docstring —
  `upsert=True` updates weight, bucket aggregates are not adjusted) and T007
  (`docs/USER_GUIDE.md` §Temporal Graph, same note).

---

## Investigated, not a defect

- **`Graph/KG/Ledger.cls:226`** — `%BuildIndices($ListBuild("uspo", "idxS", "idxP", "idxSP", "idxPOid"), 1, 0)`
  names exactly the five indices `Edge.cls` declares at lines 29–37. The
  `idx_edges_*` names that looked missing belong to the DDL path
  (`schema.py:169-170`), which only exists in namespaces that have no
  `Graph.KG.Edge` class for `%BuildIndices` to run against.

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
