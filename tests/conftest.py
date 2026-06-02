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
    load_cmd = (
        'Do $system.OBJ.Compile("Graph.KG.GraphIndex","ck-d")\n'
        'Do $system.OBJ.LoadDir("/tmp/src","ck",.err,1)\n'
        'Do $system.OBJ.Compile("Graph.KG.Edge","ck-d")\n'
        'Do $system.OBJ.Compile("Graph.KG.TestEdge","ck-d")\n'
        'H\n'
    )
    subprocess.run(
        ["docker", "exec", "-i", container_name, "iris", "session", "IRIS", "-U", "USER"],
        input=load_cmd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    try:
        from iris_devtester import IRISContainer as _IRC
        _c = _IRC.attach(container_name); _c._connection = None
        _conn = _c.get_connection()
        from iris_vector_graph import IRISGraphEngine as _E
        _E(_conn, embedding_dimension=128).initialize_schema()
        for _cls in [
            "Graph.KG.TraversalBuild", "Graph.KG.TraversalBFS",
            "Graph.KG.TraversalPaths", "Graph.KG.TraversalKHop", "Graph.KG.Traversal",
            "Graph.KG.NKGAccelLoader", "Graph.KG.NKGAccelAdjacency",
            "Graph.KG.NKGAccelTraversal", "Graph.KG.NKGAccelCentrality", "Graph.KG.NKGAccel",
        ]:
            subprocess.run(
                ["docker", "exec", "-i", container_name, "iris", "session", "IRIS", "-U", "USER"],
                input=f'Do $system.OBJ.Compile("{_cls}","cdk")\nH\n',
                capture_output=True, text=True, timeout=30,
            )
        _conn.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def iris_test_container():
    from iris_devtester import IRISContainer

    attached = False
    try:
        container = IRISContainer.attach(_GQS_CONTAINER)
        attached = True
        logger.info("Attached to existing container: %s", _GQS_CONTAINER)
    except Exception:
        import subprocess as _sp
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

    if cip:
        import iris.dbapi as _dbapi
        conn = _dbapi.connect(
            hostname=cip, port=1972, namespace="USER",
            username="_SYSTEM", password="SYS",
        )
        logger.info("Connected to %s via container IP %s:1972 (avoids SSH tunnel conflicts)", container_name, cip)
    else:
        # No Docker IP (e.g. OrbStack env). Use iris_devtester for password-reset-safe
        # connection, then verify we're NOT on los-iris (SSH tunnel on localhost:1972/21972).
        try:
            from iris_devtester import IRISContainer as _IRC
            _fresh = _IRC.attach(container_name)
            _fresh._connection = None
            conn = _fresh.get_connection()
            _c = conn.cursor()
            _c.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name='Graph.KG.LOSBriefingJob'")
            _los_count = _c.fetchone()[0]
            if _los_count > 0:
                raise RuntimeError(
                    f"localhost:{_IVG_PORT} is los-iris (SSH tunnel), NOT ivg-iris. "
                    "Stop the SSH tunnel or configure ivg-iris on a different host port."
                )
            logger.info("Connected to %s via iris_devtester fallback (OrbStack env)", container_name)
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("Could not connect to %s: %s", container_name, e)
            raise

    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    with contextlib.suppress(Exception):
        cur = conn.cursor()
        GraphSchema.add_graph_id_column(cur)
        GraphSchema.update_spo_unique_constraint(cur)
        GraphSchema.add_graph_id_index(cur)
        conn.commit()

    try:
        eng = IRISGraphEngine(conn, embedding_dimension=128)
        eng.initialize_schema(auto_deploy_objectscript=True)
    except Exception as e:
        logger.warning("Schema init failed (may already exist): %s", e)

    yield conn
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

    if cip:
        import iris.dbapi as _dbapi
        conn = _dbapi.connect(
            hostname=cip, port=1972, namespace="USER",
            username="_SYSTEM", password="SYS",
        )
    else:
        from iris_devtester import IRISContainer as _IRC
        _fresh = _IRC.attach(_ARNO_CONTAINER)
        _fresh._connection = None
        conn = _fresh.get_connection()

    _c = conn.cursor()
    _c.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name='Graph.KG.LOSBriefingJob'")
    if _c.fetchone()[0] > 0:
        raise RuntimeError(
            f"{_ARNO_CONTAINER} appears to be los-iris. "
            "Wrong container — check port and container name."
        )

    from iris_vector_graph.engine import IRISGraphEngine
    try:
        IRISGraphEngine(conn, embedding_dimension=128).initialize_schema()
    except Exception as e:
        logger.warning("arno container schema init: %s", e)

    yield conn
    conn.close()


@pytest.fixture(scope="function")
def iris_master_cleanup(iris_connection):
    cursor = iris_connection.cursor()
    for table in [
        "Graph_KG.rdf_edges",
        "Graph_KG.rdf_labels",
        "Graph_KG.rdf_props",
        "Graph_KG.kg_NodeEmbeddings",
        "Graph_KG.kg_NodeEmbeddings_optimized",
        "Graph_KG.nodes",
        "Graph_KG.docs",
    ]:
        with contextlib.suppress(Exception):
            cursor.execute(f"DELETE FROM {table}")
    with contextlib.suppress(Exception):
        iris_connection.commit()
    with contextlib.suppress(Exception):
        import iris as _iris
        _iris_obj = _iris.createIRIS(iris_connection)
        _iris_obj.kill("^KG")
        _iris_obj.kill("^NKG")
    with contextlib.suppress(Exception):
        cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
    iris_connection.commit()
    yield


@pytest.fixture(scope="function")
def iris_cursor(iris_connection):
    cursor = iris_connection.cursor()
    with contextlib.suppress(Exception):
        cursor.execute("SET SCHEMA SQLUser")
    yield cursor
    with contextlib.suppress(Exception):
        iris_connection.rollback()


@pytest.fixture(scope="function")
def clean_test_data(iris_connection):
    prefix = f"TEST_{uuid.uuid4().hex[:8]}:"
    yield prefix
    cursor = iris_connection.cursor()
    with contextlib.suppress(Exception):
        for t in ["kg_NodeEmbeddings", "rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
            col = "id" if "Emb" in t else "node_id" if t == "nodes" else "s"
            cursor.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (f"{prefix}%",))
        iris_connection.commit()


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
