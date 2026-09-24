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
def api_client(iris_connection) -> Generator[TestClient, None, None]:
    """
    Fixture providing FastAPI TestClient for E2E API tests.
    Uses the shared iris_connection (test/test creds) so the app gets a live engine.
    Module-scoped for performance (reused across tests in same file).
    """
    try:
        from iris_vector_graph import IRISGraphEngine
        from api.main import create_app

        engine = IRISGraphEngine(iris_connection)
        app = create_app(engine=engine)
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
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
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
                cursor.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ?", (test_id,))
                cursor.execute("DELETE FROM rdf_edges WHERE s = ? OR o_id = ?", (test_id, test_id))
                cursor.execute("DELETE FROM rdf_props WHERE s = ?", (test_id,))
                cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (test_id,))
                cursor.execute("DELETE FROM nodes WHERE node_id = ?", (test_id,))
            except Exception:
                pass
        iris_connection.commit()


# ---------------------------------------------------------------------------
# Spec 227 — the decisive fixture: two graphs, two models, two widths
#
# Lives in conftest.py rather than in a test_227_*.py module on purpose. A
# fixture defined inside a test module is invisible to every other test file,
# and four separate phase gates need this one.
# ---------------------------------------------------------------------------

#: One entity ID that exists in both graphs. Impossible before 4.0.0, because
#: `uq_nodes_nodeid UNIQUE (node_id)` allowed a node ID exactly one graph.
IVG227_NODE_ID = "ivg227:node:1"

IVG227_GRAPH_A = "ivg227-A"
IVG227_GRAPH_B = "ivg227-B"

IVG227_MODEL_A = "ivg227-model-a"
IVG227_MODEL_B = "ivg227-model-b"

#: Two widths, so the fixture proves a column declaration cannot hold both.
IVG227_DIM_A = 384
IVG227_DIM_B = 768


class Ivg227Env:
    """The two-graph scenario, plus the vectors and cleanup that go with it."""

    node_id = IVG227_NODE_ID
    graph_a = IVG227_GRAPH_A
    graph_b = IVG227_GRAPH_B
    model_a = IVG227_MODEL_A
    model_b = IVG227_MODEL_B
    dim_a = IVG227_DIM_A
    dim_b = IVG227_DIM_B

    def __init__(self, conn, engine):
        self.conn = conn
        self.engine = engine

    # -- vectors -----------------------------------------------------------
    def vec_a(self, fill: float = 0.1) -> list:
        return [fill] * self.dim_a

    def vec_b(self, fill: float = 0.2) -> list:
        return [fill] * self.dim_b

    @staticmethod
    def as_query(vector) -> str:
        """The `[0.1,0.1,...]` string form kg_KNN_VEC takes for a query vector."""
        return "[" + ",".join(str(float(v)) for v in vector) + "]"

    # -- population --------------------------------------------------------
    def create_nodes(self):
        """The same node ID in both graphs, with a label only one of them holds."""
        self.engine.create_node(
            self.node_id, labels=["Patient"], graph=self.graph_a
        )
        self.engine.create_node(
            self.node_id, labels=["Member"], graph=self.graph_b
        )

    def store_vectors(self):
        """One vector per graph, each from its own model at its own width."""
        self.engine.store_embedding(
            self.node_id, self.vec_a(), graph=self.graph_a, model_key=self.model_a
        )
        self.engine.store_embedding(
            self.node_id, self.vec_b(), graph=self.graph_b, model_key=self.model_b
        )

    def populate(self):
        self.create_nodes()
        self.store_vectors()

    # -- cleanup -----------------------------------------------------------
    def wipe(self):
        """Remove both graphs, including any routed table created for them.

        Routed tables are dropped rather than emptied: a route is created on
        demand, so a leftover one from a previous run would answer a later
        test's route lookup with a stale width.
        """
        import contextlib

        cursor = self.conn.cursor()
        try:
            for graph in (self.graph_a, self.graph_b):
                routes = []
                with contextlib.suppress(Exception):
                    cursor.execute(
                        "SELECT table_name FROM Graph_KG.embedding_registry "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
                    routes = [r[0] for r in cursor.fetchall()]
                for table in routes:
                    with contextlib.suppress(Exception):
                        cursor.execute(f"DROP TABLE Graph_KG.{table}")
                with contextlib.suppress(Exception):
                    cursor.execute(
                        "DELETE FROM Graph_KG.embedding_registry "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
                # The unscoped 3.2.0 tables may still hold rows for this node.
                for table in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
                    with contextlib.suppress(Exception):
                        cursor.execute(
                            f"DELETE FROM Graph_KG.{table} WHERE node_id = ?",
                            (self.node_id,),
                        )
                    with contextlib.suppress(Exception):
                        cursor.execute(
                            f"DELETE FROM Graph_KG.{table} WHERE id = ?",
                            (self.node_id,),
                        )
                with contextlib.suppress(Exception):
                    cursor.execute(
                        "DELETE FROM Graph_KG.rdf_edges "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
                for table in ("rdf_labels", "rdf_props"):
                    with contextlib.suppress(Exception):
                        cursor.execute(
                            f"DELETE FROM Graph_KG.{table} "
                            "WHERE COALESCE(graph_id, '') = ?",
                            (graph,),
                        )
                    with contextlib.suppress(Exception):
                        cursor.execute(
                            f"DELETE FROM Graph_KG.{table} WHERE s = ?",
                            (self.node_id,),
                        )
                with contextlib.suppress(Exception):
                    cursor.execute(
                        "DELETE FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
            with contextlib.suppress(Exception):
                self.conn.commit()
        finally:
            with contextlib.suppress(Exception):
                cursor.close()


@pytest.fixture(scope="function")
def ivg227_env(iris_connection):
    """Spec 227's two-graph / two-model / two-width environment.

    A missing container is a FAILURE, never a skip (constitution VIII gate 1):
    a skipped storage-layout test is indistinguishable from a passing one, and
    that is exactly how 27 tests stayed fake-green through Sept 2026.
    """
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 227 asserts a storage layout, so it cannot run without IRIS. "
            "SKIP_IRIS_TESTS=true is not an acceptable outcome here — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: spec 227's guarantees are in column "
            "declarations and constraints, which mocks cannot observe."
        )

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    env = Ivg227Env(iris_connection, engine)
    env.wipe()
    yield env
    env.wipe()


def pytest_collection_modifyitems(config, items):
    """
    Automatically add markers based on test location and naming.
    """
    for item in items:
        # Add e2e marker to all tests in e2e directory
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.requires_database)


# Spec 231: the scratch FHIR namespace IVGFHIR (fixtures live beside their helpers).
from tests.e2e.fhir_conftest import fhir_conn, fhir_engine, fhir_loader  # noqa: E402,F401
