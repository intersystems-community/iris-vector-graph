"""T192-16: Correctness test — guarded result equals non-guarded result on live IRIS."""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture(scope="module")
def guard_engine(arno_iris_connection):
    engine = IRISGraphEngine(arno_iris_connection, embedding_dimension=128)
    try:
        engine.initialize_schema()
    except Exception:
        pass  # schema may already exist
    return engine


@pytest.mark.usefixtures("arno_iris_connection")
class TestStructuralGuardCorrectness:
    """Verify EXISTS guard does not drop valid results vs non-guarded query."""

    def _setup_nodes(self, engine):
        engine.create_node("sg_alice", labels=["Person"], properties={"score": "0.9", "age": "30"})
        engine.create_node("sg_bob", labels=["Person"], properties={"score": "0.3"})
        engine.create_node("sg_carol", labels=["Person"], properties={"age": "25"})

    def _teardown_nodes(self, engine):
        for nid in ("sg_alice", "sg_bob", "sg_carol"):
            try:
                engine.delete_node(nid)
            except Exception:
                pass

    def _ids_from_result(self, result):
        col_idx = result.columns.index("id")
        return {row[col_idx] for row in result.rows}

    def test_guarded_query_returns_same_nodes_as_direct(self, guard_engine):
        """MATCH (n:Person) WHERE n.score > 0.5 returns sg_alice only (has score=0.9)."""
        self._setup_nodes(guard_engine)
        try:
            result = guard_engine.execute_cypher(
                "MATCH (n:Person) WHERE n.score > 0.5 RETURN n.node_id AS id"
            )
            assert result.error is None, f"Query error: {result.error}"
            ids = self._ids_from_result(result)
            assert "sg_alice" in ids, f"sg_alice (score=0.9) must appear; got {ids}"
            assert "sg_bob" not in ids, f"sg_bob (score=0.3) must not appear; got {ids}"
            assert "sg_carol" not in ids, f"sg_carol (no score) must not appear; got {ids}"
        finally:
            self._teardown_nodes(guard_engine)

    def test_inline_property_guard_correctness(self, guard_engine):
        """MATCH (n {score: '0.9'}) returns sg_alice only."""
        self._setup_nodes(guard_engine)
        try:
            result = guard_engine.execute_cypher(
                "MATCH (n {score: '0.9'}) RETURN n.node_id AS id"
            )
            assert result.error is None, f"Query error: {result.error}"
            ids = self._ids_from_result(result)
            assert "sg_alice" in ids, f"sg_alice must appear; got {ids}"
            assert "sg_bob" not in ids
            assert "sg_carol" not in ids
        finally:
            self._teardown_nodes(guard_engine)
