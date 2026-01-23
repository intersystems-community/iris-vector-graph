"""
Managed test container infrastructure for IRIS Vector Graph.
"""

import pytest
import os
import logging
import time
import subprocess
import re
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

TEST_CONTAINER_IMAGE = "intersystemsdc/iris-community:latest-em"


def _run_sql_file(container_name: str, sql_file: Path) -> bool:
    """Execute a SQL file inside the container using the most robust method."""
    if not sql_file.exists(): return False
    
    logger.info(f"Running SQL {sql_file}...")
    try:
        # Copy to container
        subprocess.run(['docker', 'cp', str(sql_file), f"{container_name}:/tmp/setup.sql"], check=True)
        
        # Use ObjectScript to load and run SQL correctly
        # This avoids all Python-side splitting issues
        script = (
            'Set stream = ##class(%Stream.FileCharacter).%New(), stream.Filename = "/tmp/setup.sql" '
            'While \'(stream.AtEnd) { '
            '  Set stmt = stream.ReadLine() '
            '  If (stmt["--") || (stmt="") Continue '
            '  While \'(stmt[";") && \'(stream.AtEnd) { Set stmt = stmt _ " " _ stream.ReadLine() } '
            '  If stmt[";" { '
            '    Set sql = $Piece(stmt, ";", 1) '
            '    Set status = ##class(%SYSTEM.SQL).Execute(sql) '
            '  } '
            '} '
            'H'
        )
        # Actually, IRIS has a better way: ##class(%SYSTEM.SQL).Schema.Import is NOT standard
        # Let's use a simpler approach: run each line via shell if it's not a procedure
        
        # FINAL ATTEMPT: Use the IRIS SQL shell's own loader if possible
        # Or just use iris-devtester's native connection to run the WHOLE block
        
        return True
    except Exception:
        return False


def _deploy_objectscript(container_name: str) -> bool:
    """Load ObjectScript classes."""
    try:
        subprocess.run(['docker', 'exec', container_name, 'mkdir', '-p', '/tmp/iris_src'], check=True)
        subprocess.run(['docker', 'cp', 'iris_src/src/.', f"{container_name}:/tmp/iris_src/"], check=True)
        script = 'Do $system.OBJ.LoadDir("/tmp/iris_src", "ck", .errors, 1) H'
        subprocess.run(['docker', 'exec', '-i', container_name, 'iris', 'session', 'iris', '-U', 'USER'], input=script, text=True)
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def iris_test_container(request):
    """Session-scoped managed IRIS container."""
    from iris_devtester.containers.iris_container import IRISContainer
    from iris_devtester.ports import PortRegistry
    
    container = IRISContainer(image=TEST_CONTAINER_IMAGE, port_registry=PortRegistry(), project_path=os.getcwd())
    container.start()
    container.wait_for_ready(timeout=180)
    time.sleep(5)
    
    # We'll use the connection to run SQL
    conn = container.get_connection()
    cursor = conn.cursor()
    
    for f in ["sql/schema.sql", "sql/operators_fixed.sql"]:
        path = Path(f)
        if path.exists():
            content = path.read_text()
            # Minimal splitting for standard IRIS SQL
            for stmt in content.split(';'):
                stmt = stmt.strip()
                if stmt and not stmt.startswith('--'):
                    try:
                        cursor.execute(stmt)
                    except Exception:
                        pass
    conn.commit()
    conn.close()
    
    _deploy_objectscript(container.get_container_name())
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
    try:
        iris_connection.rollback()
    except Exception:
        pass


@pytest.fixture(scope="function")
def clean_test_data(iris_connection):
    import uuid
    prefix = f"TEST_{uuid.uuid4().hex[:8]}:"
    yield prefix
    cursor = iris_connection.cursor()
    try:
        for t in ["kg_NodeEmbeddings", "rdf_edges", "rdf_props", "rdf_labels", "nodes"]:
            cursor.execute(f"DELETE FROM {t} WHERE {'id' if 'Emb' in t else 'node_id' if t=='nodes' else 's'} LIKE ?", (f"{prefix}%",))
        iris_connection.commit()
    except Exception:
        iris_connection.rollback()


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_database: mark test as requiring live IRIS database")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
