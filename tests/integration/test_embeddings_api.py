import json

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _cleanup_embeddings(cursor):
    cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id LIKE 'EMB_TEST:%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'EMB_TEST:%'")


def test_store_embedding_and_knn(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    cursor = iris_connection.cursor()
    _cleanup_embeddings(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('EMB_TEST:1')")
    iris_connection.commit()

    dim = engine._get_embedding_dimension()
    embedding = [0.1] * dim

    assert engine.store_embedding("EMB_TEST:1", embedding, metadata={"source": "test"})

    results = engine.kg_KNN_VEC(",".join(str(x) for x in embedding), k=1)
    assert results
    assert results[0][0] == "EMB_TEST:1"


def test_store_embeddings_batch_atomic(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    cursor = iris_connection.cursor()
    _cleanup_embeddings(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('EMB_TEST:2')")
    iris_connection.commit()

    dim = engine._get_embedding_dimension()
    embedding = [0.2] * dim

    with pytest.raises(ValueError):
        engine.store_embeddings(
            [
                {"node_id": "EMB_TEST:2", "embedding": embedding},
                {"node_id": "EMB_TEST:missing", "embedding": embedding},
            ]
        )

