"""Spec 235 US2: `Graph.KG.PageRank.RunJson(…, pExclude, pLimit)`, driven directly.

The oracle for an exclusion is the same walk on a second graph that never had the
excluded edges: same nodes, same seeds, so the scores must agree to 1e-12, divisor
included (contracts/objectscript.md).
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

NODES = ["Patient/p1", "Patient/p2", "Observation/o1", "Observation/o2", "Encounter/e1", "Provenance/v1"]
EDGES = [
    ("Observation/o1", "subject", "Patient/p1"),
    ("Observation/o2", "subject", "Patient/p2"),
    ("Observation/o1", "encounter", "Encounter/e1"),
    ("Encounter/e1", "subject", "Patient/p1"),
    ("Encounter/e1", "target", "Observation/o2"),
    ("Provenance/v1", "target", "Observation/o1"),
    ("Provenance/v1", "target", "Observation/o2"),
    ("Observation/o1", "in_patient_compartment", "Patient/p1"),
    ("Observation/o2", "in_patient_compartment", "Patient/p2"),
    ("Encounter/e1", "in_patient_compartment", "Patient/p1"),
]
SEEDS = ["Observation/o1"]


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def graphs(engine):
    run = uuid.uuid4().hex[:8]
    made = []

    def make(edges):
        g = f"t235ppr:{run}:{len(made)}"
        made.append(g)
        for n in NODES:
            engine.create_node(n, labels=[n.split("/")[0]], graph=g)
        for s, p, o in edges:
            engine.create_edge(s, p, o, graph=g)
        return g

    try:
        yield make
    finally:
        for g in made:
            engine.erase_graph(g)


def _raw(engine, g, *extra, bidir="1"):
    return str(
        engine._store._call_classmethod(
            "Graph.KG.PageRank", "RunJson", json.dumps(SEEDS), "0.85", "20", bidir, "1.0", g, *extra
        )
    )


def _scores(raw):
    return {r["id"]: r["score"] for r in json.loads(raw)}


def _close(a, b):
    assert set(a) == set(b), (sorted(a), sorted(b))
    for k in a:
        assert abs(a[k] - b[k]) < 1e-12, (k, a[k], b[k])


@pytest.mark.parametrize("bidir", ["0", "1"])
def test_defaults_byte_identical(engine, graphs, bidir):
    g = graphs(EDGES)
    assert _raw(engine, g, "", "1000", bidir=bidir) == _raw(engine, g, bidir=bidir)


def test_empty_array_is_no_exclusion(engine, graphs):
    g = graphs(EDGES)
    assert _raw(engine, g, "[]", "1000") == _raw(engine, g)


@pytest.mark.parametrize("bidir", ["0", "1"])
def test_bare_predicate_equals_graph_without_it(engine, graphs, bidir):
    full = graphs(EDGES)
    cut = graphs([e for e in EDGES if e[1] != "in_patient_compartment"])
    got = _scores(_raw(engine, full, json.dumps(["in_patient_compartment"]), "1000", bidir=bidir))
    _close(got, _scores(_raw(engine, cut, bidir=bidir)))


@pytest.mark.parametrize("bidir", ["0", "1"])
def test_typed_entry_only_that_source_type(engine, graphs, bidir):
    full = graphs(EDGES)
    cut = graphs([e for e in EDGES if not (e[0].startswith("Provenance/") and e[1] == "target")])
    got = _scores(_raw(engine, full, json.dumps(["Provenance.target"]), "1000", bidir=bidir))
    _close(got, _scores(_raw(engine, cut, bidir=bidir)))
    if bidir == "1":
        # Encounter/e1 -target-> Observation/o2 is kept, so it still carries rank.
        assert got.get("Encounter/e1", 0) > 0


def test_default_exclusion_set(engine, graphs):
    full = graphs(EDGES)
    drop = {"in_patient_compartment"}
    cut = graphs([e for e in EDGES if e[1] not in drop and not (e[0].startswith("Provenance/") and e[1] in ("target", "entity"))])
    excl = json.dumps(["in_patient_compartment", "Provenance.target", "Provenance.entity"])
    _close(_scores(_raw(engine, full, excl, "1000")), _scores(_raw(engine, cut)))


def test_limit_zero_is_every_scored_node(engine, graphs):
    g = graphs(EDGES)
    everything = json.loads(_raw(engine, g, "", "0"))
    assert everything == json.loads(_raw(engine, g))
    assert len(everything) == len(NODES)


def test_limit_three_is_the_top_three(engine, graphs):
    g = graphs(EDGES)
    everything = json.loads(_raw(engine, g, "", "0"))
    assert json.loads(_raw(engine, g, "", "3")) == everything[:3]


def test_malformed_exclusion_is_an_error(engine, graphs):
    g = graphs(EDGES)
    with pytest.raises(Exception):
        _raw(engine, g, "not json", "1000")
