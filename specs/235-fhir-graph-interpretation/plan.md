<!-- markdownlint-disable MD013 -->

# Implementation Plan: FHIR graph interpretation contract

**Branch**: `235-fhir-graph-interpretation` | **Date**: 2026-09-26 | **Spec**: [spec.md](spec.md)
**Input**: `specs/235-fhir-graph-interpretation/spec.md`. The 4.2 headline. It builds on 231, 232 and 233, and 4.1.0 ships first.

## Summary

Spec 235 makes a synced FHIR graph answer "which patients, and why?" using base R4 semantics only. It adds no profile enforcement.

- **Compartment edges (US1).** `ResyncKey` already builds the wanted reference edges of each resource in one set (`tWant`). Each `(param, Patient/…)` entry whose param is in the type's patient-compartment params becomes one `in_patient_compartment` edge per Patient. The edge's `via` qualifier is the sorted param list.
  - The params come from `HS.FHIRServer.Schema.LoadSchema(metadataSetKey)`, loaded once per `Open` (R1, R2).
  - Insert, keep and delete go through the existing diff, in the same batch transaction. Delete, erase, verify and snapshot are unchanged.
- **Ranking (US2).**
  - `Graph.KG.PageRank.RunJson` gains a predicate exclusion list and a result limit. `fhir_concept_ppr` excludes `in_patient_compartment`, `Provenance.target` and `Provenance.entity` by default, so 4.1.0 rankings hold (R6).
  - `group_by="patient"` runs uncapped PPR, then sums scores over `^KG` compartment adjacency, both in one `FHIRGraph.GroupPPR` call (T078 found that passing the scores to `GroupByPatient` as JSON fails at scale). It supports an optional `via` filter and returns the top `explain_top` contributors (R7).
- **Resolution (US3).** `params="clinical"` expands to four token params, filtered to the indexed ones, with `dropped_params` reported (R9). A resolved Medication adds its `medication` referrers as extra seeds (R10).
- **Reports (US4).** `fhir_coverage_report` and `fhir_concept_gaps` are live ObjectScript reports over `rdf_edges`, `rdf_props`, the token tables and `code_crosswalk`. Text-only codes are counted by reading only the resources that have no token row (R11).
- **Properties and labels (US5).** Sync writes `category` and `meta_profile` into `rdf_props` as sorted JSON arrays, from the token and `_profile` search tables. PascalCase labels are added for six allowlisted category systems. A label equal to an R4 type name is skipped (R8).
- **Anchors (US6).** `fhir_patient_anchors` walks a patient's compartment in-edges, then the clinical token rows, then the crosswalk. `cypher_api` uses it first and falls back to `fhir_bridge` only when no graph holds the patient (R15).
- **Upgrade (US7).** A nullable column `interp_version` on `Graph_KG.fhir_graphs` is the marker. A missing or older value makes the next sync run `Rederive`, which is the Rebuild resync loop over every live key. `fhir_reinterpret` runs the same loop (R12, R13).
- **Counts.** `Counts` and `Status.edges` exclude `in_patient_compartment`, and `Status` adds `compartment_edges`. `LinkReport` is unaffected (R14).

Prerequisite (R0): `233-fhir-genomics` and the 4.0.1 fixes must be merged into `main`, and `main` into this branch, before T001.

## Technical Context

**Language/Version**: Python 3.10+ (`.venv` 3.11, 3.13 verified); ObjectScript on IRIS 2024.1+ (test image `irishealth:2026.3.0AI.113.0`).

**Primary Dependencies**:

- `intersystems-irispython` (DB-API and Native API), already required;
- `HS.FHIRServer.Schema` in the target namespace, already used by 231;
- the standard library for fixture vendoring (`json`, `hashlib`, `zipfile`).

The pinned Synthea v4.0.0 generator jar (Apache-2.0) and Java 21 are needed only to regenerate the fixture. They are not a runtime or test dependency.

**Storage**:

- One new nullable column: `Graph_KG.fhir_graphs.interp_version INTEGER`, added by an idempotent migration.
- The FHIR-graph tables (`fhir_graphs`, `fhir_unresolved`, `fhir_definitions`, `fhir_canonical_refs`) leave the base schema. `GraphSchema.get_fhir_graph_schema_sql()` holds their DDL, and the first `fhir_graph_register` in a namespace with `HS_FHIRServer.Repo` runs it (FR-027, Phase 11).
- New rows only in existing tables:
  - `rdf_edges` (`p = 'in_patient_compartment'`, qualifier `{"via": […]}`);
  - `rdf_props` (keys `category`, `meta_profile`);
  - `rdf_labels` (category labels);
  - `^KG` adjacency for the new edges, through `WriteAdjacency`.
- Read only: the `HSFHIR_X0001_S_<Type>.<param>` token tables, `HSFHIR_X0001_S_<Type>._profile`, `HSFHIR_X0001_S.<Type>Compartments` (oracle only), `HS_FHIRServer.RepoInstance`.

**Testing**: pytest.

- Unit tests run with no IRIS. They cover:
  - `via` merging;
  - preset expansion;
  - the exclusion matching rule;
  - group-by aggregation over a fake score map;
  - the marker comparison;
  - the category system and label rules, including the collision rule;
  - the token-field path helper;
  - fixture vendoring;
  - the oracle builder;
  - docs parity.
- Integration tests call `RunJson`, `GroupByPatient` and `LoadCompartment` directly over TCP (Principle IV).
- E2E runs one file per user story on `ivg-iris-enterprise`, namespace `IVGFHIR`, through `tests/e2e/fhir_conftest.py`, with per-run id prefixes.

**Target Platform**: IRIS for Health namespaces that have a JsonAdvSQL R4 repository.

**Project Type**: single library.

**Performance Goals** (SC-005 to SC-007, measured by `scripts/fhir/bench_235.py` against a 4.1.0 baseline on the same container):

- sync wall time at most +15%, on the fixture and on about 100 patients;
- `group_by="patient"` at most +20% over the median PPR time at scale;
- coverage report and gaps each under 2 s at scale.

**Constraints**:

- Derivation reads no extra resources (FR-003).
- Every default is unchanged: `params=["code"]`, `group_by=None`, and `kg_PERSONALIZED_PAGERANK` with no exclusions.
- `rdf_props` holds one value per `(graph, s, key)`, so lists are JSON arrays.
- Assertions are filtered to this run's prefixed keys.
- Ids are at most 64 characters: 36-character Synthea ids plus a 10-character prefix is 46.

**Scale/Scope**: about 1,100 resources in the fixture (10 patients; Claim and EOB dropped; the vendoring step prints the exact figure), and about 11,000 in the generated 100-patient scale set. The live IVGFHIR namespace also holds 14,792 live resources from 231 to 233, which prefix filtering excludes.

All unknowns were resolved by probing the live server and code and by downloading Synthea. See [research.md](research.md) R0 to R18. Nothing is left as NEEDS CLARIFICATION.

## Constitution Check

_Gate before Phase 0; re-checked after Phase 1 design (below)._

| Principle                              | Status    | How                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------------------------- | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| II: Compatibility                      | Pass      | Every new argument defaults to 4.1.0 behaviour. `fhir_concept_ppr`'s default exclusions only remove edges that did not exist in 4.1.0, plus Provenance fan-out (FR-009, R18). `params["patient_anchors"]` stays `list[str]`. New response fields are additive.                                                                                                                                                                                                                     |
| III: Test-first                        | Pass      | The unit tests for `via` merging, the exclusion rule, group-by, the label rules, the path helper, the marker and vendoring are written, and seen failing, before the code. Each story's E2E is written before its engine change.                                                                                                                                                                                                                                                   |
| IV: E2E on a live, dedicated container | Pass      | Container `ivg-iris-enterprise` (`IVG_TEST_CONTAINER`), with the port from `IVG_PORT`, never hardcoded. `SKIP_IRIS_TESTS` defaults to `"false"`. One E2E file per story (US1 to US7) is the phase gate. The template's `iris_vector_graph` container name does not apply to this repo (Principle VI).                                                                                                                                                                              |
| VI: Grounding                          | Pass      | Measured facts include the compartment params (66 types, equal to the CompartmentDefinition), the Compartments table shape and its dangling rows, the Provenance cause, the token-table text-only behaviour, SearchColumn params and the Synthea counts and licence. See research R1 to R17.                                                                                                                                                                                       |
| VII: Cypher conformance                | N/A       | No translator, parser, SQL-generating mixin or `CY_*` UDF changes. New labels and the new edge type are data; Cypher matches them through the existing paths. The US1 and US5 E2Es still run the `MATCH` patterns live.                                                                                                                                                                                                                                                            |
| VIII: Gates                            | Pass      | A missing container is `pytest.fail` in the new files. A compile error in deploy fails the run. Gate 4: `execute_ppr` on the `GraphStore` protocol and `MockGraphStore` gain the same optional keyword arguments (`exclude`, `limit`), and `fhir_graphs.interp_version` is added to the DDL that `test_fhir_sql_columns_match_schema` reads. The bench script exits non-zero on a budget breach. SC-009 re-runs the 231, 232 and 233 suites and `test_fhir_demo_e2e.py` unchanged. |
| New schema needs justification         | Justified | One nullable column, `interp_version`, on an existing IVG table; no RDF-table change. Needs Tom's explicit approval (Additional Constraints). See Complexity Tracking.                                                                                                                                                                                                                                                                                                             |

**Principle IV gate (IRIS-backend features)**:

- [x] A dedicated, named IRIS container: `ivg-iris-enterprise`, via `scripts/enterprise-container.sh`.
- [x] An explicit E2E phase per user story (US1 to US7), not in polish.
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in new test files.
- [x] No hardcoded ports: `IVG_PORT` through `tests/e2e/fhir_conftest.py`.

**Post-design re-check**: Pass.

- There is no new store. Compartment edges are `rdf_edges` rows with `^KG` adjacency, so Eraser, `verify_graph` and snapshot cover them as they are (FR-007).
- There is one derivation path. Rebuild, SyncOnce, `Rederive` and `fhir_reinterpret` all go through `ResyncKey`, so full and incremental results cannot diverge (FR-008, SC-008).
- The only schema change is the justified column. There is no new table or dependency.

## Design

### Derivation inside `ResyncKey`

```text
Open(graph):
    key = SELECT metadataSetKey FROM HS_FHIRServer.RepoInstance WHERE ID = repo
    sch = HS.FHIRServer.Schema.LoadSchema(key)
    LoadCompartment(sch, "patient")      -> ..Comp(type, param)
    ..R4Types(type)                       -> label collision set
LoadColumns():
    + TOKEN 'category' rows and the Resource-level '_profile' row -> ..Props(type, prop)

ResyncKey(key):
    tWant(p, target) built as today (index columns, JSON links, canonicals)
    if type '= "Patient":
        for (p, target) in tWant with ..Comp(type, p) and target "Patient/*":
            via(target) += p
        tWant("in_patient_compartment", target) = $lb("via", sorted csv)
    diff tWant vs rdf_edges        # existing: UPDATE qualifiers / DeleteEdge / INSERT + WriteAdjacency
    count in_patient_compartment inserts/deletes -> ..Interp
    props: category / meta_profile from ..Props tables by Key -> sorted JSON, upsert or delete
    labels: {type} + allowlisted PascalCase category codes, minus ..R4Types -> diff rdf_labels, ^KG("label")
```

### PPR exclusion and grouping

```text
RunJson(seeds, alpha, maxIter, bidir, revWeight, graph, pExclude = "", pLimit = 1000)
    excl(p) = 1              for bare "p"
    excl(p, Type) = 1        for "Type.p"
    outdeg(n) = deg(n) - Σ degp(n, p) over excluded (p, type(n))
    skip excluded edges in the forward walk and in the reverse count
    return the top pLimit (0 = all)

fhir_concept_ppr(..., exclude_predicates=FHIR_PPR_EXCLUDE, group_by=None, via=None, explain_top=5)
    group_by None      -> {key: score} as 4.1.0 (top_k)
    group_by "patient" -> GroupPPR(graph, seeds, alpha, iter, exclude, via, explain_top, top_k)
                          -> {"patients": [...], "unattributed": {count, score}}
```

### Assertions

| Story  | Expected                                                                                     | Observed                                                    |
| ------ | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| US1    | server `<Type>Compartments` Patient rows, minus self and dangling rows, for this run's keys  | `{(s, o_id)}` for `in_patient_compartment` edges            |
| US1    | fixture-side oracle `via` per `(s, patient)`                                                 | `qualifiers.via`                                            |
| US1    | after PUT of a changed `subject`, and after DELETE: new edge and no old one                  | `rdf_edges` and `^KG("out")`                                |
| US1    | full Rebuild equals the incremental sequence                                                 | edge, prop and label sets compared                          |
| US1    | `MATCH (o:Observation)-[:in_patient_compartment]->(p:Patient)` row count                     | `execute_cypher`                                            |
| US2    | default `fhir_concept_ppr` equals the 4.1.0 baseline scores on the 231 and 233 fixtures      | score maps within 1e-12                                     |
| US2    | each patient's score is the sum of its contributors' scores; `via` filter; `explain_top`     | `group_by="patient"`                                        |
| US3    | `clinical` resolves `value-concept` and component Observations; dropped params are reported  | `fhir_resolve_concepts(..., detail=True)`                   |
| US3    | Medication hop referrers are tagged `via=medication`                                         | detail result                                               |
| US4    | each coverage figure equals a count from the vendored file                                   | `fhir_coverage_report`                                      |
| US4    | the unmatched top 20 and the text-only count equal fixture-side counts                       | `fhir_concept_gaps`                                         |
| US5    | `category` and `meta_profile` arrays equal the fixture; `VitalSigns` and `Laboratory` labels | `rdf_props`, `MATCH (o:Observation:VitalSigns)`             |
| US6    | a patient in the graph gives `anchor_source="graph"`; a patient absent gives `fhir_bridge`   | `/cypher` endpoint with the bridge mocked at the HTTP layer |
| US7    | marker NULL, then sync: `full_rederivation` true and the graph equals a fresh 4.2 sync       | `fhir_graph_sync`, set comparison                           |
| SC-009 | 231, 232 and 233 E2E and the demo E2E pass unchanged; link report and `edges` equal 4.1.0    | existing suites plus a baseline JSON                        |

## Project Structure

### Documentation (this feature)

```text
specs/235-fhir-graph-interpretation/
├── plan.md, research.md, data-model.md, quickstart.md
├── contracts/python-api.md, contracts/objectscript.md, contracts/fixture.md
├── baseline.json        # written by bench_235.py (4.1.0 and 4.2 runs)
└── tasks.md             # /speckit.tasks
```

### Source Code (repository root)

```text
iris_src/src/Graph/KG/FHIRGraph.cls      # LoadCompartment, Props/labels in ResyncKey, Rederive, Reinterpret,
                                          # GroupByPatient, IndexedTokenParams, PatientConcepts,
                                          # CoverageReport, ConceptGaps, Counts/Status filters, INTERPVERSION
iris_src/src/Graph/KG/PageRank.cls       # RunJson pExclude, pLimit
iris_src/src/Graph/KG/Eraser.cls         # EraseFHIRGraphRows nulls interp_version
iris_vector_graph/schema.py              # get_fhir_graph_schema_sql (lazy DDL) + interp_version
iris_vector_graph/_engine/schema.py      # _ensure_fhir_graph_columns migration
iris_vector_graph/_engine/fhir_graph.py  # clinical preset, detail=, group_by/via/explain_top, exclude_predicates,
                                          # fhir_coverage_report, fhir_concept_gaps, fhir_reinterpret, fhir_patient_anchors
iris_vector_graph/_engine/algorithms.py  # kg_PERSONALIZED_PAGERANK exclude_predicates; fallback filter
iris_vector_graph/stores/iris_sql_store.py  # execute_ppr passes exclude/limit
iris_vector_graph/store_protocol.py      # execute_ppr exclude/limit (Gate 4)
tests/unit/test_store_protocol.py        # MockGraphStore.execute_ppr exclude/limit
iris_vector_graph/cypher_api.py          # graph-first anchors, anchor_source, anchors, fhir_graph

tests/e2e/fixtures/fhir/synthea/synthea-r4-10.json   # vendored, generated
tests/e2e/fixtures/fhir/synthea/SOURCE.md            # jar sha256, seed, edits, counts
tests/e2e/interp_fixture.py              # vendor CLI, load, expected_compartments, expected_* oracles
tests/unit/test_235_*.py                 # via, exclusion, group-by, labels, token path, marker, fixture, docs
tests/integration/test_235_pagerank_exclude.py
tests/integration/test_235_group_by_patient.py
tests/e2e/test_235_compartment_e2e.py    # US1 gate (oracle)
tests/e2e/test_235_patient_ranking_e2e.py  # US2 gate
tests/e2e/test_235_clinical_resolve_e2e.py # US3 gate
tests/e2e/test_235_coverage_gaps_e2e.py    # US4 gate
tests/e2e/test_235_category_profile_e2e.py # US5 gate
tests/e2e/test_235_anchors_e2e.py          # US6 gate
tests/e2e/test_235_reinterpret_e2e.py      # US7 gate
scripts/fhir/bench_235.py                  # opt-in baseline vs 4.2 budgets

docs/FHIR_GRAPH.md                         # interpretation contract section, medians
CHANGELOG.md                               # 4.2.0 entry and "Upgrading" note
```

**Structure Decision**: this is the existing single-library layout. The engine work concentrates in `FHIRGraph.cls` (derivation, reports, grouping), `PageRank.cls` (exclusion) and `_engine/fhir_graph.py` (Python surface). No new module is added apart from the test fixture helper and the bench script.

## Complexity Tracking

| Violation                                                   | Why Needed                                                                                                                                                                                            | Simpler Alternative Rejected Because                                                                                                                                                                                                |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Schema change: column `Graph_KG.fhir_graphs.interp_version` | FR-022 needs a per-graph marker that SQL and `Status` can read, that Eraser can reset, and that goes when the graph is unregistered. It is nullable, so existing rows mean "absent" with no backfill. | A `^IVG.FHIRGraph(graph, "interp")` global is invisible to SQL, Eraser does not clear it, and it outlives the row. Packing it into `last_counts` overloads a field that `Rebuild` overwrites, and SyncOnce never writes that field. |
