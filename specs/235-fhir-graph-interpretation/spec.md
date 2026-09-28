# Feature Specification: FHIR graph interpretation contract

**Feature Branch**: `235-fhir-graph-interpretation`
**Created**: 2026-09-26
**Status**: Draft
**Input**: Spec 235. Make a synced IRIS FHIR repository interpretable as a
patient-centred graph using base R4 semantics only. The headline of 4.2; 4.1.0
ships first.

## Context

Specs 231–233 sync an IRIS FHIR repository into a named graph. Reference search
params become edges, token params resolve to concepts through the crosswalk,
and `fhir_concept_ppr` ranks resources. Three gaps keep that graph from
answering the question customers ask: "which patients, and why?"

- **Nothing says which resources belong to which patient.** A patient is
  reached through whatever reference happens to point at it. `Observation`
  uses `subject`, `Encounter` uses `patient`, and `Provenance` reaches its
  patient through `target` (search param `patient`).
  No edge says "this resource is in this patient's record".
- **Raw FHIR can be underdetermined.** Categories, claimed profiles and
  non-`code` tokens are not in the graph. A caller cannot tell a lab result
  from a vital sign, or measure how much of the repository IVG can interpret.
- **The ISC FHIR server does not enforce profiles on write.** US Core
  guarantees little beyond `subject`, `code` and `category`, with extensible
  bindings. Interpretation cannot rest on a profile being present.

Base R4 already defines the patient relationship. The Patient
CompartmentDefinition lists, per resource type, which params place a resource
in a patient's compartment. The server exposes it
(`HS.FHIRServer.Schema.GetCompartmentParamsForType`) and materialises it
(`HSFHIR_X0001_S.<Type>Compartments`). This spec makes that contract explicit
in the graph, reports how much of a repository it covers, and ranks patients
by it. It interprets no profile.

The design was settled in a grilling session on 2026-09-26 (21 decisions,
recorded under Clarifications).

## Clarifications

### Session 2026-09-26

- Q: Release placement? → A: 235 is the 4.2 headline. 4.1.0 ships first, and
  its `FHIR_GRAPH.md` states "base R4 search params, no profile
  interpretation".
- Q: How is patient membership represented? → A: A new derived edge
  `in_patient_compartment` (resource → Patient), one per (resource, patient).
  Existing reference edges are unchanged.
- Q: Source of membership? → A: Derived from reference edges already synced,
  using the server's patient-compartment params, loaded once per sync. No extra
  resource reads.
- Q: Which params count? → A: All R4 patient-compartment params. The edge
  carries `via=<param list>`.
- Q: Patient resources and `Patient.link`? → A: The Patient type is skipped.
  `link` stays an ordinary reference edge with no identity resolution. The
  coverage report counts linked patients.
- Q: Effect on PPR? → A: Derived edges are on the PPR denylist by default, so
  4.1.0 rankings are unchanged.
- Q: Patient ranking? → A: Opt-in `fhir_concept_ppr(group_by="patient")`. A
  patient's score is the sum of its compartment resources' scores, with an
  optional `via` filter. Top contributing resources are the explanation.
- Q: Categories? → A: A `category` property on every node, as
  `['system|code', …]`. Labels only for the HL7 observation-category and
  condition-category systems and the US Core category system. No category hub
  nodes.
- Q: `meta.profile`? → A: A `meta_profile` property only, documented as
  "claimed by the writer, not validated". No labels.
- Q: Which token params resolve? → A: The default stays `params=["code"]`. A
  new preset `params="clinical"` expands to `code`, `value-concept`,
  `component-code`, `component-value-concept` and Medication `code`, filtered to
  params the endpoint indexes.
- Q: Medication resources? → A: A resolved Medication expands one hop to the
  MedicationRequest, MedicationStatement, MedicationAdministration and
  MedicationDispense resources that reference it through `medication`. They are
  tagged `via=medication` and seeded with the Medication's weight.
- Q: Unresolved codes? → A: `fhir_concept_gaps(params=…)`, computed live and
  not persisted. It returns the top unmatched `(system, code)` pairs with
  resource counts, plus a count of text-only resources.
- Q: Coverage report? → A: `fhir_coverage_report(graph)`, computed live. The
  existing `fhir_link_report` is embedded unchanged as a section.
- Q: Lifecycle of derived edges? → A: The source resource owns them. They are
  re-derived on every re-sync in the same pass as its reference edges and
  removed on delete. Erase, snapshot/restore and verify treat them as ordinary
  edges.
- Q: Which compartments? → A: Patient only. The derivation takes the
  compartment name as a parameter. The other four compartments are non-goals.
- Q: Per-profile interpretation maps? → A: Deferred to a later spec.
- Q: Fixtures and oracle? → A: A vendored Synthea R4 bundle (about 10
  patients) plus the 233 genomics fixture. The oracle is the server's
  `<Type>Compartments` tables.
- Q: Patient anchors in the Cypher API? → A: Graph first, external bridge
  second. The response reports `anchor_source`.
- Q: Performance? → A: Relative budgets, gated, on the fixture and a scaled
  Synthea run of about 100 patients.
- Q: Provenance hub? → A: `Provenance.target` and `Provenance.entity` are on the
  PPR denylist by default. Cypher is unaffected.
- Q: Upgrading graphs synced under 4.1.0? → A: A per-graph
  `interpretation_version` marker. A stale marker triggers one full
  re-derivation on the next sync. `fhir_reinterpret(graph)` does the same on
  demand.
- Q: How are category labels named? → A: PascalCase of the code, matching the
  resource-type labels: `laboratory` → `Laboratory`, `vital-signs` →
  `VitalSigns`, `problem-list-item` → `ProblemListItem`.
- Q: Which graph answers anchors when a patient is in several FHIR graphs? →
  A: All of them. Anchors are merged, each names its source graph, and an
  optional `graph=` narrows the answer to one graph.
- Q: How many contributors explain a patient's score? → A: `explain_top=5` by
  default, settable by the caller. Each patient also carries its total
  contributor count.
- Q: What does a sync report about interpretation? → A: `fhir_graph_sync` and
  `fhir_graph_rebuild` results gain an `interpretation` block. It holds the
  compartment edges added and removed, the nodes whose `category` or
  `meta_profile` was set, whether a full re-derivation ran, and the resulting
  `interpretation_version`.
- Q: Do existing edge counts include compartment edges? → A: No. Today
  `Counts()` keys every edge in the graph as `Type.param`, so without a rule
  the link report would gain `Observation.in_patient_compartment` entries and
  the `edges` totals in status, `last_counts` and the link report would rise.
  That contradicts "`fhir_link_report` embedded unchanged". Existing counts
  cover reference edges only. Compartment edges are counted separately, as
  `compartment_edges` in status and in the `interpretation` block.
- Q: How many rows do the list-shaped report fields return? → A: The gaps list
  defaults to `top=20`, settable by the caller. Patient-less resources are
  reported per type as a count plus a sample of at most 20 keys.

### Session 2026-09-27

- Q: Should FHIR support be a separate, opt-in install? → A: No. `FHIRGraph.cls`
  reaches `HS.*` only dynamically, so it compiles and sits idle on Community, and
  there is no extra Python dependency. A split would add "is FHIR installed"
  branches to erase, verify and snapshot. Instead the FHIR-graph tables are lazy.
- Q: Which tables are lazy, and who creates them? → A: `fhir_graphs`,
  `fhir_unresolved`, `fhir_definitions` and `fhir_canonical_refs` leave the base
  schema. The first `fhir_graph_register` in a namespace creates them, and only
  when the namespace has a FHIR repository (`HS_FHIRServer.Repo`). `fhir_bridges`
  and `code_crosswalk` stay eager: neither needs a FHIR repository. Existing
  tables are kept; nothing is dropped on upgrade.
- Q: Release placement, revisited (supersedes the 2026-09-26 answer)? → A: 4.1.0
  never shipped, so there is one release, 4.1.0, holding the 4.0.1 fixes, specs
  231–234 and this spec. Below, "4.1.0" means the code before this spec (`main`
  at `2d92da2`) and "4.2" means this spec's code. The CHANGELOG, code comments and
  `docs/FHIR_GRAPH.md` say "pre-235" or "4.1.0" instead.

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Every resource knows its patient (Priority: P1)

A developer syncs a FHIR repository and asks, in Cypher or through the API,
for every resource in one patient's record, whatever reference type it used to
get there.

**Why this priority**: Every other story (ranking, anchors, coverage) depends
on patient membership. Without it the graph cannot answer "which patient".

**Independent Test**: Sync the Synthea and genomics fixtures into IVGFHIR.
For every resource of a patient-compartment type, compare IVG's
`in_patient_compartment` edges against the server's `<Type>Compartments` rows.

**Acceptance Scenarios**:

1. **Given** an Observation with `subject=Patient/p1` and
   `performer=Patient/p1`, **When** the graph syncs, **Then** exactly one
   `in_patient_compartment` edge links it to `p1`, with `via` listing
   `performer` and `subject`.
2. **Given** an Encounter with `patient=Patient/p2`, **When** the graph syncs,
   **Then** it has one compartment edge to `p2` with `via=patient`.
3. **Given** a Patient with `link` to another Patient, **When** the graph
   syncs, **Then** no compartment edge is derived for either Patient, and the
   `link` reference edge is unchanged.
4. **Given** an Observation whose `subject` is a Group, **When** the graph
   syncs, **Then** no compartment edge is derived for it, and the coverage
   report lists it as patient-less.
5. **Given** a synced graph, **When** an Observation's subject changes from
   `p1` to `p3` and the graph re-syncs incrementally, **Then** its compartment
   edge points to `p3` only.
6. **Given** a synced graph, **When** a resource is deleted and the graph
   re-syncs, **Then** its compartment edges are gone.
7. **Given** the same source data, **When** the graph is built once by full
   sync and once by a sequence of incremental syncs, **Then** the compartment
   edge sets are identical.
8. **Given** a synced graph, **When** it is erased, snapshotted and restored,
   or verified, **Then** compartment edges behave exactly like other edges.

---

### User Story 2 - Rank patients, not resources (Priority: P1)

A clinical analyst starts from a concept (a disease, a gene, a drug) and gets
back ranked patients, each with the resources that put them there.

**Why this priority**: This is the demo answer to "which patients, and why?".
It is also the reason for the compartment edges.

**Independent Test**: Run `fhir_concept_ppr(group_by="patient")` from a
genomics gene concept and from a Synthea condition concept. Check that the
scores equal the sum of the member resources' scores, and that each patient's
explanation lists its top contributors.

**Acceptance Scenarios**:

1. **Given** a synced graph, **When** `fhir_concept_ppr` runs with default
   arguments, **Then** its ranking is identical to 4.1.0 on the same data.
2. **Given** a synced graph, **When** `fhir_concept_ppr(group_by="patient")`
   runs, **Then** each patient's score equals the sum of the PPR scores of its
   compartment resources, and its five highest-scoring contributors are
   returned with its total contributor count. With `explain_top=2`, at most two
   are returned.
3. **Given** `group_by="patient", via=["subject"]`, **When** it runs,
   **Then** only resources whose compartment edge has `subject` in `via`
   contribute.
4. **Given** a resource with no compartment edge, **When**
   `group_by="patient"` runs, **Then** it contributes to no patient, and the
   response counts it as unattributed.
5. **Given** a Provenance record whose `target` and `entity` fan out to many
   resources, **When** PPR runs with default arguments, **Then**
   `Provenance.target` and `Provenance.entity` are not walked. The 233 genomics
   PPR test either shows unchanged rankings or documents the change.

---

### User Story 3 - Resolve more than `code` (Priority: P2)

A developer resolves concepts over values, components and medications, not
only `code`, with one named preset.

**Why this priority**: Genomics and medication questions live outside `code`.
The preset turns 233's per-param calls into one supported call.

**Independent Test**: Resolve a gene concept and a drug concept with
`params="clinical"` on the fixtures. Check which resources match and their
`via` tags.

**Acceptance Scenarios**:

1. **Given** default arguments, **When** concepts resolve, **Then** only
   `code` is used, as in 4.1.0.
2. **Given** `params="clinical"`, **When** concepts resolve, **Then** `code`,
   `value-concept`, `component-code`, `component-value-concept` and Medication
   `code` are used, limited to params the endpoint indexes. The response names
   the params used and any that were dropped.
3. **Given** a drug concept that resolves to a Medication, **When** concepts
   resolve, **Then** every MedicationRequest, MedicationStatement,
   MedicationAdministration and MedicationDispense referencing it through
   `medication` is returned, tagged `via=medication`, with the Medication's
   weight.

---

### User Story 4 - Measure how interpretable a repository is (Priority: P2)

Before trusting any answer, a solution engineer runs one report on a customer
repository. It shows what share of resources IVG can place, classify and
resolve, and which codes it cannot.

**Why this priority**: Checking whether the repository is "underdetermined" is
the customer's first objection. The report gives a number instead of an
argument.

**Independent Test**: Run `fhir_coverage_report` and `fhir_concept_gaps` on
the fixtures. Compare every figure against counts computed independently from
the fixture files.

**Acceptance Scenarios**:

1. **Given** a synced graph, **When** `fhir_coverage_report(graph)` runs,
   **Then** it returns the following, per resource type:
   - the resource count;
   - the share with a compartment edge, with patient-less resources of
     compartment types listed;
   - the `meta_profile` histogram;
   - the category distribution, split between label and local;
   - the resolution summary for the `clinical` preset.

   It also returns the linked-patient count, the graph's
   `interpretation_version`, and the unchanged `fhir_link_report` output.

2. **Given** a synced graph, **When** `fhir_concept_gaps(params=…)` runs,
   **Then** it returns the top unmatched `(system, code)` pairs with resource
   counts, and a count of resources whose field holds text but no code.
3. **Given** either report, **When** it runs twice with no sync in between,
   **Then** it returns the same result. Nothing is persisted.

---

### User Story 5 - Categories and claimed profiles are queryable (Priority: P2)

A developer filters by category (lab, vital signs, problem-list item) in Cypher
and can see which profiles a writer claimed, without IVG vouching for them.

**Why this priority**: Categories are the one US Core guarantee worth carrying
into the graph. Claimed profiles feed the coverage report.

**Independent Test**: On the Synthea fixture, count Observations with the
label for the HL7 `laboratory` category. Compare with the fixture file's count.
Check that `meta_profile` values equal the files' `meta.profile` arrays.

**Acceptance Scenarios**:

1. **Given** an Observation categorised under the HL7 observation-category
   system, **When** the graph syncs, **Then** the node has a `category`
   property with `system|code` entries and a label for that category.
2. **Given** a resource with only a local category code, **When** the graph
   syncs, **Then** the code appears in `category`, and no label is added.
3. **Given** a resource with `meta.profile`, **When** the graph syncs,
   **Then** the node's `meta_profile` property holds the claimed URLs, and no
   label is added.

---

### User Story 6 - Patient anchors come from the graph (Priority: P3)

A Cypher API caller asks for a patient's anchors. When the patient is in a
synced graph, the answer comes from the graph, across all crosswalked systems,
instead of from an external ICD-10-only Condition search.

**Why this priority**: It removes a second, weaker answer to the same question
from the API. Callers without a synced graph are unaffected.

**Independent Test**: Call the anchor path for a fixture patient, then for a
patient in no synced graph, with the external bridge stubbed. Check
`anchor_source` and the anchors returned in each case.

**Acceptance Scenarios**:

1. **Given** a patient present in a synced FHIR graph, **When** anchors are
   requested, **Then** they come from the patient's compartment resources,
   resolved with the `clinical` preset, and `anchor_source="graph"`.
2. **Given** a patient present in two synced FHIR graphs, **When** anchors are
   requested without `graph=`, **Then** anchors from both graphs are returned,
   each naming its source graph. **When** `graph=` names one of them, **Then**
   only that graph's anchors are returned.
3. **Given** a patient in no synced graph, **When** anchors are requested,
   **Then** the external bridge answers exactly as in 4.1.0, and
   `anchor_source="fhir_bridge"`.

---

### User Story 7 - Upgrade from 4.1.0 without a re-sync (Priority: P3)

An operator upgrades IVG. The first sync after the upgrade interprets the
whole existing graph once, without erasing it.

**Why this priority**: Incremental sync touches only changed resources.
Without this story, most of an upgraded graph would stay uninterpreted.

**Independent Test**: Build a graph with the 4.1.0 sync, then run one
4.2 sync with no source changes. Check that the result equals a fresh 4.2 full
sync.

**Acceptance Scenarios**:

1. **Given** a graph with no `interpretation_version`, **When** the next sync
   runs, **Then** compartment edges, `category` and `meta_profile` are derived
   for every resource, the marker is set to the current version, and the sync
   result's `interpretation` block shows `full_rederivation: true`.
2. **Given** a graph at the current version, **When** a sync runs, **Then** no
   full re-derivation happens.
3. **Given** any graph, **When** `fhir_reinterpret(graph)` runs, **Then** it
   performs the full re-derivation and sets the marker.
4. **Given** a graph with a stale marker, **When** `fhir_coverage_report`
   runs, **Then** it flags the graph as stale.

### Edge Cases

- A reference to a Patient that is not in the graph (dangling): no compartment
  edge. It is counted under `unresolved` in `fhir_graph_status` (reason
  `missing` or `deleted`), as today.
- A resource in more than one patient's compartment (for example, an
  Observation whose `subject` and `performer` are different Patients): one
  edge per patient, each with its own `via`.
- Contained or `urn:uuid` references follow the existing 231/232 resolution
  rules. No new reference forms are introduced.
- A resource type with no patient-compartment params (for example, Medication
  or Organization): no compartment edge. Such types are excluded from the
  compartment share denominator.
- Provenance: the server's `ProvenanceCompartments` table is empty in IVGFHIR
  because all 18 Provenance rows are deleted and none ever targeted a Patient
  (research R4). The cause is the data, not the server. R4 Provenance has no
  `patient` element; the `patient` search param is
  `Provenance.target.where(resolve() is Patient)`, and IVG derives from that
  param's reference edge. Provenance is not excluded from the oracle.
- The endpoint does not index a param named in the `clinical` preset: it is
  dropped, and the response reports it.
- Text-only codes (`CodeableConcept.text` with no `coding`) count as
  text-only in `fhir_concept_gaps`. They are not the same as an absent field.
  Planning must verify that the token tables can tell the two apart. If they
  cannot, the count must come from resource JSON or be documented as a lower
  bound.
- A Medication referenced by thousands of requests: resolution has no result
  limit, so the one-hop expansion returns all of them. The detail result
  reports how many the hop added (FR-015).
- An interrupted re-derivation leaves the marker unset, so the next sync
  retries it.
- A namespace with no FHIR repository (for example USER on the enterprise image)
  never gets the FHIR-graph tables: `fhir_graph_register` answers "no FHIR
  repository" and creates nothing (FR-027).

## Requirements _(mandatory)_

### Functional Requirements

#### Patient compartment

- **FR-001**: Sync MUST derive one `in_patient_compartment` edge from each
  resource to each Patient it references through a param in its type's R4
  patient-compartment definition, as reported by the FHIR server's schema.
- **FR-002**: Each compartment edge MUST carry a `via` qualifier listing, in
  sorted order, every compartment param that links the pair.
- **FR-003**: Derivation MUST use only reference edges already produced by
  sync. It MUST NOT read additional resources.
- **FR-004**: Derivation MUST skip resources of type Patient. `Patient.link`
  MUST remain an ordinary reference edge.
- **FR-005**: Derivation MUST be parameterised by compartment name. Only
  `patient` is enabled.
- **FR-006**: Compartment edges MUST be owned by their source resource. Each
  re-sync of a resource MUST replace its compartment edges in the same pass as
  its reference edges. Deleting the resource MUST remove them.
- **FR-007**: Compartment edges MUST be stored as ordinary edges of the FHIR
  graph. Erase, snapshot/restore and verify MUST cover them without a new
  store.
- **FR-008**: A full sync and an equivalent sequence of incremental syncs MUST
  produce identical compartment edge sets.

#### Ranking

- **FR-009**: `in_patient_compartment`, `Provenance.target` and
  `Provenance.entity` edges MUST be excluded from the PPR walk by default.
  Cypher traversal MUST be unaffected.
- **FR-010**: `fhir_concept_ppr` with default arguments MUST return the same
  ranking as 4.1.0 on graphs without Provenance fan-out.
- **FR-011**: `fhir_concept_ppr(group_by="patient")` MUST return patients
  ranked by the sum of their compartment resources' PPR scores. Each patient
  MUST come with its top `explain_top` contributing resources (default 5,
  caller-settable) and their scores, plus its total contributor count. It MUST
  report the count and score mass of unattributed resources.
- **FR-012**: `group_by="patient"` MUST accept an optional `via` list that
  restricts contributions to compartment edges whose `via` intersects it.

#### Resolution

- **FR-013**: Concept resolution MUST keep `params=["code"]` as the default.
- **FR-014**: Concept resolution MUST accept `params="clinical"`, expanding to
  `code`, `value-concept`, `component-code`, `component-value-concept` and
  Medication `code`. Params the endpoint does not index MUST be dropped, and
  the response MUST report the params used and the params dropped.
- **FR-015**: When a resolved resource is a Medication, resolution MUST add
  the MedicationRequest, MedicationStatement, MedicationAdministration and
  MedicationDispense resources that reference it through `medication`. They
  MUST be tagged `via=medication` and weighted as the Medication. The
  expansion is one hop. Resolution has no result limit, so nothing is
  truncated; the response reports how many resources the hop added.

#### Node properties

- **FR-016**: Sync MUST set a `category` property on each node whose resource
  has `category`, as a list of `system|code` strings.
- **FR-017**: Sync MUST add a category label only for codes in the HL7
  observation-category, HL7 condition-category and US Core category systems.
  The label is the code in PascalCase (`vital-signs` → `VitalSigns`), so
  `MATCH (o:Observation:VitalSigns)` works without backticks. No category
  nodes are created.
- **FR-018**: Sync MUST set a `meta_profile` property holding the resource's
  `meta.profile` URLs. It MUST NOT add labels from profiles, and it MUST NOT
  validate them.

#### Reports

- **FR-019**: `fhir_coverage_report(graph)` MUST compute, live and without
  persisting anything, per resource type:
  - the count;
  - the share with a compartment edge (compartment types only);
  - the count of patient-less resources of compartment types, plus a sample
    of at most 20 of their keys;
  - the `meta_profile` histogram;
  - the category distribution (label vs local);
  - the per-system resolution summary for the `clinical` preset.

  Graph-wide, it MUST also report the linked-patient count, the graph's
  `interpretation_version` with a staleness flag, and the unchanged
  `fhir_link_report` output as a section.

- **FR-020**: `fhir_concept_gaps(params=…)` MUST compute, live and without
  persisting anything, the top unmatched `(system, code)` pairs with resource
  counts (`top=20` by default, caller-settable), and a count of resources
  that have the field with text but no coding.

#### Anchors

- **FR-021**: The Cypher API patient-anchor path MUST answer from the graph
  when the patient is in a synced FHIR graph: compartment resources resolved
  with the `clinical` preset. Otherwise it MUST use the existing external
  bridge unchanged. The response MUST include
  `anchor_source` = `"graph"` or `"fhir_bridge"`. When the patient is in more
  than one FHIR graph, anchors from all of them MUST be merged, and each anchor
  MUST name its source graph. An optional `graph=` MUST restrict the answer to
  that graph.

#### Upgrade

- **FR-022**: Each FHIR graph MUST record an `interpretation_version`. When it
  is absent or older than the current version, the next sync MUST re-derive
  compartment edges, `category` and `meta_profile` for every resource, then set
  the marker. An interrupted re-derivation MUST leave the marker unset.
- **FR-023**: `fhir_reinterpret(graph)` MUST perform the same full
  re-derivation on demand.

#### Sync reporting and existing counts

- **FR-025**: The results of `fhir_graph_sync`, `fhir_graph_rebuild` and
  `fhir_reinterpret` MUST include an `interpretation` block holding:
  - `compartment_edges_added` and `compartment_edges_removed`;
  - `category_set` and `meta_profile_set` node counts;
  - `full_rederivation` (boolean);
  - the resulting `interpretation_version`.
- **FR-026**: Every existing count MUST exclude compartment edges, so it
  reports the same figures as 4.1.0 for the same data. That covers `totals` in
  `fhir_link_report` (built from canonical refs, so unaffected), and `edges`
  and `last_counts` in
  `fhir_graph_status`. `fhir_graph_status` MUST report compartment edges
  separately as `compartment_edges`.

#### Schema

- **FR-027**: `initialize_schema` MUST NOT create `fhir_graphs`,
  `fhir_unresolved`, `fhir_definitions` or `fhir_canonical_refs`. Their DDL MUST
  come from `GraphSchema.get_fhir_graph_schema_sql()`, which
  `fhir_graph_register` MUST run (tolerating "already exists") before `Register`
  when the namespace has `HS_FHIRServer.Repo`, and MUST skip otherwise. Without
  the tables, `fhir_patient_anchors` MUST return no graphs, `fhir_graph_status`
  MUST say the graph is not registered, and `erase_graph` and `verify_graph` MUST
  succeed. `initialize_schema` MUST still add `interp_version` to an existing
  `fhir_graphs`.

#### Docs

- **FR-024**: `docs/FHIR_GRAPH.md` MUST gain an interpretation contract
  section. It states what becomes an edge, a token, a property or a label; the
  non-goals; and the measured medians. `CHANGELOG.md` MUST carry an
  "Upgrading" note for the marker-driven re-derivation.

### Key Entities

- **Compartment edge**: `in_patient_compartment` from a clinical resource node
  to a Patient node in the same FHIR graph. Qualifier `via` holds the sorted
  list of compartment params. Owned by the source resource.
- **Compartment definition**: per resource type, the params that place a
  resource in the Patient compartment. Read from the FHIR server's schema once
  per sync; not stored by IVG.
- **Category property**: `category` on a node, a list of `system|code`.
  PascalCase labels (`Laboratory`, `VitalSigns`) only for the three recognised
  systems.
- **Claimed profile property**: `meta_profile` on a node, a list of profile
  URLs. Informational only.
- **Interpretation version**: a per-graph marker naming the interpretation
  rules the graph was last fully derived with.
- **Coverage report / concept gaps**: computed views over the graph and the
  FHIR token tables. Never stored.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: On the Synthea and genomics fixtures, IVG's compartment
  membership equals the FHIR server's own compartment tables for 100% of
  resources of compartment types. Only documented exclusions differ.
- **SC-002**: Default `fhir_concept_ppr` rankings on the 231/232 fixtures are
  identical to 4.1.0. The 233 genomics ranking is unchanged, or the difference
  is documented and explained by the Provenance denylist.
- **SC-003**: For every patient returned by `group_by="patient"`, the score
  equals the sum of its contributors' scores, within floating-point tolerance.
- **SC-004**: Every figure in the coverage report for the Synthea fixture
  matches a count computed independently from the vendored files.
- **SC-005**: Sync wall time grows by no more than 15% over 4.1.0, on the
  fixture and on a scaled Synthea run of about 100 patients.
- **SC-006**: `group_by="patient"` adds no more than 20% to the median PPR
  time at the scaled size.
- **SC-007**: `fhir_coverage_report` and `fhir_concept_gaps` each complete in
  under 2 seconds at the scaled size.
- **SC-008**: A graph synced by 4.1.0 and then synced once by 4.2 is
  identical to a fresh 4.2 full sync of the same data.
- **SC-009**: The existing 231, 232 and 233 E2E suites and
  `test_fhir_demo_e2e.py` pass unchanged. On the same data, `fhir_link_report`
  and the `edges` count in `fhir_graph_status` equal their 4.1.0 values
  exactly.

## Testing _(mandatory)_

Test-first. Unit tests come before each helper: `via` merging, preset
expansion and filtering, the group-by aggregation, the marker comparison, and
the category-system classification. Each user story has an E2E phase gate
against the live `ivg-iris-enterprise` container (port 31972), namespace
IVGFHIR, with per-run id prefixing as in 232 and 233. The compartment oracle
test compares against `HSFHIR_X0001_S.<Type>Compartments`. Performance gates
record medians to the docs.

## Assumptions

- The FHIR server's schema API (`GetCompartmentParamsForType`) returns base R4
  patient-compartment params. The vendored R4 package's
  `CompartmentDefinition-patient.json` (145 entries, 66 with params) is the
  reference.
- A Synthea R4 set of about 10 patients, generated with the Apache-2.0
  Synthea generator at a pinned version and seed, can be vendored with a
  pinned sha256 and a `SOURCE.md`, like `tests/e2e/fixtures/fhir/cpg`. The
  `synthea-sample-data` repository has no licence and is not used (research
  R16). A 100-patient scaled set is generated the same way. It stays
  out of the default test run if it is large.
- Synthea resources carry US Core `meta.profile` claims and HL7 category codes.
  That makes them adequate for the property and label stories.
- The HL7 observation-category, condition-category and US Core category system
  URIs are fixed constants. Planning records the exact URIs.
- Concept resolution has no result limit, so the Medication expansion is
  complete. No limit is introduced.

## Dependencies

- Specs 231 (FHIR named graph), 232 (canonical links) and 233 (genomics
  fixture and component-param resolution).
- The FHIR server schema class `HS.FHIRServer.Schema` in the target namespace.

## Non-goals

- Enforcing or validating profiles; interpreting profiles beyond reporting
  claims.
- Per-profile interpretation maps (US Core, IPA, ViewDefinition-style). These
  are a later spec.
- Encounter, Practitioner, RelatedPerson and Device compartments.
- Identity resolution over `Patient.link`.
- Category hub nodes.
- Persisted or snapshotted reports.
- Deprecating or changing `fhir_bridge`.
- Release, tag or publish.
