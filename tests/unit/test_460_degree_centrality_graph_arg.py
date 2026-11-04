"""4.6.0 R144: `degree_centrality(graph=...)` reaches `Graph.KG.Centrality.DegreeCentralityJson`, and its fallback.

The answers per graph are in tests/e2e/test_460_degree_centrality_graph_e2e.py. These pin the arguments on the
engine's real store: the class method gets the graph as its fourth argument when it is named and the 3.x call
otherwise; the Bug S fallback (`CLASS DOES NOT EXIST`) builds its `LazyKG` over the same graph; and an invalid name
fails before any call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return IRISGraphEngine(conn, embedding_dimension=4)


@pytest.mark.parametrize("graph,tail", [("g1", ("g1",)), (None, ()), ("", ())])
def test_the_class_method_gets_the_named_graph(graph, tail):
    eng = _engine()
    answer = '[{"id":"a","score":1,"degree":2}]'
    with patch.object(eng._store, "_call_classmethod", return_value=answer) as call:
        assert eng.degree_centrality(direction="in", graph=graph) == [{"id": "a", "score": 1.0, "degree": 2}]
    assert call.call_args.args == ("Graph.KG.Centrality", "DegreeCentralityJson", "in", "", "10000", *tail)


def test_the_bug_s_fallback_reads_the_named_graph():
    eng = _engine()
    lkg = MagicMock()
    lkg.iter_nodes.return_value = iter(["a", "b"])
    lkg.in_degree.side_effect = {"a": 0, "b": 1}.get
    with patch.object(eng._store, "_call_classmethod", side_effect=RuntimeError("<CLASS DOES NOT EXIST> DBSRV")), \
            patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg) as made:
        rows = eng.degree_centrality(direction="in", graph="g1")
    assert made.call_args.kwargs["graph"] == "g1"
    assert rows == [{"id": "b", "score": 1.0, "degree": 1}, {"id": "a", "score": 0.0, "degree": 0}]


def test_an_invalid_graph_name_fails_before_any_call():
    eng = _engine()
    with patch.object(eng._store, "_call_classmethod") as call:
        with pytest.raises(ValueError):
            eng.degree_centrality(graph="0")
    assert not call.called
