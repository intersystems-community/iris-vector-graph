"""EdgeScan.MatchEdges / MatchEdgesAllGraphs / BulkIngestEdgesFile, and the ledger half of Eraser.

Scratch namespace IVG236 (sentinel asserted before every write). The match scans read ^KG, so the
edges are written through the engine; the file ingest reads a JSON file on the server's disk.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.integration._236_world import PRED, World

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
ES = "Graph.KG.EdgeScan"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, method, *args):
    native = world.native()
    out = native.classMethodValue(ES, method, *args)
    world.scratch.require_scratch(native)
    return "" if out is None else out


def edges(raw):
    return {(e["s"], e["p"], e["o"]) for e in json.loads(str(raw))}


# ------------------------------------------------------------------------- match scans


def seed(world):
    e = world.engine()
    e.create_edge("a", PRED, "b")
    e.create_edge("a", "U", "c")
    e.create_edge("b", PRED, "c")
    e.create_edge("x", PRED, "y", graph="g1")
    world.commit()


def test_match_edges_filters_by_source_and_predicate(world):
    seed(world)
    assert edges(call(world, "MatchEdges", "a", "")) == {("a", PRED, "b"), ("a", "U", "c")}
    assert edges(call(world, "MatchEdges", "a", PRED)) == {("a", PRED, "b")}
    assert edges(call(world, "MatchEdges", "", PRED)) == {("a", PRED, "b"), ("b", PRED, "c")}
    assert edges(call(world, "MatchEdges", "nobody", "")) == set()


def test_match_edges_carries_the_weight(world):
    seed(world)
    rows = json.loads(str(call(world, "MatchEdges", "a", PRED)))
    # "g": each edge names its graph, "" the default (spec 253 FR-009, commit 56de32f5).
    assert rows == [{"s": "a", "p": PRED, "o": "b", "w": 1.0, "g": ""}]


def test_match_edges_reads_one_graph_and_the_all_graphs_form_reads_every_graph(world):
    seed(world)
    assert edges(call(world, "MatchEdges", "x", "", 0, "g1")) == {("x", PRED, "y")}
    assert edges(call(world, "MatchEdges", "x", "")) == set()
    every = edges(call(world, "MatchEdgesAllGraphs", "", ""))
    assert {("a", PRED, "b"), ("x", PRED, "y")} <= every
    assert edges(call(world, "MatchEdgesAllGraphs", "x", PRED)) == {("x", PRED, "y")}


# ------------------------------------------------------------------------ file ingest


def put_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    subprocess.run(["docker", "cp", str(path), f"{CONTAINER}:/tmp/{name}"], check=True, capture_output=True)
    return f"/tmp/{name}"


def test_bulk_ingest_edges_file_reads_json_from_the_servers_disk(world, tmp_path):
    body = json.dumps([{"s": "f1", "o": "f2"}, {"s": "f2", "o": "f3", "p": "OTHER"}, {"s": "f1", "o": "f1"}, {"s": "", "o": "f3"}])
    path = put_file(tmp_path, "ivg_edges.json", body)
    # the empty endpoint is skipped; a self-loop is an edge (4.3.0: bulk ingest dropped them)
    assert int(call(world, "BulkIngestEdgesFile", path, "LINKS")) == 3
    assert edges(call(world, "MatchEdges", "f1", "")) == {("f1", "LINKS", "f2"), ("f1", "LINKS", "f1")}
    cur = world.engine().conn.cursor()
    cur.execute("SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s LIKE 'f%' ORDER BY s, o_id")
    assert [tuple(r) for r in cur.fetchall()] == [("f1", "LINKS", "f1"), ("f1", "LINKS", "f2"), ("f2", "OTHER", "f3")]
    # a second ingest of the same file reports the same three edges as ingested and adds no row
    assert int(call(world, "BulkIngestEdgesFile", path, "LINKS")) == 3
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s LIKE 'f%'")
    assert cur.fetchone()[0] == 3


def test_a_file_larger_than_one_read_is_reassembled(world, tmp_path):
    rows = [{"s": f"big{i:05d}", "o": f"big{i + 1:05d}"} for i in range(2500)]  # > 32000 characters
    path = put_file(tmp_path, "ivg_big_edges.json", json.dumps(rows))
    assert int(call(world, "BulkIngestEdgesFile", path)) == 2500


# ------------------------------------------------------------------ erasing ledger rows


def test_erasing_a_graph_removes_its_ledger_statements_and_leaves_other_graphs(world):
    from iris_vector_graph.ledger import Changeset

    e = world.engine()
    e.ledger.enable()
    try:
        cs = Changeset(actor="t", actor_type="system", auto_stub_missing_nodes=True)
        cs.create_relationship("l1", "R", "l2", graph="gl")
        cs.create_relationship("l3", "R", "l4")
        e.ledger.commit(cs)
        native = world.native()
        assert any(native.isDefined("IVG.Ledger", "tuple", "l1", "R", "l2", g) > 0
                   for g in ("gl", native.classMethodValue("Graph.KG.GraphKey", "ForLedger", "gl")))
        e.erase_graph("gl")
        for g in ("gl", native.classMethodValue("Graph.KG.GraphKey", "ForLedger", "gl")):
            assert native.isDefined("IVG.Ledger", "tuple", "l1", "R", "l2", g) == 0
        default_key = native.classMethodValue("Graph.KG.GraphKey", "ForLedger", "")
        assert native.isDefined("IVG.Ledger", "tuple", "l3", "R", "l4", default_key) > 0
    finally:
        e.ledger.disable()
