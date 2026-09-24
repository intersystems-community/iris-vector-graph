"""Spec 231 US1 / SC-001, SC-003: a FHIR repository projected as a named graph.

The projection reads the repository's own search tables, so only a live FHIR
namespace can show it: resources go in through the FHIR server, `Rebuild` runs in
IVGFHIR, and the assertions read `rdf_edges`, `^KG` and `fhir_unresolved` there.
Resource ids carry a per-run prefix; the repository is shared across runs.
"""

from __future__ import annotations

import json
import random
import zipfile

import pytest

from tests.e2e.fhir_conftest import GRAPH, FhirLoader

pytestmark = [pytest.mark.e2e]

EXTERNAL = "https://other.example/fhir/Organization/elsewhere"


@pytest.fixture(scope="module")
def built(fhir_conn, fhir_engine):
    """Patient, Practitioner, Encounter and Observation, plus a deleted Patient that
    a second Observation still points at. Rebuilt once for the module."""
    ld = FhirLoader(fhir_conn)
    p1, dr1, e1, o1, o2, gone, ghost = (
        ld.id(n) for n in ("p1", "dr1", "e1", "o1", "o2", "gone", "ghost")
    )
    ld.put({"resourceType": "Patient", "id": p1})
    ld.put({"resourceType": "Practitioner", "id": dr1})
    ld.put({"resourceType": "Patient", "id": gone})
    ld.put(
        {
            "resourceType": "Encounter",
            "id": e1,
            "status": "finished",
            "class": {"code": "AMB"},
            "subject": {"reference": f"Patient/{p1}"},
            "serviceProvider": {"reference": EXTERNAL},
            "participant": [{"individual": {"reference": f"Practitioner/{ghost}"}}],
        }
    )
    ld.put(
        {
            "resourceType": "Observation",
            "id": o1,
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
            "subject": {"reference": f"Patient/{p1}"},
            "encounter": {"reference": f"Encounter/{e1}"},
            "performer": [
                {"reference": f"Practitioner/{dr1}"},
                {"reference": f"Patient/{p1}"},
            ],
        }
    )
    ld.put(
        {
            "resourceType": "Observation",
            "id": o2,
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
            "subject": {"reference": f"Patient/{gone}"},
        }
    )
    ld.delete("Patient", gone)
    fhir_engine.fhir_graph_register(denylist=[])
    report = fhir_engine.fhir_graph_rebuild(GRAPH)
    keys = {
        "p1": f"Patient/{p1}",
        "dr1": f"Practitioner/{dr1}",
        "e1": f"Encounter/{e1}",
        "o1": f"Observation/{o1}",
        "o2": f"Observation/{o2}",
        "gone": f"Patient/{gone}",
        "ghost": f"Practitioner/{ghost}",
    }
    return {"report": report, "keys": keys, "loader": ld}


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _edges(conn, source):
    return {
        (p, o)
        for p, o in _rows(
            conn, "SELECT p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?", GRAPH, source
        )
    }


def _unresolved(conn, source):
    return {
        (p, t, r)
        for p, t, r in _rows(
            conn,
            "SELECT param, target, reason FROM Graph_KG.fhir_unresolved WHERE graph_id = ? AND source = ?",
            GRAPH,
            source,
        )
    }


def _gk(conn):
    import iris

    return iris.createIRIS(conn).classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH)


def _adjacent(conn, s, p, o):
    import iris

    irisobj = iris.createIRIS(conn)
    gk = _gk(conn)
    return bool(irisobj.isDefined("^KG", "out", gk, s, p, o)) and bool(
        irisobj.isDefined("^KG", "in", gk, o, p, s)
    )


def test_nodes_and_labels(fhir_conn, built):
    """Scenario 1: one node per live key, labelled with its type; the deleted one is absent."""
    k = built["keys"]
    for name, rtype in (("p1", "Patient"), ("dr1", "Practitioner"), ("e1", "Encounter"), ("o1", "Observation")):
        assert _rows(
            fhir_conn, "SELECT 1 FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", GRAPH, k[name]
        ), name
        assert _rows(
            fhir_conn,
            "SELECT label FROM Graph_KG.rdf_labels WHERE graph_id = ? AND s = ?",
            GRAPH,
            k[name],
        ) == [(rtype,)]
    assert not _rows(
        fhir_conn, "SELECT 1 FROM Graph_KG.nodes WHERE graph_id = ? AND node_id = ?", GRAPH, k["gone"]
    )


def test_every_live_key_is_a_node(fhir_conn, built):
    live = _rows(fhir_conn, 'SELECT COUNT(*) FROM "HSFHIR_X0001_R".Rsrc WHERE Deleted = 0')[0][0]
    nodes = _rows(fhir_conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", GRAPH)[0][0]
    assert nodes == live


def test_reference_edges(fhir_conn, built):
    """Scenario 2: single- and multi-valued references become edges named by param,
    in SQL and in the graph's ^KG adjacency, qualified with the SearchParameter URL."""
    k = built["keys"]
    expected = {
        ("subject", k["p1"]),
        ("patient", k["p1"]),
        ("performer", k["dr1"]),
        ("performer", k["p1"]),
        ("encounter", k["e1"]),
    }
    assert _edges(fhir_conn, k["o1"]) == expected
    for p, o in expected:
        assert _adjacent(fhir_conn, k["o1"], p, o), (p, o)
    quals = _rows(
        fhir_conn,
        "SELECT p, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s = ?",
        GRAPH,
        k["o1"],
    )
    for p, q in quals:
        url = json.loads(q)["searchParam"]
        assert url.startswith("http://hl7.org/fhir/SearchParameter/"), (p, url)


def test_external_missing_deleted(fhir_conn, built):
    """Scenarios 3, 4: references that cannot be edges are listed with a reason."""
    k = built["keys"]
    rows = _unresolved(fhir_conn, k["e1"])
    assert ("service-provider", EXTERNAL, "external") in rows
    assert ("participant", k["ghost"], "missing") in rows
    assert not any(p == "service-provider" for p, _ in _edges(fhir_conn, k["e1"]))
    assert ("subject", k["gone"], "deleted") in _unresolved(fhir_conn, k["o2"])
    assert _edges(fhir_conn, k["o2"]) == set()


def test_report_counts_per_param(built):
    """Scenario 6."""
    counts = built["report"]["counts"]
    assert counts["params"]["Observation.performer"] >= 2
    assert counts["params"]["Observation.subject"] >= 1
    assert set(counts["unresolved"]) == {"external", "missing", "deleted"}


def test_rebuild_is_idempotent(fhir_conn, fhir_engine, built):
    """Scenario 7: a second rebuild rewrites nothing and reports the same counts."""
    before = _rows(
        fhir_conn,
        "SELECT edge_id, s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? ORDER BY edge_id",
        GRAPH,
    )
    again = fhir_engine.fhir_graph_rebuild(GRAPH)
    after = _rows(
        fhir_conn,
        "SELECT edge_id, s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? ORDER BY edge_id",
        GRAPH,
    )
    assert after == before
    assert again["counts"] == built["report"]["counts"]
    assert again["stale_dropped"] == 0


def test_denylist(fhir_conn, fhir_engine, built):
    """Scenario 5: a denylisted param yields no edges and is reported at 0."""
    k = built["keys"]
    try:
        fhir_engine.fhir_graph_register(denylist=["Observation.performer"])
        report = fhir_engine.fhir_graph_rebuild(GRAPH)
        assert report["counts"]["params"]["Observation.performer"] == 0
        assert not any(p == "performer" for p, _ in _edges(fhir_conn, k["o1"]))
        assert not _adjacent(fhir_conn, k["o1"], "performer", k["dr1"])
    finally:
        fhir_engine.fhir_graph_register(denylist=[])
        fhir_engine.fhir_graph_rebuild(GRAPH)
    assert ("performer", k["dr1"]) in _edges(fhir_conn, k["o1"])


def test_status(fhir_engine, built):
    st = fhir_engine.fhir_graph_status(GRAPH)
    assert st["graph_id"] == GRAPH
    assert st["pending"] == 0
    assert st["edges"] >= 5


class TestColonGraphId:
    """SC-003: `fhir:IVGFHIR:X0001` round-trips through every store that keys on it."""

    def test_sql_and_globals(self, fhir_conn, built):
        k = built["keys"]
        got = _rows(
            fhir_conn,
            "SELECT DISTINCT graph_id FROM Graph_KG.rdf_edges WHERE s = ?",
            k["o1"],
        )
        assert got == [(GRAPH,)]
        assert _adjacent(fhir_conn, k["o1"], "subject", k["p1"])

    def test_cypher(self, fhir_engine, built):
        k = built["keys"]
        r = fhir_engine.execute_cypher(
            f"USE GRAPH '{GRAPH}' MATCH (o)-[:performer]->(x) WHERE o.id = '{k['o1']}' RETURN x.id"
        )
        rows = r.rows if hasattr(r, "rows") else r.get("rows", [])
        assert {str(row[0]) for row in rows} == {k["dr1"], k["p1"]}

    def test_embedding_route(self, fhir_engine, built):
        k = built["keys"]
        # A per-run direction: earlier runs' vectors stay in the shared repository.
        rng = random.Random(k["p1"])
        vec = [rng.uniform(-1, 1) for _ in range(4)]
        fhir_engine.store_embedding(k["p1"], vec, graph=GRAPH, model_key="ivg231")
        fhir_engine.store_embedding(k["dr1"], [-x for x in vec], graph=GRAPH, model_key="ivg231")
        hits = fhir_engine.kg_KNN_VEC(json.dumps(vec), k=1, graph=GRAPH, model_key="ivg231")
        assert hits and hits[0][0] == k["p1"]

    def test_snapshot(self, fhir_engine, built, tmp_path):
        path = str(tmp_path / "fhir231.zip")
        fhir_engine.save_snapshot(path, layers=["sql", "globals"])
        with zipfile.ZipFile(path) as zf:
            sql = [n for n in zf.namelist() if n.startswith("sql/")]
            assert any(GRAPH.encode() in zf.read(n) for n in sql)
