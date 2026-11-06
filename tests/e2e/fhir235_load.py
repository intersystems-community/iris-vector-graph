"""Spec 235's ranking data: the 233 genomics fixture and one 233 model-result trio under the fixed prefix `b410`, the
233 concept graph and crosswalk, and the 231 bulk concept pipeline, in the FHIR repository's graph; and the calls
whose answers spec 235 must keep (FR-010, SC-002).

Used by tests/e2e/test_235_patient_ranking_e2e.py, which checks each answer against a walk over the rows (4.6.0 R309),
and by scripts/fhir/capture_410_baseline.py, which records them for a comparison between versions. It was part of that
script until R309: a test that imports a capture module reads like one that takes its answers from a capture.
"""

from __future__ import annotations

PREFIX = "b410"
PARAMS_233 = ["component-value-concept", "value-concept"]
INPUTS_233 = ["EGFR-L858R-var", "ROS1-Fusion-var"]


def _key(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


def load(conn, engine) -> dict:
    """Load everything the baseline calls need, under PREFIX. Idempotent: PUT is an
    upsert and the concept graph is erased first. Returns the handles `calls` and
    `teardown` use."""
    from tests.e2e.fhir_conftest import GRAPH, FhirLoader, load_run, prefix_resources
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, ONTOLOGY, crosswalk_rows, load_fixture, model_result

    if not engine.fhir_graph_register(denylist=[]).get("rebuilt"):
        engine.fhir_graph_rebuild(GRAPH)
    loader = FhirLoader(conn)
    loader.prefix = PREFIX
    genomics = load_run(conn, loader, prefix_resources(load_fixture(), PREFIX))
    patient = f"Patient/{PREFIX}-somaticPatient"
    inputs = [f"Observation/{PREFIX}-{i}" for i in INPUTS_233]
    trio = load_run(conn, loader, prefix_resources(model_result(7, patient, inputs), PREFIX))

    _clear_concepts_233(conn, engine)
    engine.import_rdf(ONTOLOGY, graph=CONCEPT_GRAPH)
    rows, _ = crosswalk_rows(load_fixture())
    for system, code, iri in rows:
        engine.code_crosswalk_add(system, code, iri, target_graph=CONCEPT_GRAPH, relation="exact", source="ivg233")

    # 231's bulk pipeline: fixed ids, loaded once per repository.
    _pipeline_231(conn, engine)
    return {"loader": loader, "resources": genomics + trio, "trio": {r["resourceType"]: r for r in trio}}


def _pipeline_231(conn, engine) -> None:
    from tests.e2e import test_231_fhir_concept_pipeline_e2e as t231
    from tests.e2e.fhir_conftest import GRAPH, FhirLoader

    loaded = t231._rows(
        conn,
        "SELECT COUNT(*) FROM \"HSFHIR_X0001_R\".Rsrc WHERE Key %STARTSWITH 'Condition/sc005-' AND Deleted = 0",
    )[0][0]
    if loaded < t231.N:
        ld = FhirLoader(conn)
        for i in range(t231.N):
            ld.put({"resourceType": "Patient", "id": f"sc005-p{i}"})
            ld.put(t231._condition(f"sc005-c{i}", f"sc005-p{i}", t231._code(i)))
    for c in t231.PARENTS + t231.CHILDREN:
        try:
            engine.create_node(c, labels=["Concept"], graph=t231.CG)
        except Exception:  # already there from an earlier run
            pass
    for p, c in zip(t231.PARENTS, t231.CHILDREN):
        try:
            engine.create_edge(p, "narrower", c, graph=t231.CG)
        except Exception:
            pass
    for i, c in enumerate(t231.PARENTS + t231.CHILDREN):
        engine.code_crosswalk_add(
            t231.ICD, f"Z{i:02d}.1", c, target_graph=t231.CG, relation="exact", source="test231"
        )
    engine.fhir_graph_sync(GRAPH)


def calls(engine, handles: dict) -> dict:
    """The calls whose answers 235 must keep (FR-010, SC-002), as {name: answer}."""
    from tests.e2e import test_231_fhir_concept_pipeline_e2e as t231
    from tests.e2e.fhir_conftest import GRAPH
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, SEEDS

    out: dict = {"ppr_231": {}, "ppr_233": {}}
    out["ppr_231"]["parents3_out_all"] = engine.fhir_concept_ppr(
        GRAPH, t231.CG, t231.PARENTS[:3], hops=1, direction="out", top_k=None
    )
    out["ppr_231"]["parents_out_top50"] = engine.fhir_concept_ppr(
        GRAPH, t231.CG, t231.PARENTS, hops=1, direction="out", max_iterations=20
    )
    for iri, hops, direction in SEEDS:
        out["ppr_233"][iri] = engine.fhir_concept_ppr(
            GRAPH, CONCEPT_GRAPH, [iri], hops=hops, direction=direction, params=PARAMS_233, top_k=None
        )
    status = engine.fhir_graph_status(GRAPH)
    out["status_edges"] = status["edges"]
    out["last_counts"] = status["last_counts"]
    out["link_report"] = engine.fhir_link_report(GRAPH)
    pred = _key(handles["trio"]["Observation"])
    out["provenance_seed"] = pred
    out["ppr_provenance"] = engine.kg_PERSONALIZED_PAGERANK([pred], bidirectional=True, graph=GRAPH, return_top_k=None)
    return out


def teardown(conn, engine, handles: dict) -> None:
    from tests.e2e.fhir_conftest import teardown_run

    teardown_run(conn, handles["loader"], handles["resources"])
    _clear_concepts_233(conn, engine)


def _clear_concepts_233(conn, engine) -> None:
    """Erase the 233 concept graph and its crosswalk rows."""
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH

    engine.erase_graph(CONCEPT_GRAPH)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND source = ?", [CONCEPT_GRAPH, "ivg233"]
        )
        conn.commit()
    finally:
        cur.close()
