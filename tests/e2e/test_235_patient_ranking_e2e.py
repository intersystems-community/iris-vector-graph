"""Spec 235 US2: default PPR exclusions and `fhir_concept_ppr(group_by="patient")` on
the live IVGFHIR repository.

The grouping oracle is independent of `GroupByPatient`: the compartment edges and
their `via` qualifiers are read back from `Graph_KG.rdf_edges`, and each patient's
expected score is the sum of an uncapped, ungrouped run's scores over them.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph import FHIR_PPR_EXCLUDE
from scripts.fhir import capture_410_baseline as cap
from scripts.fhir.bench_235 import top_codes
from tests.e2e.fhir_conftest import GRAPH

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

PRED = "in_patient_compartment"
BASELINE = os.path.join(
    os.path.dirname(__file__), "..", "..", "specs", "235-fhir-graph-interpretation", "baseline_410.json"
)
RUN = uuid.uuid4().hex[:8]
SYN_CG = f"t235rank:{RUN}"
SYN_IRI = f"t235rank:{RUN}:c0"
XW_SOURCE = "ivg235rank"
EVERYTHING = 10**6


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def _compartments(conn):
    """{resource: {patient: via}} over every compartment edge in GRAPH."""
    out = {}
    for s, o, q in _rows(
        conn, "SELECT s, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED
    ):
        out.setdefault(s, {})[o] = (json.loads(q) if q else {}).get("via") or []
    return out


def _expected(scores, comp, via=None):
    """{patient: (score, contributor keys)} from ungrouped scores."""
    want = {}
    for key, score in scores.items():
        if key.startswith("Patient/"):
            continue
        for patient, v in comp.get(key, {}).items():
            if via is None or set(v) & set(via):
                total, keys = want.get(patient, (0.0, set()))
                want[patient] = (total + score, keys | {key})
    return want


# ------------------------------------------------------------------ scenario 1


def test_default_equals_410(fhir_conn_required, fhir_engine):
    """FR-010, SC-002. 231 and the graph counts are unchanged. A 233 or direct-PPR
    difference must vanish once only the compartment edges are excluded, which
    shows it comes from the Provenance denylist (233) or from the new edges being
    walked by a caller that asked for no exclusions (direct `kg_PERSONALIZED_PAGERANK`)."""
    with open(BASELINE) as fh:
        baseline = json.load(fh)
    handles = cap.load(fhir_conn_required, fhir_engine)
    try:
        current = json.loads(json.dumps(cap.capture(fhir_engine, handles)))
        found = cap.diff(baseline, current, whole_graph=False)
        strict = [d for d in found if not d.startswith(("ppr_233.", "ppr_provenance"))]
        assert strict == [], "\n".join(strict)
        # The whole-graph counts drift with leftovers from other runs, so check what
        # 235 promises about them instead: compartment edges are counted nowhere.
        plain = _rows(
            fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p <> ?", GRAPH, PRED
        )[0][0]
        assert current["status_edges"] == plain
        assert set(current["last_counts"]) == set(baseline["last_counts"])
        assert not [p for p in current["last_counts"]["params"] if p.endswith(f".{PRED}")]
        assert set(current["link_report"]) == set(baseline["link_report"])
        assert not [r for r in current["link_report"]["rows"] if r.get("param") == PRED]

        from tests.e2e.genomics_fixture import CONCEPT_GRAPH, SEEDS

        only_comp = {"ppr_231": {}, "ppr_233": {}}
        for iri, hops, direction in SEEDS:
            got = current["ppr_233"][iri]
            changed = [d for d in found if d.startswith(f"ppr_233.{iri}[")]
            print(f"\n233 {iri}: {len(changed)} score(s) differ from pre-235 under the default exclusions")
            for d in changed[:10]:
                print("  ", d)
            if changed:
                got = fhir_engine.fhir_concept_ppr(
                    GRAPH, CONCEPT_GRAPH, [iri], hops=hops, direction=direction, params=cap.PARAMS_233,
                    top_k=None, exclude_predicates=[PRED],
                )
            only_comp["ppr_233"][iri] = got
        only_comp["ppr_provenance"] = fhir_engine.kg_PERSONALIZED_PAGERANK(
            [baseline["provenance_seed"]], bidirectional=True, graph=GRAPH, return_top_k=None,
            exclude_predicates=[PRED],
        )
        rest = [
            d for d in cap.diff(baseline, json.loads(json.dumps(only_comp)), whole_graph=False)
            if not d.startswith("ppr_231")
        ]
        assert rest == [], "\n".join(rest)
    finally:
        cap.teardown(fhir_conn_required, fhir_engine, handles)


# ------------------------------------------------------------------ grouping


@pytest.fixture(scope="module")
def cases(fhir_conn_required, fhir_engine, synthea_loaded, genomics_loaded):
    """[(name, concept graph, ids, kwargs)]: a Synthea condition concept crosswalked
    to the fixture's most frequent Condition code, and a 233 gene seed."""
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, ONTOLOGY, SEEDS, crosswalk_rows, load_fixture

    def clear():
        fhir_engine.erase_graph(SYN_CG)
        fhir_engine.erase_graph(CONCEPT_GRAPH)
        cur = fhir_conn_required.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE source = ?", [XW_SOURCE])
            fhir_conn_required.commit()
        finally:
            cur.close()

    clear()
    try:
        (system, code), *_ = top_codes(synthea_loaded[1])
        fhir_engine.create_node(SYN_IRI, labels=["Concept"], graph=SYN_CG)
        fhir_engine.code_crosswalk_add(system, code, SYN_IRI, target_graph=SYN_CG, relation="exact", source=XW_SOURCE)
        fhir_engine.import_rdf(ONTOLOGY, graph=CONCEPT_GRAPH)
        for s, c, iri in crosswalk_rows(load_fixture())[0]:
            fhir_engine.code_crosswalk_add(s, c, iri, target_graph=CONCEPT_GRAPH, relation="exact", source=XW_SOURCE)
        gene, hops, direction = SEEDS[1]
        yield [
            ("synthea", SYN_CG, [SYN_IRI], {"hops": 0}),
            ("genomics", CONCEPT_GRAPH, [gene], {"hops": hops, "direction": direction, "params": cap.PARAMS_233}),
        ]
    finally:
        clear()


@pytest.fixture(scope="module")
def runs(fhir_conn_required, fhir_engine, cases):
    """{name: (ungrouped uncapped scores, compartments)} per case."""
    comp = _compartments(fhir_conn_required)
    out = {}
    for name, cg, ids, kw in cases:
        scores = fhir_engine.fhir_concept_ppr(GRAPH, cg, ids, top_k=None, **kw)
        assert scores, name
        out[name] = scores
    return out, comp


def _grouped(fhir_engine, cases, name, **kw):
    _, cg, ids, base = next(c for c in cases if c[0] == name)
    return fhir_engine.fhir_concept_ppr(GRAPH, cg, ids, group_by="patient", **base, **kw)


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_group_sum(fhir_engine, cases, runs, name):
    """Scenario 2, SC-003."""
    scores, comp = runs[0][name], runs[1]
    want = _expected(scores, comp)
    assert want, f"{name}: no compartment resource scored"
    out = _grouped(fhir_engine, cases, name, top_k=None)
    got = {p["patient"]: p for p in out["patients"]}
    assert set(got) == set(want)
    for patient, (total, keys) in want.items():
        p = got[patient]
        assert abs(p["score"] - total) < 1e-12, (patient, p["score"], total)
        assert p["contributors_total"] == len(keys)
        assert len(p["contributors"]) == min(5, len(keys))
        for c in p["contributors"]:
            assert c["key"] in keys and abs(c["score"] - scores[c["key"]]) < 1e-12
        cs = [c["score"] for c in p["contributors"]]
        assert cs == sorted(cs, reverse=True)
    ranked = [p["score"] for p in out["patients"]]
    assert ranked == sorted(ranked, reverse=True)
    two = {p["patient"]: p for p in _grouped(fhir_engine, cases, name, top_k=None, explain_top=2)["patients"]}
    assert all(len(p["contributors"]) <= 2 for p in two.values())
    print(f"\n{name} grouped top 10:", [(p["patient"], round(p["score"], 6)) for p in out["patients"][:10]])


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_via_filter(fhir_engine, cases, runs, name):
    """Scenario 3."""
    scores, comp = runs[0][name], runs[1]
    want = _expected(scores, comp, via=["subject"])
    out = _grouped(fhir_engine, cases, name, via=["subject"], top_k=None, explain_top=EVERYTHING)
    got = {p["patient"]: p for p in out["patients"]}
    assert set(got) == set(want)
    for patient, p in got.items():
        assert abs(p["score"] - want[patient][0]) < 1e-12
        assert {c["key"] for c in p["contributors"]} == want[patient][1]
        assert all("subject" in c["via"] for c in p["contributors"])


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_unattributed(fhir_engine, cases, runs, name):
    """Scenario 4. A resource in two compartments counts once here, so the check is
    on distinct contributor keys, not on the sum of `contributors_total`."""
    scores = runs[0][name]
    out = _grouped(fhir_engine, cases, name, top_k=None, explain_top=EVERYTHING)
    keys = {c["key"] for p in out["patients"] for c in p["contributors"]}
    assert out["unattributed"]["count"] + len(keys) == len(scores)
    mass = out["unattributed"]["score"] + sum(scores[k] for k in keys)
    assert abs(mass - sum(scores.values())) < 1e-9
    assert not any(k.startswith("Patient/") for k in keys)


# ------------------------------------------------------------------ Provenance


@pytest.fixture(scope="module")
def provenance_copy(fhir_conn_required, fhir_engine, synthea_loaded):
    """A scratch graph holding the Synthea run's edges minus compartment edges and
    Provenance target/entity."""
    keys = {_key(r) for r in synthea_loaded[1]}
    edges = [
        {"source_id": s, "predicate": p, "target_id": o}
        for s, p, o in _rows(fhir_conn_required, "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH)
        if s in keys
        and p != PRED
        and not (s.startswith("Provenance/") and p in ("target", "entity"))
    ]
    g = f"t235prov:{RUN}"
    fhir_engine.erase_graph(g)
    try:
        # One edge at a time: bulk_create_edges would rebuild ^KG for the namespace.
        for n in sorted({e["source_id"] for e in edges} | {e["target_id"] for e in edges}):
            fhir_engine.create_node(n, labels=[n.split("/")[0]], graph=g)
        for e in edges:
            fhir_engine.create_edge(e["source_id"], e["predicate"], e["target_id"], graph=g)
        yield g, keys
    finally:
        fhir_engine.erase_graph(g)


def test_provenance_not_walked(fhir_engine, cases, provenance_copy):
    """Scenario 5, FR-009."""
    g, keys = provenance_copy
    seeds = fhir_engine.fhir_resolve_concepts(GRAPH, SYN_CG, [SYN_IRI])
    assert seeds and set(seeds) <= keys
    default = fhir_engine.fhir_concept_ppr(GRAPH, SYN_CG, [SYN_IRI], hops=0, top_k=None)
    # `[]` walks every edge of the copy and, like the default call, lifts the pre-235
    # 1000-row cap; max_iterations is fhir_concept_ppr's.
    copy = fhir_engine.kg_PERSONALIZED_PAGERANK(
        seeds, bidirectional=True, graph=g, return_top_k=None, max_iterations=20, exclude_predicates=[]
    )
    assert set(default) == set(copy), (len(default), len(copy), sorted(set(default) ^ set(copy))[:5])
    for k in default:
        assert abs(default[k] - copy[k]) < 1e-12, (k, default[k], copy[k])
    walked = fhir_engine.fhir_concept_ppr(GRAPH, SYN_CG, [SYN_IRI], hops=0, top_k=None, exclude_predicates=[])
    assert [k for k, v in walked.items() if k.startswith("Provenance/") and v > 0]
    assert FHIR_PPR_EXCLUDE == (PRED, "Provenance.target", "Provenance.entity")


def test_cypher_unaffected(fhir_engine, synthea_loaded):
    r = fhir_engine.execute_cypher(
        f"USE GRAPH '{GRAPH}' MATCH (pv:Provenance)-[:target]->(x) WHERE pv.id STARTS WITH $pfx RETURN pv.id, x.id",
        {"pfx": f"Provenance/{synthea_loaded[0]}-"},
    )
    rows = r.rows if hasattr(r, "rows") else r.get("rows", [])
    assert rows
