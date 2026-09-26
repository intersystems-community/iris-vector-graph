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
  `irishealth:2026.3.0AI.113.0`, superserver on host port 31972.
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
IVG_REPO=$D IVG_TCK_CAPTURE=~/.cache/ivg-tck/cap_$S \
  scripts/tck/run_sharded.sh ~/.cache/ivg-tck/run_$S TCKB TCKC TCKD TCKE
.venv/bin/python scripts/tck/summarize.py diff base.tsv ~/.cache/ivg-tck/run_$S/results.tsv
.venv/bin/python -m scripts.tck.rescore ~/.cache/ivg-tck/cap_$S --mode typed
```

An extracted tree has no `.git`, so `RUN_INFO` records its commit as blank and
`(dirty)`. Keep the commit in the directory name.

`tests/tck/features` is a symlink into `vendor/opencypher/tck/features`. In
this repository the symlink is committed with an absolute path under one
developer's home directory. On any other machine, repoint it after
initialising the submodule:

```bash
ln -sfn ../../vendor/opencypher/tck/features tests/tck/features
```

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
  connection. `default` should reproduce the recorded verdicts, and any
  mismatch it lists is a bug in the capture. `typed` also requires each result
  cell to have the right Python type. `lenient` applies the pre-spec-229 rules.

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
  old baselines such as 3894/3897. Under it an error counts as an empty result
  and the side-effect steps pass without measuring. Default off.
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

Both runs are at IVG `937d4b7`, TCK `677cbafa`, on `irishealth:2026.3.0AI.113.0`,
scored by the strict default harness.

| Multigraph | Passed / eligible | Typed        | Ignored upstream |
| ---------- | ----------------- | ------------ | ---------------- |
| on         | 3896 / 3896       | 3896 / 3896  | 1                |
| off        | 3889 / 3896       | not rescored | 1                |

With the mode off, seven scenarios fail. Each needs two relationships of the
same type between the same pair of nodes:

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

Typed scoring (`rescore.py --mode typed`) also requires every top-level cell to
have the right type. For example, integer `1`, float `1.0`, text `'1'` and
boolean `true` count as different values.

The harness still does not check the following:

- **Error detail, when IVG's message names none.** A bare parse error is
  matched on kind and phase alone. See
  [TCK error matching](KNOWN_ISSUES.md#tck-error-matching-verified-2026-09-26).
- **Runtime vs compile time, in one direction.** A scenario that expects a
  runtime error accepts one raised at translation, because IVG binds parameters
  and folds constants early.
- **Types of stored property values.** `rdf_props.val` carries no type tag, so
  an integer property stored as `'1'` matches `1`. Top-level cells are typed;
  property values inside nodes are not.
- **Isolation rewrites queries.** Each scenario's setup and queries get a
  per-scenario label (`TCK_<hex>`), injected by regex, so that scenarios do not
  see each other's data. Such a rewrite can change what a query means.
- **Multigraph mode is on** by default. See above.

Quote a figure from this suite with the IVG commit, the TCK commit, the
multigraph setting, and whether it is typed.

The old "133/133", "47%→76%→85%" and "85%→91.7%" CHANGELOG lines measured an
internal 133-scenario catalogue, not this suite. The v2.x
"2930/3897 (75.2%)" line was scored by the lenient harness.
