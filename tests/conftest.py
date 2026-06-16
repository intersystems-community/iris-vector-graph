import contextlib
import logging
import os
import re
import subprocess
import uuid

import pytest

logger = logging.getLogger(__name__)

_GQS_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris")


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

    # 2. If not found, skip rather than start a new container —
    #    unless IVG_AUTO_START_CONTAINER=1 (default when running in CI).
    if container is None:
        auto_start = os.environ.get("IVG_AUTO_START_CONTAINER", "0") not in ("0", "false", "no")
        if not auto_start:
            pytest.skip(
                f"IRIS container '{_GQS_CONTAINER}' not running. "
                f"Start with: scripts/test-container.sh up  "
                f"(or set IVG_TEST_CONTAINER=ivg-iris-enterprise for enterprise container)"
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

    _IVG_PORT = int(os.environ.get("IVG_PORT", "21972"))

    conn = None

    # Try container IP first (Linux Docker where container IPs are routable from host).
    # On macOS Docker Desktop / OrbStack the container IP is NOT routable from the host,
    # so we catch the connection error and fall through to the iris_devtester path.
    if cip:
        import iris.dbapi as _dbapi
        try:
            conn = _dbapi.connect(
                hostname=cip, port=1972, namespace="USER",
                username="_SYSTEM", password="SYS",
            )
            logger.info("Connected to %s via container IP %s:1972", container_name, cip)
        except Exception as _e:
            logger.info(
                "Container IP %s:1972 not routable from host (%s) — falling back to iris_devtester",
                cip, _e,
            )
            conn = None

    if conn is None:
        # iris_devtester path: works on macOS Docker Desktop, OrbStack, and Linux.
        # Also verifies we're NOT accidentally hitting los-iris via an SSH tunnel.
        try:
            from iris_devtester import IRISContainer as _IRC
            _fresh = _IRC.attach(container_name)
            _fresh._connection = None
            conn = _fresh.get_connection()
            _c = conn.cursor()
            try:
                _c.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name='Graph.KG.LOSBriefingJob'")
                _los_count = _c.fetchone()[0]
            finally:
                with contextlib.suppress(Exception):
                    _c.close()
            if _los_count > 0:
                raise RuntimeError(
                    f"localhost:{_IVG_PORT} is los-iris (SSH tunnel), NOT ivg-iris. "
                    "Stop the SSH tunnel or configure ivg-iris on a different host port."
                )
            logger.info("Connected to %s via iris_devtester", container_name)
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("Could not connect to %s: %s", container_name, e)
            raise

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

    def _get_or_open_native_conn():
        if _native_conn_holder[0] is None:
            try:
                import iris.dbapi as _dbapi2
                _native_conn_holder[0] = _dbapi2.connect(
                    hostname=conn.hostname,
                    port=conn.port,
                    namespace=conn.namespace,
                    username="_SYSTEM",
                    password="SYS",
                )
            except Exception as _e:
                logger.warning("Could not create native conn for session isolation: %s", _e)
        return _native_conn_holder[0]

    def _safe_createIRIS(target_conn):
        if target_conn is conn:
            native = _get_or_open_native_conn()
            if native is not None:
                return _original_createIRIS(native)
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
        eng = IRISGraphEngine(conn, embedding_dimension=128)
        eng.initialize_schema(auto_deploy_objectscript=False)
    except Exception as e:
        logger.warning("Schema init failed (may already exist): %s", e)

    yield conn

    _iris_module.createIRIS = _original_createIRIS
    with contextlib.suppress(Exception):
        if _native_conn_holder[0] is not None:
            _native_conn_holder[0].close()
    conn.close()


_ARNO_CONTAINER = os.environ.get("IVG_ARNO_CONTAINER", "ivg-iris-enterprise")
_ARNO_PORT = int(os.environ.get("IVG_ARNO_PORT", "31972"))


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

    hostname, port = "localhost", 21972  # fallback
    if cip and _tcp_reachable(cip, 1972):
        hostname, port = cip, 1972
    else:
        # Ask Docker for the host-mapped port for 1972/tcp
        port_out = _sp.run(
            ["docker", "port", _ARNO_CONTAINER, "1972/tcp"],
            capture_output=True, text=True,
        ).stdout.strip()
        # docker port output: "0.0.0.0:31972" or "[::]:31972" (possibly multiple lines)
        for line in port_out.splitlines():
            candidate = line.split(":")[-1].strip()
            if candidate.isdigit():
                hostname, port = "localhost", int(candidate)
                break

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
    # (lock on Graph.KG.Edge class definition during CREATE INDEX).
    _primary = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris")
    if _primary != _ARNO_CONTAINER:
        try:
            IRISGraphEngine(conn, embedding_dimension=128).initialize_schema()
        except Exception as e:
            logger.warning("arno container schema init: %s", e)

    yield conn
    conn.close()


@pytest.fixture(scope="function")
def iris_master_cleanup(iris_connection):
    cursor = iris_connection.cursor()
    try:
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.kg_NodeEmbeddings",
            "Graph_KG.kg_NodeEmbeddings_optimized",
            "Graph_KG.kg_EdgeEmbeddings",
            "Graph_KG.nodes",
            "Graph_KG.docs",
        ]:
            with contextlib.suppress(Exception):
                cursor.execute(f"DELETE FROM {table}")
        with contextlib.suppress(Exception):
            iris_connection.commit()
        with contextlib.suppress(Exception):
            # Use a short-lived *separate* connection for createIRIS so the
            # session-scoped iris_connection is never touched by the native API.
            # Calling createIRIS() on a connection and then executing cursor DDL
            # (DROP INDEX / CREATE INDEX) on the same connection permanently
            # corrupts the IRIS driver's parameter binding state for that connection.
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
                _iris_obj.kill("^KG")
                _iris_obj.kill("^NKG")
            finally:
                with contextlib.suppress(Exception):
                    _tmp_conn.close()
        with contextlib.suppress(Exception):
            cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    yield


@pytest.fixture(scope="function")
def arno_master_cleanup(arno_iris_connection):
    """Enterprise-side cleanup: wipe all tables and globals, same as iris_master_cleanup."""
    cursor = arno_iris_connection.cursor()
    try:
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.kg_NodeEmbeddings",
            "Graph_KG.kg_NodeEmbeddings_optimized",
            "Graph_KG.kg_EdgeEmbeddings",
            "Graph_KG.nodes",
            "Graph_KG.docs",
        ]:
            with contextlib.suppress(Exception):
                cursor.execute(f"DELETE FROM {table}")
        with contextlib.suppress(Exception):
            arno_iris_connection.commit()
        with contextlib.suppress(Exception):
            import iris as _iris
            _iris_obj = _iris.createIRIS(arno_iris_connection)
            _iris_obj.kill("^KG")
            _iris_obj.kill("^NKG")
        with contextlib.suppress(Exception):
            cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
        arno_iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
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
                col = "id" if "Emb" in t else "node_id" if t == "nodes" else "s"
                cursor.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (f"{prefix}%",))
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
