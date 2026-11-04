"""Spec 231 E2E fixtures: the scratch FHIR namespace IVGFHIR in ivg-iris-enterprise.

IVGFHIR is scratch: each session starts by emptying the repository and erasing
its FHIR graphs (`reset_repository`). Keep nothing there you want to keep.

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

import dataclasses
import glob
import json
import os
import re
import traceback
import uuid

import pytest

NAMESPACE = "IVGFHIR"
ENDPOINT = "/csp/healthshare/ivgfhir/fhir/r4"
GRAPH = "fhir:IVGFHIR:X0001"
PORT = int(os.environ.get("IVG_PORT", "31972"))
SYNTHEA_BATCH = 200
BATCH_BYTES = 1_000_000  # a batch Bundle is one string; IRIS caps strings at 3.6 MB

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


def reset_repository(conn, engine) -> None:
    """Empty the IVGFHIR repository and erase every FHIR graph registered on it.

    A FHIR delete is soft, so per-run deletes never shrank the repository: by
    2026-09-30 it held 282,582 deleted rows beside 23,638 live ones, and every
    Rebuild paid for them. HS.FHIRServer.Installer.Reset deletes the endpoint's
    resources outright. Each registered graph is erased too: its watermarks name
    rows that no longer exist, and erase_graph zeroes them."""
    import iris

    iris.createIRIS(conn).classMethodVoid("HS.FHIRServer.Installer", "Reset", "", ENDPOINT)
    cur = conn.cursor()
    try:
        cur.execute("SELECT graph_id FROM Graph_KG.fhir_graphs")
        graphs = [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
    for graph in graphs:
        engine.erase_graph(graph)


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


# ------------------------------------------------------------------ the session

#: `open_session` states. READY: set up, `conn` is live. UNREACHABLE: no IRIS answers on PORT. ABSENT: the container is
#: not FHIR-capable (no IVGFHIR namespace, or no endpoint at ENDPOINT), as on a freshly made one. BROKEN: IVGFHIR is
#: there but the session's own setup (deploy, initialize_schema, reset) raised.
READY, UNREACHABLE, ABSENT, BROKEN = "ready", "unreachable", "absent", "broken"

RECIPE = "CONTRIBUTING.md, 'The FHIR namespace (IVGFHIR)'"


@dataclasses.dataclass(frozen=True)
class FhirSession:
    conn: object
    state: str
    reason: str = ""
    detail: str = ""  # the traceback of a BROKEN setup


class SetupStepError(Exception):
    """A step of `setup_namespace` raised; `step` names it, `__cause__` is the real error."""

    def __init__(self, step: str, error: BaseException):
        super().__init__(f"{step}: {type(error).__name__}: {error}")
        self.step = step
        self.error = error


def connect_sys():
    import iris

    return iris.connect(hostname="localhost", port=PORT, namespace="%SYS", username="_SYSTEM", password="SYS")


def namespace_exists(sys_conn) -> bool:
    import iris

    return bool(int(iris.createIRIS(sys_conn).classMethodValue("%SYS.Namespace", "Exists", NAMESPACE)))


def endpoint_problem(conn) -> str | None:
    """None when IVGFHIR serves a FHIR endpoint at ENDPOINT, else what is missing. A SQL error other than a missing
    table raises: that is not an unprepared container, and saying so would hide it."""
    cur = conn.cursor()
    try:
        try:
            cur.execute("SELECT COUNT(*) FROM HS_FHIRServer.RepoInstance WHERE cspUrl = ?", [ENDPOINT])
        except Exception as exc:
            if "<-30>" not in str(exc):
                raise
            return f"{NAMESPACE} has no FHIR server (HS_FHIRServer.RepoInstance does not exist)"
        count = cur.fetchone()[0]
    finally:
        cur.close()
    return None if count else f"{NAMESPACE} has a FHIR server but no endpoint at {ENDPOINT}"


def setup_namespace(conn) -> None:
    """Compile Graph.KG.* and the test loader into IVGFHIR, create the IVG schema there, and empty the repository.
    A failing step raises SetupStepError naming it."""
    from iris_vector_graph.engine import IRISGraphEngine

    try:
        errors = deploy(conn)
    except Exception as exc:
        raise SetupStepError("deploy", exc) from exc
    if errors:
        raise SetupStepError("deploy", RuntimeError("compile errors in IVGFHIR:\n" + "\n".join(errors)))
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=4)
        engine.initialize_schema(auto_deploy_objectscript=False)
    except Exception as exc:
        raise SetupStepError("initialize_schema", exc) from exc
    try:
        reset_repository(conn, engine)
    except Exception as exc:
        raise SetupStepError("reset_repository", exc) from exc


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # pragma: no cover - closing a connection that already died says nothing more
        pass


def open_session(connect_sys=connect_sys, connect_ns=connect, namespace_exists=namespace_exists,
                 endpoint_problem=endpoint_problem, setup=setup_namespace) -> FhirSession:
    """Probe the container, then set IVGFHIR up. Never raises: a failure is a state with its reason.

    The namespace is probed from %SYS first: a connection to a namespace that does not exist answers
    `Access Denied`, which reads like a password fault."""
    where = f"IRIS on localhost:{PORT}"
    try:
        sys_conn = connect_sys()
    except Exception as exc:
        return FhirSession(None, UNREACHABLE, f"{where} is not reachable ({exc})")
    try:
        exists = namespace_exists(sys_conn)
    except Exception as exc:
        return FhirSession(None, BROKEN, f"could not ask {where} whether {NAMESPACE} exists: {exc}",
                           traceback.format_exc())
    finally:
        _close(sys_conn)
    if not exists:
        return FhirSession(None, ABSENT, f"{where} has no {NAMESPACE} namespace, so it is not FHIR-capable; "
                                         f"make it per {RECIPE}")
    try:
        conn = connect_ns()
    except Exception as exc:
        return FhirSession(None, BROKEN, f"{NAMESPACE} exists on {where} but a connection to it failed: {exc}",
                           traceback.format_exc())
    try:
        problem = endpoint_problem(conn)
        if problem:
            _close(conn)
            return FhirSession(None, ABSENT, f"{problem}; make it per {RECIPE}")
        setup(conn)
    except Exception as exc:
        _close(conn)
        step = exc.step if isinstance(exc, SetupStepError) else "the endpoint probe"
        cause = exc.error if isinstance(exc, SetupStepError) else exc
        return FhirSession(None, BROKEN,
                           f"{NAMESPACE} setup failed in {step}: {type(cause).__name__}: {cause}",
                           traceback.format_exc())
    return FhirSession(conn, READY)


def consumer_outcome(session: FhirSession, *, required: bool, skip_iris_tests: bool,
                     reported: set) -> tuple[str, str]:
    """("conn" | "skip" | "fail", message) for a fixture consuming `session`.

    ABSENT skips both fixtures: the container was not made FHIR-capable. UNREACHABLE skips `fhir_conn` and fails
    `fhir_conn_required` unless SKIP_IRIS_TESTS (constitution VIII, Gate 1). BROKEN fails the first consumer with the
    traceback and skips every later one with the same one-line reason, so a broken setup is one failure that says
    why, not one error per test. `reported` remembers which sessions have failed once."""
    if session.state == READY:
        return "conn", ""
    if session.state == ABSENT:
        return "skip", session.reason
    if session.state == UNREACHABLE:
        return ("fail" if required and not skip_iris_tests else "skip"), session.reason
    if id(session) not in reported:
        reported.add(id(session))
        return "fail", f"{session.reason}\n\n{session.detail}"
    return "skip", f"{session.reason} (the first FHIR test of this session failed with the traceback)"


_REPORTED: set = set()
_SESSION: list = []  # the run's one FhirSession, opened by whichever asks first


def current_session() -> FhirSession:
    if not _SESSION:
        _SESSION.append(open_session())
    return _SESSION[0]


def _consume(session: FhirSession, required: bool):
    action, message = consumer_outcome(
        session, required=required,
        skip_iris_tests=os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true", reported=_REPORTED,
    )
    if action == "fail":
        pytest.fail(message, pytrace=False)
    if action == "skip":
        pytest.skip(message)
    return session.conn


def pytest_runtest_setup(item):
    """Decide each FHIR test's fate before its fixtures run (tests/e2e/conftest.py imports this hook).

    The session-scoped fixtures cannot: pytest caches a session fixture's exception and raises it again in every
    test that asks for it, so a fail-once-then-skip decision has to be made per test, here. Outside tests/e2e (the
    integration modules import the fixtures directly) the fixtures still decide, once for all their tests."""
    names = getattr(item, "fixturenames", ())
    if "_fhir_session" in names:
        _consume(current_session(), required="fhir_conn_required" in names)


@pytest.fixture(scope="session")
def _fhir_session():
    """The run's FhirSession (`current_session`). Shared by `fhir_conn` and `fhir_conn_required`, so both use one
    connection and one deploy."""
    session = current_session()
    yield session
    if session.conn is not None:
        _close(session.conn)


@pytest.fixture(scope="session")
def fhir_conn(_fhir_session):
    return _consume(_fhir_session, required=False)


@pytest.fixture(scope="session")
def fhir_conn_required(_fhir_session):
    """`fhir_conn`, but an unreachable IRIS fails the run unless SKIP_IRIS_TESTS is
    set (constitution VIII, Gate 1). A container without IVGFHIR skips, as for `fhir_conn`."""
    return _consume(_fhir_session, required=True)


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


def batch_bundle(resources: list[dict], method: str) -> dict:
    """A `batch` Bundle applying `method` (PUT or DELETE) to each resource's key."""
    entries = []
    for r in resources:
        entry = {"request": {"method": method, "url": f"{r['resourceType']}/{r['id']}"}}
        if method == "PUT":
            entry = {"resource": r, **entry}
        entries.append(entry)
    return {"resourceType": "Bundle", "type": "batch", "entry": entries}


def batches(resources: list[dict], batch: int) -> list[list[dict]]:
    """Consecutive chunks of at most `batch` resources and, unless one resource is
    larger on its own, at most BATCH_BYTES of JSON."""
    out, chunk, size = [], [], 0
    for r in resources:
        n = len(json.dumps(r))
        if chunk and (len(chunk) >= batch or size + n > BATCH_BYTES):
            out.append(chunk)
            chunk, size = [], 0
        chunk.append(r)
        size += n
    if chunk:
        out.append(chunk)
    return out


def _put_batched(loader, resources: list[dict], batch: int, stored: list, rejected: list) -> None:
    i = 0
    for chunk in batches(resources, batch):
        i += len(chunk)
        out = loader.dispatch("POST", "", batch_bundle(chunk, "PUT"))
        entries = out.get("body", {}).get("entry", []) if str(out["status"]).startswith("20") else []
        if len(entries) != len(chunk):
            rejected.append(f"batch ending at {i}: {str(out)[:500]}")
            continue
        for r, e in zip(chunk, entries):
            status = str(e.get("response", {}).get("status", ""))
            if status.startswith("20"):
                stored.append(r)
            else:
                rejected.append(f"{r['resourceType']}/{r['id']}: {e}")


def load_run(conn, loader: FhirLoader, resources: list[dict], batch: int = 0) -> list[dict]:
    """PUT `resources` (already prefixed) and sync; returns the ones stored. Any
    server rejection fails the test with the full list, each a finding for
    SOURCE.md's excluded list. Stored resources are torn down before failing.
    `batch` > 0 PUTs that many per `batch` Bundle, in order."""
    stored, rejected = [], []
    try:
        if batch > 0:
            _put_batched(loader, resources, batch, stored, rejected)
        else:
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
        teardown_run(conn, loader, stored, batch=batch)
        raise
    return stored


def teardown_run(conn, loader: FhirLoader, resources: list[dict], batch: int = 0) -> None:
    """Delete `resources` in reverse load order, then sync. `batch` > 0 deletes that
    many per `batch` Bundle."""
    rev = list(reversed(resources))
    if batch > 0:
        for chunk in batches(rev, batch):
            loader.dispatch("POST", "", batch_bundle(chunk, "DELETE"))
    else:
        for r in rev:
            loader.dispatch("DELETE", f"/{r['resourceType']}/{r['id']}")
    _sync(conn)


def teardown_prefix(conn, loader: FhirLoader) -> None:
    """Delete every live resource whose id carries `loader`'s prefix, then sync.
    For tests that PUT as they go and keep no list."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT Key FROM HSFHIR_X0001_R.Rsrc WHERE Key LIKE ? AND (Deleted IS NULL OR Deleted = 0)",
            [f"%/{loader.prefix}-%"],
        )
        keys = [row[0].split("/", 1) for row in cur.fetchall()]
    finally:
        cur.close()
    resources = [{"resourceType": t, "id": i} for t, i in keys]
    for chunk in batches(resources, SYNTHEA_BATCH):
        loader.dispatch("POST", "", batch_bundle(chunk, "DELETE"))
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


@pytest.fixture(scope="module")
def synthea_loaded(fhir_conn_required):
    """(prefix, resources): the vendored Synthea fixture (spec 235), loaded in
    dependency order under a fresh per-run prefix for one module and deleted
    afterwards."""
    from iris_vector_graph.engine import IRISGraphEngine

    from tests.e2e.interp_fixture import load_order, load_synthea

    engine = IRISGraphEngine(fhir_conn_required, embedding_dimension=4)
    if not engine.fhir_graph_register(denylist=[]).get("rebuilt"):
        engine.fhir_graph_rebuild(GRAPH)
    loader = FhirLoader(fhir_conn_required)
    resources = load_run(
        fhir_conn_required,
        loader,
        load_order(prefix_resources(load_synthea(), loader.prefix)),
        batch=SYNTHEA_BATCH,
    )
    try:
        yield loader.prefix, resources
    finally:
        teardown_run(fhir_conn_required, loader, resources, batch=SYNTHEA_BATCH)
