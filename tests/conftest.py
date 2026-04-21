"""
Managed test container infrastructure for IRIS Vector Graph.
"""

import logging
import os
import subprocess
import time

import pytest

try:
    from iris_devtester.utils.dbapi_compat import get_connection as iris_connect

    _HAS_DEVTESTER = True
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

    _HAS_DEVTESTER = False

logger = logging.getLogger(__name__)

TEST_CONTAINER_IMAGE = "intersystemsdc/iris-community:latest-em"


def _apply_aggressive_password_reset(container_name: str) -> bool:
    """Create/reset the test user using single-line ObjectScript commands.

    IRIS terminal (iris session) treats each piped line as a separate command —
    multi-line If/For blocks cause <SYNTAX> errors.  All statements here are
    intentionally written as single-line routines to avoid that.
    """
    logger.info(
        f"Applying password reset and creating test user in {container_name}..."
    )

    # Each line is a self-contained single-line ObjectScript statement.
    # We use $ZCVT and a single Do with a chained method sequence.
    single_line_commands = [
        # Create test user if not already present (sc=1 means already exists)
        'Set sc = $SELECT(##class(Security.Users).Exists("test"):1, 1:##class(Security.Users).Create("test","%ALL","test","Test User",,,,0,1))',
        # Ensure password never expires and no forced change (comma-separated Set = single line)
        'Set u=##class(Security.Users).%OpenId("test") Set u.PasswordNeverExpires=1,u.ChangePassword=0 Do u.%Save()',
    ]
    script = "\n".join(single_line_commands) + "\nH\n"

    exec_cmd = [
        "docker",
        "exec",
        "-i",
        container_name,
        "iris",
        "session",
        "iris",
        "-U",
        "%SYS",
    ]

    for i in range(5):
        try:
            result = subprocess.run(
                exec_cmd,
                input=script,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
            # returncode 0 = clean exit; ignore <SYNTAX> noise in stderr
            logger.debug(f"Password reset stdout: {result.stdout[-500:]}")
            logger.info("Password reset completed.")
            return True
        except Exception as e:
            logger.debug(f"Password reset attempt {i + 1} failed: {e}")
        time.sleep(2)

    return False


def _setup_iris_container(container_name: str) -> bool:
    """Unified setup using Direct Pipe method for maximum stability.
    Separate SQL and ObjectScript execution for reliability.
    """
    try:
        logger.info(f"Starting Robust IRIS setup for container: {container_name}")

        # 0. Aggressive password reset
        _apply_aggressive_password_reset(container_name)

        # 1. Prepare directory in container
        subprocess.run(
            ["docker", "exec", container_name, "mkdir", "-p", "/tmp/src"],
            capture_output=True,
        )

        logger.info("Copying source files to container...")
        subprocess.run(
            ["docker", "cp", "iris_src/src/.", f"{container_name}:/tmp/src/"],
            check=True,
        )
        for conflicting_cls in ["Edge.cls", "TestEdge.cls"]:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "rm",
                    "-f",
                    f"/tmp/src/{conflicting_cls}",
                ],
                capture_output=True,
            )

        # 2. Schema and Views via SQL
        # We use ExecDirect from ObjectScript to avoid shell transition issues
        sql_script = """
Set stmt = ##class(%SQL.Statement).%New()
Do stmt.%Prepare("CREATE SCHEMA Graph_KG")
Do stmt.%Execute()

Do stmt.%Prepare("DROP VIEW SQLUser.kg_NodeEmbeddings")
Do stmt.%Execute()
Do stmt.%Prepare("DROP TABLE Graph_KG.kg_NodeEmbeddings_optimized")
Do stmt.%Execute()
Do stmt.%Prepare("DROP TABLE Graph_KG.kg_NodeEmbeddings")
Do stmt.%Execute()

Do stmt.%Prepare("CREATE TABLE Graph_KG.nodes(node_id VARCHAR(256) %EXACT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.rdf_labels(s VARCHAR(256) %EXACT NOT NULL, label VARCHAR(128) %EXACT NOT NULL, CONSTRAINT pk_labels PRIMARY KEY (s, label), CONSTRAINT fk_labels_node FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id))")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.rdf_props(s VARCHAR(256) %EXACT NOT NULL, key VARCHAR(128) %EXACT NOT NULL, val VARCHAR(4000) %EXACT, CONSTRAINT pk_props PRIMARY KEY (s, key))")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.rdf_edges(edge_id BIGINT IDENTITY PRIMARY KEY, s VARCHAR(256) %EXACT NOT NULL, p VARCHAR(128) %EXACT NOT NULL, o_id VARCHAR(256) %EXACT NOT NULL, qualifiers %Library.DynamicObject, graph_id VARCHAR(256) %EXACT NULL, CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id), CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES Graph_KG.nodes(node_id), CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id))")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.kg_NodeEmbeddings (id VARCHAR(256) %EXACT PRIMARY KEY, emb VECTOR(DOUBLE), metadata %Library.DynamicObject, CONSTRAINT fk_emb_node FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id))")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.kg_NodeEmbeddings_optimized (id VARCHAR(256) %EXACT PRIMARY KEY, emb VECTOR(DOUBLE), metadata %Library.DynamicObject, CONSTRAINT fk_emb_node_opt FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id))")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE TABLE Graph_KG.docs(id VARCHAR(256) %EXACT PRIMARY KEY, text VARCHAR(4000) %EXACT)")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges (o_id)")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE INDEX idx_edges_graph_id ON Graph_KG.rdf_edges (graph_id)")
Do stmt.%Execute()

Do stmt.%Prepare("CREATE VIEW SQLUser.nodes AS SELECT node_id, created_at FROM Graph_KG.nodes")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW SQLUser.rdf_labels AS SELECT * FROM Graph_KG.rdf_labels")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW SQLUser.rdf_props AS SELECT * FROM Graph_KG.rdf_props")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW SQLUser.rdf_edges AS SELECT * FROM Graph_KG.rdf_edges")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW SQLUser.kg_NodeEmbeddings AS SELECT * FROM Graph_KG.kg_NodeEmbeddings")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW SQLUser.docs AS SELECT * FROM Graph_KG.docs")
Do stmt.%Execute()

Do stmt.%Prepare("GRANT ALL PRIVILEGES ON Graph_KG.nodes TO test")
Do stmt.%Execute()
Do stmt.%Prepare("GRANT ALL PRIVILEGES ON Graph_KG.rdf_edges TO test")
Do stmt.%Execute()
Do stmt.%Prepare("GRANT ALL PRIVILEGES ON Graph_KG.rdf_labels TO test")
Do stmt.%Execute()
Do stmt.%Prepare("GRANT ALL PRIVILEGES ON Graph_KG.rdf_props TO test")
Do stmt.%Execute()
Do stmt.%Prepare("GRANT ALL PRIVILEGES ON Graph_KG.kg_NodeEmbeddings TO test")
Do stmt.%Execute()

H
"""
        logger.info("Executing robust schema setup via ObjectScript ExecDirect...")
        os_cmd = [
            "docker",
            "exec",
            "-i",
            container_name,
            "iris",
            "session",
            "IRIS",
            "-U",
            "USER",
        ]
        subprocess.run(
            os_cmd, input=sql_script, capture_output=True, text=True, errors="replace"
        )

        # 3. Load Classes
        load_cmd = 'Do $system.OBJ.LoadDir("/tmp/src", "ck", .errors, 1)\nH\n'
        subprocess.run(
            os_cmd, input=load_cmd, capture_output=True, text=True, errors="replace"
        )

        logger.info("Robust IRIS setup completed.")
        return True
    except Exception as e:
        logger.error(f"IRIS setup failed: {e}", exc_info=True)
        return False


@pytest.fixture(scope="session")
def iris_test_container():
    """Session-scoped managed IRIS container.

    If a container named 'iris-vector-graph-main' is already running, attaches
    to it via IRISContainer.attach() — no MockContainer, no hardcoded ports.
    Otherwise starts a fresh container.  Either way, blocks until test credentials
    are verified before yielding so that module-scoped iris_connection fixtures
    never race on a not-yet-ready container.
    """
    container_name = "iris-vector-graph-main"

    if not _HAS_DEVTESTER:
        yield type(
            "Container",
            (),
            {
                "get_exposed_port": lambda self, p: 1972,
                "container_name": container_name,
            },
        )()
        return

    from iris_devtester.containers.iris_container import IRISContainer

    # ── Attach to existing container if it's already running ──────────────────
    is_running = False
    container_exists = False
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            container_exists = True
            is_running = result.stdout.strip() == "true"
    except Exception:
        pass

    needs_setup = True
    if is_running:
        logger.info(f"Attaching to existing running container: {container_name}")
        container = IRISContainer.attach(container_name)
        needs_setup = True  # always run setup; DDL is idempotent (CREATE TABLE is ignored if exists)
    elif container_exists:
        logger.info(f"Starting stopped container: {container_name}")
        subprocess.run(["docker", "start", container_name], capture_output=True)
        import time as _time
        _time.sleep(10)
        container = IRISContainer.attach(container_name)
        needs_setup = True
    else:
        # ── Start a fresh container ────────────────────────────────────────────
        logger.info(f"Starting fresh IRIS container: {container_name}")
        container = IRISContainer(image=TEST_CONTAINER_IMAGE, name=container_name)
        container.start()
        container.wait_for_ready(timeout=180)
        container_name = container.get_container_name()
        needs_setup = True

    # ── Password reset (always) ───────────────────────────────────────────────
    _apply_aggressive_password_reset(container_name)

    if not is_running:
        # Fresh container: wait for IRIS to fully stabilise before setup
        logger.info("Fresh container: waiting 20s for IRIS stabilisation...")
        time.sleep(20)

    if not _setup_iris_container(container_name):
        logger.error("IRIS robust setup failed - tests may fail.")

    # ── Block until test credentials actually work ────────────────────────────
    assigned_port = container.get_exposed_port(1972)
    logger.info(f"Verifying test credentials on port {assigned_port}...")
    for attempt in range(20):  # up to 100 s
        try:
            _verify_conn = iris_connect(
                "localhost", int(assigned_port), "USER", "test", "test"
            )
            cur = _verify_conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            _verify_conn.close()
            logger.info(
                f"Container ready for tests (verified on attempt {attempt + 1})"
            )
            break
        except Exception as e:
            logger.debug(f"Credential verify attempt {attempt + 1}/20: {e}")
            _apply_aggressive_password_reset(container_name)
            time.sleep(5)
    else:
        raise RuntimeError(
            f"IRIS container '{container_name}' never accepted test credentials after 100 s. "
            "Cannot proceed — fix the container setup before running tests."
        )

    yield container

    if not is_running:
        # Only stop containers we started; leave pre-existing ones running
        container.stop()


@pytest.fixture(scope="function")
def iris_master_cleanup(iris_connection):
    """Ensure a clean state at the start of each test."""
    cursor = iris_connection.cursor()
    # T013: Aggressively cleanup all graph tables
    tables = [
        "Graph_KG.rdf_edges",
        "Graph_KG.rdf_labels",
        "Graph_KG.rdf_props",
        "Graph_KG.kg_NodeEmbeddings",
        "Graph_KG.kg_NodeEmbeddings_optimized",
        "Graph_KG.nodes",
        "Graph_KG.docs",
    ]
    for table in tables:
        try:
            cursor.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    # Reset KG global if possible
    try:
        cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
    except:
        pass
    iris_connection.commit()
    yield


@pytest.fixture(scope="module")
def iris_connection(iris_test_container):
    """Module-scoped IRIS connection using the assigned port."""
    assigned_port = iris_test_container.get_exposed_port(1972)
    container_name = iris_test_container.get_container_name()
    logger.info(f"Connecting to IRIS on port {assigned_port}...")

    conn = None
    for attempt in range(6):
        try:
            conn = iris_connect(
                "localhost",
                int(assigned_port),
                "USER",
                "test",
                "test",
            )
            break
        except Exception as e:
            if attempt < 5 and any(
                k in str(e)
                for k in (
                    "Password change required",
                    "Access Denied",
                    "Authentication failed",
                )
            ):
                logger.warning(
                    f"Connection attempt {attempt + 1} failed: {e}. Retrying password reset..."
                )
                _apply_aggressive_password_reset(container_name)
                time.sleep(5)
            else:
                logger.error(f"Failed to connect to IRIS on attempt {attempt + 1}: {e}")
                if attempt == 5:
                    raise

    from iris_vector_graph.schema import GraphSchema
    try:
        cursor_m = conn.cursor()
        GraphSchema.add_graph_id_column(cursor_m)
        GraphSchema.update_spo_unique_constraint(cursor_m)
        GraphSchema.add_graph_id_index(cursor_m)
        conn.commit()
    except Exception:
        pass

    yield conn
    if conn:
        conn.close()


@pytest.fixture(scope="function")
def iris_cursor(iris_connection):
    """Function-scoped IRIS cursor with default schema set."""
    cursor = iris_connection.cursor()
    try:
        cursor.execute("SET SCHEMA SQLUser")
    except Exception as e:
        logger.warning(f"Failed to set default schema SQLUser: {e}")
    yield cursor
    import contextlib

    with contextlib.suppress(Exception):
        iris_connection.rollback()


@pytest.fixture(scope="function")
def clean_test_data(iris_connection):
    """Provides a unique prefix for test data and cleans it up after."""
    import uuid

    prefix = f"TEST_{uuid.uuid4().hex[:8]}:"
    yield prefix
    cursor = iris_connection.cursor()
    import contextlib

    with contextlib.suppress(Exception):
        for t in ["kg_NodeEmbeddings", "rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
            col = "id" if "Emb" in t else "node_id" if t == "nodes" else "s"
            cursor.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (f"{prefix}%",))
        iris_connection.commit()


from iris_vector_graph.utils import _split_sql_statements


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_database: mark test as requiring live IRIS database"
    )
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line(
        "markers", "performance: mark test as performance benchmark"
    )


def pytest_addoption(parser):
    """Add command line options."""
    pass


def pytest_collect_file(parent, file_path):
    """Guard: fail collection if any test file references a forbidden IRIS container name.

    Checks two patterns:
      1. Plain forbidden names that must never appear (other projects' containers).
      2. IRISContainer.attach("wrong-name") — catches stale attach() calls.

    The canonical container for this project is: iris-vector-graph-main
    """
    import re

    # These strings must not appear anywhere in test files — they are other projects' containers.
    FORBIDDEN_PLAIN = [
        "los-iris",
        "posos-iris",
        "aicore-iris",
        "aihub-iris",
        "opsreview-iris",
        "objectscript-coder",
    ]

    # IRISContainer.attach("X") where X is NOT our container.
    ATTACH_PATTERN = re.compile(r'IRISContainer\.attach\(["\']([^"\']+)["\']\)')
    CANONICAL_CONTAINER = "iris-vector-graph-main"

    if (
        file_path.suffix == ".py"
        and file_path.stat().st_size > 0
        and file_path.name != "conftest.py"
    ):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")

            for forbidden in FORBIDDEN_PLAIN:
                if forbidden in content:
                    raise pytest.PytestCollectionWarning(
                        f"Forbidden container name '{forbidden}' found in {file_path}. "
                        f"This container belongs to another project. "
                        f"Use '{CANONICAL_CONTAINER}' instead."
                    )

            for match in ATTACH_PATTERN.finditer(content):
                name = match.group(1)
                if name != CANONICAL_CONTAINER:
                    raise pytest.PytestCollectionWarning(
                        f"Wrong container in IRISContainer.attach('{name}') in {file_path}. "
                        f"Use IRISContainer.attach('{CANONICAL_CONTAINER}') instead."
                    )

        except (OSError, UnicodeDecodeError):
            pass
    return None
