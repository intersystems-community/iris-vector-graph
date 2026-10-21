"""Spec 253, FR-009: the per-translation "more than one graph?" probe, against IRIS.

The probe reads one row's graph and asks the (graph_id, node_id) index for a graph above it and one below; the default
graph is '' and is bound back as a parameter. Held equal to the MIN/MAX(graph_id) read it replaced as graphs that sort
after and before the default one come and go.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]


def _min_max_says(conn) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT MIN(graph_id), MAX(graph_id) FROM Graph_KG.nodes")
    row = cur.fetchone()
    return row is not None and row[0] != row[1]


def test_the_probe_answers_as_min_max(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.create_node(f"gp{T}_default", labels=[f"Gp{T}"])  # a default-graph node, so '' is held
    made = []
    try:
        assert e._more_than_one_graph() is _min_max_says(iris_connection)
        for g in (f"zz{T}", f"aa{T}"):  # one sorting after '' and every name, one before most
            e.create_node(f"gp{T}_{g}", labels=[f"Gp{T}"], graph=g)
            made.append(g)
            assert e._more_than_one_graph() is True
            assert _min_max_says(iris_connection) is True
    finally:
        for g in made:
            e.erase_graph(g)
        e.delete_node(f"gp{T}_default")
    assert e._more_than_one_graph() is _min_max_says(iris_connection)
