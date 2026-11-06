"""4.6.0 R050 round 2 (expert walkthrough W3): ``ppr`` takes one seed or a list of them, and a seed that is not a node
ID is refused before anything runs, for every user.

``ppr(["alice"])`` wrapped its argument in a list, so the seeds were ``[["alice"]]``. The store's cache key raised
``TypeError: unhashable type: 'list'``, which the engine took for a store that does not accept the
bidirectional/graph/tolerance keywords (a WARNING said so, though nobody passed them), and ``Graph.KG.PageRank.RunJson``
then read the inner array as a node: ``{'id': '9@%Library.DynamicArray', 'score': 1}`` for ``_SYSTEM``, a bare
``TypeError`` from the Python fallback for a reader. The live answers (networkx's PPR over a small graph) are
tests/e2e/test_460_r050_reader_ppr_degree_e2e.py.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.store_protocol import IVGResult


def _engine(store=None):
    from iris_vector_graph._engine.algorithms import AlgorithmsMixin

    engine = AlgorithmsMixin.__new__(AlgorithmsMixin)
    engine._store = store or MagicMock()
    engine._store_capabilities = {"ppr": True}
    engine.capabilities = MagicMock(objectscript_deployed=False, kg_built=False)
    return engine


@pytest.mark.parametrize("seed, seeds", [("a", ["a"]), (["a"], ["a"]), (["a", "c"], ["a", "c"]), (("a", "c"), ["a", "c"])])
def test_ppr_sends_its_seeds_as_one_flat_list(seed, seeds):
    engine = _engine()
    engine.kg_PERSONALIZED_PAGERANK = MagicMock(return_value={"a": 0.6, "c": 0.4})
    assert engine.ppr(seed) == {"scores": [{"id": "a", "score": 0.6}, {"id": "c", "score": 0.4}]}
    assert list(engine.kg_PERSONALIZED_PAGERANK.call_args.args[0]) == seeds


@pytest.mark.parametrize("seeds", [[["a"]], ["a", 3], [None], "alice"])
def test_a_seed_that_is_not_a_node_id_is_refused_before_anything_runs(seeds):
    store = MagicMock()
    engine = _engine(store)
    with pytest.raises(TypeError, match="seed"):
        engine.kg_PERSONALIZED_PAGERANK(seeds)
    store.execute_ppr.assert_not_called()


def test_ppr_refuses_a_nested_list_too():
    store = MagicMock()
    with pytest.raises(TypeError, match="seed"):
        _engine(store).ppr([["alice"]])
    store.execute_ppr.assert_not_called()


def test_a_type_error_inside_the_store_is_raised_not_blamed_on_keywords(caplog):
    """Only a store whose signature lacks the keywords is a reason to compute elsewhere."""
    store = MagicMock()
    store.execute_ppr.side_effect = TypeError("unhashable type: 'list'")
    engine = _engine(store)
    engine._kg_PERSONALIZED_PAGERANK_python_fallback = MagicMock(return_value={"x": 1.0})
    with caplog.at_level(logging.WARNING), pytest.raises(TypeError, match="unhashable"):
        engine.kg_PERSONALIZED_PAGERANK(["a"])
    assert not [r for r in caplog.records if "does not accept" in r.getMessage()]
    engine._kg_PERSONALIZED_PAGERANK_python_fallback.assert_not_called()


def test_a_store_without_the_keywords_still_falls_back(caplog):
    store = MagicMock()
    store.execute_ppr.side_effect = TypeError("execute_ppr() got an unexpected keyword argument 'bidirectional'")
    engine = _engine(store)
    engine._kg_PERSONALIZED_PAGERANK_python_fallback = MagicMock(return_value={"a": 1.0})
    with caplog.at_level(logging.WARNING):
        assert engine.kg_PERSONALIZED_PAGERANK(["a"]) == {"a": 1.0}
    assert [r for r in caplog.records if "does not accept" in r.getMessage()]


def test_the_store_answer_is_used_for_a_list_of_seeds():
    store = MagicMock()
    store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["a", 0.7], ["c", 0.3]])
    engine = _engine(store)
    assert engine.ppr(["a", "c"])["scores"][0] == {"id": "a", "score": 0.7}
    assert store.execute_ppr.call_args.args[0] == ["a", "c"]
