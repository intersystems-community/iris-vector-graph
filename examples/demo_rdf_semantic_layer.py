"""
Demo: RDF Semantic Layer — Export, SHACL Validation, PROV-O Provenance

Demonstrates the three semantic layer capabilities added in v2.3.0:

  1. RDF Export  — full graph and filtered subgraph to Turtle/N-Triples/JSON-LD
  2. SHACL       — validate data quality with shape constraints
  3. PROV-O      — export temporal edge provenance as W3C PROV-O

Prerequisites:
    pip install 'iris-vector-graph[rdf]'
    scripts/test-container.sh up   # start IRIS Community Edition

Run:
    python examples/demo_rdf_semantic_layer.py
"""

import json
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect():
    try:
        import iris.dbapi as dbapi
        conn = dbapi.connect(
            hostname=os.environ.get("IRIS_HOST", "localhost"),
            port=int(os.environ.get("IVG_PORT", "21972")),
            namespace="USER",
            username=os.environ.get("IRIS_USER", "_SYSTEM"),
            password=os.environ.get("IRIS_PASSWORD", "SYS"),
        )
        return conn
    except Exception as e:
        print(f"[ERROR] Cannot connect to IRIS: {e}")
        print("        Start the container with: scripts/test-container.sh up")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Seed data — a small FHIR-like clinical graph
# ---------------------------------------------------------------------------

PATIENTS = [
    {"id": "Patient/p001", "name": "Alice Chen", "birthDate": "1980-03-15", "cohort": "HFrEF"},
    {"id": "Patient/p002", "name": "Bob Martinez", "birthDate": "1955-07-22", "cohort": "HFrEF"},
    {"id": "Patient/p003", "name": "Carol Kim",                              "cohort": "control"},  # missing birthDate
]

ENCOUNTERS = [
    {"id": "Encounter/e001", "type": "Admission",  "patient": "Patient/p001"},
    {"id": "Encounter/e002", "type": "Discharge",  "patient": "Patient/p001"},
    {"id": "Encounter/e003", "type": "Admission",  "patient": "Patient/p002"},
]

TIMESTAMPS = {
    ("Patient/p001", "admitted",    "Encounter/e001"): (1700000000, 1700086400),
    ("Patient/p001", "discharged",  "Encounter/e002"): (1700086400, 1700100000),
    ("Patient/p002", "admitted",    "Encounter/e003"): (1700200000, None),
}

PATIENT_SHAPE = """
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix fhir: <http://hl7.org/fhir/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

fhir:PatientShape a sh:NodeShape ;
    sh:targetClass fhir:Patient ;
    sh:property [
        sh:path     fhir:birthDate ;
        sh:minCount 1 ;
        sh:message  "Patient must have a birthDate" ;
    ] ;
    sh:property [
        sh:path     fhir:name ;
        sh:minCount 1 ;
        sh:message  "Patient must have a name" ;
    ] .
"""


def seed(engine):
    """Load sample clinical data into IVG."""
    print("\n--- Seeding clinical graph ---")
    for p in PATIENTS:
        props = {k: v for k, v in p.items() if k not in ("id",)}
        engine.create_node(
            p["id"],
            labels=["http://hl7.org/fhir/Patient"],
            properties={f"http://hl7.org/fhir/{k}": v for k, v in props.items()},
        )
    print(f"  {len(PATIENTS)} patients loaded")

    for enc in ENCOUNTERS:
        props = {k: v for k, v in enc.items() if k not in ("id", "patient")}
        engine.create_node(enc["id"], labels=["http://hl7.org/fhir/Encounter"], properties=props)
        engine.create_edge(enc["patient"], "http://hl7.org/fhir/encounter", enc["id"])
    print(f"  {len(ENCOUNTERS)} encounters loaded")

    for (src, pred, tgt), (ts_start, ts_end) in TIMESTAMPS.items():
        engine.create_edge_temporal(
            source=src,
            predicate=f"http://hl7.org/fhir/{pred}",
            target=tgt,
            timestamp=ts_start,
        )
    print(f"  {len(TIMESTAMPS)} temporal edges loaded")

    engine.register_namespace("fhir", "http://hl7.org/fhir/")
    engine.register_namespace("ex",   "http://example.org/")


# ---------------------------------------------------------------------------
# Demo 1: RDF Export
# ---------------------------------------------------------------------------

def demo_export(engine, outdir):
    print("\n--- Part 1: RDF Export ---")

    # 1a. Full graph as Turtle
    ttl_path = os.path.join(outdir, "clinical.ttl")
    result = engine.export_rdf(ttl_path)
    print(f"  Full export → {ttl_path}")
    print(f"    {result['triples']} triples, {result['nodes']} nodes, {result['edges']} edges")

    # 1b. Patients only as N-Triples
    nt_path = os.path.join(outdir, "patients.nt")
    result = engine.export_rdf(nt_path, format="nt", label_filter=["http://hl7.org/fhir/Patient"])
    print(f"  Patient subgraph → {nt_path}  ({result['triples']} triples)")

    # 1c. Cypher-based subgraph: HFrEF cohort only
    hfref_path = os.path.join(outdir, "hfref_cohort.ttl")
    result = engine.export_rdf_from_cypher(
        "MATCH (p:Patient) WHERE p.cohort = 'HFrEF' RETURN p.id AS id",
        hfref_path,
    )
    print(f"  HFrEF cohort export → {hfref_path}  ({result['triples']} triples)")

    # 1d. Verify Turtle is valid
    import rdflib
    g = rdflib.Graph()
    g.parse(ttl_path, format="turtle")
    print(f"  Verified: {len(g)} triples parse cleanly in rdflib")

    # Show first 5 triples
    print("  Sample triples:")
    for i, (s, p, o) in enumerate(g):
        print(f"    {str(s)[-30:]:30s}  {str(p)[-25:]:25s}  {str(o)[:40]}")
        if i >= 4:
            break

    return ttl_path


# ---------------------------------------------------------------------------
# Demo 2: SHACL Validation
# ---------------------------------------------------------------------------

def demo_shacl(engine, outdir):
    print("\n--- Part 2: SHACL Core Validation ---")

    # 2a. Validate all patients against birthDate + name shapes
    print("  Validating all patients against PatientShape...")
    report = engine.validate_shacl(PATIENT_SHAPE)
    print(f"  Conforms: {report.conforms}")
    print(f"  Violations: {len(report.violations)}")

    for v in report.violations:
        print(f"    [{v.severity}] {v.focus_node.split('/')[-1]:20s} → {v.message}")

    # 2b. Validate only Carol Kim (known to be missing birthDate)
    carol_id = "Patient/p003"
    print(f"\n  Targeted validation of {carol_id}:")
    report_targeted = engine.validate_shacl(PATIENT_SHAPE, node_ids=[carol_id])
    print(f"  Conforms: {report_targeted.conforms}")
    for v in report_targeted.violations:
        print(f"    [{v.severity}] {v.message}")

    # 2c. Show JSON-serializable report
    report_dict = report.to_dict()
    report_path = os.path.join(outdir, "validation_report.json")
    with open(report_path, "w") as f:
        json.dump(report_dict, f, indent=2)
    print(f"\n  Full report saved → {report_path}")


# ---------------------------------------------------------------------------
# Demo 3: PROV-O Temporal Provenance
# ---------------------------------------------------------------------------

def demo_prov(engine, outdir):
    print("\n--- Part 3: PROV-O Temporal Provenance ---")

    # 3a. Export all temporal edges as PROV-O
    prov_path = os.path.join(outdir, "provenance.ttl")
    result = engine.prov_export(prov_path)
    print(f"  PROV-O export → {prov_path}")
    print(f"    {result['activities']} activities, {result['entities']} entities")

    # 3b. Verify with rdflib
    import rdflib
    PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
    g = rdflib.Graph()
    g.parse(prov_path, format="turtle")

    activities = list(g.subjects(rdflib.RDF.type, PROV.Activity))
    entities   = list(g.subjects(rdflib.RDF.type, PROV.Entity))
    print(f"  Verified: {len(activities)} prov:Activity, {len(entities)} prov:Entity")

    # 3c. Show activity details
    print("  Activities:")
    for act in activities[:3]:
        started = next(g.objects(act, PROV.startedAtTime), "—")
        ended   = next(g.objects(act, PROV.endedAtTime), "—")
        used    = next(g.objects(act, PROV.used), "—")
        short_act = str(act).split("/")[-1][:40]
        print(f"    {short_act}")
        print(f"      started:  {started}")
        print(f"      ended:    {ended}")
        print(f"      used:     {str(used).split('/')[-1]}")

    # 3d. Time-windowed export (Alice's admission only)
    window_path = os.path.join(outdir, "alice_prov.ttl")
    engine.prov_export(window_path, ts_start=1699999999, ts_end=1700086401)
    g2 = rdflib.Graph()
    g2.parse(window_path, format="turtle")
    print(f"\n  Alice time-window export → {window_path}")
    print(f"    {len(list(g2.subjects(rdflib.RDF.type, PROV.Activity)))} activities in window")

    return prov_path


# ---------------------------------------------------------------------------
# Demo 4: End-to-End integration
# ---------------------------------------------------------------------------

def demo_integration(engine, ttl_path, outdir):
    print("\n--- Part 4: End-to-End Integration Pattern ---")
    print("  Simulate: export IVG graph → validate with SHACL → export provenance")

    # Round-trip: export then verify with rdflib
    import rdflib
    g = rdflib.Graph()
    g.parse(ttl_path, format="turtle")

    # Count by type
    by_type = {}
    for s, p, o in g.triples((None, rdflib.RDF.type, None)):
        type_name = str(o).split("/")[-1] or str(o).split("#")[-1]
        by_type[type_name] = by_type.get(type_name, 0) + 1

    print("  Graph composition by type:")
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {t:30s}: {count}")

    print(f"\n  → Graph is now portable to any SPARQL endpoint or rdflib pipeline.")
    print(f"  → SHACL validated before publication.")
    print(f"  → PROV-O provenance attached for audit trail.")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup(engine):
    cur = engine.conn.cursor()
    try:
        for p in PATIENTS:
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [p["id"]])
        for enc in ENCOUNTERS:
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [enc["id"]])
        cur.execute(
            "DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'Patient/%' OR s LIKE 'Encounter/%'"
        )
        cur.execute(
            "DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'Patient/%' OR s LIKE 'Encounter/%'"
        )
        cur.execute(
            "DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'Patient/%' OR s LIKE 'Encounter/%'"
        )
        engine.conn.commit()
    except Exception:
        pass
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  iris-vector-graph — RDF Semantic Layer Demo (v2.3.0)")
    print("=" * 60)

    # Check rdflib available
    try:
        import rdflib
        print(f"\n  rdflib {rdflib.__version__} detected")
    except ImportError:
        print("\n[ERROR] rdflib not installed.")
        print("        Run: pip install 'iris-vector-graph[rdf]'")
        sys.exit(1)

    conn = connect()
    print(f"\n  Connected to IRIS")

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine.initialize_schema()

    with tempfile.TemporaryDirectory() as outdir:
        try:
            seed(engine)
            ttl_path = demo_export(engine, outdir)
            demo_shacl(engine, outdir)
            demo_prov(engine, outdir)
            demo_integration(engine, ttl_path, outdir)
        finally:
            cleanup(engine)

    conn.close()
    print("\n" + "=" * 60)
    print("  Demo complete.")
    print("  See docs/SEMANTIC_LAYER.md for the full guide.")
    print("=" * 60)


if __name__ == "__main__":
    main()
