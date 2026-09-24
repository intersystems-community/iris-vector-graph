"""Spec 231 FR-011: the Task Manager class is a thin caller of SyncOnce.

Read as source: the behaviour (a scheduled run syncing the graph) is in
tests/e2e/test_231_fhir_graph_sync_e2e.py, and needs Task Manager.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
TASK = (SRC / "FHIRGraphSyncTask.cls").read_text()
GRAPH = (SRC / "FHIRGraph.cls").read_text()


def test_extends_task_definition():
    assert re.search(r"Class Graph\.KG\.FHIRGraphSyncTask Extends %SYS\.Task\.Definition", TASK)


def test_graph_id_setting():
    """Task Manager stores a task's settings from the class's properties."""
    assert re.search(r"Property GraphId As %String\(MAXLEN = 256\)", TASK)


def test_on_task_runs_one_sync():
    body = TASK[TASK.index("Method OnTask()") :]
    assert "##class(Graph.KG.FHIRGraph).SyncOnce(..GraphId)" in body
    # busy is not a failure; only an error reply becomes a failed %Status.
    assert 'tRes.status = "error"' in body


def test_schedule_names_this_class_and_rounds_up_to_minutes():
    body = GRAPH[GRAPH.index("ClassMethod Schedule(") : GRAPH.index("ClassMethod Unschedule(")]
    assert 'tTask.TaskClass = "Graph.KG.FHIRGraphSyncTask"' in body
    assert "tTask.NameSpace = $NAMESPACE" in body
    assert "tInterval \\ 60 + (tInterval # 60 > 0)" in body
    # %SYS.Task.Name is MAXLEN 50.
    assert "$Extract(" in body and ", 1, 50)" in body


def test_watermark_moves_inside_the_batch_transaction():
    body = GRAPH[GRAPH.index("Method RunBatch(") : GRAPH.index("Method ResyncKey(")]
    tstart, update, commit = (body.index(s) for s in ("TSTART", "UPDATE Graph_KG.fhir_graphs", "TCOMMIT"))
    assert tstart < update < commit
