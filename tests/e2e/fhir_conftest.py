"""Spec 231 E2E fixtures: the scratch FHIR namespace IVGFHIR in ivg-iris-enterprise.

The FHIR graph classes run in the FHIR namespace, next to the repository tables, so
the whole Graph.KG.* package is compiled into IVGFHIR and the IVG schema created
there. The enterprise image is NoPWS: resources are loaded through
IVGTest.FHIRLoad.Dispatch, the entry point the REST handler calls, not over HTTP.

Install the namespace once (from HSLIB, `iris session IRIS -U HSLIB`):

    set tSC=##class(HS.Util.Installer.Foundation).Install("IVGFHIR")
    zn "IVGFHIR"
    do ##class(HS.FHIRServer.Installer).InstallNamespace()
    do ##class(HS.FHIRServer.Installer).InstallInstance("/csp/healthshare/ivgfhir/fhir/r4","HS.FHIRServer.Storage.JsonAdvSQL.InteractionsStrategy","hl7.fhir.r4.core@4.0.1")
"""

from __future__ import annotations

import glob
import json
import os
import uuid

import pytest

NAMESPACE = "IVGFHIR"
ENDPOINT = "/csp/healthshare/ivgfhir/fhir/r4"
GRAPH = "fhir:IVGFHIR:X0001"
PORT = int(os.environ.get("IVG_PORT", "31972"))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES = os.path.join(_ROOT, "tests", "e2e", "fixtures", "fhir")


def connect():
    import iris

    return iris.connect(
        hostname="localhost", port=PORT, namespace=NAMESPACE, username="_SYSTEM", password="SYS"
    )


def deploy(conn) -> list:
    """Compile every Graph.KG.* class and the test loader into IVGFHIR over TCP, the
    way `scripts/enterprise-container.sh tcp-deploy` does for USER. Returns errors."""
    import iris

    irisobj = iris.createIRIS(conn)
    files = [(f, "iris_src/src") for f in glob.glob(os.path.join(_ROOT, "iris_src/src/**/*.cls"), recursive=True)]
    files += [(f, "fixtures") for f in glob.glob(os.path.join(_FIXTURES, "**/*.cls"), recursive=True)]
    errors = []
    for path, tag in sorted(files):
        base = os.path.join(_ROOT, "iris_src/src") if tag == "iris_src/src" else _FIXTURES
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        dest = f"/tmp/tcpsrc-{NAMESPACE}/{rel}"
        irisobj.classMethodValue("%File", "CreateDirectoryChain", os.path.dirname(dest))
        stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
        stream.invokeVoid("LinkToFile", dest)
        with open(path) as fh:
            for line in fh.read().split("\n"):
                stream.invokeVoid("WriteLine", line)
        stream.invokeVoid("%Save")
        status = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
        if not irisobj.classMethodValue("%SYSTEM.Status", "IsOK", status):
            errors.append(
                f"{rel}: {irisobj.classMethodValue('%SYSTEM.Status', 'GetOneErrorText', status)}"
            )
    return errors


class FhirLoader:
    """Loads resources through IVGTest.FHIRLoad.Dispatch. Ids carry a per-run prefix
    so reruns against the same repository do not collide."""

    def __init__(self, conn):
        import iris

        self.irisobj = iris.createIRIS(conn)
        self.prefix = "t" + uuid.uuid4().hex[:8]

    def id(self, name: str) -> str:
        return f"{self.prefix}-{name}"

    def dispatch(self, method: str, path: str, body=None) -> dict:
        raw = self.irisobj.classMethodValue(
            "IVGTest.FHIRLoad", "Dispatch", ENDPOINT, method, path, json.dumps(body) if body else ""
        )
        return json.loads(raw)

    def put(self, resource: dict) -> dict:
        out = self.dispatch("PUT", f"/{resource['resourceType']}/{resource['id']}", resource)
        assert str(out["status"]).startswith("20"), out
        return out

    def delete(self, rtype: str, rid: str) -> dict:
        out = self.dispatch("DELETE", f"/{rtype}/{rid}")
        assert str(out["status"]).startswith("20"), out
        return out


@pytest.fixture(scope="session")
def fhir_conn():
    try:
        conn = connect()
    except Exception as exc:  # pragma: no cover - environment
        pytest.skip(f"IVGFHIR on port {PORT} not reachable ({exc}); see tests/e2e/fhir_conftest.py")
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM HS_FHIRServer.RepoInstance")
    except Exception:  # pragma: no cover - environment
        pytest.skip("IVGFHIR has no FHIR server; install it per tests/e2e/fhir_conftest.py")
    errors = deploy(conn)
    assert not errors, "compile errors in IVGFHIR:\n" + "\n".join(errors)
    from iris_vector_graph.engine import IRISGraphEngine

    IRISGraphEngine(conn, embedding_dimension=4).initialize_schema(auto_deploy_objectscript=False)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def fhir_engine(fhir_conn):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(fhir_conn, embedding_dimension=4)


@pytest.fixture
def fhir_loader(fhir_conn):
    return FhirLoader(fhir_conn)
