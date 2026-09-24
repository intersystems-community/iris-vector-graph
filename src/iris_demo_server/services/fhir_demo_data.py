"""Cohort and concept graph for the FHIR graph demo, and the script that loads them.

`build_cohort()` is pure and deterministic: about 200 Patients with Encounters, ICD-10
Conditions and LOINC labs, routed to a small set of Practitioners so that PPR has
something to find (the endocrinologist sees the diabetic patients, and so on).

The concept graph `concepts:demo` is a small UMLS-shaped hierarchy. `code_crosswalk`
maps each ICD-10 code to its concept as `exact` and each lab code as `related`, so
the page can show the difference a relation filter makes.

Load it into the spec 231 FHIR namespace (see `tests/e2e/fhir_conftest.py` for the
one-time install):

    PYTHONPATH=src:. python -m iris_demo_server.services.fhir_demo_data [--deploy-ivg]

Resources are PUT with fixed IDs, so rerunning converges instead of duplicating.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

COHORT_SIZE = 200
COHORT_SEED = 231
CONCEPT_GRAPH = "concepts:demo"
CROSSWALK_SOURCE = "demo"
LIVE_PREFIX = "demo-live-"

ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
LOINC = "http://loinc.org"

DIABETES = "C0011849"
TYPE2_DM = "C0011860"
DIABETIC_NEPHROPATHY = "C0011881"
HYPERTENSION = "C0020538"
CKD = "C0022661"
CHF = "C0018802"
ASTHMA = "C0004096"
CARDIOVASCULAR = "C0007222"

CONCEPTS: Dict[str, str] = {
    DIABETES: "Diabetes mellitus",
    TYPE2_DM: "Type 2 diabetes mellitus",
    DIABETIC_NEPHROPATHY: "Diabetic nephropathy",
    HYPERTENSION: "Hypertensive disease",
    CKD: "Chronic kidney disease",
    CHF: "Congestive heart failure",
    ASTHMA: "Asthma",
    CARDIOVASCULAR: "Cardiovascular diseases",
}

#: (broader, narrower). The two grouping concepts carry no codes of their own, so
#: only expansion reaches their patients.
NARROWER: List[Tuple[str, str]] = [
    (DIABETES, TYPE2_DM),
    (TYPE2_DM, DIABETIC_NEPHROPATHY),
    (CKD, DIABETIC_NEPHROPATHY),
    (CARDIOVASCULAR, HYPERTENSION),
    (CARDIOVASCULAR, CHF),
]

ICD_CODES: Dict[str, Tuple[str, str]] = {
    "E11.9": ("Type 2 diabetes mellitus without complications", TYPE2_DM),
    "E11.65": ("Type 2 diabetes mellitus with hyperglycemia", TYPE2_DM),
    "E11.21": ("Type 2 diabetes mellitus with diabetic nephropathy", DIABETIC_NEPHROPATHY),
    "I10": ("Essential (primary) hypertension", HYPERTENSION),
    "N18.3": ("Chronic kidney disease, stage 3", CKD),
    "N18.4": ("Chronic kidney disease, stage 4", CKD),
    "I50.9": ("Heart failure, unspecified", CHF),
    "I50.22": ("Chronic systolic heart failure", CHF),
    "J45.909": ("Unspecified asthma, uncomplicated", ASTHMA),
    "J45.40": ("Moderate persistent asthma, uncomplicated", ASTHMA),
}

LAB_CODES: Dict[str, Tuple[str, str, str]] = {
    "4548-4": ("Hemoglobin A1c", "%", TYPE2_DM),
    "33914-3": ("eGFR (MDRD)", "mL/min/1.73m2", CKD),
    "30934-4": ("BNP", "pg/mL", CHF),
}

#: (system, code, concept, relation)
CROSSWALK: List[Tuple[str, str, str, str]] = [
    (ICD10, code, concept, "exact") for code, (_, concept) in ICD_CODES.items()
] + [(LOINC, code, concept, "related") for code, (_, _, concept) in LAB_CODES.items()]

#: The first four are specialists, routed by diagnosis; the rest are GPs.
PRACTITIONERS: List[Dict[str, str]] = [
    {"id": "demo-dr01", "given": "Hana", "family": "Ito", "specialty": "Endocrinology"},
    {"id": "demo-dr02", "given": "Chidi", "family": "Okafor", "specialty": "Nephrology"},
    {"id": "demo-dr03", "given": "Maja", "family": "Lindqvist", "specialty": "Cardiology"},
    {"id": "demo-dr04", "given": "Tomas", "family": "Reyes", "specialty": "Pulmonology"},
    {"id": "demo-dr05", "given": "Priya", "family": "Nair", "specialty": "Family Medicine"},
    {"id": "demo-dr06", "given": "Owen", "family": "Walsh", "specialty": "Family Medicine"},
    {"id": "demo-dr07", "given": "Lena", "family": "Fischer", "specialty": "Family Medicine"},
    {"id": "demo-dr08", "given": "Samuel", "family": "Adeyemi", "specialty": "Family Medicine"},
]
_SPECIALIST = {
    TYPE2_DM: "demo-dr01",
    DIABETIC_NEPHROPATHY: "demo-dr02",
    CKD: "demo-dr02",
    CHF: "demo-dr03",
    ASTHMA: "demo-dr04",
}
_GPS = [p["id"] for p in PRACTITIONERS if p["specialty"] == "Family Medicine"]
ORGANIZATIONS = [("demo-org1", "Riverside Health"), ("demo-org2", "Northgate Clinic")]

_GIVEN = [
    "Ada", "Ben", "Chloe", "Dev", "Elif", "Farid", "Grace", "Hugo", "Iris", "Jonas",
    "Kira", "Luis", "Mei", "Nils", "Olga", "Pavel", "Quinn", "Rosa", "Sven", "Tara",
]
_FAMILY = [
    "Abbott", "Barros", "Chen", "Dubois", "Evans", "Fontaine", "Garcia", "Haddad",
    "Ivanova", "Jensen", "Kowalski", "Larsen", "Moreau", "Novak", "Osei", "Petrov",
]


def _ref(rtype: str, rid: str) -> Dict[str, str]:
    return {"reference": f"{rtype}/{rid}"}


def display_name(resource: Dict[str, Any]) -> str:
    names = resource.get("name") or []
    if not names:
        return ""
    if isinstance(names, str):
        return names  # Organization.name
    given = (names[0].get("given") or [""])[0]
    return f"{given} {names[0].get('family', '')}".strip()


def _encounter(eid: str, pid: str, practitioner: str, org: str, day: str) -> Dict[str, Any]:
    return {
        "resourceType": "Encounter",
        "id": eid,
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        "subject": _ref("Patient", pid),
        "participant": [{"individual": _ref("Practitioner", practitioner)}],
        "serviceProvider": _ref("Organization", org),
        "period": {"start": day},
    }


def condition(cid: str, pid: str, code: str, eid: Optional[str], day: str) -> Dict[str, Any]:
    display, _ = ICD_CODES[code]
    out: Dict[str, Any] = {
        "resourceType": "Condition",
        "id": cid,
        "clinicalStatus": {
            "coding": [
                {"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}
            ]
        },
        "code": {"coding": [{"system": ICD10, "code": code, "display": display}]},
        "subject": _ref("Patient", pid),
        "onsetDateTime": day,
    }
    if eid:
        out["encounter"] = _ref("Encounter", eid)
    return out


def _lab(oid: str, pid: str, eid: str, practitioner: str, code: str, value: float, day: str):
    display, unit, _ = LAB_CODES[code]
    return {
        "resourceType": "Observation",
        "id": oid,
        "status": "final",
        "code": {"coding": [{"system": LOINC, "code": code, "display": display}]},
        "subject": _ref("Patient", pid),
        "encounter": _ref("Encounter", eid),
        "performer": [_ref("Practitioner", practitioner)],
        "effectiveDateTime": day,
        "valueQuantity": {"value": value, "unit": unit},
    }


def _diagnoses(rng: random.Random) -> List[str]:
    """ICD-10 codes for one patient, with the comorbidities a clinician would expect."""
    codes = []
    diabetic = rng.random() < 0.25
    if diabetic:
        codes.append(rng.choice(["E11.9", "E11.65"]))
        if rng.random() < 0.3:
            codes.append("E11.21")
    hypertensive = rng.random() < (0.5 if diabetic else 0.25)
    if hypertensive:
        codes.append("I10")
    if rng.random() < (0.25 if diabetic else 0.06):
        codes.append(rng.choice(["N18.3", "N18.4"]))
    if rng.random() < (0.2 if hypertensive else 0.05):
        codes.append(rng.choice(["I50.9", "I50.22"]))
    if rng.random() < 0.1:
        codes.append(rng.choice(["J45.909", "J45.40"]))
    return codes


def build_cohort(size: int = COHORT_SIZE, seed: int = COHORT_SEED) -> List[Dict[str, Any]]:
    """Every demo resource, each after the resources it references."""
    rng = random.Random(seed)
    out: List[Dict[str, Any]] = [
        {"resourceType": "Organization", "id": oid, "name": name} for oid, name in ORGANIZATIONS
    ]
    for p in PRACTITIONERS:
        out.append(
            {
                "resourceType": "Practitioner",
                "id": p["id"],
                "name": [{"family": p["family"], "given": [p["given"]], "prefix": ["Dr"]}],
            }
        )

    for n in range(1, size + 1):
        pid = f"demo-p{n:03d}"
        gp = _GPS[n % len(_GPS)]
        org = ORGANIZATIONS[n % len(ORGANIZATIONS)][0]
        year = 1940 + rng.randrange(60)
        day = f"2026-0{1 + rng.randrange(8)}-{1 + rng.randrange(28):02d}"
        out.append(
            {
                "resourceType": "Patient",
                "id": pid,
                "name": [{"family": rng.choice(_FAMILY), "given": [rng.choice(_GIVEN)]}],
                "gender": rng.choice(["female", "male"]),
                "birthDate": f"{year}-{1 + rng.randrange(12):02d}-{1 + rng.randrange(28):02d}",
                "generalPractitioner": [_ref("Practitioner", gp)],
                "managingOrganization": _ref("Organization", org),
            }
        )
        visit = f"{pid}-e0"
        out.append(_encounter(visit, pid, gp, org, day))

        codes = _diagnoses(rng)
        concepts = {ICD_CODES[c][1] for c in codes}
        seen_by: Dict[str, str] = {}
        for code in codes:
            specialist = _SPECIALIST.get(ICD_CODES[code][1])
            if specialist and specialist not in seen_by:
                seen_by[specialist] = f"{pid}-e{len(seen_by) + 1}"
                out.append(_encounter(seen_by[specialist], pid, specialist, org, day))
        for i, code in enumerate(codes):
            specialist = _SPECIALIST.get(ICD_CODES[code][1])
            out.append(condition(f"{pid}-c{i}", pid, code, seen_by.get(specialist, visit), day))

        # Labs: ordered for the condition, plus some screening of everyone else.
        labs = []
        if TYPE2_DM in concepts or DIABETIC_NEPHROPATHY in concepts or rng.random() < 0.1:
            labs.append(("4548-4", round(rng.uniform(7.0, 10.5) if TYPE2_DM in concepts else rng.uniform(4.8, 5.9), 1)))
        if CKD in concepts or DIABETIC_NEPHROPATHY in concepts or rng.random() < 0.1:
            labs.append(("33914-3", round(rng.uniform(18, 55) if CKD in concepts else rng.uniform(70, 110))))
        if CHF in concepts or rng.random() < 0.05:
            labs.append(("30934-4", round(rng.uniform(250, 900) if CHF in concepts else rng.uniform(20, 90))))
        for j, (code, value) in enumerate(labs):
            out.append(_lab(f"{pid}-o{j}", pid, visit, gp, code, value, day))
    return out


def patients(cohort: Optional[List[Dict[str, Any]]] = None) -> List[Tuple[str, str]]:
    """`(key, display name)` for every demo patient."""
    return [
        (f"Patient/{r['id']}", display_name(r))
        for r in (cohort if cohort is not None else build_cohort())
        if r["resourceType"] == "Patient"
    ]


def live_condition_id(patient_key: str, code: str) -> str:
    """One live condition per (patient, code): adding it twice is a no-op PUT."""
    pid = patient_key.split("/", 1)[1]
    return f"{LIVE_PREFIX}{pid}-{code.replace('.', '')}"


# ------------------------------------------------------------------- loading

LOADER_CLASS = "IVGDemo.FHIRLoad"
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEMO_CLS = os.path.join(os.path.dirname(_HERE), "objectscript")
_IVG_CLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "iris_src", "src")


def connection_settings() -> Dict[str, Any]:
    return {
        "hostname": os.getenv("IVG_FHIR_HOST", "localhost"),
        "port": int(os.getenv("IVG_FHIR_PORT", "31972")),
        "namespace": os.getenv("IVG_FHIR_NAMESPACE", "IVGFHIR"),
        "username": os.getenv("IVG_FHIR_USER", "_SYSTEM"),
        "password": os.getenv("IVG_FHIR_PASSWORD", "SYS"),
    }


def endpoint() -> str:
    ns = connection_settings()["namespace"].lower()
    return os.getenv("IVG_FHIR_ENDPOINT", f"/csp/healthshare/{ns}/fhir/r4")


def connect():
    import iris

    return iris.connect(**connection_settings())


def make_engine(conn):
    """An engine for the FHIR namespace. The engine defaults to USER and warns when
    the connection is elsewhere, so the demo names its own namespace."""
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(conn, namespace=connection_settings()["namespace"])


def load_classes(conn, root: str) -> List[str]:
    """Compile every .cls under `root` into the connected namespace over TCP. The
    enterprise image routes irispython to a different database than TCP, so the
    source is written server-side and loaded from there. Returns error texts."""
    import iris

    irisobj = iris.createIRIS(conn)
    ns = connection_settings()["namespace"]
    errors = []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.cls"), recursive=True)):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        dest = f"/tmp/ivgdemo-{ns}/{rel}"
        irisobj.classMethodValue("%File", "CreateDirectoryChain", os.path.dirname(dest))
        stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
        stream.invokeVoid("LinkToFile", dest)
        with open(path) as fh:
            for line in fh.read().split("\n"):
                stream.invokeVoid("WriteLine", line)
        stream.invokeVoid("%Save")
        status = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
        if not irisobj.classMethodValue("%SYSTEM.Status", "IsOK", status):
            errors.append(f"{rel}: {irisobj.classMethodValue('%SYSTEM.Status', 'GetOneErrorText', status)}")
    return errors


def make_dispatch(conn) -> Callable[[str, str, Optional[Dict[str, Any]]], Dict[str, Any]]:
    """`dispatch(method, path, body)` through the FHIR service in-process: the
    enterprise image has no web server, and this is the call the REST handler makes."""
    import iris

    irisobj = iris.createIRIS(conn)
    ep = endpoint()

    def dispatch(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = irisobj.classMethodValue(
            LOADER_CLASS, "Dispatch", ep, method, path, json.dumps(body) if body else ""
        )
        return json.loads(raw)

    return dispatch


def put(dispatch, resource: Dict[str, Any]) -> Dict[str, Any]:
    out = dispatch("PUT", f"/{resource['resourceType']}/{resource['id']}", resource)
    if not str(out.get("status", "")).startswith("20"):
        raise RuntimeError(f"PUT {resource['resourceType']}/{resource['id']} failed: {out}")
    return out


def load_concepts(engine) -> None:
    for cid, name in CONCEPTS.items():
        engine.create_node(cid, labels=["Concept"], properties={"name": name}, graph=CONCEPT_GRAPH)
    for parent, child in NARROWER:
        engine.create_edge(parent, "narrower", child, graph=CONCEPT_GRAPH)
    for system, code, concept, relation in CROSSWALK:
        engine.code_crosswalk_add(
            system, code, concept, target_graph=CONCEPT_GRAPH, relation=relation, source=CROSSWALK_SOURCE
        )


def remove_live_conditions(conn, dispatch, package: str) -> int:
    """Delete what the page's live panel added, so a reseed starts from the cohort."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT ResourceId FROM HSFHIR_{package}_R.Rsrc "
        "WHERE ResourceType = 'Condition' AND Deleted = 0 AND ResourceId %STARTSWITH ?",
        [LIVE_PREFIX],
    )
    ids = [row[0] for row in cur.fetchall()]
    for rid in ids:
        dispatch("DELETE", f"/Condition/{rid}")
    return len(ids)


def sync_until_done(engine, graph: str, attempts: int = 50) -> Dict[str, Any]:
    reply: Dict[str, Any] = {}
    for _ in range(attempts):
        reply = engine.fhir_graph_sync(graph)
        if reply.get("status") != "busy":
            status = engine.fhir_graph_status(graph)
            if not status.get("pending"):
                return reply
        time.sleep(0.2)
    return reply


def seed(conn, engine, *, deploy_ivg: bool = False, log: Callable[[str], None] = print) -> Dict[str, Any]:
    if deploy_ivg:
        errors = load_classes(conn, _IVG_CLS)
        if errors:
            raise RuntimeError("Graph.KG compile errors:\n" + "\n".join(errors))
        engine.initialize_schema(auto_deploy_objectscript=False)
    errors = load_classes(conn, _DEMO_CLS)
    if errors:
        raise RuntimeError(f"{LOADER_CLASS} compile errors:\n" + "\n".join(errors))

    log(f"registering {endpoint()} and loading {COHORT_SIZE} patients through the FHIR service")
    reg = engine.fhir_graph_register(endpoint=endpoint())
    graph = reg["graph_id"]
    dispatch = make_dispatch(conn)
    removed = remove_live_conditions(conn, dispatch, graph.rsplit(":", 1)[1])

    cohort = build_cohort()
    started = time.perf_counter()
    for n, r in enumerate(cohort, 1):
        put(dispatch, r)
        if n % 100 == 0:
            log(f"PUT {n}/{len(cohort)} ({time.perf_counter() - started:.0f} s)")
    log(f"PUT {len(cohort)} resources in {time.perf_counter() - started:.1f} s ({removed} live conditions removed)")

    load_concepts(engine)
    started = time.perf_counter()
    sync = sync_until_done(engine, graph)
    log(f"sync {graph}: {sync} in {time.perf_counter() - started:.1f} s")
    return {"graph": graph, "resources": len(cohort), "removed_live": removed, "sync": sync}


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--deploy-ivg",
        action="store_true",
        help="also compile iris_src/src (Graph.KG.*) into the namespace and create the IVG schema",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    conn = connect()
    try:
        out = seed(conn, make_engine(conn), deploy_ivg=args.deploy_ivg, log=lambda m: print(m, flush=True))
    finally:
        conn.close()
    print(json.dumps({k: v for k, v in out.items() if k != "sync"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
