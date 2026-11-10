# openCypher TCK: how the number is produced

This page explains how to reproduce IVG's openCypher TCK figure. It also says
what that figure measures, and what it does not.

## What is run

- **Suite.** The upstream openCypher TCK, vendored as the git submodule
  `vendor/opencypher`. It is pinned at commit
  `677cbafabb8c3c5eed458fd3b1ec0daec8d67d23` and has 3897 scenarios across 37
  areas, counting each Scenario Outline row as one scenario. No feature file is
  edited, and none is filtered out with an include list.
- **Harness.** `behave` with the step definitions in `tests/tck/steps/` and
  hooks in `tests/tck/environment.py`. Each scenario runs against a live IRIS
  instance through `IRISGraphEngine.execute_cypher`.
- **IRIS.** Container `ivg-iris-enterprise`, image
  `irishealth:2026.3.0AI.113.0`, superserver on host port 31972. A release
  figure is also measured on IRIS Community 2026.1 (the repo's
  `docker-compose.yml`), and on each platform under both
  intersystems-irispython drivers, 5.4.0 (the repo's `.venv`) and 5.4.1 (what
  a fresh install gets): the two platforms fail differently (Match4 [4] failed
  on 2026.3 only), and so do the drivers (see `docs/dev/driver-drift.md`).
  Point the scripts at Community with `IVG_TEST_CONTAINER` and `IVG_PORT`, and
  run one namespace at a time there: Community limits connections.
- **IVG.** The commit being measured. `scripts/tck/run_all.sh` records it in
  `RUN_INFO` and marks it `(dirty)` when the working tree has uncommitted
  changes.

## Quick start

```bash
git submodule update --init vendor/opencypher
scripts/enterprise-container.sh up

# one-off: create a namespace for TCK runs and deploy this checkout into it
scripts/tck/setup_namespace.sh TCKA --create

# the full suite, then a summary and per-scenario TSV
IVG_TCK_NAMESPACE=TCKA scripts/tck/run_all.sh /tmp/tck_run
IVG_TCK_NAMESPACE=TCKA IVG_TCK_MULTIGRAPH=0 scripts/tck/run_all.sh /tmp/tck_run_mg0

# compare the two runs
.venv/bin/python scripts/tck/summarize.py diff \
  /tmp/tck_run/results.tsv /tmp/tck_run_mg0/results.tsv
```

To gate a commit without touching the working tree, extract that commit into
its own directory and point `IVG_REPO` at it. Python-only changes need no
redeploy. After a `.cls` or UDF change, rerun `setup_namespace.sh` on every
namespace first.

```bash
S=$(git rev-parse --short HEAD); D=~/.cache/ivg-tck/src_$S
mkdir -p $D && git archive $S | tar -x -C $D && ln -sfn $PWD/.venv $D/.venv
# git archive leaves out the submodule that tests/tck/features points into
rmdir $D/vendor/opencypher && ln -sfn $PWD/vendor/opencypher $D/vendor/opencypher
IVG_REPO=$D IVG_TCK_CAPTURE=~/.cache/ivg-tck/cap_$S \
  scripts/tck/run_sharded.sh ~/.cache/ivg-tck/run_$S TCKB TCKC TCKD TCKE
.venv/bin/python scripts/tck/summarize.py diff base.tsv ~/.cache/ivg-tck/run_$S/results.tsv
.venv/bin/python -m scripts.tck.rescore ~/.cache/ivg-tck/cap_$S --mode typed
```

An extracted tree has no `.git`, so `RUN_INFO` records its commit as blank and
`(dirty)`. Keep the commit in the directory name.

`tests/tck/features` is a relative symlink into
`vendor/opencypher/tck/features` (since 4.4.1; it was committed with an
absolute path before, which a source archive could not extract). It resolves
once the submodule is initialised.

Behave has to reach the features through `tests/tck/`, because it finds
`steps/` and `environment.py` by walking up from the path it is given.

## Scripts

All scripts live in `scripts/tck/`.

- `setup_namespace.sh <NS> [--create]` creates a namespace if asked, then
  deploys classes, schema and UDFs into it.
- `deploy_namespace.py <NS>` is the deploy step: TCP class load and compile,
  schema, `CY_*` UDFs.
- `run_all.sh <out> [area ...]` runs every area, or only the named ones, with
  one `behave` process per area.
- `summarize.py <out> [out.tsv]` prints the summary line and writes the
  per-scenario TSV.
- `run_sharded.sh <out> <NS> [NS ...]` runs the whole suite split across
  several namespaces in parallel. Areas are dealt out largest first, so the
  shards finish together. It writes one `results.tsv`. With nine namespaces a
  full run takes about 8 minutes.
- `summarize.py diff A.tsv B.tsv` lists scenarios that pass in one TSV and not
  in the other.
- `compare.sh <area> ...` runs some areas and prints `REG` and `GAIN` lines
  against `$TCK_BASE`.
- `python -m scripts.tck.rescore <capture_dir> --mode default|typed|lenient`
  rescores a capture (`IVG_TCK_CAPTURE`, below) offline, with no IRIS
  connection. `default` should reproduce the verdicts a run recorded before
  4.6.0 R311, and any mismatch it lists is a bug in the capture. `typed` is the
  live harness's rule since R311: each result value must have the expected
  Python type. `lenient` applies the pre-spec-229 rules.

Every script takes its settings from the environment. None of them has a path
hard-coded.

- `IVG_REPO`: checkout to run. Default: the scripts' own repo.
- `IVG_TCK_NAMESPACE`: namespace the harness connects to. Default `USER`.
- `IVG_TEST_CONTAINER`: IRIS container. Default `ivg-iris-enterprise`.
- `IVG_PORT`: host superserver port. Default `31972`. Under OrbStack the
  container's own port 1972 is used instead.
- `IVG_TCK_MULTIGRAPH`: `0` runs with multigraph mode off. Default on. See
  below.
- `IVG_TCK_RUN_IGNORED`: `1` also runs scenarios tagged `@ignore` upstream.
- `IVG_TCK_LENIENT`: `1` restores the pre-spec-229 scoring, which reproduces
  old baselines such as 3894/3897. Under it an error counts as an empty result,
  the side-effect steps pass without measuring, and an answer matches by its
  text spelling (R311's type rule is off). Default off.
- `IVG_TCK_CAPTURE`: a directory. For each area the harness writes a JSONL file
  with one record per scenario: query, expected and actual rows (each cell
  tagged with its Python type), side-effect counts, the error, and the verdict.
  `rescore.py` reads it.
- `PYTHON`: interpreter. Default `<repo>/.venv/bin/python`. A git worktree
  falls back to the main checkout's venv.
- `TCK_TIMEOUT`: seconds per area. Default `900`.
- `TCK_LOCK`: `1` (default) holds `/tmp/ivg_iris_<NS>.lock` for the run. Set
  `0` if the caller holds it.
- `TCK_BASE`: baseline TSV for `compare.sh`.

Each namespace has its own lock, so separate checkouts can run at the same
time in separate namespaces. `run_all.sh`, `compare.sh` and
`setup_namespace.sh` all take the lock themselves. Do not wrap them in another
`flock` on the same file, or the run will wait on itself.

### Namespace setup

`setup_namespace.sh <NS> --create` creates the database directory
`/usr/irissys/mgr/<ns>/`, the database, and namespace `<NS>`, with globals and
routines both in that database. It runs through
`docker exec <container> iris session IRIS -U %SYS`. The command is idempotent,
and it refuses `USER` and `%SYS`.

It then deploys the checkout in three steps:

1. Every `.cls` under `iris_src/src` is written into the container, loaded
   over TCP, and compiled as one list.
2. `IRISGraphEngine.initialize_schema(auto_deploy_objectscript=False)` runs,
   which is what the harness itself calls.
3. The `CY_*`, `LIST_CONCAT` and `STR_SPLIT` SQL functions are created.

Two errors are expected and harmless:

- `Graph.KG.MCPToolSet` fails to compile because `Graph.KG.MCPTools` is not in
  `iris_src`.
- `STR_SPLIT` fails with `-300`. It does so in `USER` as well.

Rerun the setup after any `.cls` change, and after any UDF change in
`iris_vector_graph/schema.py`.

## Multigraph on and off

The TCK assumes a property graph, in which two relationships can share a type
and both endpoints. IVG's default graph keeps one edge per
`(source, type, target)`. Spec 234 added a per-graph multigraph mode, and
`tests/tck/environment.py` turns it on for the TCK's default graph in
`before_all`. It turns it back off in `after_all`.

The production default is single-edge. The headline figure is therefore
measured in a mode that users get only when they ask for it. Every published
figure should come with a second run using `IVG_TCK_MULTIGRAPH=0`, and the list
of scenarios that pass only with the mode on.

### Latest runs

The typed runs (R311: an answer matches only with its Python type) are at IVG `9ca0edca` (branch `460-fixes67`,
product code; `99514e62` adds only the harness's reconnect fix), TCK `677cbafa`, multigraph on, native executor on,
enterprise `irishealth:2026.3.0AI.113.0`. Under driver 5.4.0 every area passed but `return`, and under 5.4.1 every area
but `call`: each lost its IRIS connection mid-run while namespaces were created and dropped on the container, and the
harness then had no working reconnect. Both areas were rerun at `99514e62` under both drivers, 115 / 115. So 3896 /
3896 under each driver, from two runs each; no single typed full run is clean yet, and Community 2026.1 has not been
run typed.

| IRIS                                     | Driver | Typed, passed / eligible                    |
| ---------------------------------------- | ------ | ------------------------------------------- |
| Enterprise `irishealth:2026.3.0AI.113.0` | 5.4.0  | 3896 / 3896 (3781 + `return`, `call` rerun) |
| Enterprise `irishealth:2026.3.0AI.113.0` | 5.4.1  | 3896 / 3896 (3781 + `return`, `call` rerun) |

The untyped runs below passed 188 scenarios only because the harness decoded text (see "Python types" under "What a
pass does and does not verify").

The 4.6.0 runs are at IVG `2406c7dc` (branch `460-fixes64`), TCK `677cbafa`,
multigraph on, native executor on (the default), strict harness with setup
failures failing the scenario (R310), one scenario ignored upstream. Each was
run from a `git archive` extract into namespaces deployed with
`setup_namespace.sh`.

| IRIS                                      | Driver | Passed / eligible |
| ----------------------------------------- | ------ | ----------------- |
| Enterprise `irishealth:2026.3.0AI.113.0`  | 5.4.0  | 3896 / 3896       |
| Enterprise `irishealth:2026.3.0AI.113.0`  | 5.4.1  | 3896 / 3896       |
| Community `iris-community:2026.1` (234.1) | 5.4.0  | 3896 / 3896       |
| Community `iris-community:2026.1` (234.1) | 5.4.1  | 3896 / 3896       |

On the 4.6.0 release head before R310 (`99ca2807`), enterprise 2026.3 and
driver 5.4.0, the old harness scored 3895 / 3896 and the honest one (`500ecd40`,
the same product code) 3893 / 3896.
One scenario had a failing setup, Match4 [4], which fails under either harness:
its setup failed `-400 <SUBSCRIPT>`, so it ran on an empty graph. Two passed
only because their teardown error was dropped: Create4 [2] and Return6 [4],
whose `DETACH DELETE` of their nodes failed `-202`. All three are fixed (see
CHANGELOG, 4.6.0). On Community 2026.1 with driver 5.4.0 the release head
scored 3896 / 3896 under the old harness, with no setup error.

Every recorded pass before 4.6.0 was taken with setup errors dropped. At the
4.6.0 release head, on enterprise 2026.3 with driver 5.4.0, that hid the three
scenarios above and no other; the release head was not measured that way under
driver 5.4.1.

The 4.3.0 run is at IVG `f7a8c581`, in the 4.3.0 release range, with the
native executor on (the default), each answer scored against the TCK's own
expected results; the harness does not set shadow mode. Its `RUN_INFO` records
`03c6ae2a (dirty)`: the working tree held exactly the four files `f7a8c581`
then committed (the run finished at 17:03:13Z, the commit 18 seconds later).
Shadow mode (native held to the SQL path, below) gave 3896 / 3896 when spec 253
was completed, with and without the parallel path forced. The two earlier runs
are at IVG `937d4b7`, before native Cypher was the default, so they score the
SQL path alone. All three are at TCK `677cbafa`, on
`irishealth:2026.3.0AI.113.0`, scored by the strict default harness, with one
scenario ignored upstream.

From 4.4.0 a TCK run scores compiled plans only with `^IVG.CypherGen("mode") =
"sync"` in the run's namespaces: in the default `job` mode a shape's first run
is interpreted. Few TCK reads are compiled even then (5 shapes in the 4.4.0 run
below), because most of its reads are shapes the native planner leaves to the
SQL path; the compiled walk is held to the interpreter by
`tests/e2e/test_257_compiled_plans_e2e.py` instead.

| IVG, path                   | Multigraph | Passed / eligible | Typed        |
| --------------------------- | ---------- | ----------------- | ------------ |
| `0e69c125`, native (`job`)  | on         | 3896 / 3896       | not rescored |
| `0e69c125`, native (`sync`) | on         | 3896 / 3896       | not rescored |
| `f7a8c581`, native          | on         | 3896 / 3896       | not rescored |
| `937d4b7`, SQL              | on         | 3896 / 3896       | 3896 / 3896  |
| `937d4b7`, SQL              | off        | 3889 / 3896       | not rescored |

At `937d4b7` with the mode off, seven scenarios fail. Each needs two
relationships of the same type between the same pair of nodes:

- `Create3 [7]` WITH-CREATE: nodes are not created when aliases are applied to
  variable names multiple times
- `Create4 [2]` Many CREATE clauses
- `Match6 [14]` Named path with undirected fixed variable length pattern
- `Merge5 [3]` Matching two relationships
- `Merge5 [5]` Filtering relationships
- `Merge5 [6]` Creating relationship when all matches filtered out
- `Merge5 [21]` Do not match on deleted relationships

Earlier figures are kept in `CHANGELOG.md`. The lenient harness scored r11 at
3894/3897 and the v2.6.0 run at 2930/3897.

#### How the native path is held to the SQL path

From 4.3.0 a read the planner supports runs in `Graph.KG.Cypher.Exec` over `^KG`
instead of as translated SQL; everything else still takes the SQL path. In
shadow mode (`IVG_CYPHER_SHADOW=1`, or `engine.cypher_shadow = True`) every
natively planned query also runs on the SQL path, and
`iris_vector_graph.cypher.native_shadow.compare` raises `NativeMismatch` unless
the answers agree: the same columns, values compared with their types (`1` and
`"1"` differ), rows in the same order where the query orders them completely
and as a multiset otherwise. Where a `LIMIT` or `SKIP` keeps rows the query
does not order completely, the row counts must match and every native row must
be a row of the SQL answer taken without the limit. A SQL-path error where
native answered is a mismatch too. A difference therefore surfaces in the run as
a raised `NativeMismatch`, not as a quietly changed answer.

## Result format

`run_all.sh` writes the following into `<out>`:

- `<area>/TESTS-*.xml`: behave's JUnit output.
- `<area>.log`: behave's stdout and stderr.
- `RUN_INFO`: repo, commit, namespace, container, multigraph setting and times.
- `results.tsv`: one line per scenario, sorted by key, in the form
  `<verdict>\t<key>`.

The key is `<feature file stem>.<Feature name>::<scenario name>`. For Outline
rows, the scenario name ends with behave's `-- @<examples>.<row>` suffix, and
trailing whitespace is stripped. The verdict is one of:

| Verdict | Meaning                                       | Counted        |
| ------- | --------------------------------------------- | -------------- |
| `1`     | passed                                        | pass, eligible |
| `0`     | failed, errored, or untested (area timed out) | eligible       |
| `S`     | skipped by the harness, not for `@ignore`     | eligible       |
| `I`     | `@ignore` upstream at any level               | neither        |

Older TSVs have only `1` and `0`, and they read the same way. The summary line
is:

```text
<passed> / <eligible> eligible (<n> ignored upstream; <total> scenarios in total)
```

### Upstream `@ignore`

At `677cbaf`, exactly one scenario carries `@ignore`:
`Graph5 [2] Single-labels expression on relationships`. Its feature file
explains that "this scenario does not work in Cypher". The harness skips it
(`before_scenario`), and the summarizer excludes it from both the numerator and
the denominator, whatever the scenario did. `summarize.py` reads the tag from
the feature files with behave's parser, so an older run that executed the
scenario and counted it as a pass is corrected when it is re-summarised.

The vendored features also carry `@skipGrammarCheck` (22 scenarios) and
`@skipStyleCheck` (11 scenarios). Both are for openCypher's own grammar and
style tooling, not for engines. Those scenarios run and count normally.

### Environment-dependent scenarios

A few scenarios expect an answer that depends on the host, not on IVG. They are listed in
`tests/tck/environment_dependent.txt`, one per line as `<scenario key> | <reason>`; `summarize.py` refuses an entry
with no reason. A listed scenario that fails is still eligible and failed, so the number stays honest, and the
summary also names it on its own line:

```text
environment-dependent failures: 1 (tests/tck/environment_dependent.txt)
  ENV Temporal2.Temporal2 - Create Temporal Values from a String::[6] ...
```

List a scenario there only when both answers are right for their host, never to hide an IVG defect. None is listed
now. Until 4.7.0 one was:

- **Temporal2 [6]**, `datetime('1818-07-21T21:40:32.142[Europe/Stockholm]')`, which expects `+00:53:28`. IVG read a
  named zone with Python's `zoneinfo`, which reads the host's tz database (`/usr/share/zoneinfo`) before the
  `tzdata` package. A mainline tz build (macOS 2026e, the `tzdata` wheel 2025.2 and 2026.6) links Europe/Stockholm to
  Europe/Berlin and answers `+00:53:28`; Debian and Ubuntu build their `tzdata` with `backzone`, which keeps
  Stockholm's own 1818 offset, and answered `+01:12:12` (Ubuntu 24.04 and `python:3.12-slim`, 2026e). IVG now reads
  the `tzdata` wheel, a dependency, first (`iris_vector_graph/_tz.py`), so every host answers as the wheel does, and
  the scenario passes on Debian too. A later tz release can still move a historical offset like this one: that is a
  change of the wheel's version, the same on every host.

## What a pass does and does not verify

Since spec 229 the harness is strict by default. At `937d4b7` a pass checks the
following:

- **Side effects.** `And no side effects` and `And the side effects should be:`
  compare snapshots of nodes, relationships, labels and properties taken before
  and after the query. A `CREATE` that writes one node too many fails.
- **Errors.** `Then a <Kind> should be raised at <phase>: <detail>` maps the
  exception onto an openCypher kind, phase and detail
  (`tests/tck/steps/errors.py`). An error with no mapped kind fails. A query
  that raises does not pass `the result should be empty`, and it fails any step
  that expects a result table.
- **Columns.** The result's column names must equal the expected ones, in
  order. An extra column fails.
- **Nodes and relationships.** Labels and property keys must match exactly,
  not as a subset. Property values are also read back from storage and
  compared.
- **Paths.** Every node on the path is read back by id, and every step must
  resolve to a stored relationship with the expected type, direction and
  properties.
- **`@ignore`.** Graph5 [2] is excluded from both the numerator and the
  denominator.

- **The graph was built.** A `Given having executed:` query that raises, or
  answers with `IVGResult.error` set, fails the scenario with the query and
  the error (`SetupFailed`). So does a named graph that cannot be counted or
  built, and a `^KG` sync after setup that fails. A teardown that cannot
  remove a scenario's rows fails that scenario (`TeardownFailed`), and a
  failed schema setup, session flush or multigraph switch stops the run. No
  setup error counts as benign: each scenario builds its own graph under a
  new isolation label, so "already exists" cannot happen, and no setup error
  of that kind appeared in a full run on either platform. Until 4.6.0 (R310) every setup query ran under
  `except Exception: pass`, so a scenario whose setup failed ran on an
  unbuilt graph. `tests/unit/tck/test_tck_setup_honest.py` lists every
  handler left in `tests/tck` that drops an exception, with the reason each
  one is safe.

- **Python types (4.6.0 R311).** An answer matches only with the expected
  Python type, at every level: a list must be a `list`, a map a `dict`, an
  integer an `int`, a float a `float` and a boolean a `bool`. The text that
  spells one does not match, and neither does an IRIS `Decimal`: integer `1`,
  float `1.0`, text `'1'` and boolean `true` are four different values. Node,
  relationship and path values keep their own readers (a whole node is three
  columns of documented text). Before R311 a list or map expectation decoded any
  string it met, so a list answered as its JSON text passed: rescored, 188 of
  the 3896 scenarios passed only that way at the R310 head (`da666988`), under
  both drivers. `rescore.py --mode typed` applies this rule to an older
  capture.

The harness still does not check the following:

- **Error detail, when IVG's message names none.** A bare parse error is
  matched on kind and phase alone. See
  [TCK error matching](notes/known-issues-history.md#tck-error-matching-verified-2026-09-26).
- **Runtime vs compile time, in one direction.** A scenario that expects a
  runtime error accepts one raised at translation, because IVG binds parameters
  and folds constants early.
- **Types of stored property values.** `rdf_props.val` carries no type tag, so
  an integer property stored as `'1'` matches `1`. Result values are typed;
  property values inside node and relationship values are not.
- **Isolation rewrites queries.** Each scenario's setup and queries get a
  per-scenario label (`TCK_<hex>`), injected by regex, so that scenarios do not
  see each other's data. Such a rewrite can change what a query means.
- **Multigraph mode is on** by default. See above.

Quote a figure from this suite with the IVG commit, the TCK commit, the
multigraph setting, and whether it is typed.

The old "133/133", "47%→76%→85%" and "85%→91.7%" CHANGELOG lines measured an
internal 133-scenario catalogue, not this suite. The v2.x
"2930/3897 (75.2%)" line was scored by the lenient harness.
