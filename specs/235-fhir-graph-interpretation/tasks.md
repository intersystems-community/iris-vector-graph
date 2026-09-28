<!-- markdownlint-disable MD013 -->

# Tasks: FHIR graph interpretation contract

**Input**: `specs/235-fhir-graph-interpretation/` (plan.md, spec.md, research.md, data-model.md, contracts/python-api.md, contracts/objectscript.md, contracts/fixture.md, quickstart.md)

**Tests**: mandatory. Unit tests come first in every phase and must be seen failing before the code they cover. Each story's E2E file is its phase gate, run on `ivg-iris-enterprise` with `IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972`, namespace `IVGFHIR`.

**ObjectScript**: before editing any `.cls`, run `skill(action="describe")` for `objectscript-guardrails` and `objectscript-review`. Deploy with `scripts/enterprise-container.sh tcp-deploy` (TCP, not irispython: dual-DB split). A compile error fails the task.

**Format**: `- [ ] T### [P?] [US?] description with path`

## Phase 1: Setup

- [x] T001 Check the prerequisite (research R0): `git merge-base --is-ancestor 18feb40 HEAD` and `git merge-base --is-ancestor c733211 HEAD` both succeed. Done 2026-09-26: `233-fhir-genomics` merged into `main` locally (2d92da2, `--no-ff`, not pushed) and `235-fhir-graph-interpretation` fast-forwarded to it.
- [x] T002 Start `scripts/enterprise-container.sh up` if it is not running. Run `.venv/bin/pytest tests/e2e/test_231_*.py tests/e2e/test_232_*.py tests/e2e/test_233_*.py tests/e2e/test_fhir_demo_e2e.py` plus `tests/e2e/test_232_fhir_cpg_smoke_e2e.py` explicitly, and `.venv/bin/pytest tests/unit -q`. Record pass/fail in `specs/235-fhir-graph-interpretation/baseline.txt` (SC-009 before state; 1 known khop unit failure).
- [x] T003 Capture the 4.1.0 behaviour baseline before any engine change, as `specs/235-fhir-graph-interpretation/baseline_410.json`, with a throwaway script `scripts/fhir/capture_410_baseline.py` (kept; it is re-run in T079):
  - `fhir_concept_ppr` score maps (default args, `return_top_k` as the tests use) for the 231 concept pipeline seeds and the four 233 genomics seeds;
  - `fhir_graph_status(GRAPH)["edges"]` and `last_counts`;
  - `fhir_link_report(GRAPH)` in full;
  - `kg_PERSONALIZED_PAGERANK` on the 233 provenance seed.

  Record the commit sha in the file. Load fixtures through the existing `genomics_loaded` path and 231's loaders with a fixed prefix `b410` so T079 can reload the same keys.

- [x] T004 Download the Synthea v4.0.0 jar to `~/.cache/ivg-235/synthea-4.0.0.jar` and record its sha256. Run the generation commands in `contracts/fixture.md` for `-p 10` into `~/.cache/ivg-235/synthea-10` and for `-p 100` into `~/.cache/ivg-235/synthea-100`. Record the Java version. Nothing under `~/.cache` is committed.

## Phase 2: Foundational (blocks all stories)

The Synthea fixture, its loader, and the bench harness. Every story's E2E loads the same vendored file.

### Tests first (Foundational)

- [x] T005 [P] Write `tests/unit/test_235_fixture.py::TestAssemble` for `assemble(patient_bundles, support_bundles)` in `tests/e2e/interp_fixture.py`, on small synthetic bundles:
  - `urn:uuid:` references are rewritten per bundle to `Type/id`, and two bundles reusing one uuid resolve through their own maps;
  - a conditional `Practitioner?identifier=sys|val` resolves through the support bundles; an unresolvable one raises `ValueError`;
  - only referenced support resources are kept;
  - `Claim` and `ExplanationOfBenefit` are dropped, and references to them removed (a Claim is never a target in Synthea; assert that too);
  - `text` and every `attachment.data` are stripped at any depth;
  - one `Patient.link` of type `seealso` is added both ways between the first two patients sorted by id;
  - every remaining `reference` is `Type/id` in the set;
  - the counts dict reports per type, `rewritten`, `conditional`, `links_added`;
  - the input is not mutated.
- [x] T006 [P] Write `tests/unit/test_235_fixture.py::TestVendoredFile` against `tests/e2e/fixtures/fhir/synthea/synthea-r4-10.json`: a `collection` Bundle; unique `Type/id` keys; every id at most 54 characters; every reference is `Type/id` in the set; no `urn:` or `?identifier=` reference; no `text`; no `attachment.data`; exactly one pair of Patients linked both ways; per-type counts equal the counts block in `SOURCE.md`; the file sha256 equals `SOURCE.md`. When `~/.cache/ivg-235/synthea-10/fhir` exists, re-running `assemble` over it reproduces the file byte for byte (skip only that test when absent; record the exemption in `SOURCE.md`, as 233 did).
- [x] T007 [P] Write `tests/unit/test_235_bench.py` for `compare(baseline, current)` in `scripts/fhir/bench_235.py`: sync +15.0% passes and +15.1% fails; group-by PPR is compared with the baseline default PPR median, +20% limit; reports at 2.0 s fail; a missing measurement fails; the exit code is 1 on any breach and the breaches are printed.
- [x] T008 Run T005–T007 and confirm they fail (ImportError or missing file).

### Implementation (Foundational)

- [x] T009 Implement `assemble`, `FIXTURE`, `DROP_TYPES` and the `vendor <synthea-fhir-dir>` CLI in `tests/e2e/interp_fixture.py` per `contracts/fixture.md` and research R16. The CLI reads `*.json` in the directory, splits patient bundles from `hospitalInformation*` / `practitionerInformation*` support bundles, writes `FIXTURE` with sorted keys and a 1-space indent, and prints the counts block. Make T005 pass.
- [x] T010 Run `.venv/bin/python -m tests.e2e.interp_fixture vendor ~/.cache/ivg-235/synthea-10/fhir`. If the file exceeds 3 MB, record the size in research R16 and continue (no silent trimming).
- [x] T011 Write `tests/e2e/fixtures/fhir/synthea/SOURCE.md` per `contracts/fixture.md` (generator, Apache-2.0, jar URL and sha256, Java version, command and seeds, output sha256, the edits, the synthetic `Patient.link` and why, the counts block, the test-skip exemption). Run `markdownlint-cli2 --fix` and `prettier --write` on it. Make T006 pass.
- [x] T012 Add a module-scoped `synthea_loaded` fixture to `tests/e2e/fhir_conftest.py`, modelled on `genomics_loaded`: `fhir_conn_required`, `prefix_resources(load(FIXTURE), prefix)`, PUT in dependency order (Organization, Location, Practitioner, Patient, Medication, Encounter, then the rest), fail listing any rejected resource, register with `denylist=[]`, sync, yield `(prefix, resources)`, and `teardown_run` at the end. Add `load_synthea()` to `tests/e2e/interp_fixture.py`.
- [x] T013 Implement `scripts/fhir/bench_235.py` per `contracts/fixture.md` §Bench: `--label`, `--repo`, `--set {fixture|scale}`, `--out`, and `compare`. Measurements a 4.1.0 checkout does not support (group-by, reports) are skipped when `--label baseline`. Make T007 pass.
- [x] T014 Run the bench baseline against 4.1.0 before any engine change: `git archive` the T003 sha to `~/.cache/ivg-235/src_<sha>`, `tcp-deploy` it, run `bench_235.py --label baseline --set fixture` and `--set scale`, then redeploy this branch. Store both results in `specs/235-fhir-graph-interpretation/baseline.json`.

**Checkpoint**: T005–T007 pass; `.venv/bin/pytest tests/e2e/fhir_conftest.py --collect-only` is clean; `ruff check tests/e2e/interp_fixture.py tests/e2e/fhir_conftest.py scripts/fhir/` is clean; a smoke run of `synthea_loaded` (a one-test scratch file, deleted afterwards) loads and tears down with zero rejections.

## Phase 3: User Story 1, every resource knows its patient (P1) 🎯 MVP

**Goal**: one `in_patient_compartment` edge per `(resource, Patient)`, with a sorted `via`, derived inside `ResyncKey`, owned by the source, and equal to the server's compartment tables.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_compartment_e2e.py -s`.

### Tests first (US1)

- [x] T015 [P] [US1] Write `tests/unit/test_235_compartment.py` for pure helpers in `tests/e2e/interp_fixture.py`:
  - `expected_compartments(resources, comp, paths)`: `subject` and `performer` to the same Patient give one entry with `via=["performer","subject"]`; different Patients give two entries; a Group subject gives none; a Patient resource gives none (its `link` ignored); a reference to a Patient not in the set gives none; Provenance `target` containing a Patient gives `via=["patient"]`;
  - `reference_paths(fhirpath)`: turns SearchColumn FHIRPath strings into element paths, covering `X.subject`, `(X.medication as Reference)`, `X.target.where(resolve() is Patient)` and `|` unions.
- [x] T016 [P] [US1] Write `tests/integration/test_235_compartment_params.py` (Principle IV, direct classmethod over TCP): `Graph.KG.FHIRGraph.CompartmentParams(GRAPH, "Observation")` returns `["performer","subject"]`; `Provenance` → `["patient"]`; `Patient` → `["link"]`; `Medication` → `[]`; an unknown compartment name returns `[]`, not an error. Also assert that the 66 types with patient params equal those in `CompartmentDefinition-patient.json` read from the container (research R1).
- [x] T017 [US1] Write `tests/e2e/test_235_compartment_e2e.py` with `pytestmark = [pytest.mark.e2e]`, `SKIP_IRIS_TESTS` defaulting to `"false"`, using `synthea_loaded` and `genomics_loaded`:
  - `test_membership_matches_server` (SC-001): per compartment type, the oracle SQL in `contracts/fixture.md` (self rows and dangling rows dropped, run keys only) equals `{(s, o_id)}` over `in_patient_compartment` edges; mismatches are printed per type; there are no documented exclusions;
  - `test_via_matches_fixture` (FR-002): `qualifiers` parsed as JSON equals `{"via": expected_compartments(...)}` for every edge;
  - `test_scenario_1_two_params_one_edge`: PUT an Observation with `subject` and `performer` both `Patient/<run p1>`, sync, one edge with `via=["performer","subject"]`;
  - `test_scenario_2_encounter`: an Encounter with `patient` gives `via=["patient"]` (R4 lists only `patient` for Encounter; research R3 corrected);
  - `test_scenario_3_patient_skipped`: no compartment edge has a `Patient/` source; the synthetic `link` edges are present and unchanged;
  - `test_scenario_4_group_subject`: an Observation with a Group `subject` gets no edge;
  - `test_scenario_5_subject_change`: PUT with `subject` changed to `p3`, sync, the edge points to `p3` only, in `rdf_edges` and in `^KG("out")` and `^KG("in")`;
  - `test_scenario_6_delete`: DELETE, sync, no compartment edge with that source, and none in `^KG`;
  - `test_scenario_7_full_equals_incremental` (FR-008): snapshot the run's edge, prop and label sets, run `fhir_graph_rebuild(GRAPH)`, compare equal;
  - `test_scenario_8_ordinary_edges` (FR-007): `verify_graph(GRAPH)` is clean; a snapshot/restore of a scratch copy keeps the compartment edges;
    - Done differently: the test checks the snapshot's sql and globals entries rather than restoring it, because a restore replaces the whole IVGFHIR namespace.
  - `test_cypher_match`: `USE GRAPH '<g>' MATCH (o:Observation)-[:in_patient_compartment]->(p:Patient)` row count, filtered to run keys, equals the edge count;
  - `test_existing_counts_exclude` (FR-026): `fhir_graph_status(GRAPH)` has `compartment_edges` equal to the run's edge count plus the others, and `edges` equals `SELECT COUNT(*) … WHERE p <> 'in_patient_compartment'`; `fhir_link_report` totals have no `in_patient_compartment` param.
- [x] T018 [US1] Run T015–T017 and confirm they fail.

### Implementation (US1)

- [x] T019 [US1] Implement `expected_compartments` and `reference_paths` in `tests/e2e/interp_fixture.py`. Make T015 pass.
- [x] T020 [US1] In `iris_src/src/Graph/KG/FHIRGraph.cls`: add `LoadCompartment(pSchema, pCompartment = "patient")` filling `..Comp(type, param)` and `..R4Types(type)`; call it from `Open` after reading `metadataSetKey` from `HS_FHIRServer.RepoInstance` and `LoadSchema`; add `ClassMethod CompartmentParams(pGraph, pType, pCompartment = "patient")`. Deploy. Make T016 pass.
  - Done differently: `LoadCompartment` is called at the end of `LoadColumns`, not from `Open`. Every sync, rebuild and status path calls `LoadColumns`, and `Open` stays cheap. `CompartmentParams` returns `{"status","type","params"}`. `HS.FHIRServer.Schema` is called with `$ClassMethod` because the class does not exist in USER, where `tcp-deploy` compiles.
- [x] T021 [US1] In `ResyncKey`, after `tWant` is built and before the diff, derive `tWant("in_patient_compartment", target) = $lb("via", sorted csv)` for non-Patient types, per research R2. Count inserts and deletes of that predicate into `..Interp("added")` / `..Interp("removed")`. In `Qualifier`, write `via` as a JSON array. Deploy.
- [x] T022 [US1] In `Counts` and `Status`, add `p <> 'in_patient_compartment'`; `Status` adds `compartment_edges`. Deploy.
- [x] T023 [US1] Run T017 live. A membership mismatch is a finding: re-probe `SearchColumn` and the Compartments tables, record the cause in research R5 under "Measured (US1)", and fix derivation (not the oracle) unless the server is wrong, in which case the exclusion is documented in the test and in research. Record per-type edge counts in research.md "Measured (US1)".
- [x] T024 [US1] Run `.venv/bin/pytest tests/e2e/test_231_*.py tests/e2e/test_232_*.py tests/e2e/test_233_*.py tests/e2e/test_fhir_demo_e2e.py` and compare with `baseline.txt`. Any new failure blocks.

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/integration/test_235_compartment_params.py tests/e2e/test_235_compartment_e2e.py -s` passes, and T024 shows no regression.

## Phase 4: User Story 2, rank patients, not resources (P1)

**Goal**: default PPR excludes compartment and Provenance fan-out edges and matches 4.1.0; `group_by="patient"` sums contributor scores with `via`, `explain_top` and `unattributed`.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_patient_ranking_e2e.py -s`.

### Tests first (US2)

- [x] T025 [P] [US2] Write `tests/unit/test_235_ppr_exclude.py`:
  - `_exclude_matches(entry, source_key, p)` in `iris_vector_graph/_engine/algorithms.py`: bare `p` matches any source type; `Provenance.target` matches only `Provenance/…` sources; `AuditEvent.entity` is not matched by `Provenance.entity`; a malformed entry (`""`, `".p"`, `"Type."`) raises `ValueError`;
  - the Python fallback PPR with `exclude_predicates=None` gives the same scores as today on a small in-memory graph, and with an exclusion gives the scores of the graph with those edges deleted (divisor included);
  - `kg_PERSONALIZED_PAGERANK(..., exclude_predicates=[...])` passes a JSON array and a limit to `execute_ppr` (mocked store); `None` passes neither, so the call is byte-identical to 4.1.0.
- [x] T026 [P] [US2] Update `tests/unit/test_store_protocol.py`: `MockGraphStore.execute_ppr` accepts `exclude=None, limit=None`, and the protocol conformance check covers the new keywords (Gate 4).
- [x] T027 [P] [US2] Write `tests/unit/test_235_group_by.py` for `fhir_concept_ppr` in `iris_vector_graph/_engine/fhir_graph.py` with `_fhir_call` and PPR mocked:
  - default call passes `FHIR_PPR_EXCLUDE` and returns `{key: score}` unchanged;
  - `exclude_predicates=[]` passes no exclusions;
  - `group_by="patient"` runs PPR with limit 0 and passes scores, `via`, `explain_top`, `top_k` to `GroupByPatient`;
  - `via` or `explain_top` without `group_by` raises `ValueError`; `group_by="encounter"` raises `ValueError`; `explain_top=-1` raises `ValueError`; a `via` entry with characters outside `[a-z-]` raises `ValueError`;
  - `params="clinical"` is accepted and forwarded.
- [x] T028 [P] [US2] Write `tests/integration/test_235_pagerank_exclude.py` (direct `Graph.KG.PageRank.RunJson` over TCP on a scratch graph): with `pExclude=""` and `pLimit=1000` the output equals the six-argument call byte for byte; excluding a predicate equals the result on a copy of the graph without those edges (1e-12); `Type.p` excludes only that source type; `pLimit=0` returns every scored node; `pLimit=3` returns the top 3.
- [x] T029 [P] [US2] Write `tests/integration/test_235_group_by_patient.py` (direct `FHIRGraph.GroupByPatient` on a scratch graph with hand-written compartment edges and qualifiers): sums; tie order by patient key; `pExplainTop=2`; `pTopK=0` means all; `pViaJson='["subject"]'` drops a `performer`-only contributor into `unattributed`; `contributors_total` counts all kept contributors, not the cut list; a scored Patient node is unattributed.
- [x] T030 [US2] Write `tests/e2e/test_235_patient_ranking_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded` and `genomics_loaded` plus the 233 concept graph fixture:
  - `test_default_equals_410` (scenario 1, FR-010, SC-002): reload the `b410` keys, run the T003 calls, compare with `baseline_410.json` within 1e-12; print any 233 difference;
  - `test_group_sum` (scenario 2, SC-003): from a Synthea condition concept (crosswalked in a module fixture, dropped in `finally`) and a 233 gene seed, every patient's `score` equals the sum of all its contributors' PPR scores from an uncapped `group_by=None, top_k=None` run (1e-12); five contributors by default, two with `explain_top=2`;
  - `test_via_filter` (scenario 3): with `via=["subject"]`, every returned contributor's `via` contains `subject`;
  - `test_unattributed` (scenario 4): `unattributed.count` plus the sum of `contributors_total` equals the number of scored resources; the score mass adds up to the total;
  - `test_provenance_not_walked` (scenario 5): on the Synthea graph, the default run gives the same scores as a run with `Provenance.target`/`Provenance.entity` edges removed from a scratch copy; with `exclude_predicates=[]` the Provenance nodes score above 0;
  - `test_cypher_unaffected`: `MATCH (pv:Provenance)-[:target]->(x)` still returns rows.
- [x] T031 [US2] Run T025–T030 and confirm they fail.

### Implementation (US2)

- [x] T032 [US2] In `iris_src/src/Graph/KG/PageRank.cls`, add `pExclude As %String = ""` and `pLimit As %Integer = 1000` to `RunJson` per `contracts/objectscript.md`: parse the array once into `excl(p)` and `excl(p, Type)`; subtract excluded `^KG("degp")` from the divisor; skip excluded edges in the forward walk and the reverse count; limit 0 means all. Deploy. Make T028 pass. Done: the divisor with exclusions active is the count of kept `^KG("out")` edges (cached per node in `fwdDeg`), not `degp` minus excluded, because `degp` has no per-source-type split for `Type.p` entries. Malformed input throws `PPRExclude` 5001.
- [x] T033 [US2] In `iris_vector_graph/store_protocol.py` and `iris_vector_graph/stores/iris_sql_store.py`, add `exclude: list | None = None, limit: int | None = None` to `execute_ppr`, passing `""`/`1000` when `None`. Update `MockGraphStore` in `tests/unit/test_store_protocol.py`. Make T026 pass. Done: `kg_PERSONALIZED_PAGERANK` passes `limit = return_top_k or 0`; an exclusion skips `ArnoAccel.PPRJson`, which cannot exclude.
- [x] T034 [US2] In `iris_vector_graph/_engine/algorithms.py`, add `exclude_predicates=None` to `kg_PERSONALIZED_PAGERANK`, `_exclude_matches`, and the same filter in the Python fallback. Make T025 pass.
- [x] T035 [US2] In `iris_src/src/Graph/KG/FHIRGraph.cls`, add `GroupByPatient(pGraph, pScoresJson, pViaJson = "", pExplainTop = 5, pTopK = 50)` per `contracts/objectscript.md`. Deploy. Make T029 pass. Done: `GroupByPatient` does not call `Open`, so it also works on unregistered scratch graphs; `via` is always returned per contributor.
- [x] T036 [US2] In `iris_vector_graph/_engine/fhir_graph.py`, add `FHIR_PPR_EXCLUDE`, `exclude_predicates`, `group_by`, `via`, `explain_top` to `fhir_concept_ppr` per `contracts/python-api.md`. Export `FHIR_PPR_EXCLUDE` where the other FHIR constants are exported. Make T027 pass. Done: `explain_top=None` means 5. With `group_by`, PPR runs uncapped and `top_k` caps patients. The 231/232/233 E2E oracles that list every edge of a source were narrowed with `p <> 'in_patient_compartment'` (FR-007 adds edges; SC-009 is read as "unchanged apart from the new predicate"). `test_default_equals_410` accepts only 233/direct-PPR differences that vanish when only compartment edges are excluded.
- [x] T037 [US2] Run T030 live. Record the grouped top 10 for both seeds, the 233 comparison and any difference in research.md "Measured (US2)". A 233 ranking change is documented, not hidden (SC-002).

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/unit/test_store_protocol.py tests/integration/test_235_*.py tests/e2e/test_235_patient_ranking_e2e.py tests/e2e/test_235_compartment_e2e.py -s` passes, and the 231/233 E2E suites still pass.

## Phase 5: User Story 3, resolve more than `code` (P2)

**Goal**: `params="clinical"` resolves four token params filtered to indexed ones, reports `dropped_params`, and a resolved Medication adds its `medication` referrers.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_clinical_resolve_e2e.py -s`.

### Tests first (US3)

- [x] T038 [P] [US3] Write `tests/unit/test_235_clinical_preset.py` for `fhir_resolve_concepts` in `iris_vector_graph/_engine/fhir_graph.py` with `_fhir_call` mocked:
  - `params=None` sends `["code"]` and does not call `IndexedTokenParams`;
  - `params="clinical"` expands to the four params, calls `IndexedTokenParams`, and sends only the used ones;
  - `detail=False` returns `list[str]` including hop keys; `detail=True` returns the dict in `contracts/python-api.md`;
  - an unknown preset string raises `ValueError`;
  - when every clinical param is dropped, the result is empty and `dropped_params` lists all four (no IRIS resolve call).
- [x] T039 [P] [US3] Write `tests/integration/test_235_indexed_token_params.py`: `IndexedTokenParams(GRAPH, '["code","value-concept","nope"]')` returns `used` containing `code` and `value-concept` and `dropped` `["nope"]`, on IVGFHIR's live SearchColumn.
- [x] T040 [US3] Write `tests/e2e/test_235_clinical_resolve_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded` and `genomics_loaded`, with a module concept graph and crosswalk rows (dropped in `finally`):
  - `test_default_code_only` (scenario 1): default resolve of a gene concept misses the 233 variant Observations; equals the 4.1.0 key set for a `code`-only concept;
  - `test_clinical_resolves_values_and_components` (scenario 2): `params="clinical", detail=True` returns the 233 oracle Observations for the `hgnc/2621` seed, and `params_used`/`dropped_params` match `IndexedTokenParams`;
  - `test_medication_hop` (scenario 3): crosswalk an RxNorm code used by a Synthea `Medication` resource (one of the four `medicationReference` cases); detail shows every MedicationRequest/Statement/Administration/Dispense in the run whose `medication` references it, tagged `via: "medication"`, and `medication_hop.added` equals that count; the fixture-side count comes from the vendored file;
  - `test_hop_seeds_ppr`: `fhir_concept_ppr` from that concept gives each hop resource a non-zero score.
- [x] T041 [US3] Run T038–T040 and confirm they fail.

### Implementation (US3)

- [x] T042 [US3] In `iris_src/src/Graph/KG/FHIRGraph.cls`, add `IndexedTokenParams(pGraph, pParamsJson)` and extend `ResolveConcepts` with the Medication hop over `^KG("in", g, key, "medication", src)` for the four types, returning `via` and `medication_hop` alongside the unchanged `keys`. Deploy. Make T039 pass.
- [x] T043 [US3] In `iris_vector_graph/_engine/fhir_graph.py`, add `CLINICAL_PARAMS`, the `"clinical"` preset in `fhir_resolve_concepts` and `fhir_concept_ppr`, and `detail=`. Make T038 pass.
- [x] T044 [US3] Run T040 live. Record resolve counts per param, the hop counts and the resolve median in research.md "Measured (US3)".

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/integration/test_235_*.py tests/e2e/test_235_clinical_resolve_e2e.py -s` passes, and the 231/233 concept E2Es still pass.

## Phase 6: User Story 5, categories and claimed profiles are queryable (P2)

Ordered before US4 because the coverage report reads `category` and `meta_profile`.

**Goal**: `category` and `meta_profile` node properties as sorted JSON arrays; PascalCase labels for six allowlisted systems, minus R4 type names.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_category_profile_e2e.py -s`.

### Tests first (US5)

- [x] T045 [P] [US5] Write `tests/unit/test_235_category_labels.py` for pure helpers in `tests/e2e/interp_fixture.py` (the oracle side):
  - `pascal`: `vital-signs` → `VitalSigns`, `laboratory` → `Laboratory`, `encounter-diagnosis` → `EncounterDiagnosis`, `clinical-note` → `ClinicalNote`, `problem-list-item` → `ProblemListItem`, `social_history` → `SocialHistory`;
  - `expected_labels`: an allowlisted code gets a label; a local-system code does not; `procedure` under observation-category gets no `Procedure` label; the type label is always present;
  - `expected_categories`: `system|code` strings, deduplicated, sorted, a missing system as `|code`;
  - `expected_profiles`: sorted, `|version` kept.
- [x] T046 [P] [US5] Write `tests/unit/test_235_category_parity.py`: the `CATEGORY_LABEL_SYSTEMS` constant in `tests/e2e/interp_fixture.py` equals the list parsed out of `Graph.KG.FHIRGraph`'s `CATEGORYSYSTEMS` parameter in `iris_src/src/Graph/KG/FHIRGraph.cls`, and equals research R8's six URIs.
- [x] T047 [US5] Write `tests/e2e/test_235_category_profile_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded`:
  - `test_category_property` (scenario 1, FR-016): for every run node, `rdf_props` `category` parsed equals `expected_categories`; absent when expected is empty;
  - `test_labels` (FR-017): run label sets equal `expected_labels`; `MATCH (o:Observation:VitalSigns)` count equals the fixture's vital-signs Observations; the same for `Laboratory`;
  - `test_local_code_no_label` (scenario 2): PUT an Observation with only a local category code; `category` holds it and no extra label exists;
  - `test_meta_profile` (scenario 3, FR-018): `meta_profile` equals `expected_profiles`; no label derives from a profile;
  - `test_category_change_removes_label`: PUT a changed category, sync, the old label is gone from `rdf_labels` and `^KG("label")` and the type label stays;
  - `test_no_procedure_collision`: `MATCH (p:Procedure)` returns only Procedure resources.
- [x] T048 [US5] Run T045–T047 and confirm they fail.

### Implementation (US5)

- [x] T049 [US5] Implement `pascal`, `CATEGORY_LABEL_SYSTEMS`, `expected_categories`, `expected_profiles` and `expected_labels` in `tests/e2e/interp_fixture.py`. Make T045 pass.
- [x] T050 [US5] In `iris_src/src/Graph/KG/FHIRGraph.cls`: add `Parameter CATEGORYSYSTEMS`; extend `LoadColumns` to load the TOKEN `category` rows and the `Resource`-level `_profile` row into `..Props(type, prop)`; in `ResyncKey`, upsert or delete the `category` and `meta_profile` `rdf_props` rows, count changes into `..Interp("category")` / `..Interp("meta_profile")`, and diff the label set `{type} ∪ labels(category) − ..R4Types` against `rdf_labels` and `^KG("label")`. Deploy. Make T046 pass.
- [x] T051 [US5] Run T047 live, then rerun T017 (full-equals-incremental now covers props and labels). Record per-category label counts and the profile histogram in research.md "Measured (US5)".

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/e2e/test_235_category_profile_e2e.py tests/e2e/test_235_compartment_e2e.py -s` passes.

## Phase 7: User Story 4, measure how interpretable a repository is (P2)

**Goal**: live `fhir_coverage_report` and `fhir_concept_gaps` whose every figure equals an independent count from the vendored file.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_coverage_gaps_e2e.py -s`.

### Tests first (US4)

- [x] T052 [P] [US4] Write `tests/unit/test_235_token_field.py` for `_token_field(fhirpath)` in `iris_vector_graph/_engine/fhir_graph.py` (research R11): `Observation.code` → `("code",)`; `(Observation.value as CodeableConcept)` → `("valueCodeableConcept",)`; `Observation.component.code` → `("component", "code")`; `(Observation.component.value as CodeableConcept)` → `("component", "valueCodeableConcept")`; anything else raises `ValueError`.
- [x] T053 [P] [US4] Write `tests/unit/test_235_reports.py` for `fhir_coverage_report` and `fhir_concept_gaps` with `_fhir_call` mocked: coverage merges `link_report` from `fhir_link_report` unchanged; `fhir_concept_gaps(top=0)` raises `ValueError`; `params="clinical"` expands and filters as in US3; the shapes equal `contracts/python-api.md`.
- [x] T054 [P] [US4] Write `tests/unit/test_235_expected_reports.py` for `expected_coverage` and `expected_gaps` in `tests/e2e/interp_fixture.py` on a synthetic resource list: compartment share excludes non-compartment types; patient-less sample sorted and capped at 20; category `label`/`local`/`none` split; gaps sorted by count, then system, then code; text-only counts a `code` with `text` and no `coding`, and not an absent `code`.
- [x] T055 [US4] Write `tests/e2e/test_235_coverage_gaps_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded` in a scratch FHIR graph registration scoped so that only the run's resources count (register a second graph over the same repo filtered by prefix if the registry supports it; otherwise compare only the per-type figures restricted to run keys via a `keys_prefix` test hook, documented in research):
  - `test_coverage_figures` (scenario 1, SC-004): every per-type count, `with_compartment`, `patientless`, `meta_profile` histogram, category split, `linked_patients` (2) and `resolution.by_system` equal `expected_coverage`;
  - `test_link_report_embedded`: `link_report` equals `fhir_link_report(GRAPH)`;
  - `test_gaps` (scenario 2): `unmatched` top 20 and `unmatched_total` equal `expected_gaps`; PUT one Observation with a text-only `code`, sync, `text_only["code"]` rises by exactly 1;
  - `test_repeatable` (scenario 3): two calls with no sync in between are equal, and no table row count changes;
  - `test_under_2s` (SC-007 on the fixture): each report's median of 5 is under 2 s.
- [x] T056 [US4] Run T052–T055 and confirm they fail.

### Implementation (US4)

- [x] T057 [US4] Implement `expected_coverage` and `expected_gaps` in `tests/e2e/interp_fixture.py`. Make T054 pass.
- [x] T058 [US4] In `iris_src/src/Graph/KG/FHIRGraph.cls`, add `CoverageReport(pGraph)` and `ConceptGaps(pGraph, pParamsJson, pTop = 20)` per `contracts/objectscript.md`; the text-only path reads `Rsrc` JSON only for resources with no token row. Deploy.
- [x] T059 [US4] In `iris_vector_graph/_engine/fhir_graph.py`, add `_token_field`, `fhir_coverage_report` and `fhir_concept_gaps`. Make T052 and T053 pass.
- [x] T060 [US4] Run T055 live. Record the coverage figures and gaps top 5 in research.md "Measured (US4)".

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/e2e/test_235_coverage_gaps_e2e.py -s` passes.

## Phase 8: User Story 6, patient anchors come from the graph (P3)

**Goal**: `fhir_patient_anchors` answers from compartment resources with the clinical preset; `/api/cypher` uses it first and reports `anchor_source`.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_anchors_e2e.py -s`.

### Tests first (US6)

- [x] T061 [P] [US6] Write `tests/unit/test_235_cypher_anchors.py` for `_resolve_patient_anchors` in `iris_vector_graph/cypher_api.py` with the engine and `fhir_bridge` mocked:
  - graph holds the patient → `anchor_source="graph"`, bridge not called, `params["patient_anchors"]` is `list[str]`, response gains `anchors: [{id, graph}]`;
  - graph holds the patient with no anchors → still `"graph"`, bridge not called;
  - no graph holds the patient → bridge called exactly as today, `anchor_source="fhir_bridge"`, anchors with `graph: null`;
  - `fhir_graph` in the request is passed as `graph=`;
  - no `fhir_patient_id` → no `anchor_source` or `anchors` in the response (4.1.0 shape).
- [x] T062 [P] [US6] Write `tests/unit/test_235_patient_anchors.py` for `fhir_patient_anchors` with `_fhir_call` mocked: lists registered graphs when `graph=None`; merges and deduplicates per `(id, graph)`, sorted; an id with characters outside FHIR's `[A-Za-z0-9\-.]{1,64}` raises `ValueError` before IRIS.
- [x] T063 [US6] Write `tests/e2e/test_235_anchors_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded` and a module crosswalk (dropped in `finally`):
  - `test_graph_anchors` (scenario 1): a run patient's anchors equal the crosswalked concepts of the clinical tokens on its compartment resources, computed from the vendored file;
  - `test_two_graphs` (scenario 2): register a second FHIR graph over the same repo (scratch id, unregistered in `finally`), sync; anchors without `graph=` name both graphs; with `graph=` only one;
  - `test_absent_patient_uses_bridge` (scenario 3): through the FastAPI test client on `/api/cypher`, with `fhir_bridge` HTTP mocked, `anchor_source="fhir_bridge"` and the 4.1.0 anchors.
- [x] T064 [US6] Run T061–T063 and confirm they fail.

### Implementation (US6)

- [x] T065 [US6] In `iris_src/src/Graph/KG/FHIRGraph.cls`, add `PatientConcepts(pGraph, pPatientKey, pParamsJson)`. Deploy.
- [x] T066 [US6] In `iris_vector_graph/_engine/fhir_graph.py`, add `fhir_patient_anchors`. Make T062 pass.
- [x] T067 [US6] In `iris_vector_graph/cypher_api.py`, add `fhir_graph` to `CypherRequest`, make `_resolve_patient_anchors` graph-first per research R15, and add `anchor_source` and `anchors` to the response dict. Make T061 pass. Rerun `tests/unit/test_231_cypher_api_fhir_allowlist.py`.
- [x] T068 [US6] Run T063 live.

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/unit/test_231_cypher_api_fhir_allowlist.py tests/e2e/test_235_anchors_e2e.py -s` passes.

## Phase 9: User Story 7, upgrade from 4.1.0 without a re-sync (P3)

**Goal**: the `interp_version` marker drives one full re-derivation on the next sync; `fhir_reinterpret` does it on demand; the `interpretation` block reports it.
**Independent test**: `.venv/bin/pytest tests/e2e/test_235_reinterpret_e2e.py -s`.

**Schema change**: T073 adds one nullable column, `Graph_KG.fhir_graphs.interp_version INTEGER`, by idempotent `ALTER TABLE` on schema init, with no backfill (NULL = stale, so the first 4.2 sync re-derives once; erase resets it to NULL). Tom approved exactly this on 2026-09-26 (plan, Complexity Tracking). Anything beyond it needs a new approval.

### Tests first (US7)

- [x] T069 [P] [US7] Write `tests/unit/test_235_interp_marker.py`:
  - `_interp_stale(version, current)` in `iris_vector_graph/_engine/fhir_graph.py`: `None` and lower are stale, equal is not, higher raises `RuntimeError` (a graph written by a newer IVG);
  - `_ensure_fhir_graph_columns` in `iris_vector_graph/_engine/schema.py` issues `ALTER TABLE Graph_KG.fhir_graphs ADD COLUMN interp_version INTEGER` only when the column is absent (mocked cursor), and is idempotent;
  - `fhir_reinterpret` returns the ObjectScript dict and maps `busy`;
  - `fhir_graph_status` passes through `interpretation_version` and `interpretation_stale`.
- [x] T070 [P] [US7] Update the DDL parity test `test_fhir_sql_columns_match_schema` expectations so `interp_version` must be in the `fhir_graphs` DDL in `iris_vector_graph/schema.py` (Gate 4). Confirm it fails.
- [x] T071 [US7] Write `tests/e2e/test_235_reinterpret_e2e.py` with `pytestmark = [pytest.mark.e2e]`, using `synthea_loaded`:
  - `test_null_marker_rederives` (scenario 1, SC-008): record the run's edge, prop and label sets; delete the run's compartment edges and `category`/`meta_profile` rows through SQL and `^KG` (simulating 4.1.0 output), set `interp_version = NULL`; `fhir_graph_sync` returns `interpretation.full_rederivation` true and `interpretation_version` 1; the sets equal the recorded ones;
  - `test_current_marker_no_rederive` (scenario 2): a second sync reports `full_rederivation` false and `compartment_edges_added` 0;
  - `test_reinterpret` (scenario 3): `fhir_reinterpret(GRAPH)` sets the marker and returns `resources` equal to the live key count;
  - `test_stale_flag` (scenario 4): with the marker NULL, `fhir_coverage_report` has `stale` true and `fhir_graph_status` has `interpretation_stale` true;
  - `test_interrupted_leaves_marker` (edge case): force an error in the second `Rederive` batch through a test-only `^IVG.Test("FHIRGraph","failBatch")` hook, and the marker stays NULL; the next sync completes it;
  - `test_erase_nulls_marker`: `erase_graph` on a scratch registration leaves `interp_version` NULL;
  - `test_410_to_42` (SC-008, authoritative): `tcp-deploy` the 4.1.0 archive from T014, sync a fresh scratch prefix, redeploy this branch, sync once, compare with a fresh 4.2 sync of a second identical prefix (sets equal modulo prefix). Marked `slow`, run explicitly in the gate.
- [x] T072 [US7] Run T069–T071 and confirm they fail.

### Implementation (US7)

- [x] T073 [US7] Add `interp_version INTEGER` to the `fhir_graphs` DDL in `iris_vector_graph/schema.py` and `_ensure_fhir_graph_columns()` to `iris_vector_graph/_engine/schema.py`, called where `_ensure_registry_route_columns` is called. Make T070 pass.
- [x] T074 [US7] In `iris_src/src/Graph/KG/FHIRGraph.cls`: `Parameter INTERPVERSION = 1`; `Rederive()`; `Reinterpret(pGraph)`; `SyncOnce` runs `Rederive` first when stale; `SyncOnce` and `Rebuild` set the marker at the end and return the `interpretation` block from `..Interp`; `Status` adds `interpretation_version` and `interpretation_stale`; the test-only failure hook. In `iris_src/src/Graph/KG/Eraser.cls`, `EraseFHIRGraphRows` sets `interp_version = NULL`. Deploy.
- [x] T075 [US7] In `iris_vector_graph/_engine/fhir_graph.py`, add `_interp_stale`, `fhir_reinterpret`, and the `stale`/`interpretation_version` fields in `fhir_coverage_report`. Make T069 pass.
- [x] T076 [US7] Run T071 live, including `test_410_to_42`. Record the re-derivation time on the fixture in research.md "Measured (US7)".

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_*.py tests/e2e/test_235_reinterpret_e2e.py -s` and `.venv/bin/pytest -m slow tests/e2e/test_235_reinterpret_e2e.py::test_410_to_42 -s` pass.

## Phase 10: Polish & cross-cutting

- [x] T077 Write `tests/unit/test_235_docs.py` first: `docs/FHIR_GRAPH.md` has an `## Interpretation contract` section after the genomics section; it has a table naming, for each concept, whether it is an edge, token, property or label (`in_patient_compartment`, `category`, `meta_profile`, category labels, clinical tokens); it names every spec non-goal; it states `meta_profile` is "claimed by the writer, not validated"; it names `FHIR_PPR_EXCLUDE`'s three entries; its medians equal `baseline.json`; the 233 quickstart advice to denylist `Provenance.target` is replaced by the default exclusion note; `CHANGELOG.md` has a 4.2.0 "Upgrading" note naming `interp_version`, the first-sync re-derivation and `fhir_reinterpret`. Confirm it fails.
- [x] T078 Run `bench_235.py --label current --set fixture` and `--set scale`, then `bench_235.py compare` against both baselines in `baseline.json`. A breach blocks; fix the cause (not the budget) and record it in research.md "Measured (bench)". Store the current results in `baseline.json`.
- [x] T079 SC-009: rerun T002's suites and `scripts/fhir/capture_410_baseline.py --compare specs/235-fhir-graph-interpretation/baseline_410.json`. `fhir_link_report`, `edges` and `last_counts` must equal 4.1.0 exactly, and default PPR within 1e-12 (or a documented 233 difference from T037). Any new failure against `baseline.txt` blocks.
- [x] T080 Write the `## Interpretation contract` section in `docs/FHIR_GRAPH.md` from research.md "Measured" sections and T078's medians, and the 4.2.0 entry with "Upgrading" note in `CHANGELOG.md`. Update the genomics section's `Provenance.target` denylist advice. Run `markdownlint-cli2 --fix` and `prettier --write` on both. Make T077 pass.
- [x] T081 [P] Run `.venv/bin/pytest tests/unit -q` (baseline: 1 known khop failure) and `ruff check iris_vector_graph tests/unit/test_235_*.py tests/integration/test_235_*.py tests/e2e/test_235_*.py tests/e2e/interp_fixture.py tests/e2e/fhir_conftest.py scripts/fhir/`.
- [x] T082 [P] Walk through `specs/235-fhir-graph-interpretation/quickstart.md` against the live container and correct any command or output that differs. Lint and format it.
- [x] T083 Update `spec.md`, `research.md` and `data-model.md` where a measured value differs from plan time. Lint and format each.

## Phase 11: Lazy FHIR-graph tables (FR-027)

**Goal**: A namespace that never registers a FHIR repository has no FHIR-graph tables, and every FHIR entry point still answers.

> **Recorded after the fact.** The code for this phase was written first. These tasks were added to `tasks.md` afterwards. The tests were written alongside the code, not before it, and nobody saw them fail against the old code. No red run was done for T084 or T086. Only the green results in T091 were observed.

### Tests first (FR-027)

- [x] T084 [P] Write `tests/unit/test_235_lazy_fhir_tables.py`: the base schema omits the four tables and keeps `fhir_bridges` and `code_crosswalk`; `get_fhir_graph_schema_sql()` holds exactly the four; `fhir_graph_register` runs their DDL before `Register`, tolerates "already exists", raises other DDL errors, and creates nothing without `HS_FHIRServer.Repo`; `fhir_patient_anchors` returns no graphs on SQLCODE -30 and raises other errors. (No red run; see the note above.)
- [x] T085 [P] Point `tests/unit/test_231_fhir_graph_schema.py` at both scripts (base plus `get_fhir_graph_schema_sql()`).
- [x] T086 Write `tests/e2e/test_235_lazy_fhir_tables_e2e.py` in USER on 31972 (no FHIR repository): drop the four tables if empty, run `initialize_schema`, assert they are absent and the eager two present; anchors empty; status "not registered"; register fails with "no FHIR repository" and creates nothing; `erase_graph` and `verify_graph` succeed.

### Implementation (FR-027)

- [x] T087 In `iris_vector_graph/schema.py`, move the four tables' DDL from `get_base_schema_sql` into `GraphSchema.get_fhir_graph_schema_sql()`. Make T084's schema tests and T085 pass.
- [x] T088 In `iris_vector_graph/_engine/fhir_graph.py`, add `_ensure_fhir_graph_tables()` (repository probe, then idempotent DDL, then commit) and call it from `fhir_graph_register`; make `fhir_patient_anchors` treat SQLCODE -30 as no graphs. Make T084 pass.
- [x] T089 In `iris_src/src/Graph/KG/FHIRGraph.cls`, `Open` checks `TableExists("Graph_KG", "fhir_graphs")` and throws "not registered"; Register's missing-table message points at `fhir_graph_register()`. Deploy to USER (`tcp-deploy`) and IVGFHIR. Run T086 live.
- [x] T090 Add the FR-027 note to the 4.2.0 "Upgrading" section of `CHANGELOG.md` and the register comment in `docs/FHIR_GRAPH.md`. Lint and format both.
- [x] T091 Rerun the 231/232/233/235 E2E, `test_fhir_demo_e2e.py`, `tests/integration/test_235_*.py` and `test_snapshot_inventory.py` on 31972. Result: E2E 245 passed; integration 44 passed, 1 skipped, 1 known failure (`test_the_snapshot_plan_accounts_for_every_store_in_the_inventory`, fails on main). Run integration separately: an idle USER connection dies (EPIPE) during the 41-minute E2E pass.

**Phase gate**: `.venv/bin/pytest tests/unit/test_235_lazy_fhir_tables.py tests/unit/test_231_fhir_graph_schema.py tests/e2e/test_235_lazy_fhir_tables_e2e.py` pass.

## Dependencies

```text
Setup (T001–T004) ─▶ Foundational (T005–T014) ─▶ US1 (T015–T024) ─┬─▶ US2 (T025–T037)
                                                                   ├─▶ US3 (T038–T044) ─┬─▶ US6 (T061–T068)
                                                                   ├─▶ US5 (T045–T051) ─┤
                                                                   │                    └─▶ US4 (T052–T060, needs US3 + US5)
                                                                   └─▶ US7 (T069–T076, needs US5 for props in the block)
                                                   all stories ─▶ Polish (T077–T083) ─▶ Lazy tables (T084–T091)
```

- T001 blocks everything. T003 and T014 must run before any engine change (T020 onward), because they capture 4.1.0.
- US1 is a prerequisite for every other story: they all read compartment edges or `..Comp`.
- US2, US3 and US5 depend only on US1 and can run in parallel.
- US4 needs US3 (clinical preset, `IndexedTokenParams`) and US5 (props).
- US6 needs US3 (clinical preset in `PatientConcepts`).
- US7 needs US5, because its sets and `interpretation` block include props and labels. T073 needs Tom's schema approval.
- `FHIRGraph.cls` is touched by T020, T021, T022, T035, T042, T050, T058, T065 and T074. Those tasks are sequential even across parallel stories; deploy after each.

## Parallel examples

- **Foundational tests**: T005, T006 and T007 are separate files.
- **US1**: T015 and T016.
- **US2**: T025, T026, T027, T028 and T029 are separate files; then T032 (PageRank.cls), T033 (store), T034 (algorithms.py) touch different files and can proceed in parallel; T035 waits for any other `FHIRGraph.cls` edit.
- **US4**: T052, T053 and T054.
- **US6**: T061 and T062.
- **Across stories** after US1: one developer on US2 (PageRank, algorithms), another on US3 then US6, a third on US5 then US4. Coordinate every `FHIRGraph.cls` edit.
- **Polish**: T081 and T082.

## Implementation strategy

- **MVP**: Phases 1–3. Compartment edges equal to the server's own tables answer "which resources are in this patient's record".
- **Next**: US2 (ranked patients, the demo answer), then US3 and US5 in parallel, then US4 (reports need both).
- **Last**: US6 (API) and US7 (upgrade). US7 carries the one schema change and waits on Tom's approval.
- Docs follow measurements (T080 after T078). Every phase ends at its gate; no phase starts while a previous gate is red.
