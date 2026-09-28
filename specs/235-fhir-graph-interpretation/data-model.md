<!-- markdownlint-disable MD013 -->

# Data model: FHIR graph interpretation contract

All storage is in existing `Graph_KG` tables and `^KG`, apart from one new column. "Graph" means a registered FHIR graph such as `fhir:IVGFHIR:X0001`.

## Compartment edge

This is one `Graph_KG.rdf_edges` row per `(resource, patient)`.

| Column       | Value                                                                                |
| ------------ | ------------------------------------------------------------------------------------ |
| `graph_id`   | the FHIR graph                                                                       |
| `s`          | source resource key, `Type/id`. The type is never `Patient`.                         |
| `p`          | `in_patient_compartment`                                                             |
| `o_id`       | `Patient/id`, a live node in the same graph                                          |
| `qualifiers` | `{"via": ["patient", "subject"]}`: a sorted, deduplicated list of compartment params |
| `ekey`       | as for reference edges (the default single-edge key)                                 |

It also gets adjacency through `EdgeScan.WriteAdjacency`: `^KG("out", g, s, "in_patient_compartment", o)`, `^KG("in", g, o, "in_patient_compartment", s)`, and the `deg` and `degp` counts.

### Rules

- The edge exists if and only if at least one reference edge `(s, p, o_id)` with `p ∈ Comp(type(s))` exists in the same graph (FR-001).
- `via` is exactly the set of those `p` values (FR-002).
- The edge is owned by `s`. `ResyncKey(s)` inserts, updates or deletes it, and `DropNode(s)` removes it (FR-006).
- A denylisted param, or an unresolved or external target, gives no reference edge, so it gives no compartment edge.
- The edge is ordinary storage. Erase, snapshot and verify need no change (FR-007).

### State transitions for one `(s, patient)`

```text
absent --(sync adds a compartment reference)--> present(via = {p})
present(via) --(another param added/removed)--> present(via')        # UPDATE qualifiers
present --(last compartment reference removed, or s deleted, or patient deleted)--> absent
```

## Compartment definition (in memory, not stored)

| Field                 | Source                                                                                                                              |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `..Comp(type, param)` | `HS.FHIRServer.Schema.LoadSchema(RepoInstance.metadataSetKey).GetCompartmentParamsForType(type, .p)`, entries `p("patient", param)` |
| compartment name      | a `LoadCompartment` argument, default `"patient"` (FR-005)                                                                          |
| `..R4Types(type)`     | the loaded schema's resource type list, used by the label collision rule                                                            |

It is loaded once per `Open`: one sync, rebuild, re-derivation or report call.

## Node properties

These are `Graph_KG.rdf_props` rows, primary key `(graph_id, s, key)`.

| `key`          | `val`                                                                   | Source                                                                          | Present when       |
| -------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------ |
| `category`     | JSON array, sorted, of `"system\|code"`; a missing system is `"\|code"` | `HSFHIR_X0001_S_<Type>.category`, `DISTINCT value_System, value_Value` by `Key` | at least one value |
| `meta_profile` | JSON array, sorted, of profile URLs (`\|version` kept as written)       | `HSFHIR_X0001_S_<Type>._profile.value` by `Key`                                 | at least one value |

When the list becomes empty, the row is deleted. The values are the writer's claims and are never validated (FR-018).

## Category labels

These are `Graph_KG.rdf_labels` rows and `^KG("label")` entries, next to the resource-type label.

| Rule      | Detail                                                                                                                                                                                  |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Systems   | `CATEGORY_LABEL_SYSTEMS`, six URIs (research R8)                                                                                                                                        |
| Label     | the code in PascalCase, split on `-` and `_`: `vital-signs` → `VitalSigns`, `laboratory` → `Laboratory`, `encounter-diagnosis` → `EncounterDiagnosis`, `clinical-note` → `ClinicalNote` |
| Collision | skipped when the label equals an R4 resource type name, for example `procedure` → `Procedure`. The code stays in `category`.                                                            |
| Diff      | the wanted label set is `{type} ∪ labels(category)`. Stale category labels are removed on resync. The type label is never removed.                                                      |

## `Graph_KG.fhir_graphs.interp_version`

| Property | Value                                                                                                |
| -------- | ---------------------------------------------------------------------------------------------------- |
| Type     | `INTEGER`, nullable                                                                                  |
| Added by | the DDL in `schema.py`, plus `_ensure_fhir_graph_columns()` (`ALTER TABLE … ADD COLUMN`, idempotent) |
| NULL     | absent: the graph was synced by 4.1.0, or erased                                                     |
| Current  | `Graph.KG.FHIRGraph` `Parameter INTERPVERSION = 1`                                                   |
| Set      | after the last batch of `Rebuild`, `Rederive` (from `SyncOnce`) or `Reinterpret` commits             |
| Reset    | `Eraser.EraseFHIRGraphRows` sets it to NULL                                                          |
| Stale    | `interp_version IS NULL OR interp_version < INTERPVERSION`                                           |

### Transitions

```text
NULL/old --SyncOnce--> Rederive(all live keys) --commit last batch--> INTERPVERSION, then incremental pass
NULL/old --SyncOnce, interrupted mid-Rederive--> unchanged (next sync starts over)
any      --Rebuild / Reinterpret--> INTERPVERSION
any      --erase--> NULL
```

## Lazy FHIR-graph tables (FR-027)

| Table                 | Created by                   | Condition                    |
| --------------------- | ---------------------------- | ---------------------------- |
| `fhir_graphs`         | first `fhir_graph_register`  | `HS_FHIRServer.Repo` exists  |
| `fhir_unresolved`     | first `fhir_graph_register`  | `HS_FHIRServer.Repo` exists  |
| `fhir_definitions`    | first `fhir_graph_register`  | `HS_FHIRServer.Repo` exists  |
| `fhir_canonical_refs` | first `fhir_graph_register`  | `HS_FHIRServer.Repo` exists  |
| `fhir_bridges`        | `initialize_schema` (eager)  | always                       |
| `code_crosswalk`      | `initialize_schema` (eager)  | always                       |

DDL: `GraphSchema.get_fhir_graph_schema_sql()`. Readers tolerate absence:
`FHIRGraph.Open` reports "not registered", `fhir_patient_anchors` treats SQLCODE
-30 as no graphs, and the Eraser already skips -30/-29.

## Interpretation block (in results, not stored)

It is added to the results of `fhir_graph_sync`, `fhir_graph_rebuild` and `fhir_reinterpret` (FR-025).

```json
{
  "interpretation": {
    "compartment_edges_added": 0,
    "compartment_edges_removed": 0,
    "category_set": 0,
    "meta_profile_set": 0,
    "full_rederivation": false,
    "interpretation_version": 1
  }
}
```

`category_set` and `meta_profile_set` count the nodes whose property row was written or changed in this call.

## Status additions

`fhir_graph_status` changes as follows:

- `edges` now excludes `in_patient_compartment` (FR-026);
- `compartment_edges` (number) is new;
- `interpretation_version` (number or null) is new;
- `interpretation_stale` (bool) is new.

`last_counts` is written by `Counts()`, which excludes the new edge.

## Computed views (never stored)

- **Coverage report.** Its per-type rows and graph-wide fields are defined in [contracts/python-api.md](contracts/python-api.md#fhir_coverage_report).
- **Concept gaps.** Defined in [contracts/python-api.md](contracts/python-api.md#fhir_concept_gaps).
- **Grouped PPR.** `{"patients": [...], "unattributed": {...}}`, defined in [contracts/python-api.md](contracts/python-api.md#fhir_concept_ppr).
