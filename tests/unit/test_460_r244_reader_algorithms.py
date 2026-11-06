"""4.6.0 R244: a read IRIS refuses for a login holding the database at ``R`` says what is missing, and betweenness
answers one shape whatever the role.

``kg_GRAPH_WALK`` (``Graph.KG.Traversal.BFSFastJsonSorted``, which stages its rows in ``^ArnoKG("bfs_r")``) raised a
bare ``<THROW>``: the Native API keeps nothing of the ``<PROTECT>`` underneath. On that error the engine now asks whether
this login may write the global at all (a probe node, killed at once when the write is allowed) and, refused, names
the privilege: the namespace's database resource at ``RW``.

``betweenness_centrality`` listed every node, zeros too, when its ObjectScript tier could not run (a reader at ``R``
falls to the Python walk) and only the nodes on a shortest path when it could (``RW``): it lists only those now.
Live: tests/e2e/test_460_r244_reader_algorithms_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _engine():
    conn = MagicMock()
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    return eng


def _native(refuse_write: bool):
    nat = MagicMock()
    if refuse_write:
        nat.set.side_effect = RuntimeError("<PROTECT>")
    return nat


@pytest.mark.parametrize("refuse", [True, False], ids=["refused", "allowed"])
def test_a_ppr_failure_is_raised_as_it_came_and_probes_nothing(refuse):
    """R050 round 2: PPR has no work area since Graph.KG.Adj.Degree skips a refused degree-cache store, so a failure
    is not a refused write and is not explained as one (tests/e2e/test_460_r050_reader_ppr_degree_e2e.py)."""
    eng = _engine()
    store = MagicMock()
    store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[], error="<THROW>")
    eng.__dict__["_store"] = store
    eng.__dict__["_store_capabilities"] = {"ppr": True}
    nat = _native(refuse)
    with patch.object(IRISGraphEngine, "_iris_obj", return_value=nat):
        with pytest.raises(RuntimeError) as raised:
            eng.kg_PERSONALIZED_PAGERANK(["a"])
    assert str(raised.value) == "kg_PERSONALIZED_PAGERANK failed: <THROW>"
    nat.set.assert_not_called()


def test_graph_walk_names_the_missing_privilege():
    eng = _engine()
    nat = _native(True)
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("<THROW>")), \
            patch.object(IRISGraphEngine, "_iris_obj", return_value=nat):
        with pytest.raises(RuntimeError) as raised:
            eng.kg_GRAPH_WALK("a")
    text = str(raised.value)
    assert text.startswith("kg_GRAPH_WALK failed") and "^ArnoKG(\"bfs_r\")" in text, text
    assert "database resource at RW" in text


def test_graph_walk_keeps_another_error():
    eng = _engine()
    nat = _native(True)
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("<UNDEFINED>x")), \
            patch.object(IRISGraphEngine, "_iris_obj", return_value=nat):
        with pytest.raises(RuntimeError, match=r"^<UNDEFINED>x$"):
            eng.kg_GRAPH_WALK("a")
    nat.set.assert_not_called()


def test_betweenness_lists_only_nodes_on_a_shortest_path():
    eng = _engine()
    store = MagicMock()
    store.capabilities.return_value = {"betweenness": True}
    store.execute_betweenness.return_value = IVGResult(
        columns=["id", "score"], rows=[["b", 2.0], ["c", 2.0], ["a", 0.0], ["d", 0.0]])
    eng.__dict__["_store"] = store
    assert eng.betweenness_centrality() == [{"id": "b", "score": 2.0}, {"id": "c", "score": 2.0}]
