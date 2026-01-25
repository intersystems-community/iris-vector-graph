"""
E2E Test Configuration and Fixtures

Shared fixtures for E2E tests across Biomedical and Fraud Detection demos.
Uses iris-devtester for database connection management per Constitution II.
"""

import os
import time
from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient

# Import shared fixtures from parent conftest
# The iris_connection fixture is already defined in tests/conftest.py


@pytest.fixture(scope="module")
def api_client() -> Generator[TestClient, None, None]:
    """
    Fixture providing FastAPI TestClient for E2E API tests.

    Module-scoped for performance (reused across tests in same file).
    """
    try:
        from api.main import app

        client = TestClient(app)
        yield client
    except ImportError:
        pytest.skip("FastAPI app not available - run 'uvicorn api.main:app' first")


@pytest.fixture(scope="module")
def biomedical_test_data(iris_connection) -> dict:
    """
    Fixture providing biomedical test data context.

    Verifies biomedical data is loaded and returns summary stats.
    Automatically loads sample data if missing.
    """
    cursor = iris_connection.cursor()

    # Check for biomedical data
    cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label IN ('Protein', 'Gene', 'Disease', 'Drug', 'Pathway')")
    biomedical_count = cursor.fetchone()[0]

    if biomedical_count == 0:
        from scripts.setup import load_sample_data
        load_sample_data(iris_connection)
        
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label IN ('Protein', 'Gene', 'Disease', 'Drug', 'Pathway')")
        biomedical_count = cursor.fetchone()[0]

    # Check for embeddings
    cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
    embedding_count = cursor.fetchone()[0]

    # Check for relationships
    cursor.execute("SELECT COUNT(*) FROM rdf_edges")
    edge_count = cursor.fetchone()[0]

    return {
        "protein_count": biomedical_count,
        "embedding_count": embedding_count,
        "edge_count": edge_count,
        "has_data": biomedical_count > 0,
    }


@pytest.fixture(scope="module")
def fraud_test_data(iris_connection) -> dict:
    """
    Fixture providing fraud detection test data context.

    Verifies fraud data is loaded and returns summary stats.
    Automatically loads sample data if missing.
    """
    cursor = iris_connection.cursor()

    # Check for account data
    try:
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'Account'")
        account_count = cursor.fetchone()[0]
    except Exception:
        account_count = 0

    if account_count == 0:
        from scripts.setup import load_fraud_data
        load_fraud_data(iris_connection)
        
        try:
            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'Account'")
            account_count = cursor.fetchone()[0]
        except Exception:
            account_count = 0

    # Check for transaction data
    try:
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'Transaction'")
        transaction_count = cursor.fetchone()[0]
    except Exception:
        transaction_count = 0

    # Check for alert data
    try:
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'Alert'")
        alert_count = cursor.fetchone()[0]
    except Exception:
        alert_count = 0

    return {
        "account_count": account_count,
        "transaction_count": transaction_count,
        "alert_count": alert_count,
        "has_data": account_count > 0,
    }


@pytest.fixture
def timing_tracker():
    """
    Fixture for tracking test execution timing.

    Returns a context manager that tracks elapsed time.
    """

    class TimingTracker:
        def __init__(self):
            self.start_time = 0.0
            self.elapsed_ms: float | None = None

        def __enter__(self):
            self.start_time = time.time()
            return self

        def __exit__(self, *args):
            self.elapsed_ms = (time.time() - self.start_time) * 1000

        def assert_under(self, max_ms: float, operation: str = "operation"):
            """Assert the tracked operation completed under max_ms"""
            assert self.elapsed_ms is not None, "Timer was not used"
            assert (
                self.elapsed_ms < max_ms
            ), f"{operation} took {self.elapsed_ms:.2f}ms, expected <{max_ms}ms"

    return TimingTracker()


@pytest.fixture
def test_cleanup(iris_connection):
    """
    Fixture to cleanup test-created data after each test.

    Yields a list that tests can append IDs to for cleanup.
    """
    cleanup_ids = []

    yield cleanup_ids

    # Cleanup after test
    if cleanup_ids:
        cursor = iris_connection.cursor()
        for test_id in cleanup_ids:
            try:
                cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", (test_id,))
                cursor.execute("DELETE FROM rdf_edges WHERE s = ? OR o_id = ?", (test_id, test_id))
                cursor.execute("DELETE FROM rdf_props WHERE s = ?", (test_id,))
                cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (test_id,))
                cursor.execute("DELETE FROM nodes WHERE node_id = ?", (test_id,))
            except Exception:
                pass
        iris_connection.commit()


def pytest_collection_modifyitems(config, items):
    """
    Automatically add markers based on test location and naming.
    """
    for item in items:
        # Add e2e marker to all tests in e2e directory
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.requires_database)
