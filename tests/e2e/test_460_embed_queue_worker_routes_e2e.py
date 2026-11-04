"""4.6.0 R116: the queue's IRIS worker (`Graph.KG.EmbedQueue.ProcessBatch`, which `Graph.KG.EmbedQueueTask` runs)
writes a named graph's vectors to the graph's route, where `kg_KNN_VEC` in that graph reads them.

It wrote every vector to `kg_NodeEmbeddings`, whatever the entry's graph; since spec 227 a named graph's reads go to
its routed `kg_emb_*` table, so the vectors were never found.

The model: an `%Embedding.Config` named `ivgtest-fake` over `IVGTest.FakeEmbedding` (tests/e2e/fixtures), which
embeds a text ending in "a" as [1,0,0], "b" as [1,1,0], "c" as [0,1,0]. A node entry's text is its id. Worked by
hand: in graph G with nodes ..._a, ..._b, ..._c, the query [1,0,0] with k = 2 finds _a (cosine 1) then _b
(1/sqrt(2) = 0.70711).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e]

CONFIG = "ivgtest-fake"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "IVGTest.FakeEmbedding.cls"


@pytest.fixture(scope="module")
def fake_model(iris_connection):
    import iris as _iris

    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    stage = f"/tmp/ivgtest_{os.environ.get('IVG_NAMESPACE', 'USER')}"
    subprocess.run(["docker", "exec", container, "mkdir", "-p", stage], check=True, capture_output=True)
    subprocess.run(["docker", "cp", str(FIXTURE), f"{container}:{stage}/{FIXTURE.name}"], check=True, capture_output=True)
    native = _iris.createIRIS(iris_connection)
    sc = native.classMethodValue("%SYSTEM.OBJ", "Load", f"{stage}/{FIXTURE.name}", "ck-d")
    assert str(sc) == "1", sc
    cur = iris_connection.cursor()
    try:
        cur.execute("DELETE FROM %Embedding.Config WHERE Name = ?", [CONFIG])
        cur.execute(
            "INSERT INTO %Embedding.Config (Name, Configuration, EmbeddingClass, VectorLength) VALUES (?, ?, ?, ?)",
            [CONFIG, "{}", "IVGTest.FakeEmbedding", 3],
        )
        iris_connection.commit()
    finally:
        cur.close()
    yield
    cur = iris_connection.cursor()
    try:
        cur.execute("DELETE FROM %Embedding.Config WHERE Name = ?", [CONFIG])
        iris_connection.commit()
    finally:
        cur.close()


@pytest.fixture
def queued(iris_connection, fake_model):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_config=CONFIG, embedding_dimension=3)
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460eq_{run}"
    ids = {k: f"ivg460eq_{run}_{k}" for k in "abc"}
    for nid in ids.values():
        engine.create_node(nid, labels=["Ivg460Eq"], graph=graph)
    assert engine.enqueue_for_embedding(list(ids.values()), embedding_config=CONFIG, graph=graph) == 3
    yield engine, graph, ids
    native = engine._iris_obj()
    for nid in ids.values():
        native.kill("^EmbedQueue", nid)
    with contextlib.suppress(Exception):
        engine.erase_graph(graph)


def test_the_worker_writes_where_the_graph_reads(queued):
    from iris_vector_graph.schema import _call_classmethod

    engine, graph, ids = queued
    stats = json.loads(str(_call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "ProcessBatch", 10)))
    entries = {
        k: json.loads(str(_call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "GetEntry", nid)))
        for k, nid in ids.items()
    }
    assert {k: (e["status"], e["error"]) for k, e in entries.items()} == {k: ("DONE", "") for k in ids}, stats
    found = engine.kg_KNN_VEC("[1.0, 0.0, 0.0]", k=2, graph=graph, model_key=CONFIG)
    inverse = {v: k for k, v in ids.items()}
    assert [(inverse.get(n, n), round(float(s), 5)) for n, s in found] == [("a", 1.0), ("b", round(1 / math.sqrt(2), 5))]
    assert engine.embedding_count(graph=graph, model_key=CONFIG) == 3


def test_a_named_graph_with_no_route_is_refused_not_written_elsewhere(iris_connection, fake_model):
    """An entry for a graph that has no route for the model: ERROR, naming the graph and model; no vector is written
    to kg_NodeEmbeddings, where the graph's reads would not find it."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import _call_classmethod

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph, nid = f"ivg460eqn_{run}", f"ivg460eqn_{run}_a"
    engine.create_node(nid, graph=graph)
    _call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "Enqueue", nid, CONFIG, "", graph)
    try:
        _call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "ProcessBatch", 10)
        entry = json.loads(str(_call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "GetEntry", nid)))
        assert entry["status"] == "ERROR"
        assert entry["error"] == (
            f"no embedding route for graph '{graph}' and model '{CONFIG}'; create it with "
            f"engine.resolve_route(graph=..., model_key=..., create=True), or work the queue with "
            f"engine.process_embed_queue(); no vector was written"
        )
        cur = iris_connection.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {engine._t('kg_NodeEmbeddings')} WHERE node_id = ?", [nid])
            assert cur.fetchone()[0] == 0
        finally:
            cur.close()
    finally:
        engine._iris_obj().kill("^EmbedQueue", nid)
        with contextlib.suppress(Exception):
            engine.erase_graph(graph)
