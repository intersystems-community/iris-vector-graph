import pytest
import os
import iris

@pytest.fixture(scope="session")
def iris_connection():
    """Establish connection to IRIS for performance tests using DB-API"""
    host = os.getenv("IRIS_HOST", "localhost")
    port = int(os.getenv("IRIS_PORT", 1981))
    namespace = os.getenv("IRIS_NAMESPACE", "USER")
    username = os.getenv("IRIS_USERNAME", "_SYSTEM")
    password = os.getenv("IRIS_PASSWORD", "SYS")
    
    conn = iris.connect(f"{host}:{port}/{namespace}", username, password)
    yield conn
    conn.close()
