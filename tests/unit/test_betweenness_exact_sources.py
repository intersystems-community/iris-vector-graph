"""betweenness_centrality(sample_size >= N) is exact Brandes over every source.

`BetweennessGlobalParallel` picks its source count as: sampleSize when
0 < sampleSize < n, else maxSources when 0 < maxSources < n, else n. The store passed
maxSources=200 whatever the caller asked, so sample_size=0 and sample_size >= N both
gave a 200-source approximation (Pearson 0.77 against igraph exact at 5,000 nodes),
while the docstring promised "Set equal to node count for exact full Brandes".
The store now passes maxSources = sample_size when the caller names one; 0 keeps the
200-source default. The live proof is
tests/e2e/test_capabilities_betweenness_e2e.py.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest import mock

import pytest

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/NKGAccelCentrality.cls"


def _store(calls):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    iris_obj = mock.Mock()

    def cmv(cls, method, *args):
        calls.append((method, args))
        return 'OK:[{"id":"a","score":1.0}]'

    iris_obj.classMethodValue.side_effect = cmv
    store._iris_obj = lambda: iris_obj
    store.conn = mock.Mock()
    return store


def _global_args(calls):
    (args,) = [a for m, a in calls if m == "BetweennessGlobal"]
    return args


def _server_source_count(sample_size: int, max_sources: int, n: int) -> int:
    """The source-count rule in BetweennessGlobalParallel, restated."""
    if 0 < sample_size < n:
        return sample_size
    if 0 < max_sources < n:
        return max_sources
    return n


def test_the_server_rule_is_the_one_restated_here():
    body = re.search(
        r"^ClassMethod BetweennessGlobalParallel\(.*?^\}", CLS.read_text(), re.S | re.M
    ).group(0)
    assert "If (sampleSize > 0) && (sampleSize < n) {" in body
    assert "ElseIf (maxSources > 0) && (maxSources < n) {" in body
    assert "Set srcCount = n" in body


def test_default_sample_size_keeps_the_200_source_approximation():
    calls = []
    _store(calls)._betweenness_gref(0, "out", 0, 10, 256, None)
    sample_size, top_k, max_sources = _global_args(calls)
    assert (sample_size, top_k, max_sources) == (0, 10, 200)
    assert _server_source_count(sample_size, max_sources, 5000) == 200


@pytest.mark.parametrize("sample_size,n", [(300, 300), (5000, 5000), (10**9, 5000), (301, 300)])
def test_sample_size_at_or_above_node_count_is_every_source(sample_size, n):
    calls = []
    _store(calls)._betweenness_gref(sample_size, "out", 0, 10, 256, None)
    s, _top_k, max_sources = _global_args(calls)
    assert _server_source_count(s, max_sources, n) == n


@pytest.mark.parametrize("sample_size", [1, 50, 199, 250, 4999])
def test_sample_size_below_node_count_is_that_many_sources(sample_size):
    calls = []
    _store(calls)._betweenness_gref(sample_size, "out", 0, 10, 256, None)
    s, _top_k, max_sources = _global_args(calls)
    assert _server_source_count(s, max_sources, 5000) == sample_size


def test_docstring_says_what_sample_size_does():
    from iris_vector_graph.engine import IRISGraphEngine

    doc = inspect.getdoc(IRISGraphEngine.betweenness_centrality)
    assert "200" in doc
    assert "exact" in doc.lower()
    # The old wording, "0 uses the maxSources cap", named a parameter the caller
    # cannot set.
    assert "maxSources cap" not in doc
