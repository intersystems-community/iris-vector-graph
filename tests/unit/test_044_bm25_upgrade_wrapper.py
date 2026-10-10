"""Spec 044 amendment (2026-10-08): the Python side of BM25 Upgrade and schema reporting."""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine


def _engine(returns):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    obj = MagicMock()
    obj.classMethodValue.return_value = returns
    eng._iris_obj = lambda: obj
    return eng, obj


def test_bm25_upgrade_calls_the_class_method_and_parses_its_answer():
    eng, obj = _engine('{"upgraded":1,"docs":3,"vocab":9,"postings":12}')
    out = eng.bm25_upgrade("logs")
    obj.classMethodValue.assert_called_once_with("Graph.KG.BM25Index", "Upgrade", "logs")
    assert out == {"upgraded": 1, "docs": 3, "vocab": 9, "postings": 12}


def test_bm25_upgrade_reports_an_index_already_in_schema_2():
    eng, _ = _engine('{"upgraded":0,"already":1}')
    assert eng.bm25_upgrade("logs") == {"upgraded": 0, "already": 1}


def test_bm25_info_carries_the_schema():
    eng, _ = _engine('{"N":5,"avgdl":4.0,"vocab_size":9,"graph":"","schema":1}')
    assert eng.bm25_info("logs")["schema"] == 1
