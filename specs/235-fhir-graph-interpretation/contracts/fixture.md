<!-- markdownlint-disable MD013 -->

# Contract: Synthea fixture, oracles and bench

## Generation (one-off, not part of the test run)

```bash
curl -sL https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar \
  -o ~/.cache/ivg-235/synthea-4.0.0.jar           # sha256 recorded in SOURCE.md
java -jar ~/.cache/ivg-235/synthea-4.0.0.jar -s 235 -cs 235 -r 20260101 -e 20260927 -p 10 \
  --exporter.years_of_history 5 --exporter.fhir.export true \
  --exporter.baseDirectory ~/.cache/ivg-235/synthea-10
.venv/bin/python -m tests.e2e.interp_fixture vendor ~/.cache/ivg-235/synthea-10/fhir
```

The scale set uses the same command with `-p 100` and `~/.cache/ivg-235/synthea-100`. It is never vendored.

## `tests/e2e/interp_fixture.py`

These functions are pure: none touches IRIS.

```python
FIXTURE = "tests/e2e/fixtures/fhir/synthea/synthea-r4-10.json"
DROP_TYPES = {"Claim", "ExplanationOfBenefit"}

def assemble(patient_bundles: list[dict], support_bundles: list[dict]) -> tuple[list[dict], dict]:
    """Synthea output -> (resources, counts).

    - Rewrites `urn:uuid:` references per bundle to `Type/id`.
    - Resolves conditional `Type?identifier=sys|val` references through the
      support bundles, and keeps only the support resources that are referenced.
    - Drops DROP_TYPES, strips `text` and every `attachment.data`.
    - Adds one `Patient.link` (type `seealso`) between the first two patients
      (sorted by id), both ways.
    - Asserts every reference is `Type/id` into the set.
    Counts: per type, references rewritten, conditionals resolved, links added."""

def expected_compartments(resources: list[dict], comp: dict[str, set[str]],
                          paths: dict[tuple[str, str], list[str]]) -> dict[str, dict[str, list[str]]]:
    """{source key: {patient key: sorted via}}. `comp` is type -> patient params,
    `paths` is (type, param) -> reference element paths from SearchColumn."""

def expected_categories(resources) -> dict[str, list[str]]      # key -> sorted "system|code"
def expected_profiles(resources) -> dict[str, list[str]]        # key -> sorted meta.profile
def expected_labels(resources, r4_types: set[str]) -> dict[str, set[str]]
def expected_coverage(resources, comp, crosswalk) -> dict        # the fhir_coverage_report figures
def expected_gaps(resources, crosswalk, params, top=20) -> dict
def pascal(code: str) -> str                                     # "vital-signs" -> "VitalSigns"
```

These rely on 233's helpers in `tests/e2e/fhir_conftest.py`: `prefix_resources`, `load_run` and `teardown_run`.

## `SOURCE.md`

`SOURCE.md` records:

- the generator (Synthea v4.0.0, Apache-2.0) and its jar URL and sha256;
- the Java version;
- the exact command line and seeds;
- the output file sha256;
- the edits: dropped types, stripped fields, reference rewrites, the synthetic `Patient.link`;
- the per-type counts that `assemble` prints.

It also states that the synthetic `Patient.link` is not Synthea output, and why it was added.

## Compartment oracle (US1, SC-001)

```sql
SELECT Key, value FROM HSFHIR_X0001_S.<Type>Compartments WHERE value %STARTSWITH 'Patient/'
```

The query runs for each compartment type present in this run. The test then:

1. drops `Key = value` rows (Patient self rows);
2. drops rows whose `value` is not a live Patient node in the graph;
3. keeps only this run's prefixed keys.

Assertions:

- Membership: `{Key: {value}}` equals `{s: {o_id}}` over `in_patient_compartment` edges.
- `via` equals `expected_compartments(...)`.
- Documented exclusions are listed in the test. There are none expected (research R4).

## Bench (`scripts/fhir/bench_235.py`)

```text
bench_235.py --label {baseline|current} --repo <checkout> --set {fixture|scale} --out <json>
bench_235.py compare baseline.json current.json     # exit 1 on budget breach
```

- It measures:
  - the median `fhir_graph_rebuild` over 5 runs;
  - the median `fhir_concept_ppr` over 7 runs, with default arguments and with `group_by="patient"`;
  - `fhir_coverage_report` and `fhir_concept_gaps`, 5 runs each.
- Each run records the commit, the container image and the resource counts.
- Budgets:
  - sync at most +15%;
  - group-by PPR at most +20% over the baseline default PPR, at scale;
  - reports under 2 s, at scale.
