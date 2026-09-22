import contextlib
import logging
import os
import re
import subprocess
import uuid

import pytest

logger = logging.getLogger(__name__)

_GQS_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")

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


def _deploy_objectscript(container_name: str) -> None:
    subprocess.run(
        ["docker", "exec", container_name, "mkdir", "-p", "/tmp/src"],
        capture_output=True,
    )
    subprocess.run(
        ["docker", "cp", "iris_src/src/.", f"{container_name}:/tmp/src/"],
        capture_output=True,
    )
    # Load each .cls file individually with "ck-d" (no background workers).
    # Community Edition is limited to 2 CPU cores; LoadDir with parallel compilation
    # fails with ERROR #7802 on machines with many cores because the IRIS work queue
    # manager spawns more background jobs than the CE license allows.
    # Loading files one at a time avoids the worker queue entirely.
    import glob as _glob
    cls_files = sorted(_glob.glob("iris_src/src/**/*.cls", recursive=True))
    for cls_file in cls_files:
        # Get the container path relative to /tmp/src/
        rel = os.path.relpath(cls_file, "iris_src/src").replace(os.sep, "/")
        subprocess.run(
            ["docker", "exec", "-i", container_name, "iris", "session", "IRIS", "-U", "USER"],
            input=f'Do $system.OBJ.Load("/tmp/src/{rel}","ck-d")\nH\n',
            capture_output=True, text=True, timeout=30,
        )


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

    def __init__(self, open_native, on_reconnect=None):
        self._open_native = open_native
        self._on_reconnect = on_reconnect
        self._native = open_native()

    def __getattr__(self, name):
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


@pytest.fixture(scope="session")
def iris_test_container():
    from iris_devtester import IRISContainer
    import subprocess as _sp

    # Candidate names to try in order before starting a new container.
    # "iris_vector_graph" is the docker-compose service name; it's a valid
    # Community container even though it differs from the default fixture name.
    _FALLBACK_NAMES = ["iris_vector_graph"]

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
                hostname=_orb_ip, port=1972, namespace="USER",
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
                hostname=cip, port=1972, namespace="USER",
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
                hostname="localhost", port=_IVG_PORT, namespace="USER",
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
                    _open_native_iris_object, on_reconnect=_discard_dead_native_conn
                )
            except Exception as _e:
                logger.warning("Could not create native conn for session isolation: %s", _e)
        return _original_createIRIS(target_conn)

    _iris_module.createIRIS = _safe_createIRIS

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
        eng.initialize_schema(auto_deploy_objectscript=False)
    except Exception as e:
        logger.warning("Schema init failed (may already exist): %s", e)

    # Hand the connection to the per-test width guard, which must not request this
    # fixture: requesting it would open the connection for every unit test.
    _RESOLVED_SESSION_CONN[0] = conn

    yield conn

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
    for the whole namespace, and `initialize_schema()` alters it to the calling
    engine's width whenever the table is empty. Live tests legitimately build
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
            ).initialize_schema(auto_deploy_objectscript=False)
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
_ARNO_PORT = int(os.environ.get("IVG_ARNO_PORT", "31971"))


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
        hostname=hostname, port=port, namespace="USER",
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

    yield conn
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


from iris_vector_graph.utils import _split_sql_statements  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "performance: mark test as performance benchmark")


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
