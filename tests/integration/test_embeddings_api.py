import json

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _cleanup_embeddings(engine):
    cursor = engine.conn.cursor()
    cursor.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id LIKE ?", ["EMB_TEST:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_reifications WHERE edge_id IN (SELECT edge_id FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?)", ["EMB_TEST:%", "EMB_TEST:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", ["EMB_TEST:%", "EMB_TEST:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", ["EMB_TEST:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", ["EMB_TEST:%"])
    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", ["EMB_TEST:%"])
    engine.conn.commit()
    cursor.close()


def test_store_embedding_and_knn(engine):
    _cleanup_embeddings(engine)

    engine.create_node('EMB_TEST:1')

    dim = engine._get_embedding_dimension()
    embedding = [0.1] * dim

    assert engine.store_embedding("EMB_TEST:1", embedding, metadata={"source": "test"})

    results = engine.kg_KNN_VEC(json.dumps(embedding), k=1)
    assert results
    assert results[0][0] == "EMB_TEST:1"


def test_store_embeddings_batch_atomic(engine):
    _cleanup_embeddings(engine)

    engine.create_node('EMB_TEST:2')

    dim = engine._get_embedding_dimension()
    embedding = [0.2] * dim

    with pytest.raises(ValueError):
        engine.store_embeddings(
            [
                {"node_id": "EMB_TEST:2", "embedding": embedding},
                {"node_id": "EMB_TEST:missing", "embedding": embedding},
            ]
        )

