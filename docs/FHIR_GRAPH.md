<!-- markdownlint-disable MD013 -->

# A FHIR repository as a named graph

Since 4.1.0, IVG can project an ISC FHIR repository (`HS.FHIRServer`, JsonAdvSQL
storage) as one named graph. From there a query can walk from a concept in a UMLS or HPO
graph to the patients, encounters and observations that carry its codes, and rank them
with PPR. Clinical content never leaves the repository.

IVG runs inside the FHIR namespace, beside the repository's tables and the concept
graphs. `Graph.KG.FHIRGraph` does the work there, and the Python methods and the
`ivg fhir` CLI are thin callers of it.

## Quick start

```python
engine = IRISGraphEngine(conn_to_fhir_namespace)
engine.initialize_schema()

reg = engine.fhir_graph_register()          # creates the fhir_* tables; id fhir:<NAMESPACE>:<pkg>
g = reg["graph_id"]                          # e.g. fhir:IVGFHIR:X0001
engine.fhir_graph_rebuild(g)                 # full build, per-param counts
engine.fhir_graph_schedule(g, interval_s=60) # Task Manager task, one per graph

engine.code_crosswalk_add(
    "http://hl7.org/fhir/sid/icd-10-cm", "E11.9", "C0011860",
    target_graph="umls", relation="exact", source="umls",
)
scores = engine.fhir_concept_ppr(g, "umls", ["C0011860"], hops=1)
```

Then read the graph patient by patient, and check what it can and cannot resolve:

```python
# Resolve through every clinical token param, not only `code`, and rank patients
# by the summed scores of their compartment resources
ranked = engine.fhir_concept_ppr(
    g, "umls", ["C0011860"], params="clinical", group_by="patient", top_k=10,
)
for p in ranked["patients"]:
    print(p["patient"], p["score"], p["contributors"][:3])

engine.fhir_coverage_report(g)                      # compartment share, categories, profiles, staleness
engine.fhir_concept_gaps(g, params="clinical")      # commonest codes nothing maps to
engine.fhir_graph_status(g)["compartment_edges"]
engine.fhir_reinterpret(g)                          # re-derive after upgrading the classes
```

Sections below: [the model](#the-model), [sync](#sync),
[concepts to ranked resources](#from-a-concept-to-ranked-resources) and the
[interpretation contract](#interpretation-contract).

The same operations from a shell. They connect straight to IRIS through `IRIS_HOST`,
`IRIS_PORT`, `IRIS_NAMESPACE`, `IRIS_USERNAME` and `IRIS_PASSWORD`:

```bash
ivg fhir register [--endpoint /csp/healthshare/ns/fhir/r4] [--deny Provenance.target] [--interval 60]
                  [--link PlanDefinition.library ... | --no-links]
ivg fhir rebuild fhir:IVGFHIR:X0001
ivg fhir sync fhir:IVGFHIR:X0001 --once     # exit 2: another sync holds the graph
ivg fhir status fhir:IVGFHIR:X0001
ivg fhir schedule fhir:IVGFHIR:X0001 [--interval 120 | --remove]
ivg fhir links fhir:IVGFHIR:X0001 [--source PlanDefinition/pd1] [--format table|json]
```

## The model

| IVG                          | FHIR                                                          |
| ---------------------------- | ------------------------------------------------------------- |
| graph ID                     | `fhir:<$NAMESPACE>:<repo package>`, e.g. `fhir:IVGFHIR:X0001` |
| node ID                      | the resource `Key`, verbatim: `Patient/p1`                    |
| node label                   | the resource type                                             |
| node property `id`           | the key again, so Cypher's `n.id` works                       |
| edge predicate               | the search param code: `subject`, `performer`                 |
| edge qualifier `searchParam` | the SearchParameter URL (`SearchColumn.Definition`)           |
| edge qualifier `jsonLink`    | the json link entry, for an edge read from the resource body  |

Only topology is copied: one node per live resource and one edge per indexed reference
or canonical, plus the links the json links read from the resource body.
Codes and other properties stay in the repository. `fhir_resolve_concepts` reads the
token tables live.

When one reference is indexed under two params, the result is two edges. An
Observation's `subject` pointing at a Patient also yields `patient`. Duplicates within
one param collapse. A reference from a resource to itself becomes a self-loop.

### What is an edge

Every search column with `Type = 'reference'` and `DataType` `REFERENCE` or
`CANONICAL` becomes an edge, less the graph's denylist. A canonical edge points at the
local resource that declares the url, by the rule under
[Canonical edges](#canonical-edges). The two `URI` columns are excluded.

A reference only becomes an edge if FHIR indexed it or a json link reads it, so the
following never do:

- contained references (`#id`)
- `urn:uuid` references that were not resolved inside a transaction
- references inside extensions that no json link names

A reference that points at a live resource is an edge even when the target's type is
not in the param's `Target` list. Validating that is the FHIR server's job.

### What is not an edge: `fhir_unresolved`

A reference that cannot become an edge goes into `Graph_KG.fhir_unresolved` as a row
`(graph_id, source, param, target, reason)`:

| reason              | meaning                                                                       |
| ------------------- | ----------------------------------------------------------------------------- |
| `external`          | `_Reference` contains `://` and none of the repository's endpoint paths + `/` |
| `missing`           | the target key has never existed                                              |
| `deleted`           | the target key existed and is deleted                                         |
| `no-definition`     | a canonical url that no live resource in the repository declares              |
| `version-not-found` | a `url\|version` whose url is declared, but not at that version               |
| `ambiguous`         | a versionless url that more than one live resource declares                   |

When a `missing` or `deleted` target appears, the next sync turns its rows into edges.
An absolute reference that contains the repository's own endpoint path is local.

### Denylist

The registered denylist ships empty. Pass `Type.param` entries to leave high-fan-out
params out of the graph. The first candidates are `Provenance.target` and
`AuditEvent.entity`, which point at nearly everything and add hubs that dominate PPR.
The rebuild report lists every param with its edge count, including denylisted params
at 0, so start from the numbers. A changed denylist takes effect at the next rebuild.

## Canonical edges

A canonical is a `url` or `url|version` naming a definitional resource (a Library,
PlanDefinition, Measure, Questionnaire and so on). `Graph_KG.fhir_definitions` records
the `url` and `version` each live resource declared at its last sync, and
`Graph_KG.fhir_canonical_refs` records every canonical a source carries, resolved or
not, with its origin: `index` or the json link that read it.

The resolution rule:

- `url|version` is an edge when exactly one live resource declares that url at that
  version. A declared url with no such version is `version-not-found`.
- A versionless `url` is an edge when exactly one live resource declares the url, at
  any version. More than one is `ambiguous`.
- A url that no live resource declares is `no-definition`. A reference to an HL7 core
  definition that is not loaded, such as `http://hl7.org/fhir/Library/x`, lands here.

An edge never re-points. A new version of a definition makes versionless references
to it `ambiguous`: their edges are deleted and unresolved rows written. Deleting the
extra version brings the edges back in the same sync. When a definition's url
changes, every source that names the old url or the new one resyncs in the same
transaction.

The rule is stricter than the FHIR server's own search. The server matches the url
exactly, matches the version by prefix, and lets a versionless search return every
version. A search returns a list, but an edge claims one target, so the graph wants
an exact version, and refuses to pick when there are several.

The repository's index keeps the first 220 characters of a canonical url and of its
version, and values read from the body are cut the same way so both sides compare
equal. Two urls that differ only after character 220 are the same url to the graph.

### `library` and `depends-on`

R4 indexes `PlanDefinition.library` (and `library` on ActivityDefinition, Measure and
the other types whose `depends-on` SearchParameter expression includes `.library`)
merged into `depends-on`. Where that column's `Definition` contains `.library`, the
urls from the resource's `library` element are taken out of the `depends-on` edges and
emitted as `library` edges instead, provided the default json link for that type is
configured. A url that is in both `relatedArtifact` and `library` gets both edges. The
split is read from `SearchColumn.Definition`, not from a list of types.

## JSON links

Some links have no search param: `library` on the types above, and every link inside
an extension. The graph's `json_links` list names what to read from the resource
body. It is a JSON array of strings, each in one of two forms:

| entry             | reads                                                                                                                                          | predicate                                                   |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `Type.element`    | the top-level `element` of `Type`: a canonical string, or each string in an array                                                              | `element`, e.g. `library`                                   |
| `extension:<url>` | every `extension` or `modifierExtension` with that `url`, at any depth, on every type: its `valueCanonical`, or its `valueReference.reference` | the url's last segment after `/` or `:`, e.g. `cqf-library` |

A `valueCanonical` resolves by the canonical rule. A `valueReference` resolves like an
indexed reference and can be `external`, `missing` or `deleted`. The body walk stops
at depth 64 and 10,000 matches per resource; either limit fails the batch with an
error naming the key.

Anything else is rejected at registration, as is an entry listed twice or two
entries that give one predicate where one of them is an extension. The denylist
applies to json-link predicates too, as `Type.predicate`, for example
`PlanDefinition.cqf-library`.

With no `json_links`, `fhir_graph_register` stores the default list: `<Type>.library`
for each type with a split column, in sorted order, then
`extension:http://hl7.org/fhir/StructureDefinition/cqf-library`. An explicit list
replaces the default, and `[]` (`--no-links`) turns json links off. A changed list
makes registration rebuild the graph and reply `"rebuilt": true`. If another sync
holds the graph, the list is stored, `last_error` reads
`json_links changed; rebuild pending`, and the next rebuild applies it.

`fhir_graph_status` reports `json_links`: how many refs each entry read, 0 included, so
an entry that matches nothing is visible.

## The link report

`fhir_link_report(graph, source=None)` (`ivg fhir links`) lists every canonical and
json-link reference, one row per `(source, param, url, version, origin)`:

```json
{
  "source": "PlanDefinition/pd1",
  "param": "library",
  "url": "http://ex.org/Library/L",
  "version": "1.0.0",
  "origin": "PlanDefinition.library",
  "kind": "canonical",
  "status": "Library/l1"
}
```

`status` is the target key when the reference resolved, and the unresolved reason
otherwise. `source="Type/id"` limits the report to one resource. `totals.by_param`
counts each param's outcomes (`resolved` or a reason; only outcomes that occur), and
`totals.by_link` counts rows per json link, every configured entry included. The
report is read-only: it recomputes each status from the current definitions.

On the HL7 CPG 2.0.0 CHF examples (62 resources, vendored under
`tests/e2e/fixtures/fhir/cpg`), the report has 36 rows: 14 `definition`, 8
`measure`, 6 `instantiates-canonical` and 3 `library` resolve, and 5 `depends-on`
refs name HL7 definitions that are not loaded (`no-definition`).

## Sync

The repository's `Rsrc` table holds the current version of every resource, and
`RsrcVer` holds the superseded ones. A first create writes only `Rsrc`. Every later
write, a delete included, archives the prior version into `RsrcVer` and updates `Rsrc`
in place. So there are two watermarks:

- `Rsrc.ID > wm_rsrc` catches creates.
- `RsrcVer.ID > wm_ver` catches updates, deletes and re-creates.

`fhir_graph_sync` (`SyncOnce`) collects the changed keys in batches of 200. Each batch
resyncs its keys and advances its watermark inside one transaction. A failure rolls both
back and the next sync repeats the batch. Resyncing a key reads only the repository's
current state, so it is idempotent, and any sequence of syncs converges on the graph a
rebuild would build. The E2E suite compares the two node-for-node and edge-for-edge.

For a **live key**, resync:

1. ensures the node and its label exist;
2. recomputes the key's out-edges and its unresolved rows;
3. turns unresolved rows that target the key into edges.

For a **deleted or absent key**, resync:

1. records each incoming edge as `deleted`;
2. deletes the key's edges, unresolved rows, labels, properties and node;
3. deletes its vectors in every embedding route of the graph.

`fhir_graph_rebuild` reconciles the whole graph and resets both watermarks to the maxima
read before the scan. It diffs rather than erases, so vectors on surviving nodes are
kept.

One sync or rebuild runs per graph at a time. Each holds `^IVG.FHIRGraph(<graph>)`,
and a second caller gets `{"status": "busy"}` without touching anything.

`fhir_graph_schedule` creates or updates one `Graph.KG.FHIRGraphSyncTask` per graph in
the namespace. Task Manager counts minutes, so the interval rounds up:
`max(1, ceil(interval_s / 60))`. `fhir_graph_status` reports the counts, the pending
changes, the last sync and rebuild, the last error and the task ID.

Measured on `ivg-iris-enterprise`: a sync of 500 changed resources takes well under the
10 s budget.

## From a concept to ranked resources

Each operator runs on one graph. A question that crosses graphs is a pipeline:

1. **Expand.** `fhir_expand_concepts(concept_graph, ids, hops=1, predicates=None,
direction="in")` collects the concepts reachable over the concept graph's
   in-edges, or out-edges with `direction="out"`, or either with `"both"`. In-edges
   are the default because `rdfs:subClassOf`, `skos:broader` and OBO `is_a` point from
   child to parent, so walking them backwards reaches the subclasses. A graph that links
   parent to child, with `narrower` say, needs `direction="out"`.
2. **Resolve.** `fhir_resolve_concepts(graph, concept_graph, ids)` returns the keys of
   live resources whose token param (`params=`, default `["code"]`) matches a
   `(system, code)` that `Graph_KG.code_crosswalk` maps to one of the concepts,
   optionally only along some `relations=`.
3. **Rank.** `kg_PERSONALIZED_PAGERANK(seeds, graph=g)` walks only graph `g`.

`fhir_concept_ppr` chains the three steps and returns scores keyed by FHIR key. It
calls bidirectional PPR, because clinical references point from the event to the
patient.

On the E2E fixture (1,000 Patients and 1,000 coded Conditions), resolving 10 concepts
took 6.6 ms median. The whole pipeline, with 20 PPR iterations, took 67 ms. The budgets
are 250 ms and 1,000 ms.

### `code_crosswalk`

`Graph_KG.code_crosswalk(code_system_uri, code, target_graph, target_node_id, relation,
source, source_version, confidence)`, where `relation` is one of `exact`, `broader`,
`narrower` or `related`. `code_crosswalk_add` upserts a row. `target_graph = ''` is the
default graph.

### Moving off `fhir_bridges`

`migrate_fhir_bridges_to_crosswalk()` copies every `Graph_KG.fhir_bridges` row into
`code_crosswalk` and can be re-run. Each row gets:

- the code system mapped to its URI (`ICD10CM` becomes
  `http://hl7.org/fhir/sid/icd-10-cm`);
- `relation = 'related'`, `source = 'fhir_bridges'`, `source_version = bridge_type`;
- the default graph as target.

Until 5.0, `fhir_bridge_add` writes both tables.

## Genomics

Measured on the HL7 Genomics Reporting IG 3.0.0 examples (spec 233,
`tests/e2e/fixtures/fhir/genomics/`), loaded through `DispatchRequest` into `IVGFHIR`
on `ivg-iris-enterprise`. No engine code is genomics-specific.

A genomic fact lands in one of three places:

- **Edge.** A reference the sync indexes: `DiagnosticReport.result`,
  `Observation.derived-from`, `has-member`, `subject`, `specimen`, and so on.
- **Token.** A gene, variant, HGVS expression or ClinVar id in
  `Observation.component.valueCodeableConcept` or `valueCodeableConcept`. It stays in
  the repository and becomes no edge.
- **Concept graph.** SO and MONDO classes, HGNC genes and variants, in a separate named
  graph (`concepts:ivg233` in the tests). `code_crosswalk` maps each token to a concept.

```text
concept graph   MONDO_0019052 ◀─subClassOf─ MONDO_… ◀─gene_associated_with_condition─ hgnc/2621
                                                                                          ┆
                                                                  crosswalk (token → concept)
                                                                                          ┆
FHIR graph      Patient ◀─subject─ variant Observation [component: HGNC:2621] ◀─derived-from─ implication
                                            ▲
                                            └─result─ DiagnosticReport
```

Arrows run event → patient: an Observation points at its subject, a report at its
results, an implication at the variant it derives from. That is why `fhir_concept_ppr`
walks the FHIR graph bidirectionally.

### Edges

377 resources gave 1,363 edges, 2 contained references and no `fhir_unresolved` rows.

| param                  | edges |
| ---------------------- | ----- |
| `based-on`             | 14    |
| `collector`            | 4     |
| `derived-from`         | 113   |
| `focus`                | 3     |
| `general-practitioner` | 3     |
| `has-member`           | 41    |
| `patient`              | 314   |
| `performer`            | 279   |
| `requester`            | 4     |
| `result`               | 203   |
| `results-interpreter`  | 2     |
| `specimen`             | 69    |
| `subject`              | 314   |

62 references make no edge, because no search param indexes their element path:
`extension.valueReference` (33), `extension.extension.valueReference` (16), Task
`reasonReference` (9), `asserter` (2), `parent` (1) and `request` (1). Task
`reasonReference` has no R4 search param, so the IG's recommendation Tasks do not link
to the Observations they cite.

### Concepts

The concept graph is a slice of SO and MONDO (pinned in `SOURCE.md`) plus the HGNC
genes and variants the fixture names: 456 nodes, 650 edges and 399 labels.

| predicate                                | edges |
| ---------------------------------------- | ----- |
| `rdfs:subClassOf`                        | 523   |
| `biolink:gene_associated_with_condition` | 72    |
| `biolink:is_sequence_variant_of`         | 55    |

`gene_associated_with_condition` comes from MONDO's `RO:0004003` restrictions (has
material basis in germline mutation in), which Biolink does not map. The crosswalk has
137 rows: 136 clean, 1 normalized (an SO code written `SO_…`), and 3 unmapped codings
with no code.

Two things are needed for a gene to reach a patient:

- **`params`.** The genes sit in components, so the default `params=["code"]` finds
  none of the variant Observations. Pass
  `params=["component-value-concept", "value-concept"]`.
- **`direction`.** `fhir_expand_concepts` and `fhir_concept_ppr` follow in-edges by
  default. Here that goes from a disease to its subclasses and to the genes associated
  with it. `direction="out"` goes from a class to its superclasses; `"both"` follows
  either.

For `MONDO_0019052` (inborn errors of metabolism) with `direction="in"` and 6 hops,
20 concepts resolve to 7 Observations of 2 Patients, and both Patients outrank every
other Patient. Resolve took 4 ms median and the whole pipeline 25 ms, against 6.6 ms
and 67 ms on the 1,000-Patient fixture above.

`materialize_inference(graph=...)` reads only that graph and writes each inferred edge
into `^KG`, so the expansion sees it. `retract_inference` takes the edges back out.

### Model results and provenance

A model writes its result back as FHIR: a Device for the model version, an Observation
for the prediction, and a Provenance with `target` the prediction, `agent.who` the
Device and `entity.what` the input Observations. After sync the Provenance has one edge
per reference (`target`, `agent`, `entity`). The prediction's 2-hop neighbourhood
reaches the Device and the inputs, and two model versions stay separate. Deleting the
Provenance removes its edges and leaves the prediction and Device.

Every Provenance points its `target` at a result, so a result with many provenance
records becomes a hub. `fhir_concept_ppr` leaves `Provenance.target` and
`Provenance.entity` out of the walk by default (`FHIR_PPR_EXCLUDE`, see
[Interpretation contract](#interpretation-contract)). The edges stay in the graph, so
Cypher and the 2-hop neighbourhood above still see them, and no denylist entry is needed.

### What this is not

The fixture puts the concept graph and the patient graph in one namespace for the test
only. Co-residence is not a security design: the namespace stays the security boundary
(see below). Out of scope: patient or phenotype embeddings for FHIR nodes, a literature
or publication graph, VCF or raw variant storage, `ValidatedBy` Experiment or
Publication links, and any new operator or cross-graph query.

## Interpretation contract

Since 4.1.0 the sync reads a resource with base R4 semantics, so the graph can be walked
patient by patient. It uses the R4 search parameters and the patient compartment
definition the FHIR server ships. It does not read profiles: a `meta.profile` is
claimed by the writer, not validated.

| Concept                  | Kind     | Where                                                                                                                                                                                                                                                                   |
| ------------------------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `in_patient_compartment` | edge     | resource → Patient, one per (resource, patient). It is derived from the reference edges whose param is one of the type's R4 patient-compartment params. The qualifier `via` lists those params, sorted. Patient resources get none; `Patient.link` stays a `link` edge. |
| reference edges          | edge     | one per indexed reference, predicate = search param code                                                                                                                                                                                                                |
| clinical tokens          | token    | `code`, `value-concept`, `component-code`, `component-value-concept`, and Medication `code`. They stay in the repository's search tables. `params="clinical"` resolves through them, cut to the params the endpoint indexes.                                            |
| `category`               | property | a JSON list of `system\|code`, from the `category` search param                                                                                                                                                                                                         |
| `meta_profile`           | property | a JSON list of the `meta.profile` URLs, from `_profile`. Claimed by the writer, not validated. No labels.                                                                                                                                                               |
| category labels          | label    | the PascalCase code (`Laboratory`, `VitalSigns`, `EncounterDiagnosis`) for the HL7 observation-category and condition-category systems and the US Core category systems. Other systems stay property-only. A code equal to an R4 type name is skipped.                  |

A compartment edge belongs to its source resource. The sync re-derives it in the same
transaction as the resource's reference edges and deletes it with the resource.
`erase_graph`, snapshot restore and `verify_graph` treat it as an ordinary edge.
`fhir_graph_status` counts it apart as `compartment_edges`, so `edges` and
`last_counts` count reference edges only.

### Ranking and anchors

- `FHIR_PPR_EXCLUDE` holds `in_patient_compartment`, `Provenance.target` and
  `Provenance.entity`. It is the default `exclude_predicates` of `fhir_concept_ppr`,
  so compartment edges and Provenance hubs carry no mass by default.
  `exclude_predicates=()` walks everything. `kg_PERSONALIZED_PAGERANK` excludes nothing
  unless asked.
- `fhir_concept_ppr(group_by="patient")` sums each patient's compartment resources'
  scores. `via=[...]` keeps only the edges with one of those params. Each patient
  carries its top contributors, and `unattributed` holds the mass on resources outside
  any compartment.
- `/api/cypher` with `fhir_patient_id` takes `$patient_anchors` from a synced graph
  that holds the patient (`anchor_source: "graph"`), and from the external bridge
  otherwise (`"fhir_bridge"`).

### Reports

`fhir_coverage_report(graph)` and `fhir_concept_gaps(graph, params=...)` are computed
live and never stored. The coverage report gives per type the compartment share,
patient-less resources, the `meta_profile` histogram and the category split, then the
code resolution per system for the `clinical` params, the linked-patient count,
`interpretation_version` and `stale`, and `fhir_link_report` unchanged. The gaps report
lists the commonest unmatched `(system, code)` pairs and, per param, the resources whose
field carries only `text`. Those write no token row.

### Upgrading a graph built by older rules

`Graph_KG.fhir_graphs.interp_version` records the rules a graph was last interpreted
with. NULL (a graph synced by a pre-release build before spec 235, or an erased one) or an older number makes the next sync
re-derive every live resource once before its incremental pass. The result's
`interpretation` block reports `full_rederivation: true`. An interrupted re-derivation
leaves the marker as it was, so the next sync starts again. `fhir_reinterpret(graph)`
does the same on demand. Status and the coverage report flag a stale graph.

### Not in scope

- Enforcing or validating profiles, and per-profile interpretation maps (US Core, IPA,
  ViewDefinition-style).
- Encounter, Practitioner, RelatedPerson and Device compartments.
- Identity resolution over `Patient.link`.
- Category hub nodes.
- Persisted or snapshotted reports.
- Deprecating or changing `fhir_bridge`.

### Measured medians

`ivg-iris-enterprise` (`irishealth:2026.3.0AI.113.0`), `scripts/fhir/bench_235.py`.
The fixture is the vendored 10-patient Synthea file; scale is about 100 Synthea
patients. Baseline is `main` at 2d92da2, before spec 235. Budgets: sync at most +15%, grouped PPR at most
+20% of the baseline PPR, each report under 2 s. Milliseconds, `–` where the baseline has no
such call.

| Set     | Measure        | baseline | 4.1.0 |
| ------- | -------------- | -------- | ----- |
| fixture | `sync_ms`      | 35885    | 37348 |
| fixture | `ppr_ms`       | 481      | 574   |
| fixture | `ppr_group_ms` | –        | 617   |
| fixture | `coverage_ms`  | –        | 187   |
| fixture | `gaps_ms`      | –        | 377   |
| scale   | `sync_ms`      | 47057    | 52401 |
| scale   | `ppr_ms`       | 8132     | 8654  |
| scale   | `ppr_group_ms` | –        | 9024  |
| scale   | `coverage_ms`  | –        | 831   |
| scale   | `gaps_ms`      | –        | 1450  |

Both columns ran back to back on one container. Earlier test loads had left 165,483
deleted resource versions in the repository, and every `Rebuild` walks them, so sync is
slower on both sides than on a fresher repository (7.4 s fixture and 27 s scale before
those loads). The grouped PPR is one `GroupPPR` call: the walk and the grouping both run
in IRIS, so the scores never become a string.

## Security model

The namespace is the security boundary. A graph ID is not one. Any caller with SQL
access to the namespace can read every graph in it, the FHIR graph included, and the
graph holds resource keys and references, which identify patients. Deploy IVG in the
FHIR namespace only for users who may already read that repository's tables.

No other PHI leaves the repository. Codes and properties are read live, under the
caller's own SQL privileges.

## Limits

- **Storage.** Only R4 JsonAdvSQL is supported. Registration refuses any other storage
  strategy, and a namespace with more than one repository needs `endpoint=`.
- **`^NKG`.** The integer-indexed adjacency has no graph dimension. The Arno paths
  stay default-graph only, and a named-graph PPR never routes to Arno's `PPRJson`.
- **History.** FHIR history is not graph history. The graph holds the current state.
- **Canonicals.** Urls and versions compare on their first 220 characters. The body is
  read only through json links, not FHIRPath.
- **Copied content.** Only `id`, `category` and `meta_profile` properties (see the
  interpretation contract). No codes, no other properties, no Arno-backed FHIR storage.

## Deprecated in 4.1.0, removed in 5.0

- `fhir_bridge.get_kg_anchors` and `fhir_bridge.unified_clinical_pipeline`. Use
  `fhir_resolve_concepts` and `fhir_concept_ppr` instead.
- `POST /fhir-event`. A registered graph reads the repository itself, so nothing has
  to call in. Until 5.0 the endpoint answers with a `Deprecation: true` header and a
  warning, and an optional `graph` field writes into a named graph.
