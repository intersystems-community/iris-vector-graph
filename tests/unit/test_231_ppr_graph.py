"""Spec 231 FR-016: personalized PageRank runs on one graph, the one the caller names.

`Graph.KG.PageRank.RunJson` began with `Set pGraph = 0`, so every PPR walked the
default graph's `^KG` whatever graph the seeds lived in, and the Python fallback read
`nodes` and `rdf_edges` with no `graph_id` predicate at all. A named graph also must not
reach `ArnoAccel.PPRJson`: `^NKG` has no graph dimension, so Arno would answer from the
default graph with no error.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

G = "fhir:IVGFHIR:X0001"


def _store(arno: bool) -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = arno
    store._arno_capabilities = {"algorithms": ["ppr"]} if arno else {}
    store._nkg_dirty = False
    return store


def _eng():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()
    return eng, cursor


class TestStore:
    def test_runjson_receives_graph(self):
        store = _store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as cm:
            store.execute_ppr(["Patient/p1"], 0.85, 20, graph=G)
        args = cm.call_args.args
        assert args[:2] == ("Graph.KG.PageRank", "RunJson")
        assert args[7] == G

    def test_default_graph_is_empty_string(self):
        store = _store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as cm:
            store.execute_ppr(["a"], 0.85, 20)
        assert cm.call_args.args[7] == ""

    def test_named_graph_skips_arno(self):
        store = _store(True)
        with patch.object(store, "_arno_call", return_value="[]") as arno, patch.object(
            store, "_call_classmethod", return_value="[]"
        ) as cm:
            store.execute_ppr(["Patient/p1"], 0.85, 20, graph=G)
        arno.assert_not_called()
        cm.assert_called_once()

    def test_default_graph_forward_still_uses_arno(self):
        store = _store(True)
        with patch.object(store, "_arno_call", return_value="[]") as arno:
            store.execute_ppr(["a"], 0.85, 20)
        arno.assert_called_once()

    def test_invalid_graph_raises(self):
        store = _store(False)
        with pytest.raises(ValueError, match="reserved"):
            store.execute_ppr(["a"], 0.85, 20, graph="0")


class TestEngine:
    def test_store_path_receives_graph(self):
        eng, _ = _eng()
        store = MagicMock()
        store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["x", 1.0]])
        eng._store = store
        eng._store_capabilities = {"ppr": True}
        eng.kg_PERSONALIZED_PAGERANK(["Patient/p1"], graph=G)
        assert store.execute_ppr.call_args.kwargs["graph"] == G

    def test_default_graph_does_not_send_graph_keyword(self):
        """A third-party store written before 4.0.1 keeps working for the default graph."""
        eng, _ = _eng()
        store = MagicMock()
        store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["x", 1.0]])
        eng._store = store
        eng._store_capabilities = {"ppr": True}
        eng.kg_PERSONALIZED_PAGERANK(["a"])
        assert "graph" not in store.execute_ppr.call_args.kwargs

    def test_objectscript_path_receives_graph(self):
        eng, _ = _eng()
        eng._store_capabilities = {"ppr": False}
        eng.capabilities = IRISCapabilities(objectscript_deployed=True, kg_built=True)
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps([{"id": "Patient/p1", "score": 0.5}])
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            eng.kg_PERSONALIZED_PAGERANK(["Patient/p1"], graph=G)
        args = iris_obj.classMethodValue.call_args.args
        assert args[:2] == ("Graph.KG.PageRank", "RunJson")
        assert args[7] == G

    def test_fallback_filters_graph(self):
        eng, cursor = _eng()
        eng._store_capabilities = {"ppr": False}
        eng.capabilities = IRISCapabilities(objectscript_deployed=False, kg_built=False)
        eng.kg_PERSONALIZED_PAGERANK(["Patient/p1"], graph=G)
        calls = cursor.execute.call_args_list
        assert calls, "fallback issued no SQL"
        for c in calls:
            assert "graph_id = ?" in c.args[0], c.args[0]
            assert G in list(c.args[1])

    def test_fallback_default_graph_is_filtered_too(self):
        """`graph=None` means the default graph `''`, never every graph (spec 227)."""
        eng, cursor = _eng()
        eng._kg_PERSONALIZED_PAGERANK_python_fallback(["a"])
        c = cursor.execute.call_args_list[0]
        assert "graph_id = ?" in c.args[0]
        assert list(c.args[1]) == [""]

    def test_graph_is_keyword_only(self):
        eng, _ = _eng()
        with pytest.raises(TypeError):
            eng.kg_PERSONALIZED_PAGERANK(["a"], 0.85, 20, 1e-6, None, False, 1.0, G)

    def test_invalid_graph_raises_before_any_io(self):
        eng, cursor = _eng()
        with pytest.raises(ValueError):
            eng.kg_PERSONALIZED_PAGERANK(["a"], graph="bad\x01graph")
        cursor.execute.assert_not_called()


def test_runjson_source_takes_pgraph():
    from pathlib import Path

    src = (Path(__file__).parents[2] / "iris_src/src/Graph/KG/PageRank.cls").read_text()
    head = src[src.index("ClassMethod RunJson(") :]
    sig = head[: head.index("{")]
    assert "pGraph As %String" in sig
    body = head[head.index("{") : head.index("\n}\n")]
    assert "Set pGraph = 0" not in body
    assert "GraphKey).ForIndex(" in body
