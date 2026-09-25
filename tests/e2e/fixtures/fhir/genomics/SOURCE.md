# Genomics Reporting IG examples: source

`genomics-reporting-3.0.0.json` is a `collection` Bundle of every example in the
HL7 FHIR Genomics Reporting IG, merged into one resource set.

- **Package**: `hl7.fhir.uv.genomics-reporting` version `3.0.0`, from
  <https://packages.fhir.org/hl7.fhir.uv.genomics-reporting/3.0.0>
- **Package sha256**:
  `7a42cdfae8d47f0d39a2ad5c46ec9028ede4848655600e7deaff440b44d4d221`
- **License**: CC0-1.0 (the package's `package.json`)
- **Fetched**: 2026-09-24
- **Regenerate**:
  `python -m tests.e2e.genomics_fixture vendor <package.tgz>`

## Selection

Every `package/example/*.json` file except Parameters and Bundle, plus every
entry of the 12 example Bundles.

- Where a Bundle entry has the same `Type/id` as a standalone example, the
  standalone is kept.
- Where two Bundles share a `Type/id`, the first in sorted bundle-id order is
  kept.

## Changes

- `urn:uuid:` and `http://example.org/fhir/Type/id` references are rewritten to
  `Type/id` through each Bundle's own `fullUrl` map. The map is per Bundle,
  because 15 `urn:uuid` values are reused across Bundles for different
  resources.
- Ids longer than 54 characters become `id[:41]-sha256(id)[:12]`, with every
  reference to them rewritten, so the 10-character test prefix keeps them within
  FHIR's 64.
- `text` (the narrative) is removed from every resource and contained resource.
  `CodeableConcept.text` is kept.
- An entry with no id would get the last segment of its `fullUrl`. None at
  3.0.0.

Every other element is unchanged. The 2 `#…` references to contained resources
are left as they are.

## Counts

```text
standalone: 174
bundle_entries: 223
dup_standalone: 11
dup_bundle: 9
differing: 0
shortened: 6
contained_refs: 2
assigned: 0
kept: 377
```

`differing` counts duplicates whose content differs from the kept copy once
narrative is stripped and references rewritten. 8 duplicates differ only in
narrative.

## Excluded

None.

## Unindexed references

62 references sit at element paths no R4 search param indexes, so they make no
edge: `extension.valueReference` 33, `extension.extension.valueReference` 16,
Task `reasonReference` 9, `asserter` 2, `parent` 1, `request` 1.
`genomics_fixture.unindexed_refs` lists them, and the topology E2E prints them.

## Ontology

`ontology.ttl` is the SO + MONDO slice the fixture's codes resolve through, as
sorted N-Triples (research R5). Regenerate it with:

```bash
python -m tests.e2e.genomics_fixture vendor-ontology --so so.owl --mondo mondo.owl
```

The command checks both sha256 values before it writes.

- MONDO release `2026-09-01`,
  `http://purl.obolibrary.org/obo/mondo/releases/2026-09-01/mondo.owl`, sha256
  `358230f024897bdb7dd29ef3c19c59df3c6d7597d0b74b5ce926f7591f215105`, CC BY 4.0.
- SO git commit `4340c143bac3578bba0c013d02e8c5f0e51ad14a`,
  `Ontology_Files/so.owl`, sha256
  `28d19f7767d8848ceb9088658079ede9b467f4ee95205220520229a45582c6aa`, CC BY 4.0.
- Biolink tag `v4.4.5`, predicate IRIs only, CC0.

Attribution: Mondo Disease Ontology, Monarch Initiative, <https://mondo.monarchinitiative.org>;
Sequence Ontology, <http://www.sequenceontology.org>. Both CC BY 4.0. The slice
keeps their IRIs and labels unchanged and drops everything else.

455 nodes, 650 edges (`subClassOf` 523, `gene_associated_with_condition` 72,
`is_sequence_variant_of` 55), 399 labels.

## Mapping

- `rdfs:subClassOf`, child → parent: named-class `rdfs:subClassOf` in SO and
  MONDO, deprecated classes skipped.
- `biolink:gene_associated_with_condition`, gene → disease: MONDO
  `rdfs:subClassOf [owl:onProperty RO:0004003; owl:someValuesFrom gene]`.
- `biolink:is_sequence_variant_of`, variant → gene: an HGVS or ClinVar code and
  an HGNC code on one Observation.

Biolink maps no predicate to RO:0004003 ("has material basis in germline
mutation in"). Its parent RO:0004000 is a narrow mapping of
`condition associated with gene`, whose inverse is the predicate used here.

MONDO writes most RO:0004003 restrictions as top-level `owl:Restriction
rdf:nodeID` blocks that `rdfs:subClassOf rdf:nodeID` points to, not inline.
`load_owl` resolves both; reading only the inline form found 3 of the 72
gene–disease edges.

ClinVar ids are `http://identifiers.org/clinvar:N`. Biolink's prefix map has no
separator (`…/clinvar13961`), so expanding with it naively gives a dead IRI.
HGVS has no Biolink prefix and uses `urn:ivg233:hgvs:` plus the percent-encoded
expression. RefSeq codes stay uncrosswalked.

## Stop-list

The closure never passes through these; `genomics_fixture.STOP` holds each with
its reason. Walking in from any of them reaches most of the slice.

- `BFO_0000001`, `…0002`, `…0016`, `…0017`, `…0020`: BFO upper classes, not
  diseases.
- `MONDO_0000001`, `MONDO_0700096`: disease, human disease; above all 73 seeds.
- `MONDO_0003847`: hereditary disease; above 70+ seeds.
- `MONDO_7770006` to `MONDO_7770009`: "disease by …" groupings, not clinical.
- `SO_0000110`, `SO_0000001`, `SO_0001411`: SO roots.
- `SO_0001060`, `SO_0002072`: above every fixture variant code.

## Coding flaws

- `SO_0002054` uses the underscore form; `code_iri` normalizes it.
- `HGNC:262` is displayed "CYP2C19"; the code wins (CYP2C19 is `HGNC:2621`).
- The fixture displays `SO:0002073` as "wild type"; SO labels it
  "no_sequence_alteration". Its only parent is stop-listed, so it is a node with
  a label and no edge.
- JAK2, KDR and ERBB4 carry a display and no code, and stay unmapped.
- In the somatic example every `*-var` Observation's `subject` is
  `Patient/somaticPatient`, but each paired `*-molc` (molecular consequence)
  Observation's is `Patient/CGPatientExample01`. The SO codes sit on the `-molc`
  Observations, so the SO seed's oracle names CGPatientExample01. PPR still
  ranks somaticPatient second, through `somaticReport.result`.

## Test exemption

`tests/unit/test_233_genomics_fixture.py::TestVendoredFile::test_reproducible_from_package`
skips when `/tmp/gr/p.tgz` is absent. The tarball is a developer-side vendoring
input, not a container, so the fail-not-skip rule for missing containers
(constitution VIII, Gate 1) does not cover it. The rest of the class reads only
the vendored file and never skips.
