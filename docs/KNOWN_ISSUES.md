<!-- markdownlint-disable MD013 -->

# Known Issues

Open findings carried into the next release, each verified against the working tree
on the date in its heading. A finding stays here until it is fixed or ruled out;
ruled-out entries move to [Investigated, not a defect](#investigated-not-a-defect)
with the evidence, so the same suspicion is not re-raised.

---

## Test and build environment (verified 2026-09-16)

### The integration suite cannot complete in one pytest process

`pytest tests/integration` dies with `Fatal Python error: Segmentation fault`
(exit 139) roughly 30% of the way in. It is not one query shape: the crash
lands in `test_cypher_multi_type.py`, and with that file ignored it lands in
`test_cypher_rd.py:6` on a plain single-node `MATCH`. The driver is the thing
that gives out, after ~1000 tests share one session-scoped connection.

Run the suite as separate processes to get a real result:

```bash
files=($(ls tests/integration/test_*.py)); half=$((${#files[@]}/3))
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m pytest "${files[@]:0:$half}" -q -p no:randomly
# ...repeat for the remaining two thirds
```

Until this is fixed, §2 of `PRE_RELEASE_CHECKLIST.md` cannot be satisfied by the
single command it prints.

### 55 integration tests fail against the v3 API, all of them pre-existing

Run as chunks, the suite gives 51 failures and 4 errors. The same 55 node IDs
fail at the `v3.0.1` tag (checked in a worktree at `15f3d78`, `45 failed, 10
errors` — the six that differ are teardown errors there and assertion failures
now), so nothing in `a0b30ca` or `07d2cae` introduced them. What they have in
common is a test written against a pre-v3 API:

| Cause                                                        | Count | Example                                                       |
| ------------------------------------------------------------ | ----- | ------------------------------------------------------------- |
| `IVGResult.get('rows')` — removed, use `.rows` or `['rows']` | 20    | `test_query_engine_special.py`                                |
| `DeleteResult` compared to `int`                             | 6     | `test_nodes_edges_deep.py:413`                                |
| Reification `SQLCODE -29`, field not in the applicable table | 5     | `test_schema_and_engine_procs.py:158`                         |
| LazyKG results keyed by node ID, not label                   | 4     | `test_lazykg_algorithms.py:59`                                |
| `from iris_vector_graph._engine.vector import _table`        | 2     | `test_vector_engine_deep.py:190`                              |
| FK on `rdf_edges` not enforced, so no exception raised       | 3     | `test_nodepk_constraints.py:152`, `test_diabolical_qa.py:200` |
| Remaining one-offs (embed queue, PPR, chunked Arno, RRF)     | 15    | see the recipe below                                           |

Reproduce the list:

```bash
grep -hE "^(FAILED|ERROR) " chunk*.log | sed 's/^[A-Z]* //; s/ - .*//' | sort -u > failing.txt
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/python -m pytest $(tr '\n' ' ' < failing.txt) -q -p no:randomly --tb=line
```

The FK group is the one worth reading as a product question rather than test
rot: `create_edge` to a nonexistent node returns `True`, and inserting an edge
with a missing endpoint raises nothing.

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
