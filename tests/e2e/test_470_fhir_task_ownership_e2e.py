"""4.7.0 row 26c: `fhir_graph_schedule` and `fhir_graph_unschedule` act only on the graph's own Task Manager task.

`Graph.KG.FHIRGraph.Schedule` opened, and `Unschedule` deleted, whichever `%SYS.Task` had the ID in
`fhir_graphs.task_id`. After a restore into another instance (row 26 archives the registration with its `task_id`),
or once the graph's task was deleted and the ID reused, that is another task: on 4.6.5 `Unschedule` deleted it, and
`Schedule` rewrote it into a FHIR sync task. Now each first checks that the task is the graph's sync task (its class
`Graph.KG.FHIRGraphSyncTask`, this namespace, this graph), and otherwise raises `FHIRGraphError` naming it, with
nothing changed.

The namespace has no FHIR server, so the graphs are registered at the table level; the foreign task is an on-demand
`%SYS.Task.PurgeErrorsAndLogs` task in `%SYS`, which never runs by itself, deleted after the test.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_fhir_task_ownership_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
LAZY_FHIR_TABLES = ("fhir_graphs", "fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _tables(conn) -> set:
    cur = conn.cursor()
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME IN "
        f"({', '.join('?' * len(LAZY_FHIR_TABLES))})", list(LAZY_FHIR_TABLES),
    )
    return {str(r[0]) for r in cur.fetchall()}


@pytest.fixture
def world():
    """Two registered graphs, the engine, the Native API, and every task the test makes, deleted after it."""
    import iris

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema
    from iris_vector_graph.utils import _split_sql_statements

    conn = _connect()
    native = iris.createIRIS(conn)
    before = _tables(conn)
    cur = conn.cursor()
    for stmt in _split_sql_statements(GraphSchema.get_fhir_graph_schema_sql()):
        if stmt.strip():
            try:
                cur.execute(stmt)
            except Exception as e:  # noqa: BLE001 - the indexes answer "already exists" when the tables are there
                if "already" not in str(e).lower():
                    raise
    u = uuid.uuid4().hex[:8]
    graphs = [f"fhir:T470T:{u}a", f"fhir:T470T:{u}b"]
    for g in graphs:
        cur.execute(
            "INSERT INTO Graph_KG.fhir_graphs (graph_id, repo_id, rsrc_schema, search_schema, ver_schema, interval_s) "
            "VALUES (?, 'X0470', 'HSFHIR_X0470_R', 'HSFHIR_X0470_S', 'HSFHIR_X0470_V', 3600)", [g])
    conn.commit()
    tasks: list = []
    yield {"conn": conn, "native": native, "engine": IRISGraphEngine(conn, embedding_dimension=rr.DIM),
           "graphs": graphs, "tasks": tasks}
    for tid in tasks:
        if int(native.classMethodValue("%SYS.Task", "%ExistsId", tid)):
            native.classMethodValue("%SYS.Task", "%DeleteId", tid)
    cur.execute("DELETE FROM Graph_KG.fhir_graphs WHERE graph_id %STARTSWITH ?", [f"fhir:T470T:{u}"])
    conn.commit()
    for t in sorted(_tables(conn) - before):
        cur.execute(f"DROP TABLE Graph_KG.{t}")
    conn.commit()
    conn.close()


def _foreign_task(w) -> str:
    t = w["native"].classMethodObject("%SYS.Task", "%New")
    t.set("Name", "t470 not IVG's")
    t.set("NameSpace", "%SYS")
    t.set("TaskClass", "%SYS.Task.PurgeErrorsAndLogs")
    t.set("TimePeriod", 5)  # on demand: never runs by itself
    assert int(w["native"].classMethodValue("%SYSTEM.Status", "IsOK", t.invoke("%Save")))
    tid = str(t.invoke("%Id"))
    w["tasks"].append(tid)
    return tid


def _task(w, tid):
    o = w["native"].classMethodObject("%SYS.Task", "%OpenId", tid)
    return None if o is None else (o.get("Name"), o.get("TaskClass"), o.get("NameSpace"), o.get("TimePeriod"))


def _stored_task_id(w, graph):
    cur = w["conn"].cursor()
    cur.execute("SELECT task_id FROM Graph_KG.fhir_graphs WHERE graph_id = ?", [graph])
    return cur.fetchone()[0]


def _point(w, graph, tid):
    w["conn"].cursor().execute("UPDATE Graph_KG.fhir_graphs SET task_id = ? WHERE graph_id = ?", [tid, graph])
    w["conn"].commit()


@pytest.mark.parametrize("call", ("unschedule", "schedule"))
def test_a_task_that_is_not_ivgs_is_refused_and_left_alone(world, call):
    from iris_vector_graph.exceptions import FHIRGraphError

    w, graph = world, world["graphs"][0]
    tid = _foreign_task(w)
    _point(w, graph, tid)
    before = _task(w, tid)
    with pytest.raises(FHIRGraphError, match=rf"task {tid}.*not .*sync task"):
        getattr(w["engine"], f"fhir_graph_{call}")(graph)
    assert _task(w, tid) == before, "the other task was changed or deleted"
    assert str(_stored_task_id(w, graph)) == tid, "task_id was cleared or changed"


@pytest.mark.parametrize("call", ("unschedule", "schedule"))
def test_another_graphs_sync_task_is_refused_and_left_alone(world, call):
    from iris_vector_graph.exceptions import FHIRGraphError

    w, (mine, other) = world, world["graphs"]
    tid = str(w["engine"].fhir_graph_schedule(other)["task_id"])
    w["tasks"].append(tid)
    _point(w, mine, tid)
    before = _task(w, tid)
    with pytest.raises(FHIRGraphError, match=rf"task {tid}.*{other}"):
        getattr(w["engine"], f"fhir_graph_{call}")(mine)
    assert _task(w, tid) == before
    assert str(_stored_task_id(w, mine)) == tid


def test_the_graphs_own_task_is_scheduled_and_unscheduled(world):
    w, graph = world, world["graphs"][0]
    first = w["engine"].fhir_graph_schedule(graph)
    tid = str(first["task_id"])
    w["tasks"].append(tid)
    assert _task(w, tid)[1:3] == ("Graph.KG.FHIRGraphSyncTask", NS)
    again = w["engine"].fhir_graph_schedule(graph, interval_s=120)
    assert str(again["task_id"]) == tid, "rescheduling made a second task"
    gone = w["engine"].fhir_graph_unschedule(graph)
    assert gone["removed"] is True
    assert _task(w, tid) is None
    assert _stored_task_id(w, graph) in (None, "")


def test_a_task_id_naming_no_task_is_cleared_by_unschedule_and_replaced_by_schedule(world):
    w, graph = world, world["graphs"][0]
    tid = _foreign_task(w)
    w["native"].classMethodValue("%SYS.Task", "%DeleteId", tid)
    _point(w, graph, tid)
    assert w["engine"].fhir_graph_unschedule(graph)["removed"] is False
    assert _stored_task_id(w, graph) in (None, "")
    _point(w, graph, tid)
    new = str(w["engine"].fhir_graph_schedule(graph)["task_id"])
    w["tasks"].append(new)
    assert _task(w, new)[1] == "Graph.KG.FHIRGraphSyncTask"
