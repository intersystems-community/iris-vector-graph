"""Spec 235 T003: the 4.1.0 behaviour baseline, captured before any engine change.

Loads the 233 genomics fixture and one 233 model-result trio under the fixed prefix
`b410`, the 233 concept graph and crosswalk, and the 231 bulk concept pipeline, then
records the default-argument PPR score maps, the graph status counts, the link report
and the provenance-seed PPR. T030 and T079 reload the same keys with `load()` and
compare their own `capture()` with the file.

    .venv/bin/python -m scripts.fhir.capture_410_baseline --out specs/235-fhir-graph-interpretation/baseline_410.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

PREFIX = "b410"
PARAMS_233 = ["component-value-concept", "value-concept"]
INPUTS_233 = ["EGFR-L858R-var", "ROS1-Fusion-var"]


def _key(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


def load(conn, engine) -> dict:
    """Load everything the baseline calls need, under PREFIX. Idempotent: PUT is an
    upsert and the concept graph is erased first. Returns the handles `capture` and
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

    engine.erase_graph(CONCEPT_GRAPH)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND source = ?", [CONCEPT_GRAPH, "ivg233"]
        )
        conn.commit()
    finally:
        cur.close()
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


def capture(engine, handles: dict) -> dict:
    """The calls whose 4.1.0 answers 235 must keep (FR-010, SC-002)."""
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
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH

    teardown_run(conn, handles["loader"], handles["resources"])
    engine.erase_graph(CONCEPT_GRAPH)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND source = ?", [CONCEPT_GRAPH, "ivg233"]
        )
        conn.commit()
    finally:
        cur.close()


WHOLE_GRAPH = ("status_edges", "last_counts", "link_report")


def diff(baseline: dict, current: dict, tol: float = 1e-12, whole_graph: bool = True) -> list[str]:
    """Differences between two captures: score maps within `tol`, everything else exact.
    `whole_graph=False` skips WHOLE_GRAPH: those count every run in the repository, so
    they drift with whatever earlier E2E runs left behind (231's sync test keeps 509
    resources per run)."""
    out = []

    def scores(name, a, b):
        for k in sorted(set(a) | set(b)):
            if abs(a.get(k, 0.0) - b.get(k, 0.0)) > tol:
                out.append(f"{name}[{k}]: {a.get(k)} -> {b.get(k)}")

    for group in ("ppr_231", "ppr_233"):
        for seed in sorted(set(baseline[group]) | set(current[group])):
            scores(f"{group}.{seed}", baseline[group].get(seed, {}), current[group].get(seed, {}))
    scores("ppr_provenance", baseline["ppr_provenance"], current["ppr_provenance"])
    for key in WHOLE_GRAPH if whole_graph else ():
        if baseline[key] != current[key]:
            out.append(f"{key}: {json.dumps(baseline[key], sort_keys=True)} -> {json.dumps(current[key], sort_keys=True)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--out")
    group.add_argument("--compare", help="baseline file to compare a fresh capture with")
    ap.add_argument("--keep", action="store_true", help="leave the b410 run loaded")
    args = ap.parse_args(argv)

    from iris_vector_graph.engine import IRISGraphEngine

    from tests.e2e.fhir_conftest import connect

    conn = connect()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    handles = load(conn, engine)
    try:
        result = capture(engine, handles)
    finally:
        if not args.keep:
            teardown(conn, engine, handles)
    if args.compare:
        with open(args.compare) as fh:
            baseline = json.load(fh)
        # JSON round-trip so both sides have the same key and number types.
        found = diff(baseline, json.loads(json.dumps(result)))
        for line in found:
            print(line)
        print(f"{len(found)} difference(s) from {args.compare} (captured at {baseline['commit'][:7]})")
        return 1 if found else 0
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    result = {"commit": sha, "prefix": PREFIX, **result}
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
    print(
        f"wrote {args.out}: commit {sha[:7]}, ppr_231 {[len(v) for v in result['ppr_231'].values()]}, "
        f"ppr_233 {[len(v) for v in result['ppr_233'].values()]}, edges {result['status_edges']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
