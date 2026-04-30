import contextlib
import logging
import re
import subprocess
import time
import uuid

import pytest

try:
    from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
except ImportError:
    import iris

    def iris_connect(host, port, namespace, user, password):
        return iris.connect(
            hostname=host,
            port=port,
            namespace=namespace,
            username=user,
            password=password,
        )

logger = logging.getLogger(__name__)

_GQS_CONTAINER = "gqs-ivg-test"
_GQS_PORT = 1972


def _ensure_test_user() -> None:
    cmds = [
        'Set sc = $SELECT(##class(Security.Users).Exists("test"):1, 1:##class(Security.Users).Create("test","%ALL","test","Test User",,,,0,1))',
        'Set u=##class(Security.Users).%OpenId("test") Set u.PasswordNeverExpires=1,u.ChangePassword=0 Do u.%Save()',
    ]
    script = "\n".join(cmds) + "\nH\n"
    subprocess.run(
        ["docker", "exec", "-i", _GQS_CONTAINER, "iris", "session", "iris", "-U", "%SYS"],
        input=script,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )


def _deploy_objectscript() -> None:
    subprocess.run(
        ["docker", "exec", _GQS_CONTAINER, "mkdir", "-p", "/tmp/src"],
        capture_output=True,
    )
    subprocess.run(
        ["docker", "cp", "iris_src/src/.", f"{_GQS_CONTAINER}:/tmp/src/"],
        capture_output=True,
    )
    for cls in ["Edge.cls", "TestEdge.cls"]:
        subprocess.run(
            ["docker", "exec", _GQS_CONTAINER, "rm", "-f", f"/tmp/src/{cls}"],
            capture_output=True,
        )
    load_cmd = 'Do $system.OBJ.LoadDir("/tmp/src","ck",.err,1)\nH\n'
    subprocess.run(
        ["docker", "exec", "-i", _GQS_CONTAINER, "iris", "session", "IRIS", "-U", "USER"],
        input=load_cmd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


@pytest.fixture(scope="session")
def iris_test_container():
    _ensure_test_user()
    _deploy_objectscript()

    class _Stub:
        def get_exposed_port(self, _p):
            return _GQS_PORT

        def get_container_name(self):
            return _GQS_CONTAINER

    yield _Stub()


@pytest.fixture(scope="module")
def iris_connection(iris_test_container):
    conn = iris_connect("localhost", _GQS_PORT, "USER", "test", "test")

    from iris_vector_graph.schema import GraphSchema
    with contextlib.suppress(Exception):
        cur = conn.cursor()
        GraphSchema.add_graph_id_column(cur)
        GraphSchema.update_spo_unique_constraint(cur)
        GraphSchema.add_graph_id_index(cur)
        conn.commit()

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
                if name != _GQS_CONTAINER:
                    raise pytest.PytestCollectionWarning(
                        f"Wrong container IRISContainer.attach('{name}') in {file_path}. Use '{_GQS_CONTAINER}'."
                    )
        except (OSError, UnicodeDecodeError):
            pass
    return None
