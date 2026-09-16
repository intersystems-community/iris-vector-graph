"""A named-graph edge insert must not write into the default graph's adjacency.

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_edge_class_compiles.py

While `Graph.KG.Edge` declared `Index GraphIdx ... As Graph.KG.GraphIndex`, a raw
SQL insert with `graph_id = 'probe-graph'` produced:

    ^KG("out", 0, s, p, o)             = 1.0    <- default graph, wrong
    ^KG("out", "probe-graph", s, p, o) = <none>  <- its own graph, empty
    ^KG("deg", s)                      = 1       <- no graph key
    ^KG("degp", s, p)                  = 1       <- no graph key

A source-level check cannot show this; the index fires inside IRIS on insert, so
the assertion has to be made against the live globals.
"""

from __future__ import annotations

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_GRAPH = "fnidx-probe-graph"


def _iris_obj(conn):
    import iris as _iris

    return _iris.createIRIS(conn)


def _compiled(conn, class_name: str) -> bool:
    return bool(
        int(_iris_obj(conn).classMethodValue("%Dictionary.CompiledClass", "%ExistsId", class_name))
    )


@pytest.fixture()
def probe_edge(iris_connection):
    """Insert one named-graph edge by raw SQL, then remove it and its globals."""
    suffix = uuid.uuid4().hex[:8]
    s, p, o = f"FNIDX_S_{suffix}", f"FNIDX_P_{suffix}", f"FNIDX_O_{suffix}"
    cur = iris_connection.cursor()
    cur.execute(
        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)",
        [s, p, o, _GRAPH],
    )
    iris_connection.commit()
    try:
        yield s, p, o
    finally:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ?", [s])
        iris_connection.commit()
        obj = _iris_obj(iris_connection)
        for subs in (
            ("out", 0, s, p, o),
            ("in", 0, o, p, s),
            ("out", _GRAPH, s, p, o),
            ("in", _GRAPH, o, p, s),
            ("deg", s),
            ("degp", s, p),
        ):
            try:
                obj.kill("KG", *subs)
            except Exception:
                pass


def test_named_graph_insert_does_not_write_default_graph_adjacency(
    iris_connection, probe_edge
):
    """The edge must not appear under graph key 0."""
    s, p, o = probe_edge
    obj = _iris_obj(iris_connection)
    assert obj.get("KG", "out", 0, s, p, o) is None, (
        f'^KG("out", 0, ...) was written for an edge in graph {_GRAPH!r}. '
        "A functional index is still attached to Graph_KG.rdf_edges."
    )
    assert obj.get("KG", "in", 0, o, p, s) is None, (
        f'^KG("in", 0, ...) was written for an edge in graph {_GRAPH!r}.'
    )


def test_named_graph_insert_does_not_write_ungraphed_degree_counters(
    iris_connection, probe_edge
):
    """`deg`/`degp` must not be written at a subscript depth that omits the graph key."""
    s, p, o = probe_edge
    obj = _iris_obj(iris_connection)
    assert obj.get("KG", "deg", s) is None, (
        '^KG("deg", s) was written with no graph key, violating ADR-0001.'
    )
    assert obj.get("KG", "degp", s, p) is None, (
        '^KG("degp", s, p) was written with no graph key, violating ADR-0001.'
    )


def test_graphidx_is_not_a_compiled_index_on_edge(iris_connection):
    """The index is gone from the compiled class, not just from the source."""
    assert not bool(
        int(
            _iris_obj(iris_connection).classMethodValue(
                "%Dictionary.CompiledIndex", "%ExistsId", "Graph.KG.Edge||GraphIdx"
            )
        )
    ), (
        "Graph.KG.Edge||GraphIdx is still compiled. Removing it from the source "
        "is not enough; the class has to be recompiled on the server."
    )


def test_edge_class_is_compiled(iris_connection):
    """Graph.KG.Edge still compiles and still owns its table."""
    assert _compiled(iris_connection, "Graph.KG.Edge")
    cur = iris_connection.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
    assert cur.fetchone()[0] >= 0
