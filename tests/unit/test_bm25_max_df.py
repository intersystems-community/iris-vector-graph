"""engine.bm25_search: the max_df_fraction option and the skipped-term report."""

from unittest.mock import MagicMock

import pytest

from tests.unit.test_bm25_index import _make_engine


def _engine(answer):
    engine = _make_engine()
    iris_mock = MagicMock()
    iris_mock.classMethodValue.return_value = answer
    engine._iris_obj = lambda: iris_mock
    return engine, iris_mock


def test_default_call_is_the_old_three_argument_search():
    engine, iris = _engine('[{"id":"A","score":1.5}]')
    assert engine.bm25_search("ix", "q", 5) == [("A", 1.5)]
    iris.classMethodValue.assert_called_once_with("Graph.KG.BM25Index", "Search", "ix", "q", 5)


def test_a_fraction_is_passed_through_as_the_fourth_argument():
    engine, iris = _engine('[{"id":"A","score":1.5}]')
    assert engine.bm25_search("ix", "q", 5, max_df_fraction=0.5) == [("A", 1.5)]
    iris.classMethodValue.assert_called_once_with("Graph.KG.BM25Index", "Search", "ix", "q", 5, 0.5)


def test_report_returns_results_skipped_terms_and_the_fallback_flag():
    engine, iris = _engine('{"results":[{"id":"A","score":.5}],"skipped":["common"],"fallback":0}')
    out = engine.bm25_search("ix", "common rare", 5, max_df_fraction=0.5, report=True)
    assert out == {"results": [("A", 0.5)], "skipped": ["common"], "fallback": False}
    iris.classMethodValue.assert_called_once_with("Graph.KG.BM25Index", "SearchReport", "ix", "common rare", 5, 0.5)


def test_report_without_a_fraction_still_reports_nothing_skipped():
    engine, _ = _engine('{"results":[],"skipped":[],"fallback":1}')
    out = engine.bm25_search("ix", "q", 5, report=True)
    assert out == {"results": [], "skipped": [], "fallback": True}


@pytest.mark.parametrize("bad", [-0.1, 1.5, "half", None])
def test_a_fraction_outside_0_to_1_is_refused_before_any_call(bad):
    engine, iris = _engine("[]")
    with pytest.raises(ValueError, match="max_df_fraction"):
        engine.bm25_search("ix", "q", 5, max_df_fraction=bad)
    iris.classMethodValue.assert_not_called()
