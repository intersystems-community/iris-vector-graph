"""
Shared pytest fixtures for IRIS Vector Graph tests.

All integration tests use these shared fixtures for database connections.
Constitutional Principle II: Test-First with Live Database (NON-NEGOTIABLE)

Uses iris-devtester for automatic container discovery and connection management.
"""

import pytest
import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def iris_connection():
    """
    Get IRIS database connection for testing using iris-devtester auto-discovery.

    iris-devtester automatically discovers running IRIS containers and handles:
    - Port management (no hardcoded ports)
    - Password management
    - Connection retry logic

    Falls back to manual .env configuration if iris-devtester fails.

    Constitutional Principle II: Use iris-devtester for connection management (NON-NEGOTIABLE)
    """
    # Try iris-devtester auto-discovery first (per constitution)
    try:
        from iris_devtester.connections import auto_detect_iris_host_and_port
        from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect

        host, port = auto_detect_iris_host_and_port()
        logger.info(f"Auto-discovered IRIS at {host}:{port}")

        conn = dbapi_connect(host, port, 'USER', '_SYSTEM', 'SYS')
        logger.info("Connected via iris-devtester auto-discovery")
        yield conn
        conn.close()
        return
    except Exception as e:
        logger.warning(f"iris-devtester auto-discovery failed: {e}, falling back to .env")

    # Fall back to manual .env configuration
    import iris
    load_dotenv()

    conn = iris.connect(
        os.getenv('IRIS_HOST', 'localhost'),
        int(os.getenv('IRIS_PORT', '1972')),
        os.getenv('IRIS_NAMESPACE', 'USER'),
        os.getenv('IRIS_USER', '_SYSTEM'),
        os.getenv('IRIS_PASSWORD', 'SYS')
    )
    logger.info("Connected via .env configuration")

    yield conn
    conn.close()


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
