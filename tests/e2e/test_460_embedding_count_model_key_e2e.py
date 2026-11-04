"""4.6.0 R096: `embedding_count(model_key=...)` without `graph=` counts that model's vectors in the default graph.

It routed only when `graph` was not None, so `embedding_count(model_key="m")` counted `kg_NodeEmbeddings` (the
default model's table, namespace-wide) and ignored the model. The USER_GUIDE worked around it with `graph=""`.

Written by hand below: model `r096` holds one vector in the default graph (carol) and two in graph `g96`; the default
model's table holds none (the namespace is erased first).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


def test_model_key_alone_counts_the_default_graphs_route(iris_connection, iris_master_cleanup):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    model = f"r096_{uuid.uuid4().hex[:6]}"
    for nid in ("carol", "dave"):
        engine.create_node(nid, labels=["P"])
        engine.create_node(nid, labels=["P"], graph="g96")
    assert engine.store_embedding("carol", [0.0, 0.0, 1.0], model_key=model)
    assert engine.store_embedding("carol", [0.0, 1.0, 0.0], model_key=model, graph="g96")
    assert engine.store_embedding("dave", [1.0, 0.0, 0.0], model_key=model, graph="g96")

    assert engine.embedding_count() == 0  # the default model's table, namespace-wide: nothing stored there
    assert engine.embedding_count(model_key=model) == 1
    assert engine.embedding_count(graph="", model_key=model) == 1
    assert engine.embedding_count(graph="g96", model_key=model) == 2
    assert engine.embedding_count(model_key=f"{model}_unrouted") == 0
