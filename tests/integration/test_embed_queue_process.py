"""Line coverage for Graph.KG.EmbedQueue.ProcessBatch and its guards, in scratch namespace IVG236.

ProcessBatch needs a node that exists in the entry's graph before it writes a vector, so the
cases that matter without an embedding model are the refusals: a node queued for a graph it
does not belong to, a request already pending for another graph, and a batch size bound.
"""

from __future__ import annotations

import json

import pytest

from tests.integration._236_world import World

EQ = "Graph.KG.EmbedQueue"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.native().kill("EmbedQueue")
    w.renew()
    e = w.engine()
    e.create_node("present")
    e.create_node("other-only", graph="g2")
    w.commit()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.kill("EmbedQueue")
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, method, *args):
    out = world.native().classMethodValue(EQ, method, *args)
    return "" if out is None else out


def entry(world, req):
    return json.loads(call(world, "GetEntry", req))


def test_enqueue_marks_a_request_pending(world):
    assert call(world, "Enqueue", "present", "cfg", "some text") == 1
    e = entry(world, "present")
    assert (e["status"], e["config"], e["graph"], e["node_id"]) == ("PENDING", "cfg", "", "present")
    assert call(world, "PendingCount") == 1


def test_a_pending_request_for_another_graph_is_refused(world):
    call(world, "Enqueue", "present", "cfg", "", "")
    out = call(world, "Enqueue", "present", "cfg", "", "g2")
    assert out != 1  # an error status, not $$$OK
    assert entry(world, "present")["graph"] == ""


def test_a_node_queued_for_a_graph_it_is_not_in_ends_in_error(world):
    call(world, "Enqueue", "other-only", "cfg", "", "")  # the node lives in g2, not the default graph
    out = json.loads(call(world, "ProcessBatch", 10))
    assert out == {"processed": 0, "errors": 1}
    e = entry(world, "other-only")
    assert e["status"] == "ERROR" and "is not in graph" in e["error"]


def test_a_node_that_exists_nowhere_ends_in_error(world):
    call(world, "Enqueue", "ghost", "cfg")
    assert json.loads(call(world, "ProcessBatch", 10))["errors"] == 1
    assert entry(world, "ghost")["status"] == "ERROR"


def test_an_entry_for_a_present_node_is_processed_one_way_or_the_other(world):
    call(world, "Enqueue", "present", "no-such-embedding-config", "text")
    out = json.loads(call(world, "ProcessBatch", 10))
    assert out["processed"] + out["errors"] == 1
    assert entry(world, "present")["status"] in ("DONE", "ERROR")


def test_only_pending_entries_are_picked_up_and_clear_done_removes_the_done_ones(world):
    call(world, "Enqueue", "present", "cfg")
    call(world, "ProcessBatch", 10)
    first = entry(world, "present")["status"]
    out = json.loads(call(world, "ProcessBatch", 10))
    assert out == {"processed": 0, "errors": 0}  # nothing is PENDING any more
    cleared = call(world, "ClearDone")
    assert cleared == (1 if first == "DONE" else 0)
    assert call(world, "PendingCount") == 0
