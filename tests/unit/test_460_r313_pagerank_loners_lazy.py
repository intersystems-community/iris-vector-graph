"""4.6.0 R313: PageRank reads the nodes with no edge only when it computes, not on every call.

R306 made PageRank score the default graph's nodes with no edge, and put that list in the algorithm cache's key: a
node written with no edge moves no adjacency token. So every call, a cache hit too, first ran
`Graph.KG.Edgeless.LonersJson`, a scan of the node table (46 ms at 20,000 nodes against 0.8 ms for the token below):
the spec 262 speed gate measured PageRank +45% to +70% against a42db224, whose cached call took 3.6 ms.

The key now holds a node-set token instead, the node table's row count and largest row id (a node added or removed
moves it; a node gaining or losing its last edge moves the adjacency token the cache already checks), and the loners
are read inside the computation, on a miss only.
"""

from __future__ import annotations

import pytest

from iris_vector_graph import algo_cache
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class FakeServer:
    """Graph.KG.AlgoCache as the ObjectScript class answers, and PageRankGlobalJson's calls recorded."""

    def __init__(self):
        self.token, self.entries, self.calls = 1, {}, []

    def __call__(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if (cls, method) == ("Graph.KG.AlgoCache", "Token"):
            return self.token
        if (cls, method) == ("Graph.KG.AlgoCache", "Lookup"):
            entry = self.entries.get(args)
            return f"H{self.token}|{entry[1]}" if entry and entry[0] == self.token else f"M{self.token}"
        if (cls, method) == ("Graph.KG.AlgoCache", "Put"):
            g, a, h, token, payload = args
            self.entries[(g, a, h)] = (int(token), payload)
            return 1
        if (cls, method) == ("Graph.KG.PageRank", "PageRankGlobalJson"):
            ids = ["a", "b"] + (__import__("json").loads(args[2]) if len(args) > 2 else [])
            return __import__("json").dumps([{"id": i, "score": 1.0 / len(ids)} for i in ids])
        raise AssertionError((cls, method))


@pytest.fixture
def store(monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)
    s = IRISGraphStore.__new__(IRISGraphStore)
    s._call_classmethod = FakeServer()
    s._native_answer = lambda name, fn: None
    return s


def test_a_cache_hit_reads_no_loners(store):
    reads = []

    def loners():
        reads.append(1)
        return ["c"]

    first = store.execute_pagerank(0.85, 20, loners=loners, node_token="3:9")
    second = store.execute_pagerank(0.85, 20, loners=loners, node_token="3:9")
    assert reads == [1], "the loners are read once, by the computation"
    assert sorted(r[0] for r in first.rows) == sorted(r[0] for r in second.rows) == ["a", "b", "c"]


def test_a_moved_node_token_is_another_entry(store):
    reads = []
    store.execute_pagerank(0.85, 20, loners=lambda: reads.append(1) or [], node_token="2:8")
    store.execute_pagerank(0.85, 20, loners=lambda: reads.append(1) or ["c"], node_token="3:9")
    assert reads == [1, 1]
    assert algo_cache.param_hash({"damping": 0.85, "max_iterations": 20, "nodes": "2:8"}) != algo_cache.param_hash(
        {"damping": 0.85, "max_iterations": 20, "nodes": "3:9"})


def test_the_list_form_still_keys_on_the_list(store):
    """A caller that hands the list itself (the 4.6.0 R306 form) is answered as before."""
    out = store.execute_pagerank(0.85, 20, ["c"])
    assert sorted(r[0] for r in out.rows) == ["a", "b", "c"]


def test_the_engine_hands_the_store_a_token_and_no_list():
    from unittest.mock import MagicMock

    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.result import IVGResult

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._store = MagicMock()
    eng._store.execute_pagerank.return_value = IVGResult(columns=["id", "score"], rows=[["a", 1.0]])
    eng._store_capabilities = {}
    eng._node_set_token = lambda: "5:42"
    eng._loners = MagicMock(side_effect=AssertionError("read only by the computation"))
    assert eng.kg_PAGERANK() == [("a", 1.0)]
    kwargs = eng._store.execute_pagerank.call_args.kwargs
    assert kwargs["node_token"] == "5:42" and callable(kwargs["loners"])
    eng._loners.assert_not_called()
