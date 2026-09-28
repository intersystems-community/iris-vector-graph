<!-- markdownlint-disable MD013 -->

# Research: FHIR graph interpretation contract

Every fact below was measured on 2026-09-26. There are three sources:

- the code on `235-fhir-graph-interpretation` (at `ec3a00d`) and on `233-fhir-genomics`;
- the live `IVGFHIR` namespace on `ivg-iris-enterprise` (port 31972), repository `1||1`, graph `fhir:IVGFHIR:X0001`;
- the Synthea sample archive and generator, downloaded to `/tmp/synth235/` (outside the repo).

Line numbers are for `iris_src/src/Graph/KG/FHIRGraph.cls` unless another file is named.

## R0: Prerequisites on this branch

- **Facts**:
  - Spec 233 (`18feb40`) and the four 4.0.1 fix commits (`9b82a42`, `92cb61b`, `2d27c51`, `c733211`) are on `233-fhir-genomics` only. `git cherry -v main 233-fhir-genomics` lists all five as not in `main`.
  - `233-fhir-genomics` has all 40 tasks checked.
  - Without the merge, this branch lacks `tests/e2e/genomics_fixture.py`, the genomics fixture, `prefix_resources`, `load_run`/`teardown_run`, `direction` on concept expansion, and the genomics section of `docs/FHIR_GRAPH.md`.
  - There is no `v4.1.0` tag. `pyproject.toml` says 4.0.0, and `CHANGELOG.md` has "v4.1.0 (unreleased)".
- **Decision**: 235 starts only after `233-fhir-genomics` is merged into `main` and `main` is merged into this branch. Task T001 checks both with `git merge-base --is-ancestor 18feb40 HEAD`. The merge into `main` is part of the 4.1.0 release prep, which Tom owns. This plan does not do it.
- **Rationale**: the spec's fixtures, prefixing and SC-009 (233 suites unchanged) all need 233's code.
- **Alternatives**: cherry-picking 233 onto this branch was rejected, because `main` would then diverge from what ships as 4.1.0.

## R1: Where the patient-compartment params come from (FR-001, FR-005)

- **Facts**:
  - `HS_FHIRServer.RepoInstance.metadataSetKey` is `hl7.fhir.r4.core@4.0.1` for repo `1`.
  - `##class(HS.FHIRServer.Schema).LoadSchema("hl7.fhir.r4.core@4.0.1")` loads the schema. `LoadSchema("HL7v40")` loads it too.
  - `sch.GetCompartmentParamsForType(type, .p)` fills `p(compartment, param) = ""`. Compartment names are lower camel case: `patient`, `practitioner`, `relatedPerson`, `device`, `encounter`.
  - 72 of the 145 R4 types have params in some compartment, and 66 have `p("patient", …)`.
  - The per-type patient param sets equal those in `CompartmentDefinition-patient.json`, shipped in the container under `/usr/irissys/dev/fhir/fhir-metadata/packages/hl7.fhir.r4.core/`. There are no differences.
  - Examples:
    - `Observation`: `subject`, `performer`.
    - `AllergyIntolerance`: `patient`, `recorder`, `asserter`.
    - `Provenance`: `patient`.
    - `Patient`: `link`.
    - `Group`: `member`.
- **Decision**:
  - `Open` reads `metadataSetKey` for the registered repo, calls `LoadSchema` once, and fills `..Comp(type, param)` for compartment `"patient"`. The compartment name is a parameter of the loader, `LoadCompartment(pSchema, pCompartment = "patient")` (FR-005).
  - `Patient` is skipped in derivation (FR-004).
- **Rationale**: this is the server's own reading of the base CompartmentDefinition, keyed to the repository's own metadata set, so a future R4B or R5 repo gets its own table with no code change.
- **Alternatives**:
  - Parsing the CompartmentDefinition JSON was rejected, because it duplicates what the server already exposes.
  - Hardcoding `HL7v40` was rejected: it would silently misread a repository on another metadata set.

## R2: Where derivation hooks in (FR-001, FR-003, FR-006, FR-007, FR-008)

- **Facts**:
  - `ResyncKey` (1028-1305) is the only method that writes and removes a resource's reference edges.
    - It builds `tWant(p, target) = $lb(qualKey, value)` from index columns (1074-1118), JSON links (1120-1144) and canonicals (1147-1179).
    - It diffs `tWant` against `SELECT edge_id, p, o_id, qualifiers FROM rdf_edges WHERE graph_id=? AND s=?` (1183).
    - Kept edges get `UPDATE qualifiers` when those change (1197). Others go to `DeleteEdge` (1204).
    - Stray `^KG("out")` entries not in `tWant` are removed (1208-1230). New rows are `INSERT`ed and given `WriteAdjacency` (1232-1245).
  - Every sync and rebuild batch runs `ResyncKey` inside one `TSTART…TCOMMIT` in `RunBatch` (914-964).
  - A deleted resource reaches `DropNode` (1355), which removes its outgoing and incoming edges.
  - `p` is the bare search param name. A target becomes an edge only if it is a live `Type/id`. Otherwise it becomes an `fhir_unresolved` row.
  - The unique key on `rdf_edges` is `(s, p, o_id, graph_id, ekey)`, so there is one edge per `(s, p, o)`.
- **Decision**:
  - After `tWant` is built and before the diff, `ResyncKey` derives compartment entries. The rule:

    ```text
    for (p, target) in tWant:
        if ..Comp(type, p) and target starts with "Patient/":
            via(target) += p
    for target in via:
        tWant("in_patient_compartment", target) = $lb("via", sorted-unique via(target))
    ```

  - The existing diff then inserts, keeps or removes these edges in the same transaction as the reference edges. `DropNode` removes them with the resource's other outgoing edges, and no new delete path is needed (FR-006, FR-007).
  - An incoming compartment edge to a deleted Patient is removed by `DropNode`'s incoming sweep, like any other incoming edge.
  - `ResyncKey` counts `in_patient_compartment` inserts and deletes into `..Interp("added")` and `..Interp("removed")` for FR-025.
  - A reference to a Patient that is missing becomes an `fhir_unresolved` row, not an edge, so it gives no compartment edge. When that Patient arrives, the existing pending resync (1293-1304) re-runs `ResyncKey` for the waiting source, and the compartment edge appears then.
  - A param on the registration denylist gives no reference edge, so it gives no compartment edge. The docs say so.

- **Rationale**: one pass and one transaction per batch, with no extra resource reads (spec decision 3), and the full-resync-equals-incremental property comes for free, because both paths run the same `ResyncKey`.
- **Alternatives**:
  - A post-sync SQL pass over `rdf_edges` was rejected: it runs outside the batch transaction, and it would rescan the whole graph on every sync.
  - A separate store was rejected by the spec (FR-014).

## R3: Existing duplicates and `via` (FR-002)

- **Facts**:
  - In `fhir:IVGFHIR:X0001`, every `patient` edge (7330) has a `subject` edge to the same target (7330). Both params index `Observation.subject` or `Encounter.subject`.
  - `Observation.performer` edges to Patients exist, for example `Observation/ta37966a9-o1 performer Patient/ta37966a9-p1`.
  - Correction, measured in US1: only `Invoice`, `MedicationAdministration` and `MedicationDispense` list both `patient` and `subject` in the R4 patient CompartmentDefinition. `Observation` lists `subject` and `performer`; `Encounter` lists `patient` alone, so an Encounter's edge has `via = ["patient"]` even though its `subject` edge goes to the same Patient.
- **Decision**:
  - A resource with two compartment params reaching `Patient/p1` (for example a MedicationAdministration's `patient` and `subject`) gets one compartment edge with `via = ["patient", "subject"]`.
  - A resource with `subject → Patient/p1` and `performer → Patient/p2` gets two compartment edges, each with its own `via`.
  - `via` is stored in `rdf_edges.qualifiers` as `{"via": ["patient", "subject"]}`, sorted.
  - `Qualifier` (1308) today builds `{key: value}` from `$lb(key, value)`. It gains one case: when the key is `via`, the value is a comma list and is written as a JSON array.
- **Rationale**: the spec requires one edge per `(resource, patient)` (FR-002). Sorting makes `via` deterministic, so the full and incremental syncs compare equal.
- **Alternatives**: one compartment edge per param was rejected, because the unique key is per `(s, p, o)` and the spec wants one edge per pair.

## R4: Provenance and the empty `ProvenanceCompartments` (spec edge case)

- **Facts**:
  - `SELECT Deleted, COUNT(*) FROM HSFHIR_X0001_R.Rsrc WHERE ResourceType='Provenance'` returns `(1, 18)`: all 18 are deleted.
  - Before deletion, none pointed at a Patient. In `HSFHIR_X0001_V.RsrcVer` every body has `target → Observation/…`, `agent.who → Device` and `entity.what → Observation`.
  - R4 `Provenance` has no `patient` element. The server's `SearchColumn` row for `Provenance.patient` has the FHIRPath `Provenance.target.where(resolve() is Patient)`, multi-value.
  - In Synthea, each patient bundle has one Provenance whose `target` lists every resource in the bundle, the Patient first.
- **Decision**:
  - The empty table comes from the data, not from the server. Provenance is not excluded from the oracle.
  - On the Synthea fixture, each Provenance gets one compartment edge with `via = ["patient"]`, and the oracle checks it like any other type.
  - The spec's wording "IVG derives from `Provenance.patient`" becomes "from Provenance's `patient` search param (over `target`)". The Context line is corrected the same way.
- **Rationale**: measured. The server indexes `patient` from `target`, so IVG already has the `patient` reference edge to derive from.
- **Alternatives**: none. The question was factual.

## R5: The oracle (SC-001)

- **Facts**:
  - Each `HSFHIR_X0001_S.<Type>Compartments` table has columns `(ID, Key, value)`, for example `(1, 'Observation/o1', 'Patient/p1')`.
  - One table holds all five compartment kinds. There is **no param column**.
  - `PatientCompartments` holds a self row `Patient/X → Patient/X` for each of the 6893 live Patients.
  - The server keeps rows for dangling targets:
    - `ObservationCompartments` has 5798 Patient rows, 47 of them to missing or deleted Patients. The graph has 5751 `Observation.subject` edges, which is 5798 − 47.
    - `CarePlanCompartments` has 54 Patient rows, 18 of them dangling, against 36 edges.
- **Decision**:
  - The oracle is built per compartment type from `SELECT Key, value FROM HSFHIR_X0001_S.<Type>Compartments WHERE value %STARTSWITH 'Patient/'`.
  - It drops `Patient` self rows, drops rows whose `value` is not a live Patient node in the graph, and is filtered to this run's prefixed keys.
  - It must equal `{(s, o_id)}` over `in_patient_compartment` edges, per resource.
  - `via` cannot be checked against the server, so the test checks it against a fixture-side oracle. `interp_fixture.expected_compartments(resources)` walks the reference paths of each type's compartment params, as listed in `SearchColumn.FHIRPath`.
- **Rationale**: the server table is independent of IVG's code for membership. The fixture oracle covers what the server does not record.
- **Alternatives**: comparing against the raw table was rejected, because dangling rows are not edges by design (231 rules).
- **Measured (US1, 2026-09-26)**, Synthea fixture plus 233 genomics, one run prefix:
  - Membership equals the server's tables for all 20 non-Patient compartment types present, with no exclusions.
  - `PatientCompartments` also holds each `link`ed Patient in the other's compartment (the two synthetic `seealso` rows). The graph skips the Patient type (FR-004), so the oracle compares non-Patient types only.
  - Edges per type: AllergyIntolerance 16, CarePlan 31, CareTeam 31, Condition 226, DiagnosticReport 451, DocumentReference 278, Encounter 274, ImagingStudy 17, Immunization 81, MedicationAdministration 11, MedicationRequest 196, MedicationStatement 2, MolecularSequence 0, Observation 1487, Procedure 543, Provenance 10, RiskAssessment 1, ServiceRequest 7, Specimen 11, SupplyDelivery 64. Total 3737.
  - Provenance: 10 edges, one per Synthea patient bundle, `via = ["patient"]`, as R4 predicted.

## R6: Excluding edges from PPR (FR-009, FR-010)

- **Facts**:
  - `fhir_concept_ppr` (`_engine/fhir_graph.py:199-236`) calls `kg_PERSONALIZED_PAGERANK(seeds, bidirectional=True, graph=g, return_top_k=top_k)`.
  - For a named graph this always reaches `Graph.KG.PageRank.RunJson(seedJson, alpha, maxIter, bidir, revWeight, pGraph)` (`PageRank.cls:24`). The Python fallback in `_engine/algorithms.py:150` has the same semantics.
  - `RunJson` walks every `p` under `^KG("out")` and `^KG("in")`. Its divisor is `^KG("deg", g, node)` plus a reverse count, and both include every predicate.
  - `^KG("degp", g, s, p)` holds the per-predicate out-degree.
  - Results are capped at 1000 (`PageRank.cls:176-178`).
  - The teleport vector is uniform over the seeds (`1 / nSeeds`, 43-48).
  - No predicate exclusion exists today.
  - `target` and `entity` are also param names on other types (for example `AuditEvent.entity`), so a bare predicate name is too broad.
- **Decision**:
  - `RunJson` gains two trailing parameters:
    - `pExclude`: a JSON array of `"p"` (any source type) or `"Type.p"` (source key starts `Type/`).
    - `pLimit`: default 1000; 0 means unlimited.
  - An excluded edge is neither walked nor counted in the divisor. The out-degree becomes `deg − Σ degp(node, p)` over excluded `p` that match the node's type. The reverse count skips excluded `(p, source type)`.
  - The Python fallback applies the same filter.
  - `kg_PERSONALIZED_PAGERANK` gains `exclude_predicates=None`. `None` keeps 4.1.0 behaviour exactly.
  - `fhir_concept_ppr` passes `FHIR_PPR_EXCLUDE = ["in_patient_compartment", "Provenance.target", "Provenance.entity"]` unless the caller passes `exclude_predicates=` explicitly.
- **Rationale**:
  - On a graph without Provenance fan-out, excluding exactly the new edges reproduces 4.1.0's walk bit for bit (FR-010). The unit test compares score vectors.
  - Type-scoped entries keep `AuditEvent.entity` walked.
- **Alternatives**:
  - Not writing compartment edges to `^KG` was rejected, because Cypher and traversal need them (US1).
  - A separate `^KG` subscript for derived edges was rejected. It adds a store that erase, verify and snapshot would each have to learn (FR-014).

## R7: `group_by="patient"` (FR-011, FR-012)

- **Decision**:
  - `fhir_concept_ppr(..., group_by="patient", via=None, explain_top=5, top_k=50)` runs PPR with `pLimit=0` and the default exclusions, then calls a new `Graph.KG.FHIRGraph.GroupByPatient(graph, scoresJson, viaJson, explainTop, topK)`.
  - For each scored resource, `GroupByPatient` walks `^KG("out", g, key, "in_patient_compartment", patient)`.
  - When `via` is set, it keeps only edges whose `qualifiers.via` intersects it. It reads the qualifiers by the unique key `(s, p, o_id, graph_id)` for scored resources only.
  - A patient's score is the sum of its contributors' scores.
  - Output is sorted by score descending, then by patient key, and cut to `top_k`:

    ```text
    {"patients": [{patient, score, contributors_total, contributors: [{key, score, via}] up to explain_top}],
     "unattributed": {"count": n, "score": mass}}
    ```

  - `unattributed` covers scored resources with no kept compartment edge: Patients themselves, non-compartment types (Medication, Organization, Practitioner) and resources dropped by the `via` filter (FR-011).

  - A Patient node's own PPR score is not a contributor, because Patient is skipped in derivation. `group_by=None` (the default) returns the 4.1.0 dict unchanged.

- **Rationale**:
  - `^KG` adjacency makes the join O(scored resources) with no SQL.
  - The 1000 cap would drop contributors at the 100-patient scale (about 110k nodes), so the grouped path runs uncapped.
- **Alternatives**: a SQL join of the scores against `rdf_edges` in Python was rejected, because it moves every compartment edge over DB-API at scale.
- **Measured (US2, 2026-09-27)**:
  - The Synthea seed gives a grouped top 10 whose scores are the sums of their contributors, and `unattributed` holds the Patient, Medication, Organization and Practitioner mass.
  - On the 233 genomics seed, the grouped scores are `CGPatientExample01` 0.657109, `somaticPatient` 0.021501, `HG00403` 0.002478 and `ExamplePatient` 0.000726.
  - The comparison of the ungrouped 233 ranking with 4.1.0 is recorded under R18 (T079).

## R8: `category` and `meta_profile` (FR-016, FR-017, FR-018)

- **Facts**:
  - `LoadColumns` (814) reads only `REFERENCE` and `CANONICAL` SearchColumn rows. The `_profile` exclusion (812-813) is the uri filter.
  - The FHIR sync writes only the `id` property (`EnsureNode`, 1467), and labels only the resource type (1463-1470).
  - `rdf_props` is `(graph_id, s, key, val VARCHAR(64000))` with primary key `(graph_id, s, key)`, so there is one value per key.
  - `category` is an indexed, multi-value token param on Observation, Condition, DiagnosticReport, MedicationRequest, MedicationStatement and AllergyIntolerance (`AllergyIntolerance.category`, a plain `code`). Its table is `HSFHIR_X0001_S_<Type>.category (ID, Key, value_System, value_Text, value_TypeCodingCode, value_TypeCodingSystem, value_Value)`.
  - `_profile` is one SearchColumn row with `ResourceType='Resource'`, FHIRPath `Resource.meta.profile`, multi-value. Its table is `HSFHIR_X0001_S_<Type>._profile (ID, Key, value)`.
  - Every one of those tables has 0 live rows in IVGFHIR. The data exists only in deleted history.
  - The Synthea sample has these categories:
    - Observation (HL7 observation-category): vital-signs 528, laboratory 263, social-history 60, survey 57, exam 6.
    - Condition: encounter-diagnosis 107.
    - DocumentReference (US Core documentreference-category): clinical-note 128.
    - CarePlan (US Core careplan-category): assess-plan 12.
    - DiagnosticReport: LOINC and `v2-0074|LAB`.
  - The indexer writes a second row for a coding when `code.text` differs from its `display`, so token rows must be deduplicated.
- **Decisions**:
  - `LoadColumns` also loads the TOKEN SearchColumn rows for `category` and the `Resource`-level `_profile` row into `..Props(type, prop) = $lb(table, col, multi)`.
  - `ResyncKey` reads them by `Key`. Values are `DISTINCT value_System, value_Value`. A missing system is written as `|code`, the FHIR token form for "no system".
  - The values are stored as `rdf_props` with key `category` or `meta_profile` and a sorted JSON array as `val`. The row is deleted when the list is empty.
  - There are no extra resource reads (spec decision 3).
- **Labels**:
  - Labels are added only for systems in `CATEGORY_LABEL_SYSTEMS`:
    - `http://terminology.hl7.org/CodeSystem/observation-category`
    - `http://terminology.hl7.org/CodeSystem/condition-category`
    - `http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category`
    - `http://hl7.org/fhir/us/core/CodeSystem/careplan-category`
    - `http://hl7.org/fhir/us/core/CodeSystem/condition-category`
    - `http://hl7.org/fhir/us/core/CodeSystem/us-core-category`
  - The label is the code in PascalCase, split on `-` and `_` (FR-017).
  - **Collision rule:** observation-category `procedure` becomes `Procedure`, the name of an R4 resource type. A label that equals an R4 resource type name, from the schema loaded in R1, is not added. The code stays in the `category` property. This is the only collision among the listed systems. Without the rule, `MATCH (p:Procedure)` would return Observations.
  - The node's label set is `{Type} ∪ category labels`. `ResyncKey` diffs it against `rdf_labels` and `^KG("label")` like edges, so a category change removes stale labels.
- **Rationale**: token tables are what the server indexed, and they are already read by `ResolveConcepts`. The label allowlist keeps local codes from becoming labels (spec decision 8).
- **Correction (US5)**: `SELECT DISTINCT` and `GROUP BY` on a SQLUPPER-collated column return the collated, upper-cased form (`VITAL-SIGNS` from `category.value_Value`, `ACCOUNT` from `SearchColumn.ResourceType`). Without DISTINCT the stored case comes back. `SyncProps` therefore reads the rows without DISTINCT and deduplicates in a local array. `Rsrc.ResourceType` is EXACT, so DISTINCT there is safe.
- **Measured (US5, 2026-09-27)**, Synthea fixture:
  - Category labels: AssessPlan 31, ClinicalNote 274, EncounterDiagnosis 226, Exam 45, Laboratory 517, SocialHistory 58, Survey 159, VitalSigns 441.
  - Top `meta_profile` values: us-core-procedure 532, observation-lab 517, us-core-encounter 274, diagnosticreport-note 274, documentreference 274, condition-encounter-diagnosis 226, medicationrequest 196, screening-assessment 159, immunization 81, diagnosticreport-lab 62.
- **Alternatives**:
  - Reading the resource JSON was rejected, because it is an extra read per resource.
  - A prefixed label such as `Cat_Procedure` was rejected: it breaks the "PascalCase code" rule for every code to fix one.

## R9: The `clinical` preset (FR-013, FR-014)

- **Facts**:
  - `ResolveConcepts` (604-697) looks up `SearchColumn WHERE RepoKey=? AND Type='TOKEN' AND ParamName=?` per param, restricted to the resource types present (643-648).
  - Observation `value-concept`, `component-code` and `component-value-concept` are indexed (tables `valueConcept`, `componentCode`, `componentValueConcept`). Medication `code` is indexed and multi-value.
  - MedicationRequest's `code` param indexes `medication as CodeableConcept`.
- **Decision**:
  - `params="clinical"` expands to `["code", "value-concept", "component-code", "component-value-concept"]`. Medication `code` is the `code` param on the Medication type, so it needs no separate entry.
  - The Python side expands the preset, then calls a new `IndexedTokenParams(graph, paramsJson)` that returns which of those params have TOKEN SearchColumn rows for the types present.
  - Unindexed params are dropped and reported as `dropped_params` in the detail result (FR-014).
  - The default `params=["code"]` is unchanged.
- **Rationale**: this reuses the existing lookup, and "indexed" means "has a SearchColumn row", which is what `ResolveConcepts` can query.
- **Alternatives**: a server-side preset table was rejected, because it is a constant that Python can own.
- **Measured (US3, 2026-09-27)**, Synthea fixture plus 233 genomics, one run prefix:
  - Default `params=["code"]`: the fixture's most frequent Condition code (`http://snomed.info/sct|314529007`) resolves to 35 resources, the same set as 4.1.0.
  - `params="clinical"` on the genomics concept `http://identifiers.org/hgnc/2621`: all four params are indexed on this endpoint (`dropped_params: []`), and the result has 8 keys.
  - Clinical resolve median: 15.8 ms.

## R10: The Medication hop (FR-015)

- **Facts**:
  - `medication` is a single-value REFERENCE param (`…medication as Reference`) on MedicationRequest, MedicationStatement, MedicationAdministration and MedicationDispense. It already gives `medication` reference edges.
  - In the Synthea sample, 24 of 28 medication resources use `medicationCodeableConcept` and 4 use `medicationReference`. Across all 109 patients there are 1,026 Medication and 1,026 MedicationAdministration resources.
- **Decision**:
  - After resolution, for each resolved `Medication/…` key, `ResolveConcepts` adds the sources of `^KG("in", g, key, "medication", src)` whose type is one of the four.
  - The detail result tags them `via: "medication"`.
  - Seeds are uniform in `RunJson` (R6), so "seeded with the Medication's weight" means each hop resource is one more seed with the same teleport share.
  - Resolution has no result limit today (`ResolveConcepts` returns every matching key), so the hop is never truncated. The detail result reports `medication_hop: {"medications": n, "added": m}`. The spec's "within existing result limits" assumption is corrected to say so.
  - The hop runs for any `params` value that resolves a Medication, including the default, because FR-015 names no preset. On 4.1.0 data without Medication resources, the result is unchanged.
- **Rationale**: the edges already exist, so the hop is an adjacency lookup.
- **Alternatives**: a two-hop walk through Medication in PPR was rejected: it dilutes the seed across the Medication's other neighbours, which is what the spec's "seeded" wording rules out.
- **Measured (US3, 2026-09-27)**: RxNorm `313521` matches 22 resources directly, 11 of them Medication resources. The hop adds 11 resources (`medication_hop: {"medications": 11, "added": 11}`), each tagged `via: "medication"`.

## R11: Text-only codes and gaps (FR-020)

- **Facts**:
  - `HS.FHIRServer.Storage.JsonAdvSQL.Indexer:IndexCodeableConcept` begins `If tCodeableConcept.coding="" Quit`. A text-only CodeableConcept writes no token row.
  - There is no `:text` param in SearchColumn.
  - The search tables therefore cannot tell text-only from absent. Deleted history in IVGFHIR has 203 text-only cases (for example `Procedure/t5e96348f-lungMass`). Live data has none.
- **Decision**:
  - `fhir_concept_gaps` computes the unmatched `(system, code)` pairs from the token tables: `DISTINCT Key` per pair, left-anti-joined to `code_crosswalk`, top 20 by resource count.
  - For the text-only count, it takes live resources of each type that has the param indexed and has **no** token row for it. It reads only those resources' JSON from `Rsrc`, and counts those whose field (from the SearchColumn FHIRPath) has `text`.
  - A small path helper `_token_field(fhirpath)` covers the four preset shapes: `X.code`, `(X.value as CodeableConcept)`, `X.component.code` and `(X.component.value as CodeableConcept)`. It has unit tests.
- **Rationale**: only resources with no coding are read, which is few in practice, so the 2 s budget (SC-007) holds.
- **Alternatives**: reading every resource's JSON was rejected, because it is O(repository).
- **As built (US4)**: both reports take an `id_prefix` that scopes them to the resources whose id starts with it, so a test run on the shared IVGFHIR repository sees only its own resources. The coverage report lists the paths it could not map as `skipped_paths`. Patient is never a compartment type, and every type carries `meta_profile` and `category`.
- **Measured (US4, 2026-09-27)**, Synthea fixture, median of 5: coverage report 229 ms, `fhir_concept_gaps(params="clinical")` 198 ms (SC-007: under 2 s).

## R12: Where `interpretation_version` lives (FR-022)

- **Facts**:
  - `Graph_KG.fhir_graphs` (`iris_vector_graph/schema.py:401-419`) has no migration path. Only `CREATE TABLE IF NOT EXISTS` exists, and `json_links` was added the same way.
  - The pattern for an idempotent added column is `_ensure_registry_route_columns` (`_engine/schema.py:462-484`).
  - `ALTER TABLE … ADD COLUMN` with a default does not backfill existing rows (memory: IRIS ADD COLUMN DEFAULT).
  - `Eraser.EraseFHIRGraphRows` (539) keeps the registry row and zeroes the watermarks.
  - `fhir_graphs` is not in the snapshot plan (`_engine/snapshot.py` `STORE_PLAN`).
- **Decision**:
  - Add a column `interp_version INTEGER` (nullable) to the DDL, plus `_ensure_fhir_graph_columns()`, which runs `ALTER TABLE Graph_KG.fhir_graphs ADD COLUMN interp_version INTEGER` when the column is missing.
  - NULL means absent. The current version is `Parameter INTERPVERSION = 1;` on `Graph.KG.FHIRGraph`.
  - The marker is set only after the last batch of a full re-derivation commits. An interruption leaves it unchanged (FR-022).
  - `EraseFHIRGraphRows` sets it to NULL with the watermarks.
- **Rationale**: the registration row is the graph's record, it is visible to SQL and to `Status`, and it goes when the graph is unregistered.
- **Alternatives**:
  - A `^IVG.FHIRGraph(graph, "interp")` global was rejected: it is invisible to SQL, Eraser does not clear it, and it outlives the row.
  - Packing the marker into `last_counts` JSON was rejected, because that field is overwritten by `Rebuild`.
  - This is a schema change, and it is justified in the plan's Complexity Tracking.

## R13: Full re-derivation (FR-022, FR-023, SC-008)

- **Facts**:
  - `Rebuild` (382) runs `SyncAllDefinitions` and then `ResyncKey` for every Rsrc key in batches of 200, drops stale nodes, and sets the watermarks and `last_counts`.
  - `SyncOnce` (461) processes only `ID > watermark`.
- **Decision**:
  - A new private `Rederive(pGraph)` runs the Rebuild resync loop over all live keys (`pDefs=0`), in batches, without touching the watermarks.
  - `SyncOnce` calls it first when `interp_version` is NULL or below `INTERPVERSION`, then does its normal incremental pass, and sets `full_rederivation: true`.
  - `Rebuild` always ends by setting the marker.
  - `Reinterpret(pGraph)` takes the same lock, runs `Rederive`, and sets the marker.
  - All three return the `interpretation` block (FR-025).
- **Rationale**: `ResyncKey` already derives everything, so re-derivation is the same code over all keys, and the full and incremental paths cannot diverge.
- **Alternatives**: a SQL-only backfill of compartment edges from existing reference edges was rejected. It would be a second implementation of R2, and it cannot set `category` or `meta_profile`.
- **Measured (US7)**, on `ivg-iris-enterprise`:
  - The first sync of the fixture with the marker NULL reported `compartment_edges_added` 3,432, `category_set` 2,309, `meta_profile_set` 3,269 and `full_rederivation: true`.
  - `fhir_reinterpret` on the fixture graph took a median of 18,705 ms over 3 runs (18,153 / 18,705 / 21,110). That covered 20,016 live resources, because the repository also holds other runs' resources. A repeat run changes nothing: every count in the `interpretation` block is 0.
  - `Rederive` reads only live keys (`Deleted IS NULL OR Deleted = 0`). `Rebuild` still resyncs every key, deleted ones too, so it slows as deleted history grows. See "Measured (bench)" under R17.
  - `test_410_to_42` (SC-008) passed in 486 s. A graph synced by the 4.1.0 classes, then synced once by 4.2, equals a fresh 4.2 sync of the same resources in edges, properties and labels.
  - Deviations from tasks.md:
    - The failure hook is `^IVG.FHIRGraphFault(graph) = "rederive:<n>"`, not a `^IVG.Test` global.
    - `SetInterpVersion` adds the column itself (`ALTER TABLE`) when a namespace was redeployed without `initialize_schema`. Without that, every sync there would re-derive in full.

## R14: Existing counts (FR-026)

- **Facts**:
  - `Counts()` (1530) buckets `SELECT s, p FROM rdf_edges WHERE graph_id=?` by `Type.p`.
  - `Status` (243-266) computes `edges` as an unfiltered `COUNT(*)`.
  - `last_counts` is written only by `Rebuild` (440-441).
  - `LinkReport` (292) reads only `fhir_canonical_refs`. It has `totals.by_param` and no `edges` field.
- **Decision**:
  - `Counts()` and `Status.edges` add `AND p <> 'in_patient_compartment'`. `Status` adds `compartment_edges` and `interpretation_version`.
  - `LinkReport` is unaffected by construction.
  - The spec's FR-026 wording "`edges` and `params` in `fhir_link_report`" is corrected to "`totals` in `fhir_link_report`".
- **Rationale**: SC-009 compares these figures to 4.1.0 on the same data.
- **Alternatives**: none.

## R15: Graph-first patient anchors (FR-021)

- **Facts**:
  - `_resolve_patient_anchors(req)` (`cypher_api.py:228-257`) returns `list[str]`. It runs an external `Condition?patient=` search through `fhir_bridge`, keeps ICD-10 codes, and maps them through `get_kg_anchors`. Any failure returns `[]`.
  - `CypherRequest` has `fhir_patient_id`, `fhir_base_url` and `fhir_auth`.
  - Only mocked unit tests cover this path, such as `tests/unit/test_231_cypher_api_fhir_allowlist.py`.
  - Registered FHIR graphs are the rows of `Graph_KG.fhir_graphs`.
- **Decision**:
  - A new engine method, `fhir_patient_anchors(patient_id, graph=None)`:
    1. Lists `graph_id` from `fhir_graphs`, or takes the one given.
    2. Keeps the graphs holding node `Patient/<id>`.
    3. For each, calls a new `PatientConcepts(graph, patientKey, paramsJson)`. It walks `^KG("in", g, patientKey, "in_patient_compartment", res)`, reads the `clinical` token rows by `Key`, and maps `(system, code)` through `code_crosswalk` to concept ids.
    4. Returns `[{id, graph}]`, deduplicated per `(id, graph)` and sorted.
  - `_resolve_patient_anchors` calls it first:
    - If any graph holds the patient, the anchors are the graph's, even when that list is empty, and `anchor_source = "graph"`.
    - Otherwise it runs the existing bridge, unchanged, and `anchor_source = "fhir_bridge"`.
  - `params["patient_anchors"]` stays `list[str]` for queries. When `fhir_patient_id` is set, the `/api/cypher` response dict gains `anchor_source` and `anchors: [{id, graph}]`. There is no response model today; `cypher_query` returns `_run_cypher`'s dict.
  - `CypherRequest` gains an optional `fhir_graph`.
- **Rationale**: the query parameter shape is unchanged (Principle II). The provenance of each anchor goes in the response.
- **Alternatives**: falling back to the bridge when the graph finds the patient but yields no anchors was rejected, because the answer's source would then depend on crosswalk coverage.
- **As built (US6)**:
  - `PatientConcepts` returns `{"present": bool, "concepts": [...]}`, so one call answers both "does this graph hold the patient" and "what are its anchors".
  - `_resolve_patient_anchors` keeps its 4.1.0 `list[str]` shape and the 231 tests unchanged. A new `_patient_anchors(req)` wraps it: graph first, then the bridge. The unit tests target `_patient_anchors`.
  - A lookup error on the graph side falls back to the bridge.
  - The two-graph E2E registers a scratch graph over the same repository by copying the `fhir_graphs` row with its watermarks just below the run's first `Rsrc.ID`. One `SyncOnce` then projects only the run, where a `Rebuild` would project the whole shared repository.
- **Measured (US6, 2026-09-27)**: `PatientConcepts` takes 0.8 ms for a patient with no crosswalked codes.

## R16: The Synthea fixture (spec decision 17)

- **Facts**:
  - `synthetichealth/synthea-sample-data` (commit `9959d9178ea28f4ec10f17ee238b6fabe6eb0de5`) has **no LICENSE file**. GitHub reports none.
  - The generator `synthetichealth/synthea` is Apache-2.0. Release `v4.0.0` (2026-03-05) has a 201 MB jar.
  - Java 21 is installed.
  - Patient bundles are `transaction` Bundles:
    - `urn:uuid:` fullUrls and `POST` requests;
    - 36-character ids, which is 46 with the test prefix and within 64;
    - Practitioner, Organization and Location references as conditional `Type?identifier=…`, satisfied by two `batch` support bundles (`hospitalInformation*`, `practitionerInformation*`).
  - The 10 smallest sample patients hold 2,275 resources, 7.1 MB raw. Without narrative and base64 attachment data they are 4.4 MB. Without Claim and ExplanationOfBenefit as well, they are 2.2 MB.
  - Patient.link: 0 of 109 patients.
- **Decision**:
  - Generate, do not copy the sample. The pinned v4.0.0 jar (sha256 in `SOURCE.md`) runs with `-s 235 -cs 235 -r 20260101 -e 20260927 -p 10 --exporter.years_of_history 5 --exporter.fhir.export true`.
  - `tests/e2e/interp_fixture.py vendor <synthea-output-dir>` then:
    1. rewrites `urn:uuid` per bundle to `Type/id`;
    2. resolves conditional references to `Type/id` through the support bundles, and keeps only the support resources that are referenced;
    3. drops Claim and ExplanationOfBenefit;
    4. strips narrative and `attachment.data`;
    5. adds one `Patient.link` (`seealso`) between the first two patients, so the linked-patient count has data;
    6. writes `tests/e2e/fixtures/fhir/synthea/synthea-r4-10.json`, a `collection` Bundle, and prints the counts that `SOURCE.md` records.
  - Loading and prefixing reuse 233's `prefix_resources`, `load_run` and `teardown_run`.
- **Measured (T010, 2026-09-26)**:
  - The vendored file is 5,644,440 bytes (4.1 MB compact): 3,558 resources, above the 3 MB mark. It is kept whole, not trimmed. The 10 generated patients have more history than the 10 smallest sample patients (1,230 Observations).
  - `-e 20260927` is pinned as well. Without it Synthea simulates up to the day it runs, so output changes from one day to the next. With it, a rerun gives the same sha256.
  - The v4.0.0 jar reports itself as `v3.4.0-18-ga07a65555` in its run metadata.
  - `DiagnosticReport.presentedForm` holds Attachments directly, not under an `attachment` key. `assemble` therefore strips `data` from any object with a `contentType`.
  - Unreferenced support resources are dropped: all 38 PractitionerRoles, 7 Organizations, 8 Locations and 7 Practitioners.
- **Rationale**:
  - Output generated by us from Apache-2.0 software, with the seed and jar pinned, is reproducible and carries no unclear licence.
  - Every edit is listed in `SOURCE.md`, like the CPG and genomics fixtures.
- **Alternatives**:
  - Vendoring from the sample-data repo was rejected, because it has no licence. The spec's "Apache-2.0 sample" assumption is corrected.
  - Keeping Claim and ExplanationOfBenefit was rejected. They double the size and add no param that other types do not already exercise (`patient`).

## R17: Performance baseline (SC-005, SC-006, SC-007)

- **Facts**:
  - `test_231_fhir_concept_pipeline_e2e.py` measures `_median_ms(fn, runs=7)` with `perf_counter`. `test_231_fhir_graph_sync_e2e.py::test_500_changes_within_10s` times one sync.
  - `docs/FHIR_GRAPH.md` records resolve at 6.6 ms and the pipeline at 67 ms.
  - The FHIR server lives only in `IVGFHIR`, so both sides must run in that namespace.
- **Decision**:
  - `scripts/fhir/bench_235.py` measures:
    - median `Rebuild` wall time over 5 runs on the fixture graph;
    - median `fhir_concept_ppr` over 7 runs, with and without `group_by="patient"`;
    - median `fhir_coverage_report` and `fhir_concept_gaps`.
  - It writes JSON.
  - The **baseline** run uses a `git archive` of the 4.1.0 commit, deployed into `IVGFHIR` over TCP (`tcp-deploy`). The commit is the release tag once cut. Until then it is `main` after the 233 merge, and the sha is recorded.
  - The 4.2 run follows back to back on the same container. The script exits non-zero on sync > +15%, group-by > +20%, or reports ≥ 2 s.
  - The ~100-patient set is generated into `~/.cache/ivg-235/synthea-100` with `-p 100`, not vendored, and is loaded under its own prefix by the same script.
  - `specs/235-fhir-graph-interpretation/baseline.json` holds both runs. `docs/FHIR_GRAPH.md` records the medians, and a docs-parity unit test checks them against `baseline.json`.
  - The E2E suite also asserts SC-007 (< 2 s) on the fixture.
- **Rationale**: a relative budget needs both sides measured on one machine, one container and one dataset.
- **Alternatives**: an absolute budget was rejected, because the spec states relative ones.
- **Measured (T014)**, on `ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`), commit 2d92da2:
  - No `git archive` deploy was needed. `git diff 2d92da2 -- iris_src iris_vector_graph` was empty, so the deployed engine already was 4.1.0.
  - Fixture (3,558 resources loaded, 18,896 in the repository): sync 7,433 ms, PPR 479 ms, 89 seeds.
  - Scale (~100 patients, 70,398 in the repository): sync 27,322 ms, PPR 7,557 ms, 985 seeds.
  - Loads use FHIR `batch` bundles of at most 200 entries and 1 MB (`fhir_conftest.batches`). One bundle of 200 scale resources passed the 3.6 MB `<MAXSTRING>` limit.

- **Measured (bench, T078)**, both sides back to back on one container, baseline deployed from a `git archive` of 2d92da2 (`baseline.json`; the T014 run is kept as `baseline_t014.json`, and 4.2 is in `current.json`):

  | Set     | Measure        | 4.1.0  | 4.2    | Ratio |
  | ------- | -------------- | ------ | ------ | ----- |
  | fixture | `sync_ms`      | 35,885 | 37,348 | 1.04  |
  | fixture | `ppr_group_ms` | 481    | 617    | 1.28  |
  | scale   | `sync_ms`      | 47,057 | 52,401 | 1.11  |
  | scale   | `ppr_group_ms` | 8,132  | 9,024  | 1.11  |
  | scale   | `coverage_ms`  | –      | 831    |       |
  | scale   | `gaps_ms`      | –      | 1,450  |       |

  The grouped PPR ratio is against the 4.1.0 ungrouped PPR, and it is budgeted at scale only. The findings:
  - **Deleted history.** `HSFHIR_X0001_R.Rsrc` now holds 181,941 rows, 165,483 of them deleted, left by earlier test loads. `Rebuild` resyncs every key, and each deleted key goes through `DropNode`. That is why the fixture sync is 36 s here against T014's 7.4 s, on both sides.
  - **Machine load.** One run of the same code measured 142 s against a 97 s baseline, and a later run 46 s. Only pairs taken back to back are compared, and the scale pair above ran with load average about 40.
  - **`DropNode`.** It ran a `rdf_labels` SELECT for every deleted key to clear category labels. It now runs the SELECT only when the key has its type label in `^KG("label")`, because `SyncProps` writes category labels only beside the type label.
  - **Exclusion in PPR.** `PageRank.Run` checks a reverse edge's exclusion once per predicate. It calls `Excluded` per source only for a typed entry, so the walk with no exclusion adds no call.
  - **`<MAXSTRING>` at scale.** `group_by="patient"` passed every score to `GroupByPatient` as one JSON string. At scale that string passed IRIS's ~3.6 MB limit (80,000 scores are 4.8 MB), and the call failed. `FHIRGraph.GroupPPR` now runs `PageRank.Run` into a local array and groups it in the same process. Its output equals `RunJson` followed by `GroupByPatient` (`tests/integration/test_235_group_by_patient.py`). `GroupByPatient` keeps its contract.

## R18: Effect on 233 and on existing tests (FR-010, SC-009)

- **Facts**:
  - 233's provenance test calls `kg_PERSONALIZED_PAGERANK` directly (`test_233_provenance_e2e.py:68`), so it gets no exclusions.
  - 233's concept tests call `fhir_concept_ppr` on a fixture with no Provenance resource.
  - `test_231_fhir_graph_build_e2e.py:243` asserts `st["edges"] >= 5`, and `test_fhir_demo_e2e.py:46` asserts `stats["edges"] > 0`.
- **Decision**: no ranking change is expected in 233. T-gate SC-009 runs 231, 232, 233 and `test_fhir_demo_e2e.py` unchanged. If a 233 ranking moves, `docs/FHIR_GRAPH.md` records it with the Provenance denylist as the cause (spec US3 scenario 5).
- **Rationale**: measured call sites.
- **Alternatives**: none.

### Measured (T079)

- `baseline_410.json` (T003) no longer compares on its whole-graph keys. The repository has grown since T003 because `test_231_fhir_graph_sync_e2e.py` has no teardown and keeps its resources on every run (nodes 15338 → 17004). That is data drift, not a code change.
- A paired capture replaces it. The 4.1.0 classes are deployed and `capture_410_baseline` is run under the 2d92da2 Python. The archive has to go first on `sys.path`, because `-m` puts the cwd ahead of `PYTHONPATH`. The current classes are then deployed and `--compare` is run on the same data.
- Result: `status_edges`, `last_counts`, `link_report` and every `ppr_231` score are equal. There are 756 diffs, all in `ppr_233` (3 seeds × 189) and `ppr_provenance` (189):
  - `ppr_233` is the documented Provenance change. With `exclude_predicates=["in_patient_compartment"]` (no `Provenance.*` entries) every score is within 1e-12 of 4.1.0. With the default `FHIR_PPR_EXCLUDE`, `Device/b410-model-v7` drops out of the HGNC 2621 ranking and the rest move by up to 7e-5.
  - `ppr_provenance` calls `kg_PERSONALIZED_PAGERANK` with no exclusions, so it walks the new `in_patient_compartment` edges. Excluding that one predicate gives 0 diffs. `exclude_predicates=None` keeps 4.1.0's walk; the graph it walks now has more edges.
- The E2E suites from T002 (231, 232, 233, `test_fhir_demo_e2e.py`) equal `baseline.txt`: 176 passed, 1 failed, the known `test_231_ppr_graph_e2e::test_default_graph_walk_stays_in_the_default_graph`. `test_fhir_demo_e2e` once raised EPIPE in teardown at `conn.close()` after the test passed; it passed on rerun.

Nothing is left as NEEDS CLARIFICATION.
