# CPG example bundle: source

`cpg-chf-2.0.0.json` is a `collection` Bundle of the congestive heart failure
(CHF) examples from the HL7 FHIR Clinical Practice Guidelines IG.

- **Package**: `hl7.fhir.uv.cpg` version `2.0.0`, from
  <https://packages.fhir.org/hl7.fhir.uv.cpg/2.0.0>
- **Package sha256**:
  `e87e671224571ed545d21a982683e7bf77ec8ccd0877b4addd4a11024852bcc4`
- **License**: CC0-1.0 (the package's `package.json`)
- **Fetched**: 2026-09-23

## Selection

Every `package/example/*chf*` file, plus `Library-CHF.json`, whose resource
type is PlanDefinition, ActivityDefinition, Measure, MeasureReport, Library,
CarePlan, RequestGroup, ServiceRequest, Patient, Encounter, Condition or Goal.
That gives 62 resources.

## Changes

- `Library.content` is removed. It holds 569 KB of base64 CQL and ELM, and no links.
- `text` (the narrative) is removed from every resource.

Ids, urls, versions and every link element are unchanged.
