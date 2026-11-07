"""Spec 262 US3 (T026, T068 B2): a FHIR repository becomes a graph in a namespace whose IVG Schema is `IVGALT`.

The FHIR graph classes run next to the repository tables, so the namespace must be FHIR-capable: made once with the
recipe in CONTRIBUTING.md ("The FHIR namespace (IVGFHIR)"), under the name `IVG_NS_IVG262FHA` (no default: the file
skips without it). Setup with `schema_prefix="IVGALT"` deploys the classes rewritten for IVGALT; the test loader
`IVGTest.FHIRLoad` is compiled beside them. The repository is emptied first. Then a Patient, a Practitioner and an
Observation that references both are loaded, the repository is registered and rebuilt, and the graph is read back:
its nodes, labels and reference edges are in IVGALT's tables (worked by hand from the three resources), no table
exists in `Graph_KG`, and a Cypher read of the graph names nothing outside IVGALT.
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG262FHA", "")
if not NS:
    pytest.skip("set IVG_NS_IVG262FHA to a FHIR-capable scratch namespace (CONTRIBUTING.md)", allow_module_level=True)
SCHEMA = "IVGALT"
ENDPOINT = f"/csp/healthshare/{NS.lower()}/fhir/r4"
GRAPH = f"fhir:{NS}:X0001"
LOADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fhir", "IVGTest", "FHIRLoad.cls")


def _load_class(conn, path: str) -> None:
    import iris

    irisobj = iris.createIRIS(conn)
    dest = f"/tmp/ivg262fha/{os.path.basename(path)}"
    irisobj.classMethodValue("%File", "CreateDirectoryChain", os.path.dirname(dest))
    stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", dest)
    with open(path) as fh:
        for line in fh.read().split("\n"):
            stream.invokeVoid("WriteLine", line)
    stream.invokeVoid("%Save")
    status = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
    assert irisobj.classMethodValue("%SYSTEM.Status", "IsOK", status), irisobj.classMethodValue(
        "%SYSTEM.Status", "GetOneErrorText", status)


@pytest.fixture(scope="module")
def fha():
    import json

    import iris

    from iris_vector_graph import IRISGraphEngine

    try:
        conn = ns262.connect(NS)
    except Exception as exc:  # no such namespace on this container
        pytest.skip(f"{NS} is not reachable ({exc}); make it with the recipe in CONTRIBUTING.md")
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM HS_FHIRServer.RepoInstance WHERE cspUrl = ?", [ENDPOINT])
        has_endpoint = cur.fetchone()[0]
    except Exception as exc:
        if "<-30>" not in str(exc):
            raise
        has_endpoint = 0
    finally:
        cur.close()
    if not has_endpoint:
        conn.close()
        pytest.skip(f"{NS} has no FHIR endpoint at {ENDPOINT}: make it with the recipe in CONTRIBUTING.md")
    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    status = engine.initialize_schema()
    _load_class(conn, LOADER)
    irisobj = iris.createIRIS(conn)
    irisobj.classMethodVoid("HS.FHIRServer.Installer", "Reset", "", ENDPOINT)
    p = "a" + uuid.uuid4().hex[:8]

    def put(resource):
        out = json.loads(irisobj.classMethodValue("IVGTest.FHIRLoad", "Dispatch", ENDPOINT, "PUT",
                                                  f"/{resource['resourceType']}/{resource['id']}",
                                                  json.dumps(resource)))
        assert str(out["status"]).startswith("20"), out

    put({"resourceType": "Patient", "id": f"{p}-p1"})
    put({"resourceType": "Practitioner", "id": f"{p}-dr1"})
    put({"resourceType": "Observation", "id": f"{p}-o1", "status": "final",
         "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
         "subject": {"reference": f"Patient/{p}-p1"}, "performer": [{"reference": f"Practitioner/{p}-dr1"}]})
    registered = engine.fhir_graph_register(denylist=[])
    report = engine.fhir_graph_rebuild(GRAPH)
    keys = {"p1": f"Patient/{p}-p1", "dr1": f"Practitioner/{p}-dr1", "o1": f"Observation/{p}-o1"}
    yield {"conn": conn, "engine": engine, "status": status, "registered": registered, "report": report,
           "keys": keys}
    try:
        engine.erase_graph(GRAPH)
    finally:
        conn.close()


def test_setup_and_the_build_report_no_error(fha):
    assert [w for w in fha["status"]["warnings"] if "deploy error" in w] == []
    assert fha["status"]["objectscript_deployed"]
    assert fha["report"].get("status") != "error" and not fha["report"].get("error"), fha["report"]


def test_no_graph_kg_table_exists(fha):
    rows = ns262.rows(fha["conn"], "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'")
    assert rows[0][0] == 0


def test_the_graph_registry_is_in_ivgalt(fha):
    rows = ns262.rows(fha["conn"], f"SELECT graph_id FROM {SCHEMA}.fhir_graphs")
    assert (GRAPH,) in rows


def test_each_resource_is_a_node_with_its_type_as_label(fha):
    conn, k = fha["conn"], fha["keys"]
    for name, rtype in (("p1", "Patient"), ("dr1", "Practitioner"), ("o1", "Observation")):
        assert ns262.rows(conn, f"SELECT 1 FROM {SCHEMA}.nodes WHERE graph_id = ? AND node_id = ?", [GRAPH, k[name]])
        assert ns262.rows(conn, f"SELECT label FROM {SCHEMA}.rdf_labels WHERE graph_id = ? AND s = ?",
                          [GRAPH, k[name]]) == [(rtype,)]


def test_the_references_are_edges_in_ivgalt(fha):
    conn, k = fha["conn"], fha["keys"]
    edges = set(ns262.rows(conn, f"SELECT p, o_id FROM {SCHEMA}.rdf_edges WHERE graph_id = ? AND s = ? "
                                 "AND p <> 'in_patient_compartment'", [GRAPH, k["o1"]]))
    assert {("subject", k["p1"]), ("performer", k["dr1"])} <= edges, edges


def test_a_cypher_read_of_the_graph_names_only_ivgalt(fha):
    e, k = fha["engine"], fha["keys"]
    r = e.execute_cypher(f"USE GRAPH `{GRAPH}` MATCH (o:Observation)-[:subject]->(p:Patient) RETURN p.node_id",
                         raise_on_error=True)
    assert ns262.names_outside(r.sql or "", SCHEMA) == [], r.sql
    assert [row[0] for row in r.rows] == [k["p1"]]
