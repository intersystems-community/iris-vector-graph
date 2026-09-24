"""The FHIR graph demo's cohort: deterministic, self-contained, and fully crosswalked.

The demo page ranks patients by walking from a concept to the Conditions that carry
its codes. A code with no crosswalk row is invisible to that walk, and a reference to
a resource the cohort never creates lands in `fhir_unresolved` instead of the graph,
so both would make the demo quietly show less than the data holds.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from iris_demo_server.services import fhir_demo_data as data  # noqa: E402


def _refs(resource):
    """Every `reference` string anywhere in a resource."""
    if isinstance(resource, dict):
        for k, v in resource.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(resource, list):
        for item in resource:
            yield from _refs(item)


def _codes(resource):
    for coding in resource.get("code", {}).get("coding", []):
        yield coding["system"], coding["code"]


def test_cohort_is_deterministic():
    assert data.build_cohort() == data.build_cohort()


def test_cohort_size_and_types():
    cohort = data.build_cohort()
    types = Counter(r["resourceType"] for r in cohort)
    assert types["Patient"] == data.COHORT_SIZE
    assert types["Practitioner"] == len(data.PRACTITIONERS)
    assert types["Organization"] >= 1
    assert types["Encounter"] >= data.COHORT_SIZE
    assert types["Condition"] > 0 and types["Observation"] > 0


def test_every_id_is_demo_prefixed_and_unique():
    cohort = data.build_cohort()
    keys = [f"{r['resourceType']}/{r['id']}" for r in cohort]
    assert len(keys) == len(set(keys))
    assert all(r["id"].startswith("demo-") for r in cohort)


def test_every_reference_resolves_inside_the_cohort():
    cohort = data.build_cohort()
    keys = {f"{r['resourceType']}/{r['id']}" for r in cohort}
    dangling = {ref for r in cohort for ref in _refs(r)} - keys
    assert not dangling


def test_referenced_resources_load_first():
    """PUT order matters only for readability of the sync, but a target loaded
    before its referrer means the rebuild never sees a `missing` row."""
    seen = set()
    for r in data.build_cohort():
        for ref in _refs(r):
            assert ref in seen, f"{r['resourceType']}/{r['id']} references {ref} before it exists"
        seen.add(f"{r['resourceType']}/{r['id']}")


def test_every_clinical_code_is_crosswalked():
    walked = {(system, code) for system, code, _, _ in data.CROSSWALK}
    for r in data.build_cohort():
        if r["resourceType"] in ("Condition", "Observation"):
            for pair in _codes(r):
                assert pair in walked, f"{pair} has no crosswalk row"


def test_crosswalk_targets_and_hierarchy_use_known_concepts():
    for _, _, concept, relation in data.CROSSWALK:
        assert concept in data.CONCEPTS
        assert relation in ("exact", "broader", "narrower", "related")
    for parent, child in data.NARROWER:
        assert parent in data.CONCEPTS and child in data.CONCEPTS


def test_diabetes_hierarchy_reaches_nephropathy_in_two_hops():
    """The demo's headline scenario: the broad concept has no codes of its own, so
    only expansion finds its patients."""
    children = {}
    for parent, child in data.NARROWER:
        children.setdefault(parent, set()).add(child)
    one = children[data.DIABETES]
    two = set().union(*(children.get(c, set()) for c in one))
    assert data.TYPE2_DM in one
    assert data.DIABETIC_NEPHROPATHY in two
    assert not [c for c in data.CROSSWALK if c[2] == data.DIABETES]


def test_every_concept_with_codes_has_patients():
    by_concept = {(s, c): concept for s, c, concept, _ in data.CROSSWALK}
    hit = Counter()
    for r in data.build_cohort():
        if r["resourceType"] == "Condition":
            for pair in _codes(r):
                hit[by_concept[pair]] += 1
    for concept in {c for _, _, c, rel in data.CROSSWALK if rel == "exact"}:
        assert hit[concept] >= 5, f"{concept} has {hit[concept]} conditions"


def test_labs_are_related_not_exact():
    """Lab codes are evidence, not diagnoses: `relations=["exact"]` must drop them."""
    for system, _, _, relation in data.CROSSWALK:
        if system == data.LOINC:
            assert relation == "related"


def test_specialists_see_their_specialty():
    """PPR should put the endocrinologist near diabetic patients; that only works if
    the encounters route that way."""
    cohort = data.build_cohort()
    by_key = {f"{r['resourceType']}/{r['id']}": r for r in cohort}
    diabetic = {
        r["subject"]["reference"]
        for r in cohort
        if r["resourceType"] == "Condition" and any(c.startswith("E11") for _, c in _codes(r))
    }
    endo = f"Practitioner/{data.PRACTITIONERS[0]['id']}"
    assert data.PRACTITIONERS[0]["specialty"] == "Endocrinology"
    endo_patients = {
        r["subject"]["reference"]
        for r in cohort
        if r["resourceType"] == "Encounter"
        and any(p["individual"]["reference"] == endo for p in r.get("participant", []))
    }
    assert endo_patients and endo_patients <= diabetic
    assert all(k in by_key for k in endo_patients)


def test_patient_display_name():
    p = next(r for r in data.build_cohort() if r["resourceType"] == "Patient")
    assert data.display_name(p) == f"{p['name'][0]['given'][0]} {p['name'][0]['family']}"
    assert data.display_name({"resourceType": "Practitioner", "name": []}) == ""


def test_engine_is_built_for_the_fhir_namespace(monkeypatch):
    """The engine defaults to USER; the demo must pass its own namespace, or every
    run prints a spurious NamespaceMismatchWarning."""
    import iris_vector_graph.engine as eng

    seen = {}

    class FakeEngine:
        def __init__(self, conn, **kw):
            seen.update(kw)

    monkeypatch.setenv("IVG_FHIR_NAMESPACE", "MYFHIR")
    monkeypatch.setattr(eng, "IRISGraphEngine", FakeEngine)
    data.make_engine(object())
    assert seen["namespace"] == "MYFHIR"


def test_seed_reports_progress_while_loading(monkeypatch):
    """885 PUTs take ~30 s; silence for that long reads as a hang."""
    class Engine:
        def fhir_graph_register(self, endpoint):
            return {"graph_id": "fhir:NS:X0001"}

        def create_node(self, *a, **k):
            pass

        create_edge = code_crosswalk_add = create_node

    monkeypatch.setattr(data, "load_classes", lambda conn, root: [])
    monkeypatch.setattr(data, "make_dispatch", lambda conn: lambda m, p, b: {"status": "201"})
    monkeypatch.setattr(data, "remove_live_conditions", lambda *a: 0)
    monkeypatch.setattr(data, "sync_until_done", lambda e, g: {"status": "ok"})
    lines = []
    data.seed(object(), Engine(), log=lines.append)
    progress = [line for line in lines if "/" in line and line.startswith("PUT")]
    assert len(progress) >= 5, lines
