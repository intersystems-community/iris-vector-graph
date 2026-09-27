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
import re
import uuid

import pytest

NAMESPACE = "IVGFHIR"
ENDPOINT = "/csp/healthshare/ivgfhir/fhir/r4"
GRAPH = "fhir:IVGFHIR:X0001"
PORT = int(os.environ.get("IVG_PORT", "31972"))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES = os.path.join(_ROOT, "tests", "e2e", "fixtures", "fhir")


def prefix_resources(resources: list[dict], prefix: str) -> list[dict]:
    """Deep copies with every id set to f"{prefix}-{id}", and every `Type/id` substring
    naming one of `resources` rewritten to `Type/{prefix}-id` where it is followed by
    `"`, `|`, `/` or `#`. Longest ids go first, so no id rewrites inside a longer one.
    Substrings naming a key outside `resources` are left alone."""
    keys = {(r["resourceType"], r["id"]) for r in resources}
    if not keys:
        return []
    alt = "|".join(re.escape(f"{t}/{i}") for t, i in sorted(keys, key=lambda k: (-len(k[1]), k)))
    pattern = re.compile(rf"({alt})(?=[\"|/#])")
    out = []
    for r in resources:
        raw = pattern.sub(lambda m: m.group(1).replace("/", f"/{prefix}-", 1), json.dumps(r))
        copied = json.loads(raw)
        copied["id"] = f"{prefix}-{r['id']}"
        out.append(copied)
    return out


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

    def knowledge(self, stage: str) -> list:
        """Spec 232 knowledge fixture: `stage1`, `stage2`, `stage3` are directories of
        resources to PUT; `stage4` names keys to delete. `@P@` becomes the prefix, in
        ids and urls alike. Returns the keys touched."""
        base = os.path.join(_FIXTURES, "knowledge")
        if os.path.isdir(os.path.join(base, stage)):
            keys = []
            for path in sorted(glob.glob(os.path.join(base, stage, "*.json"))):
                with open(path) as fh:
                    resource = json.loads(fh.read().replace("@P@", self.prefix))
                self.put(resource)
                keys.append(f"{resource['resourceType']}/{resource['id']}")
            return keys
        with open(os.path.join(base, f"{stage}.json")) as fh:
            spec = json.loads(fh.read().replace("@P@", self.prefix))
        for key in spec["delete"]:
            self.delete(*key.split("/", 1))
        return spec["delete"]

    def url(self, path: str) -> str:
        """A fixture url, e.g. url('Library/L') -> http://ex.org/ivg232/<prefix>/Library/L."""
        return f"http://ex.org/ivg232/{self.prefix}/{path}"


@pytest.fixture(scope="session")
def _fhir_session():
    """(conn, None) when IVGFHIR is up and deployed, else (None, reason). Shared by
    `fhir_conn` (skips) and `fhir_conn_required` (fails), so both use one
    connection and one deploy."""
    try:
        conn = connect()
    except Exception as exc:  # pragma: no cover - environment
        yield None, f"IVGFHIR on port {PORT} not reachable ({exc}); see tests/e2e/fhir_conftest.py"
        return
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM HS_FHIRServer.RepoInstance")
    except Exception:  # pragma: no cover - environment
        conn.close()
        yield None, "IVGFHIR has no FHIR server; install it per tests/e2e/fhir_conftest.py"
        return
    errors = deploy(conn)
    assert not errors, "compile errors in IVGFHIR:\n" + "\n".join(errors)
    from iris_vector_graph.engine import IRISGraphEngine

    IRISGraphEngine(conn, embedding_dimension=4).initialize_schema(auto_deploy_objectscript=False)
    yield conn, None
    conn.close()


@pytest.fixture(scope="session")
def fhir_conn(_fhir_session):
    conn, reason = _fhir_session
    if conn is None:  # pragma: no cover - environment
        pytest.skip(reason)
    return conn


@pytest.fixture(scope="session")
def fhir_conn_required(_fhir_session):
    """`fhir_conn`, but a missing IVGFHIR fails the run unless SKIP_IRIS_TESTS is
    set (constitution VIII, Gate 1)."""
    conn, reason = _fhir_session
    if conn is None:  # pragma: no cover - environment
        if os.environ.get("SKIP_IRIS_TESTS", "false") == "false":
            pytest.fail(reason)
        pytest.skip(reason)
    return conn


@pytest.fixture(scope="session")
def fhir_engine(fhir_conn):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(fhir_conn, embedding_dimension=4)


@pytest.fixture
def fhir_loader(fhir_conn):
    return FhirLoader(fhir_conn)


# ------------------------------------------------------------------ spec 233


def _chunks(items, n=200):
    items = sorted(items)
    for i in range(0, len(items), n):
        yield items[i : i + n]


def graph_edges(conn, keys) -> list[tuple[str, str, str]]:
    """(s, p, o_id) in GRAPH where s is one of `keys`."""
    out = []
    cur = conn.cursor()
    try:
        for chunk in _chunks(keys):
            marks = ",".join("?" * len(chunk))
            cur.execute(
                f"SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s IN ({marks})",
                [GRAPH, *chunk],
            )
            out.extend(tuple(row) for row in cur.fetchall())
    finally:
        cur.close()
    return out


def neighbourhood(conn, key: str, hops: int) -> set[str]:
    """Every node within `hops` of `key`, walking GRAPH's rdf_edges both ways. The
    seed is excluded. `kg_SUBGRAPH` and friends are not graph-scoped (research R7)."""
    seen, frontier = {key}, {key}
    cur = conn.cursor()
    try:
        for _ in range(hops):
            nxt = set()
            for chunk in _chunks(frontier):
                marks = ",".join("?" * len(chunk))
                cur.execute(
                    f"SELECT s, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? "
                    f"AND (s IN ({marks}) OR o_id IN ({marks}))",
                    [GRAPH, *chunk, *chunk],
                )
                for s, o in cur.fetchall():
                    nxt.update((s, o))
            frontier = nxt - seen
            seen |= nxt
    finally:
        cur.close()
    return seen - {key}


def _sync(conn) -> None:
    from iris_vector_graph.engine import IRISGraphEngine

    IRISGraphEngine(conn, embedding_dimension=4).fhir_graph_sync(GRAPH)


def load_run(conn, loader: FhirLoader, resources: list[dict]) -> list[dict]:
    """PUT `resources` (already prefixed) and sync; returns the ones stored. Any
    server rejection fails the test with the full list, each a finding for
    SOURCE.md's excluded list. Stored resources are torn down before failing."""
    stored, rejected = [], []
    try:
        for r in resources:
            out = loader.dispatch("PUT", f"/{r['resourceType']}/{r['id']}", r)
            if str(out["status"]).startswith("20"):
                stored.append(r)
            else:
                rejected.append(f"{r['resourceType']}/{r['id']}: {out}")
        if rejected:
            pytest.fail("server rejected resources:\n" + "\n".join(rejected))
        _sync(conn)
    except BaseException:
        teardown_run(conn, loader, stored)
        raise
    return stored


def teardown_run(conn, loader: FhirLoader, resources: list[dict]) -> None:
    """Delete `resources` in reverse load order, then sync."""
    for r in reversed(resources):
        loader.dispatch("DELETE", f"/{r['resourceType']}/{r['id']}")
    _sync(conn)


@pytest.fixture(scope="module")
def genomics_loaded(fhir_conn_required):
    """(prefix, resources): the vendored genomics fixture, loaded under a fresh
    per-run prefix for one module and deleted afterwards."""
    from iris_vector_graph.engine import IRISGraphEngine

    from tests.e2e.genomics_fixture import load_fixture

    engine = IRISGraphEngine(fhir_conn_required, embedding_dimension=4)
    if not engine.fhir_graph_register(denylist=[]).get("rebuilt"):
        engine.fhir_graph_rebuild(GRAPH)
    loader = FhirLoader(fhir_conn_required)
    resources = load_run(fhir_conn_required, loader, prefix_resources(load_fixture(), loader.prefix))
    try:
        yield loader.prefix, resources
    finally:
        teardown_run(fhir_conn_required, loader, resources)
