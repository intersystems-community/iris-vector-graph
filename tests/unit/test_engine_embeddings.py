import pytest

from iris_vector_graph.engine import IRISGraphEngine


class DummyCursor:
    def __init__(self, existing_nodes=None):
        self.existing_nodes = existing_nodes or set()
        self._results = []
        self.executed = []

    def execute(self, sql, params=None):
        params = params or []
        self.executed.append((sql, params))

        if "FROM nodes" in sql and "node_id" in sql:
            node_id = params[0] if params else None
            count = 1 if node_id in self.existing_nodes else 0
            self._results = [(count,)]
        elif "SELECT" in sql and "VECTOR" in sql:
            self._results = [(3,)]

    def fetchone(self):
        return self._results[0] if self._results else None

    def fetchall(self):
        return self._results


class DummyConn:
    def __init__(self, existing_nodes=None):
        self.existing_nodes = existing_nodes or set()

    def cursor(self):
        return DummyCursor(existing_nodes=self.existing_nodes)

    def commit(self):
        return None

    def rollback(self):
        return None


def test_store_embedding_requires_node():
    conn = DummyConn(existing_nodes=set())
    engine = IRISGraphEngine(conn)

    with pytest.raises(ValueError):
        engine.store_embedding("node-missing", [0.1, 0.2, 0.3])


def test_store_embedding_dimension_validation():
    conn = DummyConn(existing_nodes={"node-1"})
    engine = IRISGraphEngine(conn)

    engine._get_embedding_dimension = lambda: 3

    with pytest.raises(ValueError):
        engine.store_embedding("node-1", [0.1, 0.2])


def test_store_embeddings_atomic_failure():
    conn = DummyConn(existing_nodes={"node-1"})
    engine = IRISGraphEngine(conn)

    engine._get_embedding_dimension = lambda: 3

    with pytest.raises(ValueError):
        engine.store_embeddings(
            [
                {"node_id": "node-1", "embedding": [0.1, 0.2, 0.3]},
                {"node_id": "node-missing", "embedding": [0.1, 0.2, 0.3]},
            ]
        )
