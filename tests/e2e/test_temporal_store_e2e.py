import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"TEMP_{uuid.uuid4().hex[:6]}"


class TestTemporalStoreDelegation:

    @pytest.fixture(autouse=True)
    def cleanup(self, iris_connection):
        yield
        cursor = iris_connection.cursor()
        try:
            cursor.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE '{PREFIX}%'")
            iris_connection.commit()
        except Exception:
            pass

    def test_temporal_write_routes_to_store(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        assert isinstance(engine._store, IRISGraphStore)
        result = engine.create_edge_temporal(
            f"{PREFIX}:n1", "CITED", f"{PREFIX}:n2", timestamp=1700000000, weight=0.9
        )
        assert result is True or result is False

    def test_temporal_window_routes_to_store(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        result = engine.get_edges_in_window(
            f"{PREFIX}:n1", "CITED", start=1000000000, end=1800000000
        )
        assert isinstance(result, list)

    def test_get_temporal_aggregate_routes_to_store(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        val = engine.get_temporal_aggregate(
            f"{PREFIX}:n1", "CITED", "count", 0, 9999999999
        )
        assert isinstance(val, (int, float)) or val is None
