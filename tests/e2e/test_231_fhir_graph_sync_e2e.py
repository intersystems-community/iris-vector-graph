"""Spec 231 US2 / SC-002, SC-006: the graph follows the repository through SyncOnce.

Every change goes in through the FHIR server and reaches the graph by one polled sync,
with no rebuild. The last test rebuilds and compares: sync and rebuild must agree
node-for-node and edge-for-edge.
"""

from __future__ import annotations

import time

import pytest

from iris_vector_graph.exceptions import FHIRGraphError
from tests.e2e.fhir_conftest import GRAPH, FhirLoader, connect

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def synced(fhir_conn, fhir_engine):
    """Registered and rebuilt, so every test starts from a caught-up graph."""
    fhir_engine.fhir_graph_register(denylist=[])
    fhir_engine.fhir_graph_rebuild(GRAPH)
    return FhirLoader(fhir_conn)


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _has_node(conn, key):
    return bool(
        _rows(conn, "SELECT 1 FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", GRAPH, key)
    )


def _edges(conn, source):
    return set(
        _rows(conn, "SELECT p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", GRAPH, source)
    )


def _incoming(conn, target):
    return set(
        _rows(conn, "SELECT s, p FROM Graph_KG.rdf_edges WHERE graph_id = ? AND o_id = ?", GRAPH, target)
    )


def _unresolved(conn, source):
    return set(
        _rows(
            conn,
            "SELECT param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ?",
            GRAPH,
            source,
        )
    )


def _watermarks(conn):
    return _rows(conn, "SELECT wm_rsrc, wm_ver FROM Graph_KG.fhir_graphs WHERE graph_id = ?", GRAPH)[0]


def _adjacency(conn):
    """Every (s, p, o) under the graph's ^KG("out") and ^KG("in") subtrees."""
    import iris

    irisobj = iris.createIRIS(conn)
    gk = irisobj.classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH)
    out, inn = set(), set()
    for tree, acc in (("out", out), ("in", inn)):
        for a in irisobj.iterator("^KG", tree, gk).subscripts():
            for p in irisobj.iterator("^KG", tree, gk, a).subscripts():
                for b in irisobj.iterator("^KG", tree, gk, a, p).subscripts():
                    acc.add((a, p, b) if tree == "out" else (b, p, a))
    return out, inn


def _graph_state(conn):
    """The whole graph as sets, for the sync-equals-rebuild comparison."""
    out, inn = _adjacency(conn)
    return {
        "nodes": set(_rows(conn, "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH)),
        "labels": set(_rows(conn, "SELECT s, label FROM Graph_KG.rdf_labels WHERE graph_id = ?", GRAPH)),
        "edges": set(
            _rows(conn, "SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH)
        ),
        "unresolved": set(
            _rows(
                conn,
                "SELECT source, param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ?",
                GRAPH,
            )
        ),
        "kg_out": out,
        "kg_in": inn,
    }


def _obs(oid, subject, **extra):
    body = {
        "resourceType": "Observation",
        "id": oid,
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        "subject": {"reference": subject},
    }
    body.update(extra)
    return body


def test_create_update_delete_recreate(fhir_conn, fhir_engine, synced):
    """Scenarios 1-4 in sequence, one SyncOnce after each change."""
    ld = synced
    pa, pb, oa = ld.id("pa"), ld.id("pb"), ld.id("oa")
    kpa, kpb, koa = f"Patient/{pa}", f"Patient/{pb}", f"Observation/{oa}"

    # 1. create
    ld.put({"resourceType": "Patient", "id": pa})
    ld.put({"resourceType": "Patient", "id": pb})
    ld.put(_obs(oa, kpa))
    assert fhir_engine.fhir_graph_sync(GRAPH)["status"] == "ok"
    assert _has_node(fhir_conn, koa) and _has_node(fhir_conn, kpa)
    assert ("subject", kpa) in _edges(fhir_conn, koa)

    # 2. update replaces the old reference
    ld.put(_obs(oa, kpb))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _edges(fhir_conn, koa) == {("subject", kpb), ("patient", kpb)}

    # 3. delete: the node and every edge touching it go; incoming refs become 'deleted'
    fhir_engine.store_embedding(kpb, [0.1, 0.2, 0.3, 0.4], graph=GRAPH, model_key="ivg231")
    ld.delete("Patient", pb)
    fhir_engine.fhir_graph_sync(GRAPH)
    assert not _has_node(fhir_conn, kpb)
    assert _incoming(fhir_conn, kpb) == set()
    assert _edges(fhir_conn, koa) == set()
    assert {("subject", kpb, "deleted"), ("patient", kpb, "deleted")} <= _unresolved(fhir_conn, koa)
    assert kpb not in [h[0] for h in fhir_engine.kg_KNN_VEC(
        "[0.1, 0.2, 0.3, 0.4]", k=50, graph=GRAPH, model_key="ivg231"
    )]

    # 4. re-create turns the unresolved rows back into edges
    ld.put({"resourceType": "Patient", "id": pb})
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _has_node(fhir_conn, kpb)
    assert _edges(fhir_conn, koa) == {("subject", kpb), ("patient", kpb)}
    assert not any(t == kpb for _, t, _ in _unresolved(fhir_conn, koa))


def test_missing_target_created_later(fhir_conn, fhir_engine, synced):
    """Scenario 4: a reference to a key that does not exist yet becomes an edge when it does."""
    ld = synced
    pc, oc = ld.id("pc"), ld.id("oc")
    kpc, koc = f"Patient/{pc}", f"Observation/{oc}"
    ld.put(_obs(oc, kpc))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert ("subject", kpc, "missing") in _unresolved(fhir_conn, koc)
    assert _edges(fhir_conn, koc) == set()
    ld.put({"resourceType": "Patient", "id": pc})
    fhir_engine.fhir_graph_sync(GRAPH)
    assert ("subject", kpc) in _edges(fhir_conn, koc)
    assert not any(t == kpc for _, t, _ in _unresolved(fhir_conn, koc))


def test_sync_is_idempotent(fhir_conn, fhir_engine, synced):
    """Scenario 5: a second sync with nothing new changes nothing."""
    fhir_engine.fhir_graph_sync(GRAPH)
    before = _graph_state(fhir_conn)
    again = fhir_engine.fhir_graph_sync(GRAPH)
    assert again["keys"] == 0
    assert _graph_state(fhir_conn) == before


def test_crash_before_commit_leaves_nothing(fhir_conn, fhir_engine, synced):
    """Scenarios 5, 6: a batch that fails before commit applies nothing and does not move
    the watermarks; the next sync applies it."""
    import iris

    ld = synced
    pd, od = ld.id("pd"), ld.id("od")
    kod = f"Observation/{od}"
    fhir_engine.fhir_graph_sync(GRAPH)
    wm = _watermarks(fhir_conn)
    before = _graph_state(fhir_conn)
    ld.put({"resourceType": "Patient", "id": pd})
    ld.put(_obs(od, f"Patient/{pd}"))
    irisobj = iris.createIRIS(fhir_conn)
    irisobj.set("beforecommit", "^IVG.FHIRGraphFault", GRAPH)
    try:
        with pytest.raises(FHIRGraphError):
            fhir_engine.fhir_graph_sync(GRAPH)
    finally:
        irisobj.kill("^IVG.FHIRGraphFault", GRAPH)
    assert _watermarks(fhir_conn) == wm
    assert _graph_state(fhir_conn) == before
    assert fhir_engine.fhir_graph_status(GRAPH)["last_error"]
    fhir_engine.fhir_graph_sync(GRAPH)
    assert ("subject", f"Patient/{pd}") in _edges(fhir_conn, kod)
    assert _watermarks(fhir_conn) != wm
    assert not fhir_engine.fhir_graph_status(GRAPH)["last_error"]


def test_busy(fhir_conn, fhir_engine, synced):
    """A sync that finds the graph's lock held returns busy, not an error."""
    import iris

    other = connect()
    try:
        iris.createIRIS(other).classMethodVoid("IVGTest.FHIRLoad", "HoldLock", GRAPH)
        assert fhir_engine.fhir_graph_sync(GRAPH)["status"] == "busy"
    finally:
        iris.createIRIS(other).classMethodVoid("IVGTest.FHIRLoad", "ReleaseLock", GRAPH)
        other.close()


def test_schedule_and_task(fhir_conn, fhir_engine, synced):
    """Scenario 7: one Task Manager task per graph; OnTask runs one sync."""
    import iris

    irisobj = iris.createIRIS(fhir_conn)
    out = fhir_engine.fhir_graph_schedule(GRAPH, interval_s=90)
    try:
        assert out["minutes"] == 2
        tid = out["task_id"]
        task = irisobj.classMethodObject("%SYS.Task", "%OpenId", tid)
        assert task.get("TaskClass") == "Graph.KG.FHIRGraphSyncTask"
        assert task.get("NameSpace") == "IVGFHIR"
        assert task.get("DailyIncrement") == 2
        assert fhir_engine.fhir_graph_status(GRAPH)["task_id"] == str(tid)
        # Rescheduling updates the same task.
        again = fhir_engine.fhir_graph_schedule(GRAPH, interval_s=180)
        assert again["task_id"] == tid and again["minutes"] == 3
    finally:
        gone = fhir_engine.fhir_graph_unschedule(GRAPH)
    assert gone["removed"] is True
    assert not irisobj.classMethodValue("%SYS.Task", "%ExistsId", tid)
    assert fhir_engine.fhir_graph_status(GRAPH)["task_id"] == ""

    ld = synced
    pe = ld.id("pe")
    ld.put({"resourceType": "Patient", "id": pe})
    task_def = irisobj.classMethodObject("Graph.KG.FHIRGraphSyncTask", "%New")
    task_def.set("GraphId", GRAPH)
    assert irisobj.classMethodValue("%SYSTEM.Status", "IsOK", task_def.invoke("OnTask"))
    assert _has_node(fhir_conn, f"Patient/{pe}")


def test_500_changes_within_10s(fhir_conn, fhir_engine, synced):
    """SC-006: 250 Patients and 250 Observations pointing at them, one sync."""
    ld = synced
    fhir_engine.fhir_graph_sync(GRAPH)
    for i in range(250):
        pid = ld.id(f"bulk-p{i}")
        ld.put({"resourceType": "Patient", "id": pid})
        ld.put(_obs(ld.id(f"bulk-o{i}"), f"Patient/{pid}"))
    t0 = time.perf_counter()
    out = fhir_engine.fhir_graph_sync(GRAPH)
    elapsed = time.perf_counter() - t0
    assert out["status"] == "ok"
    assert elapsed <= 10.0, f"sync of 500 changes took {elapsed:.2f}s"
    assert ("subject", f"Patient/{ld.id('bulk-p249')}") in _edges(
        fhir_conn, f"Observation/{ld.id('bulk-o249')}"
    )


def test_sync_equals_rebuild(fhir_conn, fhir_engine, synced):
    """SC-002: after every change above, the synced graph is the rebuilt graph."""
    fhir_engine.fhir_graph_sync(GRAPH)
    synced_state = _graph_state(fhir_conn)
    report = fhir_engine.fhir_graph_rebuild(GRAPH)
    assert report["stale_dropped"] == 0
    rebuilt = _graph_state(fhir_conn)
    for part in synced_state:
        assert synced_state[part] == rebuilt[part], (
            part,
            sorted(synced_state[part] ^ rebuilt[part])[:10],
        )


def test_erase_keeps_registration_and_resets_watermarks(fhir_conn, fhir_engine, synced):
    """Erasing a FHIR graph drops its content and unresolved rows, keeps its registry
    row, and zeroes its watermarks, so the next rebuild re-projects everything."""
    ld = synced
    oid = ld.id("oerase")
    ld.put(_obs(oid, f"Patient/{ld.id('perase-missing')}"))
    fhir_engine.fhir_graph_sync(GRAPH)
    count = "SELECT COUNT(*) FROM Graph_KG.{} WHERE graph_id = ?"
    assert _rows(fhir_conn, count.format("fhir_unresolved"), GRAPH)[0][0] > 0

    fhir_engine.erase_graph(GRAPH)
    assert _rows(fhir_conn, count.format("nodes"), GRAPH)[0][0] == 0
    assert _rows(fhir_conn, count.format("fhir_unresolved"), GRAPH)[0][0] == 0
    assert _watermarks(fhir_conn) == (0, 0)

    fhir_engine.fhir_graph_rebuild(GRAPH)
    assert _has_node(fhir_conn, f"Observation/{oid}")
    assert all(w > 0 for w in _watermarks(fhir_conn))
