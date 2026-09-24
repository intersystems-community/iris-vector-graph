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

reg = engine.fhir_graph_register()          # graph id fhir:<NAMESPACE>:<pkg>
g = reg["graph_id"]                          # e.g. fhir:IVGFHIR:X0001
engine.fhir_graph_rebuild(g)                 # full build, per-param counts
engine.fhir_graph_schedule(g, interval_s=60) # Task Manager task, one per graph

engine.code_crosswalk_add(
    "http://hl7.org/fhir/sid/icd-10-cm", "E11.9", "C0011860",
    target_graph="umls", relation="exact", source="umls",
)
scores = engine.fhir_concept_ppr(g, "umls", ["C0011860"], hops=1)
```

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

1. **Expand.** `fhir_expand_concepts(concept_graph, ids, hops=1, predicates=None)`
   collects the concepts reachable over the concept graph's out-edges.
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
- **Copied content.** None: no properties, no codes, no Arno-backed FHIR storage.

## Deprecated in 4.1.0, removed in 5.0

- `fhir_bridge.get_kg_anchors` and `fhir_bridge.unified_clinical_pipeline`. Use
  `fhir_resolve_concepts` and `fhir_concept_ppr` instead.
- `POST /fhir-event`. A registered graph reads the repository itself, so nothing has
  to call in. Until 5.0 the endpoint answers with a `Deprecation: true` header and a
  warning, and an optional `graph` field writes into a named graph.
