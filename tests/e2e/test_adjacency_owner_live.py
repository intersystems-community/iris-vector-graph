"""Spec 245 — the adjacency writer module keeps the layout of record, live.

`Graph.KG.Adjacency` is now the one class that sets `^KG("out")` and `^KG("in")`.
EdgeScan's primitives, the ledger and the loader all reach it. This test writes the
same single edge through each path and compares the stores with the values the
pre-change code produced (EdgeScan.WriteAdjacency, LedgerApply.OpCreateRel,
Loader.Load), so a refactor that changes one counter or one mirror fails here.

Expected values, recorded from the pre-change code for one edge with weight 1.0 under
the default graph key 0:

    ^KG("out", 0, s, p, o) = 1       ^KG("in", 0, o, p, s) = 1
    ^KG("deg", 0, s)       = 1       ^KG("degp", 0, s, p)  = 1
    ^KG("__version") moves by 1 per write

The Loader is the one intended change. It used to write the legacy no-shard layout
`^KG("out", s, p, o)`, which no reader walks. It now writes graph key 0.

Needs a live IRIS (ivg-iris-enterprise). The Loader case wipes `^KG` of the namespace
the connection is in (Graph.KG.Loader.Load does `Kill ^KG`), so it runs only when
IVG_ADJACENCY_LOADER_SCRATCH=1 is set, and only in a scratch namespace.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH = 0  # the default graph key (ADR-0001, ADR-0003)


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("this test reads ^KG in a live namespace; SKIP_IRIS_TESTS=true cannot run it.")
    if iris_connection is None:
        pytest.fail("no live IRIS connection.")


@pytest.fixture
def native(iris_connection):
    _require_iris(iris_connection)
    import iris as iris_mod

    return iris_mod.createIRIS(iris_connection)


@pytest.fixture
def triple():
    """A triple no other test or row shares, with the store cleaned afterwards."""
    tag = uuid.uuid4().hex[:10]
    return f"ADJ245{tag}S", f"ADJ245{tag}P", f"ADJ245{tag}O"


def _snapshot(native, s, p, o):
    """The five values the layout of record fixes for one edge, plus the version.

    The Native API hands back None for a node that is not there; "" is how the expectations spell it.
    """

    def get(*subs):
        value = native.getString(*subs)
        return "" if value is None else value

    return {
        "out": get("^KG", "out", GRAPH, s, p, o),
        "in": get("^KG", "in", GRAPH, o, p, s),
        "deg": get("^KG", "deg", GRAPH, s),
        "degp": get("^KG", "degp", GRAPH, s, p),
        "version": get("^KG", "__version"),
    }


def _expected(version_before, s, p, o):
    return {
        "out": "1",
        "in": "1",
        "deg": "1",
        "degp": "1",
        "version": str(int(version_before) + 1),
    }


def _cleanup(native, s, p, o):
    native.classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", s, p, o, GRAPH)


def _version(native) -> str:
    return native.getString("^KG", "__version") or "0"


# --- EdgeScan path ----------------------------------------------------------


def test_edgescan_write_matches_the_recorded_layout(native, triple):
    s, p, o = triple
    before = _version(native)
    try:
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", GRAPH)
        got = _snapshot(native, s, p, o)
        assert got == _expected(before, s, p, o), f"layout drifted: {got}"
    finally:
        _cleanup(native, s, p, o)


def test_edgescan_delete_takes_the_counters_back_to_nothing(native, triple):
    s, p, o = triple
    try:
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", GRAPH)
        _cleanup(native, s, p, o)
        got = _snapshot(native, s, p, o)
        assert got["out"] == "" and got["in"] == ""
        assert got["deg"] == "" and got["degp"] == ""
    finally:
        _cleanup(native, s, p, o)


def test_keyed_write_of_one_edge_is_the_same_as_the_unkeyed_write(native, triple):
    """A triple with one edge is byte-identical to one WriteAdjacency wrote (spec 234)."""
    s, p, o = triple
    before = _version(native)
    try:
        native.classMethodValue(
            "Graph.KG.EdgeScan", "WriteAdjacencyKeyed", s, p, o, "1.0", GRAPH, 7
        )
        got = _snapshot(native, s, p, o)
        assert got["out"] == "1" and got["in"] == "1"
        assert got["deg"] == "1" and got["degp"] == "1"
        assert int(got["version"]) >= int(before) + 1
    finally:
        _cleanup(native, s, p, o)


# --- ledger path ------------------------------------------------------------


def _ledger_commit(native, ops: list[dict]) -> dict:
    """Commit one changeset through Graph.KG.Ledger.Commit, enabling the ledger first."""
    if native.classMethodValue("Graph.KG.Ledger", "State") == "never-enabled":
        native.classMethodValue("Graph.KG.Ledger", "Enable", 0)
    meta = {
        "schema": "Graph_KG",
        "kind": "changeset",
        "idempotency_key": uuid.uuid4().hex,
        "fingerprint": uuid.uuid4().hex,
    }
    out = native.classMethodValue(
        "Graph.KG.Ledger", "Commit", json.dumps({"ops": ops}), json.dumps(meta)
    )
    return json.loads(out)


def test_ledger_create_rel_matches_the_recorded_layout(native, triple):
    s, p, o = triple
    try:
        for node in (s, o):
            _ledger_commit(native, [{"op": "create_node", "id": node}])
        before = _version(native)
        result = _ledger_commit(
            native, [{"op": "create_rel", "tuple": {"s": s, "p": p, "o": o}, "quals": {}}]
        )
        assert result.get("ok") == 1, result
        got = _snapshot(native, s, p, o)
        assert got == _expected(before, s, p, o), f"ledger layout drifted: {got}"
    finally:
        _cleanup(native, s, p, o)


def test_ledger_create_rel_over_an_existing_triple_does_not_double_count(native, triple):
    """The existing-triple branch refreshes the weight only (Adjacency.Reweight)."""
    s, p, o = triple
    try:
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", GRAPH)
        for node in (s, o):
            _ledger_commit(native, [{"op": "create_node", "id": node}])
        result = _ledger_commit(
            native,
            [
                {
                    "op": "create_rel",
                    "tuple": {"s": s, "p": p, "o": o},
                    "quals": {"weight": 3.0},
                }
            ],
        )
        assert result.get("ok") == 1, result
        got = _snapshot(native, s, p, o)
        assert got["out"] == "3" and got["in"] == "3", f"weight not refreshed: {got}"
        assert got["deg"] == "1" and got["degp"] == "1", f"counters moved: {got}"
    finally:
        _cleanup(native, s, p, o)


# --- loader path (wipes ^KG: scratch namespace only) -------------------------


@pytest.mark.skipif(
    os.environ.get("IVG_ADJACENCY_LOADER_SCRATCH") != "1",
    reason="set IVG_ADJACENCY_LOADER_SCRATCH=1 in a scratch namespace: Loader.Load kills ^KG",
)
def test_loader_writes_the_layout_of_record_under_graph_zero(native, tmp_path):
    csv = tmp_path / "edges.csv"
    csv.write_text("s,p,o\nADJ245L_S,ADJ245L_P,ADJ245L_O\n", encoding="utf-8")
    native.classMethodValue("Graph.KG.Loader", "Load", str(csv))
    assert native.getString("^KG", "out", GRAPH, "ADJ245L_S", "ADJ245L_P", "ADJ245L_O") == "1"
    assert native.getString("^KG", "in", GRAPH, "ADJ245L_O", "ADJ245L_P", "ADJ245L_S") == "1"
    assert native.getString("^KG", "deg", GRAPH, "ADJ245L_S") == "1"
    assert native.getString("^KG", "degp", GRAPH, "ADJ245L_S", "ADJ245L_P") == "1"
    # The legacy no-shard spelling must be gone.
    assert native.getString("^KG", "out", "ADJ245L_S", "ADJ245L_P", "ADJ245L_O") == ""
