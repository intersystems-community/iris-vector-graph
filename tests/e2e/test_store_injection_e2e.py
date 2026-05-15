import os
import pytest
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


class TestExistingBehaviorUnchanged:

    def test_execute_cypher_match_returns_results(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        result = engine.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 3")
        assert result.error is None
        assert isinstance(result.rows, list)

    def test_execute_cypher_var_length_returns_results(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        result = engine.execute_cypher(
            "MATCH (a)-[*1..2]->(b) RETURN b.node_id LIMIT 5"
        )
        assert result.error is None

    def test_default_store_is_irissqlstore(self, iris_connection):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        engine = IRISGraphEngine(iris_connection)
        assert isinstance(engine._store, IRISGraphStore)

    def test_store_capabilities_native_sql(self, iris_connection):
        engine = IRISGraphEngine(iris_connection)
        caps = engine._store_capabilities
        assert caps.get("native_sql") is True


class TestExplicitIRISGraphStoreInjection:

    def test_explicit_injection_works(self, iris_connection):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore(iris_connection)
        engine = IRISGraphEngine(iris_connection, store=store)
        result = engine.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 1")
        assert result.error is None

    def test_store_isinstance_check(self, iris_connection):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        from iris_vector_graph.store_protocol import GraphStore
        store = IRISGraphStore(iris_connection)
        assert isinstance(store, GraphStore)

    def test_bfs_routes_through_store(self, iris_connection):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore(iris_connection)
        engine = IRISGraphEngine(iris_connection, store=store)
        result = engine.execute_cypher(
            "MATCH (a)-[*1..2]->(b) RETURN b.node_id LIMIT 5"
        )
        assert result.error is None
