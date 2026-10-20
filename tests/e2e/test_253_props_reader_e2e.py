"""Spec 253, T012 (FR-004): the native property reader equals `SELECT val` for every property.

Graph.KG.Cypher.Props reads rdf_props' pkprops index (graph_id, s, key, ID) and data global, resolved from
%Dictionary per process and checked (the slot holding `val`); a layout it cannot confirm reads with prepared SQL.
Measured on SNB SF0.1 (research.md §A): 1538 lookups 2 ms through the globals, 12 ms through prepared SQL.

Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
G = f"pr{T}_g"
LONG = "x" * 20000 + "end"


@pytest.fixture(scope="module")
def world(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.create_node(f"pr{T}_a", properties={"name": "alpha", "n": 3, "flag": True, "long": LONG})
    e.create_node(f"pr{T}_a", properties={"name": "alpha-in-g"}, graph=G)
    e.create_node(f"pr{T}_b", properties={"quote": "it's \"q\""}, graph=G)
    yield iris_connection
    e.erase_graph(G)
    e.delete_node(f"pr{T}_a")


def _sql(conn, graph, s, key):
    cur = conn.cursor()
    cur.execute(
        "SELECT val FROM Graph_KG.rdf_props WHERE COALESCE(graph_id, '') = COALESCE(?, '') AND s = ? AND \"key\" = ?",
        [graph, s, key],
    )
    row = cur.fetchone()
    return (True, row[0]) if row else (False, None)


def _props(conn, method, *args):
    import iris

    return iris.createIRIS(conn).classMethodValue("Graph.KG.Cypher.Props", method, *args)


def _native(conn, graph, s, key, force_sql=0):
    d = json.loads(_props(conn, "GetJson", graph, s, key, force_sql))
    return bool(d["found"]), d.get("val")


CASES = [
    ("", "a", "name"),
    ("", "a", "n"),
    ("", "a", "flag"),
    ("", "a", "long"),
    ("", "a", "missing"),
    ("g", "a", "name"),
    ("g", "b", "quote"),
    ("g", "b", "name"),
]


@pytest.mark.parametrize("graph,node,key", CASES)
@pytest.mark.parametrize("force_sql", [0, 1])
def test_the_reader_equals_select_val(world, graph, node, key, force_sql):
    g = G if graph == "g" else ""
    s = f"pr{T}_{node}"
    assert _native(world, g, s, key, force_sql) == _sql(world, g, s, key)


def test_the_layout_probe_accepts_this_install(world):
    assert int(_props(world, "LayoutOK")) == 1
