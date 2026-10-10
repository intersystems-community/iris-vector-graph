"""FHIRGraph when the graph's own store refuses a write mid-rebuild or mid-sync.

A BEFORE INSERT trigger that sets %ok=0 on Graph_KG.rdf_edges (in IVGFHIR) makes the projection fail
after the repository has been read. Rebuild and SyncOnce must answer an error object, record it where
Status can read it, leave the watermarks alone, and succeed once the trigger is gone. The trigger is
dropped in a finally block.
"""

from __future__ import annotations

import contextlib

import pytest

from iris_vector_graph.exceptions import FHIRGraphError
from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]


@contextlib.contextmanager
def refusing(conn, table="rdf_edges", event="INSERT"):
    name = f"ivg_refuse_fhir_{event.lower()}_{table}"
    cur = conn.cursor()
    cur.execute(
        f"CREATE TRIGGER Graph_KG.{name} BEFORE {event} ON Graph_KG.{table} "
        'LANGUAGE OBJECTSCRIPT { Set %ok=0 Set %msg="refused" }'
    )
    conn.commit()
    try:
        yield
    finally:
        conn.rollback()
        conn.cursor().execute(f"DROP TRIGGER Graph_KG.{name}")
        conn.commit()


def refusing_edge_inserts(conn):
    return refusing(conn, "rdf_edges", "INSERT")


@pytest.fixture
def loaded(fhir_conn, fhir_engine):
    ld = FhirLoader(fhir_conn)
    p1, o1 = ld.id("fp1"), ld.id("fo1")
    ld.put({"resourceType": "Patient", "id": p1})
    ld.put(
        {
            "resourceType": "Observation",
            "id": o1,
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
            "subject": {"reference": f"Patient/{p1}"},
        }
    )
    fhir_engine.fhir_graph_register(denylist=[])
    yield fhir_engine, ld, (p1, o1)
    ld.delete("Observation", o1)
    ld.delete("Patient", p1)


def test_rebuild_reports_an_error_when_the_edge_table_refuses_and_recovers(fhir_conn, loaded):
    engine, _, _ = loaded
    with refusing_edge_inserts(fhir_conn):
        with pytest.raises(FHIRGraphError, match="refused"):
            engine.fhir_graph_rebuild(GRAPH)
    ok = engine.fhir_graph_rebuild(GRAPH)
    assert ok.get("status") == "ok", ok


def test_sync_reports_an_error_when_the_edge_table_refuses_and_recovers(fhir_conn, loaded):
    engine, ld, (p1, _) = loaded
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    ld.put({"resourceType": "Observation", "id": ld.id("fo2"), "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1"}]},
            "subject": {"reference": f"Patient/{p1}"}})
    try:
        with refusing_edge_inserts(fhir_conn):
            with pytest.raises(FHIRGraphError, match="refused"):
                engine.fhir_graph_sync(GRAPH)
        after = engine.fhir_graph_sync(GRAPH)
        assert after.get("status") in ("ok", "busy"), after
    finally:
        ld.delete("Observation", ld.id("fo2"))


@pytest.mark.parametrize("table", ["nodes", "rdf_labels", "rdf_props"])
def test_rebuild_fails_cleanly_when_another_projection_table_refuses(fhir_conn, loaded, table):
    engine, _, _ = loaded
    with refusing(fhir_conn, table, "INSERT"):
        with pytest.raises(FHIRGraphError):
            engine.fhir_graph_rebuild(GRAPH)
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"


@pytest.mark.parametrize("table", ["rdf_edges", "nodes"])
def test_dropping_a_deleted_resource_fails_cleanly_when_the_store_refuses_the_delete(fhir_conn, loaded, table):
    engine, ld, (p1, o1) = loaded
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    ld.delete("Observation", o1)
    with refusing(fhir_conn, table, "DELETE"):
        try:
            engine.fhir_graph_rebuild(GRAPH)
        except FHIRGraphError:
            pass  # either way the graph must reconcile once the table accepts deletes again
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"


# ------------------------------------------------------------------ adjacency drift repair


def _gkey(obj):
    return obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH)


def _out_defined(obj, gkey, *subs):
    return int(obj.isDefined("KG", "out", gkey, *subs)) > 0


def test_a_resync_removes_adjacency_the_repository_no_longer_justifies(fhir_conn, loaded):
    import iris

    engine, ld, (p1, o1) = loaded
    obj = iris.createIRIS(fhir_conn)
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    gkey = _gkey(obj)
    key = f"Observation/{o1}"
    # an adjacency entry with no SQL row behind it: drift
    obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", key, "driftpred", "Patient/ghost", "1.0", gkey)
    assert _out_defined(obj, gkey, key, "driftpred", "Patient/ghost")
    # a new version of the resource makes the next sync resync its key
    ld.put({"resourceType": "Observation", "id": o1, "status": "amended",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
            "subject": {"reference": f"Patient/{p1}"}})
    engine.fhir_graph_sync(GRAPH)
    assert not _out_defined(obj, gkey, key, "driftpred", "Patient/ghost"), "drift survived the resync"
    assert _out_defined(obj, gkey, key), "the real adjacency was lost with it"


def test_a_resync_restores_adjacency_the_projection_lost(fhir_conn, loaded):
    import iris

    engine, ld, (p1, o1) = loaded
    obj = iris.createIRIS(fhir_conn)
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    gkey = _gkey(obj)
    key = f"Observation/{o1}"
    assert _out_defined(obj, gkey, key)
    obj.kill("KG", "out", gkey, key)  # the SQL rows stay; only the adjacency goes
    ld.put({"resourceType": "Observation", "id": o1, "status": "corrected",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
            "subject": {"reference": f"Patient/{p1}"}})
    engine.fhir_graph_sync(GRAPH)
    assert _out_defined(obj, gkey, key), "the resync did not rewrite the adjacency"


def test_dropping_a_deleted_resource_also_clears_adjacency_that_has_no_sql_row(fhir_conn, loaded):
    import iris

    engine, ld, (p1, o1) = loaded
    obj = iris.createIRIS(fhir_conn)
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    gkey = _gkey(obj)
    key = f"Observation/{o1}"
    obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", key, "driftpred", "Patient/ghost", "1.0", gkey)
    obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", "Patient/ghost", "driftin", key, "1.0", gkey)
    ld.delete("Observation", o1)
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    assert not _out_defined(obj, gkey, key), "the dropped node kept outbound adjacency"
    assert not _out_defined(obj, gkey, "Patient/ghost", "driftin", key), "the dropped node kept inbound adjacency"


# ------------------------------------------------------------------------ busy answers


class _HeldGraphLock:
    """Hold ^IVG.FHIRGraph(graph) in an ObjectScript process in IVGFHIR (a Native API lock is invisible
    to ObjectScript's Lock command)."""

    def __init__(self, graph, seconds=14):
        import os
        import subprocess
        import time

        container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "iris", "session", "IRIS", "-U", "IVGFHIR"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        self.proc.stdin.write(f'lock +^IVG.FHIRGraph("{graph}")\nhang {seconds}\nhalt\n')
        self.proc.stdin.flush()
        self.until = time.time() + seconds
        time.sleep(4)

    def release(self):
        import time

        self.proc.terminate()
        self.proc.wait(timeout=30)
        time.sleep(max(0.0, self.until - time.time()) + 1)


def test_rebuild_sync_and_reinterpret_answer_busy_while_another_process_holds_the_graph(fhir_conn, loaded):
    import iris

    engine, _, _ = loaded
    obj = iris.createIRIS(fhir_conn)
    held = _HeldGraphLock(GRAPH)
    try:
        assert engine.fhir_graph_rebuild(GRAPH).get("status") == "busy"
        assert engine.fhir_graph_sync(GRAPH).get("status") == "busy"
        import json

        raw = str(obj.classMethodValue("Graph.KG.FHIRGraph", "Reinterpret", GRAPH))
        assert json.loads(raw).get("status") == "busy"
    finally:
        held.release()
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"


# ------------------------------------------------------------------------- the Task Manager task


def _ivgfhir_session(*statements):
    import os
    import subprocess

    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    script = 'write "<<"_"OUT",!\n' + "\n".join(statements) + '\nwrite !,"OUT"_">>",!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", container, "iris", "session", "IRIS", "-U", "IVGFHIR"],
        input=script, capture_output=True, text=True, timeout=180,
    ).stdout
    body = out.split("<<OUT", 1)[1].split("OUT>>", 1)[0]
    return "\n".join(line for line in body.splitlines() if line.strip() and not line.startswith("IVGFHIR")).strip()


def test_the_sync_task_runs_one_sync_and_reports_an_error_as_a_status(loaded):
    engine, _, _ = loaded
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    out = _ivgfhir_session(
        "set t=##class(Graph.KG.FHIRGraphSyncTask).%New()",
        f'set t.GraphId="{GRAPH}"',
        'set sc=t.OnTask() write "OK:",$system.Status.IsOK(sc),!',
        'set t.GraphId="fhir:IVGFHIR:NOSUCH"',
        'set sc=t.OnTask() write "BAD:",$system.Status.IsOK(sc),":",$system.Status.GetErrorText(sc),!',
    )
    lines = {line.split(":", 1)[0]: line.split(":", 1)[1] for line in out.splitlines() if ":" in line}
    assert lines["OK"] == "1", out
    assert lines["BAD"].startswith("0:"), out
    assert "FHIR graph sync fhir:IVGFHIR:NOSUCH" in lines["BAD"] and "is not registered" in lines["BAD"], out


# ------------------------------------------------------------------------------ the link report


def test_link_report_classifies_absolute_references_against_the_repository(fhir_conn, loaded):
    import json

    import iris

    from tests.e2e.fhir_conftest import ENDPOINT

    engine, ld, (p1, o1) = loaded
    assert engine.fhir_graph_rebuild(GRAPH).get("status") == "ok"
    gone = ld.id("lr-gone")
    ld.put({"resourceType": "Patient", "id": gone})
    ld.delete("Patient", gone)
    engine.fhir_graph_rebuild(GRAPH)
    base = "https://repo.example" + ENDPOINT
    rows = [
        ("lr-src", "subject", f"{base}/Patient/{p1}"),
        ("lr-src", "subject", f"{base}/Patient/{gone}"),
        ("lr-src", "subject", f"{base}/Patient/{ld.id('lr-nowhere')}"),
        ("lr-src", "subject", "https://elsewhere.example/fhir/Patient/x"),
    ]
    cur = fhir_conn.cursor()
    try:
        for source, param, url in rows:
            cur.execute(
                "INSERT INTO Graph_KG.fhir_canonical_refs (graph_id, source, param, url, version, origin, kind) "
                "VALUES (?, ?, ?, ?, NULL, 'index', 'reference')",
                [GRAPH, source, param, url],
            )
        fhir_conn.commit()
        obj = iris.createIRIS(fhir_conn)
        report = json.loads(str(obj.classMethodValue("Graph.KG.FHIRGraph", "LinkReport", GRAPH, "lr-src")))
        status = {r["url"]: r["status"] for r in report["rows"]}
        assert status[f"{base}/Patient/{p1}"] == f"Patient/{p1}"  # resolved: the target key
        assert status[f"{base}/Patient/{gone}"] == "deleted"
        assert status[f"{base}/Patient/{ld.id('lr-nowhere')}"] == "missing"
        assert status["https://elsewhere.example/fhir/Patient/x"] not in (f"Patient/{p1}", "deleted")
        by_param = report["totals"]["by_param"]["subject"]
        assert report["source"] == "lr-src"
        assert by_param.get("resolved") == 1 and by_param.get("deleted") == 1 and by_param.get("missing") == 1, by_param
    finally:
        fhir_conn.rollback()
        cur.execute("DELETE FROM Graph_KG.fhir_canonical_refs WHERE graph_id = ? AND source = 'lr-src'", [GRAPH])
        fhir_conn.commit()
