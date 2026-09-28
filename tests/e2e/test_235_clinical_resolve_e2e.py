"""Spec 235 US3: `params="clinical"` and the Medication hop on the live IVGFHIR
repository.

Oracles are fixture-side: `expected` in genomics_fixture for the 233 variant
Observations, and the vendored Synthea file for the Medication hop (every
MedicationRequest/Statement/Administration/Dispense whose `medicationReference`
names a Medication coded with the crosswalked RxNorm code).
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest

from iris_vector_graph import CLINICAL_PARAMS
from scripts.fhir.bench_235 import top_codes
from tests.e2e.fhir_conftest import GRAPH

pytestmark = [pytest.mark.e2e]

RUN = uuid.uuid4().hex[:8]
CG = f"t235clin:{RUN}"
RX_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
RX_CODE = "313521"
RX_IRI = f"{CG}:rx{RX_CODE}"
SCT_IRI = f"{CG}:sct"
XW_SOURCE = "ivg235clin"
HOP_TYPES = ("MedicationRequest", "MedicationStatement", "MedicationAdministration", "MedicationDispense")
MED_CC = "medicationCodeableConcept"


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def _codes(cc):
    return {(c.get("system"), c.get("code")) for c in (cc or {}).get("coding", [])}


def _code_matches(resources, pair):
    """Run keys whose `code` param holds `pair`: `code` on most types, the
    medication CodeableConcept on the four medication-use types (research R9)."""
    out = set()
    for r in resources:
        cc = r.get(MED_CC) if r["resourceType"] in HOP_TYPES else r.get("code")
        if pair in _codes(cc):
            out.add(_key(r))
    return out


def _hop(resources, pair):
    meds = {_key(r) for r in resources if r["resourceType"] == "Medication" and pair in _codes(r.get("code"))}
    return {
        _key(r)
        for r in resources
        if r["resourceType"] in HOP_TYPES and (r.get("medicationReference") or {}).get("reference") in meds
    }


@pytest.fixture(scope="module")
def concepts(fhir_conn_required, fhir_engine, synthea_loaded, genomics_loaded):
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, ONTOLOGY, crosswalk_rows, load_fixture

    def clear():
        fhir_engine.erase_graph(CG)
        fhir_engine.erase_graph(CONCEPT_GRAPH)
        cur = fhir_conn_required.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE source = ?", [XW_SOURCE])
            fhir_conn_required.commit()
        finally:
            cur.close()

    clear()
    try:
        (sct_system, sct_code), *_ = top_codes(synthea_loaded[1])
        for iri in (RX_IRI, SCT_IRI):
            fhir_engine.create_node(iri, labels=["Concept"], graph=CG)
        fhir_engine.code_crosswalk_add(RX_SYSTEM, RX_CODE, RX_IRI, target_graph=CG, source=XW_SOURCE)
        fhir_engine.code_crosswalk_add(sct_system, sct_code, SCT_IRI, target_graph=CG, source=XW_SOURCE)
        fhir_engine.import_rdf(ONTOLOGY, graph=CONCEPT_GRAPH)
        for s, c, iri in crosswalk_rows(load_fixture())[0]:
            fhir_engine.code_crosswalk_add(s, c, iri, target_graph=CONCEPT_GRAPH, source=XW_SOURCE)
        yield (sct_system, sct_code)
    finally:
        clear()


@pytest.fixture(scope="module")
def synthea(synthea_loaded):
    resources = synthea_loaded[1]
    return resources, {_key(r) for r in resources}


@pytest.fixture(scope="module")
def genomics(genomics_loaded):
    resources = genomics_loaded[1]
    return resources, {_key(r) for r in resources}


def test_default_code_only(fhir_engine, concepts, synthea, genomics):
    """Scenario 1: the default is `code` alone. A gene misses the variant
    Observations; a Condition code resolves exactly its `code` matches, no hop."""
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, SEEDS, expected

    g_res, g_keys = genomics
    gene = SEEDS[1][0]
    variants = expected(g_res, {gene})
    assert variants
    assert not set(fhir_engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, [gene])) & variants

    s_res, s_keys = synthea
    want = _code_matches(s_res, concepts)
    assert want
    out = fhir_engine.fhir_resolve_concepts(GRAPH, CG, [SCT_IRI], detail=True)
    assert set(out["keys"]) & s_keys == want
    assert out["params_used"] == ["code"] and out["dropped_params"] == []
    assert out["via"] == {} and out["medication_hop"]["added"] == 0
    assert fhir_engine.fhir_resolve_concepts(GRAPH, CG, [SCT_IRI]) == out["keys"]
    print(f"\ndefault {concepts}: {len(want)} resources")


def test_clinical_resolves_values_and_components(fhir_engine, concepts, genomics):
    """Scenario 2."""
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, SEEDS, expected

    res, keys = genomics
    gene = SEEDS[1][0]
    out = fhir_engine.fhir_resolve_concepts(GRAPH, CONCEPT_GRAPH, [gene], params="clinical", detail=True)
    assert set(out["keys"]) & keys == expected(res, {gene})
    idx = fhir_engine._fhir_call("IndexedTokenParams", GRAPH, '["' + '","'.join(CLINICAL_PARAMS) + '"]')
    assert out["params_used"] == idx["used"] and out["dropped_params"] == idx["dropped"]
    assert set(out["params_used"]) | set(out["dropped_params"]) == set(CLINICAL_PARAMS)
    print(f"\nclinical {gene}: used {out['params_used']} dropped {out['dropped_params']}, {len(out['keys'])} keys")


def test_medication_hop(fhir_engine, concepts, synthea):
    """Scenario 3: every medication-use resource in the run whose `medication`
    references a resolved Medication is added, tagged `via: "medication"`."""
    res, keys = synthea
    hop = _hop(res, (RX_SYSTEM, RX_CODE))
    direct = _code_matches(res, (RX_SYSTEM, RX_CODE))
    assert hop and not hop & direct
    out = fhir_engine.fhir_resolve_concepts(GRAPH, CG, [RX_IRI], detail=True)
    tagged = {k for k, v in out["via"].items() if v == "medication"}
    assert tagged & keys == hop
    assert set(out["keys"]) & keys == direct | hop
    assert out["medication_hop"]["added"] == len(tagged)
    meds = {k for k in out["keys"] if k.startswith("Medication/")}
    assert out["medication_hop"]["medications"] == len(meds)
    assert len(out["keys"]) == len(set(out["keys"]))
    assert fhir_engine.fhir_resolve_concepts(GRAPH, CG, [RX_IRI]) == out["keys"]

    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        fhir_engine.fhir_resolve_concepts(GRAPH, CG, [RX_IRI], params="clinical", detail=True)
        samples.append(time.perf_counter() - t0)
    print(
        f"\nhop {RX_CODE}: {len(meds)} Medication, direct {len(direct)}, added {len(hop)} in run "
        f"({out['medication_hop']}); clinical resolve median {statistics.median(samples) * 1000:.1f} ms"
    )


def test_hop_seeds_ppr(fhir_engine, concepts, synthea):
    res, _ = synthea
    hop = _hop(res, (RX_SYSTEM, RX_CODE))
    scores = fhir_engine.fhir_concept_ppr(GRAPH, CG, [RX_IRI], hops=0, top_k=None)
    assert all(scores.get(k, 0) > 0 for k in hop), sorted(k for k in hop if not scores.get(k))
