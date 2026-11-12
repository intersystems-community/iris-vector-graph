"""Session and shared fixtures.

Namespaces (each from the environment):

- IVG_NAMESPACE: the namespace the session connects to and deploys into (USER when unset).
- IVG_NS_IVG236: the spec 236 scratch namespace (`ivg_236_scratch`). No default: the fixture deploys this checkout's
  classes and setup into it, and its tests erase it, so it skips until the variable names a namespace you own
  (4.6.0 R287).
"""

import contextlib
import logging
import os
import re
import subprocess
import uuid
from typing import Optional

import pytest

logger = logging.getLogger(__name__)

_GQS_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
#: The namespace the session connects to and deploys into. USER unless a scratch
#: namespace is named, so a run can be kept off the namespace other suites share.
_IVG_NAMESPACE = os.environ.get("IVG_NAMESPACE", "USER")
#: The same, for tests that open a connection or a terminal session of their own: they use this, never "USER" by name,
#: so a run pointed at a scratch namespace leaves USER alone (4.6.0 R129; tests/unit/test_no_hardcoded_user_namespace.py).
TEST_NAMESPACE = _IVG_NAMESPACE


def requires_user_namespace(why: str):
    """A `skipif` marker for a test that can only run in USER (`why`), and writes there: it runs only when USER is the
    run's own namespace, never when IVG_NAMESPACE names another."""
    return pytest.mark.skipif(
        TEST_NAMESPACE != "USER",
        reason=f"runs only in USER ({why}); this run's namespace is {TEST_NAMESPACE} (IVG_NAMESPACE)",
    )


@pytest.fixture(scope="session")
def ivg_namespace() -> str:
    """The namespace this run's IRIS fixtures connect to and deploy into (IVG_NAMESPACE, default USER)."""
    return TEST_NAMESPACE

#: The vector width the session namespace is bootstrapped at, and the one the
#: vector suite assumes for the shared `Graph_KG.kg_NodeEmbeddings`. Matches
#: `schema.py`'s own default in `get_base_schema_sql`. See
#: `_restore_session_embedding_width` for why it has to be put back at teardown.
SESSION_EMBEDDING_DIM = 768


def container_state_is_running(state) -> bool:
    """True only for Docker's `running` state.

    `IRISContainer.attach` succeeds against a container that is merely *present*, so a
    stopped container is indistinguishable from a healthy one by attach alone. Every
    connection then falls through to whatever is listening on localhost:1972 and the
    suite reports green against the wrong database.

    Compares for equality, not containment: `not-running` must not read as running.
    """
    if not state:
        return False
    return state.strip().lower() == "running"


def docker_container_state(container_name: str):
    """The container's Docker state, or None when it does not exist or Docker is absent."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def requires_running_container(container_name: str):
    """A `skipif` marker that skips unless `container_name` is *running*.

    `docker inspect <name>` exits 0 for a container in any state, so a guard built on its
    exit code lets a test body run against a stopped container and fail with Docker's
    `container ... is not running`. `ivg-iris` is exactly that case here: it exists and is
    deliberately left down, because `MaxServerConn=1` makes it unusable for the suite.

    Evaluated at import, like every `skipif` condition, so the state is read once per
    session. Looks `docker_container_state` up through the module rather than closing over
    it, so a unit test can substitute it.
    """
    state = globals()["docker_container_state"](container_name)
    return pytest.mark.skipif(
        not container_state_is_running(state),
        reason=f"container {container_name} is {state or 'absent'}, not running",
    )


def container_hostname_matches(reported, expected_hostname="", container_id="") -> bool:
    """True only when `reported` identifies the container we asked for.

    An IRIS instance reports its own hostname (`%SYSTEM.INetInfo::LocalHostName`), and
    inside Docker that is `{{.Config.Hostname}}` — by default the first twelve characters
    of `{{.Id}}`. Comparing the two turns "the container is running" into "the connection
    reached *that* container", which is the assertion `container_state_is_running` cannot
    make.

    Fails closed in every direction: an unreadable hostname, or nothing to compare it
    against, is not a pass. A helper that returned True on an empty reading would be a
    no-op on exactly the builds where the probe does not work.

    A prefix match requires the full twelve characters; a shorter string is a prefix of
    too many IDs to mean anything.
    """
    def _norm(value) -> str:
        return str(value).strip().lower() if value else ""

    got = _norm(reported)
    if not got:
        return False
    if got == _norm(expected_hostname):
        return True
    cid = _norm(container_id)
    return bool(cid) and len(got) >= 12 and cid.startswith(got)


def _docker_inspect(container_name: str, fmt: str):
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", fmt, container_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def docker_container_id(container_name: str):
    """The container's full ID, or None when it does not exist or Docker is absent."""
    return _docker_inspect(container_name, "{{.Id}}")


def docker_container_hostname(container_name: str):
    """The hostname Docker gave the container, or None when it cannot be read."""
    return _docker_inspect(container_name, "{{.Config.Hostname}}")


def probe_instance_hostname(conn):
    """Ask the instance behind `conn` for its own hostname, or None if it cannot say.

    Measured on `irishealth:2026.3.0AI.113.0` (2026-09-21): there is no SQL route to this.
    `SELECT $SYSTEM.INetInfo.LocalHostName()`, `SELECT $ZU(110)` and
    `SELECT $SYSTEM.Util.InstallDirectory()` all fail `SQLCODE -12 <A term expected>`;
    `CALL %SYSTEM.INetInfo_LocalHostName()` and a `%SYS.ProcessQuery` select both hang
    past 120 seconds. The Native API answers immediately and needs no user table, so it
    works on all four of `iris_connection`'s fallback paths.

    Opens its **own** handle and closes it. `createIRIS` on a connection that also runs
    cursor DDL permanently corrupts the driver's parameter binding — see the
    `_safe_createIRIS` monkeypatch below — so a probe that reused the session connection
    would break the very session it is validating.
    """
    import iris as _iris

    native = None
    try:
        native = _iris.connect(
            hostname=conn.hostname,
            port=conn.port,
            namespace=conn.namespace,
            username="_SYSTEM",
            password="SYS",
        )
        return _iris.createIRIS(native).classMethodValue(
            "%SYSTEM.INetInfo", "LocalHostName"
        )
    except Exception as exc:  # pragma: no cover - build-dependent
        logger.warning("Could not read the instance's hostname: %s", exc)
        return None
    finally:
        if native is not None:
            with contextlib.suppress(Exception):
                native.close()


def _deploy_native(namespace: str, port: Optional[int] = None):
    """(connection, Native API handle) in `namespace` on 127.0.0.1:`port` (IVG_PORT, default 31972), as IRIS_USERNAME /
    IRIS_PASSWORD (default _SYSTEM / SYS). The class deploy runs over TCP: piping ObjectScript into `iris session`
    is what Cortex XDR kills (4.7.0)."""
    import iris

    conn = iris.createConnection(
        hostname="127.0.0.1", port=int(port or os.environ.get("IVG_PORT", "31972")), namespace=namespace,
        username=os.environ.get("IRIS_USERNAME", "_SYSTEM"), password=os.environ.get("IRIS_PASSWORD", "SYS"),
    )
    return conn, iris.createIRIS(conn)


def _recorded_schema(native) -> Optional[str]:
    """The namespace's recorded IVG Schema (``^IVG.Deploy("schema")``), None when nothing is recorded."""
    value = native.get("^IVG.Deploy", "schema")
    if value is None:
        return None
    return str(value).strip() or None


def _deploy_objectscript(container_name: str, namespace: Optional[str] = None, native=None,
                         port: Optional[int] = None) -> None:
    """Load the classes of iris_src/src whose source changed since the last deploy into `namespace` of
    `container_name`, over TCP through `native` (a Native API handle in that namespace; one is opened on
    127.0.0.1:`port` when not given). Each source is sent as a stream and loaded with %SYSTEM.OBJ.LoadStream, one
    class at a time ("ck-d", no background workers: Community Edition's 2-core limit made LoadDir's parallel
    compile fail ERROR #7802), and its hash recorded in ^IVGDeployed(namespace, file) when it loaded."""
    namespace = namespace or _IVG_NAMESPACE
    if native is None:
        conn, native = _deploy_native(namespace, port)
        try:
            return _deploy_objectscript(container_name, namespace, native=native)
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    # The sources load as written, and they name Graph_KG. A namespace recorded under another IVG Schema would get
    # Graph_KG's schema classes beside its own (4.6.0 R173), so it refuses before anything is loaded.
    from iris_vector_graph.schema_name import DEFAULT_SCHEMA, IVGSchemaError, same_schema

    recorded_schema = _recorded_schema(native)
    if recorded_schema is not None and not same_schema(recorded_schema, DEFAULT_SCHEMA):
        raise IVGSchemaError.mismatch(recorded_schema, DEFAULT_SCHEMA, namespace)
    import glob as _glob
    cls_files = sorted(_glob.glob("iris_src/src/**/*.cls", recursive=True))
    sources = {
        os.path.relpath(f, "iris_src/src").replace(os.sep, "/"): _source_hash(f) for f in cls_files
    }
    recorded = _recorded_deploy_hashes(native, sources, namespace)
    failed = []
    for rel in classes_to_deploy(sources, recorded):
        # A class whose source is unchanged since the last deploy into this namespace is not
        # compiled again. Recompiling all 57 classes at the start of every test session made each
        # session slow, timed out under load (the "errors in 33 s" in the setup of whatever file
        # was unlucky), and, under the line monitor, stopped it counting the recompiled routines.
        with open(os.path.join("iris_src", "src", rel), encoding="utf-8") as fh:
            text = fh.read()
        stream = native.classMethodObject("%Stream.GlobalCharacter", "%New")
        for i in range(0, len(text), 1_000_000):
            stream.invoke("Write", text[i: i + 1_000_000])
        stream.invoke("Rewind")
        status = native.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck-d")
        # every load's status is read (4.6.0 R235): a SqlProcs that failed #5083 left the session without it, silently
        if status == 1 or str(status) == "1":
            native.set(sources[rel], "^IVGDeployed", namespace, rel)
        else:
            failed.append(f"{rel}: {native.classMethodValue('%SYSTEM.Status', 'GetErrorText', status)}")
    if failed:
        raise RuntimeError(f"ObjectScript deploy into {namespace} failed for {len(failed)} class(es): " + "; ".join(failed))


def _source_hash(path: str) -> str:
    import hashlib

    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:20]


#: Projects every SQL procedure (4.6.0 R084); loaded last (R235, as tests/e2e/fhir_conftest.deploy_sources and
#: tcp-deploy load it).
SQL_PROCS = "Graph/KG/SqlProcs.cls"


def classes_to_deploy(sources: dict, recorded: dict) -> list:
    """The classes whose source differs from what was last deployed (all of them when nothing is recorded), in name
    order but Graph.KG.SqlProcs last: in a namespace upgraded from before R084 the old Traversal, TemporalIndex and the
    classes after S still project the procedures SqlProcs projects until they are recompiled, and SqlProcs loaded
    before them failed ERROR #5083 (4.6.0 R235)."""
    changed = [rel for rel in sorted(sources) if recorded.get(rel) != sources[rel]]
    return sorted(changed, key=lambda rel: rel == SQL_PROCS)


def _recorded_deploy_hashes(native, sources: dict, namespace: Optional[str] = None) -> dict:
    """{file: hash} of what was last deployed into `namespace` (``^IVGDeployed(namespace, file)``), read over TCP."""
    namespace = namespace or _IVG_NAMESPACE
    recorded = {}
    for rel in sources:
        value = native.get("^IVGDeployed", namespace, rel)
        value = "" if value is None else str(value).strip()
        if value:
            recorded[rel] = value
    return recorded


_NATIVE_CONNECTION_IS_GONE = (
    "COMMUNICATION LINK ERROR",
    "COMMUNICATION ERROR",
    "CONNECTION CLOSED",
    "CONNECTION LOST",
    "BROKEN PIPE",
    "EPIPE",
    "ECONNRESET",
)


def native_error_means_the_connection_is_gone(exc: BaseException) -> bool:
    """Is this the socket dying, rather than IRIS answering with an error?

    Only a dead connection is worth retrying: an ObjectScript error means the call
    reached IRIS and got a real answer, and running it twice would repeat whatever
    it did before it failed.
    """
    text = str(exc).upper()
    return any(marker in text for marker in _NATIVE_CONNECTION_IS_GONE)


class ReconnectingNative:
    """An IRIS native object that reopens its connection once if it dies.

    The session shares one dedicated native connection — `createIRIS` on the
    connection that also runs cursor DDL corrupts the driver's parameter binding,
    so every `createIRIS(session_connection)` call is redirected to this one. Until
    4.0.0 nothing noticed when it died. The T074 gate measured the cost: one EPIPE
    became 69 `ERROR at setup` entries at `iris_master_cleanup`, each one a test
    that never ran, and the single real event was buried in the 69th copy of its
    own message.

    So one retry, after reopening, and the death is logged at `ERROR` rather than
    swallowed: the point is to name the event once, not to hide it.
    """

    def __init__(self, open_native, on_reconnect=None, generation=None):
        self._open_native = open_native
        self._on_reconnect = on_reconnect
        # generation: a counter the session bumps when it retires the connection on purpose (per module, below); the
        # proxy then takes the new one without calling it a death
        self._generation = generation
        self._seen = generation() if generation is not None else None
        self._native = open_native()

    def __getattr__(self, name):
        if self._generation is not None and self._generation() != self._seen:
            self._seen = self._generation()
            self._native = self._open_native()
        attr = getattr(self._native, name)
        if not callable(attr):
            return attr

        def _call_with_one_retry(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except Exception as exc:
                if not native_error_means_the_connection_is_gone(exc):
                    raise
                if self._on_reconnect is not None:
                    self._on_reconnect(exc)
                self._native = self._open_native()
                return getattr(self._native, name)(*args, **kwargs)

        return _call_with_one_retry


class NativeProcessDegraded(UserWarning):
    """The session's native process could not compile a statement it had never seen (see probe_native_compiles)."""


#: Where report_degraded_native appends, one line a module (IVG_NATIVE_PROBE_LOG overrides it).
NATIVE_PROBE_LOG = os.path.expanduser(os.environ.get("IVG_NATIVE_PROBE_LOG", "~/.cache/ivg-bench/native-probe.log"))


def probe_native_compiles(native, schema: str = "Graph_KG") -> Optional[str]:
    """None when `native`'s server process compiles a new statement on rdf_edges; otherwise what IRIS said.

    4.6.0 full E2E (2026-11-05): the session's native process stopped compiling cached queries
    (`<UNDEFINED>lviter+16^%qaqpnl *mt(`, "failed to compile Cached Query Class") while a fresh process compiled the
    same text. The column alias makes the text new each time, so the statement is compiled rather than found cached
    (a literal would not do: the cache replaces literals with parameters). A namespace without the table (-30) and a
    connection that is gone say nothing about compiling.
    """
    sql = (
        f"SELECT TOP 1 e.s AS ivgprobe_{uuid.uuid4().hex} FROM {schema}.rdf_edges e WHERE e.o_id = ?"
        f" AND NOT EXISTS (SELECT 1 FROM {schema}.nodes n WHERE n.node_id = e.s)"
    )
    try:
        rs = native.classMethodObject("%SQL.Statement", "%ExecDirect", None, sql, "")
    except Exception as exc:
        return None if native_error_means_the_connection_is_gone(exc) else str(exc)
    code = int(rs.get("%SQLCODE") or 0)
    if code >= 0 or code == -30:
        return None
    return f"SQLCODE {code}: {rs.get('%Message')}"


def report_degraded_native(module: str, namespace: str, error: str, log_path) -> None:
    """Name the module after which the native process stopped compiling: a warning, and a line in `log_path`."""
    import datetime
    import warnings
    from pathlib import Path

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    one_line = " | ".join(part.strip() for part in error.splitlines() if part.strip())
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {namespace} {module}: {one_line}\n")
    warnings.warn(
        NativeProcessDegraded(
            f"after {module}, the session's native process could not compile a new statement on rdf_edges ({error}); "
            f"it was retired and the next module opens a new one. Logged to {log_path}"
        ),
        stacklevel=2,
    )


class SessionHeartbeat:
    """Keeps the session-scoped connections from dying of idleness.

    Measured against `ivg-iris-enterprise` from the host: a connection that has run
    a query and then sits idle is alive at 300 s and gone at 420 s, on both the
    OrbStack address and the published port, and the next call fails with `EPIPE`.
    `arno_iris_connection` went unused for about six minutes in the full unit run
    (`test_bfs_arno` to `test_rrf_fuse_e2e`), and every Enterprise test after that
    failed at setup. See tests/unit/test_session_heartbeat.py.

    `beat()` runs `SELECT 1` on each registered connection that has gone `interval`
    seconds without one. It is called between tests, on the test thread, so it never
    shares a connection with a running test. A dead connection is logged once at
    `ERROR`; reopening it is not attempted, because the fixtures holding it cannot be
    handed a new object.
    """

    def __init__(self, interval: float = 120.0, clock=None):
        import time

        self.interval = interval
        self._clock = clock or time.monotonic
        self._conns: dict = {}

    def register(self, name: str, conn) -> None:
        self._conns[name] = [conn, self._clock(), False]

    def unregister(self, name: str) -> None:
        self._conns.pop(name, None)

    def beat(self) -> None:
        now = self._clock()
        for name, entry in list(self._conns.items()):
            conn, last, reported = entry
            if now - last < self.interval:
                continue
            entry[1] = now
            cur = None
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchall()
            except Exception as exc:
                if not reported:
                    entry[2] = True
                    logger.error(
                        "Session connection %s did not answer its heartbeat (%s). "
                        "Tests that use it from here on will fail at setup.", name, exc,
                    )
            finally:
                if cur is not None:
                    with contextlib.suppress(Exception):
                        cur.close()


_SESSION_HEARTBEAT = SessionHeartbeat()


def put_native_namespace_back(native, expected: str) -> Optional[str]:
    """The namespace `native`'s process was in when it was not `expected`, after moving it back; None when it was.

    R234: one Arno call that left the session's shared Native API process in USER made every later native read in an
    IVGFIX run answer from USER (hundreds of empty answers, none in the test that moved it). Raises when the process
    will not go back, since every test after it would read the wrong namespace.
    """
    found = str(native.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    if found.upper() == expected.upper():
        return None
    native.classMethodValue("%SYSTEM.Process", "SetNamespace", expected)
    now = str(native.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    if now.upper() != expected.upper():
        raise RuntimeError(f"the session's Native API process is still in {now}, not {expected}")
    return found


#: Set by `iris_connection`: a no-argument callable that runs `put_native_namespace_back` on the session's shared
#: Native API process (None while there is none), and the namespace it must be in.
_SESSION_NATIVE_NS_CHECK: list = [None]

#: Set by `iris_connection`: a callable taking a module name, which probes the session's native process and closes it
#: (the next native call opens another). None outside a session that has the connection.
_SESSION_NATIVE_RETIRE: list = [None]


@pytest.fixture(scope="module", autouse=True)
def _session_native_retired_per_module(request):
    """At the end of each module the session's native process is probed, then replaced, so a process one module left
    unable to compile cannot fail the modules after it, and the module is named (tests/unit/
    test_native_process_retired_per_module.py)."""
    yield
    retire = _SESSION_NATIVE_RETIRE[0]
    if retire is not None:
        retire(request.module.__name__)


@pytest.fixture(autouse=True)
def _session_native_namespace_kept():
    """After each test, the session's Native API process is in the session's namespace; a test that moved it errors at
    teardown, naming where it left it, and the next test starts in the right namespace (R234)."""
    yield
    check = _SESSION_NATIVE_NS_CHECK[0]
    if check is None:
        return
    try:
        moved = check()
    except Exception as exc:
        if native_error_means_the_connection_is_gone(exc):
            return  # ReconnectingNative reopens it, in the session's namespace, on the next call
        raise
    if moved is not None:
        pytest.fail(
            f"this test left the session's Native API process in {moved}; it was moved back. Every later native "
            "call would have run there (R234: an Arno call that does not put the namespace back)"
        )


@pytest.fixture(autouse=True)
def _session_connection_heartbeat():
    _SESSION_HEARTBEAT.beat()
    yield


@pytest.fixture(scope="session")
def iris_test_container():
    from iris_devtester import IRISContainer
    import subprocess as _sp

    attached = False
    container = None

    # 1. Try the configured name.
    try:
        container = IRISContainer.attach(_GQS_CONTAINER)
        attached = True
        logger.info("Attached to existing container: %s", _GQS_CONTAINER)
    except Exception:
        pass

    # 2. If not found: fail on developer machines (missing container = error);
    #    skip in CI when SKIP_IRIS_TESTS=true (container not provisioned in that job).
    #    pytest.skip produces silent fake-green on dev machines — pytest.fail surfaces
    #    the problem.  CI sets SKIP_IRIS_TESTS=true explicitly to opt into skipping.
    if container is None:
        auto_start = os.environ.get("IVG_AUTO_START_CONTAINER", "0") not in ("0", "false", "no")
        if auto_start:
            pass  # fall through to start the container
        elif os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
            pytest.skip(
                f"IRIS container '{_GQS_CONTAINER}' not running — "
                f"skipped because SKIP_IRIS_TESTS=true"
            )
        else:
            pytest.fail(
                f"IRIS container '{_GQS_CONTAINER}' is not running. "
                f"Start it with: scripts/enterprise-container.sh up\n"
                f"(Set IVG_AUTO_START_CONTAINER=1 to start automatically, "
                f"or SKIP_IRIS_TESTS=true to skip in CI.)"
            )
        _sp.run(["docker", "rm", "-f", _GQS_CONTAINER], capture_output=True)
        logger.info("Starting fresh Community IRIS container: %s", _GQS_CONTAINER)
        container = (
            IRISContainer.community()
            .with_name(_GQS_CONTAINER)
            .with_preconfigured_password("SYS")
            .start()
        )

    name = container.get_container_name()

    # `attach` above succeeds on a container that exists but is not running. Deploying
    # into a stopped container silently sends every later connection to localhost:1972.
    # Gate 1 requires a fail, not a skip — a stopped container is as broken as a missing
    # one, and is harder to notice.
    state = docker_container_state(name)
    if not container_state_is_running(state):
        pytest.fail(
            f"IRIS container '{name}' exists but its Docker state is "
            f"'{state or 'unknown'}', not 'running'. Tests would fall through to "
            f"localhost:1972 and report green against the wrong database.\n"
            f"Start it with: scripts/enterprise-container.sh up"
        )

    _deploy_objectscript(name)

    yield container

    if not attached:
        keep = os.environ.get("IVG_KEEP_CONTAINER", "0") in ("1", "true", "yes")
        if not keep:
            try:
                container.stop()
                logger.info("Stopped container: %s", name)
            except Exception as e:
                logger.warning("Could not stop container %s: %s", name, e)


@pytest.fixture(scope="session")
def iris_connection(iris_test_container):
    import subprocess as _sp

    container_name = iris_test_container.get_container_name()
    cip = _sp.run(
        ["docker", "inspect", container_name,
         "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True,
    ).stdout.strip()

    _IVG_PORT = int(os.environ.get("IVG_PORT", "31972"))

    conn = None
    _which_path = "none"

    # Try OrbStack DNS first: {container}.orb.local resolves to the OrbStack-routable IP,
    # which allows direct :1972 connections without port-forwarding or socat.
    # Falls through silently on non-OrbStack hosts.
    import socket as _socket
    _orb_host = f"{container_name}.orb.local"
    try:
        _orb_ip = _socket.gethostbyname(_orb_host)
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname=_orb_ip, port=1972, namespace=_IVG_NAMESPACE,
                username="_SYSTEM", password="SYS",
            )
            _which_path = f"OrbStack DNS {_orb_host} ({_orb_ip}):1972"
            logger.info("Connected to %s via OrbStack DNS %s (%s):1972", container_name, _orb_host, _orb_ip)
        except Exception as _e:
            logger.info("OrbStack %s:1972 connect failed (%s) — falling back", _orb_ip, _e)
            conn = None
    except _socket.gaierror:
        pass  # not OrbStack

    # Try container IP (Linux Docker where container IPs are routable from host).
    if conn is None and cip:
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname=cip, port=1972, namespace=_IVG_NAMESPACE,
                username="_SYSTEM", password="SYS",
            )
            _which_path = f"container IP {cip}:1972"
            logger.info("Connected to %s via container IP %s:1972", container_name, cip)
        except Exception as _e:
            logger.info("Container IP %s:1972 not routable (%s) — falling back", cip, _e)
            conn = None

    if conn is None and _IVG_PORT != 21972:
        # Socat proxy fallback (legacy; OrbStack path above should handle enterprise container).
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname="localhost", port=_IVG_PORT, namespace=_IVG_NAMESPACE,
                username="_SYSTEM", password="SYS",
            )
            _which_path = f"localhost:{_IVG_PORT} (socat proxy)"
            logger.info("Connected to %s via localhost:%s (socat proxy)", container_name, _IVG_PORT)
        except Exception as _e:
            logger.info("localhost:%s connect failed (%s) — trying iris_devtester", _IVG_PORT, _e)
            conn = None

    if conn is None:
        # iris_devtester path: works on macOS Docker Desktop, OrbStack, and Linux.
        try:
            from iris_devtester import IRISContainer as _IRC
            _fresh = _IRC.attach(container_name)
            _fresh._connection = None
            conn = _fresh.get_connection()
            _which_path = "iris_devtester"
            logger.info("Connected to %s via iris_devtester", container_name)
        except Exception as e:
            logger.error("Could not connect to %s: %s", container_name, e)
            raise

    # Whichever path produced the connection, prove it reached the named container.
    #
    # This replaces a negative probe that only caught one known impostor (los-iris, by
    # looking for a class of its own). On 2026-09-18 a suite ran against
    # `irispython-dx-iris` on localhost:1972 and reported `547 passed`; that instance
    # holds no LOS class, so the negative probe said nothing. A positive assertion
    # catches every impostor, named or not.
    _expected_hostname = docker_container_hostname(container_name)
    _expected_id = docker_container_id(container_name)
    _reported = probe_instance_hostname(conn)
    if not container_hostname_matches(_reported, _expected_hostname, _expected_id):
        pytest.fail(
            f"The connection obtained via the {_which_path} path does not belong to "
            f"container '{container_name}'.\n"
            f"  the instance reports hostname: {_reported or '<unreadable>'}\n"
            f"  docker says the container is:  "
            f"{_expected_hostname or '<unknown>'} (id {(_expected_id or '?')[:12]})\n"
            f"A suite that runs against another instance reports a number that measures "
            f"nothing. Check for an SSH tunnel or another container on port "
            f"{_IVG_PORT}, then: scripts/enterprise-container.sh up"
        )
    logger.info(
        "Instance identity confirmed: %s answered as %s (%s path)",
        container_name, _reported, _which_path,
    )

    # Install the createIRIS monkeypatch BEFORE any operation touches the session
    # connection via the native IRIS API.  iris.createIRIS(conn) + cursor DDL on
    # the same connection permanently corrupts the IRIS Python driver's parameter
    # binding state.  All code paths that call createIRIS — including schema init,
    # _call_classmethod, engine._iris_obj() — must be redirected to a dedicated
    # native connection that never receives cursor DDL.
    #
    # Community Edition has a 5-connection limit.  To avoid exhausting it, we use
    # lazy native-connection creation: the native slot is opened on first demand,
    # then reused for the life of the session.  This avoids holding an extra
    # connection open during the large chunks of a test session where _iris_obj()
    # is never called.
    import iris as _iris_module
    _original_createIRIS = _iris_module.createIRIS
    _native_conn_holder: list = [None]  # mutable cell so the closure can update it
    _native_generation: list = [0]  # bumped when the connection is retired on purpose

    def _open_native_iris_object():
        if _native_conn_holder[0] is None:
            import iris as _iris_native
            _native_conn_holder[0] = _iris_native.connect(
                hostname=conn.hostname,
                port=conn.port,
                namespace=conn.namespace,
                username="_SYSTEM",
                password="SYS",
            )
        return _original_createIRIS(_native_conn_holder[0])

    def _discard_dead_native_conn(exc):
        # Named once, at ERROR, and attributable: the test running right now is the
        # one that killed the connection.  Before 4.0.0 this event was silent and
        # every later test that needed the native API failed at setup instead.
        logger.error(
            "The session's dedicated native connection died (%s) — reopening it. "
            "The test running at this point is the one that killed it.", exc,
        )
        dead = _native_conn_holder[0]
        _native_conn_holder[0] = None
        if dead is not None:
            with contextlib.suppress(Exception):
                dead.close()

    def _safe_createIRIS(target_conn):
        if target_conn is conn:
            try:
                return ReconnectingNative(
                    _open_native_iris_object,
                    on_reconnect=_discard_dead_native_conn,
                    generation=lambda: _native_generation[0],
                )
            except Exception as _e:
                logger.warning("Could not create native conn for session isolation: %s", _e)
        return _original_createIRIS(target_conn)

    _iris_module.createIRIS = _safe_createIRIS

    def _native_namespace_check():
        if _native_conn_holder[0] is None:
            return None
        return put_native_namespace_back(_original_createIRIS(_native_conn_holder[0]), conn.namespace)

    _SESSION_NATIVE_NS_CHECK[0] = _native_namespace_check

    def _retire_native(module: str) -> None:
        live = _native_conn_holder[0]
        if live is None:
            return
        error = probe_native_compiles(_original_createIRIS(live))
        if error is not None:
            report_degraded_native(module, conn.namespace, error, NATIVE_PROBE_LOG)
        _native_conn_holder[0] = None
        _native_generation[0] += 1
        with contextlib.suppress(Exception):
            live.close()

    _SESSION_NATIVE_RETIRE[0] = _retire_native

    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    with contextlib.suppress(Exception):
        cur = conn.cursor()
        try:
            GraphSchema.add_graph_id_column(cur)
            GraphSchema.update_spo_unique_constraint(cur)
            GraphSchema.add_graph_id_index(cur)
            conn.commit()
        finally:
            with contextlib.suppress(Exception):
                cur.close()

    try:
        # 768 matches schema.py's own default (get_base_schema_sql) and what
        # most of the e2e/integration suite assumes for kg_NodeEmbeddings.
        eng = IRISGraphEngine(conn, embedding_dimension=SESSION_EMBEDDING_DIM)
        # 4.7.0 row 25: an empty vector column at another width is re-declared only when asked
        eng.initialize_schema(auto_deploy_objectscript=False, rewidth=True)
    except Exception as e:
        logger.warning("Schema init failed (may already exist): %s", e)

    # Hand the connection to the per-test width guard, which must not request this
    # fixture: requesting it would open the connection for every unit test.
    _RESOLVED_SESSION_CONN[0] = conn
    _SESSION_HEARTBEAT.register("iris_connection", conn)

    yield conn

    _SESSION_HEARTBEAT.unregister("iris_connection")
    _SESSION_NATIVE_NS_CHECK[0] = None
    _SESSION_NATIVE_RETIRE[0] = None
    _RESOLVED_SESSION_CONN[0] = None
    _restore_session_embedding_width(conn)

    _iris_module.createIRIS = _original_createIRIS
    with contextlib.suppress(Exception):
        if _native_conn_holder[0] is not None:
            _native_conn_holder[0].close()
    conn.close()


def _restore_session_embedding_width(conn) -> None:
    """Put `Graph_KG.kg_NodeEmbeddings` back at the session width, loudly.

    The shared legacy embedding table has one `emb VECTOR(DOUBLE, n)` declaration
    for the whole namespace, and `initialize_schema(rewidth=True)` alters it to the calling
    engine's width whenever the table is empty (before 4.7.0 `initialize_schema()` did, unasked). Live tests legitimately build
    engines at other widths — 4, 8, 128, 384, 1536 all appear, some of them
    exercising the width migration itself — so any of them can leave the shared
    column narrowed.

    That state is sticky: once rows exist at the narrow width the engine refuses to
    widen (it cannot invent the missing dimensions), so every later run logs
    `CRITICAL: ... is VECTOR(DOUBLE, 4) but the engine is configured for 768` and
    every configured-width write is rejected with SQLCODE -104. It survived several
    runs before being spotted, because the message reads as a warning about the
    run's own data rather than as damage the previous run left behind.

    Restoring here rather than failing: a width-migration test that ends on another
    width is doing its job, so the drift is not by itself a defect — leaving it
    behind for the next session is. The log line names the width found, so a test
    that pollutes is still visible.
    """
    try:
        from iris_vector_graph.schema import GraphSchema

        cursor = conn.cursor()
        try:
            table = "Graph_KG.kg_NodeEmbeddings"
            found = GraphSchema.get_embedding_dimension(cursor, table)
            if found is None or found == SESSION_EMBEDDING_DIM:
                return
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            rows = int(row[0]) if row else 0
            logger.warning(
                "Session teardown: %s.emb is VECTOR(DOUBLE, %s), not the session "
                "width %s, and holds %s row(s). Some test in this run re-declared "
                "the shared table; restoring it so the next session starts clean.",
                table, found, SESSION_EMBEDDING_DIM, rows,
            )
            if rows:
                # The rows are this run's own test data at a width nothing else can
                # read, and they are what blocks the widening.
                cursor.execute(f"DELETE FROM {table}")
            conn.commit()
            from iris_vector_graph.engine import IRISGraphEngine

            IRISGraphEngine(
                conn, embedding_dimension=SESSION_EMBEDDING_DIM
            ).initialize_schema(auto_deploy_objectscript=False, rewidth=True)
            restored = GraphSchema.get_embedding_dimension(cursor, table)
            if restored != SESSION_EMBEDDING_DIM:
                logger.error(
                    "Session teardown: could not restore %s.emb to %s — it is still "
                    "VECTOR(DOUBLE, %s). The next session will log a width mismatch.",
                    table, SESSION_EMBEDDING_DIM, restored,
                )
        finally:
            with contextlib.suppress(Exception):
                cursor.close()
    except Exception as e:  # teardown must never fail the run
        logger.warning("Session teardown: embedding width restore skipped: %s", e)


def touches_shared_namespace(fixturenames) -> bool:
    """Could this test have re-declared the shared legacy embedding table?

    Only a test wired to the live session connection could. pytest resolves the
    whole fixture closure into `request.fixturenames`, so asking about
    `iris_connection` covers every fixture that depends on it. `arno_iris_connection`
    is a different namespace and shares no table with it.
    """
    return "iris_connection" in fixturenames


# Set by the `iris_connection` fixture once it has a live connection, so the
# per-test width guard can reach it without requesting (and thereby creating) it.
_RESOLVED_SESSION_CONN: list = [None]


@pytest.fixture(autouse=True)
def _keep_shared_embedding_width(request):
    """Restore the shared `emb` width after any test that could have changed it.

    Session teardown already restores it for the *next* run. This restores it for
    the next *test*, which is where the T074 gate lost four tests to a width some
    earlier test left behind, with nothing in the failure naming the cause.

    The drift is not treated as a failure — a width-migration test that ends on
    another width is doing its job — but it is logged at `ERROR` against the nodeid
    that caused it, so the polluter is named once instead of its victims failing
    anonymously later.
    """
    yield

    if not touches_shared_namespace(request.fixturenames):
        return
    # Never force the fixture into existence: a test that skipped before touching
    # the connection has nothing to restore, and opening one here would undo the
    # laziness that keeps Community's connection limit intact.
    conn = _RESOLVED_SESSION_CONN[0]
    if conn is None:
        return

    try:
        from iris_vector_graph.schema import GraphSchema

        cursor = conn.cursor()
        try:
            found = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
        finally:
            with contextlib.suppress(Exception):
                cursor.close()
        if found is None or found == SESSION_EMBEDDING_DIM:
            return
        logger.error(
            "%s left Graph_KG.kg_NodeEmbeddings.emb at VECTOR(DOUBLE, %s) instead of "
            "the session width %s. Restoring it — without this the next vector test "
            "fails with SQLCODE -104 or a dimension mismatch and nothing names this "
            "test.", request.node.nodeid, found, SESSION_EMBEDDING_DIM,
        )
        _restore_session_embedding_width(conn)
    except Exception as e:  # a teardown guard must never fail the test it guards
        logger.warning("Per-test embedding width restore skipped: %s", e)


_ARNO_CONTAINER = os.environ.get("IVG_ARNO_CONTAINER", "ivg-iris-enterprise")


def arno_port(environ=None) -> int:
    """The host port of the Arno container's SuperServer: IVG_ARNO_PORT, else IVG_PORT, else 31972.

    The default was 31971, which ivg-iris-enterprise maps to its port 19721, not the SuperServer: every Arno fixture
    then failed with a COMMUNICATION LINK ERROR whenever OrbStack DNS was down (4.6.0 R171)."""
    environ = os.environ if environ is None else environ
    return int(environ.get("IVG_ARNO_PORT") or environ.get("IVG_PORT") or "31972")


_ARNO_PORT = arno_port()


def arno_host_port() -> tuple:
    """(host, port) for the Arno container: OrbStack DNS at 1972, else localhost at `arno_port()`."""
    import socket as _socket

    try:
        return _socket.gethostbyname(f"{_ARNO_CONTAINER}.orb.local"), 1972
    except _socket.gaierror:
        return "localhost", _ARNO_PORT


def arno_native_connection():
    """A native `iris.connect` to the Arno container in this run's namespace (TEST_NAMESPACE), for `createIRIS`."""
    import iris as _iris

    host, port = arno_host_port()
    return _iris.connect(
        hostname=host, port=port, namespace=TEST_NAMESPACE, username="_SYSTEM", password="SYS"
    )


@pytest.fixture(scope="session")
def arno_iris_connection():
    import subprocess as _sp

    ps = _sp.run(
        ["docker", "ps", "--filter", f"name={_ARNO_CONTAINER}", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    if _ARNO_CONTAINER not in ps.stdout:
        pytest.skip(
            f"{_ARNO_CONTAINER} not running. "
            f"Start with: scripts/enterprise-container.sh up"
        )

    cip = _sp.run(
        ["docker", "inspect", _ARNO_CONTAINER,
         "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True,
    ).stdout.strip()

    import iris.dbapi as _dbapi

    # Prefer the Docker-internal IP (container-to-container path), but many
    # Docker network configurations on macOS make the internal IP unreachable
    # from the host.  Probe with a quick TCP connect first; if it fails, look
    # up the host-mapped port via `docker port` and connect via localhost.
    def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    # OrbStack DNS: {container}.orb.local resolves to a host-routable IP.
    hostname, port = "localhost", _ARNO_PORT
    import socket as _socket
    _orb_host = f"{_ARNO_CONTAINER}.orb.local"
    try:
        _orb_ip = _socket.gethostbyname(_orb_host)
        hostname, port = _orb_ip, 1972
        logger.info("arno_iris_connection: using OrbStack DNS %s → %s:1972", _orb_host, _orb_ip)
    except _socket.gaierror:
        # Not OrbStack — try direct container IP, then fall back to host port
        if cip and _tcp_reachable(cip, 1972):
            hostname, port = cip, 1972

    conn = _dbapi.connect(
        hostname=hostname, port=port, namespace=_IVG_NAMESPACE,
        username="_SYSTEM", password="SYS",
    )

    _c = conn.cursor()
    try:
        _c.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name='Graph.KG.LOSBriefingJob'")
        _los_count = _c.fetchone()[0]
    finally:
        with contextlib.suppress(Exception):
            _c.close()
    if _los_count > 0:
        raise RuntimeError(
            f"{_ARNO_CONTAINER} appears to be los-iris. "
            "Wrong container — check port and container name."
        )

    from iris_vector_graph.engine import IRISGraphEngine
    # Skip schema init if enterprise container is already the primary test container
    # (iris_connection fixture will have already deployed + initialized it). Running
    # initialize_schema() concurrently from two connections causes SQLCODE -110
    # (lock on a Graph.KG.* class definition during CREATE INDEX).
    _primary = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    if _primary != _ARNO_CONTAINER:
        try:
            IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
        except Exception as e:
            logger.warning("arno container schema init: %s", e)

    _SESSION_HEARTBEAT.register("arno_iris_connection", conn)
    yield conn
    _SESSION_HEARTBEAT.unregister("arno_iris_connection")
    conn.close()


@pytest.fixture(scope="function")
def iris_master_cleanup(iris_connection):
    # Some tests call native iris.createIRIS()/classMethodValue() directly on
    # the shared session iris_connection (e.g. Arno-native e2e tests) instead
    # of a scoped-off connection. A native-API failure there can permanently
    # corrupt the driver's protocol/parameter-binding state for that
    # connection (documented below re: DDL-after-createIRIS). Once that
    # happens, iris_connection.cursor() raises <COMMUNICATION LINK ERROR> for
    # EVERY subsequent test that requests this fixture — turning one test's
    # native-API misuse into a suite-wide cascade. Skip cleanup gracefully
    # instead of propagating that as a fixture-setup error.
    try:
        cursor = iris_connection.cursor()
    except Exception as e:
        pytest.skip(f"iris_connection unusable (likely corrupted by a prior "
                     f"test's native API call) — skipping cleanup: {e}")
    try:
        # Graph_KG.docs is graph content as of 4.0.0 — it carries graph_id and its
        # `id` names a node — and Graph.KG.Eraser::EraseAll deletes it. This DELETE
        # is kept because a container may still hold a pre-4.0.0 Eraser, whose
        # inventory does not name the table; against a current one it is redundant
        # rather than wrong.
        with contextlib.suppress(Exception):
            cursor.execute("DELETE FROM Graph_KG.docs")
        with contextlib.suppress(Exception):
            iris_connection.commit()

        # Everything else is Graph.KG.Eraser's (ADR-0004). This fixture used to
        # keep its own list of seven tables and then kill ^KG/^NKG through a
        # short-lived dbapi connection — which never worked: iris.createIRIS is
        # monkeypatched at session setup and only redirects the *session*
        # connection to the dedicated native one, so that call fell through to
        # the original, which rejects a dbapi connection outright. The whole
        # block sat inside contextlib.suppress, so every integration test ran
        # against whatever ^KG the previous run had left behind while the
        # fixture reported a clean database. See
        # tests/integration/test_master_cleanup.py.
        #
        # Passing iris_connection is what the monkeypatch is for: it swaps in the
        # dedicated native connection, so the session connection is never touched
        # by the native API and its parameter-binding state stays intact.
        #
        # A cleanup that cannot clean is not something to suppress — every
        # assertion after it would be measuring the previous test's state — so a
        # failure here is raised.
        import iris as _iris

        _iris.createIRIS(iris_connection).classMethodValue(
            "Graph.KG.Eraser", "EraseAll"
        )

        # `Do ##class(Graph.KG.Traversal).BuildKG()` used to sit here. IRIS SQL
        # rejects a Do statement at prepare time (SQLCODE -51), so it never ran
        # — and there is nothing to rebuild from an emptied database.
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    yield
    # And after: a module fixture or an unfixtured test that runs next starts from
    # what this test left, and a leftover ^NKG changes what the next BFS or
    # betweenness answers (4.1.1 gate).
    _iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Eraser", "EraseAll")
    iris_connection.commit()


@pytest.fixture(scope="function")
def arno_master_cleanup(arno_iris_connection):
    """Enterprise-side cleanup: wipe all tables and globals, same as iris_master_cleanup."""
    cursor = arno_iris_connection.cursor()
    _native = None
    try:
        # Graph_KG.docs is graph content as of 4.0.0 — it carries graph_id and its
        # `id` names a node — and Graph.KG.Eraser::EraseAll deletes it. This DELETE
        # is kept because a container may still hold a pre-4.0.0 Eraser, whose
        # inventory does not name the table; against a current one it is redundant
        # rather than wrong.
        with contextlib.suppress(Exception):
            cursor.execute("DELETE FROM Graph_KG.docs")
        with contextlib.suppress(Exception):
            arno_iris_connection.commit()

        # Everything else is Graph.KG.Eraser's (ADR-0004). This used to keep its
        # own list of eight tables and then kill ^KG/^NKG through
        # iris.createIRIS(arno_iris_connection) — which never ran: the
        # session-level createIRIS monkeypatch redirects only the *session*
        # connection, so the call reached the original, which rejects a dbapi
        # connection, inside contextlib.suppress. The tables were cleared and
        # the globals were not, which is the exact drift shape the Eraser exists
        # to end. See tests/integration/test_arno_master_cleanup.py.
        #
        # A dedicated native connection, opened and closed here: the monkeypatch
        # exists to keep the *session* connection away from the native API, and
        # this is not that connection.
        import iris as _iris

        _native = _iris.connect(
            hostname=arno_iris_connection.hostname,
            port=arno_iris_connection.port,
            namespace=arno_iris_connection.namespace,
            username="_SYSTEM",
            password="SYS",
        )
        _iris.createIRIS(_native).classMethodValue("Graph.KG.Eraser", "EraseAll")

        # `Do ##class(Graph.KG.Traversal).BuildKG()` used to sit here. IRIS SQL
        # rejects a Do statement at prepare time (SQLCODE -51), so it never ran
        # either — and there is nothing to rebuild from an emptied database.
        arno_iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
        if _native is not None:
            with contextlib.suppress(Exception):
                _native.close()
    yield


@pytest.fixture(scope="function")
def iris_cursor(iris_connection):
    cursor = iris_connection.cursor()
    with contextlib.suppress(Exception):
        cursor.execute("SET SCHEMA SQLUser")
    try:
        yield cursor
    finally:
        with contextlib.suppress(Exception):
            iris_connection.rollback()
        with contextlib.suppress(Exception):
            cursor.close()


@pytest.fixture(scope="function")
def clean_test_data(iris_connection):
    prefix = f"TEST_{uuid.uuid4().hex[:8]}:"
    yield prefix
    cursor = iris_connection.cursor()
    try:
        with contextlib.suppress(Exception):
            for t in ["kg_NodeEmbeddings", "rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
                # `node_id` on the embedding table since 4.0.0, and `Graph_KG.` because
                # an unqualified `kg_NodeEmbeddings` resolves to SQLUSER (SQLCODE -30).
                # Both failures were silent: the whole loop sits in a suppress().
                col = "node_id" if ("Emb" in t or t == "nodes") else "s"
                cursor.execute(
                    f"DELETE FROM Graph_KG.{t} WHERE {col} LIKE ?", (f"{prefix}%",)
                )
            iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "performance: mark test as performance benchmark")
    # 4.6.0 R309: the scratch namespaces the gated E2E files need, and with IVG_GATE=1 the skip gate
    from tests.e2e import gate

    gate.register(config)


def pytest_addoption(parser):
    pass


def pytest_collect_file(parent, file_path):
    FORBIDDEN_PLAIN = [
        "los-iris", "posos-iris", "aicore-iris", "aihub-iris",
        "opsreview-iris", "objectscript-coder", "iris-vector-graph-main",
    ]
    ATTACH_PATTERN = re.compile(r'IRISContainer\.attach\(["\']([^"\']+)["\']\)')

    if file_path.suffix == ".py" and file_path.stat().st_size > 0 and file_path.name != "conftest.py":
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            for forbidden in FORBIDDEN_PLAIN:
                if forbidden in content:
                    raise pytest.PytestCollectionWarning(
                        f"Forbidden container name '{forbidden}' in {file_path}. Use '{_GQS_CONTAINER}'."
                    )
            for match in ATTACH_PATTERN.finditer(content):
                name = match.group(1)
                if name not in (_GQS_CONTAINER, _ARNO_CONTAINER):
                    raise pytest.PytestCollectionWarning(
                        f"Wrong container IRISContainer.attach('{name}') in {file_path}. "
                        f"Use '{_GQS_CONTAINER}' (Community) or '{_ARNO_CONTAINER}' (Enterprise/Arno)."
                    )
        except (OSError, UnicodeDecodeError):
            pass
    return None


@pytest.fixture(scope="function")
def ledger_reset(iris_connection, iris_master_cleanup):
    """Spec 213: wipe ledger storage for isolation, on top of iris_master_cleanup.

    Kills ^IVG.Ledger and truncates Graph_KG.ledger_revisions / ledger_stats.
    Errors are ignored when the ledger classes are not yet deployed.
    """
    def _wipe():
        cursor = iris_connection.cursor()
        try:
            # Rebuild rdf_edges indices first: rows loaded with %NOINDEX leave phantom
            # entries that DELETE cannot see but a full SELECT (and genesis) can.
            with contextlib.suppress(Exception):
                cursor.execute("SELECT Graph_KG.rebuild_edge_indices()")
            for table in ["Graph_KG.ledger_revisions", "Graph_KG.ledger_stats"]:
                with contextlib.suppress(Exception):
                    cursor.execute(f"DELETE FROM {table}")
            with contextlib.suppress(Exception):
                iris_connection.commit()
            with contextlib.suppress(Exception):
                import iris as _iris
                import iris.dbapi as _tmp_dbapi
                _tmp_conn = _tmp_dbapi.connect(
                    hostname=iris_connection.hostname,
                    port=iris_connection.port,
                    namespace=iris_connection.namespace,
                    username="_SYSTEM",
                    password="SYS",
                )
                try:
                    _iris_obj = _iris.createIRIS(_tmp_conn)
                    try:
                        # bypasses the immutability triggers on ledger_revisions
                        _iris_obj.classMethodValue("Graph.KG.Ledger", "PurgeAll")
                    except Exception:
                        _iris_obj.kill("^IVG.Ledger")
                finally:
                    with contextlib.suppress(Exception):
                        _tmp_conn.close()
        finally:
            with contextlib.suppress(Exception):
                cursor.close()

    _wipe()
    yield
    _wipe()


@pytest.fixture(scope="function")
def node_graph_reset(iris_connection, iris_master_cleanup):
    """Spec 214: remove all named-graph rows before and after each test.

    Deletes all rows with graph_id != '' (non-default-graph) from the four
    structural tables in FK-safe order, then delegates to iris_master_cleanup
    for default-graph cleanup.

    COALESCE because graph_id is nullable and the default graph has two
    spellings: create_edge writes '', any INSERT omitting the column leaves NULL.
    A bare `graph_id <> ''` evaluates to unknown for the NULL rows and so happens
    to spare them, which is what this fixture wants — but only by accident. Said
    explicitly, it survives someone reading it as a bug and "fixing" it.
    """
    def _wipe_named_graphs():
        cursor = iris_connection.cursor()
        try:
            with contextlib.suppress(Exception):
                cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE COALESCE(graph_id, '') <> ''")
            with contextlib.suppress(Exception):
                cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s IN (SELECT node_id FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') <> '')")
            with contextlib.suppress(Exception):
                cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s IN (SELECT node_id FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') <> '')")
            with contextlib.suppress(Exception):
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') <> ''")
            with contextlib.suppress(Exception):
                iris_connection.commit()
        finally:
            with contextlib.suppress(Exception):
                cursor.close()

    _wipe_named_graphs()
    yield
    _wipe_named_graphs()


# --------------------------------------------------------------------------- spec 236
#
# A scratch namespace for the Arno callout adoption tests. `BuildKG` / `sync()` can
# leave the Native API process in USER (`Build2HopExactStats`, research R3), so every
# live write in spec 236 goes through `require_scratch`, which asserts a sentinel
# global that only exists in the scratch namespace. A handle that flipped to USER fails
# the assertion instead of erasing USER.
#
# 4.6.0 R287: the namespace is IVG_NS_IVG236, with no default. It was IVG236, written
# here, and the fixture deploys this checkout's classes and setup into it and its tests
# erase it, so one agent's run overwrote IVG236 for everyone (twice by 2026-11-04).

_SCRATCH_236_NS = os.environ.get("IVG_NS_IVG236", "")
_SCRATCH_236_SENTINEL = ("IVGScratch236Sentinel",)


class Scratch236:
    def __init__(self, host: str, port: int, namespace: str = _SCRATCH_236_NS):
        self._host, self._port = host, port
        self.namespace = namespace
        self._conns: list = []

    def _connect(self, namespace: str):
        import iris as _iris

        conn = _iris.connect(self._host, self._port, namespace, "_SYSTEM", "SYS")
        self._conns.append(conn)
        return conn

    def native(self, namespace: str | None = None):
        """A new Native API handle in `namespace` (default: the scratch namespace)."""
        import iris as _iris

        return _iris.createIRIS(self._connect(namespace or self.namespace))

    def sentinel_ok(self, native) -> bool:
        try:
            if str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) != self.namespace:
                return False
            return native.get(*_SCRATCH_236_SENTINEL) == self.namespace
        except Exception:
            return False

    def require_scratch(self, native) -> None:
        assert self.sentinel_ok(native), (
            f"handle is not in {self.namespace} (sentinel missing): refusing to "
            "write or erase. BuildKG/sync can leave the process in USER."
        )

    def reconnect_after_sync(self, _stale=None):
        """A fresh handle after `BuildKG` / `sync()`; the stale one is not trusted."""
        fresh = self.native()
        self.require_scratch(fresh)
        return fresh

    def dbapi(self):
        """A DB-API connection in the scratch namespace (for the engine and SQL)."""
        import iris.dbapi as _dbapi

        conn = _dbapi.connect(
            hostname=self._host, port=self._port, namespace=self.namespace,
            username="_SYSTEM", password="SYS",
        )
        self._conns.append(conn)
        return conn

    def close(self):
        for conn in self._conns:
            with contextlib.suppress(Exception):
                conn.close()
        self._conns.clear()


def _scratch_236_endpoint(container: str) -> tuple[str, int]:
    import socket as _socket

    try:
        return _socket.gethostbyname(f"{container}.orb.local"), 1972
    except _socket.gaierror:
        return "localhost", int(os.environ.get("IVG_PORT", "31972"))


@pytest.fixture(scope="session")
def ivg_236_scratch():
    """The scratch namespace IVG_NS_IVG236 names, on the enterprise container: this checkout's classes deployed, IVG
    set up (initialize_schema: CY_TYPED and the other setup functions), the sentinel set. Its tests erase it.

    Skipped unless IVG_NS_IVG236 names a namespace the caller owns (4.6.0 R287): there is no default, and USER and
    the % namespaces are refused. A namespace that does not exist is created (scripts/tck/setup_namespace.sh)."""
    import iris as _iris

    ns = os.environ.get("IVG_NS_IVG236", "").strip()
    if not ns:
        pytest.skip("IVG_NS_IVG236 is not set: name a scratch namespace you own (this fixture deploys this checkout's "
                    "classes and setup into it, and its tests erase it)")
    if ns.upper() == "USER" or ns.startswith("%"):
        pytest.skip(f"IVG_NS_IVG236={ns} is a shared namespace: name a scratch namespace you own")
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    if not container_state_is_running(docker_container_state(container)):
        pytest.skip(f"{container} not running. Start with: scripts/enterprise-container.sh up")
    host, port = _scratch_236_endpoint(container)
    scratch = Scratch236(host, port, ns)
    try:
        try:
            probe = scratch._connect(ns)
        except Exception:
            subprocess.run(
                ["bash", "scripts/tck/setup_namespace.sh", ns, "--create"],
                check=True, capture_output=True, text=True, timeout=900,
                env={**os.environ, "IVG_TEST_CONTAINER": container},
            )
            probe = scratch._connect(ns)
        from iris_vector_graph import IRISGraphEngine
        from iris_vector_graph._engine import class_deploy as _cd

        result = _cd.deploy_packaged_classes(probe)
        errors = list(result.errors if result is not None else [])
        if errors:
            raise RuntimeError(f"{ns} class deploy failed: {errors}")
        # the setup functions the translated SQL calls (GRAPH_KG.CY_TYPED does not exist without them, R287)
        IRISGraphEngine(scratch.dbapi(), embedding_dimension=SESSION_EMBEDDING_DIM, namespace=ns).initialize_schema(
            auto_deploy_objectscript=False)
        _iris.createIRIS(probe).set(ns, *_SCRATCH_236_SENTINEL)
        # Point the scratch namespace at the library the shared one loaded, so
        # ArnoAccel.IsAvailable loads the same build here. ^ArnoAccel is per database.
        lib = scratch.native(namespace="USER").get("ArnoAccel", "lib_path")
        if lib:
            scratch.native().set(lib, "ArnoAccel", "lib_path")
        yield scratch
    finally:
        scratch.close()
