"""A `%NOINDEX` load that cannot rebuild its indices must not pass for a load.

`BulkLoader` inserts with `INSERT %NOINDEX %NOCHECK` — 450× faster, and the row is
written to the data map without touching a single index. Phase 5 exists to put the
indices back. It never ran: `_rebuild_indices` asked IRIS for

    SELECT %SYSTEM_SQL.BuildIndices('Graph.KG.rdfedges')

and there is no such function. IRIS answers `SQLCODE -359 SQL Function (function
stored procedure) not found`, the `except` logged a warning, `rebuild_all_indices`
recorded `False` in a stats dict nobody reads, and `load_networkx` returned as if the
load had succeeded.

What that leaves behind is worse than a missing index. Measured on
ivg-iris-enterprise, with one bulk-loaded edge (`lni_a KNOWS lni_b`) and 17 ring
edges in the tables:

    SELECT COUNT(*) FROM Graph_KG.rdf_edges              -> 17
    SELECT s FROM Graph_KG.rdf_edges WHERE s LIKE 'lni%' -> no rows
    SELECT s FROM Graph_KG.rdf_edges WHERE %NOINDEX ...  -> lni_a

The row is in the database and invisible to every index-driven reader: `COUNT(*)`,
any `WHERE` on an indexed column, the `DELETE`s `Graph.KG.Eraser` runs, the joins
`kg_KNN_VEC` and the Cypher translator emit. `Graph.KG.TraversalBuild.BuildKG`
reads with an embedded cursor and no predicate, so it *does* see the row and writes
`^KG("out", 0, "lni_a", ...)` — which is how a bulk-loaded node nobody can select
kept turning up in `^NKG` and made the Arno WCC ring test report two components
long after `EraseAll` had reported the database empty.

`##class(<class>).%BuildIndices()` through the native bridge returns 1 and the same
counts become 18 and one lni row, so the call is right and the SQL was the whole
defect.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.bulk_loader import BulkLoader

#: The four classes behind Graph_KG.nodes / rdf_labels / rdf_props / rdf_edges,
#: as INFORMATION_SCHEMA.TABLES.CLASSNAME reports them.
CLASSES = [
    "Graph.KG.rdfedges",
    "Graph.KG.rdflabels",
    "Graph.KG.rdfprops",
    "Graph.KG.nodes",
]


def _loader() -> BulkLoader:
    conn = MagicMock()
    return BulkLoader(conn)


def test_the_rebuild_calls_build_indices_and_not_a_function_iris_does_not_have():
    loader = _loader()
    with patch(
        "iris_vector_graph.bulk_loader._call_classmethod", return_value=1
    ) as call:
        results = loader.rebuild_all_indices()

    assert results == {cls: True for cls in CLASSES}
    assert [(c.args[1], c.args[2]) for c in call.call_args_list] == [
        (cls, "%BuildIndices") for cls in CLASSES
    ], (
        "the rebuild did not call %BuildIndices — the SQL form it used instead, "
        "SELECT %SYSTEM_SQL.BuildIndices(...), does not exist (SQLCODE -359), so "
        "every %NOINDEX row stays invisible to SQL"
    )


def test_a_rebuild_that_raised_is_not_recorded_as_done():
    loader = _loader()
    with patch(
        "iris_vector_graph.bulk_loader._call_classmethod",
        side_effect=RuntimeError("SQLCODE -359"),
    ):
        results = loader.rebuild_all_indices()

    assert results == {cls: False for cls in CLASSES}


def test_a_rebuild_that_returned_a_bad_status_is_not_recorded_as_done():
    """`%BuildIndices` answers with a %Status, so a falsy return is a failure."""
    loader = _loader()
    with patch("iris_vector_graph.bulk_loader._call_classmethod", return_value=0):
        results = loader.rebuild_all_indices()

    assert results == {cls: False for cls in CLASSES}


def test_load_graph_refuses_to_report_success_when_the_indices_are_not_back():
    """The caller has to hear about it — the rows are unselectable until it is fixed.

    `load_networkx` used to put the failures in `stats["index_rebuild"]` and return
    normally, which is how a load that left the tables unreadable looked exactly
    like one that worked.
    """
    pytest.importorskip("networkx")
    import networkx as nx

    G = nx.DiGraph()
    G.add_node("bli_a")
    G.add_node("bli_b")
    G.add_edge("bli_a", "bli_b", predicate="KNOWS")

    loader = _loader()
    with patch.object(loader, "load_nodes", return_value={"nodes": 2}), patch.object(
        loader, "load_edges", return_value={"edges": 1}
    ), patch.object(
        loader, "rebuild_all_indices", return_value={"Graph.KG.rdfedges": False}
    ):
        with pytest.raises(RuntimeError, match="Graph.KG.rdfedges"):
            loader.load_networkx(G, use_noindex=True, build_globals=False)


def test_a_plain_load_does_not_rebuild_anything():
    """Without %NOINDEX the indices were maintained on the way in."""
    pytest.importorskip("networkx")
    import networkx as nx

    G = nx.DiGraph()
    G.add_edge("bli_a", "bli_b", predicate="KNOWS")

    loader = _loader()
    with patch.object(loader, "load_nodes", return_value={"nodes": 2}), patch.object(
        loader, "load_edges", return_value={"edges": 1}
    ), patch.object(loader, "rebuild_all_indices") as rebuild:
        loader.load_networkx(G, use_noindex=False, build_globals=False)

    rebuild.assert_not_called()
