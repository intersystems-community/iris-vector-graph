"""
Managed test container infrastructure for IRIS Vector Graph.
"""

import logging
import os
import subprocess
import time

import iris
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
    Copies source and pipes SQL/ObjectScript directly into IRIS session.
    """
    try:
        logger.info(f"Starting Direct Pipe IRIS setup for container: {container_name}")
        
        # 0. Aggressive password reset
        _apply_aggressive_password_reset(container_name)

        # 1. Prepare directory in container
        subprocess.run(['docker', 'exec', container_name, 'mkdir', '-p', '/tmp/src'], capture_output=True)
        
        logger.info("Copying source and SQL files to container...")
        subprocess.run(['docker', 'cp', 'iris_src/src/.', f"{container_name}:/tmp/src/"], check=True)
        subprocess.run(['docker', 'cp', 'sql/schema.sql', f"{container_name}:/tmp/schema.sql"], check=True)
        subprocess.run(['docker', 'cp', 'sql/operators_fixed.sql', f"{container_name}:/tmp/operators_fixed.sql"], check=True)
        
        setup_script = """iris session iris -U USER <<EOF
sql
SET SCHEMA SQLUser;
\\i /tmp/schema.sql
\\i /tmp/operators_fixed.sql
-- Explicitly grant privileges to the test user
GRANT ALL PRIVILEGES ON SQLUser.nodes TO test;
GRANT ALL PRIVILEGES ON SQLUser.rdf_edges TO test;
GRANT ALL PRIVILEGES ON SQLUser.rdf_labels TO test;
GRANT ALL PRIVILEGES ON SQLUser.rdf_props TO test;
GRANT ALL PRIVILEGES ON SQLUser.kg_NodeEmbeddings TO test;
SELECT count(*) as table_count FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'SQLUser';
q
-- Load IRIS ObjectScript classes
Do \\$system.OBJ.LoadDir("/tmp/src", "ck", .errors, 1)
H
EOF
"""
        
        cmd = ['docker', 'exec', '-i', container_name, 'bash']
        logger.info("Piping SQL and ObjectScript commands via HEREDOC into IRIS session...")
        result = subprocess.run(cmd, input=setup_script, capture_output=True, text=True, errors='replace')
        
        # Log results
        if result.stdout:
            logger.info(f"IRIS Setup Output:\n{result.stdout}")
        if result.stderr:
            logger.warning(f"IRIS Setup Errors:\n{result.stderr}")
            
        if result.returncode != 0:
            logger.error(f"IRIS session exited with code {result.returncode}")
            return False

        logger.info("Direct Pipe IRIS setup completed successfully.")
        return True
    except Exception as e:
        logger.error(f"IRIS setup failed with exception: {e}", exc_info=True)
        return False


@pytest.fixture(scope="session")
def iris_test_container(request):
    """Session-scoped managed IRIS container."""
    from iris_devtester.containers.iris_container import IRISContainer
    from iris_devtester.ports import PortRegistry
    
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
    container = IRISContainer(image=TEST_CONTAINER_IMAGE, port_registry=PortRegistry(), project_path=os.getcwd())
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


@pytest.fixture(scope="module")
def iris_connection(iris_test_container):
    """Module-scoped IRIS connection using the assigned port."""
    assigned_port = iris_test_container.get_assigned_port()
    container_name = iris_test_container.get_container_name()
    logger.info(f"Connecting to IRIS on port {assigned_port}...")
    
    conn = None
    for attempt in range(3):
        try:
            conn = iris.connect(
                hostname='localhost',
                port=assigned_port,
                namespace='USER',
                username='test',
                password='test'
            )
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


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "performance: mark test as performance benchmark")


def pytest_addoption(parser):
    """Add command line options."""
    parser.addoption("--use-existing-iris", action="store_true", default=False, help="Use existing IRIS container")
