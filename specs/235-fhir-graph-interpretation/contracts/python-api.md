<!-- markdownlint-disable MD013 -->

# Contract: Python API

These methods are on `IRISGraphEngine` (the `_engine/fhir_graph.py` and `_engine/algorithms.py` mixins) and on `/api/cypher`. Every new argument is keyword-only and defaults to 4.1.0 behaviour. The errors are those of 231: `ValueError` for bad arguments, and `RuntimeError` carrying the ObjectScript error text.

## `fhir_concept_ppr`

```python
fhir_concept_ppr(
    graph, concept_graph, ids, *,
    hops=1, predicates=None, params=None, relations=None,
    top_k=50, damping_factor=0.85, max_iterations=20,
    direction="in",                        # 233
    exclude_predicates=FHIR_PPR_EXCLUDE,   # new
    group_by=None,                         # new: None | "patient"
    via=None,                              # new: list[str] | None, only with group_by
    explain_top=5,                         # new: int >= 0, only with group_by
) -> dict
```

- `FHIR_PPR_EXCLUDE = ("in_patient_compartment", "Provenance.target", "Provenance.entity")`. Each entry is a bare predicate, which applies to any source type, or `Type.predicate`. Passing `[]` walks every edge.
- With `group_by=None`, the return is `{key: score}`, top `top_k`, as in 4.1.0.
- With `group_by="patient"`:

  ```json
  {
    "patients": [
      {
        "patient": "Patient/p1",
        "score": 0.0123,
        "contributors_total": 41,
        "contributors": [{ "key": "Observation/o9", "score": 0.004, "via": ["subject"] }]
      }
    ],
    "unattributed": { "count": 12, "score": 0.31 }
  }
  ```

  - `patients` is sorted by `score` descending, then by `patient` ascending, and cut to `top_k` (`None` means all).
  - `contributors` is sorted the same way and cut to `explain_top`.
  - `score` equals the sum of the scores of all `contributors_total` contributors (SC-003).

- Errors:
  - `via` or `explain_top` without `group_by` raises `ValueError`.
  - A `group_by` other than `"patient"` raises `ValueError`.
  - A `via` entry that is not a search-param name raises `ValueError`.
- `params="clinical"` is accepted, as in `fhir_resolve_concepts`.

## `kg_PERSONALIZED_PAGERANK`

```python
kg_PERSONALIZED_PAGERANK(seed_entities, damping_factor=0.85, max_iterations=100, tolerance=1e-6,
                         return_top_k=None, bidirectional=False, reverse_edge_weight=1.0, *,
                         graph=None, exclude_predicates=None)
```

- `exclude_predicates=None` keeps the 4.1.0 walk exactly.
- A list uses the matching rule of `fhir_concept_ppr`. It applies to the ObjectScript path (`RunJson`) and to the Python fallback.
- `GraphStore.execute_ppr` gains `exclude: list | None = None` and `limit: int | None = None`. `MockGraphStore` gains the same arguments (Gate 4).

## `fhir_resolve_concepts`

```python
fhir_resolve_concepts(graph, concept_graph, ids, *, params=None, relations=None, detail=False)
```

- `params` is `None` (meaning `["code"]`), a list of param names, or `"clinical"`. `"clinical"` expands to `["code", "value-concept", "component-code", "component-value-concept"]`, then drops the params not indexed for any type in the graph.
- With `detail=False`, the return is `list[str]` of keys, as in 4.1.0. It includes the Medication-hop keys.
- With `detail=True`:

  ```json
  {
    "keys": ["Observation/o1", "MedicationRequest/m7"],
    "params_used": ["code", "value-concept"],
    "dropped_params": ["component-value-concept"],
    "via": { "MedicationRequest/m7": "medication" },
    "medication_hop": { "medications": 2, "added": 5 }
  }
  ```

## `fhir_coverage_report`

```python
fhir_coverage_report(graph, *, id_prefix=None) -> dict
```

This report is computed live and never stored. `id_prefix`, when set, keeps only the resources whose id starts with it (the E2E tests share one repository). Shape:

```json
{
  "graph": "fhir:IVGFHIR:X0001",
  "interpretation_version": 1,
  "interpretation_current": 1,
  "stale": false,
  "types": {
    "Observation": {
      "count": 914,
      "compartment_type": true,
      "with_compartment": 914,
      "compartment_share": 1.0,
      "patientless": 0,
      "patientless_sample": [],
      "meta_profile": {
        "http://hl7.org/fhir/us/core/StructureDefinition/us-core-vital-signs": 528
      },
      "category": { "label": 914, "local": 0, "none": 0 }
    },
    "Medication": { "count": 4, "compartment_type": false }
  },
  "resolution": {
    "params_used": ["code", "value-concept", "component-code", "component-value-concept"],
    "dropped_params": [],
    "by_system": {
      "http://loinc.org": {
        "codes": 120,
        "resources": 900,
        "resolved_codes": 30,
        "resolved_resources": 400
      }
    }
  },
  "linked_patients": 2,
  "link_report": {}
}
```

- `compartment_share` and `patientless` appear only when `compartment_type` is true. `patientless_sample` holds at most 20 keys, sorted.
- `category.label` counts nodes with at least one allowlisted code. `local` counts nodes with only other codes. `none` counts nodes with none.
- `resolution.by_system` covers the `clinical` params. A code counts as resolved when `code_crosswalk` has it for any concept graph.
- `linked_patients` counts Patient nodes that are the source or target of a `link` edge.
- `link_report` is `fhir_link_report(graph)` unchanged (FR-019).

## `fhir_concept_gaps`

```python
fhir_concept_gaps(graph, *, params=None, top=20, id_prefix=None) -> dict
```

```json
{
  "graph": "fhir:IVGFHIR:X0001",
  "params_used": ["code"],
  "dropped_params": [],
  "unmatched": [{ "system": "http://snomed.info/sct", "code": "444814009", "resources": 17 }],
  "unmatched_total": { "codes": 42, "resources": 311 },
  "text_only": { "code": 3 },
  "skipped_paths": []
}
```

- `unmatched` is sorted by `resources` descending, then by `system` and `code`, and cut to `top`.
- `text_only` is keyed by param: resources with the field and its `text`, but no token row.
- The element paths come from `TokenPaths`. `skipped_paths` lists the FHIRPath parts that cannot be turned into element paths (a `where(...)` or `resolve()`, say), so the text-only count skips them.
- `id_prefix` is as in `fhir_coverage_report`.

## `fhir_reinterpret`

```python
fhir_reinterpret(graph) -> dict   # {"status": "ok", "graph": ..., "resources": n, "elapsed_ms": ms, "interpretation": {...}}
```

It takes the graph lock. When the lock is held, it returns `{"status": "busy"}`, as Rebuild does.

## `fhir_graph_sync`, `fhir_graph_rebuild` and `fhir_graph_status`

- The sync and rebuild results gain `interpretation` (see [data-model.md](../data-model.md#interpretation-block-in-results-not-stored)).
- Status gains `compartment_edges`, `interpretation_version` (None when NULL), `interpretation_current` and `interpretation_stale`. `edges` excludes compartment edges.
- `fhir_graph_status` and `fhir_coverage_report` raise `RuntimeError` when the marker is newer than this IVG's `INTERPVERSION`.

## `fhir_patient_anchors`

```python
fhir_patient_anchors(patient_id, *, graph=None) -> dict
# {"graphs": ["fhir:IVGFHIR:X0001"], "anchors": [{"id": "MONDO:0005148", "graph": "fhir:IVGFHIR:X0001"}]}
```

- `graphs` lists the registered FHIR graphs that hold `Patient/<patient_id>`. An empty list means none.
- `anchors` is deduplicated per `(id, graph)` and sorted.

## `/api/cypher`

- The request gains `fhir_graph: str | None`, which restricts the lookup to that graph.
- When `fhir_patient_id` is set:
  - The new `_patient_anchors(req)` calls `fhir_patient_anchors` first. It wraps `_resolve_patient_anchors`, which stays unchanged as the bridge path. An exception from the graph lookup counts as "no graph holds the patient".
  - If `graphs` is not empty, `params["patient_anchors"]` is the list of anchor ids, and the response gains `"anchor_source": "graph"` and `"anchors": [...]`.
  - Otherwise the existing bridge runs unchanged, and the response gains `"anchor_source": "fhir_bridge"` and `"anchors": [{"id": a, "graph": null}, ...]`.
- `params["patient_anchors"]` stays `list[str]`.
