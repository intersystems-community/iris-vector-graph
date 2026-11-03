<!-- markdownlint-disable MD013 -->

# Implementation Plan: One IVG Schema per namespace

**Branch**: `262-ivg-owned-sql-schema` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md) | **ADR**: [0016](../../docs/adr/0016-one-ivg-schema-per-namespace.md)
**Input**: Feature specification from `specs/262-ivg-owned-sql-schema/spec.md`

## Summary

Every SQL object IVG creates moves into one IVG Schema per namespace (`Graph_KG` by default, chosen by `schema_prefix` or
`IVG_SCHEMA_PREFIX`), recorded in `^IVG.Deploy("schema")` by the first `initialize_schema()`. The deploy rewrites the
ObjectScript sources for the recorded schema before compiling them, so the hot embedded SQL stays embedded: run-time SQL
measured 1.4× to 11× slower per statement (research R1), far over FR-020a's 5%. The translator and every Python query
qualify functions and tables through two helpers; the `SQLUser` views go; 4.5.x objects stay in existing namespaces with
their grants copied to the replacements, until `remove_legacy_objects` drops them by a frozen manifest. The benchmark
classes leave the wheel.

## Technical Context

**Language/Version**: Python 3.10+ (`.venv` 3.11, 3.13 verified); ObjectScript on IRIS 2024.1+ (test image `irishealth:2026.3.0AI.113.0`)
**Primary Dependencies**: `intersystems-irispython` (DB-API + Native API); no new dependency
**Storage**: InterSystems IRIS: the IVG Schema's tables, functions, procedures; `^IVG.Deploy("schema")`; unchanged `^KG`, `^NKG`, `^IVG.*`
**Testing**: pytest (unit, integration, e2e) on `ivg-iris-enterprise:31972`; openCypher TCK (`scripts/tck/run_sharded.sh`); a new speed gate
**Target Platform**: IRIS namespaces on Linux containers (Community and Enterprise)
**Project Type**: single library (`iris_vector_graph/`, `iris_src/src/`)
**Performance Goals**: no write, erase or read path more than 5% slower than `a42db224` on `Graph_KG` and on `IVGALT` (FR-020a)
**Constraints**: test-first; database state asserted after each call; legacy namespaces built from frozen DDL; no edits outside this repo
**Scale/Scope**: ~47 functions and procedures, 4 views, 53 `User.func*` classes, 102 `SQLUser.` references in the translator, 283 `Graph_KG` references in 32 classes, 2 classes leaving the wheel

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design._

| Principle | How the plan meets it |
| --- | --- |
| I. Library-First | All in `iris_vector_graph/` and `iris_src/src/`; operators get `remove_legacy_objects` and `status()`, not scripts |
| II. Compatibility-First | Existing namespaces keep every 4.5.x object and grant (FR-010, FR-010a); `schema_prefix="Graph_KG"` callers unchanged. Two approved breaks, both in ADR-0016 and the 2026-11-03 grill: fresh namespaces get no `SQLUser` views and functions, and a second schema in one namespace now raises |
| III. Test-First | Every task below starts with a failing test; the red run is recorded in the task |
| IV. IRIS E2E | `ivg-iris-enterprise`, port from `IVG_PORT` (`tests/conftest.py`); `SKIP_IRIS_TESTS` defaults `"false"`; an e2e test per user story, plus one per write/delete/read path in `IVGALT` (FR-021) |
| V. Simplicity | One rewrite at deploy instead of a run-time layer; two name helpers; the record in the existing `^IVG.Deploy` |
| VI. Grounding | Container, port and schema verified from `tests/conftest.py` and `engine.py:269`; IRIS name mapping and grant API measured (research R2, R4) |
| VII. Cypher gates | The translator changes, so: the direction-symmetry E2E runs in `IVGALT` as well as `Graph_KG`, and the full TCK runs with 0 regressions against the current `results.tsv` |
| VIII. Spec hygiene | Container gates unchanged; compile errors in the rewritten sources fail the deploy (they already do) |

The template's `iris_vector_graph` / `IRISContainer.attach` line is stale: the constitution names `ivg-iris-enterprise`
via `IVG_PORT` for tests (Principle IV, VI), and that is what this plan uses.

**Result**: PASS, no violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/262-ivg-owned-sql-schema/
├── spec.md, plan.md, research.md, data-model.md, quickstart.md
├── contracts/python-api.md
├── checklists/requirements.md
└── tasks.md            (/speckit.tasks)
```

### Source Code (repository root)

```text
iris_vector_graph/
├── schema_name.py                 NEW  resolve + validate the IVG Schema name; read/write the record
├── legacy_manifest.py             NEW  frozen 4.5.x manifest (generated, checked by a test)
├── _engine/class_deploy.py        rewrite sources for the recorded schema before LoadStream; fingerprint the result
├── _engine/schema.py              initialize_schema(dry_run=): record, infer, refuse; create functions in the IVG Schema; no SQLUser views; copy grants
├── schema.py                      function/procedure DDL takes the schema; rescue no longer re-creates SQLUser.rdf_edges
├── cypher/translator.py           _fn(name, schema) beside _table(); every SQLUser. reference through it
├── _engine/query.py, prop_values.py, text_search.py   qualified names only
├── _engine/admin.py, status.py    status().schema, .legacy_objects; remove_legacy_objects()
├── engine.py                      schema_prefix=None → IVG_SCHEMA_PREFIX → Graph_KG; first-access checks (FR-006, FR-006a)
└── (IVGSchemaError lives in schema_name.py, re-exported from iris_vector_graph/__init__.py)
iris_src/src/Graph/KG/             unchanged text, rewritten at deploy (research R1); gains CypherFn, StrSplit, JsonKeys, SQL,
                                   PageRankEmbedded moved from User.* and the top-level package (research R10)
iris_src/dev/                      NEW  Graph/KG/Benchmark.cls, Graph/KG/BenchFormat.cls, User.Exec.cls (not packaged)
scripts/dev/gen_legacy_manifest.py NEW
scripts/bench/schema_gate.py       NEW  FR-020a speed gate
docs/ADMIN_GUIDE.md, docs/inventory.json, docs/migration/v4.6.0.md
tests/unit/        test_schema_name.py, test_source_rewrite.py, test_legacy_manifest.py, test_no_sqluser_refs.py, test_fn_names.py
tests/integration/ test_schema_record.py, test_grant_copy.py, test_function_placement.py
tests/e2e/         test_262_fresh_namespace.py, test_262_upgrade_451.py, test_262_alt_schema_paths.py, test_262_record.py,
                   test_262_vendored.py, test_262_inventory.py, test_262_not_upgraded.py
```

**Structure Decision**: the existing single-library layout; the only new top-level directory is `iris_src/dev/` for
classes the tests deploy and the wheel does not ship.

## Phases (for /speckit.tasks)

1. **Name and record** (US3, US4 core): `schema_name.py`, validation, env resolution, `^IVG.Deploy("schema")`, the two
   first-access errors. Unit tests first; then E2E on record/infer/refuse with frozen 4.5.1 DDL.
2. **Source rewrite** (FR-005): rewrite with the allow-list; unit test over every packaged source for `IVGALT`; E2E that
   a deployed `IVGALT` namespace compiles and has 0 surviving references (SC-004).
3. **Functions and views move** (US1, FR-001/2/7/8): DDL into the IVG Schema; `_fn()` and qualified Python SQL; views
   dropped from setup; `test_no_sqluser_refs.py`; translator direction-symmetry in both schemas; full TCK.
4. **Upgrade and legacy** (US2, FR-009–FR-014): frozen manifest + generator; grant copy; `remove_legacy_objects`;
   dry runs with snapshots; native plans dropped.
5. **Every path in `IVGALT`** (FR-021, SC-003): one E2E per write, delete and read path, from the graph-scope list.
6. **What ships** (US5): `iris_src/dev/`, wheel check, tests deploy dev classes themselves.
7. **Vendored consumers** (US6): the vendored-namespace E2E; migration notes; the opsreview bus message at release.
8. **Inventory and docs** (FR-017–FR-020): ADMIN_GUIDE table, `docs/inventory.json`, `test_262_inventory.py`.
9. **Speed gate** (FR-020a, SC-006a): `scripts/bench/schema_gate.py` against `a42db224`, both schemas; recorded in the sign-off.

## Complexity Tracking

None. The source rewrite is the simplest design that meets the 5% gate (research R1).
