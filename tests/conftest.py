"""
Managed test container infrastructure for IRIS Vector Graph.
"""

import pytest
import os
import logging
import time
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_CONTAINER_IMAGE = "intersystemsdc/iris-community:latest-em"


def _robust_iris_setup(container_name: str) -> bool:
    """Setup IRIS using native shell commands for maximum stability."""
    try:
        # Load ObjectScript classes
        logger.info("Deploying ObjectScript classes...")
        subprocess.run(['docker', 'exec', container_name, 'mkdir', '-p', '/tmp/iris_src'], check=True)
        subprocess.run(['docker', 'cp', 'iris_src/src/.', f"{container_name}:/tmp/iris_src/"], check=True)
        
        # Using $system.OBJ.LoadDir is the most robust way
        script = 'Do $system.OBJ.LoadDir("/tmp/iris_src", "ck", .errors, 1) H'
        command = ['docker', 'exec', '-i', container_name, 'iris', 'session', 'iris', '-U', 'USER']
        result = subprocess.run(command, input=script, capture_output=True, text=True)
        if "Error" in result.stdout:
            logger.warning(f"Deployment warnings:\n{result.stdout}")
        
        return True
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        return False


@pytest.fixture(scope="session")
def iris_test_container(request):
    """Session-scoped managed IRIS container."""
    from iris_devtester.containers.iris_container import IRISContainer
    from iris_devtester.ports import PortRegistry
    
    container = IRISContainer(image=TEST_CONTAINER_IMAGE, port_registry=PortRegistry(), project_path=os.getcwd())
    container.start()
    container.wait_for_ready(timeout=180)
    time.sleep(10)
    
    container_name = container.get_container_name()
    
    # 1. Create tables via DBAPI (reliable for simple DDL)
    conn = container.get_connection()
    cursor = conn.cursor()
    for f in ["sql/schema.sql", "sql/operators_fixed.sql"]:
        path = Path(f)
        if path.exists():
            content = path.read_text()
            # Simple splitter for DDL
            for stmt in content.split(';'):
                stmt = stmt.strip()
                if stmt and not stmt.startswith('--'):
                    try: cursor.execute(stmt)
                    except Exception: pass
    conn.commit()
    conn.close()
    
    # 2. Load Classes via Shell
    _robust_iris_setup(container_name)
    
    yield container
    container.stop()


@pytest.fixture(scope="module")
def iris_connection(iris_test_container):
    conn = iris_test_container.get_connection()
    yield conn
    conn.close()


@pytest.fixture(scope="function")
def iris_cursor(iris_connection):
    cursor = iris_connection.cursor()
    yield cursor
    try: iris_connection.rollback()
    except Exception: pass


@pytest.fixture(scope="function")
def clean_test_data(iris_connection):
    import uuid
    prefix = f"TEST_{uuid.uuid4().hex[:8]}:"
    yield prefix
    cursor = iris_connection.cursor()
    try:
        for t in ["kg_NodeEmbeddings", "rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
            col = 'id' if 'Emb' in t else 'node_id' if t=='nodes' else 's'
            cursor.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (f"{prefix}%",))
        iris_connection.commit()
    except Exception: iris_connection.rollback()


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "performance: mark test as performance benchmark")


def pytest_addoption(parser):
    parser.addoption("--use-existing-iris", action="store_true", default=False, help="Use existing IRIS container")
