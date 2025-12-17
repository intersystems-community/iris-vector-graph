"""
Shared pytest fixtures for IRIS Vector Graph tests.

All integration tests use these shared fixtures for database connections.
Constitutional Principle II: Test-First with Live Database (NON-NEGOTIABLE)

Uses iris-devtester for connection management.
Targets the specific test container: iris_test_vector_graph_ai
"""

import pytest
import os
import subprocess
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# The dedicated test container name
TEST_CONTAINER_NAME = 'iris_test_vector_graph_ai'


def get_container_port(container_name: str, internal_port: int = 1972) -> int:
    """Get the host port for a specific Docker container."""
    try:
        result = subprocess.run(
            ['docker', 'port', container_name, str(internal_port)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse "0.0.0.0:1981" or "[::]:1981" format
            port_line = result.stdout.strip().split('\n')[0]
            port = int(port_line.split(':')[-1])
            return port
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return None


@pytest.fixture(scope="module")
def iris_connection():
    """
    Get IRIS database connection for testing using the dedicated test container.

    Connection priority:
    1. IRIS_TEST_CONTAINER env var (or default: iris_test_vector_graph_ai)
    2. IRIS_PORT env var (explicit port override)
    3. Auto-discovery via docker port command

    Constitutional Principle II: Use iris-devtester for connection management (NON-NEGOTIABLE)
    """
    from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect
    load_dotenv()

    host = os.getenv('IRIS_HOST', 'localhost')
    container_name = os.getenv('IRIS_TEST_CONTAINER', TEST_CONTAINER_NAME)

    # Try to get port from the specific test container
    port = None

    # Priority 1: Get port from specific test container (preferred)
    port = get_container_port(container_name)
    if port:
        logger.info(f"Found test container {container_name} on port {port}")

    # Priority 2: Explicit port override via IRIS_TEST_PORT (NOT IRIS_PORT to avoid .env conflicts)
    if port is None:
        test_port = os.getenv('IRIS_TEST_PORT')
        if test_port:
            port = int(test_port)
            logger.info(f"Using explicit IRIS_TEST_PORT={port}")

    # Priority 3: Fall back to default port
    if port is None:
        port = 1972
        logger.warning(f"Container {container_name} not found, using default port {port}")

    try:
        conn = dbapi_connect(host, port, 'USER', '_SYSTEM', 'SYS')
        logger.info(f"Connected to IRIS at {host}:{port}")
        yield conn
        conn.close()
    except Exception as e:
        pytest.skip(f"No IRIS database available at {host}:{port}: {e}")


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_database: mark test as requiring live IRIS database"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow-running"
    )
