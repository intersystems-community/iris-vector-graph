<!-- markdownlint-disable MD013 -->

# Research: One IVG Schema per namespace (spec 262)

Measured 2026-11-03 on `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`, port 31972), namespace `IVGSWAP`. Scripts and
raw output: `~/.cache/ivg-bench/spec262/` (`r1_bench.py`, `r1.json`, `r2_probe.py`, `r4d.py`).

## R1. How the ObjectScript classes follow the recorded schema

**Decision**: the deploy rewrites the class sources for the namespace's IVG Schema before it loads them, so every
statement stays as it is written today (embedded `&sql` or a prepared `%SQL.Statement`) and names the recorded schema
at compile time. No class reads the schema at run time.

**Rationale**: FR-020a allows 5%. Moving the embedded statements to run-time SQL costs far more than that. Server-side,
20,000 iterations, median of 5 runs, a 20,000-row table keyed `(node_id, graph_id)`:

| Statement | Embedded `&sql` | `%SQL.Statement`, prepared once | `%SQL.Statement`, prepared per call |
| --- | --- | --- | --- |
| Keyed `SELECT` (the FK / lookup shape) | 1.40 µs | 5.80 µs (4.1×) | 15.62 µs (11.1×) |
| `INSERT` + `DELETE` pair | 13.29 µs | 18.95 µs (1.4×) | 39.48 µs (3.0×) |

The classes hold 81 embedded `&sql` statements in 6 files (Eraser, TemporalIndex, the ledger, the adjacency writers),
which are the hot paths, and 80 dynamic prepares in 17 files whose SQL text names `Graph_KG` as a literal. The rewrite
treats both the same way and adds nothing at run time.

The deploy already sends every source over the connection and compiles it in the target namespace
(`_engine/class_deploy.py:upload_and_compile`), and one namespace has one IVG Schema (ADR-0016), so a per-namespace
compile is what happens today. The fingerprint (`^IVG.Deploy("fingerprint")`) is taken over the rewritten sources, so a
schema change forces a recompile.

**What the rewrite replaces** (an allow-list, not a blanket replace):

- The SQL schema `Graph_KG` where it is followed by `.` and an IVG table, function, procedure or view name.
- The table classes' package `Graph.KG` where it is followed by an IVG table class name (`rdfedges`, `rdflabels`,
  `rdfprops`, `nodes`, …) or a function class (`func…`, `prockg…`), for example
  `%Dictionary.CompiledIndex` ids (`Adjacency.cls:523`), `$ClassMethod("Graph.KG.rdfedges", "%BuildIndices", …)`
  (`TraversalBuild.cls:131`) and `%Dictionary.CompiledStorage` lookups (`Cypher/Props.cls:31`).
- Never the code classes (`Graph.KG.Eraser`, `Graph.KG.Adjacency`, …), which stay in `Graph.KG` whatever the schema.

A unit test rewrites every packaged source for `IVGALT` and asserts that no allow-listed `Graph_KG.`/`Graph.KG.` reference
survives and that no code-class reference changed; SC-004 counts the survivors in the deployed classes.

**Alternatives considered**:

- Run-time dynamic SQL everywhere (`..S()` returning the record): measured above, 1.4× to 11× per statement.
- A macro (`$$$IVGSchema`) resolved at compile time from an include file written per namespace: equivalent cost, but
  embedded `&sql` does not expand macros inside table names, so it needs the rewrite anyway.
- Keeping `Graph_KG` hard-coded and supporting only the default: rejected by ADR-0016.

## R2. How IRIS names what a schema creates

**Decision**: take IRIS's mapping as given and validate against it at `initialize_schema()`.

Measured by creating a table and a function in three schemas:

| Schema | Table class | Function class | Call |
| --- | --- | --- | --- |
| `IVGALT` | `IVGALT.probet` | `IVGALT.funcPROBEFN` | `SELECT IVGALT.PROBE_FN(1)` → 2 |
| `MY_APP` | `MY.APP.probet` | `MY.APP.funcPROBEFN` | 2 |
| `R262_Fn` | `R262.Fn.probet` | `R262.Fn.funcPROBEFN` | 2 |

So the package is the schema with `_` turned into `.`, a function `X_Y` compiles to `<package>.funcXY`, and schema names
are not case-sensitive (`SELECT ivgalt.probe_fn(41)` → 42).

Consequences for validation (FR-004, spec Edge Cases):

- A schema whose package is, or is under, one of IVG's code packages (`Graph.KG`, `IVG`, `IVGC`) is refused, except the
  default `Graph_KG` itself. `IVG` → package `IVG` would put table classes beside `IVG.CypherEngine`.
- A schema whose package already holds classes IVG did not create is refused, naming one of them.
- Two IVG function names must not compile to the same class (`JSON_VALUE` and `JSONVALUE` both give `funcJSONVALUE`); a
  unit test checks the generated names.
- The record stores the name upper-cased for comparison and as given for display.

## R3. Where the schema record lives

**Decision**: `^IVG.Deploy("schema")`, beside `^IVG.Deploy("fingerprint")`.

**Rationale**: `^IVG.Deploy` already is the namespace's record of what IVG deployed there, is written by the same code
path, and is in the backup list. The record holds the name as given; `^IVG.Deploy("schema", "recorded")` holds the
`$ZDateTime` it was written and the IVG version. The ObjectScript classes never read it (R1); the Python engine reads it
once per connection (FR-006, FR-006a).

**Alternatives considered**: a `Graph_KG.ivg_config` table (cannot hold the schema's own name before the schema exists);
a new `^IVG.Config` global (a second place for deploy state).

## R4. Copying grants on legacy objects (FR-010a)

**Decision**: read the grants through `%Library.SQLCatalogPriv:SQLAllPrivileges(objectFilter)`, keep the rows whose
`GRANTED_VIA` is `Direct`, and re-grant each on the replacement with `%SYSTEM.SQL.Security.GrantPrivilege`
(`GrantPrivilegeWithGrant` when `HAS_GRANT_OPTION` is `Yes`).

Measured: after `GRANT EXECUTE ON R262G.PROBE_FN TO r262_role`, the query returns
`['r262_role', 'ROLE', 'EXECUTE', 'OBJECT', 'STORED PROCEDURE', 'R262G.PROBE_FN', …, 'No', 'Direct', 0]`, beside rows
whose `GRANTED_VIA` is `SuperUser` or `Owner Privilege`, which must not be copied. `INFORMATION_SCHEMA` has no
`ROUTINE_PRIVILEGES` or `TABLE_PRIVILEGES` table on this build (SQLCODE -30), so it cannot be the source.

Views are re-created only in the legacy sense (FR-008: the 4.6.0 schema has no `SQLUser` views), so a grant on a legacy
view has no replacement; it is reported by the dry run, and the migration notes say to grant on the table instead.

## R5. Where IVG names `SQLUser` today

`grep` over `iris_vector_graph/`: 102 `SQLUser.` references in `cypher/translator.py`, plus `_engine/query.py`,
`_engine/schema.py`, `schema.py` and `prop_values.py`; 28 `CREATE FUNCTION SQLUser.*` and 19 unqualified creates (which
land in `SQLUser`), and 4 views. `text_search.py` reads `rdf_edges`, `rdf_props` and `rdf_labels` unqualified
(lines 106, 161, 209, 210), resolving through the views.

**Decision**: one helper, `_fn(name, schema)`, beside the existing `_table(name, prefix)`
(`cypher/translator.py:127`), returns the qualified function name; every generated reference goes through one of the
two. A unit test greps the package for `SQLUser.` and fails on any occurrence outside the legacy manifest and the
removal code.

## R6. The legacy manifest

**Decision**: a frozen Python constant, generated by a script kept in `scripts/dev/` from two sources that must agree:
the DDL text at the `v4.4.0`…`v4.5.1` tags, and a live namespace built at each tag (T002). It lists every function,
procedure, view and class those releases created in `SQLUser`, the `User` package and the top-level package
(including the shipped `User.CypherFn`, `User.funcSTRSPLIT`, `User.funcJSONKEYS`, `User.SQL`, `User.Exec` and
`PageRankEmbedded`), plus `Graph.KG.Benchmark` / `Graph.KG.BenchFormat`. A test regenerates it and fails on any
difference between the two sources or with the constant, so it cannot drift. The DDL text alone misses the shipped
classes (found by `/speckit.analyze`, C1).

**Rationale**: FR-012 forbids patterns, and the only exact source of what 4.5.x created is the 4.5.x code.

## R7. Compiled native plans (spec 257)

`initialize_schema()` already drops every `IVGC.P<n>` class when the deploy changes (`schema.py`,
`Graph.KG.Cypher.Gen.DropAll`), and a changed source set changes the fingerprint (R1), so FR-014 needs no new
mechanism; a test checks that a plan compiled before the upgrade is gone after it.

## R8. The speed gate baseline

**Decision**: the baseline is commit `a42db224` (the release branch before spec 262), deployed to its own scratch
namespace on the same container; the gate runs each path there and on the 262 build, interleaved, and compares medians
of at least 5 runs. Paths: FR-020a's list. Output under `~/.cache/ivg-bench/spec262/gate/`.

**Rationale**: "4.5.2" was never published (it became 4.6.0); `a42db224` is its last code state.

## R10. Shipped classes whose methods are SQL functions

**Decision**: `User.CypherFn`, `User.funcSTRSPLIT` and `User.funcJSONKEYS` move to the IVG Schema's package
(`Graph.KG.CypherFn`, `Graph.KG.StrSplit`, `Graph.KG.JsonKeys` for `Graph_KG`), keeping each `SqlName`; the R1 rewrite
renames them with the schema (`IVGALT.CypherFn`). `User.SQL` and `PageRankEmbedded` move to `Graph.KG` as code classes.
`User.Exec` leaves the wheel (FR-015).

**Rationale**: a `[ SqlProc ]` method projects into the SQL schema of its class's package. Measured in `IVGSWAP`:
`R262.Pk.CypherFn.IVGPROBE` projected as `R262_Pk.CypherFn_IVGPROBE`, and a method with `SqlName = STR_SPLIT_PROBE` as
`R262_Pk.STR_SPLIT_PROBE`; both answered `SELECT R262_Pk.…`. So the package decides the schema, and moving the class is
the whole change.

## R9. `engine.status()`

**Decision** (the default recorded at clarify): `status()` gains `schema` (the namespace's IVG Schema, or `None` with
the FR-006a reason) and `legacy_objects` (the count of manifest objects still present), and `report()` prints both.
