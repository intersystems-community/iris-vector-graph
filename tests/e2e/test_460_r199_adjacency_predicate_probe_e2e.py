"""4.6.0 R199, live: `status()` of a namespace with edges listed `adjacency predicate probe failed: <SYNTAX>`.

The walkthrough saw it on a fresh Community 2026.1 namespace; the enterprise 2026.3 build answers the same. The probe
called `native.get(["KG", "out", "", 0, ""])`, a Python list where the Native API takes the global's name, and the
server rejects that name with `<SYNTAX>` on every build. Its fallback, `native.orderAll`, is no Native API method. So
the check it guards, that the adjacency index ^KG("out", graph, s, p, o) matches `rdf_edges`, never ran.

It now reads the first entry of ^KG("out") with `nextSubscript` and asks `rdf_edges` for a row with that source,
predicate and graph. By hand:

* edges written through the engine are in both, so the probe reports nothing and the index reads consistent;
* an entry set straight into ^KG("out", 0, <a source that collates first>, "R199Ghost", ...) has no row, so the probe
  reports an index mismatch naming it. The ghost's source is the number -999000000000000000: numbers collate before
  strings and the default graph's key 0 before any graph name, so it is the first entry whatever else the namespace
  holds.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

NODES = ["r199:a", "r199:b", "r199:c"]
GHOST_SOURCE = -999000000000000000
GHOST_PRED = "R199Ghost"
PROBES = ("adjacency predicate probe", "predicate consistency probe", "Index mismatch")


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for n in NODES:
        e.delete_node(n, graph=None)
    e.bulk_create_nodes([{"id": n, "labels": ["R199"], "properties": {}} for n in NODES])
    e.bulk_create_edges([
        {"source_id": "r199:a", "predicate": "R199_ROAD", "target_id": "r199:b"},
        {"source_id": "r199:b", "predicate": "R199_ROAD", "target_id": "r199:c"},
    ])
    try:
        yield e
    finally:
        for n in NODES:
            e.delete_node(n, graph=None)


def _probe_errors(status):
    return [x for x in status.errors if any(p in x for p in PROBES)]


def test_status_of_a_namespace_with_edges_reports_no_probe_error(engine):
    status = engine.status()
    assert status.adjacency.kg_populated is True
    assert _probe_errors(status) == []
    assert status.adjacency.kg_predicates_consistent is True
    assert "Probe errors" not in status.report()


def test_an_adjacency_entry_with_no_edge_row_is_reported(engine):
    native = engine._iris_obj()
    native.set(1, "^KG", "out", 0, GHOST_SOURCE, GHOST_PRED, "r199:nowhere")
    try:
        status = engine.status()
    finally:
        native.kill("^KG", "out", 0, GHOST_SOURCE)
    assert status.adjacency.kg_predicates_consistent is False
    errors = _probe_errors(status)
    assert len(errors) == 1 and errors[0].startswith("Index mismatch"), status.errors
    assert f"{GHOST_SOURCE} -{GHOST_PRED}->" in errors[0], errors
    # and with the ghost gone it reads consistent again
    assert engine.status().adjacency.kg_predicates_consistent is True
