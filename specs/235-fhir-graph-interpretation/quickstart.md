<!-- markdownlint-disable MD013 -->

# Quickstart: FHIR graph interpretation

This guide uses the `ivg-iris-enterprise` container (port 31972) and the
`IVGFHIR` namespace, installed as described in `tests/e2e/fhir_conftest.py`.
It needs 233 merged (research R0).

## 1. Generate and vendor the fixture (once)

Follow [contracts/fixture.md](contracts/fixture.md#generation-one-off-not-part-of-the-test-run).
The `vendor` step writes `tests/e2e/fixtures/fhir/synthea/synthea-r4-10.json`
and prints the per-type counts that `SOURCE.md` records.

## 2. Run the gates

```bash
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
.venv/bin/pytest tests/unit/test_235_*.py
.venv/bin/pytest tests/integration/test_235_*.py
.venv/bin/pytest tests/e2e/test_235_compartment_e2e.py -s        # US1
.venv/bin/pytest tests/e2e/test_235_patient_ranking_e2e.py -s    # US2
.venv/bin/pytest tests/e2e/test_235_clinical_resolve_e2e.py -s   # US3
.venv/bin/pytest tests/e2e/test_235_coverage_gaps_e2e.py -s      # US4
.venv/bin/pytest tests/e2e/test_235_category_profile_e2e.py -s   # US5
.venv/bin/pytest tests/e2e/test_235_anchors_e2e.py -s            # US6
.venv/bin/pytest tests/e2e/test_235_reinterpret_e2e.py -s        # US7
.venv/bin/pytest tests/e2e/test_231_*.py tests/e2e/test_232_*.py \
  tests/e2e/test_233_*.py tests/e2e/test_fhir_demo_e2e.py        # SC-009
```

## 3. Which patients, and why

```python
from tests.e2e.fhir_conftest import GRAPH

engine.fhir_graph_sync(GRAPH)["interpretation"]
# {"compartment_edges_added": ..., "full_rederivation": true, "interpretation_version": 1, ...}

engine.execute_cypher(
    f"USE GRAPH '{GRAPH}' "
    "MATCH (o:Observation:VitalSigns)-[:in_patient_compartment]->(p:Patient) "
    "RETURN p.id, count(o) ORDER BY count(o) DESC LIMIT 5"
)

ranked = engine.fhir_concept_ppr(
    GRAPH, "concepts:demo", ["MONDO:0005148"],
    params="clinical", group_by="patient", via=["subject", "patient"], explain_top=3,
)
ranked["patients"][0]
# {"patient": "Patient/...", "score": ..., "contributors_total": ..., "contributors": [...]}
```

## 4. How interpretable is this repository?

```python
cov = engine.fhir_coverage_report(GRAPH)
cov["types"]["Observation"]["compartment_share"], cov["stale"]
gaps = engine.fhir_concept_gaps(GRAPH, params="clinical")
gaps["unmatched"][:5], gaps["text_only"]
```

## 5. Upgrade from 4.1.0

A graph synced by 4.1.0 has `interp_version` NULL. The first 4.2 sync
re-derives every resource and reports `full_rederivation: true`. To re-derive
on demand, call `engine.fhir_reinterpret(GRAPH)`.

## 6. Performance budgets

The baseline needs 4.1.0 on both sides: its ObjectScript deployed into IVGFHIR,
and its Python imported from an archive of the same commit. Run the two
benches back to back, since the machine's load moves a Rebuild by 2x.

```bash
SHA=2d92da2dbea89f26645c965ed6556d6931d0cb19
mkdir -p ~/.cache/ivg-235/py_2d92da2
git archive $SHA iris_vector_graph | tar -x -C ~/.cache/ivg-235/py_2d92da2

# 4.1.0 classes, then the baseline bench
.venv/bin/python -c "import iris; from tests.e2e.test_235_reinterpret_e2e import _deploy_from, _archive_410; \
  _deploy_from(iris.connect('localhost', 31972, 'IVGFHIR', '_SYSTEM', 'SYS'), _archive_410())"
.venv/bin/python -m scripts.fhir.bench_235 --label baseline --repo ~/.cache/ivg-235/py_2d92da2 \
  --commit $SHA --set scale --out /tmp/base_scale.json

# current classes, then the current bench
.venv/bin/python -c "import iris; from tests.e2e.fhir_conftest import deploy; \
  deploy(iris.connect('localhost', 31972, 'IVGFHIR', '_SYSTEM', 'SYS'))"
.venv/bin/python -m scripts.fhir.bench_235 --label current --set scale --out /tmp/cur_scale.json

.venv/bin/python -m scripts.fhir.bench_235 compare /tmp/base_scale.json /tmp/cur_scale.json
```

Repeat with `--set fixture`. Record the runs as `{"fixture": run, "scale": run}`
in `baseline.json` and `current.json` here, and the medians in
`docs/FHIR_GRAPH.md`.
