"""
Managed test container infrastructure for IRIS Vector Graph.
"""

import logging
import os
import subprocess
import time

try:
    import iris
except ImportError:
    iris = None

try:
    import irisnative
except ImportError:
    irisnative = None
import pytest

logger = logging.getLogger(__name__)

TEST_CONTAINER_IMAGE = "intersystemsdc/iris-community:latest-em"


def _apply_aggressive_password_reset(container_name: str) -> bool:
    """Aggressively clear password expiry flags via ObjectScript and create test user."""
    logger.info(f"Applying aggressive password reset and creating test user in {container_name}...")
    aggro_pwd_script = """
Set sc = ##class(Security.Users).UnExpireUser("_SYSTEM")
Set sc = ##class(Security.Users).UnExpireUser("SuperUser")
If '##class(Security.Users).Exists("test") {
    Set sc = ##class(Security.Users).Create("test", "%ALL", "test", "Test User", , , , 0, 1)
}
Set obj = ##class(Security.Users).%OpenId("test")
If $IsObject(obj) {
    Set obj.PasswordNeverExpires = 1
    Set obj.ChangePassword = 0
    Do obj.PasswordSet("test")
    Do obj.%Save()
}
For usr = "_SYSTEM", "SuperUser", "test" {
    Set obj = ##class(Security.Users).%OpenId(usr)
    If $IsObject(obj) {
        Set obj.PasswordNeverExpires = 1
        Set obj.ChangePassword = 0
        Do obj.%Save()
    }
}
H
"""
    exec_cmd = ['docker', 'exec', '-i', container_name, 'iris', 'session', 'iris', '-U', '%SYS']
    
    # Retry loop for initial setup
    for i in range(5):
        try:
            result = subprocess.run(exec_cmd, input=aggro_pwd_script, capture_output=True, text=True, errors='replace')
            if result.returncode == 0:
                logger.info("Aggressive password reset successful.")
                return True
        except Exception as e:
            logger.debug(f"Attempt {i+1} failed: {e}")
        
        logger.debug(f"Aggressive password reset attempt {i+1} failed, retrying in 2s...")
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
        subprocess.run(['docker', 'exec', container_name, 'mkdir', '-p', '/tmp/src'], capture_output=True)
        
        logger.info("Copying source files to container...")
        subprocess.run(['docker', 'cp', 'iris_src/src/.', f"{container_name}:/tmp/src/"], check=True)
        
        # 2. Schema and Views via SQL
        # We use ExecDirect from ObjectScript to avoid shell transition issues
        sql_script = """
Set stmt = ##class(%SQL.Statement).%New()
Do stmt.%Prepare("CREATE SCHEMA Graph_KG")
Do stmt.%Execute()
Do stmt.%Prepare("SET SCHEMA Graph_KG")
Do stmt.%Execute()

Set tables = ##class(%DynamicArray).%New()
Do tables.%Push("CREATE TABLE nodes(node_id VARCHAR(256) PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
Do tables.%Push("CREATE TABLE rdf_labels(s VARCHAR(256) NOT NULL, label VARCHAR(128) NOT NULL, CONSTRAINT pk_labels PRIMARY KEY (s, label), CONSTRAINT fk_labels_node FOREIGN KEY (s) REFERENCES nodes(node_id))")
Do tables.%Push("CREATE TABLE rdf_props(s VARCHAR(256) NOT NULL, key VARCHAR(128) NOT NULL, val VARCHAR(4000), CONSTRAINT pk_props PRIMARY KEY (s, key))")
Do tables.%Push("CREATE TABLE rdf_edges(edge_id BIGINT IDENTITY PRIMARY KEY, s VARCHAR(256) NOT NULL, p VARCHAR(128) NOT NULL, o_id VARCHAR(256) NOT NULL, qualifiers %Library.DynamicObject, CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES nodes(node_id), CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES nodes(node_id), CONSTRAINT u_spo UNIQUE (s, p, o_id))")
Do tables.%Push("CREATE TABLE kg_NodeEmbeddings (id VARCHAR(256) PRIMARY KEY, emb VECTOR(DOUBLE, 768), metadata %Library.DynamicObject, CONSTRAINT fk_emb_node FOREIGN KEY (id) REFERENCES nodes(node_id))")
Do tables.%Push("CREATE TABLE kg_NodeEmbeddings_optimized (id VARCHAR(256) PRIMARY KEY, emb VECTOR(DOUBLE, 768), metadata %Library.DynamicObject, CONSTRAINT fk_emb_node_opt FOREIGN KEY (id) REFERENCES nodes(node_id))")
Do tables.%Push("CREATE TABLE docs(id VARCHAR(256) PRIMARY KEY, text VARCHAR(4000))")
Do tables.%Push("CREATE INDEX idx_edges_oid ON rdf_edges (o_id)")

Set iter = tables.%GetIterator()
While iter.%GetNext(.key, .val) {
    Do stmt.%Prepare(val)
    Do stmt.%Execute()
}

-- Views
Do stmt.%Prepare("SET SCHEMA SQLUser")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW nodes AS SELECT node_id, created_at FROM Graph_KG.nodes")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW rdf_labels AS SELECT * FROM Graph_KG.rdf_labels")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW rdf_props AS SELECT * FROM Graph_KG.rdf_props")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW rdf_edges AS SELECT * FROM Graph_KG.rdf_edges")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW kg_NodeEmbeddings AS SELECT * FROM Graph_KG.kg_NodeEmbeddings")
Do stmt.%Execute()
Do stmt.%Prepare("CREATE VIEW docs AS SELECT * FROM Graph_KG.docs")
Do stmt.%Execute()

-- Grants
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
        os_cmd = ['docker', 'exec', '-i', container_name, 'iris', 'session', 'IRIS', '-U', 'USER']
        subprocess.run(os_cmd, input=sql_script, capture_output=True, text=True, errors='replace')

        # 3. Load Classes
        load_cmd = "Do \$system.OBJ.LoadDir(\"/tmp/src\", \"ck\", .errors, 1)\nH\n"
        subprocess.run(os_cmd, input=load_cmd, capture_output=True, text=True, errors='replace')

        logger.info("Robust IRIS setup completed.")
        return True
    except Exception as e:
        logger.error(f"IRIS setup failed: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"IRIS setup failed with exception: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"IRIS setup failed with exception: {e}", exc_info=True)
        return False


@pytest.fixture(scope="session")
def iris_test_container(request):
    """Session-scoped managed IRIS container."""
    use_existing = request.config.getoption("--use-existing-iris")
    container_name = "iris-vector-graph-main"
    
    from iris_devtester.containers.iris_container import IRISContainer
    from iris_devtester.ports import PortRegistry
    
    if use_existing:
        # Check if it's running
        try:
            result = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', container_name], 
                                    capture_output=True, text=True)
            if result.stdout.strip() == "true":
                logger.info(f"Using existing container: {container_name}")
                # Mock the container object
                class MockContainer:
                    def get_container_name(self): return container_name
                    def get_assigned_port(self): return 1972
                    def stop(self): pass
                    def start(self): pass
                
                container = MockContainer()
                _apply_aggressive_password_reset(container_name)
                _setup_iris_container(container_name)
                yield container
                return
        except Exception as e:
            logger.warning(f"Failed to use existing container: {e}")

    # Port Conflict Handling: Cleanup any existing containers with 'iris-test' in name
    try:
        ps = subprocess.run(['docker', 'ps', '-a', '--filter', 'name=iris-test', '--format', '{{.Names}}'], 
                            capture_output=True, text=True, errors='replace')
        for name in ps.stdout.splitlines():
            if name.strip():
                logger.info(f"Cleaning up existing container: {name}")
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    except Exception as e:
        logger.debug(f"Pre-startup cleanup skipped: {e}")

    # Initialize container
    container = IRISContainer(image=TEST_CONTAINER_IMAGE)
    container.start()
    
    # Wait for IRIS to be ready
    container.wait_for_ready(timeout=180)
    
    container_name = container.get_container_name()
    
    # Pre-emptive password reset before any connection attempt
    _apply_aggressive_password_reset(container_name)
    
    # Stability: 20-second sleep after ready signal
    logger.info("IRIS container reported ready. Waiting 20s for stabilization...")
    time.sleep(20)
    
    # Execute unified setup
    if not _setup_iris_container(container_name):
        logger.error("IRIS robust setup failed - tests may fail.")
    
    yield container
    container.stop()


@pytest.fixture(scope="function", autouse=True)
def iris_master_cleanup(iris_connection):
    """Ensure a clean state at the start of each test."""
    cursor = iris_connection.cursor()
    # T013: Aggressively cleanup all graph tables
    tables = [
        "Graph_KG.rdf_edges", "Graph_KG.rdf_labels", "Graph_KG.rdf_props", 
        "Graph_KG.kg_NodeEmbeddings", "Graph_KG.kg_NodeEmbeddings_optimized",
        "Graph_KG.nodes", "Graph_KG.docs"
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
    for attempt in range(3):
        try:
            # T013: Prefer irisnative for robust remote connectivity
            if irisnative:
                conn = irisnative.createConnection(
                    'localhost',
                    assigned_port,
                    'USER',
                    'test',
                    'test'
                )
            elif iris and hasattr(iris, 'connect'):
                # Use getattr to avoid shadowing issues
                connect_fn = getattr(iris, 'connect')
                conn = connect_fn(
                    hostname='localhost',
                    port=assigned_port,
                    namespace='USER',
                    username='test',
                    password='test'
                )

            else:
                raise ImportError("Neither iris.connect nor irisnative available for connection")
            
            # T013: Ensure Graph_KG schema is used
            cursor = conn.cursor()
            try:
                cursor.execute("SET SCHEMA Graph_KG")
            except:
                pass
            break
        except Exception as e:
            if attempt < 2 and ("Password change required" in str(e) or "Access Denied" in str(e) or "Authentication failed" in str(e)):
                logger.warning(f"Connection attempt {attempt+1} failed: {e}. Retrying aggressive password reset...")
                _apply_aggressive_password_reset(container_name)
                time.sleep(2)
            else:
                logger.error(f"Failed to connect to IRIS on attempt {attempt+1}: {e}")
                if attempt == 2:
                    raise e
            
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
            col = 'id' if 'Emb' in t else 'node_id' if t == 'nodes' else 's'
            cursor.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (f"{prefix}%",))
        iris_connection.commit()


from iris_vector_graph.utils import _split_sql_statements


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "performance: mark test as performance benchmark")


def pytest_addoption(parser):
    """Add command line options."""
    parser.addoption("--use-existing-iris", action="store_true", default=False, help="Use existing IRIS container")
