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
- `summarize.py diff A.tsv B.tsv` lists scenarios that pass in one TSV and not
  in the other.
- `compare.sh <area> ...` runs some areas and prints `REG` and `GAIN` lines
  against `$TCK_BASE`.

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

These results measure **result conformance**: the rows a query returns. They do
not measure write conformance. A pass does **not** mean the scenario's full
expectation was checked.

As of the harness at commit `b82049e`:

- **Side effects are not checked.** `And no side effects` and
  `And the side effects should be:` are both no-op steps. About 3200 scenarios
  assert one or the other. A `CREATE` that wrote the wrong number of nodes, or a
  query that should not write but did, can still pass.
- **Error kind and phase are not checked.** For the 695 error scenarios, any
  exception passes. So does an error string on the result. `SemanticError`,
  `ProcedureError` and `ConstraintVerificationFailed` accept any `Exception`,
  and "at compile time" and "at runtime" are not told apart.
- **"The result should be empty" passes when the query raised.**
- **Value comparison is loose.** Here is how comparison currently works:
  - An expected node matches an actual node when the expected labels and
    properties are a subset of the actual ones.
  - Columns in the result that the expected table does not name are not
    rejected.
  - Paths are compared by node labels and relationship types, not by identity.
  - Some null-like values compare equal to empty lists and empty strings.
- **Isolation rewrites queries.** Each scenario's setup and queries get a
  per-scenario label (`TCK_<hex>`), injected by regex, so that scenarios do not
  see each other's data. Such a rewrite can change what a query means.
- **Multigraph mode is on** by default. See above.

Spec 229 (`specs/229-tck-write-verification`) is making side effects, error
kinds, empty results and node, path and column comparison strict. The work is
in progress on other branches. When it lands, the figure will drop, and the
result-conformance and write-conformance numbers will be reported separately.
Until then, quote any figure from this suite as "result conformance; side
effects and error kinds not verified", together with the IVG commit, the TCK
commit, and the multigraph setting.

The old "133/133" and "85%→91.7%" CHANGELOG lines measured an internal
133-scenario catalogue, not this suite.
