# Synthea R4 fixture: source

`synthea-r4-10.json` is a `collection` Bundle of 10 synthetic patients and the
support resources they reference. It was generated with Synthea and then edited
by `tests/e2e/interp_fixture.py`.

- **Generator**: Synthea v4.0.0 (`synthetichealth/synthea`), Apache-2.0. The
  jar reports itself as `v3.4.0-18-ga07a65555` in its run metadata.
- **Jar**:
  <https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar>
- **Jar sha256**:
  `ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1`
- **Java**: OpenJDK 21.0.6 (Temurin-21.0.6+7 LTS)
- **Generated**: 2026-09-26
- **Output sha256**:
  `59579b3bdd212d7be4597144288a03ab3ff094db804296702fee8e27fff90518`

## Regenerate

```bash
java -jar ~/.cache/ivg-235/synthea-4.0.0.jar \
  -s 235 -cs 235 -r 20260101 -e 20260927 -p 10 \
  --exporter.years_of_history 5 --exporter.fhir.export true \
  --exporter.baseDirectory ~/.cache/ivg-235/synthea-10
python -m tests.e2e.interp_fixture vendor ~/.cache/ivg-235/synthea-10/fhir
```

Seeds are 235 for patients and clinicians. The reference date is 2026-01-01 and
the end date is 2026-09-27. Without `-e`, Synthea simulates up to the day it
runs, so its output changes daily.

## Changes

- `urn:uuid:` references are rewritten to `Type/id` through each patient
  bundle's own `fullUrl` map.
- Conditional references (`Practitioner?identifier=…`,
  `Organization?identifier=…`, `Location?identifier=…`) are resolved to
  `Type/id` through the identifiers in the `hospitalInformation*` and
  `practitionerInformation*` support bundles. A reference that does not resolve
  stops the build.
- Support resources are kept only if the kept set references them, directly or
  through another kept support resource. All 38 PractitionerRoles are dropped,
  as are 7 Organizations, 8 Locations and 7 Practitioners.
- `Claim` and `ExplanationOfBenefit` are dropped. They double the size and
  exercise no search parameter that other types lack. The only references to
  them were in `Provenance.target` (940 entries) and `ExplanationOfBenefit.claim`,
  and those entries are removed. A single-valued reference to a dropped type
  would stop the build. There are none.
- `text` (narrative) is removed from every resource.
- The `data` of every Attachment is removed:
  `DocumentReference.content.attachment` and `DiagnosticReport.presentedForm`.
  `contentType` and every other Attachment element are kept.
- **Synthetic `Patient.link`**: one `link` of type `seealso` is added both ways
  between the first two patients sorted by id. This is not Synthea output.
  Synthea writes no `Patient.link` (0 of 109 patients at `-p 100`), and without
  one the coverage report's linked-patient count would be 0 on every fixture.

Every other element is unchanged. After these edits, every reference is
`Type/id` and points to a resource in the file.

## Counts

```text
AllergyIntolerance: 16
CarePlan: 31
CareTeam: 31
Condition: 226
Device: 12
DiagnosticReport: 439
DocumentReference: 274
Encounter: 274
ImagingStudy: 17
Immunization: 81
Location: 31
Medication: 11
MedicationAdministration: 11
MedicationRequest: 196
Observation: 1230
Organization: 31
Patient: 10
Practitioner: 31
Procedure: 532
Provenance: 10
SupplyDelivery: 64
rewritten: 11776
conditional: 2665
dropped_refs: 940
links_added: 2
kept: 3558
```

The file is 5,644,440 bytes, above the 3 MB this spec aimed for. It is kept
whole (research R16).

## Test exemption

`tests/unit/test_235_fixture.py::TestVendoredFile::test_reproducible_from_synthea_output`
rebuilds the file from `~/.cache/ivg-235/synthea-10/fhir` and compares it byte
for byte. It is skipped when that directory is absent, because the 201 MB jar and
its output are not committed. Every other check of the vendored file runs
unconditionally.

## Excluded

None. `test_235` E2E loads fail and list any resource the server rejects.
