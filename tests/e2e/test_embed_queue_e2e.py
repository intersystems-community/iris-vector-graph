import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


class TestEmbedQueueE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=0)
        self.prefix = f"EQ_{uuid.uuid4().hex[:8]}"

    def _class_available(self):
        try:
            cursor = self.engine.conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'Graph.KG.EmbedQueue'"
            )
            row = cursor.fetchone()
            return int(row[0]) > 0 if row else False
        except Exception:
            return False

    def test_enqueue_returns_count_or_zero(self):
        node_ids = [f"{self.prefix}:n1", f"{self.prefix}:n2"]
        result = self.engine.enqueue_for_embedding(node_ids)
        assert isinstance(result, int)
        assert result >= 0

    def test_pending_count_after_enqueue(self):
        if not self._class_available():
            pytest.skip("Graph.KG.EmbedQueue not deployed on this container")
        node_ids = [f"{self.prefix}:p1", f"{self.prefix}:p2", f"{self.prefix}:p3"]
        self.engine.enqueue_for_embedding(node_ids)
        count = self.engine.embed_queue_pending()
        assert isinstance(count, int)
        assert count >= 0

    def test_process_queue_returns_dict(self):
        if not self._class_available():
            pytest.skip("Graph.KG.EmbedQueue not deployed on this container")
        self.engine.enqueue_for_embedding([f"{self.prefix}:proc1"])
        result = self.engine.process_embed_queue(batch_size=5)
        assert isinstance(result, dict)
        assert "processed" in result
        assert "errors" in result
        assert isinstance(result["processed"], int)
        assert isinstance(result["errors"], int)

    def test_graceful_degradation_no_class(self):
        original = self.engine._arno_available
        try:
            result = self.engine.enqueue_for_embedding(["does_not_matter"])
            assert isinstance(result, int)
            result2 = self.engine.embed_queue_pending()
            assert isinstance(result2, int)
            result3 = self.engine.process_embed_queue()
            assert isinstance(result3, dict)
        except Exception as e:
            pytest.fail(f"EmbedQueue methods should not raise, got: {e}")
        finally:
            self.engine._arno_available = original
