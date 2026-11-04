"""4.6.0 R087: `start_background_embedding` schedules a task class that exists, and the task works the queue.

`Graph.KG.EmbedQueue.StartBackgroundTask` saved a `%SYS.Task` whose `TaskClass` was `Graph.KG.EmbedQueueTask`, a class
no release shipped, so the task could not run and the queue stayed PENDING. It now runs `Graph.KG.EmbedQueueTask`
(`ProcessBatch` until no entry is PENDING, `BatchSize` at a time), and reuses one task per namespace instead of adding a
task to the Task Manager on every call.

What the task does here, worked by hand, without an `%Embedding.Config` (the test container has none): an entry for a
node id queued for a graph that does not hold the node is refused by `ProcessBatch` before any embedding call, with the
message `node <id> is not in graph '<graph>'; the entry was queued for a graph this node does not belong to, so no
vector was written`, and its status becomes ERROR. Nothing else would change that entry, so the change is the task's.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def queued(iris_connection):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import _call_classmethod

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    node_id, graph = f"ivg460bg_{run}_x", f"ivg460bg_{run}"
    _call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "Enqueue", node_id, "", "", graph)
    yield engine, node_id, graph
    native = engine._iris_obj()
    native.kill("^EmbedQueue", node_id)
    task_id = native.get("^IVG.EmbedQueueTask")
    if task_id:
        native.classMethodValue("%SYS.Task", "%DeleteId", str(task_id))
        native.kill("^IVG.EmbedQueueTask")


def _entry(engine, req_id) -> dict:
    from iris_vector_graph.schema import _call_classmethod

    return json.loads(str(_call_classmethod(engine.conn, "Graph.KG.EmbedQueue", "GetEntry", req_id)))


def test_the_task_class_ships():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "EmbedQueueTask.cls"
    assert "Class Graph.KG.EmbedQueueTask Extends %SYS.Task.Definition" in src.read_text()


def test_the_background_task_works_the_queue(queued):
    engine, node_id, graph = queued
    assert _entry(engine, node_id)["status"] == "PENDING"

    task_id = engine.start_background_embedding(batch_size=10)
    assert task_id.isdigit(), task_id

    deadline = time.monotonic() + 90
    entry = _entry(engine, node_id)
    while entry["status"] == "PENDING" and time.monotonic() < deadline:
        time.sleep(1)
        entry = _entry(engine, node_id)
    assert (entry["status"], entry["error"]) == (
        "ERROR",
        f"node {node_id} is not in graph '{graph}'; the entry was queued for a graph this node does not belong to, "
        "so no vector was written",
    )


def test_a_second_call_reuses_the_namespaces_task(queued):
    engine, _, _ = queued
    first = engine.start_background_embedding(batch_size=10)
    second = engine.start_background_embedding(batch_size=5)
    assert first.isdigit() and second == first
