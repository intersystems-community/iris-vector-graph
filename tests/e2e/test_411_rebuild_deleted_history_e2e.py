"""4.1.1 — FHIR Rebuild resyncs live keys only, and still clears what a delete left.

A FHIR delete is soft, so the repository keeps every key ever deleted. Rebuild used
to resync each one; on the 4.1.1 test repo that was 282,582 deleted keys beside
23,638 live ones. See tests/unit/test_411_rebuild_deleted_history.py.

Each case deletes resources without a sync in between, so the graph still holds
them when Rebuild runs, and checks the graph afterwards.
"""

from __future__ import annotations

import pytest

from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]

RSRC = "HSFHIR_X0001_R.Rsrc"


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _exec(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
    finally:
        cur.close()


def _has_node(conn, key):
    return bool(
        _rows(conn, "SELECT 1 FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", GRAPH, key)
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


def _patient(pid):
    return {"resourceType": "Patient", "id": pid, "name": [{"family": "Rebuild"}]}


def _obs(oid, subject):
    return {
        "resourceType": "Observation",
        "id": oid,
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
        "subject": {"reference": subject},
    }


@pytest.fixture
def run(fhir_conn_required, fhir_engine):
    fhir_engine.fhir_graph_register(denylist=[])
    ld = FhirLoader(fhir_conn_required)
    made = []

    def put(resource):
        ld.put(resource)
        made.append((resource["resourceType"], resource["id"]))

    yield ld, put
    for rtype, rid in reversed(made):
        ld.dispatch("DELETE", f"/{rtype}/{rid}")
    fhir_engine.fhir_graph_sync(GRAPH)


def test_keys_counts_live_rows_only(fhir_conn_required, fhir_engine, run):
    ld, put = run
    for i in range(3):
        put(_patient(ld.id(f"gone{i}")))
    fhir_engine.fhir_graph_sync(GRAPH)
    for i in range(3):
        ld.delete("Patient", ld.id(f"gone{i}"))

    report = fhir_engine.fhir_graph_rebuild(GRAPH)

    live = _rows(
        fhir_conn_required,
        f"SELECT COUNT(*) FROM {RSRC} WHERE ID <= ? AND (Deleted IS NULL OR Deleted = 0)",
        report["wm_rsrc"],
    )[0][0]
    assert report["keys"] == live
    for i in range(3):
        assert not _has_node(fhir_conn_required, f"Patient/{ld.id(f'gone{i}')}")
    assert report["stale_dropped"] >= 3


def test_a_referrer_of_a_deleted_key_records_deleted(fhir_conn_required, fhir_engine, run):
    ld, put = run
    pid, oid = ld.id("p"), ld.id("o")
    put(_patient(pid))
    put(_obs(oid, f"Patient/{pid}"))
    fhir_engine.fhir_graph_sync(GRAPH)
    ld.delete("Patient", pid)

    fhir_engine.fhir_graph_rebuild(GRAPH)

    assert not _has_node(fhir_conn_required, f"Patient/{pid}")
    assert _has_node(fhir_conn_required, f"Observation/{oid}")
    assert ("subject", f"Patient/{pid}", "deleted") in _unresolved(
        fhir_conn_required, f"Observation/{oid}"
    )
    assert not _rows(
        fhir_conn_required,
        "SELECT 1 FROM Graph_KG.rdf_edges WHERE graph_id = ? AND o_id = ?",
        GRAPH,
        f"Patient/{pid}",
    )


def test_a_deleted_source_with_only_unresolved_rows_is_swept(
    fhir_conn_required, fhir_engine, run
):
    """No sync path leaves this, but Rebuild used to clear it, so it still does."""
    ld, put = run
    oid = ld.id("orphan")
    put(_obs(oid, f"Patient/{ld.id('nobody')}"))
    ld.delete("Observation", oid)
    _exec(
        fhir_conn_required,
        "INSERT INTO Graph_KG.fhir_unresolved (graph_id, source, param, target, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        GRAPH,
        f"Observation/{oid}",
        "subject",
        f"Patient/{ld.id('nobody')}",
        "missing",
    )

    fhir_engine.fhir_graph_rebuild(GRAPH)

    assert _unresolved(fhir_conn_required, f"Observation/{oid}") == set()


def test_a_second_rebuild_changes_nothing(fhir_conn_required, fhir_engine, run):
    ld, put = run
    pid = ld.id("stay")
    put(_patient(pid))
    put(_obs(ld.id("stay-o"), f"Patient/{pid}"))
    fhir_engine.fhir_graph_rebuild(GRAPH)
    before = (
        set(_rows(fhir_conn_required, "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH)),
        set(
            _rows(
                fhir_conn_required,
                "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?",
                GRAPH,
            )
        ),
    )

    again = fhir_engine.fhir_graph_rebuild(GRAPH)

    after = (
        set(_rows(fhir_conn_required, "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH)),
        set(
            _rows(
                fhir_conn_required,
                "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?",
                GRAPH,
            )
        ),
    )
    assert again["stale_dropped"] == 0
    assert after == before


def test_zz_sync_after_a_repository_reset_drops_what_is_gone(
    fhir_conn_required, fhir_engine, run
):
    """Last in the module: it empties the scratch repository. After a Reset every
    row is gone and MAX(ID) is behind the watermarks; the sync used to read no rows
    and leave the graph as it was. See tests/unit/test_411_sync_watermark_ahead.py."""
    import iris

    from tests.e2e.fhir_conftest import ENDPOINT

    ld, put = run
    pid = ld.id("reset")
    put(_patient(pid))
    fhir_engine.fhir_graph_sync(GRAPH)
    assert _has_node(fhir_conn_required, f"Patient/{pid}")

    iris.createIRIS(fhir_conn_required).classMethodVoid(
        "HS.FHIRServer.Installer", "Reset", "", ENDPOINT
    )
    out = fhir_engine.fhir_graph_sync(GRAPH)

    assert out["status"] == "ok", out
    assert out.get("rebuilt") == "repository_behind_watermark", out
    assert not _has_node(fhir_conn_required, f"Patient/{pid}")
    assert _rows(
        fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH
    )[0][0] == 0
