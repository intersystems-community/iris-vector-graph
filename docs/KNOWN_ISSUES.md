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

### `test_embeddings_api.py` depends on the embedding column's leftover width

```text
FAILED tests/integration/test_embeddings_api.py::test_store_embedding_and_knn
E   iris_vector_graph.exceptions.EmbeddingIdentityConflict: dimension mismatch:
    recorded dimension 384 but this writer declares 768
```

The `engine` fixture (`tests/integration/conftest.py:34`) declares
`embedding_dimension=768` and then calls `initialize_schema`, which can only widen a
vector column while its table is **empty**. Any earlier test that left rows in
`Graph_KG.kg_NodeEmbeddings` sends the ALTER to `needs_manual_migration` — logged
`CRITICAL: ... is VECTOR(DOUBLE, 384) but the engine is configured for 768, and the
table is not empty (5 rows)` — and the write is then refused by the identity check.
The fixture's own teardown deletes the rows and restores width 128, so the failure only
appears when a _previous_ file's rows survive.

From a clean state both tests in the file pass:

```bash
# clear the embedding tables and the registry, then
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
  .venv/bin/pytest tests/integration/test_embeddings_api.py -p no:randomly -q
# 2 passed
```

This is container state, not a 3.2.0 regression. The pre-226 behaviour was no better —
the `CRITICAL` line already said every write of the configured width would be rejected;
226 only moved the rejection from an `SQLCODE -104` at INSERT to a named refusal. The
fixture should clear the embedding tables **before** it migrates, not only after.

### `conftest.py` cannot tell a stopped container from a missing one

`tests/conftest.py:56` does `IRISContainer.attach("ivg-iris-enterprise")`, and
`attach` **succeeds on a container that is `Exited`** — it resolves the name, not a
live instance. So the `pytest.fail` guard at `:76` never fires, and the connection
chain behind it (OrbStack DNS → container IP → `localhost:$IVG_PORT` →
iris_devtester's default) keeps walking until something answers. On 2026-09-18 that
was `irispython-dx-iris` at `localhost:1972`: a whole suite ran against another
project's instance, reported `547 passed`, and the only signal was two
`<CLASS DOES NOT EXIST> Graph.KG.Traversal` failures in `test_engine_status.py`.

The passes are the dangerous part — a green run against the wrong instance is not a
measurement. The guard needs to check the container is actually `running` (and that
the connection it hands back is the container's own port), not merely that the name
resolves. Until then, confirm `docker ps` shows `ivg-iris-enterprise` up **before**
trusting any gate number, and treat a run that produced unexplained
`CLASS DOES NOT EXIST` failures as void rather than partial.

A related tripwire already exists for `los-iris` at `:177-186`; nothing checks for
the dx instance.

### `irispython-dx-iris` holds a stale partial IVG deployment

The instance that answered above carries 22 of the 56 `Graph.KG.*` classes and a
`Graph_KG.nodes` table — enough to satisfy a connection and most schema reads, not
enough to run the suite. Its presence is why the fall-through above stays quiet
instead of erroring on the first query. It is not this project's container; do not
deploy to it and do not clean it up from here.

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

## Embeddings and vector width (verified 2026-09-18)

### A named graph cannot carry its own embedding model or dimension

There is no per-graph embedding anywhere in the schema, and this is a design fact,
not a missing filter:

- `kg_NodeEmbeddings` is `id VARCHAR(256) %EXACT PRIMARY KEY, emb VECTOR(DOUBLE, N),
metadata` — no `graph_id` column. The primary key is `id` alone, so a node has
  exactly one embedding row, and `Graph_KG.nodes` carries `UNIQUE (node_id)`, so the
  same node cannot exist twice to hold two.
- `kg_EdgeEmbeddings` is keyed `(s, p, o_id)` — also no `graph_id`.
- `embedding_config` (native IRIS `EMBEDDING()`) is set per engine instance
  (`engine.py:246`), not per graph.
- `kg_KNN_VEC` accepts `IN embeddingConfig VARCHAR(128)` (`schema.py:949`) and never
  reads it, so passing a different config per query changes nothing.

Two graphs in one namespace therefore share one vector space and one width. Writing
a 384-wide embedding for a node in graph A and a 768-wide one for the same node in
graph B is not "isolated per graph" — it is one row being overwritten. Multiple
models today means **one namespace per model**. The `metadata` column can record
which model produced a vector, but nothing enforces or filters on it.

**Still true in 3.2.0, deliberately.** Spec 226 makes the namespace-wide identity
explicit and enforced rather than per-graph: `Graph_KG.embedding_registry` reserves a
`graph_id VARCHAR(256) DEFAULT ''` column in its primary key, but 3.2.0 writes and reads
only `''`, meaning "all graphs". Per-graph models remain blocked by the two schema facts
above — no `graph_id` on either embedding table, and `uq_nodes_nodeid UNIQUE (node_id)` —
and lifting them is not what 226 does. The reserved column exists so that work can arrive
later without a registry migration.

### `get_procedures_sql_list(embedding_dimension=...)` is inert

The parameter is accepted and never interpolated; the generated `kg_KNN_VEC` is
`SELECT TOP :k n.id, VECTOR_COSINE(n.emb, TO_VECTOR(:queryInput, DOUBLE)) AS score`
with no declared length anywhere. This is why the pre-3.1.0 default of 1000 never
produced a procedure at the wrong width. Pinned by
`tests/unit/test_embedding_dimension_default.py::test_get_procedures_sql_list_does_not_actually_use_its_dimension`,
so wiring the width into `TO_VECTOR` will fail that test and require the docstring to
change with it. Left alone here because it changes generated stored-procedure SQL and
needs live verification against a populated table.

**Resolved as a deprecation in 3.2.0, not as a fix.** Live measurement showed the
three-argument `TO_VECTOR(:q, DOUBLE, n)` form pads or truncates the _query_ and then
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
3.2.0 adds is that the disagreement is now _recorded_ and the next write is _refused_
(`EmbeddingIdentityConflict`) instead of being attempted at a width the column cannot
take.

### A recorded width can go stale against the column it describes

**Open in 3.2.0.** `Graph_KG.embedding_registry.dimension` is a snapshot taken when the row
was written. `_sync_recorded_dimension` (`iris_vector_graph/_engine/schema.py:582`) carries
it forward only for tables the current `_migrate_vector_dimensions` call actually altered —
`for name in altered_names` at `:751-752`. A column that reaches its target width by any
other route leaves the old number recorded, and the refusal then blames the writer for a
width the column no longer declares:

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

When they disagree, delete the registry row and re-run `initialize_schema`; adoption re-reads
the live column declaration. The narrow fix is for adoption to reconcile a recorded width
against the column on every `initialize_schema` rather than only after an ALTER, which is a
behaviour change (it would silently rewrite a recorded width) and so is deliberately not in
3.2.0.

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
